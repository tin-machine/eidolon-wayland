use super::*;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    AwaitingPrepared,
    Prepared,
    Catalog,
    Started,
    Stopped,
    Pending,
    Failed,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Action {
    Inspect,
    Start,
    Heartbeat,
    Stop,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope {
    schema: u32,
    backend: String,
    revision: String,
    session: SessionId,
    profile_revision: String,
    scope: String,
    nonce: String,
    uid: u32,
    sequence: String,
    request_sequence: String,
    event: Event,
}
#[derive(Deserialize, Clone, PartialEq, Eq)]
#[serde(
    tag = "type",
    content = "payload",
    rename_all = "snake_case",
    deny_unknown_fields
)]
pub enum Event {
    Prepared {
        lease_ms: u32,
        boot_id: String,
        remote_socket: String,
    },
    Catalog(Box<Catalog>),
    Started {
        app_id: String,
        mode: String,
    },
    Heartbeat {
        phase: String,
    },
    Stopped {
        reason: StopReason,
        process_complete: bool,
        pipes_eof: bool,
        remote_cleaned: bool,
        receipt_written: bool,
    },
    Pending {
        reason: FailureReason,
        process_complete: bool,
        pipes_eof: bool,
    },
}
#[derive(Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum StopReason {
    Stop,
    LeaseExpired,
    InputEof,
    ProcessExited,
}
#[derive(Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum FailureReason {
    ProcessOwnership,
    PipeEof,
    DirectoryCleanup,
    ReceiptWrite,
    Protocol,
    ToolInspection,
    Start,
}
#[derive(Deserialize, Clone, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Catalog {
    schema: u32,
    namespace: String,
    scope: String,
    revision: String,
    entries: Vec<Entry>,
    arbitrary_exec: bool,
    terminal: bool,
    instance_limit: u32,
    base_bundle_sha256: String,
    desktop_bundle_sha256: String,
    waypipe_sha256: String,
    shm_fixture_sha256: String,
    desktop_binary_sha256: String,
    helper_sha256: String,
    adapter_sha256: String,
    owner_sha256: String,
    wire_sha256: String,
    demo_source_sha256: String,
    waypipe_version: String,
}
#[derive(Deserialize, Clone, Debug, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct Entry {
    id: String,
    label: String,
    mode: String,
    icon: String,
    available: bool,
}
impl Catalog {
    fn valid(&self, binding: &Binding) -> bool {
        let pins = &binding.pins;
        let source = pins.source_hashes();
        self.schema == 1
            && self.namespace == "linux-desktop"
            && self.scope == binding.scope
            && self.revision == binding.catalog_revision
            && !self.arbitrary_exec
            && !self.terminal
            && self.instance_limit == 1
            && self.entries
                == [Entry {
                    id: APP_ID.into(),
                    label: "Linux アプリ".into(),
                    mode: "gui".into(),
                    icon: "builtin:script".into(),
                    available: true,
                }]
            && self.base_bundle_sha256 == pins.base_bundle_sha256
            && self.desktop_bundle_sha256 == pins.desktop_bundle_sha256
            && self.waypipe_sha256 == pins.waypipe_sha256
            && self.shm_fixture_sha256 == pins.shm_fixture_sha256
            && self.desktop_binary_sha256 == pins.desktop_binary_sha256
            && self.helper_sha256 == source.base
            && self.adapter_sha256 == source.adapter
            && self.owner_sha256 == source.owner
            && self.wire_sha256 == source.wire
            && self.demo_source_sha256 == source.demo
            && self.waypipe_version == "0.11.3"
    }
}

