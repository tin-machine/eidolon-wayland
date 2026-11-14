use eidolon_model::{
    ids::{CommandSeq, SceneEpoch},
    state::{RuntimeState, Status, XrState},
};
use serde::Serialize;

/// Whole-Runtime ownership, distinct from an application's origin retirement.
/// Only a live Runtime constructs this census. Android must retain its last
/// accepted snapshot and separately observe nativeDestroy's actual result.
#[derive(Debug, Clone, Serialize)]
pub struct RuntimeReclamationSnapshot {
    pub runtime: eidolon_model::ids::RuntimeHandle,
    pub origin_retired: bool,
    pub recovery_connect_allowed: bool,
    pub recovery_command_allowed: bool,
    pub connections: usize,
    pub workers: usize,
    pub native_targets: usize,
    pub panels: usize,
    pub sdk_bindings: usize,
    pub board_windows: usize,
    pub pending_operations: usize,
    pub remote_cleanup_pending: bool,
    pub local_cleanup_pending: bool,
    pub shutdown_complete: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct Snapshot {
    pub schema: u32,
    pub state: RuntimeState,
    pub scene: Option<SceneEpoch>,
    pub xr_state: XrState,
    pub activity_visible: bool,
    pub commands_accepted: u64,
    pub commands_processed: u64,
    pub commands_cancelled: u64,
    pub commands_pending: usize,
    pub events_pending: usize,
    pub input_resets: u64,
    pub error: Option<&'static str>,
    pub shutdown: crate::shutdown::Report,
    pub reclamation: Option<RuntimeReclamationSnapshot>,
    /// 初期診断panelを除くSDK所有数。停止時はshutdown.sdk_bindingsの全所有を待つ。
    pub app_sdk_bindings: usize,
    pub helper: Option<crate::helper::Report>,
    pub helper_workers: usize,
    pub native_deps: Option<crate::compositor::primitives::Report>,
    pub dependency_workers: usize,
    pub compositor: Option<serde_json::Value>,
    pub compositor_workers: usize,
    pub app: Option<crate::session::Report>,
    pub desktop_v3_pending_targets: usize,
    pub desktop_v3_finalizer_pending: bool,
    pub desktop_v3_cleanup_error: Option<&'static str>,
    pub desktop_v3_presentation:
        Option<eidolon_model::linux_desktop_v3::report::PresentationSummary>,
    pub desktop_v3_sdk_target: Option<eidolon_model::linux_desktop_v3::report::SdkTarget>,
    pub panels: Vec<crate::panels::PanelCommand>,
    pub workspace: crate::workspace::Report,
    pub theme: crate::theme::Report,
    pub render: eidolon_render::Snapshot,
    pub transport: eidolon_transport::session::Snapshot,
    pub window_launch: Option<eidolon_model::window_launch::Report>,
    pub window_slots: crate::window_slots::Report,
    pub window_groups: crate::window_group_owner::Report,
}
impl Default for Snapshot {
    fn default() -> Self {
        Self {
            schema: 1,
            state: RuntimeState::Starting,
            scene: None,
            xr_state: XrState::Idle,
            activity_visible: false,
            commands_accepted: 0,
            commands_processed: 0,
            commands_cancelled: 0,
            commands_pending: 0,
            events_pending: 0,
            input_resets: 0,
            error: None,
            shutdown: Default::default(),
            reclamation: None,
            app_sdk_bindings: 0,
            helper: None,
            helper_workers: 0,
            native_deps: None,
            dependency_workers: 0,
            compositor: None,
            compositor_workers: 0,
            app: None,
            desktop_v3_pending_targets: 0,
            desktop_v3_finalizer_pending: false,
            desktop_v3_cleanup_error: None,
            desktop_v3_presentation: None,
            desktop_v3_sdk_target: None,
            panels: vec![],
            workspace: Default::default(),
            theme: Default::default(),
            render: Default::default(),
            transport: Default::default(),
            window_launch: None,
            window_slots: Default::default(),
            window_groups: Default::default(),
        }
    }
}

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Event {
    CredentialRequired {
        session: eidolon_model::ids::SessionId,
        request_id: CommandSeq,
        profile_id: String,
    },
    SessionState {
        session: Option<eidolon_model::ids::SessionId>,
        state: eidolon_transport::session::State,
        error: Option<&'static str>,
    },
    SurfaceDetached {
        target: eidolon_model::surface::TargetKey,
    },
    RuntimeReady,
    CommandComplete {
        request_id: CommandSeq,
        code: Status,
    },
    Failure {
        error: &'static str,
    },
    Stopped,
}
