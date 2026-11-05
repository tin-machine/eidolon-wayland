use crate::{
    ids::{CommandSeq, SceneEpoch},
    limits::COMMAND_BYTES,
    state::{Status, XrState},
};
use serde::{Deserialize, Serialize};

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RuntimeConfig {
    pub schema: u32,
    #[serde(default)]
    pub keyboard_layout: crate::keyboard::KeyboardLayout,
    #[serde(default)]
    pub paths: Option<RuntimePaths>,
    #[serde(default)]
    pub installation: Option<String>,
    #[serde(default)]
    pub profiles: Vec<ConnectionProfile>,
}
impl Default for RuntimeConfig {
    fn default() -> Self {
        Self {
            schema: 1,
            keyboard_layout: Default::default(),
            paths: None,
            installation: None,
            profiles: Vec::new(),
        }
    }
}
#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ConnectionProfile {
    pub schema: u32,
    pub profile_id: String,
    pub installation: String,
    pub username: String,
    pub host_key: String,
}
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RuntimePaths {
    pub native_library_dir: String,
    pub files_dir: String,
}
impl RuntimeConfig {
    pub fn parse(json: &str) -> Result<Self, Status> {
        if json.len() > COMMAND_BYTES {
            return Err(Status::InvalidArgument);
        }
        let config: Self = serde_json::from_str(json).map_err(|_| Status::InvalidArgument)?;
        config.validate()?;
        Ok(config)
    }
    pub fn validate(&self) -> Result<(), Status> {
        if self.schema != 1 {
            return Err(Status::InvalidArgument);
        }
        if let Some(paths) = &self.paths {
            for value in [&paths.native_library_dir, &paths.files_dir] {
                if !std::path::Path::new(value).is_absolute()
                    || value.len() > 2048
                    || value.contains('\0')
                {
                    return Err(Status::InvalidArgument);
                }
            }
        }
        if self.profiles.len() > 1
            || self
                .installation
                .as_ref()
                .is_some_and(|id| !valid_installation(id))
        {
            return Err(Status::InvalidArgument);
        }
        for profile in &self.profiles {
            if profile.schema != 1
                || profile.profile_id != "termux-local"
                || self.installation.as_ref() != Some(&profile.installation)
                || !valid_installation(&profile.installation)
                || profile.username.is_empty()
                || profile.username.len() > 64
                || !profile
                    .username
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'_')
                || profile.host_key.len() > 4096
                || profile.host_key.contains('\0')
            {
                return Err(Status::InvalidArgument);
            }
        }
        Ok(())
    }
}
fn valid_installation(id: &str) -> bool {
    id.len() == 32
        && id
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum AppMode {
    Pattern,
    Foot,
    FootFixture,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PanelAction {
    Create,
    Destroy,
}
impl AppMode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Pattern => "pattern",
            Self::Foot => "foot",
            Self::FootFixture => "foot_fixture",
        }
    }
}