/// 一つの authenticated actor が所有する bounded protocol state。
/// enqueue と remote acknowledgement は別。送信不明 / timeout 後の再送はしない。
/// SSH/channel/forward/journal 回収は別 owner が検査するので Stopped を全体 cleanup にしない。
pub struct Protocol {
    binding: Binding,
    phase: Phase,
    event_sequence: u64,
    // Issuing bytes does not mean the helper has received them. In particular an
    // already emitted terminal event can cross the next heartbeat or Stop.
    request_sequence: u64,
    acknowledged_sequence: u64,
    in_flight: Option<Action>,
}
impl std::fmt::Debug for Protocol {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("LinuxDesktopProtocol")
            .field("phase", &self.phase)
            .field("events", &self.event_sequence)
            .field("requests", &self.request_sequence)
            .field("acknowledged", &self.acknowledged_sequence)
            .field("pending", &self.in_flight.is_some())
            .finish()
    }
}
impl Protocol {
    pub fn new(binding: Binding) -> Self {
        Self {
            binding,
            phase: Phase::AwaitingPrepared,
            event_sequence: 0,
            request_sequence: 0,
            acknowledged_sequence: 0,
            in_flight: None,
        }
    }
    pub fn phase(&self) -> Phase {
        self.phase
    }
    pub(super) fn in_flight(&self) -> Option<Action> {
        self.in_flight
    }
    pub fn selection(&self) -> Option<Selection> {
        (self.phase == Phase::Catalog && self.in_flight.is_none()).then(|| Selection {
            scope: self.binding.scope.clone(),
            revision: self.binding.catalog_revision.clone(),
            app_id: APP_ID.into(),
        })
    }
    pub fn fail(&mut self) {
        self.phase = Phase::Failed;
        self.in_flight = None;
    }
    /// 返却 bytes は private wire 専用。ACK map / 通常診断へ本文を保存しない。
    pub fn command(
        &mut self,
        action: Action,
        selection: Option<&Selection>,
    ) -> Result<Vec<u8>, &'static str> {
        if self.in_flight.is_some()
            || !match action {
                Action::Inspect => self.phase == Phase::Prepared && selection.is_none(),
                Action::Start => {
                    self.phase == Phase::Catalog && selection == self.selection().as_ref()
                }
                Action::Heartbeat => {
                    matches!(
                        self.phase,
                        Phase::Prepared | Phase::Catalog | Phase::Started
                    ) && selection.is_none()
                }
                Action::Stop => {
                    matches!(
                        self.phase,
                        Phase::Prepared | Phase::Catalog | Phase::Started
                    ) && selection.is_none()
                }
            }
        {
            return Err("DESKTOP_COMMAND_INVALID");
        }
        let next = self
            .request_sequence
            .checked_add(1)
            .filter(|n| *n <= i64::MAX as u64)
            .ok_or("DESKTOP_SEQUENCE_EXHAUSTED")?;
        let b = &self.binding;
        let command = match action {
            Action::Inspect => serde_json::json!({"type":"inspect"}),
            Action::Start => serde_json::json!({"type":"start","selection":selection}),
            Action::Heartbeat => serde_json::json!({"type":"heartbeat"}),
            Action::Stop => serde_json::json!({"type":"stop"}),
        };
        let mut value = serde_json::to_vec(&serde_json::json!({"schema":1,"backend":"linux_desktop","revision":REVISION,
            "session":b.session,"profile_revision":b.profile_revision,"scope":b.scope,"nonce":b.nonce,"uid":b.uid,
            "sequence":next.to_string(),"command":command})).map_err(|_|"DESKTOP_COMMAND_INVALID")?;
        value.push(b'\n');
        self.request_sequence = next;
        self.in_flight = Some(action);
        Ok(value)
    }
    pub fn accept(&mut self, raw: &[u8]) -> Result<Event, &'static str> {
        let result = self.accept_inner(raw);
        if result.is_err() {
            self.fail();
        }
        result
    }
    fn accept_inner(&mut self, raw: &[u8]) -> Result<Event, &'static str> {
        if raw.is_empty()
            || raw.len() > 16384
            || matches!(self.phase, Phase::Stopped | Phase::Pending | Phase::Failed)
        {
            return Err("DESKTOP_PROTOCOL_INVALID");
        }
        let row: Envelope = serde_json::from_slice(raw).map_err(|_| "DESKTOP_PROTOCOL_INVALID")?;
        let b = &self.binding;
        if row.schema != 1
            || row.backend != "linux_desktop"
            || row.revision != REVISION
            || row.session != b.session
            || row.profile_revision != b.profile_revision
            || row.scope != b.scope
            || row.nonce != b.nonce
            || row.uid != b.uid
            || decimal(&row.sequence, false) != self.event_sequence.checked_add(1)
        {
            return Err("DESKTOP_PROTOCOL_SCOPE");
        }
        let request = decimal(&row.request_sequence, true).ok_or("DESKTOP_PROTOCOL_SCOPE")?;
        let asynchronous = matches!(
            &row.event,
            Event::Pending { .. }
                | Event::Stopped {
                    reason: StopReason::LeaseExpired
                        | StopReason::InputEof
                        | StopReason::ProcessExited,
                    ..
                }
        );
        // A reply acknowledges exactly the one in-flight request. An unsolicited
        // terminal event instead reports the helper's last processed request:
        // it may precede the request just issued, but cannot precede an ACK we
        // already accepted or refer to a request this owner never issued.
        if if asynchronous {
            request < self.acknowledged_sequence || request > self.request_sequence
        } else {
            request != self.request_sequence
        } {
            return Err("DESKTOP_PROTOCOL_SCOPE");
        }
        let next = match &row.event {
            Event::Prepared {
                lease_ms: 15000,
                boot_id,
                remote_socket,
            } if self.phase == Phase::AwaitingPrepared
                && self.in_flight.is_none()
                && valid_boot_id(boot_id)
                && valid_socket(remote_socket, &b.nonce) =>
            {
                Phase::Prepared
            }
            Event::Catalog(value)
                if self.phase == Phase::Prepared
                    && self.in_flight == Some(Action::Inspect)
                    && value.valid(b) =>
            {
                Phase::Catalog
            }
            Event::Started { app_id, mode }
                if self.phase == Phase::Catalog
                    && self.in_flight == Some(Action::Start)
                    && app_id == APP_ID
                    && mode == "gui" =>
            {
                Phase::Started
            }
            Event::Heartbeat { phase }
                if self.in_flight == Some(Action::Heartbeat)
                    && phase
                        == match self.phase {
                            Phase::Prepared => "prepared",
                            Phase::Catalog => "catalog",
                            Phase::Started => "started",
                            _ => "invalid",
                        } =>
            {
                self.phase
            }
            Event::Stopped {
                reason,
                process_complete: true,
                pipes_eof: true,
                remote_cleaned: true,
                receipt_written: true,
            } if (*reason != StopReason::Stop || self.in_flight == Some(Action::Stop))
                && (*reason != StopReason::ProcessExited || self.phase == Phase::Started)
                && self.phase != Phase::AwaitingPrepared =>
            {
                Phase::Stopped
            }
            Event::Pending { .. } => Phase::Pending,
            _ => return Err("DESKTOP_PROTOCOL_TRANSITION"),
        };
        self.event_sequence += 1;
        self.acknowledged_sequence = request;
        self.phase = next;
        self.in_flight = None;
        Ok(row.event)
    }
}

