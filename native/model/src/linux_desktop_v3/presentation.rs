//! Pure startup/evidence model; fixture JSON is never remote origin authority.
use super::*;
use crate::{
    ids::{CommandSeq, RuntimeHandle, SceneEpoch, WindowId},
    surface::TargetKey,
};
use std::collections::{BTreeMap, BTreeSet};

pub const MAIN_WAIT_MS: u64 = 20_000;
pub const SDK_WAIT_MS: u64 = 10_000;

fn nullable<'de, D, T>(d: D) -> std::result::Result<Option<T>, D::Error>
where
    D: de::Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(d)
}

object_record!(pub NativeOrigin, NativeOriginWire, {
    runtime: RuntimeHandle,
    session: SessionId,
    request: CommandSeq,
    nonce: Nonce,
    scope: Hash,
    app_id: AppId,
});
impl NativeOrigin {
    pub fn parse(raw: &[u8]) -> Result<Self> {
        decode(raw, MAX_RECORD_BYTES)
    }
    record_methods!();
    /// Native-only origin construction from a captured, validated context.
    /// The request and runtime must come from the native command dispatcher;
    /// neither is inferred from the remote catalog or a UI payload.
    pub fn capture(
        runtime: RuntimeHandle,
        request: CommandSeq,
        context: &Context,
        selection: &Selection,
        pins: &Pins,
    ) -> Result<Self> {
        selection.matches(context, pins)?;
        let context_record = context.record();
        let selection_record = selection.record();
        let raw = canonical_json(&json!({
            "runtime": runtime,
            "session": context_record["session"],
            "request": request,
            "nonce": context_record["nonce"],
            "scope": context.scope(),
            "app_id": selection_record["app_id"],
        }))?;
        let origin = Self::parse(&raw)?;
        origin.matches(context, selection, pins)?;
        Ok(origin)
    }
    pub fn runtime(&self) -> RuntimeHandle {
        self.runtime
    }
    pub fn session(&self) -> SessionId {
        self.session
    }
    pub fn request(&self) -> CommandSeq {
        self.request
    }
    pub fn app_id(&self) -> AppId {
        self.app_id
    }
    pub fn matches(&self, context: &Context, selection: &Selection, pins: &Pins) -> Result<()> {
        selection.matches(context, pins)?;
        require(
            self.session == context.session
                && self.nonce == context.nonce
                && self.scope.as_str() == context.scope()
                && self.app_id == selection.app_id,
            "APP_ORIGIN_UNKNOWN",
        )
    }
    fn main_app_id(&self) -> String {
        match self.app_id {
            AppId::Editor => "mousepad".into(),
            AppId::Demo => format!(
                "io.github.tinmachine.eidolon.linuxdesktop.r{}{}",
                self.nonce.as_str(),
                self.nonce.as_str()
            ),
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Parent {
    None {},
    Resolved { window: WindowId },
    Unresolved {},
    Invalid {},
}
#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum ParentWire {
    None {},
    Resolved { window: WindowId },
    Unresolved {},
    Invalid {},
}
impl<'de> Deserialize<'de> for Parent {
    fn deserialize<D: de::Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
        let wire: ParentWire = object_only(d)?;
        Ok(match wire {
            ParentWire::None {} => Self::None {},
            ParentWire::Resolved { window } => Self::Resolved { window },
            ParentWire::Unresolved {} => Self::Unresolved {},
            ParentWire::Invalid {} => Self::Invalid {},
        })
    }
}
impl Parent {
    pub fn parse(raw: &[u8]) -> Result<Self> {
        decode(raw, MAX_RECORD_BYTES)
    }
}
pub fn parse_target(raw: &[u8]) -> Result<TargetKey> {
    object_only(parse_json(raw, MAX_RECORD_BYTES)?).map_err(|_| Error("CONTRACT_INVALID"))
}
#[derive(Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Role {
    Main,
    Dialog,
}
string_enum!(Role, { Main => "main", Dialog => "dialog" });
#[derive(Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Life {
    Mapped,
    Unmapped,
    Destroyed,
}
string_enum!(Life, { Mapped => "mapped", Unmapped => "unmapped", Destroyed => "destroyed" });
object_record!(Window, WindowWire, {
    origin: NativeOrigin,
    window: WindowId,
    role: Role,
    life: Life,
    #[serde(deserialize_with = "nullable")]
    app_id: Option<String>,
    parent: Parent,
});
#[derive(Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
enum Action {
    Create,
}
string_enum!(Action, { Create => "create" });
object_record!(Create, CreateWire, {
    runtime: RuntimeHandle,
    #[serde(deserialize_with = "object_only")]
    target: TargetKey,
    command_seq: CommandSeq,
    action: Action,
});
object_record!(Panel, PanelWire, { create: Create, applied: bool, error: bool });
object_record!(Ack, AckWire, { create: Create, success: bool });
object_record!(Sdk, SdkWire, {
    runtime: RuntimeHandle,
    #[serde(deserialize_with = "object_only")]
    target: TargetKey,
    bound: bool,
    diagnostic: bool,
});
object_record!(Frame, FrameWire, {
    runtime: RuntimeHandle,
    #[serde(deserialize_with = "object_only")]
    target: TargetKey,
    frames: u64,
    paused: bool,
    presentation_paused: bool,
});
object_record!(Evidence, EvidenceWire, {
    #[serde(deserialize_with = "nullable")]
    panel: Option<Panel>,
    #[serde(deserialize_with = "nullable")]
    ack: Option<Ack>,
    #[serde(deserialize_with = "nullable")]
    sdk: Option<Sdk>,
    #[serde(deserialize_with = "nullable")]
    frame: Option<Frame>,
});
impl Evidence {
    fn empty(&self) -> bool {
        self.panel.is_none() && self.ack.is_none() && self.sdk.is_none() && self.frame.is_none()
    }
}
object_record!(Terminal, TerminalWire, { origin: NativeOrigin, reason: StopReason });
contract_record!(Observation, ObservationWire, {
    now_ms: u64,
    origin: NativeOrigin,
    #[serde(deserialize_with = "nullable")]
    started: Option<NativeOrigin>,
    #[serde(deserialize_with = "nullable")]
    terminal: Option<Terminal>,
    cancelled: bool,
    cleanup_pending: bool,
    activity_visible: bool,
    #[serde(deserialize_with = "nullable")]
    scene: Option<SceneEpoch>,
    xr_focused: bool,
    global_window_count: u32,
    windows: Vec<Window>,
    #[serde(deserialize_with = "nullable")]
    current_create: Option<Create>,
    evidence: Evidence,
    fallback: bool,
});
impl Observation {
    pub fn parse(raw: &[u8]) -> Result<Self> {
        let v: Self = decode(raw, MAX_RECORD_BYTES)?;
        v.validate()?;
        Ok(v)
    }
    fn validate(&self) -> Result<()> {
        require(
            self.now_ms <= i64::MAX as u64
                && self.global_window_count <= 9
                && self.windows.len() <= self.global_window_count as usize
                && self
                    .windows
                    .iter()
                    .map(|w| w.window)
                    .collect::<BTreeSet<_>>()
                    .len()
                    == self.windows.len()
                && self
                    .windows
                    .iter()
                    .all(|w| w.app_id.as_ref().is_none_or(|id| id.chars().count() <= 128))
                && self
                    .evidence
                    .frame
                    .as_ref()
                    .is_none_or(|f| f.frames <= i64::MAX as u64),
            "CONTRACT_INVALID",
        )
    }
    record_methods!();
}

