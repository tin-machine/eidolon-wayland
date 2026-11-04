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
    time::Duration,
};

struct Shared {
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
    fn fail(&mut self, code: &'static str) {
        // 回収済みownerに遅いJNI例外が来てもStoppedを巻き戻さない。
        if self.snapshot.state == RuntimeState::Stopped {
            return;
        }
        if self.snapshot.error.is_none() {
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
        let transport = Arc::new(eidolon_transport::session::Controller::start()?);
        let io = transport.clone();
        let render = Arc::new(eidolon_render::Controller::start()?);
        let gl = render.clone();
        let shared = Arc::new(Mutex::new(Shared {
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
                {
                    let mut state = state.lock().unwrap_or_else(|e| e.into_inner());
                    if state.snapshot.state == RuntimeState::Starting
                        && !stopping.load(Ordering::Acquire)
                    {
                        state.snapshot.state = RuntimeState::Ready;
                        state.emit(Event::RuntimeReady);
                    }
                }
                let result = catch_unwind(AssertUnwindSafe(|| {
                    ready_hook();
                    let mut detach_count = 0;
                    let mut next_session = Some(eidolon_model::ids::SessionId::new(1).unwrap());
                    let mut last_credential = None;
                    let mut last_transport = (None, eidolon_transport::session::State::Idle, None);
                    loop {
                        let render = gl.snapshot();
                        let ssh = io.snapshot();
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
                        if stopping.load(Ordering::Acquire) {
                            gl.request_stop();
                            io.request_stop();
                            if helper.is_none() && gl.is_finished() && io.is_finished() {
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
                        if resetting.swap(false, Ordering::AcqRel) {
                            state.snapshot.input_resets += 1;
                        }
                        if state.snapshot.state == RuntimeState::Failed {
                            io.request_stop();
                            drop(state);
                            thread::park_timeout(Duration::from_millis(5));
                            continue;
                        }
                        if let Some(command) = state.commands.pop_front() {
                            let mut asynchronous = false;
                            let code = match &command {
                                Command::ForwardProbe { session, nonce, .. } => {
                                    if let Some(paths) =
                                        config.paths.as_ref().filter(|_| cfg!(debug_assertions))
                                    {
                                        io.forward_probe(
                                            *session,
                                            std::path::Path::new(&paths.files_dir),
                                            nonce,
                                        )
                                    } else {
                                        Status::NotReady
                                    }
                                }
                                Command::Connect {
                                    profile_id,
                                    request_id,
                                    ..
                                } => {
                                    if let Some(profile) = profiles.get(profile_id) {
                                        if let Some(session) = next_session {
                                            let code =
                                                io.connect(profile.clone(), session, *request_id);
                                            if code == Status::Accepted {
                                                next_session = session.checked_next();
                                            }
                                            code
                                        } else {
                                            Status::Failed
                                        }
                                    } else {
                                        io.unknown_profile()
                                    }
                                }
                                Command::Disconnect { session, .. } => {
                                    let code = io.disconnect(*session);
                                    if code == Status::Accepted {
                                        state.credentials.clear();
                                        last_credential = None;
                                    }
                                    code
                                }
                                Command::SurfaceLost { target, .. } => gl.detach(*target),
                                Command::HelperProbe {
                                    nonce, request_id, ..
                                } => {
                                    if helper.is_some() {
                                        Status::Busy
                                    } else if let Some(paths) =
                                        config.paths.clone().filter(|_| cfg!(debug_assertions))
                                    {
                                        let (progress, stop, nonce) = (
                                            progress_state.clone(),
                                            stopping.clone(),
                                            nonce.clone(),
                                        );
                                        match thread::Builder::new()
                                            .name("eidolon-helper".into())
                                            .spawn(move || {
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
                            };
                            state.snapshot.commands_processed += 1;
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
                gl.request_stop();
                io.request_stop();
                while !io.is_finished() {
                    thread::park_timeout(Duration::from_millis(5));
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
                let mut state = state.lock().unwrap_or_else(|e| e.into_inner());
                state.snapshot.state = RuntimeState::Stopping;
                state.snapshot.xr_state = XrState::Idle;
                state.snapshot.activity_visible = false;
                state.snapshot.input_resets += 1;
                state.snapshot.commands_cancelled += state.commands.len() as u64;
                state.commands.clear();
                state.snapshot.state = RuntimeState::Stopped;
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
        self.stop.store(true, Ordering::Release);
        self.render.request_stop();
        self.transport.request_stop();
        self.lock().credentials.clear();
        self.worker.thread().unpark();
        Status::Accepted
    }
    pub fn snapshot(&self) -> Snapshot {
        let state = self.lock();
        Snapshot {
            transport: self.transport.snapshot(),
            render: self.render.snapshot(),
            commands_pending: state.commands.len(),
            events_pending: state.events.len() + state.terminal.len(),
            ..state.snapshot.clone()
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
    use eidolon_model::ids::{CommandSeq, SceneEpoch};
    use std::{sync::Barrier, time::Instant};
    fn wait(runtime: &Runtime, predicate: impl Fn(&Runtime) -> bool) {
        let deadline = Instant::now() + Duration::from_secs(2);
        while !predicate(runtime) {
            assert!(Instant::now() < deadline, "{:?}", runtime.snapshot());
            thread::sleep(Duration::from_millis(1));
        }
    }
    fn scene(id: i64) -> Command {
        Command::SceneReady {
            schema: 1,
            request_id: CommandSeq::new(id).unwrap(),
            scene: SceneEpoch::new(id).unwrap(),
        }
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
