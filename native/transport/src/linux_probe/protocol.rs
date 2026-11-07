use super::{Pins, REVISION};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub phase: &'static str,
    pub stdout_bytes: u64,
    pub stderr_bytes: u64,
    pub eof: bool,
    pub exit_status: Option<u32>,
    pub exit_signal: bool,
    pub finished_received: bool,
    pub group_alive: Option<bool>,
    pub child_reaped: Option<bool>,
    pub pipes_closed: Option<bool>,
    pub reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub gtk: Option<super::GtkReport>,
}
#[derive(Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Identity {
    pub(super) pid: u32,
    pub(super) start: String,
    pub(super) group: u32,
    pub(super) uid: u32,
    pub(super) state: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Metrics {
    bytes: u64,
    retained: u64,
    truncated: bool,
}
impl Metrics {
    pub(super) fn valid(&self) -> bool {
        self.retained == self.bytes.min(65536) && self.truncated == (self.bytes > 65536)
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Files {
    #[serde(rename = "eidolon-linux-session.py")]
    pub(super) helper: String,
    pub(super) waypipe: String,
    #[serde(rename = "eidolon-shm-fixture")]
    pub(super) fixture: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Pattern {
    frames: u32,
    configures: u32,
    acks: u32,
    releases: u32,
    callbacks: u32,
}
#[derive(Deserialize)]
#[serde(tag = "type", deny_unknown_fields)]
enum Frame {
    #[serde(rename = "prepared")]
    Prepared {
        schema: u32,
        backend: String,
        revision: String,
        nonce: String,
        uid: u32,
        socket: String,
        launcher_sha256: String,
    },
    #[serde(rename = "tools")]
    Tools {
        schema: u32,
        backend: String,
        revision: String,
        nonce: String,
        uid: u32,
        waypipe_version: String,
        fixture_revision: String,
        frames: u32,
        bundle_sha256: String,
        files: Files,
    },
    #[serde(rename = "child_started")]
    ChildStarted {
        schema: u32,
        backend: String,
        revision: String,
        nonce: String,
        uid: u32,
        child: Identity,
    },
    #[serde(rename = "finished")]
    Finished {
        schema: u32,
        backend: String,
        revision: String,
        nonce: String,
        uid: u32,
        reason: String,
        cleaned: bool,
        group_alive: bool,
        child_reaped: bool,
        pipes_closed: bool,
        pattern_verified: bool,
        pattern: Option<Pattern>,
        exit_status: Option<i32>,
        stdout: Metrics,
        stderr: Metrics,
    },
}
pub(super) struct Protocol {
    nonce: String,
    uid: u32,
    pins: Pins,
    pub socket: Option<String>,
    pub report: Report,
    pub(super) child_started: bool,
    pub(super) child: Option<Identity>,
    gtk: Option<super::gtk::GtkState>,
    pub(super) cleaned: bool,
    pattern: bool,
    pub(super) stopping: bool,
}
impl Protocol {
    pub fn new(nonce: &str, uid: u32, pins: &Pins) -> Self {
        Self {
            nonce: nonce.into(),
            uid,
            pins: pins.clone(),
            socket: None,
            report: Report {
                phase: "opening",
                ..Default::default()
            },
            child_started: false,
            child: None,
            gtk: None,
            cleaned: false,
            pattern: false,
            stopping: false,
        }
    }
    pub(super) fn common(
        &self,
        schema: u32,
        backend: &str,
        revision: &str,
        nonce: &str,
        uid: u32,
    ) -> bool {
        schema == 1
            && backend == "linux_posix"
            && revision == REVISION
            && nonce == self.nonce
            && uid == self.uid
    }
    pub fn line(&mut self, line: &[u8]) -> Result<(), &'static str> {
        if line.is_empty() || line.len() > 16384 || self.report.finished_received {
            return Err("LINUX_PROTOCOL_ERROR");
        }
        if self.gtk.is_some() {
            #[derive(Deserialize)]
            struct Header {
                #[serde(rename = "type")]
                kind: String,
            }
            let header: Header =
                serde_json::from_slice(line).map_err(|_| "LINUX_PROTOCOL_ERROR")?;
            if matches!(header.kind.as_str(), "tools" | "gtk_report" | "finished") {
                let frame = serde_json::from_slice(line).map_err(|_| "LINUX_PROTOCOL_ERROR")?;
                let mut gtk = self.gtk.take().unwrap();
                let result = gtk.frame(frame, self, false);
                self.report.gtk = Some(gtk.report.clone());
                self.gtk = Some(gtk);
                return result;
            }
        }
        let frame: Frame = serde_json::from_slice(line).map_err(|_| "LINUX_PROTOCOL_ERROR")?;
        self.frame(frame)
    }
    fn frame(&mut self, frame: Frame) -> Result<(), &'static str> {
        match frame {
            Frame::Prepared {
                schema,
                backend,
                revision,
                nonce,
                uid,
                socket,
                launcher_sha256,
            } => {
                if !self.common(schema, &backend, &revision, &nonce, uid)
                    || self.report.phase != "opening"
                    || launcher_sha256 != self.pins.helper_sha256()
                    || !valid_socket(&socket, &nonce)
                {
                    return Err("LINUX_PREPARED_INVALID");
                }
                self.socket = Some(socket);
                self.report.phase = "prepared";
            }
            Frame::Tools {
                schema,
                backend,
                revision,
                nonce,
                uid,
                waypipe_version,
                fixture_revision,
                frames,
                bundle_sha256,
                files,
            } => {
                if !self.common(schema, &backend, &revision, &nonce, uid)
                    || self.report.phase != "inspecting"
                    || waypipe_version != "0.11.3"
                    || fixture_revision != "eidolon-shm-client-v1"
                    || frames != 100
                    || bundle_sha256 != self.pins.bundle_sha256
                    || files.helper != self.pins.helper_sha256()
                    || files.waypipe != self.pins.waypipe_sha256
                    || files.fixture != self.pins.fixture_sha256
                {
                    return Err("LINUX_TOOLS_INVALID");
                }
                self.report.phase = "tools_verified";
            }
            Frame::ChildStarted {
                schema,
                backend,
                revision,
                nonce,
                uid,
                child,
            } => {
                if !self.common(schema, &backend, &revision, &nonce, uid)
                    || self.report.phase != "starting"
                    || self.child_started
                    || child.uid != uid
                    || child.pid == 0
                    || child.pid != child.group
                    || child.start.is_empty()
                    || child.start.len() > 32
                    || !child.start.bytes().all(|b| b.is_ascii_digit())
                    || child.state.len() != 1
                    || !child.state.bytes().all(|b| b.is_ascii_alphabetic())
                {
                    return Err("LINUX_CHILD_INVALID");
                }
                self.child_started = true;
                self.child = Some(child);
                self.report.phase = "running";
            }
            Frame::Finished {
                schema,
                backend,
                revision,
                nonce,
                uid,
                reason,
                cleaned,
                group_alive,
                child_reaped,
                pipes_closed,
                pattern_verified,
                pattern,
                exit_status,
                stdout,
                stderr,
            } => {
                if !self.common(schema, &backend, &revision, &nonce, uid)
                    || self.report.phase == "opening"
                    || !stdout.valid()
                    || !stderr.valid()
                    || !valid_reason(&reason, false)
                {
                    return Err("LINUX_FINISHED_INVALID");
                }
                if pattern_verified
                    && (!self.child_started
                        || exit_status != Some(0)
                        || !pattern.as_ref().is_some_and(|p| {
                            p.frames == 100
                                && p.callbacks == 100
                                && p.releases == 100
                                && p.configures > 0
                                && p.acks > 0
                        }))
                {
                    return Err("LINUX_PATTERN_INVALID");
                }
                if !pattern_verified && pattern.is_some() {
                    return Err("LINUX_PATTERN_INVALID");
                }
                self.cleaned = cleaned
                    && !group_alive
                    && pipes_closed
                    && (!self.child_started || child_reaped);
                self.pattern = pattern_verified && reason == "Completed" && !self.stopping;
                self.report.reason = Some(reason);
                self.report.group_alive = Some(group_alive);
                self.report.child_reaped = Some(child_reaped);
                self.report.pipes_closed = Some(pipes_closed);
                self.report.finished_received = true;
                self.report.phase = "finishing";
            }
        }
        Ok(())
    }
    pub fn with_gtk(mut self, binding: Option<&super::GtkBinding>) -> Self {
        self.gtk = binding.map(super::gtk::GtkState::new);
        self
    }
    pub fn mode(&self) -> &'static str {
        self.gtk
            .as_ref()
            .map_or("pattern", |gtk| gtk.binding.case.helper_mode())
    }
    pub fn gtk_pass(&self) -> bool {
        self.cleaned()
            && self
                .gtk
                .as_ref()
                .is_some_and(|gtk| gtk.report.helper_verified)
    }
    pub fn inspect(&mut self) -> Result<(), &'static str> {
        if self.report.phase != "prepared" || self.stopping {
            return Err("NOT_READY");
        }
        self.report.phase = "inspecting";
        Ok(())
    }
    pub fn start(&mut self) -> Result<(), &'static str> {
        if self.report.phase != "tools_verified" || self.stopping {
            return Err("NOT_READY");
        }
        self.report.phase = "starting";
        Ok(())
    }
    pub fn stop(&mut self) {
        self.stopping = true;
    }
    pub fn finish(&mut self, remaining: usize) -> Result<(), &'static str> {
        if remaining != 0
            || !self.report.finished_received
            || !self.report.eof
            || self.report.exit_status != Some(0)
            || self.report.exit_signal
        {
            return Err("LINUX_REMOTE_EXIT");
        }
        self.report.phase = "closed";
        Ok(())
    }
    pub fn cleaned(&self) -> bool {
        self.report.phase == "closed" && self.cleaned
    }
    pub fn pattern_pass(&self) -> bool {
        self.cleaned() && self.pattern
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Audit {
    schema: u32,
    backend: String,
    revision: String,
    nonce: String,
    uid: u32,
    cleaned: bool,
    already_absent: Option<bool>,
    pending: Option<bool>,
    receipt: Option<Frame>,
}
pub(super) fn audit(raw: &[u8], nonce: &str, uid: u32, pins: &Pins) -> Result<bool, &'static str> {
    if raw.is_empty() || raw.len() > 16384 {
        return Err("LINUX_AUDIT_INVALID");
    }
    let value: Audit = serde_json::from_slice(raw).map_err(|_| "LINUX_AUDIT_INVALID")?;
    let mut protocol = Protocol::new(nonce, uid, pins);
    if !protocol.common(
        value.schema,
        &value.backend,
        &value.revision,
        &value.nonce,
        value.uid,
    ) {
        return Err("LINUX_AUDIT_INVALID");
    }
    if !value.cleaned {
        return if value.pending == Some(true)
            && value.already_absent.is_none()
            && value.receipt.is_none()
        {
            Ok(false)
        } else {
            Err("LINUX_AUDIT_INVALID")
        };
    }
    if value.pending.is_some() || value.already_absent != Some(true) {
        return Err("LINUX_AUDIT_INVALID");
    }
    let receipt = value.receipt.ok_or("LINUX_RECEIPT_MISSING")?;
    let Frame::Finished { child_reaped, .. } = &receipt else {
        return Err("LINUX_AUDIT_INVALID");
    };
    protocol.child_started = *child_reaped;
    protocol.report.phase = "prepared";
    protocol.frame(receipt)?;
    // channel EOF/exitはcallerで別検査。ここではreceipt自体の資源回収証拠だけ。
    Ok(protocol.cleaned)
}

