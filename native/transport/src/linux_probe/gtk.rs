//! 固定GTKだけのprivate pinsと本文なしtyped counter。SHM/SDK成功を自己申告から作らない。
use super::{Pins, hash, hex};
pub use eidolon_model::commands::LinuxGtkCase as GtkCase;
use serde::{Deserialize, Serialize};

const SOURCE: &[u8] = include_bytes!("../../../../scripts/fixtures/gtk3-interaction-fixture.c");

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GtkPins {
    schema: u32,
    pub(super) base: Pins,
    pub(super) gtk_bundle_sha256: String,
    pub(super) source_sha256: String,
    pub(super) binary_sha256: String,
    pub(super) gtk_version: String,
}
impl std::fmt::Debug for GtkPins {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("LinuxGtkPins(<private>)")
    }
}
impl GtkPins {
    pub fn parse(json: &str) -> Result<Self, &'static str> {
        if json.len() > 4096 {
            return Err("LINUX_GTK_PIN_INVALID");
        }
        let pins: Self = serde_json::from_str(json).map_err(|_| "LINUX_GTK_PIN_INVALID")?;
        if !pins.valid() {
            return Err("LINUX_GTK_PIN_INVALID");
        }
        Ok(pins)
    }
    pub(super) fn valid(&self) -> bool {
        self.schema == 1
            && self.base.valid()
            && hex(&self.gtk_bundle_sha256, 64)
            && self.source_sha256 == hash(SOURCE)
            && hex(&self.binary_sha256, 64)
            && self.gtk_version.strip_prefix("3.24.").is_some_and(|patch| {
                !patch.is_empty() && patch.len() <= 3 && patch.bytes().all(|b| b.is_ascii_digit())
            })
    }
    pub fn binding(&self, case: GtkCase) -> GtkBinding {
        GtkBinding {
            case,
            pins: self.clone(),
        }
    }
}