/// Native-only facts. The identity is never inferred from window title, app_id,
/// SDK Entity ID, or a JSON fixture.
#[derive(Clone, PartialEq, Eq)]
pub struct NativeCreateIdentity {
    pub runtime: RuntimeHandle,
    pub target: TargetKey,
    pub command_seq: CommandSeq,
}
impl NativeCreateIdentity {
    fn into_create(self) -> Create {
        Create {
            runtime: self.runtime,
            target: self.target,
            command_seq: self.command_seq,
            action: Action::Create,
        }
    }
}

pub struct NativeWindowFact {
    pub origin: NativeOrigin,
    pub window: WindowId,
    pub role: Role,
    pub life: Life,
    pub app_id: Option<String>,
    pub parent: Parent,
}
pub struct NativePanelFact {
    pub create: NativeCreateIdentity,
    pub applied: bool,
    pub error: bool,
}
pub struct NativeAckFact {
    pub create: NativeCreateIdentity,
    pub success: bool,
}
pub struct NativeSdkFact {
    pub runtime: RuntimeHandle,
    pub target: TargetKey,
    pub bound: bool,
    pub diagnostic: bool,
}
/// The caller must obtain windows_mode and content_frame_seen from the
/// renderer's *current* target report, not from a cumulative frame counter.
pub struct NativeFrameFact {
    pub runtime: RuntimeHandle,
    pub target: TargetKey,
    pub frames: u64,
    pub paused: bool,
    pub presentation_paused: bool,
    pub windows_mode: bool,
    pub content_frame_seen: bool,
}

