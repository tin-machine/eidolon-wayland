use eidolon_model::{
    ids::{CommandSeq, SceneEpoch},
    state::{RuntimeState, Status, XrState},
};
use serde::Serialize;

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
        }
    }
}

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Event {
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
