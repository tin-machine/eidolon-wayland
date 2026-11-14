use crate::diagnostics::{Event, Snapshot};
use eidolon_model::{
    commands::{Command, RuntimeConfig},
    limits,
    state::{RuntimeState, Status, XrState},
};
use std::{
    collections::VecDeque,
    panic::{AssertUnwindSafe, catch_unwind},
    sync::{
        Arc, Mutex, MutexGuard,
        atomic::{AtomicBool, Ordering},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

struct Shared {
    // surroundingを含む。diagnostics::Snapshot/Eventへコピーしない。
    ime_ui: Option<eidolon_model::ime::ImeSnapshot>,
    shutdown: crate::shutdown::Watch,
    sdk_bindings: Vec<eidolon_model::surface::TargetKey>,
    // SDK登録はSurface作成より先で、Surface世代の交換/退役後もownerKeyが残る。
    // 現在のrender targetやsession/window番号から診断所有を推測しない。
    diagnostic_sdk_bindings: Vec<eidolon_model::surface::TargetKey>,
    sdk_evidence: crate::sdk_evidence::Ledger,
    v3_retirement: Option<crate::v3_retirement::Owner>,
    v3_finalizer_workers: usize,
    v3_recovery_local_proven: bool,
    last_scene: Option<eidolon_model::ids::SceneEpoch>,
    panels: crate::panels::Controller,
    workspace: crate::workspace::Controller,
    credentials: eidolon_transport::credentials::CredentialSlot,
    // 設定bindingは通常snapshot/eventへ出さない。
    credential_scope: Option<eidolon_transport::session::CredentialScope>,
    // 最新のSDK可視性。遅れて処理する通常commandで描画pauseを巻き戻さない。
    render_visibility: [bool; 2],
    snapshot: Snapshot,
    commands: VecDeque<Command>,
    events: VecDeque<Event>,
    // 通常eventが満杯でもfailure/stoppedを失わない、最大2件の予約枠。
    terminal: VecDeque<Event>,
}

/// 捕捉した catalog 選択と native 枠は transport Start の enqueue より前に確定する。
struct DesktopLaunch {
    session: eidolon_model::ids::SessionId,
    request: eidolon_model::ids::CommandSeq,
    nonce: String,
    selection: eidolon_model::linux_desktop::Selection,
    lease: eidolon_model::window_slots::Lease,
}
impl Shared {
    fn reclaim_v3(
        &mut self,
        current: &mut crate::session::Session,
        observer: Option<&mut crate::startup_observation::V3PresentationObserver>,
        render: &eidolon_render::Controller,
        closing: bool,
    ) -> Option<crate::v3_retirement::OriginRetirementProof> {
        if closing {
            self.sdk_evidence.seal();
            if let Some(owner) = self.v3_retirement.as_mut()
                && let Err(error) = owner.seal(render)
                && error != "DESKTOP_LOCAL_RETIREMENT_PENDING"
            {
                self.snapshot.desktop_v3_cleanup_error.get_or_insert(error);
            }
        }
        if let Err(error) = self.sync_v3_targets(render)
            && error != "DESKTOP_LOCAL_RETIREMENT_PENDING"
        {
            self.snapshot.desktop_v3_cleanup_error.get_or_insert(error);
            current.fail(error);
        }
        let owner = self.v3_retirement.as_mut()?;
        let before = current.v3_pending_retirements();
        if let Some(observer) = observer
            && let Err(error) = owner.collect_windows(current, observer, render)
        {
            self.snapshot.desktop_v3_cleanup_error.get_or_insert(error);
        }
        let after = current.v3_pending_retirements();
        for window in before {
            if !after.contains(&window) {
                self.sdk_evidence.forget_window(window);
            }
        }
        self.snapshot.desktop_v3_pending_targets = owner.pending_targets();
        owner.poll_origin(current, render)
    }

    fn observe_v3(
        &mut self,
        current: &mut crate::session::Session,
        observer: &mut crate::startup_observation::V3PresentationObserver,
        transport: &eidolon_transport::session::Snapshot,
        render: &eidolon_render::Snapshot,
        now_ms: u64,
        closing: bool,
    ) -> Result<bool, &'static str> {
        use eidolon_model::linux_desktop_v3::presentation::{
            NativeAckFact, NativeCreateIdentity, NativeFrameFact, NativePanelFact, NativeSdkFact,
            Observation, PresentationState, Role,
        };
        let origin = current.v3_origin().cloned().ok_or("APP_ORIGIN_UNKNOWN")?;
        let transport = transport.desktop_v3_transport.as_ref().filter(|proof| {
            proof.session == origin.session() && proof.launch_request == Some(origin.request())
        });
        let started = transport.is_some_and(|proof| {
            proof.started_observed && !proof.terminal_observed && proof.error.is_none()
        });
        let terminal = transport
            .and_then(|proof| proof.terminal_reason)
            .map(|reason| (origin.clone(), reason));
        let cut = if closing {
            crate::linux_desktop_v3::I3WindowCut {
                origin: origin.clone(),
                global_window_count: 0,
                windows: vec![],
                admitted: Default::default(),
                fixed_main: observer.snapshot().map_err(|error| error.0)?.main,
                admission_open: false,
            }
        } else {
            current.v3_cut()?
        };
        let candidate = cut.fixed_main.or_else(|| {
            cut.windows
                .iter()
                .find(|window| window.role == Role::Main)
                .map(|window| window.window)
        });
        let panel = if closing {
            None
        } else {
            self.panels.snapshot().into_iter().find(|panel| {
                panel.action == eidolon_model::commands::PanelAction::Create
                    && panel.target.session == origin.session()
                    && Some(panel.target.scene) == self.snapshot.scene
                    && candidate == Some(panel.target.window)
            })
        };
        let create = panel.as_ref().map(|panel| NativeCreateIdentity {
            runtime: origin.runtime(),
            target: panel.target,
            command_seq: panel.command_seq,
        });
        let panel_fact =
            panel
                .as_ref()
                .zip(create.as_ref())
                .map(|(panel, create)| NativePanelFact {
                    create: create.clone(),
                    applied: panel.applied,
                    error: panel.error.is_some(),
                });
        // Applied is stored only at native panels.acknowledge, never at JNI enqueue.
        let ack = panel
            .as_ref()
            .zip(create.as_ref())
            .and_then(|(panel, create)| {
                panel.applied.then(|| NativeAckFact {
                    create: create.clone(),
                    success: panel.error.is_none(),
                })
            });
        let sdk = create.as_ref().map(|create| NativeSdkFact {
            runtime: create.runtime,
            target: create.target,
            bound: self.sdk_bindings.contains(&create.target)
                && self.sdk_evidence.current(create.target, create.command_seq),
            diagnostic: self.diagnostic_sdk_bindings.contains(&create.target),
        });
        let frame = create.as_ref().and_then(|create| {
            render
                .targets
                .iter()
                .find(|frame| frame.key == create.target)
                .map(|frame| NativeFrameFact {
                    runtime: create.runtime,
                    target: frame.key,
                    frames: frame.frames,
                    paused: render.paused,
                    presentation_paused: frame.presentation_paused,
                    windows_mode: render.mode == eidolon_render::RenderMode::Windows
                        && frame.role == eidolon_render::RenderMode::Windows,
                    content_frame_seen: frame.content_frame.is_some()
                        && cut.admitted.contains(&frame.key.window),
                })
        });
        let observation = Observation::native_builder(origin.clone(), now_ms)
            .lifecycle(
                self.snapshot.activity_visible,
                self.snapshot.scene,
                self.snapshot.xr_state == XrState::Focused,
            )
            .transport(started.then_some(origin), terminal, closing, closing)
            .windows(cut.global_window_count, cut.windows)
            .current_create(create)
            .panel(panel_fact)
            .ack(ack)
            .sdk(sdk)
            .frame(frame)
            .finish()
            .map_err(|error| error.0)?;
        let result = observer.tick(&observation).map_err(|error| error.0)?;
        if let Some(main) = result.newly_adopted_main {
            current.v3_commit_main(main)?;
        }
        let input_open = !closing
            && started
            && cut.admission_open
            && result.presentation.startup_completed
            && !matches!(
                result.presentation.state,
                PresentationState::Stopping | PresentationState::Pending
            );
        let allowed = if input_open {
            self.panels
                .snapshot()
                .into_iter()
                .filter(|panel| {
                    cut.admitted.contains(&panel.target.window)
                        && self.v3_target_ready(panel.target, render)
                })
                .map(|panel| panel.target.window)
                .collect()
        } else {
            Default::default()
        };
        current.set_v3_input_allowed(allowed);
        let stop = matches!(
            result.presentation.state,
            PresentationState::Stopping | PresentationState::Pending
        );
        self.snapshot.desktop_v3_presentation = Some(result.presentation);
        self.snapshot.desktop_v3_sdk_target = result.sdk_target;
        Ok(stop)
    }

    fn observe_sdk(
        &mut self,
        lease: crate::sdk_evidence::Lease,
        phase: eidolon_model::commands::SdkEvidencePhase,
        render: &eidolon_render::Controller,
    ) -> Status {
        use eidolon_model::commands::{PanelAction, SdkEvidencePhase};
        if !self.sdk_evidence.matches(lease) {
            return Status::Stale;
        }
        if phase == SdkEvidencePhase::Retired && self.sdk_evidence.retired(lease) {
            return Status::Accepted;
        }
        let Some(owner) = self.v3_retirement.as_mut() else {
            return Status::Stale;
        };
        if !owner.owns_target(lease.target, lease.create) {
            return Status::Stale;
        }
        match phase {
            SdkEvidencePhase::Current => {
                if owner.is_sealed()
                    || self.snapshot.state != RuntimeState::Ready
                    || self.snapshot.scene != Some(lease.target.scene)
                    || !self.sdk_bindings.contains(&lease.target)
                    || !self.panels.snapshot().iter().any(|panel| {
                        panel.target == lease.target
                            && panel.command_seq == lease.create
                            && panel.action == PanelAction::Create
                            && panel.error.is_none()
                    })
                {
                    return Status::NotReady;
                }
            }
            SdkEvidencePhase::Retiring | SdkEvidencePhase::Retired => {
                self.sdk_evidence.seal_target(lease.target);
                if self.panels.retire(lease.target).is_err()
                    || owner.retire_target(lease.target, render).is_err()
                {
                    return Status::NotReady;
                }
                if phase == SdkEvidencePhase::Retired {
                    if owner
                        .sdk_retired(lease.target, lease.create, render)
                        .is_err()
                    {
                        return Status::NotReady;
                    }
                    self.sdk_bindings.retain(|target| *target != lease.target);
                }
            }
        }
        self.sdk_evidence.observe(lease, phase)
    }

    fn v3_target_ready(
        &self,
        target: eidolon_model::surface::TargetKey,
        render: &eidolon_render::Snapshot,
    ) -> bool {
        let Some(owner) = self.v3_retirement.as_ref() else {
            return false;
        };
        !owner.is_sealed()
            && owner.origin().session() == target.session
            && self.snapshot.scene == Some(target.scene)
            && self.snapshot.activity_visible
            && self.snapshot.xr_state == XrState::Focused
            && self.render_visibility.iter().all(|visible| *visible)
            && self.sdk_bindings.contains(&target)
            && self.panels.snapshot().iter().any(|panel| {
                panel.target == target
                    && panel.action == eidolon_model::commands::PanelAction::Create
                    && panel.applied
                    && panel.error.is_none()
                    && self.sdk_evidence.current(target, panel.command_seq)
            })
            && render.mode == eidolon_render::RenderMode::Windows
            && !render.paused
            && render.targets.iter().any(|frame| {
                frame.key == target
                    && frame.role == eidolon_render::RenderMode::Windows
                    && frame.content_frame.is_some()
                    && frame.frames > 0
                    && !frame.presentation_paused
            })
    }

    fn v3_input_target_current(
        &self,
        target: eidolon_model::surface::TargetKey,
        render: &eidolon_render::Snapshot,
        transport: &eidolon_transport::session::Snapshot,
    ) -> bool {
        let Some(owner) = self
            .v3_retirement
            .as_ref()
            .filter(|owner| owner.origin().session() == target.session)
        else {
            return true;
        };
        transport.session == Some(target.session)
            && transport
                .desktop_v3_transport
                .as_ref()
                .is_some_and(|proof| {
                    proof.session == target.session
                        && proof.launch_request == Some(owner.origin().request())
                        && proof.started_observed
                        && !proof.terminal_observed
                        && proof.error.is_none()
                        && proof.transport.phase
                            == eidolon_model::linux_desktop_v3::report::TransportPhase::Started
                })
            && self
                .snapshot
                .desktop_v3_presentation
                .as_ref()
                .is_some_and(|presentation| {
                    presentation.startup_completed
                        && !matches!(presentation.state,
                        eidolon_model::linux_desktop_v3::presentation::PresentationState::Stopping
                        | eidolon_model::linux_desktop_v3::presentation::PresentationState::Pending)
                })
            && self.snapshot.app.as_ref().is_some_and(|app| {
                app.session == Some(target.session)
                    && app.ready
                    && app.error.is_none()
                    && !matches!(app.state, "closing" | "stopped" | "fail")
                    && app.gui_windows.contains(&target.window)
            })
            && self.v3_target_ready(target, render)
    }

    /// Record every native Create before exposing its plan to Android. Retired
    /// targets keep their original Create identity even after the Destroy seq changes.
    fn sync_v3_targets(&mut self, render: &eidolon_render::Controller) -> Result<(), &'static str> {
        let Some(owner) = self.v3_retirement.as_mut() else {
            return Ok(());
        };
        for panel in self.panels.snapshot() {
            if panel.target.session != owner.origin().session() {
                continue;
            }
            self.panels.require_v3_evidence(panel.target);
            if panel.action == eidolon_model::commands::PanelAction::Create && !owner.is_sealed() {
                owner.reserve_target(panel.target, panel.command_seq)?;
            } else {
                self.sdk_evidence.seal_target(panel.target);
                self.panels.retire(panel.target)?;
                owner.retire_target(panel.target, render)?;
            }
        }
        owner.collect_completed(render)?;
        for target in owner.completed_targets() {
            if self.sdk_bindings.contains(&target) {
                continue;
            }
            let command = self
                .panels
                .snapshot()
                .into_iter()
                .find(|panel| panel.target == target);
            if let Some(command) = command {
                if command.action != eidolon_model::commands::PanelAction::Destroy {
                    continue;
                }
                if let Some(error) = command.error {
                    self.snapshot.desktop_v3_cleanup_error.get_or_insert(error);
                }
                if self.panels.complete_retired(command.command_seq, target) != Status::Accepted {
                    continue;
                }
            }
            owner.acknowledge_completed_target(target);
        }
        Ok(())
    }

    fn app_sdk_bindings(&self) -> usize {
        self.sdk_bindings
            .iter()
            .filter(|key| !self.diagnostic_sdk_bindings.contains(key))
            .count()
    }
    fn clear_credentials(&mut self) {
        self.credentials.clear();
        self.credential_scope = None;
    }
    fn refresh_app_report(&mut self, current: &crate::session::Session) {
        let mut snapshot = current.snapshot();
        if !self.workspace.workload_observation() {
            for workload in &mut snapshot.workloads {
                workload.display.io = eidolon_model::workload::IoDisplay::stopped();
            }
        }
        self.snapshot.app = Some(snapshot);
    }
    fn refresh_ime(&mut self, app: Option<&crate::session::Session>) {
        self.ime_ui = if self.snapshot.state == RuntimeState::Ready
            && self.snapshot.activity_visible
            && self.snapshot.xr_state == XrState::Focused
            && self.render_visibility.iter().all(|v| *v)
        {
            app.and_then(|current| current.ime_snapshot(self.snapshot.input_resets))
                .filter(|ime| {
                    self.panels.snapshot().iter().any(|panel| {
                        panel.target.session == ime.target.session
                            && panel.target.window == ime.target.window
                            && Some(panel.target.scene) == self.snapshot.scene
                            && panel.applied
                            && self.snapshot.window_groups.input_allowed(panel.target)
                            && panel.error.is_none()
                            && panel.action == eidolon_model::commands::PanelAction::Create
                    })
                })
        } else {
            None
        };
    }
    fn fail(&mut self, code: &'static str) {
        // 回収済みownerに遅いJNI例外が来てもStoppedを巻き戻さない。
        if self.snapshot.state == RuntimeState::Stopped {
            return;
        }
        if self.snapshot.error.is_none() {
            self.ime_ui = None;
            self.clear_credentials();
            self.snapshot.error = Some(code);
            self.snapshot.state = RuntimeState::Failed;
            self.terminal.push_back(Event::Failure { error: code });
        }
    }
    fn emit(&mut self, event: Event) {
        if self.events.len() == limits::UI_COMMANDS {
            self.fail("RESOURCE_LIMIT");
        } else {
            self.events.push_back(event);
        }
    }
}

/// 配置はcore workerだけが更新する。SDK所有/Board/保存workspaceの寿命は変えない。
struct WindowGroups {
    owner: crate::window_group_owner::Controller,
    hidden: Vec<eidolon_model::surface::TargetKey>,
}
impl WindowGroups {
    fn new(runtime: eidolon_model::ids::RuntimeHandle) -> Self {
        Self {
            owner: crate::window_group_owner::Controller::new(runtime),
            hidden: vec![],
        }
    }
    fn sync(
        &mut self,
        state: &mut Shared,
        render: &eidolon_render::Controller,
        transport: &eidolon_transport::session::Controller,
        now: Duration,
        reset: &AtomicBool,
    ) {
        let draw = render.snapshot();
        let ssh = transport.snapshot();
        let _ = self.owner.observe(crate::window_group_owner::World {
            snapshot: &state.snapshot,
            transport: &ssh,
            render: &draw,
            now,
        });
        let hidden = self.owner.hidden_targets();
        if hidden != self.hidden {
            if render.set_hidden_targets(&hidden) == Status::Accepted {
                self.hidden = hidden;
            } else if !render.is_stopping() {
                state.fail("GROUP_RENDER_FAILED");
            }
        }
        state.snapshot.window_groups = self.owner.snapshot();
        if self.owner.take_input_reset() {
            reset.store(true, Ordering::Release);
        }
    }
}

/// protocol/model所有threadの土台。UIからのcallは短いlockとenqueueだけ。
fn advance_v3_reclamation(
    shared: &Mutex<Shared>,
    current: Option<&mut crate::session::Session>,
    observer: &mut Option<crate::startup_observation::V3PresentationObserver>,
    finalizer: &mut crate::v3_retirement::Finalizer,
    render: &eidolon_render::Controller,
    transport: &Arc<eidolon_transport::session::Controller>,
    closing: bool,
) {
    let proof = current
        .filter(|session| session.is_v3())
        .and_then(|current| {
            let closing = closing
                || current.closed()
                || current.snapshot().state == "closing"
                || current.snapshot().error.is_some();
            shared.lock().unwrap_or_else(|e| e.into_inner()).reclaim_v3(
                current,
                observer.as_mut(),
                render,
                closing,
            )
        });
    // Inspect may end before a local Session is allocated. Only I2's captured
    // no-allocation capability can discharge that case; None is never evidence.
    if !finalizer.pending()
        && !finalizer.worker_pending()
        && !finalizer.completed()
        && let Some(handoff) = transport.take_v3_reclamation()
        && let Err(error) = finalizer.accept_handoff(handoff)
    {
        shared
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .snapshot
            .desktop_v3_cleanup_error
            .get_or_insert(error);
    }
    finalizer.tick(proof, transport);
    let mut shared = shared.lock().unwrap_or_else(|e| e.into_inner());
    shared.v3_finalizer_workers = usize::from(finalizer.worker_pending());
    shared.snapshot.desktop_v3_finalizer_pending =
        finalizer.pending() || finalizer.worker_pending();
    shared.v3_recovery_local_proven = shared
        .v3_retirement
        .as_ref()
        .is_some_and(|owner| finalizer.permits_recovery(owner));
    if let Some(error) = finalizer.last_error() {
        shared
            .snapshot
            .desktop_v3_cleanup_error
            .get_or_insert(error);
    }
    if finalizer.completed() && !finalizer.worker_pending() {
        // Reuse begins only after same-capture publication. Keep the diagnostic
        // summary, but never let the completed finalizer clear a later owner.
        shared.v3_retirement = None;
        shared.v3_recovery_local_proven = false;
        *observer = None;
        *finalizer = Default::default();
    }
}

/// protocol/model所有threadの土台。UIからのcallは短いlockとenqueueだけ。
pub struct Runtime {
    handle: eidolon_model::ids::RuntimeHandle,
    shared: Arc<Mutex<Shared>>,
    stop: Arc<AtomicBool>,
    reset: Arc<AtomicBool>,
    worker: JoinHandle<()>,
    render: Arc<eidolon_render::Controller>,
    transport: Arc<eidolon_transport::session::Controller>,
    normal_linux: bool,
}
impl Runtime {
    pub fn start(config: RuntimeConfig) -> std::io::Result<Self> {
        Self::start_with_hook(config, || {})
    }

