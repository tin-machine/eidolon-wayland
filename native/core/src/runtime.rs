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
    panels: crate::panels::Controller,
    workspace: crate::workspace::Controller,
    credentials: eidolon_transport::credentials::CredentialSlot,
    // 最新のSDK可視性。遅れて処理する通常commandで描画pauseを巻き戻さない。
    render_visibility: [bool; 2],
    snapshot: Snapshot,
    commands: VecDeque<Command>,
    events: VecDeque<Event>,
    // 通常eventが満杯でもfailure/stoppedを失わない、最大2件の予約枠。
    terminal: VecDeque<Event>,
}
impl Shared {
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
            self.credentials.clear();
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

/// protocol/model所有threadの土台。UIからのcallは短いlockとenqueueだけ。
pub struct Runtime {
    shared: Arc<Mutex<Shared>>,
    stop: Arc<AtomicBool>,
    reset: Arc<AtomicBool>,
    worker: JoinHandle<()>,
    render: Arc<eidolon_render::Controller>,
    transport: Arc<eidolon_transport::session::Controller>,
}
impl Runtime {
    pub fn start(config: RuntimeConfig) -> std::io::Result<Self> {
        Self::start_with_hook(config, || {})
    }

    fn start_with_hook(
        config: RuntimeConfig,
        ready_hook: impl FnOnce() + Send + 'static,
    ) -> std::io::Result<Self> {
        let invalid = || std::io::Error::new(std::io::ErrorKind::InvalidInput, "CONFIG_INVALID");
        config.validate().map_err(|_| invalid())?;
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
            panels: Default::default(),
            workspace: crate::workspace::Controller::load(None),
            credentials: Default::default(),
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
                // server ACKだけで枠の予約を解かない。新windowがmapするまで追加を直列化する。
                let mut opening_window: Option<(
                    eidolon_model::ids::SessionId,
                    eidolon_model::ids::CommandSeq,
                    Vec<eidolon_model::ids::WindowId>,
                )> = None;
                let mut pending_app: Option<(
                    eidolon_model::ids::SessionId,
                    String,
                    eidolon_model::commands::AppMode,
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
                            && let Some((session, nonce, mode, virtual_clock)) = pending_app.take()
                        {
                            let started = (|| {
                                if ssh.session != Some(session)
                                    || ssh.state != eidolon_transport::session::State::Ready
                                {
                                    return Err("STALE");
                                }
                                if let Some(current) = app.as_mut()
                                    && current.session() == session
                                {
                                    if current.virtual_clock() != virtual_clock {
                                        return Err("NOT_READY");
                                    }
                                    return current.select_app(&nonce, mode);
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
                                )?);
                                if !virtual_clock {
                                    gl.use_windows();
                                }
                                Ok(())
                            })();
                            if let Err(error) = started {
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
                        if let Some(current) = app.as_mut() {
                            let close = stopping.load(Ordering::Acquire)
                                || ssh.session != Some(current.session())
                                || matches!(
                                    ssh.state,
                                    eidolon_transport::session::State::Failed
                                        | eidolon_transport::session::State::Disconnected
                                        | eidolon_transport::session::State::Stopped
                                )
                                || current.snapshot().error.is_some();
                            let remote_gone = io.is_finished()
                                || (ssh.owned_connections == 0 && ssh.forward.active == 0);
                            if let Some(forward) = ssh.app_forward.as_ref() {
                                current.forwarding(forward);
                            }
                            current.tick(close, remote_gone);
                            if let Some((session, request, before)) = opening_window.as_ref() {
                                let now = current.snapshot();
                                let failed = ssh
                                    .app_forward
                                    .as_ref()
                                    .and_then(|r| r.remote.as_ref())
                                    .and_then(|r| r.last_terminal.as_ref())
                                    .is_some_and(|r| r.request_id == Some(*request) && !r.success);
                                if close
                                    || current.session() != *session
                                    || failed
                                    || now.windows.iter().any(|w| {
                                        !before.contains(&w.window)
                                            && w.life == eidolon_model::window::WindowLife::Mapped
                                    })
                                {
                                    opening_window = None;
                                }
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
                            current.render_tick(&gl, clock.elapsed());
                            let report = current.snapshot();
                            if !close && report.ready && !report.forward_sent {
                                let code = io.start_app(
                                    current.session(),
                                    &current.socket().unwrap(),
                                    current.nonce(),
                                    current.mode(),
                                );
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
                            state.lock().unwrap_or_else(|e| e.into_inner()).snapshot.app =
                                Some(snapshot);
                        }
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
                                    shared.credentials.begin(session, request);
                                    shared.emit(Event::CredentialRequired {
                                        session,
                                        request_id: request,
                                        profile_id: "termux-local".into(),
                                    });
                                    last_credential = Some((session, request));
                                }
                                if let Ok(key) = shared.credentials.take(session, request) {
                                    io.provide(session, request, key);
                                }
                            } else if last_credential.take().is_some() {
                                shared.credentials.clear();
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
                                && state
                                    .lock()
                                    .unwrap_or_else(|e| e.into_inner())
                                    .sdk_bindings
                                    .is_empty()
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
                                app.windows.iter().filter_map(|w| {
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
                        if let Err(code) = state
                            .panels
                            .reconcile_layouts(scene, &mapped, may_create, &layouts)
                        {
                            state.fail(code);
                        }
                        state.snapshot.panels = state.panels.snapshot();
                        state.snapshot.workspace = state.workspace.snapshot();
                        if resetting.swap(false, Ordering::AcqRel) {
                            state.ime_ui = None;
                            state.snapshot.input_resets += 1;
                            if let Some(current) = app.as_mut() {
                                current.reset_input();
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
                            let code = match &command {
                                Command::SetImeBackend {
                                    session, enabled, ..
                                } => {
                                    if *enabled && !cfg!(debug_assertions) {
                                        Status::NotReady
                                    } else if let Some(current) = app
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
                                    } else if !state.render_visibility.iter().all(|v| *v)
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
                                    } else {
                                        let target = match &command {
                                            Command::LaunchTerminal { target, .. } => {
                                                Some(target.clone())
                                            }
                                            _ => None,
                                        };
                                        let code =
                                            io.launch_terminal(*session, *request_id, target);
                                        if code == Status::Accepted {
                                            opening_window = Some((
                                                *session,
                                                *request_id,
                                                app.as_ref()
                                                    .unwrap()
                                                    .snapshot()
                                                    .windows
                                                    .iter()
                                                    .map(|w| w.window)
                                                    .collect(),
                                            ));
                                        }
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
                                                state.panels.show(*session, *window, *slot)
                                            }
                                            Command::CloseWindow { .. } => current
                                                .close_window(*window)
                                                .map(|_| Status::Accepted)
                                                .unwrap_or(Status::Stale),
                                            _ => unreachable!(),
                                        }
                                    }
                                }

                                Command::SetPose { target, .. }
                                | Command::PoseApplied { target, .. }
                                | Command::ResizeLogical { target, .. }
                                | Command::SetPhysicalSize { target, .. }
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
                                    if !live {
                                        Status::Stale
                                    } else if let Some(current) = app
                                        .as_mut()
                                        .filter(|a| a.session() == target.session && !a.closed())
                                    {
                                        let outcome = match &command {
                                            Command::SetPose {
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
                                            Command::ResizeLogical { width, height, .. } => current
                                                .resize_logical(
                                                    target.window,
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
                                    if *input_epoch != state.snapshot.input_resets
                                        || state.snapshot.scene != Some(target.scene)
                                        || !live
                                    {
                                        Status::Stale
                                    } else if !state.render_visibility.iter().all(|v| *v)
                                        || !state.snapshot.activity_visible
                                        || state.snapshot.xr_state != XrState::Focused
                                    {
                                        Status::NotReady
                                    } else if let Some(current) = current {
                                        let result = match &command {
                                            Command::FocusWindow { .. } => {
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
                                    let ready = match action {
                                        eidolon_model::commands::PanelAction::Create => {
                                            targets.iter().any(|t| t.key == *target && t.frames > 0)
                                        }
                                        eidolon_model::commands::PanelAction::Destroy => {
                                            targets.iter().all(|t| t.key != *target)
                                        }
                                    };
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
                                    if (virtual_clock && !cfg!(debug_assertions))
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
                                            Some((*session, nonce.clone(), *mode, virtual_clock));
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
                                Command::Connect {
                                    profile_id,
                                    request_id,
                                    ..
                                } => {
                                    if app_busy {
                                        Status::Busy
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
                                        state.credentials.clear();
                                        last_credential = None;
                                        resetting.store(true, Ordering::Release);
                                    }
                                    code
                                }
                                Command::Disconnect { session, .. } => {
                                    let code = io.disconnect(*session);
                                    if code == Status::Accepted {
                                        state.credentials.clear();
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
                                Command::SetXrState { state: xr, .. } => {
                                    state.snapshot.xr_state = *xr;
                                    Status::Accepted
                                }
                                Command::SetActivityState { visible, .. } => {
                                    state.snapshot.activity_visible = *visible;
                                    Status::Accepted
                                }
                                Command::ResetInput { .. } => Status::Accepted,
                                Command::RegisterSdkBinding { .. }
                                | Command::ReleaseSdkBinding { .. } => unreachable!(
                                    "SDK ownership is handled before the command queue"
                                ),
                            };
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
                while !io.is_finished() {
                    thread::park_timeout(Duration::from_millis(5));
                }
                if let Some(current) = app.as_mut() {
                    while !current.closed() {
                        current.tick(true, true);
                        state.lock().unwrap_or_else(|e| e.into_inner()).snapshot.app =
                            Some(current.snapshot());
                        thread::park_timeout(Duration::from_millis(5));
                    }
                }
                while !gl.is_finished() {
                    // snapshot/Stopはこの待機に参加しない。未回収ownerのruntimeは再利用しない。
                    if gl.snapshot().error == Some("RENDER_STALLED") {
                        state
                            .lock()
                            .unwrap_or_else(|e| e.into_inner())
                            .fail("RENDER_STALLED");
                    }
                    thread::park_timeout(Duration::from_millis(5));
                }
                // SDKはUI thread上でnative detachを観測してEntity/listenerを回収する。
                // 遅いackやscene終了中でも、実所有物が残る限りStoppedを出さない。
                while !state
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .sdk_bindings
                    .is_empty()
                {
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
                state.snapshot.panels.clear();
                state.credentials.clear();
                state.terminal.push_back(Event::Stopped);
            })?;
        Ok(Self {
            shared,
            stop,
            reset,
            worker,
            render,
            transport,
        })
    }

    fn lock(&self) -> MutexGuard<'_, Shared> {
        self.shared.lock().unwrap_or_else(|e| e.into_inner())
    }
    pub fn command(&self, command: Command) -> Status {
        // BUSYでもfocus喪失・解除の安全効果は通常queueと別に伝える。
        if command.needs_input_reset() {
            self.reset.store(true, Ordering::Release);
            self.worker.thread().unpark();
        }
        let mut state = self.lock();
        // 所有権の記帳はqueueの満杯や停止中にも左右されない。releaseは全tupleを照合し、
        // 古いscene/重複ackで別Entityの所有記録を消さない。通常の操作commandは従来どおりqueueへ。
        if let Command::RegisterSdkBinding { target, .. }
        | Command::ReleaseSdkBinding { target, .. } = &command
        {
            let code = if matches!(command, Command::ReleaseSdkBinding { .. }) {
                if let Some(index) = state.sdk_bindings.iter().position(|key| key == target) {
                    state.sdk_bindings.remove(index);
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
            } else if state.sdk_bindings.contains(target) {
                Status::Accepted
            } else if state.sdk_bindings.len() == 8 {
                Status::Busy
            } else {
                state.sdk_bindings.push(*target);
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
                state.render_visibility[0] = false;
                self.render.set_visible(false);
            }
            Command::SetXrState {
                state: XrState::Idle,
                ..
            } => {
                state.render_visibility[1] = false;
                self.render.set_visible(false);
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
            state.credentials.clear();
            state.ime_ui = None;
        }
        self.render.request_stop();
        self.transport.request_stop();
        self.worker.thread().unpark();
        Status::Accepted
    }
    pub fn snapshot(&self) -> Snapshot {
        let state = self.lock();
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
        Snapshot {
            shutdown: state
                .shutdown
                .report(Instant::now(), pending, state.sdk_bindings.len()),
            transport: self.transport.snapshot(),
            render: self.render.snapshot(),
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
                && app.windows.iter().any(|window| {
                    window.window == key.window
                        && window.life == eidolon_model::window::WindowLife::Mapped
                })
        });
        if !valid || !state.panels.accepts(key) {
            return Status::Stale;
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
        match state.credentials.provide(session, request, private) {
            Ok(()) => {
                self.worker.thread().unpark();
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
    fn scene(id: i64) -> Command {
        Command::SceneReady {
            schema: 1,
            request_id: CommandSeq::new(id).unwrap(),
            scene: SceneEpoch::new(id).unwrap(),
        }
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
            assert_eq!(runtime.command(scene(1)), Status::Accepted);
        }
        assert_eq!(runtime.command(scene(1)), Status::Busy);
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
            request_id: CommandSeq::new(1).unwrap(),
            state: XrState::Focused,
        });
        runtime.command(Command::SetActivityState {
            schema: 1,
            request_id: CommandSeq::new(2).unwrap(),
            visible: true,
        });
        for id in 3..=limits::CORE_COMMANDS as i64 {
            assert_eq!(runtime.command(scene(id)), Status::Accepted);
        }
        let status = runtime.command(Command::SetActivityState {
            schema: 1,
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
            assert_eq!(runtime.command(scene(id as i64)), Status::Accepted);
        }
        assert_eq!(runtime.command(scene(999)), Status::Busy);
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
    fn command_completion_and_old_scene_are_distinct_from_enqueue() {
        let runtime = Runtime::start(RuntimeConfig {
            schema: 1,
            paths: None,
            ..Default::default()
        })
        .unwrap();
        wait(&runtime, |r| r.snapshot().state == RuntimeState::Ready);
        assert_eq!(runtime.command(scene(2)), Status::Accepted);
        assert_eq!(runtime.command(scene(1)), Status::Accepted);
        wait(&runtime, |r| r.snapshot().commands_processed == 2);
        let events = runtime.poll(64).unwrap();
        assert!(events.iter().any(|e| matches!(e, Event::CommandComplete { request_id, code: Status::Stale } if request_id.get() == 1)));
        assert_eq!(runtime.snapshot().scene.unwrap().get(), 2);
        assert_eq!(runtime.poll(0).unwrap_err(), Status::InvalidArgument);
        runtime.request_stop();
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