pub(super) fn valid_socket(path: &str, nonce: &str) -> bool {
    let tail = format!("/.eidolon-linux/s-{nonce}/p");
    let Some(home) = path.strip_suffix(&tail) else {
        return false;
    };
    path.len() <= 103
        && home.starts_with('/')
        && home.len() > 1
        && !path.contains('\0')
        && !path.chars().any(char::is_control)
        && !path
            .split('/')
            .skip(1)
            .any(|part| part.is_empty() || part == "." || part == "..")
        && !path.starts_with("/data/data/com.termux/")
}

impl Identity {
    pub(super) fn valid(&self) -> bool {
        self.pid != 0
            && self.group != 0
            && !self.start.is_empty()
            && self.start.len() <= 32
            && self.start.bytes().all(|b| b.is_ascii_digit())
            && self.state.len() == 1
            && self.state.bytes().all(|b| b.is_ascii_alphabetic())
    }
}
pub(super) fn valid_reason(reason: &str, gtk: bool) -> bool {
    matches!(
        reason,
        "Completed"
            | "Stop"
            | "EOF"
            | "Signal"
            | "LeaseExpired"
            | "MaximumDuration"
            | "OutputFailed"
            | "ControlInvalid"
            | "InternalFailure"
            | "CleanupPending"
    ) || gtk && reason == "ReportInvalid"
}
