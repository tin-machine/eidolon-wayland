//! Pure I2–I5 snapshot contract. No SSH/Wayland/SDK callback is synthesized here.
//! A launch ACK does not imply Started; Started does not imply SDK-visible Ready.
use super::{
    AppId, Error, FailureReason, Reason, REVISION, Result, MAX_RECORD_BYTES, decode, require,
    presentation::{NativeOrigin, PresentationState, Startup},
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
    pub receipt_written: bool,
    pub ssh_channel_complete: bool,
    pub forward_retired: bool,
    pub router_drained: bool,
}
impl RemoteCleanup {
    pub fn complete(&self) -> bool {
        self.process_complete
            && self.pipes_eof
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
        if let Some(presentation) = &self.presentation {
            if presentation.state == PresentationState::Ready {
                require(
                    self.transport.phase == TransportPhase::Started
                        && self.transport.launch_accepted
                        && !self.transport.journal_pending
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
        }
        if self.relaunch_allowed {
            require(
                !self.cleanup_pending
                    && !self.transport.journal_pending
                    && self.transport.phase == TransportPhase::Complete
                    && self.remote_cleanup.complete()
                    && self.local_retirement.complete()
                    && self.runtime_reclamation.complete(),
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
        let record = fixture["records"].as_array().unwrap().iter()
            .find(|v| v["id"] == "origin").unwrap();
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
        r.transport.journal_pending = false;
        assert!(r.validate().is_ok(), "typed evidence can be consistent");
        r.sdk_target.as_mut().unwrap().target.window = WindowId::new(2).unwrap();
        assert!(r.validate().is_err(), "different window cannot supply main evidence");
        r.sdk_target.as_mut().unwrap().target.window = WindowId::new(1).unwrap();
        r.sdk_target.as_mut().unwrap().runtime = RuntimeHandle::new(999).unwrap();
        assert!(r.validate().is_err(), "foreign runtime target");
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
        assert!(r.validate().is_err(), "completed transport alone insufficient");
        r.remote_cleanup = RemoteCleanup {
            process_complete: true, pipes_eof: true, receipt_written: true,
            ssh_channel_complete: true, forward_retired: true, router_drained: true,
        };
        r.local_retirement = LocalRetirement {
            waypipe_stopped: true, listener_retired: true, windows_retired: true,
            sdk_bindings_released: true, sdk_entities_disposed: true,
        };
        r.runtime_reclamation = RuntimeReclamation {
            connections_stopped: true, workers_stopped: true, panels_released: true,
            targets_released: true, shutdown_complete: true, native_destroy_accepted: true,
        };
        assert!(r.validate().is_ok());
        let bytes = serde_json::to_vec(&r).unwrap();
        let parsed = V3Report::parse(&bytes).unwrap();
        assert_eq!(parsed.first_failure, Some(Reason::AppPresentationFailed));
        assert_eq!(parsed.cleanup_error, Some(FailureReason::ReceiptWrite));
        let mut wrong = serde_json::to_value(&r).unwrap();
        wrong["protocol_revision"] = json!("unsupported-v4");
        assert!(V3Report::parse(wrong.to_string().as_bytes()).is_err());
        let duplicated = format!(r#"{{"protocol_revision":"{}","protocol_revision":"{}"}}"#,
            REVISION, REVISION);
        assert!(V3Report::parse(duplicated.as_bytes()).is_err());
    }
}
