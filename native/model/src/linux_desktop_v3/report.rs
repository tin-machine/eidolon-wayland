//! Pure I2–I5 snapshot contract. No SSH/Wayland/SDK callback is synthesized here.
//! A launch ACK does not imply Started; Started does not imply SDK-visible Ready.
use super::{
    AppId, Error, FailureReason, MAX_RECORD_BYTES, REVISION, Reason, Result, decode,
    presentation::{NativeOrigin, PresentationState, Startup},
    require,
};
use crate::{
    ids::{CommandSeq, RuntimeHandle, WindowId},
    surface::TargetKey,
};
use serde::{Deserialize, Serialize};
use std::fmt;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TransportPhase {
    Preparing,
    Catalog,
    Forwarded,
    Started,
    Stopping,
    CleanupPending,
    Complete,
}

/// Strictly a transport observation; not a presentation or renderer verdict.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TransportState {
    pub phase: TransportPhase,
    pub launch_accepted: bool,
    pub journal_pending: bool,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PresentationSummary {
    pub state: PresentationState,
    pub main: Option<WindowId>,
    pub first_adopted_ms: Option<u64>,
    pub startup_completed: bool,
    pub first_failure: Option<Reason>,
    pub cleanup_pending: bool,
    pub fallback_used: bool,
}
impl PresentationSummary {
    /// B1 Startup remains the only authority to advance presentation states.
    pub fn from_startup(startup: &Startup) -> Result<Self> {
        serde_json::from_value(startup.summary()).map_err(|_| Error("CONTRACT_INVALID"))
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SdkAction {
    Create,
}

/// The full TargetKey includes scene and generation. A frame counter alone
/// cannot satisfy the B1 Startup evidence model.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SdkTarget {
    pub runtime: RuntimeHandle,
    pub target: TargetKey,
    pub create_command: CommandSeq,
    pub action: SdkAction,
    pub acknowledged: bool,
    pub binding_present: bool,
    pub renderer_frame_seen: bool,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RemoteCleanup {
    pub process_complete: bool,
    pub pipes_eof: bool,
    /// Verified remote reboot can replace EOF evidence, never fabricate it.
    pub boot_changed: bool,
    pub remote_cleaned: bool,
    pub receipt_written: bool,
    pub ssh_channel_complete: bool,
    pub forward_retired: bool,
    pub router_drained: bool,
}
impl RemoteCleanup {
    pub fn complete(&self) -> bool {
        self.process_complete
            && (self.pipes_eof || self.boot_changed)
            && self.remote_cleaned
            && self.receipt_written
            && self.ssh_channel_complete
            && self.forward_retired
            && self.router_drained
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LocalRetirement {
    pub waypipe_stopped: bool,
    pub listener_retired: bool,
    pub windows_retired: bool,
    pub sdk_bindings_released: bool,
    pub sdk_entities_disposed: bool,
}
impl LocalRetirement {
    pub fn complete(&self) -> bool {
        self.waypipe_stopped
            && self.listener_retired
            && self.windows_retired
            && self.sdk_bindings_released
            && self.sdk_entities_disposed
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RuntimeReclamation {
    pub connections_stopped: bool,
    pub workers_stopped: bool,
    pub panels_released: bool,
    pub targets_released: bool,
    pub shutdown_complete: bool,
    pub native_destroy_accepted: bool,
}
impl RuntimeReclamation {
    pub fn complete(&self) -> bool {
        self.connections_stopped
            && self.workers_stopped
            && self.panels_released
            && self.targets_released
            && self.shutdown_complete
            && self.native_destroy_accepted
    }
}

/// Failures of the controller and its local resources are not C2 wire reasons.
/// Keep these separate from the B1 FailureReason golden contract.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ControllerFault {
    SshIo,
    CommandTimeout,
    ChannelIncomplete,
    ForwardRequest,
    ForwardRetirement,
    RouterDrain,
    JournalReserve,
    JournalUpdate,
    JournalClear,
    PreviousIoPending,
    LocalAdmissionUnavailable,
    LocalRetirementTimeout,
    ScopeMismatch,
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ControllerFaults {
    pub first: Option<ControllerFault>,
    pub cleanup: Option<ControllerFault>,
}

/// Version-specific report. Existing v2 Snapshot and journal schema are not
/// reinterpreted. Null presentation/target means no observation, not success.
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct V3Report {
    pub protocol_revision: String,
    pub origin: NativeOrigin,
    pub transport: TransportState,
    pub presentation: Option<PresentationSummary>,
    pub sdk_target: Option<SdkTarget>,
    pub remote_cleanup: RemoteCleanup,
    pub local_retirement: LocalRetirement,
    pub runtime_reclamation: RuntimeReclamation,
    pub first_failure: Option<Reason>,
    pub cleanup_error: Option<FailureReason>,
    /// Local/controller failures; never reinterpret these as C2 receipts.
    #[serde(default)]
    pub controller_faults: ControllerFaults,
    pub cleanup_pending: bool,
    pub relaunch_allowed: bool,
}
impl fmt::Debug for V3Report {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("V3Report(<private>)")
    }
}
impl V3Report {
    pub fn parse(raw: &[u8]) -> Result<Self> {
        let report: Self = decode(raw, MAX_RECORD_BYTES)?;
        report.validate()?;
        Ok(report)
    }

    pub fn validate(&self) -> Result<()> {
        require(self.protocol_revision == REVISION, "CONTRACT_INVALID")?;
        if let Some(target) = &self.sdk_target {
            require(
                target.runtime == self.origin.runtime()
                    && target.target.session == self.origin.session(),
                "APP_ORIGIN_UNKNOWN",
            )?;
        }
        if let Some(presentation) = &self.presentation
            && presentation.state == PresentationState::Ready
        {
            require(
                self.transport.phase == TransportPhase::Started
                    && self.transport.launch_accepted
                    && self.transport.journal_pending
                    && !self.cleanup_pending
                    && presentation.startup_completed
                    && presentation.main.is_some()
                    && !presentation.cleanup_pending
                    && self.sdk_target.as_ref().is_some_and(|t| {
                        presentation.main == Some(t.target.window)
                            && t.acknowledged
                            && t.binding_present
                            && t.renderer_frame_seen
                    }),
                "CONTRACT_INVALID",
            )?;
        }
        if self.relaunch_allowed {
            require(
                !self.cleanup_pending
                    && !self.transport.journal_pending
                    && self.transport.phase == TransportPhase::Complete
                    && self.remote_cleanup.complete()
                    && self.local_retirement.complete(),
                "RECOVERY_REQUIRED",
            )?;
        }
        Ok(())
    }

    /// Observe identity from existing B1 NativeOrigin; never from UI/remote metadata.
    pub fn origin_app(&self) -> AppId {
        self.origin.app_id()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn origin() -> NativeOrigin {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../scripts/tests/fixtures/linux-desktop-v3-contract.json"
        ))
        .unwrap();
        let record = fixture["records"]
            .as_array()
            .unwrap()
            .iter()
            .find(|v| v["id"] == "origin")
            .unwrap();
        NativeOrigin::parse(record["raw"].as_str().unwrap().as_bytes()).unwrap()
    }

    fn report() -> V3Report {
        let origin = origin();
        V3Report {
            protocol_revision: REVISION.into(),
            origin,
            transport: TransportState {
                phase: TransportPhase::Preparing,
                launch_accepted: false,
                journal_pending: true,
            },
            presentation: None,
            sdk_target: None,
            remote_cleanup: RemoteCleanup {
                process_complete: false,
                pipes_eof: false,
                boot_changed: false,
                remote_cleaned: false,
                receipt_written: false,
                ssh_channel_complete: false,
                forward_retired: false,
                router_drained: false,
            },
            local_retirement: LocalRetirement {
                waypipe_stopped: false,
                listener_retired: false,
                windows_retired: false,
                sdk_bindings_released: false,
                sdk_entities_disposed: false,
            },
            runtime_reclamation: RuntimeReclamation {
                connections_stopped: false,
                workers_stopped: false,
                panels_released: false,
                targets_released: false,
                shutdown_complete: false,
                native_destroy_accepted: false,
            },
            first_failure: None,
            cleanup_error: None,
            controller_faults: ControllerFaults::default(),
            cleanup_pending: true,
            relaunch_allowed: false,
        }
    }

    #[test]
    fn report_does_not_conflate_accepted_started_and_presentation_ready() {
        let mut r = report();
        assert_eq!(format!("{r:?}"), "V3Report(<private>)");
        assert!(V3Report::parse(&serde_json::to_vec(&r).unwrap()).is_ok());
        let startup = Startup::begin(r.origin.clone(), 0, vec![]).unwrap();
        r.presentation = Some(PresentationSummary::from_startup(&startup).unwrap());
        assert_eq!(
            r.presentation.as_ref().unwrap().state,
            PresentationState::AwaitingMain
        );
        r.transport.launch_accepted = true;
        r.transport.phase = TransportPhase::Started;
        assert_ne!(
            r.presentation.as_ref().unwrap().state,
            PresentationState::Ready
        );
        assert!(r.validate().is_ok());
        r.presentation.as_mut().unwrap().state = PresentationState::Ready;
        r.presentation.as_mut().unwrap().startup_completed = true;
        r.presentation.as_mut().unwrap().main = Some(WindowId::new(1).unwrap());
        assert!(r.validate().is_err(), "no synthesized SDK evidence");
        let target = TargetKey::from_raw(r.origin.session().get(), 1, 1, 1).unwrap();
        r.sdk_target = Some(SdkTarget {
            runtime: r.origin.runtime(),
            target,
            create_command: CommandSeq::new(1).unwrap(),
            action: SdkAction::Create,
            acknowledged: true,
            binding_present: true,
            renderer_frame_seen: false,
        });
        assert!(r.validate().is_err(), "ACK and binding alone are not Ready");
        r.sdk_target.as_mut().unwrap().renderer_frame_seen = true;
        r.presentation.as_mut().unwrap().cleanup_pending = false;
        assert!(r.validate().is_err(), "cleanup pending cannot be Ready");
        r.cleanup_pending = false;
        assert!(
            r.validate().is_ok(),
            "Ready retains its crash-recovery journal"
        );
        r.transport.journal_pending = false;
        assert!(
            r.validate().is_err(),
            "Ready cannot outlive its recovery journal"
        );
        r.transport.journal_pending = true;
        r.sdk_target.as_mut().unwrap().target.window = WindowId::new(2).unwrap();
        assert!(
            r.validate().is_err(),
            "different window cannot supply main evidence"
        );
        r.sdk_target.as_mut().unwrap().target.window = WindowId::new(1).unwrap();
        r.sdk_target.as_mut().unwrap().runtime = RuntimeHandle::new(999).unwrap();
        assert!(r.validate().is_err(), "foreign runtime target");
    }

    #[test]
    fn native_origin_capture_matches_b1_golden_vector() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../scripts/tests/fixtures/linux-desktop-v3-contract.json"
        ))
        .unwrap();
        let raw = |id: &str| {
            fixture["records"]
                .as_array()
                .unwrap()
                .iter()
                .find(|record| record["id"] == id)
                .unwrap()["raw"]
                .as_str()
                .unwrap()
                .as_bytes()
                .to_vec()
        };
        let context = super::super::Context::parse(&raw("context")).unwrap();
        let pins = super::super::Pins::parse(&raw("pins")).unwrap();
        let selection = super::super::Selection::parse(&raw("selection")).unwrap();
        let expected = origin();
        let captured = NativeOrigin::capture(
            expected.runtime(),
            expected.request(),
            &context,
            &selection,
            &pins,
        )
        .unwrap();
        assert_eq!(captured.record(), expected.record());
    }

    #[test]
    fn remote_reboot_is_not_fabricated_pipe_eof() {
        let mut cleanup = report().remote_cleanup;
        cleanup.process_complete = true;
        cleanup.boot_changed = true;
        cleanup.remote_cleaned = true;
        cleanup.receipt_written = true;
        cleanup.ssh_channel_complete = true;
        cleanup.forward_retired = true;
        cleanup.router_drained = true;
        assert!(!cleanup.pipes_eof);
        assert!(
            cleanup.complete(),
            "verified reboot replaces, not fabricates, EOF"
        );
        cleanup.boot_changed = false;
        assert!(!cleanup.complete());
        cleanup.pipes_eof = true;
        assert!(cleanup.complete());
        cleanup.remote_cleaned = false;
        assert!(
            !cleanup.complete(),
            "remote cleanup remains an independent proof"
        );
    }

    #[test]
    fn report_fails_closed_on_pending_cleanup_and_preserves_failure_order() {
        let mut r = report();
        r.first_failure = Some(Reason::AppPresentationFailed);
        r.cleanup_error = Some(FailureReason::ReceiptWrite);
        r.relaunch_allowed = true;
        assert!(r.validate().is_err(), "pending resources cannot relaunch");
        r.transport.phase = TransportPhase::Complete;
        r.transport.journal_pending = false;
        r.cleanup_pending = false;
        assert!(
            r.validate().is_err(),
            "completed transport alone insufficient"
        );
        r.remote_cleanup = RemoteCleanup {
            process_complete: true,
            pipes_eof: true,
            boot_changed: false,
            remote_cleaned: true,
            receipt_written: true,
            ssh_channel_complete: true,
            forward_retired: true,
            router_drained: true,
        };
        r.local_retirement = LocalRetirement {
            waypipe_stopped: true,
            listener_retired: true,
            windows_retired: true,
            sdk_bindings_released: true,
            sdk_entities_disposed: true,
        };
        r.runtime_reclamation = RuntimeReclamation {
            connections_stopped: true,
            workers_stopped: true,
            panels_released: true,
            targets_released: true,
            shutdown_complete: true,
            native_destroy_accepted: true,
        };
        assert!(
            r.validate().is_ok(),
            "relaunch does not require nativeDestroy"
        );
        r.runtime_reclamation.shutdown_complete = false;
        r.runtime_reclamation.native_destroy_accepted = false;
        assert!(
            r.validate().is_ok(),
            "origin may relaunch within a live runtime"
        );
        let bytes = serde_json::to_vec(&r).unwrap();
        let parsed = V3Report::parse(&bytes).unwrap();
        assert_eq!(parsed.first_failure, Some(Reason::AppPresentationFailed));
        assert_eq!(parsed.cleanup_error, Some(FailureReason::ReceiptWrite));
        assert_eq!(parsed.controller_faults, ControllerFaults::default());
        let mut wrong = serde_json::to_value(&r).unwrap();
        wrong["protocol_revision"] = json!("unsupported-v4");
        assert!(V3Report::parse(wrong.to_string().as_bytes()).is_err());
        let duplicated = format!(
            r#"{{"protocol_revision":"{}","protocol_revision":"{}"}}"#,
            REVISION, REVISION
        );
        assert!(V3Report::parse(duplicated.as_bytes()).is_err());
    }
}
