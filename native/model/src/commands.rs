use crate::{
    ids::{CommandSeq, SceneEpoch},
    limits::COMMAND_BYTES,
    state::{Status, XrState},
};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RuntimeConfig {
    pub schema: u32,
}
impl RuntimeConfig {
    pub fn parse(json: &str) -> Result<Self, Status> {
        if json.len() > COMMAND_BYTES {
            return Err(Status::InvalidArgument);
        }
        let config: Self = serde_json::from_str(json).map_err(|_| Status::InvalidArgument)?;
        if config.schema != 1 {
            return Err(Status::InvalidArgument);
        }
        Ok(config)
    }
}

/// 未実装tagを成功扱いしない。接続・入力等は担当taskでこのenumへ追加する。
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum Command {
    SceneReady {
        schema: u32,
        request_id: CommandSeq,
        scene: SceneEpoch,
    },
    SetXrState {
        schema: u32,
        request_id: CommandSeq,
        state: XrState,
    },
    SetActivityState {
        schema: u32,
        request_id: CommandSeq,
        visible: bool,
    },
    ResetInput {
        schema: u32,
        request_id: CommandSeq,
    },
}
impl Command {
    pub fn parse(json: &str) -> Result<Self, Status> {
        if json.len() > COMMAND_BYTES {
            return Err(Status::InvalidArgument);
        }
        let command: Self = serde_json::from_str(json).map_err(|_| Status::InvalidArgument)?;
        let schema = match command {
            Self::SceneReady { schema, .. }
            | Self::SetXrState { schema, .. }
            | Self::SetActivityState { schema, .. }
            | Self::ResetInput { schema, .. } => schema,
        };
        if schema != 1 {
            return Err(Status::InvalidArgument);
        }
        Ok(command)
    }
    pub fn request_id(&self) -> CommandSeq {
        match *self {
            Self::SceneReady { request_id, .. }
            | Self::SetXrState { request_id, .. }
            | Self::SetActivityState { request_id, .. }
            | Self::ResetInput { request_id, .. } => request_id,
        }
    }
    pub fn needs_input_reset(&self) -> bool {
        matches!(
            self,
            Self::ResetInput { .. }
                | Self::SetActivityState { visible: false, .. }
                | Self::SetXrState {
                    state: XrState::Idle | XrState::Visible,
                    ..
                }
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_unknown_schema_fields_tags_and_invalid_ids_before_enqueue() {
        let valid = r#"{"schema":1,"request_id":"1","type":"scene_ready","scene":"1"}"#;
        assert!(Command::parse(valid).is_ok());
        for json in [
            valid.replace("\"schema\":1", "\"schema\":2"),
            valid.replace("\"scene_ready\"", "\"shell\""),
            valid.replace("\"scene\":\"1\"", "\"scene\":0"),
            valid.replace("\"scene\":\"1\"", "\"scene\":\"1\",\"path\":\"bad\""),
            valid.replace("\"scene\":\"1\"", "\"scene\":NaN"),
            "x".repeat(COMMAND_BYTES + 1),
        ] {
            assert!(Command::parse(&json).is_err(), "{json}");
        }
        assert!(RuntimeConfig::parse(r#"{"schema":1}"#).is_ok());
        assert!(RuntimeConfig::parse(r#"{"schema":2}"#).is_err());
        assert!(RuntimeConfig::parse(r#"{"schema":1,"shell":"bad"}"#).is_err());
    }
}