pub(super) fn valid_boot_id(value: &str) -> bool {
    value.len() == 36
        && value != "00000000-0000-0000-0000-000000000000"
        && value.bytes().enumerate().all(|(index, byte)| {
            if [8, 13, 18, 23].contains(&index) {
                byte == b'-'
            } else {
                byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)
            }
        })
}

pub(super) fn valid_socket(path: &str, nonce: &str) -> bool {
    let tail = format!("/.eidolon-desktop/s-{nonce}/p");
    let Some(home) = path.strip_suffix(&tail) else {
        return false;
    };
    path.len() <= 103
        && home.starts_with('/')
        && home.len() > 1
        && !path.chars().any(char::is_control)
        && !path
            .split('/')
            .skip(1)
            .any(|part| part.is_empty() || part == "." || part == "..")
        && !path.starts_with("/data/data/com.termux/")
}

impl std::fmt::Debug for Event {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            Self::Prepared { .. } => "Prepared",
            Self::Catalog(_) => "Catalog(<private>)",
            Self::Started { .. } => "Started",
            Self::Heartbeat { .. } => "Heartbeat",
            Self::Stopped { .. } => "Stopped",
            Self::Pending { .. } => "Pending",
        })
    }
}
impl std::fmt::Debug for Catalog {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("LinuxDesktopCatalog(<private>)")
    }
}
