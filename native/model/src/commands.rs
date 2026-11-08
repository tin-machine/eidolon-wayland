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
    pub preferences_dir: Option<String>,
    #[serde(default)]
    pub paths: Option<RuntimePaths>,
    #[serde(default)]
    pub installation: Option<String>,
    #[serde(default)]
    pub profiles: Vec<ConnectionProfile>,
    /// 通常 Linux 接続。debug probe と独立した immutable C1 profile / pins の組。
    #[serde(default)]
    pub linux_profile_v2: Option<String>,
    #[serde(default)]
    pub linux_desktop_pins: Option<String>,
    /// Debug専用保存profile。nested JSONも重複keyを失わずProfile::parseへ渡す。
    #[serde(default)]
    pub debug_probe_profile_v2: Option<String>,
    #[serde(default)]
    pub debug_linux_shm_pins: Option<String>,
    #[serde(default)]
    pub debug_linux_gtk_pins: Option<String>,
}
impl Default for RuntimeConfig {
    fn default() -> Self {
        Self {
            schema: 1,
            keyboard_layout: Default::default(),
            preferences_dir: None,
            paths: None,
            installation: None,
            profiles: Vec::new(),
            linux_profile_v2: None,
            linux_desktop_pins: None,
            debug_probe_profile_v2: None,
            debug_linux_shm_pins: None,
            debug_linux_gtk_pins: None,
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
        if let Some(path) = &self.preferences_dir
            && (!std::path::Path::new(path).is_absolute()
                || path.len() > 2048
                || path.contains('\0'))
        {
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
        if self.linux_profile_v2.is_some() != self.linux_desktop_pins.is_some()
            || self
                .linux_desktop_pins
                .as_ref()
                .is_some_and(|json| json.is_empty() || json.len() > 4096)
        {
            return Err(Status::InvalidArgument);
        }
        if let Some(json) = &self.linux_profile_v2 {
            let profile = crate::connection_profile::Profile::parse(
                json,
                self.installation
                    .as_deref()
                    .ok_or(Status::InvalidArgument)?,
            )
            .map_err(|_| Status::InvalidArgument)?;
            if profile.profile_id() != "remote-linux"
                || profile.backend() != crate::connection_profile::Backend::LinuxSsh
            {
                return Err(Status::InvalidArgument);
            }
        }
        if [
            self.debug_linux_shm_pins.as_ref(),
            self.debug_linux_gtk_pins.as_ref(),
        ]
        .into_iter()
        .flatten()
        .any(|json| json.is_empty() || json.len() > 4096 || self.debug_probe_profile_v2.is_none())
        {
            return Err(Status::InvalidArgument);
        }
        if let Some(json) = &self.debug_probe_profile_v2 {
            let profile = crate::connection_profile::Profile::parse(
                json,
                self.installation
                    .as_deref()
                    .ok_or(Status::InvalidArgument)?,
            )
            .map_err(|_| Status::InvalidArgument)?;
            if profile.profile_id() != "linux-probe"
                || profile.backend() != crate::connection_profile::Backend::LinuxSsh
            {
                return Err(Status::InvalidArgument);
            }
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
    LinuxDesktop,
    LinuxGtkInput,
    LinuxGtkDialog,
    LinuxGtkResize,
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum LinuxGtkCase {
    Input,
    Dialog,
    Resize,
}
impl LinuxGtkCase {
    pub fn mode(self) -> AppMode {
        match self {
            Self::Input => AppMode::LinuxGtkInput,
            Self::Dialog => AppMode::LinuxGtkDialog,
            Self::Resize => AppMode::LinuxGtkResize,
        }
    }
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Input => "input",
            Self::Dialog => "dialog",
            Self::Resize => "resize",
        }
    }
    pub fn helper_mode(self) -> &'static str {
        match self {
            Self::Input => "gtk_input",
            Self::Dialog => "gtk_dialog",
            Self::Resize => "gtk_resize",
        }
    }
    pub fn prepare_mode(self) -> &'static str {
        match self {
            Self::Input => "prepare-gtk-input",
            Self::Dialog => "prepare-gtk-dialog",
            Self::Resize => "prepare-gtk-resize",
        }
    }
}

#[derive(Debug, Default, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ThemeSelection {
    #[default]
    Off,
    Builtin,
    Imported,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PanelAction {
    Create,
    Destroy,
}
impl AppMode {
    pub fn linux_gtk_case(self) -> Option<LinuxGtkCase> {
        match self {
            Self::LinuxGtkInput => Some(LinuxGtkCase::Input),
            Self::LinuxGtkDialog => Some(LinuxGtkCase::Dialog),
            Self::LinuxGtkResize => Some(LinuxGtkCase::Resize),
            _ => None,
        }
    }
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Pattern => "pattern",
            Self::Foot => "foot",
            Self::FootFixture => "foot_fixture",
            Self::LinuxDesktop => "linux_desktop",
            Self::LinuxGtkInput => "linux_gtk_input",
            Self::LinuxGtkDialog => "linux_gtk_dialog",
            Self::LinuxGtkResize => "linux_gtk_resize",
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
    Popup,
    Clipboard,
    LogicalResize,
    Pointer,
    KeyboardUs,
    KeyboardJis,
    TextInput,
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
    RegisterSdkBinding {
        schema: u32,
        request_id: CommandSeq,
        target: crate::surface::TargetKey,
        /// 初期診断panelのSDK所有。省略した既存callerはアプリ資源として回収を待つ。
        #[serde(default)]
        diagnostic: bool,
    },
    ReleaseSdkBinding {
        schema: u32,
        request_id: CommandSeq,
        target: crate::surface::TargetKey,
    },
    SetWorkloadObservation {
        schema: u32,
        request_id: CommandSeq,
        enabled: bool,
    },
    SetTheme {
        schema: u32,
        request_id: CommandSeq,
        selection: ThemeSelection,
        reduced_motion: bool,
    },
    BeginThemeImport {
        schema: u32,
        request_id: CommandSeq,
        token: String,
    },
    ApplyThemeImport {
        schema: u32,
        request_id: CommandSeq,
        token: String,
    },
    CancelThemeImport {
        schema: u32,
        request_id: CommandSeq,
        token: String,
    },
    SetPose {
        schema: u32,
        request_id: CommandSeq,
        target: crate::surface::TargetKey,
        pose: crate::workspace::PanelPose,
    },
    SetPoseControl {
        schema: u32,
        request_id: CommandSeq,
        target: crate::surface::TargetKey,
        pose: crate::workspace::PanelPose,
        controls_revision: u64,
    },
    WindowGroup {
        schema: u32,
        request_id: CommandSeq,
        target: crate::surface::TargetKey,
        controls_revision: u64,
        expected_group: Option<CommandSeq>,
        expected_revision: Option<CommandSeq>,
        action: crate::window_groups::Action,
    },
    WindowGroupApplied {
        schema: u32,
        request_id: CommandSeq,
        target: crate::surface::TargetKey,
        group: CommandSeq,
        revision: CommandSeq,
        pose: crate::workspace::PanelPose,
        physical_size: crate::resize::PhysicalSize,
        content_frame: crate::ids::FrameSeq,
    },
    PoseApplied {
        schema: u32,
        request_id: CommandSeq,
        target: crate::surface::TargetKey,
        pose_command: Option<CommandSeq>,
        pose: crate::workspace::PanelPose,
    },
    SetBackground {
        schema: u32,
        request_id: CommandSeq,
        background: crate::workspace::Background,
    },
    BackgroundApplied {
        schema: u32,
        request_id: CommandSeq,
        scene: SceneEpoch,
        background_command: Option<CommandSeq>,
        background: crate::workspace::Background,
    },
    SetKeyboardLayout {
        schema: u32,
        request_id: CommandSeq,
        layout: crate::keyboard::KeyboardLayout,
    },
    OpenTerminal {
        schema: u32,
        request_id: CommandSeq,
        session: crate::ids::SessionId,
    },
    Catalog {
        schema: u32,
        request_id: CommandSeq,
        session: crate::ids::SessionId,
        operation: crate::catalog::Operation,
    },
    LinuxDesktopInspect {
        schema: u32,
        request_id: CommandSeq,
        session: crate::ids::SessionId,
    },
    LinuxDesktopLaunch {
        schema: u32,
        request_id: CommandSeq,
        session: crate::ids::SessionId,
        selection: crate::linux_desktop::Selection,
    },
    LinuxDesktopRecover {
        schema: u32,
        request_id: CommandSeq,
        session: crate::ids::SessionId,
    },
    LaunchTerminal {
        schema: u32,
        request_id: CommandSeq,
        session: crate::ids::SessionId,
        target: crate::catalog::Target,
    },
    ShowWindow {
        schema: u32,
        request_id: CommandSeq,
        session: crate::ids::SessionId,
        scene: SceneEpoch,
        window: crate::ids::WindowId,
        slot: usize,
    },
    CloseWindow {
        schema: u32,
        request_id: CommandSeq,
        session: crate::ids::SessionId,
        scene: SceneEpoch,
        window: crate::ids::WindowId,
    },
    CloseWindowTarget {
        schema: u32,
        request_id: CommandSeq,
        target: crate::surface::TargetKey,
        controls_revision: u64,
    },
    ResizeLogical {
        schema: u32,
        request_id: CommandSeq,
        target: crate::surface::TargetKey,
        width: u32,
        height: u32,
    },
    SetPhysicalSize {
        schema: u32,
        request_id: CommandSeq,
        target: crate::surface::TargetKey,
        size: crate::resize::PhysicalSize,
    },
    SetPhysicalSizeControl {
        schema: u32,
        request_id: CommandSeq,
        target: crate::surface::TargetKey,
        controls_revision: u64,
        expected_size_command: Option<CommandSeq>,
        expected_size: crate::resize::PhysicalSize,
        step: crate::resize::PhysicalStep,
    },
    PhysicalSizeApplied {
        schema: u32,
        request_id: CommandSeq,
        target: crate::surface::TargetKey,
        size_command: Option<CommandSeq>,
        size: crate::resize::PhysicalSize,
    },
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
    FocusWindowControl {
        schema: u32,
        request_id: CommandSeq,
        target: crate::surface::TargetKey,
        input_epoch: u64,
        controls_revision: u64,
    },
    Key {
        schema: u32,
        request_id: CommandSeq,
        target: crate::surface::TargetKey,
        input_epoch: u64,
        focus_token: CommandSeq,
        key: crate::keyboard::KeyPacket,
    },
    /// A1/A2の受入れまで有効化はdebug buildに限定する。保存設定にはしない。
    SetImeBackend {
        schema: u32,
        request_id: CommandSeq,
        session: crate::ids::SessionId,
        enabled: bool,
    },
    Ime {
        schema: u32,
        request_id: CommandSeq,
        target: crate::surface::TargetKey,
        transaction: crate::ime::ImeTransaction,
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
    ConnectSavedProbe {
        schema: u32,
        request_id: CommandSeq,
    },
    LinuxShmProbe {
        schema: u32,
        request_id: CommandSeq,
        session: crate::ids::SessionId,
        nonce: String,
    },
    LinuxShmSdkProbe {
        schema: u32,
        request_id: CommandSeq,
        session: crate::ids::SessionId,
        nonce: String,
    },
    LinuxGtkProbe {
        schema: u32,
        request_id: CommandSeq,
        session: crate::ids::SessionId,
        nonce: String,
        case: LinuxGtkCase,
    },
    LinuxGtkRecover {
        schema: u32,
        request_id: CommandSeq,
        session: crate::ids::SessionId,
        nonce: String,
        case: LinuxGtkCase,
    },
    LinuxShmRecover {
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
            Self::RegisterSdkBinding { schema, .. }
            | Self::ReleaseSdkBinding { schema, .. }
            | Self::SetWorkloadObservation { schema, .. }
            | Self::SetTheme { schema, .. }
            | Self::BeginThemeImport { schema, .. }
            | Self::ApplyThemeImport { schema, .. }
            | Self::CancelThemeImport { schema, .. }
            | Self::SetPose { schema, .. }
            | Self::SetPoseControl { schema, .. }
            | Self::WindowGroup { schema, .. }
            | Self::WindowGroupApplied { schema, .. }
            | Self::PoseApplied { schema, .. }
            | Self::SetBackground { schema, .. }
            | Self::BackgroundApplied { schema, .. }
            | Self::SetKeyboardLayout { schema, .. }
            | Self::OpenTerminal { schema, .. }
            | Self::Catalog { schema, .. }
            | Self::LinuxDesktopInspect { schema, .. }
            | Self::LinuxDesktopLaunch { schema, .. }
            | Self::LinuxDesktopRecover { schema, .. }
            | Self::LaunchTerminal { schema, .. }
            | Self::ShowWindow { schema, .. }
            | Self::CloseWindow { schema, .. }
            | Self::CloseWindowTarget { schema, .. }
            | Self::ResizeLogical { schema, .. }
            | Self::SetPhysicalSize { schema, .. }
            | Self::SetPhysicalSizeControl { schema, .. }
            | Self::PhysicalSizeApplied { schema, .. }
            | Self::Pointer { schema, .. }
            | Self::FocusWindow { schema, .. }
            | Self::FocusWindowControl { schema, .. }
            | Self::Key { schema, .. }
            | Self::SetImeBackend { schema, .. }
            | Self::Ime { schema, .. }
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
            | Self::ConnectSavedProbe { schema, .. }
            | Self::LinuxShmProbe { schema, .. }
            | Self::LinuxShmSdkProbe { schema, .. }
            | Self::LinuxShmRecover { schema, .. }
            | Self::LinuxGtkProbe { schema, .. }
            | Self::LinuxGtkRecover { schema, .. }
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
        if let Self::BeginThemeImport { token, .. }
        | Self::ApplyThemeImport { token, .. }
        | Self::CancelThemeImport { token, .. } = &command
            && !valid_installation(token)
        {
            return Err(Status::InvalidArgument);
        }
        if let Self::Connect { profile_id, .. } = &command
            && !matches!(profile_id.as_str(), "termux-local" | "remote-linux")
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
        | Self::LinuxShmProbe { nonce, .. }
        | Self::LinuxShmSdkProbe { nonce, .. }
        | Self::LinuxShmRecover { nonce, .. }
        | Self::LinuxGtkProbe { nonce, .. }
        | Self::LinuxGtkRecover { nonce, .. }
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
        match &command {
            Self::Ime {
                target,
                transaction,
                ..
            } if !transaction.valid()
                || target.session != transaction.target.session
                || target.window != transaction.target.window =>
            {
                return Err(Status::InvalidArgument);
            }
            Self::Catalog { operation, .. } if !operation.valid() => {
                return Err(Status::InvalidArgument);
            }
            Self::LinuxDesktopLaunch { selection, .. } if !selection.valid() => {
                return Err(Status::InvalidArgument);
            }
            Self::LaunchTerminal { target, .. } if !target.valid() => {
                return Err(Status::InvalidArgument);
            }
            Self::SetPose { pose, .. }
            | Self::SetPoseControl { pose, .. }
            | Self::WindowGroupApplied { pose, .. }
            | Self::PoseApplied { pose, .. }
                if !pose.valid() =>
            {
                return Err(Status::InvalidArgument);
            }
            Self::WindowGroup {
                expected_group,
                expected_revision,
                action,
                ..
            } if expected_group.is_some() != expected_revision.is_some()
                || expected_group.is_none()
                    && *action != crate::window_groups::Action::Collapse =>
            {
                return Err(Status::InvalidArgument);
            }
            Self::WindowGroupApplied { physical_size, .. } if !physical_size.valid() => {
                return Err(Status::InvalidArgument);
            }
            Self::ShowWindow { slot, .. } if *slot >= crate::limits::VISIBLE_PANELS => {
                return Err(Status::InvalidArgument);
            }
            Self::ResizeLogical { width, height, .. } => {
                crate::resize::logical_size(*width, *height)
                    .map_err(|_| Status::InvalidArgument)?;
            }
            Self::SetPhysicalSize { size, .. } | Self::PhysicalSizeApplied { size, .. }
                if !size.valid() =>
            {
                return Err(Status::InvalidArgument);
            }
            Self::SetPhysicalSizeControl {
                expected_size,
                step,
                ..
            } => {
                step.apply(*expected_size)
                    .map_err(|_| Status::InvalidArgument)?;
            }
            _ => (),
        }
        Ok(command)
    }
    pub fn request_id(&self) -> CommandSeq {
        match *self {
            Self::RegisterSdkBinding { request_id, .. }
            | Self::ReleaseSdkBinding { request_id, .. }
            | Self::SetWorkloadObservation { request_id, .. }
            | Self::SetTheme { request_id, .. }
            | Self::BeginThemeImport { request_id, .. }
            | Self::ApplyThemeImport { request_id, .. }
            | Self::CancelThemeImport { request_id, .. }
            | Self::SetPose { request_id, .. }
            | Self::SetPoseControl { request_id, .. }
            | Self::WindowGroup { request_id, .. }
            | Self::WindowGroupApplied { request_id, .. }
            | Self::PoseApplied { request_id, .. }
            | Self::SetBackground { request_id, .. }
            | Self::BackgroundApplied { request_id, .. }
            | Self::SetKeyboardLayout { request_id, .. }
            | Self::OpenTerminal { request_id, .. }
            | Self::Catalog { request_id, .. }
            | Self::LinuxDesktopInspect { request_id, .. }
            | Self::LinuxDesktopLaunch { request_id, .. }
            | Self::LinuxDesktopRecover { request_id, .. }
            | Self::LaunchTerminal { request_id, .. }
            | Self::ShowWindow { request_id, .. }
            | Self::CloseWindow { request_id, .. }
            | Self::CloseWindowTarget { request_id, .. }
            | Self::ResizeLogical { request_id, .. }
            | Self::SetPhysicalSize { request_id, .. }
            | Self::SetPhysicalSizeControl { request_id, .. }
            | Self::PhysicalSizeApplied { request_id, .. }
            | Self::Pointer { request_id, .. }
            | Self::FocusWindow { request_id, .. }
            | Self::FocusWindowControl { request_id, .. }
            | Self::Key { request_id, .. }
            | Self::SetImeBackend { request_id, .. }
            | Self::Ime { request_id, .. }
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
            | Self::ConnectSavedProbe { request_id, .. }
            | Self::LinuxShmProbe { request_id, .. }
            | Self::LinuxShmSdkProbe { request_id, .. }
            | Self::LinuxShmRecover { request_id, .. }
            | Self::LinuxGtkProbe { request_id, .. }
            | Self::LinuxGtkRecover { request_id, .. }
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
                | Self::SetImeBackend { .. }
                | Self::SetPose { .. }
                | Self::SetPoseControl { .. }
                | Self::WindowGroup { .. }
                | Self::SetPhysicalSizeControl { .. }
                | Self::SetKeyboardLayout { .. }
                | Self::ShowWindow { .. }
                | Self::CloseWindow { .. }
                | Self::CloseWindowTarget { .. }
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
    fn sdk_binding_is_an_app_owner_unless_diagnostic_is_explicit() {
        let mut value = serde_json::json!({"schema":1,"type":"register_sdk_binding","request_id":"1",
            "target":{"session":"1","window":"1","scene":"1","generation":"1"}});
        assert!(matches!(
            Command::parse(&value.to_string()).unwrap(),
            Command::RegisterSdkBinding {
                diagnostic: false,
                ..
            }
        ));
        value["diagnostic"] = true.into();
        assert!(matches!(
            Command::parse(&value.to_string()).unwrap(),
            Command::RegisterSdkBinding {
                diagnostic: true,
                ..
            }
        ));
        value["diagnostic"] = "true".into();
        assert!(Command::parse(&value.to_string()).is_err());
    }
    #[test]
    fn group_wire_keeps_new_instance_and_geometry_ack_fences_distinct() {
        let action = serde_json::json!({"schema":1,"type":"window_group","request_id":"10",
            "target":{"session":"2","window":"3","scene":"4","generation":"5"},"controls_revision":0,
            "expected_group":null,"expected_revision":null,"action":"collapse"});
        assert!(
            Command::parse(&action.to_string())
                .unwrap()
                .needs_input_reset()
        );
        for (field, value) in [
            ("expected_group", serde_json::json!("8")),
            ("expected_revision", serde_json::json!("2")),
            ("action", serde_json::json!("expand")),
            ("action", serde_json::json!("next_front")),
            ("action", serde_json::json!("script")),
        ] {
            let mut changed = action.clone();
            changed[field] = value;
            assert!(Command::parse(&changed.to_string()).is_err());
        }
        let mut current = action.clone();
        current["expected_group"] = serde_json::json!("8");
        current["expected_revision"] = serde_json::json!("2");
        current["action"] = serde_json::json!("expand");
        assert!(Command::parse(&current.to_string()).is_ok());
        let ack = serde_json::json!({"schema":1,"type":"window_group_applied","request_id":"11","target":action["target"],
            "group":"8","revision":"2","pose":{"position_m":[0.,2.,1.5],"rotation_xyzw":[0.,0.,0.,1.]},
            "physical_size":{"width_m":1.2,"height_m":0.9},"content_frame":"12"});
        assert!(
            !Command::parse(&ack.to_string())
                .unwrap()
                .needs_input_reset()
        );
        for (field, value) in [
            ("group", serde_json::json!("0")),
            ("revision", serde_json::json!(2)),
            ("content_frame", serde_json::json!("0")),
            (
                "physical_size",
                serde_json::json!({"width_m":5.,"height_m":0.9}),
            ),
            (
                "pose",
                serde_json::json!({"position_m":[0.,20.,1.5],"rotation_xyzw":[0.,0.,0.,1.]}),
            ),
        ] {
            let mut changed = ack.clone();
            changed[field] = value;
            assert!(Command::parse(&changed.to_string()).is_err());
        }
    }
    #[test]
    fn window_control_close_requires_full_positive_sdk_target_and_urgent_reset() {
        let source = r#"{"schema":1,"request_id":"1","type":"close_window_target","controls_revision":0,"target":{"session":"2","window":"3","scene":"4","generation":"5"}}"#;
        assert!(Command::parse(source).unwrap().needs_input_reset());
        for field in ["session", "window", "scene", "generation"] {
            let mut value: serde_json::Value = serde_json::from_str(source).unwrap();
            value["target"][field] = "0".into();
            assert!(Command::parse(&value.to_string()).is_err());
        }
        let mut value: serde_json::Value = serde_json::from_str(source).unwrap();
        value["target"]
            .as_object_mut()
            .unwrap()
            .remove("generation");
        assert!(Command::parse(&value.to_string()).is_err());
    }
    #[test]
    fn physical_control_requires_captured_revision_size_and_fixed_step() {
        let source = r#"{"schema":1,"request_id":"1","type":"set_physical_size_control","controls_revision":2,"target":{"session":"2","window":"3","scene":"4","generation":"5"},"expected_size_command":null,"expected_size":{"width_m":1.2,"height_m":0.9},"step":"larger"}"#;
        assert!(Command::parse(source).unwrap().needs_input_reset());
        for name in ["controls_revision", "expected_size", "step", "target"] {
            let mut value: serde_json::Value = serde_json::from_str(source).unwrap();
            value.as_object_mut().unwrap().remove(name);
            assert!(Command::parse(&value.to_string()).is_err());
        }
        for step in ["script", "resize_logical", "10"] {
            let mut value: serde_json::Value = serde_json::from_str(source).unwrap();
            value["step"] = step.into();
            assert!(Command::parse(&value.to_string()).is_err());
        }
        let mut value: serde_json::Value = serde_json::from_str(source).unwrap();
        value["expected_size"]["width_m"] = 3.0.into();
        assert!(Command::parse(&value.to_string()).is_err());
    }
    #[test]
    fn window_move_pose_requires_revision_full_target_and_valid_pose() {
        let source = r#"{"schema":1,"request_id":"1","type":"set_pose_control","controls_revision":2,"target":{"session":"2","window":"3","scene":"4","generation":"5"},"pose":{"position_m":[0,1.4,1.5],"rotation_xyzw":[0,0,0,1]}}"#;
        assert!(Command::parse(source).unwrap().needs_input_reset());
        let mut missing: serde_json::Value = serde_json::from_str(source).unwrap();
        missing.as_object_mut().unwrap().remove("controls_revision");
        assert!(Command::parse(&missing.to_string()).is_err());
        let mut old: serde_json::Value = serde_json::from_str(source).unwrap();
        old["target"]["generation"] = "0".into();
        assert!(Command::parse(&old.to_string()).is_err());
        let mut invalid: serde_json::Value = serde_json::from_str(source).unwrap();
        invalid["pose"]["position_m"][1] = (-1).into();
        assert!(Command::parse(&invalid.to_string()).is_err());
    }
    #[test]
    fn window_control_focus_uses_the_tracked_reset_epoch_and_theme_revision() {
        let source = r#"{"schema":1,"request_id":"2","type":"focus_window_control","controls_revision":3,"input_epoch":4,"target":{"session":"2","window":"3","scene":"4","generation":"5"}}"#;
        assert!(!Command::parse(source).unwrap().needs_input_reset());
        for field in ["input_epoch", "controls_revision"] {
            let mut value: serde_json::Value = serde_json::from_str(source).unwrap();
            value.as_object_mut().unwrap().remove(field);
            assert!(Command::parse(&value.to_string()).is_err());
        }
    }
    #[test]
    fn ime_envelope_rejects_cross_window_payload_and_hides_text_in_debug() {
        let value = serde_json::json!({"schema":1,"type":"ime","request_id":"1",
            "target":{"session":"1","window":"2","scene":"3","generation":"4"},
            "transaction":{"target":{"session":"1","window":"2","surface":"5","focus_token":"6",
                "input_epoch":0,"activation":1,"serial":4294967295u32},"edit_revision":1,"sequence":1,
                "commit":"private-ime-fixture","preedit":null,"delete_before":0,"delete_after":0}});
        let parsed = Command::parse(&value.to_string()).unwrap();
        assert!(!format!("{parsed:?}").contains("private-ime-fixture"));
        assert!(!parsed.needs_input_reset());
        for field in ["session", "window"] {
            let mut invalid = value.clone();
            invalid["transaction"]["target"][field] = "9".into();
            assert!(Command::parse(&invalid.to_string()).is_err());
        }
        let mut invalid = value;
        invalid["transaction"]["commit"] = "a".repeat(crate::ime::IME_TEXT_BYTES + 1).into();
        assert!(Command::parse(&invalid.to_string()).is_err());
    }
    #[test]
    fn launcher_commands_require_structured_arguments_and_snapshot_identity() {
        let mut value = serde_json::json!({"schema":1,"request_id":"1","type":"launch_terminal","session":"7",
            "target":{"kind":"temporary","definition":{"id":"example","label":"例","mode":"terminal","executable":"scripts/a b.sh","args":["", "a b", "$(touch never)"]}}});
        assert!(Command::parse(&value.to_string()).is_ok());
        value["target"]["definition"]["shell"] = "bash -c arbitrary".into();
        assert!(Command::parse(&value.to_string()).is_err());
        value["target"] =
            serde_json::json!({"kind":"registered","id":"example","revision":"a".repeat(64)});
        assert!(Command::parse(&value.to_string()).is_ok());
        value["target"]["revision"] = "old".into();
        assert!(Command::parse(&value.to_string()).is_err());
        let save = serde_json::json!({"schema":1,"request_id":"2","type":"catalog","session":"7","operation":{
            "kind":"save","path":".config/eidolon/launcher.toml","expected_revision":null,
            "definition":{"id":"example","label":"例","mode":"terminal","executable":"scripts/run"}}});
        assert!(Command::parse(&save.to_string()).is_ok());
    }
    #[test]
    fn theme_import_only_accepts_fixed_private_tokens_and_closed_selection() {
        for kind in [
            "begin_theme_import",
            "apply_theme_import",
            "cancel_theme_import",
        ] {
            let value = serde_json::json!({"schema":1,"request_id":"1","type":kind,"token":"0123456789abcdef0123456789abcdef"});
            assert!(Command::parse(&value.to_string()).is_ok());
            for token in [
                "../source",
                "/private/file",
                "0",
                "0123456789abcdef0123456789abcdeG",
            ] {
                let mut bad = value.clone();
                bad["token"] = token.into();
                assert!(Command::parse(&bad.to_string()).is_err());
            }
        }
        let value = serde_json::json!({"schema":1,"request_id":"1","type":"set_theme","selection":"builtin","reduced_motion":true});
        assert!(Command::parse(&value.to_string()).is_ok());
        let mut bad = value;
        bad["selection"] = "/some/path".into();
        assert!(Command::parse(&bad.to_string()).is_err());
    }
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
    fn saved_probe_wire_keeps_nested_validation_and_has_no_free_endpoint_command() {
        let key =
            include_str!("../../../android/app/src/test/resources/connection-profiles-v2.tsv")
                .lines()
                .find(|l| l.starts_with("linux-"))
                .unwrap()
                .split('\t')
                .nth(7)
                .unwrap();
        let profile = serde_json::json!({"schema":2,"installation":"1".repeat(32),"profile_id":"linux-probe","revision":"9007199254740993","display_name":"fixture","backend":"linux_ssh","host":"192.0.2.1","port":22,"username":"fixture","host_key":key});
        let mut config = RuntimeConfig {
            installation: Some("1".repeat(32)),
            debug_probe_profile_v2: Some(profile.to_string()),
            ..Default::default()
        };
        assert!(config.validate().is_ok());
        for (key, value) in [
            ("profile_id", serde_json::json!("other-profile")),
            ("backend", serde_json::json!("termux_local")),
            ("revision", serde_json::json!(9007199254740993_u64)),
            ("installation", serde_json::json!("2".repeat(32))),
        ] {
            let mut bad = profile.clone();
            bad[key] = value;
            config.debug_probe_profile_v2 = Some(bad.to_string());
            assert!(config.validate().is_err());
        }
        config.debug_probe_profile_v2 =
            Some(profile.to_string().trim_end_matches('}').to_owned() + ",\"schema\":2}");
        assert!(config.validate().is_err());
        assert!(
            Command::parse(r#"{"schema":1,"type":"connect_saved_probe","request_id":"7"}"#).is_ok()
        );
        assert!(
            Command::parse(
                r#"{"schema":1,"type":"connect_saved_probe","request_id":"7","host":"192.0.2.1"}"#
            )
            .is_err()
        );
    }
    #[test]
    fn linux_shm_probe_wire_has_only_fixed_scope_and_nonce() {
        for kind in [
            "linux_shm_probe",
            "linux_shm_sdk_probe",
            "linux_shm_recover",
        ] {
            let value = serde_json::json!({"schema":1,"type":kind,"request_id":"9007199254740993","session":"1","nonce":"1234567890abcdef"});
            assert!(Command::parse(&value.to_string()).is_ok());
            for (key, bad) in [
                ("host", serde_json::json!("192.0.2.1")),
                ("path", serde_json::json!("/tmp/x")),
                ("nonce", serde_json::json!("../bad")),
                ("session", serde_json::json!(1)),
            ] {
                let mut invalid = value.clone();
                invalid[key] = bad;
                assert!(Command::parse(&invalid.to_string()).is_err());
            }
        }
        assert!(
            RuntimeConfig {
                debug_linux_shm_pins: Some("{}".into()),
                ..Default::default()
            }
            .validate()
            .is_err()
        );
    }
    #[test]
    fn linux_gtk_probe_has_fixed_cases_and_no_free_host_path_or_argv() {
        for kind in ["linux_gtk_probe", "linux_gtk_recover"] {
            for case in ["input", "dialog", "resize"] {
                let good = serde_json::json!({"schema":1,"type":kind,"request_id":"9007199254740993","session":"2","nonce":"1234567890abcdef","case":case});
                assert!(Command::parse(&good.to_string()).is_ok());
                for (key, value) in [
                    ("case", serde_json::json!("foot")),
                    ("host", serde_json::json!("192.0.2.1")),
                    ("path", serde_json::json!("/fixture")),
                    ("argv", serde_json::json!(["/bin/sh"])),
                    ("session", serde_json::json!(2)),
                ] {
                    let mut bad = good.clone();
                    bad[key] = value;
                    assert!(Command::parse(&bad.to_string()).is_err());
                }
            }
        }
        assert!(
            RuntimeConfig {
                debug_linux_gtk_pins: Some("{}".into()),
                ..Default::default()
            }
            .validate()
            .is_err()
        );
        for case in [
            LinuxGtkCase::Input,
            LinuxGtkCase::Dialog,
            LinuxGtkCase::Resize,
        ] {
            assert_eq!(case.mode().linux_gtk_case(), Some(case));
            assert_ne!(case.mode(), AppMode::Pattern);
            assert_ne!(case.mode(), AppMode::Foot);
        }
    }
    #[test]
    fn normal_linux_wire_has_explicit_selection_and_no_arbitrary_launch_fields() {
        let selection = serde_json::json!({"scope":"a".repeat(64),"revision":"b".repeat(64),"app_id":"gtk3-desktop"});
        let launch = serde_json::json!({"schema":1,"type":"linux_desktop_launch","request_id":"9007199254740993","session":"2","selection":selection});
        assert!(matches!(
            Command::parse(&launch.to_string()),
            Ok(Command::LinuxDesktopLaunch { .. })
        ));
        for (key, value) in [
            ("argv", serde_json::json!(["/bin/sh"])),
            ("nonce", serde_json::json!("0123456789abcdef")),
            ("session", serde_json::json!(2)),
        ] {
            let mut bad = launch.clone();
            bad[key] = value;
            assert!(Command::parse(&bad.to_string()).is_err());
        }
        let mut bad = launch.clone();
        bad["selection"]["app_id"] = serde_json::json!("foot");
        assert!(Command::parse(&bad.to_string()).is_err());
        for kind in ["linux_desktop_inspect", "linux_desktop_recover"] {
            let command =
                serde_json::json!({"schema":1,"type":kind,"request_id":"3","session":"2"});
            assert!(Command::parse(&command.to_string()).is_ok());
        }
        assert!(
            Command::parse(
                r#"{"schema":1,"type":"connect","request_id":"1","profile_id":"remote-linux"}"#
            )
            .is_ok()
        );
        assert!(
            Command::parse(
                r#"{"schema":1,"type":"connect","request_id":"1","profile_id":"linux-probe"}"#
            )
            .is_err()
        );
        assert_eq!(AppMode::LinuxDesktop.as_str(), "linux_desktop");
        assert_eq!(AppMode::LinuxDesktop.linux_gtk_case(), None);
    }
    #[test]
    fn normal_linux_config_requires_pair_and_keeps_probe_namespace_independent() {
        let host_key =
            include_str!("../../../android/app/src/test/resources/connection-profiles-v2.tsv")
                .lines()
                .find(|line| line.starts_with("linux-"))
                .unwrap()
                .split('\t')
                .nth(7)
                .unwrap();
        let profile = serde_json::json!({"schema":2,"installation":"1".repeat(32),"profile_id":"remote-linux","revision":"9007199254740993","display_name":"normal","backend":"linux_ssh","host":"192.0.2.1","port":22,"username":"fixture","host_key":host_key});
        let mut config = RuntimeConfig {
            installation: Some("1".repeat(32)),
            linux_profile_v2: Some(profile.to_string()),
            linux_desktop_pins: Some("{}".into()),
            ..Default::default()
        };
        // Pin cryptographic/schema validation belongs to transport; model bounds the config wire.
        assert!(config.validate().is_ok());
        let mut probe = profile.clone();
        probe["profile_id"] = serde_json::json!("linux-probe");
        config.debug_probe_profile_v2 = Some(probe.to_string());
        assert!(config.validate().is_ok());
        config.linux_profile_v2 = Some(probe.to_string());
        assert!(config.validate().is_err());
        config.linux_profile_v2 = Some(profile.to_string());
        config.linux_desktop_pins = None;
        assert!(config.validate().is_err());
        config.linux_profile_v2 = None;
        config.linux_desktop_pins = Some("{}".into());
        assert!(config.validate().is_err());
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