#[derive(Clone, PartialEq, Eq)]
pub struct CurrentEvidence {
    pub runtime: RuntimeHandle,
    pub target: TargetKey,
    pub create_command: CommandSeq,
    pub acknowledged: bool,
    pub binding_present: bool,
    pub renderer_frame_seen: bool,
}

/// Build Observation from native owner snapshots without serializing a fixture
/// or manufacturing a matching identity for evidence that came from elsewhere.
pub struct NativeObservationBuilder {
    now_ms: u64,
    origin: NativeOrigin,
    started: Option<NativeOrigin>,
    terminal: Option<(NativeOrigin, StopReason)>,
    cancelled: bool,
    cleanup_pending: bool,
    activity_visible: bool,
    scene: Option<SceneEpoch>,
    xr_focused: bool,
    global_window_count: u32,
    windows: Vec<NativeWindowFact>,
    current_create: Option<NativeCreateIdentity>,
    panel: Option<NativePanelFact>,
    ack: Option<NativeAckFact>,
    sdk: Option<NativeSdkFact>,
    frame: Option<NativeFrameFact>,
    fallback: bool,
}
impl NativeObservationBuilder {
    fn new(origin: NativeOrigin, now_ms: u64) -> Self {
        Self {
            origin,
            now_ms,
            started: None,
            terminal: None,
            cancelled: false,
            cleanup_pending: false,
            activity_visible: false,
            scene: None,
            xr_focused: false,
            global_window_count: 0,
            windows: Vec::new(),
            current_create: None,
            panel: None,
            ack: None,
            sdk: None,
            frame: None,
            fallback: false,
        }
    }
    pub fn lifecycle(mut self, visible: bool, scene: Option<SceneEpoch>, focused: bool) -> Self {
        self.activity_visible = visible;
        self.scene = scene;
        self.xr_focused = focused;
        self
    }
    pub fn transport(
        mut self,
        started: Option<NativeOrigin>,
        terminal: Option<(NativeOrigin, StopReason)>,
        cancelled: bool,
        cleanup_pending: bool,
    ) -> Self {
        self.started = started;
        self.terminal = terminal;
        self.cancelled = cancelled;
        self.cleanup_pending = cleanup_pending;
        self
    }
    pub fn windows(mut self, global_count: u32, windows: Vec<NativeWindowFact>) -> Self {
        self.global_window_count = global_count;
        self.windows = windows;
        self
    }
    pub fn current_create(mut self, create: Option<NativeCreateIdentity>) -> Self {
        self.current_create = create;
        self
    }
    pub fn panel(mut self, panel: Option<NativePanelFact>) -> Self {
        self.panel = panel;
        self
    }
    pub fn ack(mut self, ack: Option<NativeAckFact>) -> Self {
        self.ack = ack;
        self
    }
    pub fn sdk(mut self, sdk: Option<NativeSdkFact>) -> Self {
        self.sdk = sdk;
        self
    }
    pub fn frame(mut self, frame: Option<NativeFrameFact>) -> Self {
        self.frame = frame;
        self
    }
    pub fn fallback(mut self, allowed: bool) -> Self {
        self.fallback = allowed;
        self
    }
    pub fn finish(self) -> Result<Observation> {
        require(
            self.started.as_ref().is_none_or(|v| v == &self.origin)
                && self.terminal.as_ref().is_none_or(|v| v.0 == self.origin)
                && self.windows.iter().all(|v| v.origin == self.origin),
            "APP_ORIGIN_UNKNOWN",
        )?;
        let current = self
            .current_create
            .clone()
            .map(NativeCreateIdentity::into_create);
        if let Some(identity) = &current {
            require(
                identity.runtime == self.origin.runtime
                    && identity.target.session == self.origin.session
                    && Some(identity.target.scene) == self.scene
                    && self
                        .panel
                        .as_ref()
                        .is_none_or(|v| v.create.clone().into_create() == *identity)
                    && self
                        .ack
                        .as_ref()
                        .is_none_or(|v| v.create.clone().into_create() == *identity)
                    && self.sdk.as_ref().is_none_or(|v| {
                        v.runtime == identity.runtime && v.target == identity.target
                    })
                    && self.frame.as_ref().is_none_or(|v| {
                        v.runtime == identity.runtime && v.target == identity.target
                    }),
                "PRESENTATION_STALE",
            )?;
        } else {
            require(
                self.panel.is_none()
                    && self.ack.is_none()
                    && self.sdk.is_none()
                    && self.frame.is_none()
                    && !self.fallback,
                "PRESENTATION_STALE",
            )?;
        }
        let observation = Observation {
            now_ms: self.now_ms,
            origin: self.origin,
            started: self.started,
            terminal: self
                .terminal
                .map(|(origin, reason)| Terminal { origin, reason }),
            cancelled: self.cancelled,
            cleanup_pending: self.cleanup_pending,
            activity_visible: self.activity_visible,
            scene: self.scene,
            xr_focused: self.xr_focused,
            global_window_count: self.global_window_count,
            windows: self
                .windows
                .into_iter()
                .map(|v| Window {
                    origin: v.origin,
                    window: v.window,
                    role: v.role,
                    life: v.life,
                    app_id: v.app_id,
                    parent: v.parent,
                })
                .collect(),
            current_create: current,
            evidence: Evidence {
                panel: self.panel.map(|v| Panel {
                    create: v.create.into_create(),
                    applied: v.applied,
                    error: v.error,
                }),
                ack: self.ack.map(|v| Ack {
                    create: v.create.into_create(),
                    success: v.success,
                }),
                sdk: self.sdk.map(|v| Sdk {
                    runtime: v.runtime,
                    target: v.target,
                    bound: v.bound,
                    diagnostic: v.diagnostic,
                }),
                // A GL swap without window content is not a presentation frame.
                frame: self.frame.and_then(|v| {
                    (v.windows_mode && v.content_frame_seen).then_some(Frame {
                        runtime: v.runtime,
                        target: v.target,
                        frames: v.frames,
                        paused: v.paused,
                        presentation_paused: v.presentation_paused,
                    })
                }),
            },
            fallback: self.fallback,
        };
        observation.validate()?;
        Ok(observation)
    }
}