#[derive(Clone)]
pub struct GtkBinding {
    pub case: GtkCase,
    pub(super) pins: GtkPins,
}
impl std::fmt::Debug for GtkBinding {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("LinuxGtkBinding")
            .field("case", &self.case)
            .finish_non_exhaustive()
    }
}
impl GtkBinding {
    pub(crate) fn base_pins(&self) -> Pins {
        self.pins.base.clone()
    }
    pub(super) fn valid(&self, base: &Pins) -> bool {
        self.pins.valid()
            && self.pins.base.bundle_sha256 == base.bundle_sha256
            && self.pins.base.waypipe_sha256 == base.waypipe_sha256
            && self.pins.base.fixture_sha256 == base.fixture_sha256
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct GtkRow {
    schema: u32,
    probe: String,
    run_id: String,
    case: GtkCase,
    app_id: String,
    dialog_app_id: String,
    pid: u32,
    uid: u32,
    sequence: u32,
    phase: String,
    reason: String,
    complete: bool,
    host_selftest: bool,
    physical_input_verified: bool,
    a_down: u32,
    a_up: u32,
    delete_down: u32,
    delete_up: u32,
    other_keys: u32,
    entry_changes: u32,
    button_clicks: u32,
    token_seen: bool,
    delete_seen: bool,
    dialog_opened: u32,
    dialog_closed: u32,
    dialog_responses: u32,
    dialog_parent_set: bool,
    dialog_visible: bool,
    main_configures: u32,
    dialog_configures: u32,
    main_draws: u32,
    dialog_draws: u32,
    resize_large_seen: bool,
    resize_return_seen: bool,
    logical_size: [u32; 2],
    entry_rect: [u32; 4],
    button_rect: [u32; 4],
}
impl GtkRow {
    fn counters(&self) -> [u32; 14] {
        [
            self.a_down,
            self.a_up,
            self.delete_down,
            self.delete_up,
            self.other_keys,
            self.entry_changes,
            self.button_clicks,
            self.dialog_opened,
            self.dialog_closed,
            self.dialog_responses,
            self.main_configures,
            self.dialog_configures,
            self.main_draws,
            self.dialog_draws,
        ]
    }
    pub(super) fn completed(&self) -> bool {
        self.complete
            && self.phase == "final"
            && self.reason == "completed"
            && match self.case {
                GtkCase::Input => {
                    [
                        self.a_down,
                        self.a_up,
                        self.delete_down,
                        self.delete_up,
                        self.button_clicks,
                    ] == [1; 5]
                        && self.entry_changes == 2
                        && self.other_keys == 0
                        && self.token_seen
                        && self.delete_seen
                }
                GtkCase::Dialog => {
                    [
                        self.dialog_opened,
                        self.dialog_closed,
                        self.dialog_responses,
                    ] == [1; 3]
                        && self.dialog_parent_set
                        && !self.dialog_visible
                }
                GtkCase::Resize => {
                    self.resize_large_seen
                        && self.resize_return_seen
                        && self.logical_size == [640, 360]
                }
            }
    }
    fn valid(&self, nonce: &str, uid: u32, case: GtkCase) -> bool {
        let run_id = format!("{nonce}{nonce}");
        let app_id = format!("io.github.tinmachine.eidolon.gtkfixture.r{run_id}");
        self.schema == 1
            && self.probe == "eidolon-gtk3-interaction-v1"
            && self.run_id == run_id
            && self.case == case
            && self.app_id == app_id
            && self.dialog_app_id == format!("{app_id}.dialog")
            && self.pid != 0
            && self.uid == uid
            && !self.host_selftest
            && !self.physical_input_verified
            && self.counters().iter().all(|n| *n <= i32::MAX as u32)
            && self
                .logical_size
                .iter()
                .chain(self.entry_rect.iter())
                .chain(self.button_rect.iter())
                .all(|n| *n <= 4096)
            && matches!(self.phase.as_str(), "ready" | "state" | "final")
            && matches!(
                self.reason.as_str(),
                "running"
                    | "completed"
                    | "window_closed"
                    | "timeout"
                    | "display_unavailable"
                    | "wayland_required"
            )
            && (self.phase == "final") == (self.reason != "running")
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct GtkReport {
    pub case: GtkCase,
    pub reports: u32,
    pub process_bound: bool,
    pub helper_verified: bool,
    pub forced_stop: Option<bool>,
    pub report_error: Option<String>,
    pub last: Option<GtkRow>,
}

impl GtkReport {
    pub(crate) fn initial(case: GtkCase) -> Self {
        Self {
            case,
            reports: 0,
            process_bound: false,
            helper_verified: false,
            forced_stop: None,
            report_error: None,
            last: None,
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct GtkFiles {
    #[serde(rename = "gtk3-interaction-fixture.c")]
    source: String,
    #[serde(rename = "eidolon-gtk3-fixture")]
    binary: String,
}
#[derive(Deserialize)]
#[serde(tag = "type", deny_unknown_fields)]
pub(super) enum GtkFrame {
    #[serde(rename = "tools")]
    Tools {
        schema: u32,
        backend: String,
        revision: String,
        nonce: String,
        uid: u32,
        waypipe_version: String,
        fixture_revision: String,
        bundle_sha256: String,
        files: super::protocol::Files,
        gtk_bundle_sha256: String,
        gtk_files: GtkFiles,
        gtk_version: String,
    },
    #[serde(rename = "gtk_report")]
    Report {
        schema: u32,
        backend: String,
        revision: String,
        nonce: String,
        uid: u32,
        process: super::protocol::Identity,
        report: GtkRow,
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
        exit_status: Option<i32>,
        stdout: super::protocol::Metrics,
        stderr: super::protocol::Metrics,
        mode: String,
        gtk_verified: bool,
        gtk_reports: u32,
        gtk_report_error: Option<String>,
        gtk_forced_stop: bool,
    },
}

pub(super) struct GtkState {
    pub binding: GtkBinding,
    pub report: GtkReport,
    process: Option<super::protocol::Identity>,
    ready: bool,
}
impl GtkState {
    pub fn new(binding: &GtkBinding) -> Self {
        Self {
            binding: binding.clone(),
            report: GtkReport::initial(binding.case),
            process: None,
            ready: false,
        }
    }
    pub fn frame(
        &mut self,
        frame: GtkFrame,
        protocol: &mut super::protocol::Protocol,
        audit: bool,
    ) -> Result<(), &'static str> {
        use GtkFrame::*;
        match frame {
            Tools {
                schema,
                backend,
                revision,
                nonce,
                uid,
                waypipe_version,
                fixture_revision,
                bundle_sha256,
                files,
                gtk_bundle_sha256,
                gtk_files,
                gtk_version,
            } => {
                let pins = &self.binding.pins;
                if !protocol.common(schema, &backend, &revision, &nonce, uid)
                    || protocol.report.phase != "inspecting"
                    || waypipe_version != "0.11.3"
                    || fixture_revision != "eidolon-gtk3-interaction-v1"
                    || bundle_sha256 != pins.base.bundle_sha256
                    || files.helper != pins.base.helper_sha256()
                    || files.waypipe != pins.base.waypipe_sha256
                    || files.fixture != pins.base.fixture_sha256
                    || gtk_bundle_sha256 != pins.gtk_bundle_sha256
                    || gtk_files.source != pins.source_sha256
                    || gtk_files.binary != pins.binary_sha256
                    || gtk_version != pins.gtk_version
                {
                    return Err("LINUX_GTK_TOOLS_INVALID");
                }
                protocol.report.phase = "tools_verified";
            }
            Report {
                schema,
                backend,
                revision,
                nonce,
                uid,
                process,
                report,
            } => {
                if !protocol.common(schema, &backend, &revision, &nonce, uid)
                    || protocol.report.phase != "running"
                    || !report.valid(&nonce, uid, self.binding.case)
                    || report.sequence != self.report.reports + 1
                    || self.report.reports >= 256
                    || self.report.last.as_ref().is_some_and(|last| {
                        last.phase == "final"
                            || report
                                .counters()
                                .iter()
                                .zip(last.counters())
                                .any(|(new, old)| *new < old)
                    })
                    || (report.phase == "ready" && self.report.reports != 0)
                    || (report.phase == "state" && !self.ready)
                    || !process.valid()
                    || process.state == "Z"
                    || process.uid != uid
                    || process.pid != report.pid
                    || protocol
                        .child
                        .as_ref()
                        .is_none_or(|child| child.group != process.group)
                    || self.process.as_ref().is_some_and(|saved| saved != &process)
                {
                    return Err("LINUX_GTK_REPORT_INVALID");
                }
                self.ready |= report.phase == "ready";
                self.process = Some(process);
                self.report.process_bound = true;
                self.report.reports += 1;
                self.report.last = Some(report);
            }
            Finished {
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
                exit_status,
                stdout,
                stderr,
                mode,
                gtk_verified,
                gtk_reports,
                gtk_report_error,
                gtk_forced_stop,
            } => {
                if !protocol.common(schema, &backend, &revision, &nonce, uid)
                    || protocol.report.phase == "opening"
                    || !stdout.valid()
                    || !stderr.valid()
                    || mode != self.binding.case.helper_mode()
                    || pattern_verified
                    || gtk_reports > 256
                    || (!audit && gtk_reports != self.report.reports)
                    || !super::protocol::valid_reason(&reason, true)
                    || gtk_report_error
                        .as_deref()
                        .is_some_and(|error| !valid_report_error(error))
                {
                    return Err("LINUX_GTK_FINISHED_INVALID");
                }
                let owned_clean = cleaned
                    && !group_alive
                    && pipes_closed
                    && (!protocol.child_started || child_reaped);
                if gtk_verified
                    && (!owned_clean
                        || gtk_forced_stop
                        || exit_status != Some(0)
                        || reason != "Completed"
                        || gtk_report_error.is_some()
                        || gtk_reports < 2
                        || (!audit
                            && (!self.ready
                                || !protocol.child_started
                                || self.report.last.as_ref().is_none_or(|row| !row.completed()))))
                {
                    return Err("LINUX_GTK_FINISHED_INVALID");
                }
                protocol.cleaned = owned_clean;
                self.report.helper_verified = gtk_verified && !audit && !protocol.stopping;
                self.report.forced_stop = Some(gtk_forced_stop);
                self.report.report_error = gtk_report_error;
                protocol.report.reason = Some(reason);
                protocol.report.group_alive = Some(group_alive);
                protocol.report.child_reaped = Some(child_reaped);
                protocol.report.pipes_closed = Some(pipes_closed);
                protocol.report.finished_received = true;
                protocol.report.phase = "finishing";
            }
        }
        Ok(())
    }
}
fn valid_report_error(error: &str) -> bool {
    matches!(
        error,
        "GTK_REPORT_LIMIT"
            | "GTK_REPORT_IDENTITY"
            | "GTK_REPORT_TYPE"
            | "GTK_REPORT_PHASE"
            | "GTK_REPORT_GEOMETRY"
            | "GTK_REPORT_AFTER_FINAL"
            | "GTK_REPORT_COUNTER_REGRESSED"
            | "GTK_PROCESS_INVALID"
            | "GTK_PROCESS_CHANGED"
            | "JSON_INVALID"
            | "GTK_REPORT_REJECTED"
    )
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct GtkAudit {
    schema: u32,
    backend: String,
    revision: String,
    nonce: String,
    uid: u32,
    cleaned: bool,
    already_absent: Option<bool>,
    pending: Option<bool>,
    receipt: Option<GtkFrame>,
}
pub(super) fn audit(
    raw: &[u8],
    nonce: &str,
    uid: u32,
    binding: &GtkBinding,
) -> Result<bool, &'static str> {
    if raw.is_empty() || raw.len() > 16384 {
        return Err("LINUX_AUDIT_INVALID");
    }
    let value: GtkAudit = serde_json::from_slice(raw).map_err(|_| "LINUX_AUDIT_INVALID")?;
    let mut protocol = super::protocol::Protocol::new(nonce, uid, &binding.pins.base);
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
    let GtkFrame::Finished { child_reaped, .. } = &receipt else {
        return Err("LINUX_AUDIT_INVALID");
    };
    protocol.child_started = *child_reaped;
    protocol.report.phase = "prepared";
    GtkState::new(binding).frame(receipt, &mut protocol, true)?;
    Ok(protocol.cleaned) // receiptは回収だけ。実GUI/SHM/SDKへ昇格しない。
}
