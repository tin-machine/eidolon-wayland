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
    SurfaceLost {
        schema: u32,
        request_id: CommandSeq,
        target: crate::surface::TargetKey,
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
            Self::SceneReady { schema, .. }
            | Self::RemoteCleanupProbe { schema, .. }
            | Self::NativeDepsProbe { schema, .. }
            | Self::CompositorProbe { schema, .. }
            | Self::ForwardProbe { schema, .. }
            | Self::Connect { schema, .. }
            | Self::Disconnect { schema, .. }
            | Self::SurfaceLost { schema, .. }
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
        if let Self::HelperProbe { nonce, .. }
        | Self::ForwardProbe { nonce, .. }
        | Self::RemoteCleanupProbe { nonce, .. }
        | Self::NativeDepsProbe { nonce, .. }
        | Self::CompositorProbe { nonce, .. } = &command
            && (nonce.len() != 16
                || !nonce
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)))
        {
            return Err(Status::InvalidArgument);
        }
        Ok(command)
    }
    pub fn request_id(&self) -> CommandSeq {
        match *self {
            Self::SceneReady { request_id, .. }
            | Self::RemoteCleanupProbe { request_id, .. }
            | Self::NativeDepsProbe { request_id, .. }
            | Self::CompositorProbe { request_id, .. }
            | Self::ForwardProbe { request_id, .. }
            | Self::Connect { request_id, .. }
            | Self::Disconnect { request_id, .. }
            | Self::SurfaceLost { request_id, .. }
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
