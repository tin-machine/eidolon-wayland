use super::*;
use contract::{FailureReason, Reason, StopReason};

pub const MAX_FRAME_BYTES: usize = contract::MAX_RECORD_BYTES;
const MAX_CHUNK_BYTES: usize = 65536;
const MAX_QUEUED_FRAMES: usize = 16;

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
impl Phase {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::AwaitingPrepared => "awaiting_prepared",
            Self::Prepared => "prepared",
            Self::Catalog => "catalog",
            Self::Started => "started",
            Self::Stopped => "stopped",
            Self::Pending => "pending",
            Self::Failed => "failed",
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Action {
    Inspect,
    Start,
    Heartbeat,
    Stop,
}
impl Action {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Inspect => "inspect",
            Self::Start => "start",
            Self::Heartbeat => "heartbeat",
            Self::Stop => "stop",
        }
    }
}

#[derive(Clone, PartialEq, Eq)]
pub enum Event {
    Prepared {
        lease_ms: u32,
        boot_id: String,
        remote_socket: String,
    },
    Catalog(Value),
    Started {
        selection: Selection,
        mode: String,
        root_pid: u32,
        root_start_time_ticks: String,
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
        public_reason: Reason,
        process_complete: bool,
        pipes_eof: bool,
    },
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

pub struct Envelope {
    pub sequence: u64,
    pub request_sequence: u64,
    pub event: Event,
}

fn common(value: &Value, binding: &Binding, names: &[&str]) -> Result<u64> {
    fields(value, names)?;
    if value["schema"] != json!(1) {
        return Err("DESKTOP_V3_PROTOCOL_INVALID");
    }
    contract::DecoderVersion::selected(binding.pins(), string(&value["protocol_revision"])?)
        .map_err(|e| e.0)?;
    let context = Context::parse(&canonical(&value["context"])?).map_err(|e| e.0)?;
    if &context != binding.context() || value["scope"] != binding.scope() {
        return Err("DESKTOP_V3_PROTOCOL_SCOPE");
    }
    decimal(&value["sequence"], false)
}
fn valid_socket(value: &str, nonce: &str) -> bool {
    let tail = format!("/.eidolon-desktop-v3/s-{nonce}/p");
    let Some(home) = value.strip_suffix(&tail) else {
        return false;
    };
    value.len() <= 103
        && home.len() > 1
        && value.starts_with('/')
        && !value.starts_with("/data/data/com.termux/")
        && !value.chars().any(char::is_control)
        && !value
            .split('/')
            .skip(1)
            .any(|part| matches!(part, "" | "." | ".."))
}
fn fixed_bool(value: &Value) -> Result<bool> {
    value.as_bool().ok_or("DESKTOP_V3_PROTOCOL_INVALID")
}

pub fn parse_command(raw: &[u8], binding: &Binding) -> Result<Value> {
    let value = contract::parse_json(raw, MAX_FRAME_BYTES).map_err(|e| e.0)?;
    common(
        &value,
        binding,
        &[
            "schema",
            "protocol_revision",
            "context",
            "scope",
            "sequence",
            "command",
        ],
    )?;
    let command = &value["command"];
    fields(command, &["type", "payload"])?;
    match string(&command["type"])? {
        "start" => {
            fields(&command["payload"], &["selection"])?;
            Selection::parse(&canonical(&command["payload"]["selection"])?)
                .map_err(|e| e.0)?
                .matches(binding.context(), binding.pins())
                .map_err(|e| e.0)?;
        }
        "inspect" | "heartbeat" | "stop" => {
            fields(&command["payload"], &[])?;
        }
        _ => return Err("DESKTOP_V3_PROTOCOL_INVALID"),
    }
    Ok(value)
}

pub fn parse_event(raw: &[u8], binding: &Binding, evidence: &ExpectedEvidence) -> Result<Envelope> {
    let value = contract::parse_json(raw, MAX_FRAME_BYTES).map_err(|e| e.0)?;
    let sequence = common(
        &value,
        binding,
        &[
            "schema",
            "protocol_revision",
            "context",
            "scope",
            "sequence",
            "request_sequence",
            "event",
        ],
    )?;
    let request_sequence = decimal(&value["request_sequence"], true)?;
    let event = &value["event"];
    fields(event, &["type", "payload"])?;
    let payload = &event["payload"];
    let event = match string(&event["type"])? {
        "prepared" => {
            fields(payload, &["lease_ms", "boot_id", "remote_socket"])?;
            let boot_id = string(&payload["boot_id"])?;
            let remote_socket = string(&payload["remote_socket"])?;
            if payload["lease_ms"] != json!(15000)
                || !valid_boot(boot_id)
                || !valid_socket(remote_socket, &binding.nonce())
            {
                return Err("DESKTOP_V3_PROTOCOL_INVALID");
            }
            Event::Prepared {
                lease_ms: 15000,
                boot_id: boot_id.into(),
                remote_socket: remote_socket.into(),
            }
        }
        "catalog" => {
            fields(payload, &["descriptor", "evidence"])?;
            contract::validate_catalog(
                &canonical(&payload["descriptor"])?,
                binding.context(),
                binding.pins(),
            )
            .map_err(|e| e.0)?;
            evidence.matches_binding(binding)?;
            if payload["evidence"] != evidence.record() {
                return Err("DESKTOP_V3_EVIDENCE_STALE");
            }
            Event::Catalog(payload.clone())
        }
        "started" => {
            fields(payload, &["selection", "mode", "process_identity"])?;
            let selection =
                Selection::parse(&canonical(&payload["selection"])?).map_err(|e| e.0)?;
            selection
                .matches(binding.context(), binding.pins())
                .map_err(|e| e.0)?;
            let process = &payload["process_identity"];
            fields(process, &["pid", "start_time_ticks"])?;
            let pid = decimal(&process["pid"], false)?;
            decimal(&process["start_time_ticks"], false)?;
            if payload["mode"] != "gui" || pid > i32::MAX as u64 {
                return Err("DESKTOP_V3_PROTOCOL_INVALID");
            }
            Event::Started {
                selection,
                mode: "gui".into(),
                root_pid: pid as u32,
                root_start_time_ticks: string(&process["start_time_ticks"])?.into(),
            }
        }
        "heartbeat" => {
            fields(payload, &["phase"])?;
            let phase = string(&payload["phase"])?;
            if !matches!(phase, "prepared" | "catalog" | "started") {
                return Err("DESKTOP_V3_PROTOCOL_INVALID");
            }
            Event::Heartbeat {
                phase: phase.into(),
            }
        }
        "stopped" => {
            fields(
                payload,
                &[
                    "reason",
                    "process_complete",
                    "pipes_eof",
                    "remote_cleaned",
                    "receipt_written",
                ],
            )?;
            contract::parse_reason(&canonical(&payload["reason"])?, "stop").map_err(|e| e.0)?;
            let reason: StopReason = serde_json::from_value(payload["reason"].clone())
                .map_err(|_| "DESKTOP_V3_PROTOCOL_INVALID")?;
            if [
                "process_complete",
                "pipes_eof",
                "remote_cleaned",
                "receipt_written",
            ]
            .iter()
            .any(|name| payload[*name] != json!(true))
            {
                return Err("DESKTOP_V3_PROTOCOL_INVALID");
            }
            Event::Stopped {
                reason,
                process_complete: true,
                pipes_eof: true,
                remote_cleaned: true,
                receipt_written: true,
            }
        }
        "pending" => {
            fields(
                payload,
                &["reason", "public_reason", "process_complete", "pipes_eof"],
            )?;
            contract::parse_reason(&canonical(&payload["reason"])?, "failure").map_err(|e| e.0)?;
            contract::parse_reason(&canonical(&payload["public_reason"])?, "public")
                .map_err(|e| e.0)?;
            let reason: FailureReason = serde_json::from_value(payload["reason"].clone())
                .map_err(|_| "DESKTOP_V3_PROTOCOL_INVALID")?;
            let public_reason: Reason = serde_json::from_value(payload["public_reason"].clone())
                .map_err(|_| "DESKTOP_V3_PROTOCOL_INVALID")?;
            if public_reason == Reason::None {
                return Err("DESKTOP_V3_PROTOCOL_INVALID");
            }
            Event::Pending {
                reason,
                public_reason,
                process_complete: fixed_bool(&payload["process_complete"])?,
                pipes_eof: fixed_bool(&payload["pipes_eof"])?,
            }
        }
        _ => return Err("DESKTOP_V3_PROTOCOL_INVALID"),
    };
    Ok(Envelope {
        sequence,
        request_sequence,
        event,
    })
}

pub struct Protocol {
    binding: Binding,
    evidence: ExpectedEvidence,
    phase: Phase,
    event_sequence: u64,
    request_sequence: u64,
    acknowledged_sequence: u64,
    in_flight: Option<Action>,
    started_selection: Option<Selection>,
}
impl std::fmt::Debug for Protocol {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("LinuxDesktopV3Protocol")
            .field("phase", &self.phase)
            .field("events", &self.event_sequence)
            .field("requests", &self.request_sequence)
            .field("pending", &self.in_flight.is_some())
            .finish()
    }
}
impl Protocol {
    pub fn new(binding: Binding, evidence: ExpectedEvidence) -> Result<Self> {
        evidence.matches_binding(&binding)?;
        Ok(Self {
            binding,
            evidence,
            phase: Phase::AwaitingPrepared,
            event_sequence: 0,
            request_sequence: 0,
            acknowledged_sequence: 0,
            in_flight: None,
            started_selection: None,
        })
    }
    pub fn phase(&self) -> Phase {
        self.phase
    }
    pub fn in_flight(&self) -> Option<Action> {
        self.in_flight
    }
    pub fn fail(&mut self) {
        self.phase = Phase::Failed;
        self.in_flight = None;
    }
    pub fn selection(&self, app_id: &str) -> Result<Selection> {
        if self.phase != Phase::Catalog || self.in_flight.is_some() {
            return Err("DESKTOP_V3_COMMAND_INVALID");
        }
        Selection::parse(&canonical(&json!({"scope":self.binding.scope(),"revision":self.binding.catalog_revision(),"app_id":app_id}))?).map_err(|e| e.0)
    }
    pub fn command(&mut self, action: Action, selection: Option<&Selection>) -> Result<Vec<u8>> {
        if self.in_flight.is_some()
            || !match action {
                Action::Inspect => self.phase == Phase::Prepared && selection.is_none(),
                Action::Start => self.phase == Phase::Catalog && selection.is_some(),
                Action::Heartbeat | Action::Stop => {
                    matches!(
                        self.phase,
                        Phase::Prepared | Phase::Catalog | Phase::Started
                    ) && selection.is_none()
                }
            }
        {
            return Err("DESKTOP_V3_COMMAND_INVALID");
        }
        if let Some(selection) = selection {
            selection
                .matches(self.binding.context(), self.binding.pins())
                .map_err(|e| e.0)?;
        }
        let next = self
            .request_sequence
            .checked_add(1)
            .filter(|n| *n <= i64::MAX as u64)
            .ok_or("DESKTOP_V3_SEQUENCE_EXHAUSTED")?;
        let payload =
            selection.map_or_else(|| json!({}), |selection| json!({"selection":selection}));
        let mut raw = canonical(
            &json!({"schema":1,"protocol_revision":REVISION,"context":self.binding.context(),
            "scope":self.binding.scope(),"sequence":next.to_string(),"command":{"type":action.as_str(),"payload":payload}}),
        )?;
        parse_command(&raw, &self.binding)?;
        raw.push(b'\n');
        self.request_sequence = next;
        self.in_flight = Some(action);
        if action == Action::Start {
            self.started_selection = selection.cloned();
        }
        Ok(raw)
    }
    pub fn accept(&mut self, raw: &[u8]) -> Result<Event> {
        let result = self.accept_inner(raw);
        if result.is_err() {
            self.fail();
        }
        result
    }
    fn accept_inner(&mut self, raw: &[u8]) -> Result<Event> {
        if matches!(self.phase, Phase::Stopped | Phase::Pending | Phase::Failed) {
            return Err("DESKTOP_V3_PROTOCOL_INVALID");
        }
        let row = parse_event(raw, &self.binding, &self.evidence)?;
        if Some(row.sequence) != self.event_sequence.checked_add(1) {
            return Err("DESKTOP_V3_PROTOCOL_SCOPE");
        }
        let request = row.request_sequence;
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
        if if asynchronous {
            request < self.acknowledged_sequence || request > self.request_sequence
        } else {
            request != self.request_sequence
        } {
            return Err("DESKTOP_V3_PROTOCOL_SCOPE");
        }
        let next = match &row.event {
            Event::Prepared { .. }
                if self.phase == Phase::AwaitingPrepared && self.in_flight.is_none() =>
            {
                Phase::Prepared
            }
            Event::Catalog(_)
                if self.phase == Phase::Prepared && self.in_flight == Some(Action::Inspect) =>
            {
                Phase::Catalog
            }
            Event::Started { selection, .. }
                if self.phase == Phase::Catalog && self.in_flight == Some(Action::Start) =>
            {
                if self.started_selection.as_ref() != Some(selection) {
                    return Err("DESKTOP_V3_PROTOCOL_SCOPE");
                }
                Phase::Started
            }
            Event::Heartbeat { phase }
                if self.in_flight == Some(Action::Heartbeat) && phase == self.phase.as_str() =>
            {
                self.phase
            }
            Event::Stopped { reason, .. } if self.phase != Phase::AwaitingPrepared => {
                if *reason == StopReason::Stop && self.in_flight != Some(Action::Stop)
                    || *reason == StopReason::ProcessExited
                        && self.phase != Phase::Started
                        && !(self.in_flight == Some(Action::Start)
                            && request == self.request_sequence)
                {
                    return Err("DESKTOP_V3_PROTOCOL_TRANSITION");
                }
                Phase::Stopped
            }
            Event::Pending { .. } => Phase::Pending,
            _ => return Err("DESKTOP_V3_PROTOCOL_TRANSITION"),
        };
        self.event_sequence += 1;
        self.acknowledged_sequence = request;
        self.phase = next;
        self.in_flight = None;
        Ok(row.event)
    }
}