    /// JNI registryが採番した実handleを予約leaseのownerへ伝える。
    pub fn start_registered(
        config: RuntimeConfig,
        handle: eidolon_model::ids::RuntimeHandle,
    ) -> std::io::Result<Self> {
        Self::start_owned_with_hook(config, handle, || {})
    }
    fn start_with_hook(
        config: RuntimeConfig,
        ready_hook: impl FnOnce() + Send + 'static,
    ) -> std::io::Result<Self> {
        // Android以外のstandalone fixture owner。registry経路はstart_registeredを使う。
        static NEXT: std::sync::atomic::AtomicI64 = std::sync::atomic::AtomicI64::new(1);
        let value = NEXT
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |id| id.checked_add(1))
            .map_err(|_| std::io::Error::other("ID_EXHAUSTED"))?;
        let handle = eidolon_model::ids::RuntimeHandle::new(value)
            .map_err(|_| std::io::Error::other("ID_EXHAUSTED"))?;
        Self::start_owned_with_hook(config, handle, ready_hook)
    }
    fn start_owned_with_hook(
        config: RuntimeConfig,
        runtime_handle: eidolon_model::ids::RuntimeHandle,
        ready_hook: impl FnOnce() + Send + 'static,
    ) -> std::io::Result<Self> {
        let invalid = || std::io::Error::new(std::io::ErrorKind::InvalidInput, "CONFIG_INVALID");
        config.validate().map_err(|_| invalid())?;
        if (config.debug_probe_profile_v2.is_some()
            || config.debug_linux_shm_pins.is_some()
            || config.debug_linux_gtk_pins.is_some())
            && !cfg!(any(test, feature = "debug-fixtures"))
        {
            return Err(invalid());
        }
        let probe_spec = config
            .debug_probe_profile_v2
            .as_ref()
            .map(|json| {
                let profile = eidolon_model::connection_profile::Profile::parse(
                    json,
                    config.installation.as_deref().ok_or_else(invalid)?,
                )
                .map_err(|_| invalid())?;
                eidolon_transport::connect_profile::ConnectSpec::from_v2(&profile)
                    .map_err(|_| invalid())
            })
            .transpose()?;
        let desktop_profile = config
            .linux_profile_v2
            .as_deref()
            .map(|json| {
                eidolon_model::connection_profile::Profile::parse(
                    json,
                    config.installation.as_deref().ok_or_else(invalid)?,
                )
                .map_err(|_| invalid())
            })
            .transpose()?;
        let desktop_pins = config
            .linux_desktop_pins
            .as_deref()
            .map(eidolon_transport::linux_desktop::Pins::parse)
            .transpose()
            .map_err(|_| invalid())?;
        // Parse the B1/E1 v3 envelope once at Runtime creation. A malformed
        // capture never silently selects the older v2 transport.
        let desktop_v3_binding = config
            .linux_desktop_v3
            .as_deref()
            .map(|raw| {
                let binding =
                    eidolon_model::linux_desktop_v3::configuration::RuntimeBinding::parse(
                        raw.as_bytes(),
                    )
                    .map_err(|_| invalid())?;
                let profile = desktop_profile.as_ref().ok_or_else(invalid)?;
                binding.matches_profile(profile).map_err(|_| invalid())?;
                Ok::<_, std::io::Error>(binding)
            })
            .transpose()?;
        let normal_linux = desktop_profile.is_some();
        #[cfg(any(test, feature = "debug-fixtures"))]
        let linux_pins = config
            .debug_linux_shm_pins
            .as_deref()
            .map(eidolon_transport::linux_probe::Pins::parse)
            .transpose()
            .map_err(|_| invalid())?;
        #[cfg(any(test, feature = "debug-fixtures"))]
        let linux_gtk_pins = config
            .debug_linux_gtk_pins
            .as_deref()
            .map(eidolon_transport::linux_probe::GtkPins::parse)
            .transpose()
            .map_err(|_| invalid())?;
        let mut profiles = std::collections::BTreeMap::new();
        for wire in &config.profiles {
            let json = serde_json::to_string(wire).map_err(|_| invalid())?;
            let profile = eidolon_transport::host_key::Profile::parse(
                &json,
                config.installation.as_deref().ok_or_else(invalid)?,
            )
            .map_err(|_| invalid())?;
            profiles.insert(profile.profile_id.clone(), profile);
        }
        let transport = Arc::new(eidolon_transport::session::Controller::start_with_root(
            config
                .paths
                .as_ref()
                .map(|paths| std::path::PathBuf::from(&paths.files_dir)),
        )?);
        let io = transport.clone();
        let render = Arc::new(eidolon_render::Controller::start()?);
        let gl = render.clone();
        let shared = Arc::new(Mutex::new(Shared {
            ime_ui: None,
            shutdown: Default::default(),
            sdk_bindings: vec![],
            diagnostic_sdk_bindings: vec![],
            sdk_evidence: Default::default(),
            v3_retirement: None,
            v3_finalizer_workers: 0,
            v3_recovery_local_proven: false,
            last_scene: None,
            panels: Default::default(),
            workspace: crate::workspace::Controller::load(None),
            credentials: Default::default(),
            credential_scope: None,
            render_visibility: [false; 2],
            snapshot: Snapshot::default(),
            commands: VecDeque::new(),
            events: VecDeque::new(),
            terminal: VecDeque::new(),
        }));
        let stop = Arc::new(AtomicBool::new(false));
        let reset = Arc::new(AtomicBool::new(false));
        let (state, progress_state, stopping, resetting) =
            (shared.clone(), shared.clone(), stop.clone(), reset.clone());
        let worker = thread::Builder::new()
            .name("eidolon-core".into())
            .spawn(move || {
                let mut helper: Option<(
                    eidolon_model::ids::CommandSeq,
                    JoinHandle<crate::helper::Report>,
                )> = None;
                let mut dependencies: Option<(
                    eidolon_model::ids::CommandSeq,
                    JoinHandle<crate::compositor::primitives::Report>,
                )> = None;
                let mut compositor: Option<(
                    eidolon_model::ids::CommandSeq,
                    JoinHandle<serde_json::Value>,
                )> = None;
                let mut app: Option<crate::session::Session> = None;
                let mut app_linux = false;
                let mut desktop_launch: Option<DesktopLaunch> = None;
                // I2's move-only permit stays owned until local Waypipe is
                // ready; no second reserve or Start retry is permitted.
                let mut v3_pending: Option<eidolon_transport::linux_desktop::v3::runtime::LaunchPermit> = None;
                let mut v3_observer: Option<crate::startup_observation::V3PresentationObserver> = None;
                // Survives panic in the normal loop; journal publication owns
                // its retryable receipt until the same capture accepts it.
                let mut v3_finalizer = crate::v3_retirement::Finalizer::default();
                let mut slots = crate::window_slots::Controller::new(runtime_handle);
                let mut groups = WindowGroups::new(runtime_handle);
                let mut slot_refused = Vec::new();
                // server ACKだけで枠の予約を解かない。新windowがmapするまで追加を直列化する。
                let mut opening_window: Option<(
                    eidolon_model::ids::SessionId,
                    eidolon_model::ids::CommandSeq,
                    String,
                    Vec<eidolon_model::ids::WindowId>,
                )> = None;
                let mut pending_app: Option<(
                    eidolon_model::ids::SessionId,
                    String,
                    eidolon_model::commands::AppMode,
                    bool,
                    bool,
                )> = None;
                let clock = Instant::now();
                let mut workspace = crate::workspace::Controller::load(
                    config.preferences_dir.as_deref().map(std::path::Path::new),
                );
                if config.preferences_dir.is_none() {
                    workspace.set_keyboard_layout(config.keyboard_layout);
                }
                io.set_workload_observation(workspace.workload_observation());
                let mut workspace_storage = workspace.detach_storage();
                let mut last_workspace_save = Instant::now();
                {
                    let mut state = state.lock().unwrap_or_else(|e| e.into_inner());
                    state.workspace = workspace;
                    state.snapshot.workspace = state.workspace.snapshot();
                    if state.snapshot.state == RuntimeState::Starting
                        && !stopping.load(Ordering::Acquire)
                    {
                        state.snapshot.state = RuntimeState::Ready;
                        state.emit(Event::RuntimeReady);
                    }
                }
                let result = catch_unwind(AssertUnwindSafe(|| {
                    let mut themes = crate::theme::Controller::start(
                        config.preferences_dir.as_deref().map(std::path::Path::new),
                        &gl,
                    )
                    .expect("theme worker creation failed");
                    ready_hook();
                    let mut detach_count = 0;
                    let mut last_credential = None;
                    let mut last_transport = (None, eidolon_transport::session::State::Idle, None);
                    loop {
                        if last_workspace_save.elapsed() >= Duration::from_millis(250)
                            || stopping.load(Ordering::Acquire)
                        {
                            let value = state
                                .lock()
                                .unwrap_or_else(|e| e.into_inner())
                                .workspace
                                .take_pending_save();
                            if let Some(value) = value {
                                workspace_storage.value = value;
                                let _ = workspace_storage.save();
                                let mut shared = state.lock().unwrap_or_else(|e| e.into_inner());
                                shared
                                    .workspace
                                    .storage_result(workspace_storage.report.clone());
                                shared.snapshot.workspace = shared.workspace.snapshot();
                                last_workspace_save = Instant::now();
                            }
                        }
                        let render = gl.snapshot();
                        themes.poll(&gl);
                        if stopping.load(Ordering::Acquire) {
                            themes.stop();
                        }
                        state
                            .lock()
                            .unwrap_or_else(|e| e.into_inner())
                            .snapshot
                            .theme = themes.report();
                        let ssh = io.snapshot();
                        if !stopping.load(Ordering::Acquire)
                            && pending_app.as_ref().is_none_or(|(session, nonce, _, _, _)| slots.can_begin(*session, nonce))
                            && let Some((session, nonce, mode, virtual_clock, linux)) = pending_app.take()
                        {
                            let mut created_session = false;
                            let started = (|| {
                                if ssh.session != Some(session)
                                    || ssh.state != if mode == eidolon_model::commands::AppMode::LinuxDesktop {
                                        eidolon_transport::session::State::DesktopReady
                                    } else if linux {eidolon_transport::session::State::ProbeReady}else{eidolon_transport::session::State::Ready}
                                {
                                    return Err("STALE");
                                }
                                if let Some(current) = app.as_mut()
                                    && current.session() == session
                                {
                                    if current.virtual_clock() != virtual_clock {
                                        return Err("NOT_READY");
                                    }
                                    if app_linux!=linux {return Err("NOT_READY");}
                                    current.select_app(&nonce, mode)?;
                                    if !virtual_clock { slots.begin(session, &nonce).map_err(|_| "NOT_READY")?; }
                                    return Ok(());
                                }
                                if app.as_ref().is_some_and(|a| !a.closed()) {
                                    return Err("BUSY");
                                }
                                let paths = config.paths.as_ref().ok_or("NOT_READY")?;
                                let keyboard_layout = state
                                    .lock()
                                    .unwrap_or_else(|e| e.into_inner())
                                    .workspace
                                    .keyboard_layout();
                                app = Some(crate::session::Session::start(
                                    session,
                                    std::path::Path::new(&paths.files_dir),
                                    &std::path::Path::new(&paths.native_library_dir)
                                        .join("libwaypipe_bin.so"),
                                    &nonce,
                                    mode,
                                    crate::session::Options {
                                        virtual_clock,
                                        keyboard_layout,
                                        pixels: gl.pixel_budget(),
                                    },
                                ));
                                app_linux=linux;
                                created_session = true;
                                if !virtual_clock {
                                    slots.begin(session, &nonce).map_err(|_| "NOT_READY")?;
                                    gl.use_windows();
                                }
                                if let Some(error) = app.as_ref().and_then(|current| current.snapshot().error) {
                                    return Err(error);
                                }
                                if mode == eidolon_model::commands::AppMode::LinuxDesktop {
                                    let launch = desktop_launch.as_ref().filter(|launch| launch.session == session && launch.nonce == nonce).ok_or("STALE")?;
                                    app.as_mut().ok_or("NOT_READY")?.reserve_desktop(launch.request)?;
                                }
                                Ok(())
                            })();
                            if let Err(error) = started {
                                if mode == eidolon_model::commands::AppMode::LinuxDesktop {
                                    if let Some(launch) = desktop_launch.take() {
                                        slots.not_enqueued(launch.lease);
                                        state.lock().unwrap_or_else(|e| e.into_inner()).snapshot.window_launch = Some(eidolon_model::window_launch::Report {
                                            session: launch.session, request_id: launch.request, outcome: eidolon_model::window_launch::Outcome::Cancelled,
                                        });
                                    }
                                    opening_window = None;
                                    if app.as_ref().is_none_or(|current| current.closed()) {
                                        app = Some(crate::session::Session::failed_start(session, &nonce, mode, virtual_clock, error));
                                        app_linux = true;
                                    }
                                }
                                if mode == eidolon_model::commands::AppMode::LinuxDesktop || created_session {
                                    // Partial startup still owns its local allocations. Close the inspected
                                    // helper first; Session::tick waits for this remote proof before retiring.
                                    io.disconnect(session);
                                }
                                if let Some(current) = app.as_mut().filter(|current| current.session() == session && current.nonce() == nonce) {
                                    current.fail(error);
                                    state.lock().unwrap_or_else(|e| e.into_inner()).snapshot.app = Some(current.snapshot());
                                } else {
                                    state.lock().unwrap_or_else(|e| e.into_inner()).snapshot.app =
                                    Some(crate::session::Report {
                                        state: "fail",
                                        session: Some(session),
                                        nonce,
                                        mode: Some(mode),
                                        error: Some(error),
                                        ..Default::default()
                                    });
                                }
                            }
                        }
                        if let Some(current) = app.as_mut() {
                            let desktop = current.mode() == eidolon_model::commands::AppMode::LinuxDesktop;
                            let desktop_finished = desktop && ssh.desktop.as_ref().is_some_and(|report| report.session == Some(current.session()) && report.nonce == current.nonce() && report.finished);
                            let desktop_terminating = desktop && !current.is_v3() && ssh.desktop.as_ref().is_some_and(|report| report.session == Some(current.session()) && report.nonce == current.nonce()
                                && (report.finished || report.state == "stopping" || report.remote.terminal_event || report.remote.channel_closed));
                            let v3_terminating = current.is_v3() && ssh.desktop_v3_transport.as_ref().is_some_and(|transport|
                                transport.session == current.session()
                                && (transport.terminal_observed || transport.error.is_some()
                                    || transport.transport.phase == eidolon_model::linux_desktop_v3::report::TransportPhase::CleanupPending));
                            let close = stopping.load(Ordering::Acquire)
                                || ssh.session != Some(current.session())
                                || matches!(
                                    ssh.state,
                                    eidolon_transport::session::State::Failed
                                        | eidolon_transport::session::State::Disconnected
                                        | eidolon_transport::session::State::Stopped
                                )
                                || (desktop && (desktop_terminating || v3_terminating
                                    || matches!(ssh.state, eidolon_transport::session::State::Disconnecting | eidolon_transport::session::State::CleanupPending)))
                                || current.snapshot().error.is_some();
                            let remote_gone = io.is_finished()
                                || desktop_finished
                                || (ssh.owned_connections == 0 && ssh.forward.active == 0);
                            if current.is_v3() {
                                let start_error = current.v3_origin().cloned()
                                    .filter(|origin| {
                                        !close && ssh.desktop_v3_transport.as_ref().is_some_and(|proof| {
                                            proof.session == current.session()
                                                && proof.launch_request == Some(origin.request())
                                                && proof.started_observed
                                                && !proof.terminal_observed
                                                && proof.error.is_none()
                                        })
                                    })
                                    .and_then(|origin| {
                                        current.v3_transport_started(&origin).err()
                                    });
                                if let Some(error) = start_error {
                                    current.fail(error);
                                }
                                if close {
                                    // Dropping the permit seals the one-shot I2 Launch gate.
                                    v3_pending.take();
                                    current.set_v3_input_allowed(Default::default());
                                }
                            }
                            if !app_linux && let Some(forward) = ssh.app_forward.as_ref() {
                                current.forwarding(forward);
                            }
                            if desktop && !current.is_v3() && let Some(forward) = ssh.desktop.as_ref() {
                                current.desktop_forwarding(forward);
                            }
                            #[cfg(any(test, feature = "debug-fixtures"))]
                            if app_linux && !desktop && let Some(forward)=ssh.linux_forward.as_ref() {current.linux_forwarding(forward);}
                            current.tick(close, remote_gone);
                            if current.is_v3() && v3_pending.is_none()
                                && let Some(observer) = v3_observer.as_mut() {
                                    let mut shared = state.lock().unwrap_or_else(|e| e.into_inner());
                                    let closing = close || current.closed() || current.snapshot().state == "closing";
                                    let should_stop = match shared.observe_v3(
                                        current, observer, &ssh, &gl.snapshot(),
                                        clock.elapsed().as_millis() as u64, closing,
                                    ) {
                                        Ok(stop) => stop,
                                        Err(error) => {
                                            observer.fail(&eidolon_model::linux_desktop_v3::Error(error));
                                            shared.snapshot.desktop_v3_presentation = observer.snapshot().ok();
                                            shared.snapshot.desktop_v3_sdk_target = None;
                                            current.set_v3_input_allowed(Default::default());
                                            current.fail(error);
                                            true
                                        }
                                    };
                                    if should_stop {
                                        // B1 timeout/cancellation are successful model transitions,
                                        // not parser errors. They must still stop native admission
                                        // and the captured remote owner.
                                        if let Some(reason) = shared.snapshot.desktop_v3_presentation.as_ref()
                                            .and_then(|presentation| presentation.first_failure)
                                        {
                                            use eidolon_model::linux_desktop_v3::Reason;
                                            current.fail(match reason {
                                                Reason::AppLaunchTimeout => "APP_LAUNCH_TIMEOUT",
                                                Reason::AppPresentationTimeout => "APP_PRESENTATION_TIMEOUT",
                                                Reason::AppStartupCancelled => "APP_STARTUP_CANCELLED",
                                                Reason::AppWindowUnsupported => "APP_WINDOW_UNSUPPORTED",
                                                Reason::AppOriginUnknown => "APP_ORIGIN_UNKNOWN",
                                                _ => "APP_PRESENTATION_FAILED",
                                            });
                                        }
                                        shared.sdk_evidence.seal();
                                        if let Some(owner) = shared.v3_retirement.as_mut() {
                                            let _ = owner.seal(&gl);
                                        }
                                        shared.snapshot.desktop_v3_sdk_target = None;
                                        drop(shared);
                                        io.disconnect(current.session());
                                        current.tick(true, remote_gone);
                                    }
                            }
                            if let Some((session, request, nonce, before)) = opening_window.as_ref() {
                                use eidolon_model::window_launch::{Candidate, Outcome, Report, correlate};
                                let now = current.snapshot();
                                let expected_nonce = nonce.as_str();
                                let candidates: Vec<_> = now.windows.iter().map(|w| Candidate {
                                    window: w.window, app_id: w.app_id.as_deref(), life: w.life,
                                }).collect();
                                let outcome = if close || current.session() != *session
                                    || current.nonce() != expected_nonce || ssh.session != Some(*session)
                                {
                                    Outcome::Cancelled
                                } else if desktop {
                                    let receipt = ssh.desktop.as_ref().and_then(|remote| Some(eidolon_model::linux_desktop::Receipt {
                                        session: remote.session?, request: remote.request_id?, nonce: &remote.nonce, started: remote.started,
                                        terminal: remote.finished || remote.state == "stopping" || remote.remote.terminal_event || remote.remote.channel_closed,
                                    }));
                                    let windows = now.windows.iter().map(|w| eidolon_model::linux_desktop::Candidate {
                                        window: w.window, app_id: w.app_id.as_deref(), parent: w.parent, life: w.life,
                                    }).collect::<Vec<_>>();
                                    eidolon_model::linux_desktop::correlate(*session, *request, expected_nonce, receipt, &windows, before)
                                } else {
                                    ssh.app_forward.as_ref().filter(|f| f.nonce == expected_nonce)
                                        .and_then(|f| f.remote.as_ref()).map_or(Outcome::AwaitingReceipt, |remote| {
                                            correlate(expected_nonce, Some(*request), &remote.terminal_launches, &candidates, before)
                                        })
                                };
                                state.lock().unwrap_or_else(|e| e.into_inner()).snapshot.window_launch = Some(Report {
                                    session: *session, request_id: *request, outcome,
                                });
                                if !current.virtual_clock() && slots.outcome(*session, *request, outcome).is_err() {
                                    current.fail("SLOT_STATE_INVALID");
                                }
                                if outcome.completed() { opening_window = None; }
                            }
                            if desktop && close && let Some(launch) = desktop_launch.take() {
                                slots.not_enqueued(launch.lease);
                            }

                            let selected = state
                                .lock()
                                .unwrap_or_else(|e| e.into_inner())
                                .panels
                                .visible();
                            current.set_visible_windows(&selected);
                            let restores = {
                                let mut shared = state.lock().unwrap_or_else(|e| e.into_inner());
                                let panels = shared.panels.snapshot();
                                shared.workspace.sync(
                                    current.session(),
                                    &current.layout_windows(),
                                    &panels,
                                )
                            };
                            for restore in restores {
                                if let Err(error) = current.restore_sizes(restore) {
                                    current.fail(error);
                                }
                            }
                            {
                                let shared = state.lock().unwrap_or_else(|e| e.into_inner());
                                let targets = current.is_v3().then(|| {
                                    if shared.v3_retirement.as_ref().is_none_or(|owner| owner.is_sealed()) {
                                        return Vec::new();
                                    }
                                    shared.panels.snapshot().into_iter().filter(|panel| {
                                        panel.target.session == current.session()
                                            && Some(panel.target.scene) == shared.snapshot.scene
                                            && panel.action == eidolon_model::commands::PanelAction::Create
                                            && panel.error.is_none()
                                    }).map(|panel| panel.target).collect::<Vec<_>>()
                                });
                                current.render_tick(&gl, clock.elapsed(), targets.as_deref());
                            }
                            let report = current.snapshot();
                            if !close && report.ready && !report.forward_sent {
                                let code = if current.is_v3() {
                                    if let Some(permit) = v3_pending.take() {
                                        if permit.origin().session() != current.session()
                                            || permit.local_nonce().as_str() != current.nonce()
                                        {
                                            Status::Stale
                                        } else if let Some(socket) = current.socket() {
                                            io.start_linux_desktop_v3(permit, &socket)
                                        } else {
                                            Status::NotReady
                                        }
                                    } else {
                                        Status::Stale
                                    }
                                } else if desktop {
                                    if let Some(launch) = desktop_launch.take() {
                                        let code = if launch.session != current.session() || launch.nonce != current.nonce() {
                                            Status::Stale
                                        } else if let Some(socket) = current.socket() {
                                            io.start_linux_desktop(launch.session, launch.request, &socket, launch.selection)
                                        } else { Status::NotReady };
                                        if code == Status::Accepted {
                                            if slots.enqueued(launch.lease).is_err() { current.fail("SLOT_STATE_INVALID"); }
                                        } else {
                                            slots.not_enqueued(launch.lease);
                                            opening_window = None;
                                            state.lock().unwrap_or_else(|e| e.into_inner()).snapshot.window_launch = Some(eidolon_model::window_launch::Report {
                                                session: launch.session, request_id: launch.request, outcome: eidolon_model::window_launch::Outcome::Cancelled,
                                            });
                                        }
                                        code
                                    } else { Status::Stale }
                                } else if app_linux {
                                    #[cfg(any(test, feature = "debug-fixtures"))]
                                    {if let Some(paths)=config.paths.as_ref() {
                                        if let Some(case)=current.mode().linux_gtk_case() {
                                            if let Some(pins)=linux_gtk_pins.as_ref() {io.linux_gtk_probe(current.session(),std::path::Path::new(&paths.files_dir),current.socket().as_deref(),current.nonce(),pins.binding(case))}else{Status::NotReady}
                                        } else if let Some(pins)=linux_pins.as_ref() {
                                            io.linux_probe(current.session(),std::path::Path::new(&paths.files_dir),current.socket().as_deref(),current.nonce(),pins.clone())
                                        }else{Status::NotReady}
                                    }else{Status::NotReady}}
                                    #[cfg(not(any(test, feature = "debug-fixtures")))]
                                    {Status::InvalidArgument}
                                }else{io.start_app(
                                    current.session(),
                                    &current.socket().unwrap(),
                                    current.nonce(),
                                    current.mode(),
                                )};
                                if code == Status::Accepted {
                                    current.forward_started();
                                } else {
                                    current.fail("NOT_READY");
                                }
                            }
                            if current.snapshot().error.is_some() && !close {
                                io.disconnect(current.session());
                            }
                            let observation = state
                                .lock()
                                .unwrap_or_else(|e| e.into_inner())
                                .workspace
                                .workload_observation();
                            let mut snapshot = current.snapshot();
                            if !observation {
                                for workload in &mut snapshot.workloads {
                                    workload.display.io =
                                        eidolon_model::workload::IoDisplay::stopped();
                                }
                            }
                            for workload in &snapshot.workloads {
                                gl.set_semantic_state(
                                    current.session(),
                                    workload.window,
                                    eidolon_render::theme_state::SemanticState {
                                        focused: snapshot.input.focus_window
                                            == Some(workload.window),
                                        busy: observation && workload.display.busy,
                                        cpu_cores: (observation && !workload.display.stale)
                                            .then_some(workload.display.smoothed_cores)
                                            .flatten(),
                                        rss_bytes: (observation && !workload.display.stale)
                                            .then_some(workload.display.rss_bytes)
                                            .flatten(),
                                        io_bytes_per_second: observation
                                            .then_some(workload.display.io.bytes_per_second)
                                            .flatten(),
                                        workload_identity: workload.identity.clone(),
                                    },
                                );
                            }
                            if !current.virtual_clock() {
                                let windows = snapshot.windows.iter().map(|w| crate::window_slots::Window {
                                    id: w.window, app_id: w.app_id.as_deref(), life: w.life,
                                    admissible: !desktop || snapshot.gui_windows.contains(&w.window),
                                }).collect::<Vec<_>>();
                                let mut shared = state.lock().unwrap_or_else(|e| e.into_inner());
                                let native = render.targets.iter().map(|t| t.key).collect::<Vec<_>>();
                                let panels = if stopping.load(Ordering::Acquire) && render.workers == 0 && render.contexts == 0 && render.targets.is_empty() {
                                    // owner終了後の通常panel ACKは受付されない。render thread終了＋SDK所有解除を用いる。
                                    vec![]
                                } else { shared.panels.snapshot().iter().map(|p| p.target).collect::<Vec<_>>() };
                                if slots.observe(crate::window_slots::Observation {
                                    session: current.session(), nonce: current.nonce(), windows: &windows,
                                    session_closed: current.closed(), native: &native, sdk: &shared.sdk_bindings, panels: &panels,
                                }).is_err() { shared.fail("SLOT_STATE_INVALID"); }
                                let slot_report = slots.report();
                                slot_refused.retain(|(session,window)| *session==current.session() && snapshot.windows.iter().any(|w|w.window==*window));
                                for window in &slot_report.unassigned {
                                    let key=(current.session(),*window);
                                    if !slot_refused.contains(&key) && current.close_window(*window).is_ok() {
                                        slot_refused.push(key);
                                    }
                                }
                                shared.snapshot.window_slots = slot_report;
                            }
                            state.lock().unwrap_or_else(|e| e.into_inner()).snapshot.app = Some(snapshot);
                        }
                        advance_v3_reclamation(
                            &state, app.as_mut(), &mut v3_observer, &mut v3_finalizer,
                            &gl, &io, stopping.load(Ordering::Acquire),
                        );
                        {
                            let mut shared = state.lock().unwrap_or_else(|e| e.into_inner());
                            if ssh.error == Some("JNI_FAILURE") {
                                shared.fail("JNI_FAILURE");
                            }
                            let current = (ssh.session, ssh.state, ssh.error);
                            if current != last_transport {
                                shared.emit(Event::SessionState {
                                    session: ssh.session,
                                    state: ssh.state,
                                    error: ssh.error,
                                });
                                last_transport = current;
                            }
                            if !stopping.load(Ordering::Acquire)
                                && shared.snapshot.state == RuntimeState::Ready
                                && let (Some(session), Some(request)) =
                                    (ssh.session, ssh.credential_request)
                            {
                                if last_credential != Some((session, request)) {
                                    shared.clear_credentials();
                                    if let Some(scope) = io
                                        .credential_scope()
                                        .filter(|s| s.session == session && s.request == request)
                                    {
                                        shared.credentials.begin(session, request);
                                        shared.emit(Event::CredentialRequired {
                                            session,
                                            request_id: request,
                                            profile_id: scope.profile_id.clone(),
                                        });
                                        shared.credential_scope = Some(scope);
                                        last_credential = Some((session, request));
                                    }
                                }
                                if let Ok(key) = shared.credentials.take(session, request)
                                    && let Some(scope) = shared.credential_scope.take()
                                {
                                    if scope.revision.is_some() {
                                        io.provide_for(session, request, &scope.binding, key);
                                    } else {
                                        io.provide(session, request, key);
                                    }
                                }
                            } else if last_credential.take().is_some() {
                                shared.clear_credentials();
                            }
                            if matches!(render.error, Some("RENDER_STALLED" | "JNI_FAILURE")) {
                                shared.fail(render.error.unwrap());
                            }
                            let count = (render.detaches - detach_count) as usize;
                            if count > render.detached.len() {
                                shared.fail("RESOURCE_LIMIT");
                            }
                            for target in render
                                .detached
                                .iter()
                                .skip(render.detached.len().saturating_sub(count))
                            {
                                if shared.v3_retirement.as_ref().is_some_and(|owner|
                                    owner.origin().session() == target.session)
                                {
                                    shared.sdk_evidence.seal_target(*target);
                                    let _ = shared.panels.retire(*target);
                                    if let Some(owner) = shared.v3_retirement.as_mut() {
                                        let _ = owner.retire_target(*target, &gl);
                                    }
                                    if shared.snapshot.desktop_v3_sdk_target.as_ref()
                                        .is_some_and(|current| current.target == *target)
                                    {
                                        shared.snapshot.desktop_v3_sdk_target = None;
                                    }
                                }
                                shared.emit(Event::SurfaceDetached { target: *target });
                            }
                            detach_count = render.detaches;
                        }
                        if helper.as_ref().is_some_and(|(_, job)| job.is_finished()) {
                            let (request_id, job) = helper.take().unwrap();
                            let report = job.join().unwrap_or_else(|_| crate::helper::Report {
                                error: Some("JNI_FAILURE"),
                                ..Default::default()
                            });
                            let mut state = state.lock().unwrap_or_else(|e| e.into_inner());
                            let code = if report.cancelled {
                                Status::Stale
                            } else if report.error.is_some() {
                                Status::Failed
                            } else {
                                Status::Accepted
                            };
                            if report.error == Some("JNI_FAILURE") {
                                state.fail("JNI_FAILURE");
                            }
                            state.snapshot.helper = Some(report);
                            state.snapshot.helper_workers = 0;
                            state.emit(Event::CommandComplete { request_id, code });
                        }
                        if dependencies
                            .as_ref()
                            .is_some_and(|(_, job)| job.is_finished())
                        {
                            let (request_id, job) = dependencies.take().unwrap();
                            let report = job.join().unwrap_or_else(|_| {
                                crate::compositor::primitives::Report {
                                    state: "fail",
                                    error: Some("JNI_FAILURE"),
                                    ..Default::default()
                                }
                            });
                            let code = if report.error == Some("CANCELLED") {
                                Status::Stale
                            } else if report.error.is_some() {
                                Status::Failed
                            } else {
                                Status::Accepted
                            };
                            let mut shared = state.lock().unwrap_or_else(|e| e.into_inner());
                            if report.error == Some("JNI_FAILURE") {
                                shared.fail("JNI_FAILURE");
                            }
                            shared.snapshot.native_deps = Some(report);
                            shared.snapshot.dependency_workers = 0;
                            shared.emit(Event::CommandComplete { request_id, code });
                        }
                        if compositor
                            .as_ref()
                            .is_some_and(|(_, job)| job.is_finished())
                        {
                            let (request_id, job) = compositor.take().unwrap();
                            let report = job.join().unwrap_or_else(
                                |_| serde_json::json!({"state":"fail", "error":"JNI_FAILURE"}),
                            );
                            let code = if report["error"] == "CANCELLED" {
                                Status::Stale
                            } else if report["state"] == "pass" {
                                Status::Accepted
                            } else {
                                Status::Failed
                            };
                            let mut shared = state.lock().unwrap_or_else(|e| e.into_inner());
                            if report["error"] == "JNI_FAILURE" {
                                shared.fail("JNI_FAILURE");
                            }
                            shared.snapshot.compositor = Some(report);
                            shared.snapshot.compositor_workers = 0;
                            shared.emit(Event::CommandComplete { request_id, code });
                        }
                        if stopping.load(Ordering::Acquire) {
                            gl.request_stop();
                            io.request_stop();
                            if helper.is_none()
                                && dependencies.is_none()
                                && compositor.is_none()
                                && app.as_ref().is_none_or(|a| a.closed())
                                && gl.is_finished()
                                && io.is_finished()
                                && themes.finished()
                                && !v3_finalizer.pending()
                                && !v3_finalizer.worker_pending()
                                && state
                                    .lock()
                                    .unwrap_or_else(|e| e.into_inner())
                                    .sdk_bindings
                                    .is_empty()
                                && state.lock().unwrap_or_else(|e| e.into_inner()).v3_retirement.is_none()
                            {
                                break;
                            }
                            state
                                .lock()
                                .unwrap_or_else(|e| e.into_inner())
                                .snapshot
                                .state = RuntimeState::Stopping;
                            thread::park_timeout(Duration::from_millis(5));
                            continue;
                        }
                        let mut state = state.lock().unwrap_or_else(|e| e.into_inner());
                        state.credentials.expire();
                        for key in render
                            .targets
                            .iter()
                            .map(|target| &target.key)
                            .chain(render.detached.iter())
                        {
                            state.panels.advance_past(key.generation.get());
                        }
                        let mapped: Vec<_> = state
                            .snapshot
                            .app
                            .as_ref()
                            .filter(|app| {
                                app.callback_clock == "target-driven"
                                    && app.error.is_none()
                                    && app.state != "stopped"
                            })
                            .into_iter()
                            .flat_map(|app| {
                                app.windows
                                    .iter()
                                    .filter(|w| w.life == eidolon_model::window::WindowLife::Mapped)
                                    .filter(|w| app.mode != Some(eidolon_model::commands::AppMode::LinuxDesktop) || app.gui_windows.contains(&w.window))
                                    .filter_map(|w| app.session.map(|session| (session, w.window)))
                            })
                            .collect();
                        let scene = state.snapshot.scene;
                        let may_create = state.snapshot.activity_visible;
                        let layouts = state
                            .snapshot
                            .app
                            .as_ref()
                            .into_iter()
                            .flat_map(|app| {
                                app.windows.iter()
                                    .filter(|w| app.mode != Some(eidolon_model::commands::AppMode::LinuxDesktop) || app.gui_windows.contains(&w.window))
                                    .filter_map(|w| {
                                    render.theme_id.as_ref()?;
                                    let content = w.logical_size?;
                                    let canvas =
                                        eidolon_render::theme_state::Canvas::for_content(content)
                                            .ok()?;
                                    Some((
                                        app.session?,
                                        w.window,
                                        crate::panels::DecorationLayout {
                                            content,
                                            canvas: canvas.size,
                                            content_origin: canvas.content_origin,
                                        },
                                    ))
                                })
                            })
                            .collect::<Vec<_>>();
                        let panel_result = if app.as_ref().is_some_and(|a| !a.virtual_clock()) {
                            state.panels.reconcile_assigned(scene, &mapped, may_create, &layouts, &slots.assignments())
                        } else { state.panels.reconcile_layouts(scene, &mapped, may_create, &layouts) };
                        if let Err(code) = panel_result {
                            state.fail(code);
                        }
                        if let Err(code) = state.sync_v3_targets(&gl)
                            && code != "DESKTOP_LOCAL_RETIREMENT_PENDING" { state.fail(code); }
                        state.snapshot.panels = state.panels.snapshot();
                        state.snapshot.workspace = state.workspace.snapshot();
                        groups.sync(&mut state,&gl,&io,clock.elapsed(),&resetting);
                        if resetting.swap(false, Ordering::AcqRel) {
                            state.ime_ui = None;
                            state.snapshot.input_resets += 1;
                            if let Some(current) = app.as_mut() {
                                current.reset_input();
                                state.refresh_app_report(current);
                            }
                        }
                        if state.snapshot.state == RuntimeState::Failed {
                            io.request_stop();
                            drop(state);
                            thread::park_timeout(Duration::from_millis(5));
                            continue;
                        }
                        state.refresh_ime(app.as_ref());
                        if let Some(command) = state.commands.pop_front() {
                            let mut asynchronous = false;
                            let app_busy =
                                pending_app.is_some() || app.as_ref().is_some_and(|a| !a.closed());
                            // Session::closed は protocol/local の証拠。全世代のアプリ資源を別に待つ。
                            // 初期診断panelは接続後に退役するため、接続/起動の前提にはしない。
                            let native_resources_busy = app_busy || slots.report().occupied != 0
                                || gl.snapshot().targets.iter().any(|target| target.role != eidolon_render::RenderMode::Diagnostic)
                                || state.app_sdk_bindings() != 0
                                || !state.panels.snapshot().is_empty();
                            let native_busy = native_resources_busy || state.v3_retirement.is_some();
                            let v3_recovery_allowed = !native_resources_busy
                                && state.v3_retirement.as_ref()
                                    .is_some_and(|owner| v3_finalizer.permits_recovery(owner));
                            // Recheck current SDK/content facts at the actual input effect,
                            // after queued Scene/retirement callbacks could have revoked them.
                            let input_target = match &command {
                                Command::Ime { target, .. }
                                | Command::CloseWindowTarget { target, .. }
                                | Command::SetPose { target, .. }
                                | Command::SetPoseControl { target, .. }
                                | Command::ResizeLogical { target, .. }
                                | Command::SetPhysicalSize { target, .. }
                                | Command::SetPhysicalSizeControl { target, .. }
                                | Command::Pointer { target, .. }
                                | Command::FocusWindow { target, .. }
                                | Command::FocusWindowControl { target, .. }
                                | Command::Key { target, .. }
                                | Command::WindowGroup { target, .. } => Some(*target),
                                _ => None,
                            };
                            let legacy_v3_target = match &command {
                                Command::ShowWindow { session, .. }
                                | Command::CloseWindow { session, .. } => state.v3_retirement.as_ref()
                                    .is_some_and(|owner| owner.origin().session() == *session),
                                _ => false,
                            };
                            let code = if legacy_v3_target || input_target.is_some_and(|target|
                                !state.v3_input_target_current(target, &gl.snapshot(), &io.snapshot())) {
                                Status::NotReady
                            } else { match &command {
                                Command::SetImeBackend {
                                    session, enabled, ..
                                } => {
                                    // 明示的な通常IME切替。backendは既定無効、target/epoch/activation検査は維持する。
                                    if let Some(current) = app
                                        .as_mut()
                                        .filter(|a| a.session() == *session && !a.closed())
                                    {
                                        match current.set_ime_backend(*enabled) {
                                            Ok(()) => Status::Accepted,
                                            Err("STALE") => Status::Stale,
                                            Err(_) => Status::NotReady,
                                        }
                                    } else {
                                        Status::Stale
                                    }
                                }
                                Command::Ime {
                                    target,
                                    transaction,
                                    ..
                                } => {
                                    let live = state.panels.snapshot().iter().any(|p| {
                                        p.target == *target
                                            && p.action
                                                == eidolon_model::commands::PanelAction::Create
                                            && p.applied
                                            && p.error.is_none()
                                    }) && gl
                                        .snapshot()
                                        .targets
                                        .iter()
                                        .any(|t| t.key == *target && t.frames > 0);
                                    if resetting.load(Ordering::Acquire)
                                        || transaction.target.input_epoch
                                            != state.snapshot.input_resets
                                        || state.snapshot.scene != Some(target.scene)
                                        || !live
                                        || transaction.target.session != target.session
                                        || transaction.target.window != target.window
                                    {
                                        Status::Stale
                                    } else if !groups.owner.input_allowed(*target)
                                        || !state.render_visibility.iter().all(|v| *v)
                                        || !state.snapshot.activity_visible
                                        || state.snapshot.xr_state != XrState::Focused
                                    {
                                        Status::NotReady
                                    } else if let Some(current) = app
                                        .as_mut()
                                        .filter(|a| a.session() == target.session && !a.closed())
                                    {
                                        match current.ime(state.snapshot.input_resets, transaction)
                                        {
                                            Ok(()) => Status::Accepted,
                                            Err("STALE") => Status::Stale,
                                            Err("INVALID_ARGUMENT") => Status::InvalidArgument,
                                            Err("RESOURCE_LIMIT") => Status::Busy,
                                            Err(_) => Status::NotReady,
                                        }
                                    } else {
                                        Status::Stale
                                    }
                                }
                                Command::SetTheme {
                                    selection,
                                    reduced_motion,
                                    ..
                                } => themes.select(*selection, *reduced_motion, &gl),
                                Command::BeginThemeImport { token, .. } => {
                                    themes.begin_import(token, &gl)
                                }
                                Command::ApplyThemeImport { token, .. } => {
                                    themes.apply_import(token)
                                }
                                Command::CancelThemeImport { token, .. } => {
                                    themes.cancel_import(token, &gl)
                                }
                                Command::SetBackground {
                                    request_id,
                                    background,
                                    ..
                                } => {
                                    state.workspace.set_background(*request_id, *background);
                                    Status::Accepted
                                }
                                Command::BackgroundApplied {
                                    scene,
                                    background_command,
                                    background,
                                    ..
                                } => {
                                    if state.workspace.background_applied(
                                        *scene,
                                        *background_command,
                                        *background,
                                    ) {
                                        Status::Accepted
                                    } else {
                                        Status::Stale
                                    }
                                }
                                Command::SetWorkloadObservation { enabled, .. } => {
                                    state.workspace.set_workload_observation(*enabled);
                                    io.set_workload_observation(*enabled);
                                    Status::Accepted
                                }
                                Command::SetKeyboardLayout { layout, .. } => {
                                    let changed = app
                                        .as_mut()
                                        .filter(|a| !a.closed())
                                        .map_or(Ok(()), |a| a.set_keyboard_layout(*layout));
                                    if changed.is_ok() {
                                        state.workspace.set_keyboard_layout(*layout);
                                        Status::Accepted
                                    } else {
                                        Status::Failed
                                    }
                                }

                                Command::LinuxDesktopV3Inspect { session, request_id, .. } => {
                                    if native_busy || helper.is_some() || dependencies.is_some() || compositor.is_some() {
                                        Status::Busy
                                    } else { io.inspect_linux_desktop_v3(*session, *request_id) }
                                }
                                Command::LinuxDesktopV3Recover { session, request_id, .. } => {
                                    if (native_busy && !v3_recovery_allowed) || helper.is_some() || dependencies.is_some() || compositor.is_some() {
                                        Status::Busy
                                    } else { io.recover_linux_desktop_v3(*session, *request_id) }
                                }
                                // I3 must reserve and own the local Waypipe/listener and
                                // origin-bound window admission before a Launch can proceed.
                                // I2's transport API is intentionally inaccessible to
                                // a bare untrusted/unsynchronized Command::Launch.
                                Command::LinuxDesktopV3Launch { session, request_id, selection, .. } => {
                                    if native_busy || v3_pending.is_some() || v3_observer.is_some()
                                        || v3_finalizer.pending() || v3_finalizer.worker_pending()
                                        || opening_window.is_some() || desktop_launch.is_some()
                                        || helper.is_some() || dependencies.is_some() || compositor.is_some()
                                    {
                                        Status::Busy
                                    } else if desktop_v3_binding.is_none() {
                                        Status::NotReady
                                    } else if let Some(paths) = config.paths.as_ref() {
                                        match io.reserve_linux_desktop_v3_launch(
                                            runtime_handle, *session, *request_id, selection
                                        ) {
                                            Err(status) => status,
                                            Ok(permit) => {
                                                let nonce = permit.local_nonce().as_str().to_owned();
                                                let origin = permit.origin().clone();
                                                // Install ownership before any partial native setup.
                                                match crate::v3_retirement::Owner::new(
                                                    origin.clone(), permit.captured_context().clone(), permit.generation()
                                                ) {
                                                    Ok(owner) => state.v3_retirement = Some(owner),
                                                    Err(error) => {
                                                        io.abort_linux_desktop_v3_launch(permit);
                                                        state.fail(error);
                                                        continue;
                                                    }
                                                }
                                                state.sdk_evidence = Default::default();
                                                let created = crate::session::Session::start_v3(
                                                    *session,
                                                    std::path::Path::new(&paths.files_dir),
                                                    &std::path::Path::new(&paths.native_library_dir).join("libwaypipe_bin.so"),
                                                    &permit,
                                                    crate::session::Options {
                                                        virtual_clock: false,
                                                        keyboard_layout: state.workspace.keyboard_layout(),
                                                        pixels: gl.pixel_budget(),
                                                    },
                                                );
                                                let error = created.snapshot().error;
                                                // Keep the partially prepared native owner even
                                                // if binding/listener/Waypipe setup failed.
                                                app = Some(created);
                                                if let Some(error) = error {
                                                    io.abort_linux_desktop_v3_launch(permit);
                                                    if let Some(current) = app.as_mut() {
                                                        current.fail(error);
                                                    }
                                                    Status::NotReady
                                                } else if slots.begin(*session, &nonce).is_err() {
                                                    io.abort_linux_desktop_v3_launch(permit);
                                                    if let Some(current) = app.as_mut() {
                                                        current.fail("SLOT_STATE_INVALID");
                                                    }
                                                    Status::NotReady
                                                } else {
                                                    match crate::startup_observation::V3PresentationObserver::begin(
                                                        origin, clock.elapsed().as_millis() as u64, vec![]
                                                    ) {
                                                        Ok(observer) => {
                                                            v3_observer = Some(observer);
                                                            v3_pending = Some(permit);
                                                            app_linux = false;
                                                            gl.use_windows();
                                                            Status::Accepted
                                                        }
                                                        Err(_) => {
                                                            io.abort_linux_desktop_v3_launch(permit);
                                                            if let Some(current) = app.as_mut() {
                                                                current.fail("CONTRACT_INVALID");
                                                            }
                                                            Status::NotReady
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    } else {
                                        Status::NotReady
                                    }
                                },
                                Command::LinuxDesktopInspect { session, request_id, .. } => {
                                    if native_busy || helper.is_some() || dependencies.is_some() || compositor.is_some() { Status::Busy }
                                    else { io.inspect_linux_desktop(*session, *request_id) }
                                }
                                Command::LinuxDesktopRecover { session, request_id, .. } => {
                                    if native_busy || helper.is_some() || dependencies.is_some() || compositor.is_some() { Status::Busy }
                                    else { io.recover_linux_desktop(*session, *request_id) }
                                }
                                Command::LinuxDesktopLaunch { session, request_id, selection, .. } => {
                                    if native_busy || opening_window.is_some() || desktop_launch.is_some()
                                        || helper.is_some() || dependencies.is_some() || compositor.is_some() { Status::Busy }
                                    else if config.paths.is_none() { Status::NotReady }
                                    else {
                                        match io.desktop_launch_nonce(*session, selection) {
                                            Err(code) => code,
                                            Ok(nonce) => {
                                                let reservation = slots.begin(*session, &nonce).and_then(|()| slots.reserve(*session, *request_id));
                                                match reservation {
                                                    Err(code) => code,
                                                    Ok(lease) => {
                                                        desktop_launch = Some(DesktopLaunch { session: *session, request: *request_id, nonce: nonce.clone(), selection: selection.clone(), lease });
                                                        pending_app = Some((*session, nonce.clone(), eidolon_model::commands::AppMode::LinuxDesktop, false, true));
                                                        opening_window = Some((*session, *request_id, nonce.clone(), vec![]));
                                                        state.snapshot.window_launch = Some(eidolon_model::window_launch::Report { session: *session, request_id: *request_id, outcome: eidolon_model::window_launch::Outcome::Queued });
                                                        state.snapshot.window_slots = slots.report();
                                                        state.snapshot.app = Some(crate::session::Report {
                                                            state: "queued", session: Some(*session), nonce, mode: Some(eidolon_model::commands::AppMode::LinuxDesktop), callback_clock: "target-driven", ..Default::default()
                                                        });
                                                        Status::Accepted
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                                Command::Catalog {
                                    session,
                                    request_id,
                                    operation,
                                    ..
                                } => io.catalog(*session, *request_id, operation.clone()),
                                Command::OpenTerminal {
                                    session,
                                    request_id,
                                    ..
                                }
                                | Command::LaunchTerminal {
                                    session,
                                    request_id,
                                    ..
                                } => {
                                    if app
                                        .as_ref()
                                        .is_none_or(|a| a.session() != *session || a.closed())
                                    {
                                        Status::Stale
                                    } else if opening_window.is_some()
                                        || app.as_ref().is_some_and(|a| {
                                            a.snapshot().windows.len() >= limits::TOPLEVELS
                                        })
                                    {
                                        Status::Busy
                                    } else if !slots.has_mapped_initial(*session) {
                                        // 最初の窓がmapして中央を確保するまでは追加を受け付けない。
                                        Status::NotReady
                                    } else {
                                        let lease = slots.reserve(*session, *request_id);
                                        let target = match &command {
                                            Command::LaunchTerminal { target, .. } => Some(target.clone()),
                                            _ => None,
                                        };
                                        let code = match lease {
                                            Ok(_) => io.launch_terminal(*session, *request_id, target),
                                            Err(code) => code,
                                        };
                                        if code == Status::Accepted {
                                            if slots.enqueued(lease.unwrap()).is_err() { state.fail("SLOT_STATE_INVALID"); }
                                            state.snapshot.window_launch = Some(eidolon_model::window_launch::Report {
                                                session: *session, request_id: *request_id,
                                                outcome: eidolon_model::window_launch::Outcome::Queued,
                                            });
                                            opening_window = Some((
                                                *session,
                                                *request_id,
                                                app.as_ref().unwrap().nonce().to_owned(),
                                                app.as_ref()
                                                    .unwrap()
                                                    .snapshot()
                                                    .windows
                                                    .iter()
                                                    .map(|w| w.window)
                                                    .collect(),
                                            ));
                                        } else if let Ok(lease) = lease { slots.not_enqueued(lease); }
                                        state.snapshot.window_slots = slots.report();
                                        code
                                    }
                                }

                                Command::ShowWindow {
                                    session,
                                    scene,
                                    window,
                                    ..
                                }
                                | Command::CloseWindow {
                                    session,
                                    scene,
                                    window,
                                    ..
                                } => {
                                    let current = app.as_mut().filter(|current| {
                                        current.session() == *session && !current.closed()
                                    });
                                    if state.snapshot.scene != Some(*scene)
                                        || current.as_ref().is_none_or(|current| {
                                            !current.snapshot().windows.iter().any(|w| {
                                                w.window == *window
                                                    && w.life
                                                        == eidolon_model::window::WindowLife::Mapped
                                            })
                                        })
                                    {
                                        Status::Stale
                                    } else {
                                        let current = current.unwrap();
                                        current.reset_input();
                                        match &command {
                                            Command::ShowWindow { slot, .. } => {
                                                if current.virtual_clock() { state.panels.show(*session, *window, *slot) }
                                                else if slots.assignments().contains(&(*slot,*session,*window)) { Status::Accepted }
                                                else { Status::InvalidArgument }
                                            }
                                            Command::CloseWindow { .. } => current
                                                .close_window(*window)
                                                .map(|_| Status::Accepted)
                                                .unwrap_or(Status::Stale),
                                            _ => unreachable!(),
                                        }
                                    }
                                }

                                Command::CloseWindowTarget {
                                    target,
                                    controls_revision,
                                    ..
                                } => {
                                    let render = gl.snapshot();
                                    let live = state.snapshot.scene == Some(target.scene)
                                        && state.panels.snapshot().iter().any(|p| {
                                            p.target == *target
                                                && p.action
                                                    == eidolon_model::commands::PanelAction::Create
                                                && p.applied
                                                && p.error.is_none()
                                        });
                                    if !live
                                        || render.theme_revision != *controls_revision
                                        || !render
                                            .targets
                                            .iter()
                                            .any(|t| t.key == *target && t.frames > 0)
                                    {
                                        Status::Stale
                                    } else if !state.render_visibility.iter().all(|v| *v)
                                        || !state.snapshot.activity_visible
                                        || state.snapshot.xr_state != XrState::Focused
                                    {
                                        Status::NotReady
                                    } else if let Some(current) = app.as_mut().filter(|current| {
                                        current.session() == target.session && !current.closed()
                                    }) {
                                        current.reset_input();
                                        current
                                            .close_window(target.window)
                                            .map(|_| Status::Accepted)
                                            .unwrap_or(Status::Stale)
                                    } else {
                                        Status::Stale
                                    }
                                }

                                Command::WindowGroup {request_id,target,controls_revision,expected_group,expected_revision,action,..} => {
                                    let render=gl.snapshot();let transport=io.snapshot();
                                    groups.owner.start(crate::window_group_owner::Ticket {
                                        request:*request_id,target:*target,controls_revision:*controls_revision,
                                        expected_group:*expected_group,expected_revision:*expected_revision,action:*action,
                                    },crate::window_group_owner::World {snapshot:&state.snapshot,transport:&transport,render:&render,now:clock.elapsed()})
                                }
                                Command::WindowGroupApplied {target,group,revision,pose,physical_size,content_frame,..} => {
                                    let render=gl.snapshot();let transport=io.snapshot();
                                    groups.owner.applied(crate::window_group_owner::GeometryAck {
                                        group:*group,revision:*revision,target:*target,pose:*pose,
                                        physical_size:*physical_size,content_frame:*content_frame,
                                    },crate::window_group_owner::World {snapshot:&state.snapshot,transport:&transport,render:&render,now:clock.elapsed()})
                                }
                                Command::SetPose { target, .. }
                                | Command::SetPoseControl { target, .. }
                                | Command::PoseApplied { target, .. }
                                | Command::ResizeLogical { target, .. }
                                | Command::SetPhysicalSize { target, .. }
                                | Command::SetPhysicalSizeControl { target, .. }
                                | Command::PhysicalSizeApplied { target, .. } => {
                                    let live = state.snapshot.scene == Some(target.scene)
                                        && state.panels.snapshot().iter().any(|p| {
                                            p.target == *target
                                                && p.action
                                                    == eidolon_model::commands::PanelAction::Create
                                                && p.applied
                                                && p.error.is_none()
                                        })
                                        && gl
                                            .snapshot()
                                            .targets
                                            .iter()
                                            .any(|t| t.key == *target && t.frames > 0);
                                    let controls_current = match &command {
                                        Command::SetPoseControl { controls_revision, .. }
                                        | Command::SetPhysicalSizeControl { controls_revision, .. } => gl.snapshot().theme_revision == *controls_revision,
                                        _ => true,
                                    };
                                    let controls_ready =
                                        !matches!(&command, Command::SetPoseControl { .. } | Command::SetPhysicalSizeControl { .. })
                                            || state.snapshot.activity_visible
                                                && state.snapshot.xr_state == XrState::Focused
                                                && state.render_visibility.iter().all(|v| *v);
                                    if !live || !controls_current {
                                        Status::Stale
                                    } else if groups.owner.blocks_geometry(*target) && matches!(&command,
                                        Command::SetPose {..} | Command::SetPoseControl {..} | Command::ResizeLogical {..}
                                        | Command::SetPhysicalSize {..} | Command::SetPhysicalSizeControl {..}) {
                                        Status::Busy
                                    } else if !controls_ready {
                                        Status::NotReady
                                    } else if let Some(current) = app
                                        .as_mut()
                                        .filter(|a| a.session() == target.session && !a.closed())
                                    {
                                        let outcome = match &command {
                                            Command::SetPose {
                                                request_id, pose, ..
                                            }
                                            | Command::SetPoseControl {
                                                request_id, pose, ..
                                            } => state.workspace.set_pose(
                                                *target,
                                                *request_id,
                                                *pose,
                                            ),
                                            Command::PoseApplied {
                                                pose_command, pose, ..
                                            } => {
                                                if state.workspace.pose_applied(
                                                    *target,
                                                    *pose_command,
                                                    *pose,
                                                ) {
                                                    Ok(())
                                                } else {
                                                    Err("STALE")
                                                }
                                            }
                                            Command::ResizeLogical { request_id, width, height, .. } => current
                                                .resize_logical_owned(
                                                    *target,
                                                    *request_id,
                                                    eidolon_model::window::LogicalSize {
                                                        width: *width,
                                                        height: *height,
                                                    },
                                                ),
                                            Command::SetPhysicalSize {
                                                request_id, size, ..
                                            } => current.set_physical_size(
                                                target.window,
                                                *request_id,
                                                *size,
                                            ),
                                            Command::SetPhysicalSizeControl { request_id, expected_size_command, expected_size, step, .. } => current.step_physical_size(
                                                target.window, *request_id, *expected_size_command, *expected_size, *step,
                                            ),
                                            Command::PhysicalSizeApplied {
                                                size_command,
                                                size,
                                                ..
                                            } => current.physical_size_applied(
                                                target.window,
                                                *size_command,
                                                *size,
                                            ),
                                            _ => unreachable!(),
                                        };
                                        if outcome.is_ok()
                                            && matches!(
                                                command,
                                                Command::SetPose { .. }
                                                    | Command::ResizeLogical { .. }
                                                    | Command::SetPhysicalSize { .. }
                                                    | Command::SetPhysicalSizeControl { .. }
                                            )
                                            && let Some(window) =
                                                current.current_window(target.window)
                                        {
                                            state.workspace.remember(&window);
                                        }
                                        match outcome {
                                            Ok(()) => Status::Accepted,
                                            Err("STALE") => Status::Stale,
                                            Err("INVALID_ARGUMENT") => Status::InvalidArgument,
                                            Err("RESOURCE_LIMIT") => Status::Busy,
                                            Err(_) => Status::NotReady,
                                        }
                                    } else {
                                        Status::Stale
                                    }
                                }
                                Command::Pointer {
                                    target,
                                    input_epoch,
                                    ..
                                }
                                | Command::FocusWindow {
                                    target,
                                    input_epoch,
                                    ..
                                }
                                | Command::FocusWindowControl {
                                    target,
                                    input_epoch,
                                    ..
                                }
                                | Command::Key {
                                    target,
                                    input_epoch,
                                    ..
                                } => {
                                    let current = app
                                        .as_mut()
                                        .filter(|a| a.session() == target.session && !a.closed());
                                    let live = state.panels.snapshot().iter().any(|p| {
                                        p.target == *target
                                            && p.action
                                                == eidolon_model::commands::PanelAction::Create
                                            && p.applied
                                            && p.error.is_none()
                                    }) && gl
                                        .snapshot()
                                        .targets
                                        .iter()
                                        .any(|t| t.key == *target && t.frames > 0);
                                    let controls_current = match &command {
                                        Command::FocusWindowControl {
                                            controls_revision, ..
                                        } => gl.snapshot().theme_revision == *controls_revision,
                                        _ => true,
                                    };
                                    if !controls_current
                                        || *input_epoch != state.snapshot.input_resets
                                        || state.snapshot.scene != Some(target.scene)
                                        || !live
                                    {
                                        Status::Stale
                                    } else if !groups.owner.input_allowed(*target)
                                        || !state.render_visibility.iter().all(|v| *v)
                                        || !state.snapshot.activity_visible
                                        || state.snapshot.xr_state != XrState::Focused
                                    {
                                        Status::NotReady
                                    } else if let Some(current) = current {
                                        let result = match &command {
                                            Command::FocusWindow { .. }
                                            | Command::FocusWindowControl { .. } => {
                                                current.focus_window(target.window)
                                            }
                                            Command::Key {
                                                key, focus_token, ..
                                            } => current.key(target.window, *focus_token, *key),
                                            Command::Pointer { pointer, .. } => {
                                                let render = gl.snapshot();
                                                let size = render
                                                    .targets
                                                    .iter()
                                                    .find(|t| t.key == *target)
                                                    .map(|t| {
                                                        // 装飾panelの入力quadはcontentだけを覆い、そのUVを受け取る。
                                                        // canvasの余白はSDK側でNoCollisionとし、背後の面へrayを通す。
                                                        state
                                                            .panels
                                                            .snapshot()
                                                            .iter()
                                                            .find(|p| p.target == *target)
                                                            .and_then(|p| p.decoration.as_ref())
                                                            .map_or(
                                                                (t.gpu.width, t.gpu.height),
                                                                |layout| {
                                                                    (
                                                                        layout.content.width as i32,
                                                                        layout.content.height
                                                                            as i32,
                                                                    )
                                                                },
                                                            )
                                                    });
                                                match size {
                                                    Some((width, height)) => current.pointer(
                                                        target.window,
                                                        *pointer,
                                                        width,
                                                        height,
                                                    ),
                                                    None => Err("STALE"),
                                                }
                                            }
                                            _ => unreachable!(),
                                        };
                                        match result {
                                            Ok(()) => Status::Accepted,
                                            Err("STALE") => Status::Stale,
                                            Err("INVALID_ARGUMENT") => Status::InvalidArgument,
                                            Err("RESOURCE_LIMIT") => Status::Busy,
                                            Err(_) => Status::NotReady,
                                        }
                                    } else {
                                        Status::Stale
                                    }
                                }
                                Command::AdapterApplied {
                                    command_seq,
                                    target,
                                    action,
                                    ..
                                }
                                | Command::AdapterFailed {
                                    command_seq,
                                    target,
                                    action,
                                    ..
                                } => {
                                    let success =
                                        matches!(&command, Command::AdapterApplied { .. });
                                    let targets = gl.snapshot().targets;
                                    let mut ready = match action {
                                        eidolon_model::commands::PanelAction::Create => {
                                            targets.iter().any(|t| t.key == *target && t.frames > 0)
                                        }
                                        eidolon_model::commands::PanelAction::Destroy => {
                                            targets.iter().all(|t| t.key != *target)
                                        }
                                    };
                                    if let Some(owner) = state.v3_retirement.as_ref()
                                        && owner.origin().session() == target.session
                                    {
                                        ready &= match action {
                                            eidolon_model::commands::PanelAction::Create => {
                                                !owner.is_sealed()
                                                    && state.sdk_evidence.current(*target, *command_seq)
                                                    && state.sdk_bindings.contains(target)
                                                    && targets.iter().any(|frame| {
                                                        frame.key == *target
                                                            && frame.role == eidolon_render::RenderMode::Windows
                                                            && frame.content_frame.is_some()
                                                            && !frame.presentation_paused
                                                    })
                                            }
                                            eidolon_model::commands::PanelAction::Destroy => {
                                                !state.sdk_bindings.contains(target)
                                                    && owner.completed_targets().contains(target)
                                            }
                                        };
                                    }
                                    if !state.panels.contains(*command_seq, *target, *action) {
                                        Status::Stale
                                    } else if success && !ready {
                                        Status::NotReady
                                    } else {
                                        let decorated_failure = !success
                                            && *action
                                                == eidolon_model::commands::PanelAction::Create
                                            && state.panels.snapshot().iter().any(|p| {
                                                p.target == *target && p.decoration.is_some()
                                            });
                                        let status = state.panels.acknowledge(
                                            *command_seq,
                                            *target,
                                            *action,
                                            success,
                                        );
                                        if decorated_failure && status == Status::Accepted {
                                            themes.fallback(&gl, "SDK_DECORATION_FAILED");
                                        }
                                        status
                                    }
                                }
                                Command::RenderProbe {
                                    target,
                                    stage,
                                    width,
                                    height,
                                    request_id,
                                    ..
                                } => {
                                    #[cfg(any(test, feature = "debug-fixtures"))]
                                    {
                                        match eidolon_render::fixtures::make(
                                            *stage,
                                            request_id.get(),
                                            *target,
                                            *width,
                                            *height,
                                        ) {
                                            Ok((image, probes)) if cfg!(debug_assertions) => {
                                                gl.submit(image, probes)
                                            }
                                            _ => Status::NotReady,
                                        }
                                    }
                                    #[cfg(not(any(test, feature = "debug-fixtures")))]
                                    {
                                        let _ = (target, stage, width, height, request_id);
                                        Status::NotReady
                                    }
                                }
                                Command::AppProbe {
                                    session,
                                    nonce,
                                    mode,
                                    ..
                                }
                                | Command::StartApp {
                                    session,
                                    nonce,
                                    mode,
                                    ..
                                } => {
                                    let virtual_clock =
                                        matches!(&command, Command::AppProbe { .. });
                                    let prior = ssh.app_forward.as_ref().is_none_or(|r| {
                                        matches!(r.state, "pass" | "stopped" | "fail")
                                    }) && ssh
                                        .forward_probe
                                        .as_ref()
                                        .is_none_or(|r| matches!(r.state, "pass" | "fail"));
                                    if *mode == eidolon_model::commands::AppMode::LinuxDesktop || mode.linux_gtk_case().is_some() || (virtual_clock && !cfg!(debug_assertions))
                                        || config.paths.is_none()
                                        || (!virtual_clock
                                            && *mode != eidolon_model::commands::AppMode::Foot
                                            && !(*mode
                                                == eidolon_model::commands::AppMode::FootFixture
                                                && cfg!(debug_assertions)))
                                    {
                                        Status::NotReady
                                    } else if ssh.session != Some(*session) {
                                        Status::Stale
                                    } else if ssh.state != eidolon_transport::session::State::Ready
                                    {
                                        Status::NotReady
                                    } else if app
                                        .as_ref()
                                        .is_some_and(|a| a.session() == *session && a.closed())
                                    {
                                        Status::Stale
                                    } else if app.as_ref().is_some_and(|a| {
                                        a.session() == *session
                                            && a.virtual_clock() != virtual_clock
                                    }) {
                                        Status::NotReady
                                    } else if pending_app.is_some()
                                        || helper.is_some()
                                        || dependencies.is_some()
                                        || compositor.is_some()
                                        || !prior
                                    {
                                        Status::Busy
                                    } else {
                                        pending_app =
                                            Some((*session, nonce.clone(), *mode, virtual_clock, false));
                                        state.snapshot.app = Some(crate::session::Report {
                                            state: "queued",
                                            session: Some(*session),
                                            nonce: nonce.clone(),
                                            mode: Some(*mode),
                                            callback_clock: if virtual_clock {
                                                "virtual"
                                            } else {
                                                "target-driven"
                                            },
                                            ..Default::default()
                                        });
                                        Status::Accepted
                                    }
                                }
                                Command::RemoteCleanupProbe {
                                    session,
                                    nonce,
                                    scenario,
                                    ..
                                } => {
                                    if let Some(paths) =
                                        config.paths.as_ref().filter(|_| cfg!(debug_assertions))
                                    {
                                        if app_busy {
                                            Status::Busy
                                        } else {
                                            io.cleanup_probe(
                                                *session,
                                                std::path::Path::new(&paths.files_dir),
                                                nonce,
                                                *scenario,
                                            )
                                        }
                                    } else {
                                        Status::NotReady
                                    }
                                }
                                Command::CompositorProbe {
                                    nonce,
                                    request_id,
                                    scenario,
                                    ..
                                } => {
                                    #[cfg(any(test, feature = "debug-fixtures"))]
                                    {
                                        if compositor.is_some()
                                            || dependencies.is_some()
                                            || app_busy
                                        {
                                            Status::Busy
                                        } else if cfg!(debug_assertions) {
                                            let scenario = *scenario;
                                            let (stop, nonce) = (stopping.clone(), nonce.clone());
                                            match thread::Builder::new()
                                                .name("eidolon-wl-check".into())
                                                .spawn(move || {
                                                    crate::compositor::checks::run_scenario(
                                                        scenario, stop, nonce,
                                                    )
                                                }) {
                                                Ok(job) => {
                                                    state.snapshot.compositor_workers = 1;
                                                    state.snapshot.compositor = None;
                                                    compositor = Some((*request_id, job));
                                                    asynchronous = true;
                                                    Status::Accepted
                                                }
                                                Err(_) => Status::Failed,
                                            }
                                        } else {
                                            Status::NotReady
                                        }
                                    }
                                    #[cfg(not(any(test, feature = "debug-fixtures")))]
                                    {
                                        let _ = (nonce, request_id, scenario);
                                        Status::NotReady
                                    }
                                }
                                Command::NativeDepsProbe {
                                    nonce, request_id, ..
                                } => {
                                    if dependencies.is_some() || compositor.is_some() || app_busy {
                                        Status::Busy
                                    } else if let Some(paths) =
                                        config.paths.clone().filter(|_| cfg!(debug_assertions))
                                    {
                                        let (stop, nonce) = (stopping.clone(), nonce.clone());
                                        match thread::Builder::new()
                                            .name("eidolon-deps".into())
                                            .spawn(move || {
                                                crate::compositor::primitives::run(
                                                    std::path::Path::new(&paths.files_dir),
                                                    &nonce,
                                                    &stop,
                                                )
                                            }) {
                                            Ok(job) => {
                                                state.snapshot.dependency_workers = 1;
                                                state.snapshot.native_deps =
                                                    Some(Default::default());
                                                dependencies = Some((*request_id, job));
                                                asynchronous = true;
                                                Status::Accepted
                                            }
                                            Err(_) => Status::Failed,
                                        }
                                    } else {
                                        Status::NotReady
                                    }
                                }
                                Command::ForwardProbe { session, nonce, .. } => {
                                    if let Some(paths) =
                                        config.paths.as_ref().filter(|_| cfg!(debug_assertions))
                                    {
                                        if app_busy {
                                            Status::Busy
                                        } else {
                                            io.forward_probe(
                                                *session,
                                                std::path::Path::new(&paths.files_dir),
                                                nonce,
                                            )
                                        }
                                    } else {
                                        Status::NotReady
                                    }
                                }
                                Command::LinuxShmProbe {session,nonce,..} | Command::LinuxShmSdkProbe {session,nonce,..} => {
                                    #[cfg(any(test, feature = "debug-fixtures"))]
                                    {
                                        if linux_pins.is_none() || config.paths.is_none() {Status::NotReady}
                                        else if ssh.session!=Some(*session) {Status::Stale}
                                        else if ssh.state!=eidolon_transport::session::State::ProbeReady {Status::NotReady}
                                        else if pending_app.is_some() || app.is_some() || helper.is_some() || dependencies.is_some() || compositor.is_some() || ssh.linux_forward.is_some() {Status::Busy}
                                        else {
                                            let virtual_clock = matches!(&command, Command::LinuxShmProbe {..});
                                            pending_app=Some((*session,nonce.clone(),eidolon_model::commands::AppMode::Pattern,virtual_clock,true));
                                            state.snapshot.app=Some(crate::session::Report{state:"queued",session:Some(*session),nonce:nonce.clone(),mode:Some(eidolon_model::commands::AppMode::Pattern),callback_clock:if virtual_clock {"virtual"} else {"target-driven"},..Default::default()});
                                            Status::Accepted
                                        }
                                    }
                                    #[cfg(not(any(test, feature = "debug-fixtures")))]
                                    {let _=(session,nonce);Status::InvalidArgument}
                                }
                                Command::LinuxGtkProbe {session,nonce,case,..} => {
                                    #[cfg(any(test, feature = "debug-fixtures"))]
                                    {
                                        if linux_gtk_pins.is_none() || config.paths.is_none() {Status::NotReady}
                                        else if ssh.session!=Some(*session) {Status::Stale}
                                        else if ssh.state!=eidolon_transport::session::State::ProbeReady {Status::NotReady}
                                        else if pending_app.is_some() || app.is_some() || helper.is_some() || dependencies.is_some() || compositor.is_some() || ssh.linux_forward.is_some() {Status::Busy}
                                        else {
                                            pending_app=Some((*session,nonce.clone(),case.mode(),false,true));
                                            state.snapshot.app=Some(crate::session::Report{state:"queued",session:Some(*session),nonce:nonce.clone(),mode:Some(case.mode()),callback_clock:"target-driven",..Default::default()});
                                            Status::Accepted
                                        }
                                    }
                                    #[cfg(not(any(test, feature = "debug-fixtures")))]
                                    {let _=(session,nonce,case);Status::InvalidArgument}
                                }
                                Command::LinuxGtkRecover {session,nonce,case,..} => {
                                    #[cfg(any(test, feature = "debug-fixtures"))]
                                    {
                                        if app_busy || helper.is_some() || dependencies.is_some() || compositor.is_some() {Status::Busy}
                                        else if let (Some(pins),Some(paths))=(linux_gtk_pins.as_ref(),config.paths.as_ref()) {
                                            io.linux_gtk_probe(*session,std::path::Path::new(&paths.files_dir),None,nonce,pins.binding(*case))
                                        }else{Status::NotReady}
                                    }
                                    #[cfg(not(any(test, feature = "debug-fixtures")))]
                                    {let _=(session,nonce,case);Status::InvalidArgument}
                                }
                                Command::LinuxShmRecover {session,nonce,..} => {
                                    #[cfg(any(test, feature = "debug-fixtures"))]
                                    {
                                        if app_busy || helper.is_some() || dependencies.is_some() || compositor.is_some() {Status::Busy}
                                        else if let (Some(pins),Some(paths))=(linux_pins.as_ref(),config.paths.as_ref()) {
                                            io.linux_probe(*session,std::path::Path::new(&paths.files_dir),None,nonce,pins.clone())
                                        }else{Status::NotReady}
                                    }
                                    #[cfg(not(any(test, feature = "debug-fixtures")))]
                                    {let _=(session,nonce);Status::InvalidArgument}
                                }
                                Command::ConnectSavedProbe { request_id, .. } => {
                                    if !cfg!(any(test, feature = "debug-fixtures")) {
                                        Status::InvalidArgument
                                    } else if native_busy {
                                        Status::Busy
                                    } else if let Some(spec) = &probe_spec {
                                        io.connect_spec(spec.clone(), *request_id)
                                    } else {
                                        Status::NotReady
                                    }
                                }
                                Command::Connect {
                                    profile_id,
                                    request_id,
                                    ..
                                } => {
                                    if native_busy && !(v3_recovery_allowed && profile_id == "remote-linux"
                                        && desktop_v3_binding.is_some()) {
                                        Status::Busy
                                    } else if profile_id == "remote-linux" {
                                        if let (Some(profile), Some(binding), Some(paths)) = (
                                            desktop_profile.as_ref(), desktop_v3_binding.as_ref(), config.paths.as_ref()
                                        ) {
                                            io.connect_linux_desktop_v3(
                                                profile.clone(), binding.configuration().clone(),
                                                binding.storage().clone(),
                                                std::path::Path::new(&paths.files_dir), *request_id,
                                            )
                                        } else if let (Some(profile), Some(pins), Some(paths)) = (desktop_profile.as_ref(), desktop_pins.as_ref(), config.paths.as_ref()) {
                                            io.connect_linux(profile.clone(), pins.clone(), std::path::Path::new(&paths.files_dir), *request_id)
                                        } else { Status::NotReady }
                                    } else if let Some(profile) = profiles.get(profile_id) {
                                        io.connect(profile.clone(), *request_id)
                                    } else {
                                        io.unknown_profile()
                                    }
                                }
                                Command::CancelConnect {
                                    connect_request, ..
                                } => {
                                    let code = io.cancel_connect(*connect_request);
                                    if code == Status::Accepted {
                                        state.clear_credentials();
                                        last_credential = None;
                                        resetting.store(true, Ordering::Release);
                                    }
                                    code
                                }
                                Command::Disconnect { session, .. } => {
                                    let code = io.disconnect(*session);
                                    if code == Status::Accepted {
                                        state.clear_credentials();
                                        last_credential = None;
                                    }
                                    code
                                }
                                Command::SurfaceLost { target, .. } => {
                                    let code = gl.detach(*target);
                                    if code == Status::Accepted
                                        && let Err(error) = state.panels.retire(*target)
                                    {
                                        state.fail(error);
                                    }
                                    code
                                }
                                Command::WaypipeProbe {
                                    nonce, request_id, ..
                                }
                                | Command::HelperProbe {
                                    nonce, request_id, ..
                                } => {
                                    if helper.is_some() || app_busy {
                                        Status::Busy
                                    } else if let Some(paths) =
                                        config.paths.clone().filter(|_| cfg!(debug_assertions))
                                    {
                                        let waypipe =
                                            matches!(command, Command::WaypipeProbe { .. });
                                        let (progress, stop, nonce) = (
                                            progress_state.clone(),
                                            stopping.clone(),
                                            nonce.clone(),
                                        );
                                        match thread::Builder::new()
                                            .name("eidolon-helper".into())
                                            .spawn(move || {
                                                if waypipe {
                                                    return crate::waypipe::probe(
                                                        &std::path::Path::new(
                                                            &paths.native_library_dir,
                                                        )
                                                        .join("libwaypipe_bin.so"),
                                                        std::path::Path::new(&paths.files_dir),
                                                        &nonce,
                                                        stop,
                                                        |report| {
                                                            progress
                                                                .lock()
                                                                .unwrap_or_else(|e| e.into_inner())
                                                                .snapshot
                                                                .helper = Some(report.clone());
                                                        },
                                                    );
                                                }
                                                let executable =
                                                    std::path::Path::new(&paths.native_library_dir)
                                                        .join("libeidolon_probe_bin.so");
                                                crate::helper::probe(
                                                    &executable,
                                                    std::path::Path::new(&paths.files_dir),
                                                    &nonce,
                                                    stop,
                                                    |report| {
                                                        progress
                                                            .lock()
                                                            .unwrap_or_else(|e| e.into_inner())
                                                            .snapshot
                                                            .helper = Some(report.clone());
                                                    },
                                                )
                                            }) {
                                            Ok(job) => {
                                                state.snapshot.helper_workers = 1;
                                                state.snapshot.helper = Some(Default::default());
                                                helper = Some((*request_id, job));
                                                asynchronous = true;
                                                Status::Accepted
                                            }
                                            Err(_) => Status::Failed,
                                        }
                                    } else {
                                        Status::NotReady
                                    }
                                }
                                Command::SceneReady { scene, .. } => {
                                    if state.snapshot.scene.is_some_and(|old| *scene <= old) {
                                        Status::Stale
                                    } else {
                                        state.snapshot.scene = Some(*scene);
                                        state.workspace.scene(Some(*scene));
                                        Status::Accepted
                                    }
                                }
                                Command::SetXrState { state: xr, expected_scene, .. } => {
                                    if expected_scene.is_some_and(|scene| state.snapshot.scene != Some(scene)) {
                                        Status::Stale
                                    } else {
                                        state.snapshot.xr_state = *xr;
                                        Status::Accepted
                                    }
                                }
                                Command::SetActivityState { visible, expected_scene, .. } => {
                                    if expected_scene.is_some_and(|scene| state.snapshot.scene != Some(scene)) {
                                        Status::Stale
                                    } else {
                                        state.snapshot.activity_visible = *visible;
                                        Status::Accepted
                                    }
                                }
                                Command::ResetInput { .. } => Status::Accepted,
                                Command::RegisterSdkBinding { .. }
                                | Command::SdkBindingEvidence { .. }
                                | Command::SceneClosing { .. }
                                | Command::ReleaseSdkBinding { .. } => unreachable!(
                                    "SDK ownership is handled before the command queue"
                                ),
                            }};
                            if pending_app.is_none() && let Some(current)=app.as_ref() {
                                state.refresh_app_report(current);
                            }
                            state.snapshot.workspace=state.workspace.snapshot();
                            groups.sync(&mut state,&gl,&io,clock.elapsed(),&resetting);
                            state.snapshot.commands_processed += 1;
                            // ACKをUIへ公開する前にlast_sequence等も同じowner順序で更新する。
                            state.refresh_ime(app.as_ref());
                            if !asynchronous {
                                state.emit(Event::CommandComplete {
                                    request_id: command.request_id(),
                                    code,
                                });
                            }
                        } else {
                            drop(state);
                            thread::park_timeout(Duration::from_millis(5));
                        }
                    }
                }));
                if result.is_err() {
                    io.request_stop();
                    state
                        .lock()
                        .unwrap_or_else(|e| e.into_inner())
                        .fail("JNI_FAILURE");
                    // Failedは再利用しない。明示停止までは状態を診断可能にする。
                    while !stopping.load(Ordering::Acquire) {
                        thread::park_timeout(Duration::from_millis(5));
                    }
                }
                if let Some((_, job)) = helper.take() {
                    while !job.is_finished() {
                        thread::park_timeout(Duration::from_millis(5));
                    }
                    let report = job.join().unwrap_or_else(|_| crate::helper::Report {
                        error: Some("JNI_FAILURE"),
                        ..Default::default()
                    });
                    let mut state = state.lock().unwrap_or_else(|e| e.into_inner());
                    state.snapshot.helper = Some(report);
                    state.snapshot.helper_workers = 0;
                }
                if let Some((_, job)) = dependencies.take() {
                    while !job.is_finished() {
                        thread::park_timeout(Duration::from_millis(5));
                    }
                    let report =
                        job.join()
                            .unwrap_or_else(|_| crate::compositor::primitives::Report {
                                state: "fail",
                                error: Some("JNI_FAILURE"),
                                ..Default::default()
                            });
                    let mut shared = state.lock().unwrap_or_else(|e| e.into_inner());
                    shared.snapshot.native_deps = Some(report);
                    shared.snapshot.dependency_workers = 0;
                }
                if let Some((_, job)) = compositor.take() {
                    while !job.is_finished() {
                        thread::park_timeout(Duration::from_millis(5));
                    }
                    let report = job.join().unwrap_or_else(
                        |_| serde_json::json!({"state":"fail", "error":"JNI_FAILURE"}),
                    );
                    let mut shared = state.lock().unwrap_or_else(|e| e.into_inner());
                    shared.snapshot.compositor = Some(report);
                    shared.snapshot.compositor_workers = 0;
                }
                gl.request_stop();
                io.request_stop();
                v3_pending.take();
                // Keep native dispatch, SDK receipts, renderer fences and the I2
                // finalizer progressing together, including after core unwind.
                loop {
                    let ssh = io.snapshot();
                    let remote_gone = io.is_finished()
                        || (ssh.owned_connections == 0 && ssh.forward.active == 0);
                    if let Some(current) = app.as_mut() {
                        current.tick(true, remote_gone);
                        let mut shared = state.lock().unwrap_or_else(|e| e.into_inner());
                        if current.is_v3() && let Some(observer) = v3_observer.as_mut()
                            && let Err(error) = shared.observe_v3(
                                current, observer, &ssh, &gl.snapshot(),
                                clock.elapsed().as_millis() as u64, true,
                            ) {
                                observer.fail(&eidolon_model::linux_desktop_v3::Error(error));
                                shared.snapshot.desktop_v3_presentation = observer.snapshot().ok();
                                shared.snapshot.desktop_v3_sdk_target = None;
                                current.fail(error);
                            }
                        shared.refresh_app_report(current);
                        // The unwind drain owns the same Board as the normal
                        // loop. Observe actual closed-session/all-generation
                        // resource facts instead of leaving its last report
                        // stale or clearing the Board from an empty snapshot.
                        if !current.virtual_clock() {
                            let report = current.snapshot();
                            let windows = report.windows.iter().map(|window| crate::window_slots::Window {
                                id: window.window, app_id: window.app_id.as_deref(),
                                life: window.life, admissible: false,
                            }).collect::<Vec<_>>();
                            let renderer = gl.snapshot();
                            let native = renderer.targets.iter().map(|target| target.key).collect::<Vec<_>>();
                            let panels = if gl.is_finished() && renderer.targets.is_empty() {
                                vec![]
                            } else {
                                shared.panels.snapshot().iter().map(|panel| panel.target).collect::<Vec<_>>()
                            };
                            if slots.observe(crate::window_slots::Observation {
                                session: current.session(), nonce: current.nonce(), windows: &windows,
                                session_closed: current.closed(), native: &native,
                                sdk: &shared.sdk_bindings, panels: &panels,
                            }).is_err() {
                                shared.fail("SLOT_STATE_INVALID");
                            }
                            shared.snapshot.window_slots = slots.report();
                        }
                    }
                    advance_v3_reclamation(
                        &state, app.as_mut(), &mut v3_observer, &mut v3_finalizer,
                        &gl, &io, true,
                    );
                    let mut shared = state.lock().unwrap_or_else(|e| e.into_inner());
                    if gl.snapshot().error == Some("RENDER_STALLED") {
                        shared.fail("RENDER_STALLED");
                    }
                    if io.is_finished() && gl.is_finished()
                        && app.as_ref().is_none_or(|current| current.closed())
                        && shared.sdk_bindings.is_empty()
                        && slots.report().occupied == 0
                        && shared.v3_retirement.is_none()
                        && !v3_finalizer.pending() && !v3_finalizer.worker_pending()
                    {
                        break;
                    }
                    drop(shared);
                    thread::park_timeout(Duration::from_millis(5));
                }
                let mut state = state.lock().unwrap_or_else(|e| e.into_inner());
                state.snapshot.state = RuntimeState::Stopping;
                state.snapshot.xr_state = XrState::Idle;
                state.snapshot.activity_visible = false;
                state.snapshot.input_resets += 1;
                state.snapshot.commands_cancelled += state.commands.len() as u64;
                state.commands.clear();
                state.ime_ui = None;
                state.snapshot.state = RuntimeState::Stopped;
                state.shutdown.finish(Instant::now());
                state.snapshot.theme.workers = 0;
                state.snapshot.theme.loading = false;
                // Renderer, transport, native Session and every SDK binding
                // have actually retired above. Release the remaining native
                // panel-plan metadata too; an empty serialized array alone is
                // not the ownership proof for configuration persistence.
                state.panels = Default::default();
                state.snapshot.panels.clear();
                state.snapshot.window_groups=Default::default();
                state.clear_credentials();
                state.terminal.push_back(Event::Stopped);
            })?;
        Ok(Self {
            handle: runtime_handle,
            shared,
            stop,
            reset,
            worker,
            render,
            transport,
            normal_linux,
        })
    }

    fn lock(&self) -> MutexGuard<'_, Shared> {
        self.shared.lock().unwrap_or_else(|e| e.into_inner())
    }
    pub fn command(&self, command: Command) -> Status {
        if matches!(
            command,
            Command::ConnectSavedProbe { .. }
                | Command::LinuxShmProbe { .. }
                | Command::LinuxShmSdkProbe { .. }
                | Command::LinuxShmRecover { .. }
                | Command::LinuxGtkProbe { .. }
                | Command::LinuxGtkRecover { .. }
        ) && !cfg!(any(test, feature = "debug-fixtures"))
        {
            return Status::InvalidArgument;
        }
        // BUSYでもfocus喪失・解除の安全効果は通常queueと別に伝える。
        if command.needs_input_reset() {
            self.reset.store(true, Ordering::Release);
            self.worker.thread().unpark();
        }
        let mut state = self.lock();
        if let Command::SdkBindingEvidence {
            target,
            create_command,
            adapter,
            phase,
            ..
        } = &command
        {
            let code = state.observe_sdk(
                crate::sdk_evidence::Lease {
                    target: *target,
                    create: *create_command,
                    adapter: *adapter,
                },
                *phase,
                &self.render,
            );
            if code == Status::Accepted {
                state.snapshot.commands_accepted += 1;
                state.snapshot.commands_processed += 1;
                self.worker.thread().unpark();
            }
            return code;
        }
        if let Command::SceneReady {
            scene, request_id, ..
        } = &command
        {
            if state.snapshot.state == RuntimeState::Failed {
                return Status::Failed;
            }
            if state.snapshot.state != RuntimeState::Ready || self.stop.load(Ordering::Acquire) {
                return Status::NotReady;
            }
            if state
                .last_scene
                .or(state.snapshot.scene)
                .is_some_and(|old| *scene <= old)
            {
                return Status::Stale;
            }
            if let Some(old) = state.snapshot.scene {
                state.sdk_evidence.seal_scene(old);
            }
            state.last_scene = Some(*scene);
            state.snapshot.scene = Some(*scene);
            state.snapshot.xr_state = XrState::Idle;
            state.snapshot.activity_visible = false;
            state.render_visibility = [false; 2];
            state.ime_ui = None;
            state.workspace.scene(Some(*scene));
            self.render.set_visible(false);
            state.snapshot.commands_accepted += 1;
            state.snapshot.commands_processed += 1;
            state.emit(Event::CommandComplete {
                request_id: *request_id,
                code: Status::Accepted,
            });
            self.worker.thread().unpark();
            return Status::Accepted;
        }
        if let Command::SceneClosing { scene, .. } = &command {
            if state.snapshot.scene != Some(*scene) {
                return Status::Stale;
            }
            state.last_scene = Some(state.last_scene.map_or(*scene, |old| old.max(*scene)));
            state.snapshot.scene = None;
            state.snapshot.xr_state = XrState::Idle;
            state.render_visibility[1] = false;
            state.ime_ui = None;
            state.workspace.scene(None);
            state.sdk_evidence.seal_scene(*scene);
            let retiring: Vec<_> = state
                .panels
                .snapshot()
                .into_iter()
                .filter(|panel| panel.target.scene == *scene)
                .map(|panel| panel.target)
                .collect();
            for target in retiring {
                let _ = state.panels.retire(target);
                if let Some(owner) = state.v3_retirement.as_mut() {
                    let _ = owner.retire_target(target, &self.render);
                }
            }
            self.render.set_visible(false);
            state.snapshot.commands_accepted += 1;
            state.snapshot.commands_processed += 1;
            self.worker.thread().unpark();
            return Status::Accepted;
        }
        if let Command::SetActivityState {
            expected_scene: Some(scene),
            ..
        }
        | Command::SetXrState {
            expected_scene: Some(scene),
            ..
        } = &command
            && state.snapshot.scene != Some(*scene)
        {
            return Status::Stale;
        }
        // 所有権の記帳はqueueの満杯や停止中にも左右されない。releaseは全tupleを照合し、
        // 古いscene/重複ackで別Entityの所有記録を消さない。通常の操作commandは従来どおりqueueへ。
        if let Command::RegisterSdkBinding { target, .. }
        | Command::ReleaseSdkBinding { target, .. } = &command
        {
            let diagnostic = matches!(
                command,
                Command::RegisterSdkBinding {
                    diagnostic: true,
                    ..
                }
            );
            let code = if matches!(command, Command::ReleaseSdkBinding { .. }) {
                if state.sdk_evidence.contains(*target) {
                    return Status::NotReady;
                }
                if let Some(index) = state.sdk_bindings.iter().position(|key| key == target) {
                    state.sdk_bindings.remove(index);
                    state.diagnostic_sdk_bindings.retain(|key| key != target);
                    Status::Accepted
                } else {
                    Status::Stale
                }
            } else if state.snapshot.state != RuntimeState::Ready
                || self.stop.load(Ordering::Acquire)
            {
                Status::NotReady
            } else if state.snapshot.scene != Some(target.scene) {
                Status::Stale
            } else if state
                .v3_retirement
                .as_ref()
                .is_some_and(|owner| owner.origin().session() == target.session)
            {
                let Command::RegisterSdkBinding {
                    create_command: Some(create),
                    adapter: Some(adapter),
                    ..
                } = &command
                else {
                    return Status::InvalidArgument;
                };
                if diagnostic
                    || !state.panels.snapshot().iter().any(|panel| {
                        panel.target == *target
                            && panel.command_seq == *create
                            && panel.action == eidolon_model::commands::PanelAction::Create
                            && panel.error.is_none()
                            && panel.v3_evidence_required
                    })
                    || state.v3_retirement.as_ref().is_none_or(|owner| {
                        owner.is_sealed() || !owner.owns_target(*target, *create)
                    })
                {
                    return Status::Stale;
                }
                if !state.sdk_bindings.contains(target)
                    && state.sdk_bindings.len() >= limits::SDK_BINDINGS
                {
                    return Status::Busy;
                }
                let lease = crate::sdk_evidence::Lease {
                    target: *target,
                    create: *create,
                    adapter: *adapter,
                };
                let code = state.sdk_evidence.register(lease);
                if code != Status::Accepted {
                    return code;
                }
                if state
                    .v3_retirement
                    .as_mut()
                    .unwrap()
                    .sdk_registered(*target, *create)
                    .is_err()
                {
                    state.sdk_evidence.seal_target(*target);
                    return Status::Stale;
                }
                if !state.sdk_bindings.contains(target) {
                    state.sdk_bindings.push(*target);
                }
                Status::Accepted
            } else if matches!(
                &command,
                Command::RegisterSdkBinding {
                    create_command: Some(_),
                    ..
                }
            ) {
                Status::Stale
            } else if state.sdk_bindings.contains(target) {
                // 同じownerの再送は許すが、アプリ所有を診断へ付け替えて回収を迂回できない。
                if state.diagnostic_sdk_bindings.contains(target) == diagnostic {
                    Status::Accepted
                } else {
                    Status::Stale
                }
            } else if diagnostic
                && (state.snapshot.app.is_some()
                    || self.render.snapshot().mode != eidolon_render::RenderMode::Diagnostic)
            {
                // use_windowsは一方向。古い診断callbackで新しいアプリ所有を免除しない。
                Status::NotReady
            } else if state.sdk_bindings.len() >= limits::SDK_BINDINGS {
                Status::Busy
            } else {
                state.sdk_bindings.push(*target);
                if diagnostic {
                    state.diagnostic_sdk_bindings.push(*target);
                }
                Status::Accepted
            };
            if code == Status::Accepted {
                state.snapshot.commands_accepted += 1;
                state.snapshot.commands_processed += 1;
                self.worker.thread().unpark();
            }
            return code;
        }
        // BUSYでも非表示の安全効果を先に適用。可視方向は受付成功時だけ更新する。
        match &command {
            Command::SetActivityState { visible: false, .. } => {
                state.snapshot.activity_visible = false;
                state.render_visibility[0] = false;
                self.render.set_visible(false);
            }
            Command::SetXrState {
                state: xr @ (XrState::Idle | XrState::Visible),
                ..
            } => {
                state.snapshot.xr_state = *xr;
                if *xr == XrState::Idle {
                    state.render_visibility[1] = false;
                    self.render.set_visible(false);
                }
            }
            _ => (),
        }
        if state.snapshot.state == RuntimeState::Failed {
            return Status::Failed;
        }
        if state.snapshot.state != RuntimeState::Ready || self.stop.load(Ordering::Acquire) {
            return Status::NotReady;
        }
        if state.commands.len() == limits::CORE_COMMANDS {
            if matches!(
                command,
                Command::Key { .. }
                    | Command::FocusWindow { .. }
                    | Command::Pointer { .. }
                    | Command::Ime { .. }
            ) {
                self.reset.store(true, Ordering::Release);
                self.worker.thread().unpark();
            }
            return Status::Busy;
        }
        match &command {
            Command::SetActivityState { visible, .. } => state.render_visibility[0] = *visible,
            Command::SetXrState { state: xr, .. } => {
                state.render_visibility[1] = *xr != XrState::Idle
            }
            _ => (),
        }
        self.render
            .set_visible(state.render_visibility[0] && state.render_visibility[1]);
        state.commands.push_back(command);
        state.snapshot.commands_accepted += 1;
        self.worker.thread().unpark();
        Status::Accepted
    }
    pub fn request_stop(&self) -> Status {
        {
            let mut state = self.lock();
            state.shutdown.start(Instant::now());
            self.stop.store(true, Ordering::Release);
            if state.snapshot.state != RuntimeState::Stopped {
                state.snapshot.state = RuntimeState::Stopping;
            }
            state.clear_credentials();
            state.ime_ui = None;
            state.sdk_evidence.seal();
            state.snapshot.desktop_v3_sdk_target = None;
            if let Some(owner) = state.v3_retirement.as_mut()
                && let Err(error) = owner.seal(&self.render)
                && error != "DESKTOP_LOCAL_RETIREMENT_PENDING"
            {
                state.snapshot.desktop_v3_cleanup_error.get_or_insert(error);
            }
        }
        self.render.request_stop();
        self.transport.request_stop();
        self.worker.thread().unpark();
        Status::Accepted
    }
    pub fn snapshot(&self) -> Snapshot {
        let state = self.lock();
        let (transport, transport_reclamation) = self.transport.reclamation_snapshot();
        let render = self.render.snapshot();
        let mut pending = vec![];
        if self.stop.load(Ordering::Acquire) {
            if !self.worker.is_finished() {
                pending.push("core");
            }
            if !self.transport.is_finished() {
                pending.push("transport");
            }
            if !self.render.is_finished() {
                pending.push("render");
            }
            if state.snapshot.helper_workers != 0 {
                pending.push("helper");
            }
            if state.snapshot.dependency_workers != 0 {
                pending.push("dependencies");
            }
            if state.snapshot.compositor_workers != 0 {
                pending.push("compositor");
            }
            if !self.worker.is_finished()
                && state
                    .snapshot
                    .app
                    .as_ref()
                    .is_some_and(|app| app.state != "stopped" && !app.directory_cleaned)
            {
                pending.push("app");
            }
            if state.snapshot.theme.workers != 0 {
                pending.push("theme");
            }
            if !state.sdk_bindings.is_empty() {
                pending.push("sdk");
            }
        }
        let shutdown = state
            .shutdown
            .report(Instant::now(), pending, state.sdk_bindings.len());
        let panels = state.panels.snapshot();
        let app_resources_pending = state.snapshot.app.as_ref().is_some_and(|app| {
            app.state != "stopped"
                || app.clients != 0
                || app.objects != 0
                || app.surfaces != 0
                || app.pixel_bytes != 0
                || app.pool_bytes != 0
                || app.pool_count != 0
                || app.local.owned_groups != 0
                || app.local.workers != 0
        });
        let auxiliary_resources_pending = state.snapshot.helper.as_ref().is_some_and(|helper| {
            helper.owned_children != 0
                || !helper.directory_removed
                || helper
                    .waypipe
                    .as_ref()
                    .is_some_and(|waypipe| waypipe.owned_groups != 0 || !waypipe.socket_cleaned)
        }) || state.snapshot.native_deps.as_ref().is_some_and(
            |dependencies| {
                dependencies.mapped_regions_after != 0
                    || !dependencies.owned_fds_closed
                    || !dependencies.socket_cleaned
            },
        ) || state.snapshot.compositor.as_ref().is_some_and(
            |report| {
                report.get("error").and_then(serde_json::Value::as_str) == Some("RESOURCE_LEAK")
            },
        );
        let origin_resources_retired = !app_resources_pending
            && state.app_sdk_bindings() == 0
            && panels.is_empty()
            && state.snapshot.window_slots.occupied == 0
            && render
                .targets
                .iter()
                .all(|target| target.role == eidolon_render::RenderMode::Diagnostic);
        let native_origin_pending = state.v3_retirement.is_some()
            || state.snapshot.desktop_v3_finalizer_pending
            || state.v3_finalizer_workers != 0;
        let local_recovery_proven = origin_resources_retired
            && ((state.v3_recovery_local_proven
                && state
                    .v3_retirement
                    .as_ref()
                    .is_some_and(|owner| owner.is_sealed()))
                || (state.v3_retirement.is_none()
                    && (transport_reclamation.no_local_allocation_proven
                        || transport_reclamation.v2_capture_proven)));
        let add = |values: &[usize]| {
            values
                .iter()
                .fold(0usize, |sum, value| sum.saturating_add(*value))
        };
        let workers = add(&[
            usize::from(!self.worker.is_finished()),
            transport.workers,
            transport.tokio_threads,
            transport.forward.active,
            render.workers,
            state.snapshot.helper_workers,
            state.snapshot.dependency_workers,
            state.snapshot.compositor_workers,
            state.snapshot.theme.workers,
            state
                .snapshot
                .app
                .as_ref()
                .map_or(0, |app| app.local.workers),
            state.v3_finalizer_workers,
        ]);
        let pending_operations = add(&[
            state.commands.len(),
            render.queued,
            transport_reclamation.pending_operations,
            panels.iter().filter(|panel| !panel.applied).count(),
            usize::from(state.snapshot.desktop_v3_finalizer_pending),
            state.v3_finalizer_workers,
        ]);
        let origin_retired = origin_resources_retired
            && !native_origin_pending
            && !transport_reclamation.remote_cleanup_pending
            && transport.owned_connections == 0
            && transport.forward.active == 0
            && transport_reclamation.pending_operations == 0
            && state.commands.is_empty();
        let reclamation = crate::diagnostics::RuntimeReclamationSnapshot {
            runtime: self.handle,
            origin_retired,
            recovery_connect_allowed: local_recovery_proven
                && !self.stop.load(Ordering::Acquire)
                && transport_reclamation.recovery_connect_allowed,
            recovery_command_allowed: local_recovery_proven
                && !self.stop.load(Ordering::Acquire)
                && transport_reclamation.recovery_command_allowed,
            connections: transport.owned_connections,
            workers,
            native_targets: add(&[
                render.targets.len(),
                state.snapshot.desktop_v3_pending_targets,
            ]),
            panels: panels.len(),
            sdk_bindings: state.sdk_bindings.len(),
            board_windows: state.snapshot.window_slots.occupied,
            pending_operations,
            remote_cleanup_pending: transport_reclamation.remote_cleanup_pending,
            local_cleanup_pending: app_resources_pending
                || auxiliary_resources_pending
                || native_origin_pending
                || (self.stop.load(Ordering::Acquire)
                    && (render.contexts != 0
                        || render.latest_images != 0
                        || render.composition.textures != 0
                        || render.composition.texture_bytes != 0
                        || render.composition.decoration_texture_bytes != 0)),
            shutdown_complete: shutdown.complete,
        };
        Snapshot {
            shutdown,
            reclamation: Some(reclamation),
            app_sdk_bindings: state.app_sdk_bindings(),
            transport,
            render,
            commands_pending: state.commands.len(),
            events_pending: state.events.len() + state.terminal.len(),
            ..state.snapshot.clone()
        }
    }
    pub fn catalog_ui(&self) -> eidolon_transport::session::CatalogUi {
        self.transport.catalog_ui()
    }
    /// この返り値はUI接続専用。通常snapshot・診断・disk保存の経路から隔離する。
    pub fn ime_ui(&self) -> Option<eidolon_model::ime::ImeSnapshot> {
        let state = self.lock();
        if self.stop.load(Ordering::Acquire)
            || self.reset.load(Ordering::Acquire)
            || state.snapshot.state != RuntimeState::Ready
            || !state.snapshot.activity_visible
            || state.snapshot.xr_state != XrState::Focused
            || !state.render_visibility.iter().all(|v| *v)
        {
            None
        } else {
            state.ime_ui.clone()
        }
    }
    pub fn poll(&self, max: usize) -> Result<Vec<Event>, Status> {
        if !(1..=limits::POLL_EVENTS).contains(&max) {
            return Err(Status::InvalidArgument);
        }
        let mut state = self.lock();
        let mut events = Vec::with_capacity(max);
        let mut bytes = 2;
        // 以前の通常event→failure→stoppedの順。Stopped後に古いReady等を配送しない。
        while events.len() < max {
            let next = state.events.front().or_else(|| state.terminal.front());
            let Some(next) = next else {
                break;
            };
            let size = serde_json::to_vec(next).expect("bounded event").len() + 1;
            if bytes + size > limits::POLL_BYTES {
                break;
            }
            bytes += size;
            let next = state
                .events
                .pop_front()
                .or_else(|| state.terminal.pop_front())
                .unwrap();
            events.push(next);
        }
        Ok(events)
    }
    pub fn can_destroy(&self) -> bool {
        self.snapshot().state == RuntimeState::Stopped && self.worker.is_finished()
    }
    pub fn worker_finished(&self) -> bool {
        self.worker.is_finished()
    }
    pub fn attach_surface(
        &self,
        key: eidolon_model::surface::TargetKey,
        window: eidolon_render::PlatformWindow,
        width: i32,
        height: i32,
    ) -> Status {
        let state = self.lock();
        if state.snapshot.state != RuntimeState::Ready || self.stop.load(Ordering::Acquire) {
            return Status::NotReady;
        }
        if state.snapshot.scene != Some(key.scene) {
            return Status::Stale;
        }
        self.render.attach(key, window, width, height)
    }
    pub fn attach_window_surface(
        &self,
        key: eidolon_model::surface::TargetKey,
        window: eidolon_render::PlatformWindow,
        width: i32,
        height: i32,
    ) -> Status {
        let state = self.lock();
        if state.snapshot.state != RuntimeState::Ready || self.stop.load(Ordering::Acquire) {
            return Status::NotReady;
        }
        if state.snapshot.scene != Some(key.scene) {
            return Status::Stale;
        }
        let valid = state.snapshot.app.as_ref().is_some_and(|app| {
            app.session == Some(key.session)
                && app.callback_clock == "target-driven"
                && app.ready
                && app.error.is_none()
                && (app.mode != Some(eidolon_model::commands::AppMode::LinuxDesktop)
                    || app.gui_windows.contains(&key.window))
                && app.windows.iter().any(|window| {
                    window.window == key.window
                        && window.life == eidolon_model::window::WindowLife::Mapped
                })
        });
        if !valid || !state.panels.accepts(key) {
            return Status::Stale;
        }
        if let Some(owner) = state.v3_retirement.as_ref()
            && owner.origin().session() == key.session
            && (owner.is_sealed()
                || !state.sdk_bindings.contains(&key)
                || !state.sdk_evidence.contains(key)
                || !state.panels.snapshot().iter().any(|panel| {
                    panel.target == key
                        && panel.action == eidolon_model::commands::PanelAction::Create
                        && panel.v3_evidence_required
                        && owner.owns_target(key, panel.command_seq)
                }))
        {
            return Status::NotReady;
        }
        self.render.attach_window(key, window, width, height)
    }
    pub fn provide_credential(
        &self,
        session: eidolon_model::ids::SessionId,
        request: eidolon_model::ids::CommandSeq,
        private: eidolon_transport::credentials::Secret,
    ) -> Status {
        let mut state = self.lock();
        if state.snapshot.state != RuntimeState::Ready || self.stop.load(Ordering::Acquire) {
            return Status::Stale;
        }
        if state
            .credential_scope
            .as_ref()
            .is_none_or(|scope| scope.revision.is_some())
        {
            return Status::Stale;
        }
        Self::supply_credential(&mut state, &self.worker, session, request, private)
    }
    /// 通常 Linux / debug probe 接続owner専用。通常diagnosticsはbindingを含めない。
    pub fn credential_scope(
        &self,
    ) -> Result<Option<eidolon_transport::session::CredentialScope>, Status> {
        if !self.normal_linux && !cfg!(any(test, feature = "debug-fixtures")) {
            return Err(Status::InvalidArgument);
        }
        let state = self.lock();
        if state.snapshot.state != RuntimeState::Ready || self.stop.load(Ordering::Acquire) {
            return Ok(None);
        }
        Ok(state
            .credential_scope
            .as_ref()
            .filter(|scope| {
                scope.revision.is_some()
                    && (scope.profile_id == "remote-linux"
                        || cfg!(any(test, feature = "debug-fixtures")))
                    && self.transport.credential_scope().is_some_and(|live| {
                        live.session == scope.session
                            && live.request == scope.request
                            && live.profile_id == scope.profile_id
                            && live.revision == scope.revision
                            && live.binding == scope.binding
                    })
            })
            .cloned())
    }
    pub fn provide_credential_for(
        &self,
        session: eidolon_model::ids::SessionId,
        request: eidolon_model::ids::CommandSeq,
        binding: &str,
        private: eidolon_transport::credentials::Secret,
    ) -> Status {
        if !self.normal_linux && !cfg!(any(test, feature = "debug-fixtures")) {
            return Status::InvalidArgument;
        }
        let mut state = self.lock();
        if state.snapshot.state != RuntimeState::Ready
            || self.stop.load(Ordering::Acquire)
            || state.credential_scope.as_ref().is_none_or(|scope| {
                scope.session != session
                    || scope.request != request
                    || scope.revision.is_none()
                    || (scope.profile_id != "remote-linux"
                        && !cfg!(any(test, feature = "debug-fixtures")))
                    || scope.binding != binding
            })
            || self.transport.credential_scope().is_none_or(|scope| {
                scope.session != session
                    || scope.request != request
                    || scope.binding != binding
                    || state.credential_scope.as_ref().is_none_or(|captured| {
                        captured.profile_id != scope.profile_id
                            || captured.revision != scope.revision
                    })
            })
        {
            return Status::Stale;
        }
        Self::supply_credential(&mut state, &self.worker, session, request, private)
    }
    fn supply_credential(
        state: &mut Shared,
        worker: &JoinHandle<()>,
        session: eidolon_model::ids::SessionId,
        request: eidolon_model::ids::CommandSeq,
        private: eidolon_transport::credentials::Secret,
    ) -> Status {
        match state.credentials.provide(session, request, private) {
            Ok(()) => {
                worker.thread().unpark();
                Status::Accepted
            }
            Err(eidolon_transport::Error::Stale) => Status::Stale,
            Err(_) => Status::InvalidArgument,
        }
    }
    pub fn guarded<T>(&self, action: impl FnOnce() -> T) -> Result<T, Status> {
        catch_unwind(AssertUnwindSafe(action)).map_err(|_| {
            self.lock().fail("JNI_FAILURE");
            self.worker.thread().unpark();
            Status::Failed
        })
    }
    pub fn fail(&self) {
        self.lock().fail("JNI_FAILURE");
        self.worker.thread().unpark();
    }
}
impl Drop for Runtime {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        self.render.request_stop();
        self.transport.request_stop();
        self.worker.thread().unpark();
        // Joinしない。通常のdestroyはis_finished確認後にだけ呼ばれる。
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn legacy_credential_api_rejects_v2_and_private_binding_never_enters_snapshot() {
        let (entered_tx, entered_rx) = std::sync::mpsc::channel();
        let (resume_tx, resume_rx) = std::sync::mpsc::channel();
        let runtime = Runtime::start_with_hook(RuntimeConfig::default(), move || {
            entered_tx.send(()).unwrap();
            resume_rx.recv().unwrap();
        })
        .unwrap();
        entered_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        let session = eidolon_model::ids::SessionId::new(9).unwrap();
        let request = eidolon_model::ids::CommandSeq::new(7).unwrap();
        let binding = "private-probe-fixture-binding";
        {
            let mut state = runtime.lock();
            state.credentials.begin(session, request);
            state.credential_scope = Some(eidolon_transport::session::CredentialScope {
                session,
                request,
                profile_id: "linux-probe".into(),
                revision: Some("9007199254740993".into()),
                binding: binding.into(),
            });
        }
        let legacy_rejected = runtime.provide_credential(session, request, vec![].into());
        let forged_rejected =
            runtime.provide_credential_for(session, request, binding, vec![].into());
        let private_absent = !serde_json::to_string(&runtime.snapshot())
            .unwrap()
            .contains(binding)
            && !serde_json::to_string(&runtime.poll(64).unwrap())
                .unwrap()
                .contains(binding);
        runtime.request_stop();
        resume_tx.send(()).unwrap();
        wait(&runtime, Runtime::can_destroy);
        assert_eq!(legacy_rejected, Status::Stale);
        assert_eq!(forged_rejected, Status::Stale);
        assert!(private_absent);
        assert!(runtime.credential_scope().unwrap().is_none());
    }

    #[test]
    fn ime_text_is_private_and_urgent_reset_hides_it_even_when_queue_is_full() {
        let (entered_tx, entered_rx) = std::sync::mpsc::channel();
        let (resume_tx, resume_rx) = std::sync::mpsc::channel();
        let runtime = Runtime::start_with_hook(RuntimeConfig::default(), move || {
            entered_tx.send(()).unwrap();
            resume_rx.recv().unwrap();
        })
        .unwrap();
        entered_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        let private: eidolon_model::ime::ImeSnapshot = serde_json::from_value(serde_json::json!({
            "target":{"session":"1","window":"2","surface":"3","focus_token":"4",
                "input_epoch":0,"activation":1,"serial":1},"edit_revision":1,"last_sequence":0,
            "surrounding":{"text":"private-ime-fixture","cursor":0,"anchor":0},
            "content_hint":0,"content_purpose":0,"cursor_rectangle":null,"change_cause":0
        }))
        .unwrap();
        {
            let mut state = runtime.lock();
            state.render_visibility = [true, true];
            state.snapshot.activity_visible = true;
            state.snapshot.xr_state = XrState::Focused;
            state.ime_ui = Some(private);
        }
        let private_available = runtime.ime_ui().is_some();
        let ordinary_contains_text = serde_json::to_string(&runtime.snapshot())
            .unwrap()
            .contains("private-ime-fixture");
        for id in 1..=limits::CORE_COMMANDS {
            runtime.command(Command::SetWorkloadObservation {
                schema: 1,
                request_id: eidolon_model::ids::CommandSeq::new(id as i64).unwrap(),
                enabled: false,
            });
        }
        let result = runtime.command(Command::ResetInput {
            schema: 1,
            request_id: eidolon_model::ids::CommandSeq::new(999).unwrap(),
        });
        let reset_hid_text = runtime.ime_ui().is_none();
        runtime.request_stop();
        resume_tx.send(()).unwrap();
        wait(&runtime, Runtime::can_destroy);
        assert!(private_available);
        assert!(!ordinary_contains_text);
        assert_eq!(result, Status::Busy);
        assert!(reset_hid_text);
        assert!(runtime.ime_ui().is_none());
    }
    use eidolon_model::ids::{CommandSeq, SceneEpoch};
    use std::{sync::Barrier, time::Instant};
    fn wait(runtime: &Runtime, predicate: impl Fn(&Runtime) -> bool) {
        let deadline = Instant::now() + Duration::from_secs(2);
        while !predicate(runtime) {
            assert!(Instant::now() < deadline, "{:?}", runtime.snapshot());
            thread::sleep(Duration::from_millis(1));
        }
    }
    #[test]
    fn stop_snapshot_changes_before_a_busy_protocol_owner_can_run_again() {
        let (entered_tx, entered_rx) = std::sync::mpsc::channel();
        let (release_tx, release_rx) = std::sync::mpsc::channel();
        let runtime = Runtime::start_with_hook(RuntimeConfig::default(), move || {
            entered_tx.send(()).unwrap();
            release_rx.recv().unwrap();
        })
        .unwrap();
        entered_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        assert_eq!(runtime.snapshot().state, RuntimeState::Ready);
        assert_eq!(runtime.request_stop(), Status::Accepted);
        assert_eq!(runtime.snapshot().state, RuntimeState::Stopping);
        release_tx.send(()).unwrap();
        wait(&runtime, |r| r.snapshot().state == RuntimeState::Stopped);
    }
    #[test]
    fn group_command_without_verified_foot_never_owns_or_changes_normal_workspace() {
        use eidolon_model::{
            ids::{CommandSeq, SceneEpoch, SessionId, SurfaceGeneration, WindowId},
            surface::TargetKey,
        };
        let runtime = Runtime::start(RuntimeConfig::default()).unwrap();
        wait(&runtime, |r| r.snapshot().state == RuntimeState::Ready);
        let before = runtime.snapshot().input_resets;
        let target = TargetKey {
            session: SessionId::new(2).unwrap(),
            window: WindowId::new(3).unwrap(),
            scene: SceneEpoch::new(4).unwrap(),
            generation: SurfaceGeneration::new(5).unwrap(),
        };
        assert_eq!(
            runtime.command(Command::WindowGroup {
                schema: 1,
                request_id: CommandSeq::new(50).unwrap(),
                target,
                controls_revision: 0,
                expected_group: None,
                expected_revision: None,
                action: eidolon_model::window_groups::Action::Collapse
            }),
            Status::Accepted
        );
        wait(&runtime, |r| r.snapshot().commands_processed >= 1);
        let snapshot = runtime.snapshot();
        assert!(snapshot.input_resets > before);
        assert!(
            snapshot.window_groups.groups.is_empty()
                && snapshot.window_groups.candidates.is_empty()
        );
        assert!(snapshot.workspace.windows.is_empty());
        assert!(snapshot.window_groups.input_allowed(target));
        assert!(runtime.poll(32).unwrap().iter().any(|event|matches!(event,Event::CommandComplete{request_id,code:Status::NotReady} if request_id.get()==50)));
        runtime.request_stop();
        wait(&runtime, |r| r.snapshot().state == RuntimeState::Stopped);
        assert!(runtime.snapshot().window_groups.groups.is_empty());
    }
    fn scene(id: i64) -> Command {
        Command::SceneReady {
            schema: 1,
            request_id: CommandSeq::new(id).unwrap(),
            scene: SceneEpoch::new(id).unwrap(),
        }
    }
    fn queued_work(id: i64) -> Command {
        Command::SetWorkloadObservation {
            schema: 1,
            request_id: CommandSeq::new(id).unwrap(),
            enabled: false,
        }
    }
    fn completed(runtime: &Runtime, command: Command) -> Status {
        let request = command.request_id();
        let before = runtime.snapshot().commands_processed;
        assert_eq!(runtime.command(command), Status::Accepted);
        wait(runtime, |r| r.snapshot().commands_processed > before);
        runtime
            .poll(64)
            .unwrap()
            .into_iter()
            .find_map(|event| match event {
                Event::CommandComplete { request_id, code } if request_id == request => Some(code),
                _ => None,
            })
            .expect("queued command completion")
    }
    fn register_sdk(target: eidolon_model::surface::TargetKey, diagnostic: bool) -> Command {
        Command::RegisterSdkBinding {
            schema: 1,
            request_id: CommandSeq::new(100).unwrap(),
            target,
            diagnostic,
            create_command: None,
            adapter: None,
        }
    }
    fn release_sdk(target: eidolon_model::surface::TargetKey) -> Command {
        Command::ReleaseSdkBinding {
            schema: 1,
            request_id: CommandSeq::new(101).unwrap(),
            target,
        }
    }
    fn connect_missing_profile(request: i64) -> Command {
        Command::Connect {
            schema: 1,
            request_id: CommandSeq::new(request).unwrap(),
            profile_id: "termux-local".into(),
        }
    }
    #[test]
    fn storage_census_keeps_runtime_diagnostic_and_old_scene_owners_until_actual_stop() {
        use eidolon_model::{ids::RuntimeHandle, surface::TargetKey};
        let handle = RuntimeHandle::new(73).unwrap();
        let runtime = Runtime::start_registered(RuntimeConfig::default(), handle).unwrap();
        wait(&runtime, |r| r.snapshot().state == RuntimeState::Ready);
        let idle = runtime.snapshot().reclamation.unwrap();
        assert_eq!(idle.runtime, handle);
        assert!(idle.origin_retired);
        assert!(idle.workers > 0);
        assert!(!idle.shutdown_complete);
        assert!(!idle.recovery_connect_allowed && !idle.recovery_command_allowed);

        assert_eq!(completed(&runtime, scene(1)), Status::Accepted);
        let diagnostic = TargetKey::from_raw(1, 1, 1, 1).unwrap();
        let old_app = TargetKey::from_raw(1, 2, 1, 2).unwrap();
        assert_eq!(
            runtime.command(register_sdk(diagnostic, true)),
            Status::Accepted
        );
        assert_eq!(
            runtime.attach_surface(diagnostic, Default::default(), 64, 64),
            Status::Accepted
        );
        wait(&runtime, |r| r.snapshot().render.targets.len() == 1);
        let diagnostic_only = runtime.snapshot().reclamation.unwrap();
        assert!(
            diagnostic_only.origin_retired,
            "origin retirement excludes the diagnostic"
        );
        assert_eq!(diagnostic_only.native_targets, 1);
        assert_eq!(diagnostic_only.sdk_bindings, 1);
        assert_eq!(
            runtime.command(register_sdk(old_app, false)),
            Status::Accepted
        );
        assert!(!runtime.snapshot().reclamation.unwrap().origin_retired);
        assert_eq!(completed(&runtime, scene(2)), Status::Accepted);
        let new_scene_key = TargetKey::from_raw(1, 2, 2, 2).unwrap();
        assert_eq!(runtime.command(release_sdk(new_scene_key)), Status::Stale);
        assert_eq!(runtime.snapshot().reclamation.unwrap().sdk_bindings, 2);

        assert_eq!(runtime.request_stop(), Status::Accepted);
        wait(&runtime, |r| r.snapshot().render.workers == 0);
        let waiting = runtime.snapshot().reclamation.unwrap();
        assert_eq!(waiting.runtime, handle);
        assert_eq!(waiting.sdk_bindings, 2);
        assert!(!waiting.shutdown_complete);
        assert!(!runtime.can_destroy());
        assert_eq!(runtime.command(release_sdk(old_app)), Status::Accepted);
        assert_eq!(runtime.command(release_sdk(diagnostic)), Status::Accepted);
        wait(&runtime, Runtime::can_destroy);
        let done = runtime.snapshot().reclamation.unwrap();
        assert_eq!(done.runtime, handle);
        assert!(done.origin_retired && done.shutdown_complete);
        assert!(!done.remote_cleanup_pending && !done.local_cleanup_pending);
        assert_eq!(
            [
                done.connections,
                done.workers,
                done.native_targets,
                done.panels,
                done.sdk_bindings,
                done.board_windows,
                done.pending_operations,
            ],
            [0; 7]
        );
        // A pure default diagnostic has no positive Runtime owner and cannot
        // replace the last live snapshot in Android's StopReceipt.
        assert!(Snapshot::default().reclamation.is_none());

        // A finished worker does not erase its failed resource cleanup. These
        // retained reports have no live worker left to retry or fabricate proof.
        {
            let mut state = runtime.lock();
            state.snapshot.helper = Some(crate::helper::Report {
                error: Some("CLEANUP_FAILED"),
                directory_removed: false,
                ..Default::default()
            });
        }
        let failed_directory = runtime.snapshot().reclamation.unwrap();
        assert_eq!(failed_directory.workers, 0);
        assert!(failed_directory.local_cleanup_pending);
        {
            let mut state = runtime.lock();
            state.snapshot.helper = None;
            state.snapshot.native_deps = Some(crate::compositor::primitives::Report {
                state: "fail",
                socket_cleaned: true,
                owned_fds_closed: false,
                error: Some("CLEANUP_FAILED"),
                ..Default::default()
            });
        }
        let failed_fd = runtime.snapshot().reclamation.unwrap();
        assert_eq!(failed_fd.workers, 0);
        assert!(failed_fd.local_cleanup_pending);
    }
    #[test]
    fn diagnostic_sdk_role_cannot_hide_an_app_owner_from_an_old_scene() {
        use eidolon_model::surface::TargetKey;
        let runtime = Runtime::start(RuntimeConfig::default()).unwrap();
        wait(&runtime, |r| r.snapshot().state == RuntimeState::Ready);
        assert_eq!(completed(&runtime, scene(1)), Status::Accepted);
        let diagnostic = TargetKey::from_raw(1, 1, 1, 1).unwrap();
        let app = TargetKey::from_raw(1, 1, 1, 2).unwrap();
        assert_eq!(
            runtime.command(register_sdk(diagnostic, true)),
            Status::Accepted
        );
        assert_eq!(
            runtime.attach_surface(
                diagnostic,
                eidolon_render::PlatformWindow::default(),
                64,
                64
            ),
            Status::Accepted
        );
        wait(&runtime, |r| r.snapshot().render.targets.len() == 1);
        // session/window番号が同じでも、別世代の通常SDK ownerを診断扱いしない。
        assert_eq!(runtime.command(register_sdk(app, false)), Status::Accepted);
        assert_eq!(runtime.command(register_sdk(app, true)), Status::Stale);
        assert_eq!(
            runtime.command(register_sdk(diagnostic, false)),
            Status::Stale
        );
        assert_eq!(runtime.snapshot().app_sdk_bindings, 1);
        assert_eq!(
            completed(&runtime, connect_missing_profile(10)),
            Status::Busy
        );
        assert_eq!(runtime.snapshot().transport.credential_requests, 0);
        assert_eq!(completed(&runtime, scene(2)), Status::Accepted);
        let wrong_scene = TargetKey::from_raw(1, 1, 2, 2).unwrap();
        assert_eq!(runtime.command(release_sdk(wrong_scene)), Status::Stale);
        assert_eq!(runtime.command(register_sdk(app, true)), Status::Stale);
        assert_eq!(
            completed(&runtime, connect_missing_profile(11)),
            Status::Busy
        );
        assert_eq!(runtime.command(release_sdk(app)), Status::Accepted);
        assert_eq!(runtime.command(release_sdk(app)), Status::Stale);
        assert_eq!(runtime.snapshot().app_sdk_bindings, 0);
        assert_eq!(runtime.snapshot().shutdown.sdk_bindings, 1);
        // native surfaceの退役後もSDK ownerKeyは残るが、通常接続を妨げない。
        assert_eq!(
            completed(
                &runtime,
                Command::SurfaceLost {
                    schema: 1,
                    request_id: CommandSeq::new(12).unwrap(),
                    target: diagnostic,
                }
            ),
            Status::Accepted
        );
        wait(&runtime, |r| r.snapshot().render.targets.is_empty());
        assert_eq!(
            completed(&runtime, connect_missing_profile(13)),
            Status::NotReady
        );
        assert_eq!(runtime.snapshot().transport.error, Some("HOST_KEY_UNKNOWN"));
        runtime.request_stop();
        wait(&runtime, |r| r.snapshot().render.workers == 0);
        assert!(!runtime.can_destroy());
        let stopped = runtime.snapshot().shutdown;
        assert_eq!(stopped.sdk_bindings, 1);
        assert!(stopped.pending_owners.contains(&"sdk"));
        assert_eq!(runtime.command(release_sdk(diagnostic)), Status::Accepted);
        wait(&runtime, Runtime::can_destroy);
        assert!(runtime.snapshot().shutdown.complete);
        assert_eq!(runtime.snapshot().app_sdk_bindings, 0);
    }
    #[test]
    fn diagnostic_sdk_registration_cannot_begin_after_window_mode() {
        use eidolon_model::surface::TargetKey;
        let runtime = Runtime::start(RuntimeConfig::default()).unwrap();
        wait(&runtime, |r| r.snapshot().state == RuntimeState::Ready);
        assert_eq!(completed(&runtime, scene(1)), Status::Accepted);
        let first = TargetKey::from_raw(1, 1, 1, 1).unwrap();
        let late = TargetKey::from_raw(1, 1, 1, 2).unwrap();
        assert_eq!(runtime.command(register_sdk(first, true)), Status::Accepted);
        // Coreのapp snapshotがまだない間でも、rendererの一方向遷移を照合する。
        runtime.render.use_windows();
        assert!(runtime.snapshot().app.is_none());
        assert_eq!(runtime.command(register_sdk(late, true)), Status::NotReady);
        assert_eq!(runtime.command(register_sdk(first, true)), Status::Accepted);
        assert_eq!(runtime.command(register_sdk(late, false)), Status::Accepted);
        assert_eq!(runtime.snapshot().shutdown.sdk_bindings, 2);
        assert_eq!(runtime.snapshot().app_sdk_bindings, 1);
        assert_eq!(
            completed(&runtime, connect_missing_profile(10)),
            Status::Busy
        );
        runtime.request_stop();
        assert_eq!(runtime.command(release_sdk(first)), Status::Accepted);
        assert_eq!(runtime.command(release_sdk(first)), Status::Stale);
        assert_eq!(runtime.snapshot().app_sdk_bindings, 1);
        assert_eq!(runtime.command(release_sdk(late)), Status::Accepted);
        wait(&runtime, Runtime::can_destroy);
    }
    #[test]
    fn unretired_app_native_target_still_blocks_connect_without_an_sdk_owner() {
        use eidolon_model::surface::TargetKey;
        let runtime = Runtime::start(RuntimeConfig::default()).unwrap();
        wait(&runtime, |r| r.snapshot().state == RuntimeState::Ready);
        assert_eq!(completed(&runtime, scene(1)), Status::Accepted);
        let target = TargetKey::from_raw(1, 1, 1, 2).unwrap();
        runtime.render.use_windows();
        assert_eq!(
            runtime.render.windows(target.session, &[target.window]),
            Status::Accepted
        );
        let (image, probes) = eidolon_render::fixtures::make(0, 1, target, 64, 64).unwrap();
        assert_eq!(runtime.render.submit(image, probes), Status::Accepted);
        assert_eq!(
            runtime
                .render
                .attach_window(target, eidolon_render::PlatformWindow::default(), 64, 64),
            Status::Accepted
        );
        wait(&runtime, |r| {
            r.snapshot()
                .render
                .targets
                .iter()
                .any(|t| t.key == target && t.role == eidolon_render::RenderMode::Windows)
        });
        assert_eq!(runtime.snapshot().app_sdk_bindings, 0);
        assert_eq!(runtime.snapshot().shutdown.sdk_bindings, 0);
        assert_eq!(
            completed(&runtime, connect_missing_profile(10)),
            Status::Busy
        );
        assert_eq!(
            completed(
                &runtime,
                Command::SurfaceLost {
                    schema: 1,
                    request_id: CommandSeq::new(11).unwrap(),
                    target,
                }
            ),
            Status::Accepted
        );
        wait(&runtime, |r| r.snapshot().render.targets.is_empty());
        assert_eq!(
            completed(&runtime, connect_missing_profile(12)),
            Status::NotReady
        );
        runtime.request_stop();
        wait(&runtime, Runtime::can_destroy);
    }
    #[test]
    fn sdk_release_bypasses_full_queue_but_old_or_duplicate_ack_cannot_finish_stop() {
        use eidolon_model::surface::TargetKey;
        let (entered_tx, entered_rx) = std::sync::mpsc::channel();
        let (release_tx, release_rx) = std::sync::mpsc::channel();
        let runtime = Runtime::start_with_hook(RuntimeConfig::default(), move || {
            entered_tx.send(()).unwrap();
            release_rx.recv().unwrap();
        })
        .unwrap();
        entered_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        runtime.lock().snapshot.scene = Some(SceneEpoch::new(1).unwrap());
        let first = TargetKey::from_raw(1, 1, 1, 1).unwrap();
        let second = TargetKey::from_raw(1, 2, 1, 2).unwrap();
        let command = |target, owned| {
            if owned {
                Command::RegisterSdkBinding {
                    schema: 1,
                    request_id: CommandSeq::new(1).unwrap(),
                    target,
                    diagnostic: false,
                    create_command: None,
                    adapter: None,
                }
            } else {
                Command::ReleaseSdkBinding {
                    schema: 1,
                    request_id: CommandSeq::new(1).unwrap(),
                    target,
                }
            }
        };
        for target in [first, second, second] {
            assert_eq!(runtime.command(command(target, true)), Status::Accepted);
        }
        assert_eq!(runtime.snapshot().shutdown.sdk_bindings, 2);
        for _ in 0..limits::CORE_COMMANDS {
            assert_eq!(runtime.command(queued_work(1)), Status::Accepted);
        }
        assert_eq!(runtime.command(queued_work(1)), Status::Busy);
        assert_eq!(runtime.command(command(second, false)), Status::Accepted);
        assert_eq!(runtime.command(command(second, false)), Status::Stale);
        // 外部ownerが返らない場合を仮想時刻で検査し、実時間10秒は待たない。
        runtime
            .lock()
            .shutdown
            .start(Instant::now() - Duration::from_secs(11));
        runtime.request_stop();
        runtime.request_stop();
        assert_eq!(runtime.command(command(second, true)), Status::NotReady);
        let old = TargetKey::from_raw(1, 1, 2, 1).unwrap();
        assert_eq!(runtime.command(command(old, false)), Status::Stale);
        let snapshot = runtime.snapshot();
        assert_eq!(snapshot.shutdown.sdk_bindings, 1);
        assert!(snapshot.shutdown.timed_out && !snapshot.shutdown.complete);
        assert!(snapshot.shutdown.pending_owners.contains(&"sdk"));
        release_tx.send(()).unwrap();
        assert!(!runtime.can_destroy());
        assert_eq!(runtime.command(command(first, false)), Status::Accepted);
        wait(&runtime, Runtime::can_destroy);
        let stopped = runtime.snapshot().shutdown;
        assert!(stopped.complete && stopped.timed_out);
        assert!(stopped.pending_owners.is_empty());
    }
    #[test]
    fn full_key_queue_requests_urgent_reset_without_enqueuing_the_lost_key() {
        use eidolon_model::{
            ids::{SessionId, SurfaceGeneration, WindowId},
            keyboard::{KeyAction, KeyPacket},
            surface::TargetKey,
        };
        let (entered_tx, entered_rx) = std::sync::mpsc::channel();
        let (release_tx, release_rx) = std::sync::mpsc::channel();
        let runtime = Runtime::start_with_hook(RuntimeConfig::default(), move || {
            entered_tx.send(()).unwrap();
            release_rx.recv().unwrap();
        })
        .unwrap();
        entered_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        runtime.poll(64).unwrap();
        for id in 1..=limits::CORE_COMMANDS {
            assert_eq!(
                runtime.command(Command::SetActivityState {
                    schema: 1,
                    expected_scene: None,
                    request_id: CommandSeq::new(id as i64).unwrap(),
                    visible: true
                }),
                Status::Accepted
            );
        }
        let target = TargetKey {
            session: SessionId::new(1).unwrap(),
            window: WindowId::new(1).unwrap(),
            scene: SceneEpoch::new(1).unwrap(),
            generation: SurfaceGeneration::new(1).unwrap(),
        };
        assert_eq!(
            runtime.command(Command::Key {
                schema: 1,
                request_id: CommandSeq::new(999).unwrap(),
                target,
                input_epoch: 0,
                focus_token: CommandSeq::new(1).unwrap(),
                key: KeyPacket {
                    device: 7,
                    scan_code: 42,
                    key_code: 59,
                    action: KeyAction::Released,
                    repeat: 0,
                    meta: 0,
                    time_ms: 1
                }
            }),
            Status::Busy
        );
        assert!(runtime.reset.load(Ordering::Acquire));
        runtime.reset.store(false, Ordering::Release);
        assert_eq!(
            runtime.command(Command::Pointer {
                schema: 1,
                request_id: CommandSeq::new(1000).unwrap(),
                target,
                input_epoch: 0,
                pointer: eidolon_model::pointer::PointerPacket {
                    source: eidolon_model::ids::InputSourceId::new(1).unwrap(),
                    uv: None,
                    event: eidolon_model::pointer::PointerEvent::Buttons {
                        changed: 1,
                        state: 0
                    },
                    time_ms: 0,
                }
            }),
            Status::Busy
        );
        assert!(runtime.reset.load(Ordering::Acquire));
        assert_eq!(
            runtime.snapshot().commands_accepted,
            limits::CORE_COMMANDS as u64
        );
        runtime.request_stop();
        release_tx.send(()).unwrap();
        wait(&runtime, Runtime::can_destroy);
        assert_eq!(runtime.snapshot().commands_processed, 0);
        assert_eq!(
            runtime.snapshot().commands_cancelled,
            limits::CORE_COMMANDS as u64
        );
    }
    #[test]
    fn native_dependency_command_uses_owned_worker_and_stops_after_reclaim() {
        use std::os::unix::fs::DirBuilderExt;
        let parent = std::env::temp_dir().join(format!("eidolon-core-deps-{}", std::process::id()));
        std::fs::DirBuilder::new()
            .mode(0o700)
            .create(&parent)
            .unwrap();
        let runtime = Runtime::start(RuntimeConfig {
            paths: Some(eidolon_model::commands::RuntimePaths {
                files_dir: parent.to_str().unwrap().into(),
                native_library_dir: "/unused-native-dependency-fixture".into(),
            }),
            ..Default::default()
        })
        .unwrap();
        wait(&runtime, |r| r.snapshot().state == RuntimeState::Ready);
        assert_eq!(
            runtime.command(Command::NativeDepsProbe {
                schema: 1,
                request_id: CommandSeq::new(1).unwrap(),
                nonce: "cccccccccccccccc".into(),
            }),
            Status::Accepted
        );
        wait(&runtime, |r| {
            r.snapshot()
                .native_deps
                .as_ref()
                .is_some_and(|r| r.state == "pass")
        });
        assert_eq!(runtime.snapshot().dependency_workers, 0);
        assert!(runtime.snapshot().native_deps.unwrap().owned_fds_closed);
        let started = Instant::now();
        assert_eq!(runtime.request_stop(), Status::Accepted);
        assert!(started.elapsed() < Duration::from_millis(100));
        wait(&runtime, Runtime::can_destroy);
        assert_eq!(runtime.snapshot().state, RuntimeState::Stopped);
        assert_eq!(runtime.snapshot().dependency_workers, 0);
        // transport journalの保存先は停止後も残す。probe固有socket/directoryだけが消える。
        let entries = std::fs::read_dir(&parent)
            .unwrap()
            .map(|e| e.unwrap().file_name())
            .collect::<Vec<_>>();
        assert_eq!(entries, [std::ffi::OsString::from("transport-state")]);
        assert_eq!(
            std::fs::read_dir(parent.join("transport-state"))
                .unwrap()
                .count(),
            0
        );
        std::fs::remove_dir(parent.join("transport-state")).unwrap();
        std::fs::remove_dir(parent).unwrap();
    }
    #[test]
    fn missing_profile_never_requests_secret_and_stale_disconnect_preserves_new_request() {
        use eidolon_model::ids::SessionId;
        let runtime = Runtime::start(RuntimeConfig::default()).unwrap();
        wait(&runtime, |r| r.snapshot().state == RuntimeState::Ready);
        assert_eq!(
            runtime.command(Command::Connect {
                schema: 1,
                request_id: CommandSeq::new(1).unwrap(),
                profile_id: "termux-local".into()
            }),
            Status::Accepted
        );
        wait(&runtime, |r| r.snapshot().commands_processed == 1);
        assert_eq!(runtime.snapshot().transport.error, Some("HOST_KEY_UNKNOWN"));
        assert_eq!(runtime.snapshot().transport.credential_requests, 0);
        let session = SessionId::new(2).unwrap();
        let request = CommandSeq::new(10).unwrap();
        {
            let mut state = runtime.lock();
            state.credentials.begin(session, request);
            state
                .credentials
                .provide(
                    session,
                    request,
                    eidolon_transport::credentials::generate().unwrap(),
                )
                .unwrap();
        }
        runtime.command(Command::Disconnect {
            schema: 1,
            request_id: CommandSeq::new(2).unwrap(),
            session: SessionId::new(1).unwrap(),
        });
        wait(&runtime, |r| r.snapshot().commands_processed == 2);
        let retained = runtime.lock().credentials.take(session, request).is_ok();
        runtime.request_stop();
        wait(&runtime, Runtime::can_destroy);
        assert!(retained);
    }
    #[test]
    fn invisible_signal_bypasses_full_queue_and_old_visible_commands_cannot_resume_gl() {
        let entered = Arc::new(Barrier::new(2));
        let release = Arc::new(Barrier::new(2));
        let (a, b) = (entered.clone(), release.clone());
        let runtime = Runtime::start_with_hook(
            RuntimeConfig {
                schema: 1,
                paths: None,
                ..Default::default()
            },
            move || {
                a.wait();
                b.wait();
            },
        )
        .unwrap();
        entered.wait();
        runtime.render.set_visible(true);
        runtime.command(Command::SetXrState {
            schema: 1,
            expected_scene: None,
            request_id: CommandSeq::new(1).unwrap(),
            state: XrState::Focused,
        });
        runtime.command(Command::SetActivityState {
            schema: 1,
            expected_scene: None,
            request_id: CommandSeq::new(2).unwrap(),
            visible: true,
        });
        for id in 3..=limits::CORE_COMMANDS as i64 {
            assert_eq!(runtime.command(queued_work(id)), Status::Accepted);
        }
        let status = runtime.command(Command::SetActivityState {
            schema: 1,
            expected_scene: None,
            request_id: CommandSeq::new(257).unwrap(),
            visible: false,
        });
        let paused_at_busy = runtime.snapshot().render.paused;
        release.wait();
        wait(&runtime, |r| r.snapshot().commands_processed >= 2);
        let paused_after_old_commands = runtime.snapshot().render.paused;
        runtime.request_stop();
        wait(&runtime, Runtime::can_destroy);
        assert_eq!(status, Status::Busy);
        assert!(paused_at_busy && paused_after_old_commands);
    }
    #[test]
    fn bounded_queue_stop_bypasses_full_commands_without_waiting() {
        let entered = Arc::new(Barrier::new(2));
        let release = Arc::new(Barrier::new(2));
        let (a, b) = (entered.clone(), release.clone());
        let runtime = Runtime::start_with_hook(
            RuntimeConfig {
                schema: 1,
                paths: None,
                ..Default::default()
            },
            move || {
                a.wait();
                b.wait();
            },
        )
        .unwrap();
        entered.wait();
        for id in 1..=limits::CORE_COMMANDS {
            assert_eq!(runtime.command(queued_work(id as i64)), Status::Accepted);
        }
        assert_eq!(runtime.command(queued_work(999)), Status::Busy);
        assert!(!runtime.can_destroy());
        assert_eq!(runtime.request_stop(), Status::Accepted);
        assert_eq!(runtime.request_stop(), Status::Accepted);
        release.wait();
        wait(&runtime, Runtime::can_destroy);
        let snapshot = runtime.snapshot();
        assert_eq!(snapshot.commands_pending, 0);
        assert_eq!(snapshot.commands_cancelled, limits::CORE_COMMANDS as u64);
        assert_eq!(snapshot.commands_processed, 0);
        assert!(
            runtime
                .poll(64)
                .unwrap()
                .iter()
                .any(|e| matches!(e, Event::Stopped))
        );
    }
    #[test]
    fn scene_identity_is_applied_synchronously_and_old_scene_never_queues() {
        let runtime = Runtime::start(RuntimeConfig {
            schema: 1,
            paths: None,
            ..Default::default()
        })
        .unwrap();
        wait(&runtime, |r| r.snapshot().state == RuntimeState::Ready);
        assert_eq!(runtime.command(scene(2)), Status::Accepted);
        assert_eq!(runtime.command(scene(1)), Status::Stale);
        assert_eq!(runtime.snapshot().commands_processed, 1);
        let events = runtime.poll(64).unwrap();
        assert!(events.iter().any(|e| matches!(e, Event::CommandComplete { request_id, code: Status::Accepted } if request_id.get() == 2)));
        assert!(!events.iter().any(
            |e| matches!(e, Event::CommandComplete { request_id, .. } if request_id.get() == 1)
        ));
        assert_eq!(runtime.snapshot().scene.unwrap().get(), 2);
        assert_eq!(runtime.poll(0).unwrap_err(), Status::InvalidArgument);
        runtime.request_stop();
        wait(&runtime, Runtime::can_destroy);
    }
    #[test]
    fn old_scene_close_and_queued_focus_cannot_change_replacement_scene() {
        let (entered_tx, entered_rx) = std::sync::mpsc::channel();
        let (release_tx, release_rx) = std::sync::mpsc::channel();
        let runtime = Runtime::start_with_hook(RuntimeConfig::default(), move || {
            entered_tx.send(()).unwrap();
            release_rx.recv().unwrap();
        })
        .unwrap();
        entered_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        assert_eq!(runtime.command(scene(1)), Status::Accepted);
        let focus = |request, scene| Command::SetXrState {
            schema: 1,
            request_id: CommandSeq::new(request).unwrap(),
            state: XrState::Focused,
            expected_scene: Some(SceneEpoch::new(scene).unwrap()),
        };
        assert_eq!(runtime.command(focus(10, 1)), Status::Accepted);
        // The core owner has not consumed the old Focused callback yet.
        assert_eq!(runtime.command(scene(2)), Status::Accepted);
        assert_eq!(
            runtime.command(Command::SceneClosing {
                schema: 1,
                request_id: CommandSeq::new(11).unwrap(),
                scene: SceneEpoch::new(1).unwrap(),
            }),
            Status::Stale
        );
        assert_eq!(runtime.command(focus(12, 1)), Status::Stale);
        assert_eq!(runtime.snapshot().scene, Some(SceneEpoch::new(2).unwrap()));
        assert_eq!(runtime.snapshot().xr_state, XrState::Idle);
        release_tx.send(()).unwrap();
        wait(&runtime, |r| r.snapshot().commands_processed == 3);
        assert_eq!(runtime.snapshot().xr_state, XrState::Idle);
        assert!(runtime.snapshot().render.paused);
        assert!(runtime.poll(64).unwrap().iter().any(|event| matches!(event,
            Event::CommandComplete {request_id, code: Status::Stale} if request_id.get() == 10)));
        assert_eq!(completed(&runtime, focus(13, 2)), Status::Accepted);
        assert_eq!(
            runtime.command(Command::SceneClosing {
                schema: 1,
                request_id: CommandSeq::new(14).unwrap(),
                scene: SceneEpoch::new(2).unwrap(),
            }),
            Status::Accepted
        );
        assert!(runtime.snapshot().scene.is_none());
        assert_eq!(runtime.command(scene(2)), Status::Stale);
        assert_eq!(runtime.command(scene(3)), Status::Accepted);
        runtime.request_stop();
        wait(&runtime, Runtime::can_destroy);
    }

    #[test]
    fn sdk_current_requires_native_create_and_retirement_survives_full_queue() {
        use eidolon_model::{
            linux_desktop_v3::{Context, presentation::NativeOrigin},
            surface::TargetKey,
        };
        let (entered_tx, entered_rx) = std::sync::mpsc::channel();
        let (release_tx, release_rx) = std::sync::mpsc::channel();
        let runtime = Runtime::start_with_hook(RuntimeConfig::default(), move || {
            entered_tx.send(()).unwrap();
            release_rx.recv().unwrap();
        })
        .unwrap();
        entered_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../../scripts/tests/fixtures/linux-desktop-v3-contract.json"
        ))
        .unwrap();
        let raw = |id| {
            fixture["records"]
                .as_array()
                .unwrap()
                .iter()
                .find(|record| record["id"] == id)
                .unwrap()["raw"]
                .as_str()
                .unwrap()
                .as_bytes()
        };
        let origin = NativeOrigin::parse(raw("origin")).unwrap();
        let context = Context::parse(raw("context")).unwrap();
        assert_eq!(runtime.command(scene(1)), Status::Accepted);
        let target;
        let create;
        {
            let mut state = runtime.lock();
            state.v3_retirement =
                Some(crate::v3_retirement::Owner::new(origin.clone(), context, 7).unwrap());
            let window = eidolon_model::ids::WindowId::new(1).unwrap();
            state
                .panels
                .reconcile(
                    Some(SceneEpoch::new(1).unwrap()),
                    &[(origin.session(), window)],
                    true,
                )
                .unwrap();
            state.sync_v3_targets(&runtime.render).unwrap();
            let panel = state.panels.snapshot()[0].clone();
            target = panel.target;
            create = panel.command_seq;
            assert!(panel.v3_evidence_required);
        }
        let registration = |target, adapter| Command::RegisterSdkBinding {
            schema: 1,
            request_id: CommandSeq::new(100).unwrap(),
            target,
            diagnostic: false,
            create_command: Some(create),
            adapter: Some(adapter),
        };
        let evidence = |adapter, phase| Command::SdkBindingEvidence {
            schema: 1,
            request_id: CommandSeq::new(101).unwrap(),
            target,
            create_command: create,
            adapter,
            phase,
        };
        use eidolon_model::commands::SdkEvidencePhase::{Current, Retired, Retiring};
        let foreign =
            TargetKey::from_raw(target.session.get(), 2, 1, target.generation.get()).unwrap();
        assert_eq!(runtime.command(registration(foreign, 7)), Status::Stale);
        assert_eq!(runtime.command(evidence(7, Current)), Status::Stale);
        assert_eq!(runtime.command(registration(target, 7)), Status::Accepted);
        assert_eq!(runtime.command(registration(target, 8)), Status::Stale);
        assert_eq!(runtime.command(evidence(7, Current)), Status::Accepted);
        // Current SDK objects alone never enable input without ACK/content/frame.
        assert!(!runtime.lock().v3_input_target_current(
            target,
            &runtime.render.snapshot(),
            &runtime.transport.snapshot()
        ));
        {
            use eidolon_model::linux_desktop_v3::{
                presentation::PresentationState,
                report::{PresentationSummary, RemoteCleanup, TransportPhase, TransportState},
            };
            // Explicit fixture observations: positively satisfy each gate, then
            // revoke one current fact at a time without changing the old ACK.
            let mut state = runtime.lock();
            state.snapshot.activity_visible = true;
            state.snapshot.xr_state = XrState::Focused;
            state.render_visibility = [true; 2];
            state.snapshot.app = Some(crate::session::Report {
                session: Some(target.session),
                ready: true,
                state: "running",
                gui_windows: vec![target.window],
                ..Default::default()
            });
            state.snapshot.desktop_v3_presentation = Some(PresentationSummary {
                state: PresentationState::Ready,
                main: Some(target.window),
                first_adopted_ms: Some(1),
                startup_completed: true,
                first_failure: None,
                cleanup_pending: false,
                fallback_used: false,
            });
            let mut render = eidolon_render::Snapshot {
                mode: eidolon_render::RenderMode::Windows,
                targets: vec![eidolon_render::TargetReport {
                    key: target,
                    frames: 1,
                    presentation_paused: false,
                    player_clock_ms: None,
                    content_frame: Some(eidolon_model::ids::FrameSeq::new(1).unwrap()),
                    role: eidolon_render::RenderMode::Windows,
                    canvas: None,
                    content_size: None,
                    decoration_frames: 0,
                    io_draw: None,
                    frame_timing: Default::default(),
                    gpu: Default::default(),
                }],
                ..Default::default()
            };
            let mut transport = eidolon_transport::session::Snapshot {
                session: Some(target.session),
                desktop_v3_transport: Some(
                    eidolon_transport::linux_desktop::v3::runtime::TransportSnapshot {
                        protocol_revision: eidolon_model::linux_desktop_v3::REVISION.into(),
                        session: target.session,
                        inspect_request: CommandSeq::new(1).unwrap(),
                        launch_request: Some(origin.request()),
                        transport: TransportState {
                            phase: TransportPhase::Started,
                            launch_accepted: true,
                            journal_pending: true,
                        },
                        journal_stage: None,
                        start_attempted: true,
                        started_observed: true,
                        terminal_observed: false,
                        terminal_reason: None,
                        actor_finished: false,
                        io_retired: false,
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
                        cleanup_pending: false,
                        faults: Default::default(),
                        error: None,
                    },
                ),
                ..Default::default()
            };
            assert!(!state.v3_input_target_current(target, &render, &transport));
            assert_eq!(
                state.panels.acknowledge(
                    create,
                    target,
                    eidolon_model::commands::PanelAction::Create,
                    true
                ),
                Status::Accepted
            );
            assert!(state.v3_input_target_current(target, &render, &transport));
            transport
                .desktop_v3_transport
                .as_mut()
                .unwrap()
                .terminal_observed = true;
            assert!(!state.v3_input_target_current(target, &render, &transport));
            transport
                .desktop_v3_transport
                .as_mut()
                .unwrap()
                .terminal_observed = false;
            transport
                .desktop_v3_transport
                .as_mut()
                .unwrap()
                .launch_request = Some(CommandSeq::new(origin.request().get() + 1).unwrap());
            assert!(!state.v3_input_target_current(target, &render, &transport));
            transport
                .desktop_v3_transport
                .as_mut()
                .unwrap()
                .launch_request = Some(origin.request());
            render.targets[0].content_frame = None;
            assert!(!state.v3_input_target_current(target, &render, &transport));
            render.targets[0].content_frame = Some(eidolon_model::ids::FrameSeq::new(1).unwrap());
            render.targets[0].key.scene = SceneEpoch::new(2).unwrap();
            assert!(!state.v3_input_target_current(target, &render, &transport));
            state.snapshot.app = None;
        }
        for id in 1..=limits::CORE_COMMANDS {
            assert_eq!(runtime.command(queued_work(id as i64)), Status::Accepted);
        }
        assert_eq!(runtime.command(queued_work(999)), Status::Busy);
        assert_eq!(runtime.command(release_sdk(target)), Status::NotReady);
        assert_eq!(runtime.command(evidence(8, Retiring)), Status::Stale);
        assert_eq!(runtime.command(evidence(7, Retiring)), Status::Accepted);
        assert_eq!(runtime.command(evidence(7, Current)), Status::NotReady);
        wait(&runtime, |r| {
            r.command(evidence(7, Retired)) == Status::Accepted
        });
        assert_eq!(runtime.command(evidence(7, Retired)), Status::Accepted);
        {
            let mut state = runtime.lock();
            state.sync_v3_targets(&runtime.render).unwrap();
            assert!(state.sdk_bindings.is_empty());
            assert!(state.panels.snapshot().is_empty());
            assert_eq!(state.v3_retirement.as_ref().unwrap().pending_targets(), 0);
            // This ingress fixture never creates a Session/capture handoff.
            // Its manually installed owner is removed only in fixture teardown.
            state.v3_retirement = None;
        }
        runtime.request_stop();
        release_tx.send(()).unwrap();
        wait(&runtime, Runtime::can_destroy);
    }
    #[test]
    fn panic_fails_runtime_and_still_allows_cleanup() {
        let runtime = Runtime::start(RuntimeConfig {
            schema: 1,
            paths: None,
            ..Default::default()
        })
        .unwrap();
        wait(&runtime, |r| r.snapshot().state == RuntimeState::Ready);
        assert_eq!(
            runtime.guarded(|| panic!("synthetic fixture")),
            Err(Status::Failed)
        );
        assert_eq!(runtime.snapshot().state, RuntimeState::Failed);
        assert_eq!(runtime.command(scene(1)), Status::Failed);
        runtime.request_stop();
        wait(&runtime, Runtime::can_destroy);
        assert_eq!(runtime.snapshot().error, Some("JNI_FAILURE"));
    }
    #[test]
    fn late_panic_cannot_reopen_a_stopped_owner_or_prevent_destroy() {
        let runtime = Runtime::start(RuntimeConfig {
            schema: 1,
            paths: None,
            ..Default::default()
        })
        .unwrap();
        runtime.request_stop();
        wait(&runtime, Runtime::can_destroy);
        assert_eq!(
            runtime.guarded(|| panic!("late synthetic failure")),
            Err(Status::Failed)
        );
        assert_eq!(runtime.snapshot().state, RuntimeState::Stopped);
        assert!(runtime.can_destroy());
    }
    #[test]
    fn ui_queue_overflow_has_reserved_failure_and_stopped_events() {
        let runtime = Runtime::start(RuntimeConfig {
            schema: 1,
            paths: None,
            ..Default::default()
        })
        .unwrap();
        wait(&runtime, |r| r.snapshot().state == RuntimeState::Ready);
        for id in 1..=400 {
            let status = runtime.command(scene(id));
            if status == Status::Failed {
                break;
            }
            if status == Status::Busy {
                thread::yield_now();
            }
        }
        wait(&runtime, |r| r.snapshot().state == RuntimeState::Failed);
        assert_eq!(runtime.snapshot().error, Some("RESOURCE_LIMIT"));
        assert!(runtime.snapshot().events_pending <= limits::UI_COMMANDS + 1);
        runtime.request_stop();
        wait(&runtime, Runtime::can_destroy);
        let mut events = Vec::new();
        while runtime.snapshot().events_pending > 0 {
            events.extend(runtime.poll(64).unwrap());
        }
        assert!(matches!(events[0], Event::RuntimeReady));
        assert!(matches!(events[events.len() - 2], Event::Failure { .. }));
        assert!(matches!(events[events.len() - 1], Event::Stopped));
    }
}
