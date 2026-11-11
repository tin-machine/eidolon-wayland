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
                    presentation.startup_completed
                        && presentation.main.is_some()
                        && !presentation.cleanup_pending
                        && self.sdk_target.as_ref().is_some_and(|t| {
                            t.acknowledged && t.binding_present && t.renderer_frame_seen
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