/// 未実装tagを成功扱いしない。接続・入力等は担当taskでこのenumへ追加する。
#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CleanupScenario {
    CancelBeforeExec,
    PrepareEof,
    Stop,
    ControlEof,
    SshDrop,
    LeaseExpire,
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CompositorScenario {
    Pointer,
    KeyboardUs,
    KeyboardJis,
    Lifecycle,
    Transforms,
    FrameGates,
    Tree,
    BadSerial,
    StaleConfigure,
    PrematureBuffer,
    UnconfiguredRemap,
    BadScale,
    PositionerOverflow,

    Pattern,
    SharedCache,
    InvalidDimensions,
    InvalidStride,
    InvalidOffset,
    InvalidFormat,
    Truncate,
    NegativeResize,
    OversizedPool,
    PoolLimit,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum Command {
    Pointer {
        schema: u32,
        request_id: CommandSeq,
        target: crate::surface::TargetKey,
        input_epoch: u64,
        pointer: crate::pointer::PointerPacket,
    },
    FocusWindow {
        schema: u32,
        request_id: CommandSeq,
        target: crate::surface::TargetKey,
        input_epoch: u64,
    },
    Key {
        schema: u32,
        request_id: CommandSeq,
        target: crate::surface::TargetKey,
        input_epoch: u64,
        focus_token: CommandSeq,
        key: crate::keyboard::KeyPacket,
    },
    AdapterApplied {
        schema: u32,
        request_id: CommandSeq,
        command_seq: CommandSeq,
        target: crate::surface::TargetKey,
        action: PanelAction,
    },
    AdapterFailed {
        schema: u32,
        request_id: CommandSeq,
        command_seq: CommandSeq,
        target: crate::surface::TargetKey,
        action: PanelAction,
    },
    RenderProbe {
        schema: u32,
        request_id: CommandSeq,
        nonce: String,
        target: crate::surface::TargetKey,
        stage: u32,
        width: i32,
        height: i32,
    },
    CompositorProbe {
        schema: u32,
        request_id: CommandSeq,
        nonce: String,
        scenario: CompositorScenario,
    },
    RemoteCleanupProbe {
        schema: u32,
        request_id: CommandSeq,
        session: crate::ids::SessionId,
        nonce: String,
        scenario: CleanupScenario,
    },
    NativeDepsProbe {
        schema: u32,
        request_id: CommandSeq,
        nonce: String,
    },
    AppProbe {
        schema: u32,
        request_id: CommandSeq,
        session: crate::ids::SessionId,
        nonce: String,
        mode: AppMode,
    },
    StartApp {
        schema: u32,
        request_id: CommandSeq,
        session: crate::ids::SessionId,
        nonce: String,
        mode: AppMode,
    },
    ForwardProbe {
        schema: u32,
        request_id: CommandSeq,
        session: crate::ids::SessionId,
        nonce: String,
    },
    Connect {
        schema: u32,
        request_id: CommandSeq,
        profile_id: String,
    },
    Disconnect {
        schema: u32,
        request_id: CommandSeq,
        session: crate::ids::SessionId,
    },
    CancelConnect {
        schema: u32,
        request_id: CommandSeq,
        connect_request: CommandSeq,
    },
    SurfaceLost {
        schema: u32,
        request_id: CommandSeq,
        target: crate::surface::TargetKey,
    },
    WaypipeProbe {
        schema: u32,
        request_id: CommandSeq,
        nonce: String,
    },
    HelperProbe {
        schema: u32,
        request_id: CommandSeq,
        nonce: String,
    },
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
            Self::Pointer { schema, .. }
            | Self::FocusWindow { schema, .. }
            | Self::Key { schema, .. }
            | Self::AdapterApplied { schema, .. }
            | Self::AdapterFailed { schema, .. }
            | Self::SceneReady { schema, .. }
            | Self::RenderProbe { schema, .. }
            | Self::RemoteCleanupProbe { schema, .. }
            | Self::NativeDepsProbe { schema, .. }
            | Self::AppProbe { schema, .. }
            | Self::StartApp { schema, .. }
            | Self::CompositorProbe { schema, .. }
            | Self::ForwardProbe { schema, .. }
            | Self::Connect { schema, .. }
            | Self::Disconnect { schema, .. }
            | Self::CancelConnect { schema, .. }
            | Self::SurfaceLost { schema, .. }
            | Self::WaypipeProbe { schema, .. }
            | Self::HelperProbe { schema, .. }
            | Self::SetXrState { schema, .. }
            | Self::SetActivityState { schema, .. }
            | Self::ResetInput { schema, .. } => schema,
        };
        if schema != 1 {
            return Err(Status::InvalidArgument);
        }
        if let Self::Connect { profile_id, .. } = &command
            && profile_id != "termux-local"
        {
            return Err(Status::InvalidArgument);
        }
        if let Self::WaypipeProbe { nonce, .. }
        | Self::RenderProbe { nonce, .. }
        | Self::HelperProbe { nonce, .. }
        | Self::ForwardProbe { nonce, .. }
        | Self::RemoteCleanupProbe { nonce, .. }
        | Self::NativeDepsProbe { nonce, .. }
        | Self::AppProbe { nonce, .. }
        | Self::StartApp { nonce, .. }
        | Self::CompositorProbe { nonce, .. } = &command
            && (nonce.len() != 16
                || !nonce
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)))
        {
            return Err(Status::InvalidArgument);
        }
        if let Self::RenderProbe {
            stage,
            width,
            height,
            ..
        } = &command
            && (*stage >= 18 || crate::surface::validate_dimensions(*width, *height).is_err())
        {
            return Err(Status::InvalidArgument);
        }
        if let Self::Key { key, .. } = &command
            && key.code().is_none()
        {
            return Err(Status::InvalidArgument);
        }
        if let Self::Pointer { pointer, .. } = &command
            && !pointer.valid()
        {
            return Err(Status::InvalidArgument);
        }
        Ok(command)
    }
    pub fn request_id(&self) -> CommandSeq {
        match *self {
            Self::Pointer { request_id, .. }
            | Self::FocusWindow { request_id, .. }
            | Self::Key { request_id, .. }
            | Self::AdapterApplied { request_id, .. }
            | Self::AdapterFailed { request_id, .. }
            | Self::SceneReady { request_id, .. }
            | Self::RenderProbe { request_id, .. }
            | Self::RemoteCleanupProbe { request_id, .. }
            | Self::NativeDepsProbe { request_id, .. }
            | Self::AppProbe { request_id, .. }
            | Self::StartApp { request_id, .. }
            | Self::CompositorProbe { request_id, .. }
            | Self::ForwardProbe { request_id, .. }
            | Self::Connect { request_id, .. }
            | Self::Disconnect { request_id, .. }
            | Self::CancelConnect { request_id, .. }
            | Self::SurfaceLost { request_id, .. }
            | Self::WaypipeProbe { request_id, .. }
            | Self::HelperProbe { request_id, .. }
            | Self::SetXrState { request_id, .. }
            | Self::SetActivityState { request_id, .. }
            | Self::ResetInput { request_id, .. } => request_id,
        }
    }
    pub fn needs_input_reset(&self) -> bool {
        matches!(
            self,
            Self::ResetInput { .. }
                | Self::Disconnect { .. }
                | Self::SurfaceLost { .. }
                | Self::SceneReady { .. }
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
    fn pointer_payload_source_and_key_focus_token_are_validated_before_enqueue() {
        let pointer = r#"{"schema":1,"request_id":"1","type":"pointer","target":{"session":"1","window":"1","scene":"1","generation":"1"},"input_epoch":0,"pointer":{"source":"9","uv":[0.5,0.5],"event":{"type":"buttons","changed":1,"state":1},"time_ms":12}}"#;
        assert!(Command::parse(pointer).is_ok());
        for invalid in [
            pointer.replace("\"source\":\"9\"", "\"source\":\"0\""),
            pointer.replace("\"source\":\"9\"", "\"source\":9"),
            pointer.replace("\"changed\":1", "\"changed\":8"),
            pointer.replace("[0.5,0.5]", "[1e999,0.5]"),
            pointer.replace("\"time_ms\":12", "\"time_ms\":12,\"text\":\"x\""),
        ] {
            assert!(Command::parse(&invalid).is_err());
        }
        let key = r#"{"schema":1,"request_id":"1","type":"key","target":{"session":"1","window":"1","scene":"1","generation":"1"},"input_epoch":0,"focus_token":"1","key":{"device":7,"scan_code":30,"key_code":29,"action":"pressed","repeat":0,"meta":0,"time_ms":12}}"#;
        assert!(Command::parse(key).is_ok());
        assert!(Command::parse(&key.replace("\"focus_token\":\"1\",", "")).is_err());
        assert!(
            Command::parse(&key.replace("\"focus_token\":\"1\"", "\"focus_token\":\"0\"")).is_err()
        );
    }
    #[test]
    fn profile_shape_installation_and_fixed_profile_id_are_validated_without_crypto() {
        let profile = ConnectionProfile {
            schema: 1,
            profile_id: "termux-local".into(),
            installation: "a".repeat(32),
            username: "fixture".into(),
            host_key: "synthetic".into(),
        };
        let mut config = RuntimeConfig {
            installation: Some("a".repeat(32)),
            profiles: vec![profile],
            ..Default::default()
        };
        assert!(config.validate().is_ok());
        config.profiles[0].installation = "b".repeat(32);
        assert!(config.validate().is_err());
        config.profiles[0].installation = "a".repeat(32);
        config.profiles[0].username = "bad;command".into();
        assert!(config.validate().is_err());
        assert!(
            Command::parse(
                r#"{"schema":1,"request_id":"1","type":"connect","profile_id":"external"}"#
            )
            .is_err()
        );
        assert!(
            RuntimeConfig::parse(r#"{"schema":1,"profiles":[],"endpoint":"external"}"#).is_err()
        );
    }
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