/// Bounded LF-only decoder. Failed or incomplete streams are not reusable.
#[derive(Default)]
pub struct FrameDecoder {
    buffer: Vec<u8>,
    failed: bool,
}
impl FrameDecoder {
    pub fn feed(&mut self, chunk: &[u8]) -> Result<Vec<Vec<u8>>> {
        let result = self.feed_inner(chunk);
        if result.is_err() {
            self.failed = true;
            self.buffer.clear();
        }
        result
    }
    fn feed_inner(&mut self, chunk: &[u8]) -> Result<Vec<Vec<u8>>> {
        if self.failed || chunk.len() > MAX_CHUNK_BYTES {
            return Err("DESKTOP_V3_PROTOCOL_INVALID");
        }
        let mut frames = Vec::new();
        for byte in chunk {
            if *byte == b'\r' {
                return Err("DESKTOP_V3_PROTOCOL_INVALID");
            }
            if *byte == b'\n' {
                if self.buffer.is_empty()
                    || self.buffer.len() > MAX_FRAME_BYTES
                    || frames.len() >= MAX_QUEUED_FRAMES
                {
                    return Err("DESKTOP_V3_PROTOCOL_INVALID");
                }
                frames.push(std::mem::take(&mut self.buffer));
            } else {
                if self.buffer.len() >= MAX_FRAME_BYTES {
                    return Err("DESKTOP_V3_PROTOCOL_INVALID");
                }
                self.buffer.push(*byte);
            }
        }
        Ok(frames)
    }
    pub fn finish(&self) -> Result<()> {
        if self.failed || !self.buffer.is_empty() {
            return Err("DESKTOP_V3_PROTOCOL_INVALID");
        }
        Ok(())
    }
}
