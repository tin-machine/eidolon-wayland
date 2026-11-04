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
}
impl Runtime {
    pub fn start(config: RuntimeConfig) -> std::io::Result<Self> {
        Self::start_with_hook(config, || {})
    }

    fn start_with_hook(
        _config: RuntimeConfig,
        ready_hook: impl FnOnce() + Send + 'static,
    ) -> std::io::Result<Self> {
        let shared = Arc::new(Mutex::new(Shared {
            snapshot: Snapshot::default(),
            commands: VecDeque::new(),
            events: VecDeque::new(),
            terminal: VecDeque::new(),
        }));
        let stop = Arc::new(AtomicBool::new(false));
        let reset = Arc::new(AtomicBool::new(false));
        let (state, stopping, resetting) = (shared.clone(), stop.clone(), reset.clone());
        let worker = thread::Builder::new()
            .name("eidolon-core".into())
            .spawn(move || {
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
                    while !stopping.load(Ordering::Acquire) {
                        let mut state = state.lock().unwrap_or_else(|e| e.into_inner());
                        if resetting.swap(false, Ordering::AcqRel) {
                            state.snapshot.input_resets += 1;
                        }
                        if state.snapshot.state == RuntimeState::Failed {
                            drop(state);
                            thread::park_timeout(Duration::from_millis(5));
                            continue;
                        }
                        if let Some(command) = state.commands.pop_front() {
                            let code = match &command {
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
                            state.emit(Event::CommandComplete {
                                request_id: command.request_id(),
                                code,
                            });
                        } else {
                            drop(state);
                            thread::park_timeout(Duration::from_millis(5));
                        }
                    }
                }));
                if result.is_err() {
                    state
                        .lock()
                        .unwrap_or_else(|e| e.into_inner())
                        .fail("JNI_FAILURE");
                    // Failedは再利用しない。明示停止までは状態を診断可能にする。
                    while !stopping.load(Ordering::Acquire) {
                        thread::park_timeout(Duration::from_millis(5));
                    }
                }
                let mut state = state.lock().unwrap_or_else(|e| e.into_inner());
                state.snapshot.state = RuntimeState::Stopping;
                state.snapshot.xr_state = XrState::Idle;
                state.snapshot.activity_visible = false;
                state.snapshot.input_resets += 1;
                state.snapshot.commands_cancelled += state.commands.len() as u64;
                state.commands.clear();
                state.snapshot.state = RuntimeState::Stopped;
                state.terminal.push_back(Event::Stopped);
            })?;
        Ok(Self {
            shared,
            stop,
            reset,
            worker,
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
        if state.snapshot.state == RuntimeState::Failed {
            return Status::Failed;
        }
        if state.snapshot.state != RuntimeState::Ready || self.stop.load(Ordering::Acquire) {
            return Status::NotReady;
        }
        if state.commands.len() == limits::CORE_COMMANDS {
            return Status::Busy;
        }
        state.commands.push_back(command);
        state.snapshot.commands_accepted += 1;
        self.worker.thread().unpark();
        Status::Accepted
    }
    pub fn request_stop(&self) -> Status {
        self.stop.store(true, Ordering::Release);
        self.worker.thread().unpark();
        Status::Accepted
    }
    pub fn snapshot(&self) -> Snapshot {
        let state = self.lock();
        Snapshot {
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
    fn bounded_queue_stop_bypasses_full_commands_without_waiting() {
        let entered = Arc::new(Barrier::new(2));
        let release = Arc::new(Barrier::new(2));
        let (a, b) = (entered.clone(), release.clone());
        let runtime = Runtime::start_with_hook(RuntimeConfig { schema: 1 }, move || {
            a.wait();
            b.wait();
        })
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
        let runtime = Runtime::start(RuntimeConfig { schema: 1 }).unwrap();
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
        let runtime = Runtime::start(RuntimeConfig { schema: 1 }).unwrap();
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
        let runtime = Runtime::start(RuntimeConfig { schema: 1 }).unwrap();
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
        let runtime = Runtime::start(RuntimeConfig { schema: 1 }).unwrap();
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