impl Observation {
    pub fn native_builder(origin: NativeOrigin, now_ms: u64) -> NativeObservationBuilder {
        NativeObservationBuilder::new(origin, now_ms)
    }
    pub fn origin(&self) -> &NativeOrigin {
        &self.origin
    }
    pub fn has_current_create(&self) -> bool {
        self.current_create.is_some()
    }
    /// Exact current Create, accepted ACK, bound app SDK target, and fresh content
    /// frame are required. Never use a previous target's successful facts.
    pub fn current_evidence(&self) -> Option<CurrentEvidence> {
        let current = self.current_create.as_ref()?;
        let e = &self.evidence;
        let compatible = e.panel.as_ref().is_none_or(|p| p.create == *current)
            && e.ack.as_ref().is_none_or(|a| a.create == *current)
            && e.sdk
                .as_ref()
                .is_none_or(|s| s.runtime == current.runtime && s.target == current.target)
            && e.frame
                .as_ref()
                .is_none_or(|f| f.runtime == current.runtime && f.target == current.target);
        if !compatible
            || Some(current.target.scene) != self.scene
            || current.runtime != self.origin.runtime
            || current.target.session != self.origin.session
        {
            return None;
        }
        Some(CurrentEvidence {
            runtime: current.runtime,
            target: current.target,
            create_command: current.command_seq,
            acknowledged: e.panel.as_ref().is_some_and(|p| p.applied && !p.error)
                && e.ack.as_ref().is_some_and(|a| a.success),
            binding_present: e.sdk.as_ref().is_some_and(|s| s.bound && !s.diagnostic),
            renderer_frame_seen: e
                .frame
                .as_ref()
                .is_some_and(|f| f.frames > 0 && !f.paused && !f.presentation_paused),
        })
    }
}

fn topology(windows: &[Window], main: WindowId, origin: &NativeOrigin) -> bool {
    let nodes: BTreeMap<_, _> = windows.iter().map(|w| (w.window, w)).collect();
    for window in windows.iter().filter(|w| w.life != Life::Destroyed) {
        if window.role == Role::Main {
            if window.window != main
                || !matches!(window.parent, Parent::None {})
                || window.app_id.as_deref() != Some(origin.main_app_id().as_str())
            {
                return false;
            }
            continue;
        }
        if origin.app_id == AppId::Demo
            && window.app_id.as_deref() != Some(format!("{}.dialog", origin.main_app_id()).as_str())
        {
            return false;
        }
        let mut current = window;
        let mut seen = BTreeSet::from([window.window]);
        loop {
            let Parent::Resolved { window: parent } = current.parent else {
                return false;
            };
            if seen.contains(&parent) {
                return false;
            }
            let Some(next) = nodes.get(&parent) else {
                return false;
            };
            if next.life == Life::Destroyed || &next.origin != origin {
                return false;
            }
            if next.window == main {
                break;
            }
            if next.role != Role::Dialog {
                return false;
            }
            seen.insert(next.window);
            current = next;
        }
    }
    true
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PresentationState {
    AwaitingMain,
    AwaitingSdk,
    Ready,
    Stopping,
    Pending,
}
string_enum!(PresentationState, {
    AwaitingMain => "awaiting_main",
    AwaitingSdk => "awaiting_sdk",
    Ready => "ready",
    Stopping => "stopping",
    Pending => "pending"
});

#[derive(Clone)]
pub struct Startup {
    origin: NativeOrigin,
    t0_ms: u64,
    before: Vec<WindowId>,
    last_ms: u64,
    phase: PresentationState,
    main: Option<WindowId>,
    first_adopted_ms: Option<u64>,
    started: bool,
    roles: BTreeMap<WindowId, Role>,
    current_create: Option<Create>,
    current_retired: bool,
    startup_completed: bool,
    first_failure: Option<Reason>,
    cleanup_pending: bool,
    fallback_used: bool,
}
impl Startup {
    pub fn begin(origin: NativeOrigin, t0_ms: u64, before: Vec<WindowId>) -> Result<Self> {
        require(
            t0_ms <= i64::MAX as u64 - MAIN_WAIT_MS - SDK_WAIT_MS
                && before.len() <= 9
                && before.iter().collect::<BTreeSet<_>>().len() == before.len(),
            "CONTRACT_INVALID",
        )?;
        Ok(Self {
            origin,
            t0_ms,
            before,
            last_ms: t0_ms,
            phase: PresentationState::AwaitingMain,
            main: None,
            first_adopted_ms: None,
            started: false,
            roles: BTreeMap::new(),
            current_create: None,
            current_retired: false,
            startup_completed: false,
            first_failure: None,
            cleanup_pending: false,
            fallback_used: false,
        })
    }
    pub fn origin(&self) -> &NativeOrigin {
        &self.origin
    }

    /// Called exclusively after the native/SDK retirement proof for a
    /// destroyed dialog WindowId. The compositor's WindowId allocator never
    /// reuses an ID within one Session. This releases the B1 role cache
    /// without weakening remap or main-identity invariants.
    /// B1 golden vectors retain their existing semantics and role histories.
    pub fn retire_confirmed_dialog(&mut self, window: WindowId) -> Result<()> {
        require(self.main != Some(window) && !self.before.contains(&window),
            "APP_WINDOW_UNSUPPORTED")?;
        self.roles.remove(&window);
        Ok(())
    }

    pub fn role_history_len(&self) -> usize {
        self.roles.len()
    }

    pub fn fail_observation(&self, error: &Error, cleanup_pending: bool) -> Self {
        let mut stopped = self.clone();
        stopped.cleanup_pending |= cleanup_pending;
        let reason = match error.0 {
            "APP_ORIGIN_UNKNOWN" => Reason::AppOriginUnknown,
            "APP_WINDOW_UNSUPPORTED" => Reason::AppWindowUnsupported,
            _ => Reason::AppPresentationFailed,
        };
        stopped.stop(Some(reason))
    }
    pub fn summary(&self) -> Value {
        json!({"state":self.phase,"main":self.main,"first_adopted_ms":self.first_adopted_ms,
            "startup_completed":self.startup_completed,"first_failure":self.first_failure,
            "cleanup_pending":self.cleanup_pending,"fallback_used":self.fallback_used})
    }
    fn stop(mut self, reason: Option<Reason>) -> Self {
        self.phase = if self.cleanup_pending {
            PresentationState::Pending
        } else {
            PresentationState::Stopping
        };
        if self.first_failure.is_none() {
            self.first_failure = reason;
        }
        self
    }
    pub fn advance(&self, v: &Observation) -> Result<Self> {
        v.validate()?;
        require(v.now_ms >= self.last_ms, "CLOCK_STALE")?;
        require(
            v.origin == self.origin
                && v.started.as_ref().is_none_or(|o| o == &self.origin)
                && v.terminal.as_ref().is_none_or(|t| t.origin == self.origin)
                && v.windows.iter().all(|w| w.origin == self.origin),
            "APP_ORIGIN_UNKNOWN",
        )?;
        let mut s = self.clone();
        s.last_ms = v.now_ms;
        s.cleanup_pending = v.cleanup_pending;
        if matches!(
            self.phase,
            PresentationState::Stopping | PresentationState::Pending
        ) {
            return Ok(s.stop(None));
        }
        // Validate scope first. A foreign terminal must not cancel this owner.
        if v.terminal.is_some() {
            let reason = (!self.startup_completed).then_some(Reason::AppStartupCancelled);
            return Ok(s.stop(reason));
        }
        if v.cancelled
            || (!self.startup_completed
                && (!v.activity_visible || v.scene.is_none() || !v.xr_focused))
        {
            let reason = (!self.startup_completed).then_some(Reason::AppStartupCancelled);
            return Ok(s.stop(reason));
        }
        if !self.startup_completed {
            let deadline = self
                .first_adopted_ms
                .map_or(self.t0_ms + MAIN_WAIT_MS, |t| t + SDK_WAIT_MS);
            if v.now_ms >= deadline {
                let reason = if self.main.is_none() {
                    Reason::AppLaunchTimeout
                } else {
                    Reason::AppPresentationTimeout
                };
                return Ok(s.stop(Some(reason)));
            }
        }
        if v.cleanup_pending {
            return Ok(s.stop(Some(Reason::AppPresentationFailed)));
        }
        for w in &v.windows {
            require(
                s.roles.get(&w.window).is_none_or(|role| *role == w.role),
                "APP_WINDOW_UNSUPPORTED",
            )?;
            s.roles.insert(w.window, w.role);
        }
        let e = &v.evidence;
        if let Some(current) = &v.current_create {
            require(
                current.runtime == self.origin.runtime
                    && current.target.session == self.origin.session
                    && Some(current.target.scene) == v.scene,
                "PRESENTATION_STALE",
            )?;
            if let Some(old) = &self.current_create
                && current != old
            {
                require(
                    current.target.newer_than(old.target) && current.command_seq > old.command_seq,
                    "PRESENTATION_STALE",
                )?;
            }
            require(
                e.panel.as_ref().is_none_or(|p| p.create == *current)
                    && e.ack.as_ref().is_none_or(|a| a.create == *current)
                    && e.sdk.as_ref().is_none_or(|sdk| {
                        sdk.runtime == current.runtime && sdk.target == current.target
                    })
                    && e.frame
                        .as_ref()
                        .is_none_or(|f| f.runtime == current.runtime && f.target == current.target),
                "PRESENTATION_STALE",
            )?;
        } else {
            require(e.empty() && !v.fallback, "PRESENTATION_STALE")?;
        }
        s.started |= v.started.is_some();
        let roots: Vec<_> = v
            .windows
            .iter()
            .filter(|w| w.role == Role::Main && w.life != Life::Destroyed)
            .collect();
        if roots.len() > 1
            || roots.iter().any(|w| {
                !matches!(w.parent, Parent::None {})
                    || w.app_id.as_deref() != Some(self.origin.main_app_id().as_str())
                    || self.before.contains(&w.window)
            })
        {
            return Ok(s.stop(Some(Reason::AppWindowUnsupported)));
        }
        if s.main.is_none() {
            if !s.started || roots.first().is_none_or(|w| w.life != Life::Mapped) {
                require(v.current_create.is_none(), "PRESENTATION_STALE")?;
                return Ok(s);
            }
            s.main = Some(roots[0].window);
            s.first_adopted_ms = Some(v.now_ms);
            s.phase = PresentationState::AwaitingSdk;
        }
        let main_id = s.main.expect("adopted above");
        let Some(main) = v.windows.iter().find(|w| w.window == main_id) else {
            return Ok(s.stop(Some(Reason::AppWindowUnsupported)));
        };
        if main.life == Life::Destroyed || !topology(&v.windows, main_id, &self.origin) {
            return Ok(s.stop(Some(Reason::AppWindowUnsupported)));
        }
        if let Some(current) = &v.current_create {
            require(current.target.window == main_id, "PRESENTATION_STALE")?;
            let same = self.current_create.as_ref() == Some(current);
            require(
                !(self.current_retired && same && main.life == Life::Mapped),
                "PRESENTATION_STALE",
            )?;
            s.current_create = Some(current.clone());
            if !same {
                s.current_retired = false;
            }
        }
        if (v.current_create.is_none() || main.life != Life::Mapped) && s.current_create.is_some() {
            s.current_retired = true;
        }
        let failed =
            e.panel.as_ref().is_some_and(|p| p.error) || e.ack.as_ref().is_some_and(|a| !a.success);
        if v.fallback {
            // Native authorizes the one retry at a failing Create; retire it.
            if !failed || s.fallback_used || self.startup_completed {
                return Ok(s.stop(Some(Reason::AppPresentationFailed)));
            }
            s.phase = PresentationState::AwaitingSdk;
            s.fallback_used = true;
            s.current_retired = true;
            return Ok(s);
        }
        if failed {
            return Ok(s.stop(Some(Reason::AppPresentationFailed)));
        }
        let ready = main.life == Life::Mapped
            && v.current_create.is_some()
            && e.panel.as_ref().is_some_and(|p| p.applied)
            && e.ack.as_ref().is_some_and(|a| a.success)
            && e.sdk
                .as_ref()
                .is_some_and(|sdk| sdk.bound && !sdk.diagnostic)
            && e.frame
                .as_ref()
                .is_some_and(|f| f.frames > 0 && !f.paused && !f.presentation_paused)
            && v.activity_visible
            && v.scene.is_some()
            && v.xr_focused;
        s.phase = if ready {
            PresentationState::Ready
        } else {
            PresentationState::AwaitingSdk
        };
        s.startup_completed |= ready;
        Ok(s)
    }
}
