//! EGL所有thread。共有lockの保持中にはplatform/GL関数を呼ばない。
#[cfg(target_os = "android")]
mod android;
#[cfg(not(target_os = "android"))]
mod host;
#[cfg(target_os = "android")]
use android as platform;
use eidolon_model::{
    limits,
    state::Status,
    surface::{TargetKey, validate_dimensions},
};
#[cfg(not(target_os = "android"))]
use host as platform;
pub use platform::PlatformWindow;
use serde::Serialize;
use std::{
    collections::VecDeque,
    sync::{Arc, Condvar, Mutex},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

const QUEUE: usize = 32;
const ACKS: usize = 64;
const STALL: Duration = Duration::from_secs(2);
const FRAME: Duration = Duration::from_nanos(16_666_667);

#[cfg(any(target_os = "android", test))]
fn check_gl_error(error: u32) -> Result<(), &'static str> {
    match error {
        0 => Ok(()),
        0x0507 => Err("EGL_CONTEXT_LOST"),
        _ => Err("SURFACE_LOST"),
    }
}

#[derive(Clone, Default, Debug, Serialize)]
pub struct GpuInfo {
    pub config_id: i32,
    pub rgba_bits: [i32; 4],
    pub width: i32,
    pub height: i32,
    pub vendor: String,
    pub renderer: String,
    pub version: String,
    pub readback: bool,
    pub max_swap_us: u64,
}
#[derive(Clone, Debug, Serialize)]
pub struct TargetReport {
    pub key: TargetKey,
    pub frames: u64,
    #[serde(flatten)]
    pub gpu: GpuInfo,
}
#[derive(Clone, Default, Debug, Serialize)]
pub struct Snapshot {
    pub workers: usize,
    pub targets: Vec<TargetReport>,
    pub detached: VecDeque<TargetKey>,
    pub queued: usize,
    pub paused: bool,
    pub attaches: u64,
    pub detaches: u64,
    pub duplicates: u64,
    pub stale: u64,
    pub context_rebuilds: u64,
    pub error: Option<&'static str>,
}
enum Message {
    Attach {
        key: TargetKey,
        window: PlatformWindow,
        width: i32,
        height: i32,
    },
    Detach(TargetKey),
}
struct Shared {
    queue: VecDeque<Message>,
    snapshot: Snapshot,
    stop: bool,
    visible: bool,
    busy_since: Option<Instant>,
}
type State = Arc<(Mutex<Shared>, Condvar)>;
pub struct Controller {
    state: State,
    worker: JoinHandle<()>,
}

/// 世代の履歴はwindow数で制限する。古いdetachは新世代を選択できない。
#[derive(Default)]
struct Generations {
    latest: Vec<TargetKey>,
}
impl Generations {
    fn attach(&mut self, key: TargetKey) -> Status {
        if let Some(previous) = self.latest.iter_mut().find(|p| p.same_window(key)) {
            if *previous == key {
                return Status::Stale;
            }
            if !key.newer_than(*previous) {
                return Status::Stale;
            }
            *previous = key;
        } else {
            if self.latest.len() == limits::TOPLEVELS {
                return Status::Busy;
            }
            self.latest.push(key);
        }
        Status::Accepted
    }
}
struct Active {
    report: TargetReport,
    gpu: platform::Target,
}

impl Controller {
    pub fn start() -> std::io::Result<Self> {
        let state = Arc::new((
            Mutex::new(Shared {
                queue: VecDeque::new(),
                snapshot: Snapshot {
                    workers: 1,
                    paused: true,
                    ..Default::default()
                },
                stop: false,
                visible: false,
                busy_since: None,
            }),
            Condvar::new(),
        ));
        let owned = state.clone();
        let worker = thread::Builder::new()
            .name("eidolon-gl".into())
            .spawn(move || {
                let result =
                    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| owner(&owned)));
                let mut state = owned.0.lock().unwrap_or_else(|e| e.into_inner());
                if result.is_err() {
                    state.snapshot.error = Some("JNI_FAILURE");
                }
                state.queue.clear(); // queued ANativeWindowの追加参照もここで回収。
                state.snapshot.targets.clear();
                state.snapshot.workers = 0;
                state.busy_since = None;
            })?;
        Ok(Self { state, worker })
    }
    fn enqueue(&self, message: Message) -> Status {
        let mut state = self.state.0.lock().unwrap_or_else(|e| e.into_inner());
        if state.stop {
            return Status::NotReady;
        }
        if state.snapshot.workers == 0 || state.snapshot.error == Some("JNI_FAILURE") {
            return Status::Failed;
        }
        if state.snapshot.error == Some("RENDER_STALLED") {
            return Status::Failed;
        }
        if state.queue.len() == QUEUE {
            return Status::Busy;
        }
        state.queue.push_back(message);
        self.state.1.notify_one();
        Status::Accepted
    }
    pub fn attach(
        &self,
        key: TargetKey,
        window: PlatformWindow,
        width: i32,
        height: i32,
    ) -> Status {
        if let Err(status) = validate_dimensions(width, height) {
            return status;
        }
        self.enqueue(Message::Attach {
            key,
            window,
            width,
            height,
        })
    }
    pub fn detach(&self, key: TargetKey) -> Status {
        self.enqueue(Message::Detach(key))
    }
    pub fn set_visible(&self, visible: bool) {
        self.state
            .0
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .visible = visible;
        self.state.1.notify_one();
    }
    pub fn request_stop(&self) {
        self.state.0.lock().unwrap_or_else(|e| e.into_inner()).stop = true;
        self.state.1.notify_one();
    }
    pub fn snapshot(&self) -> Snapshot {
        let mut state = self.state.0.lock().unwrap_or_else(|e| e.into_inner());
        if state
            .busy_since
            .is_some_and(|since| since.elapsed() > STALL)
        {
            state.snapshot.error = Some("RENDER_STALLED");
            state.stop = true;
        }
        let mut result = state.snapshot.clone();
        result.queued = state.queue.len();
        result.paused = !state.visible;
        result
    }
    pub fn is_finished(&self) -> bool {
        self.worker.is_finished()
    }
}
impl Drop for Controller {
    fn drop(&mut self) {
        self.request_stop();
    }
}

fn update(state: &State, action: impl FnOnce(&mut Shared)) {
    action(&mut state.0.lock().unwrap_or_else(|e| e.into_inner()));
}
fn ack(state: &mut Shared, key: TargetKey) {
    if state.snapshot.detached.back() == Some(&key) {
        return;
    }
    if state.snapshot.detached.len() == ACKS {
        state.snapshot.detached.pop_front();
    }
    state.snapshot.detached.push_back(key);
    state.snapshot.detaches += 1;
}
fn busy<T>(state: &State, action: impl FnOnce() -> T) -> T {
    update(state, |s| s.busy_since = Some(Instant::now()));
    let result = action();
    update(state, |s| s.busy_since = None);
    result
}
fn owner(state: &State) {
    let mut active: Vec<Active> = Vec::new();
    let mut generations = Generations::default();
    let mut next = Instant::now();
    loop {
        let (message, visible, stop) = {
            let mut shared = state.0.lock().unwrap_or_else(|e| e.into_inner());
            if !shared.stop
                && shared.queue.is_empty()
                && (!shared.visible || active.is_empty() || Instant::now() < next)
            {
                let timeout = if shared.visible && !active.is_empty() {
                    next.saturating_duration_since(Instant::now())
                } else {
                    Duration::from_millis(100)
                };
                shared = state
                    .1
                    .wait_timeout(shared, timeout)
                    .unwrap_or_else(|e| e.into_inner())
                    .0;
            }
            (shared.queue.pop_front(), shared.visible, shared.stop)
        };
        if stop {
            break;
        }
        if let Some(message) = message {
            match message {
                Message::Attach {
                    key,
                    window,
                    width,
                    height,
                } => {
                    if active.iter().any(|a| a.report.key == key) {
                        update(state, |s| s.snapshot.duplicates += 1);
                        continue;
                    }
                    if active.len() >= limits::VISIBLE_PANELS
                        || generations.attach(key) != Status::Accepted
                    {
                        update(state, |s| {
                            s.snapshot.stale += 1;
                            ack(s, key);
                        });
                        continue;
                    }
                    match busy(state, || platform::Target::new(window, width, height)) {
                        Ok(gpu) => {
                            active.push(Active {
                                report: TargetReport {
                                    key,
                                    frames: 0,
                                    gpu: gpu.info(),
                                },
                                gpu,
                            });
                            update(state, |s| {
                                s.snapshot.attaches += 1;
                                if s.snapshot.error != Some("RENDER_STALLED") {
                                    s.snapshot.error = None;
                                }
                            });
                        }
                        Err(error) => update(state, |s| {
                            s.snapshot.error = Some(error);
                            ack(s, key);
                        }),
                    }
                }
                Message::Detach(key) => {
                    if let Some(index) = active.iter().position(|a| a.report.key == key) {
                        busy(state, || drop(active.remove(index)));
                    } else {
                        update(state, |s| s.snapshot.stale += 1);
                    }
                    // full tupleのみack。遅い旧世代detachは現targetへ触れない。
                    update(state, |s| ack(s, key));
                }
            }
        }
        if visible && Instant::now() >= next {
            let mut lost = Vec::new();
            for (index, target) in active.iter_mut().enumerate() {
                match busy(state, || target.gpu.frame(target.report.frames)) {
                    Ok(()) => {
                        target.report.frames += 1;
                        target.report.gpu = target.gpu.info();
                    }
                    Err("EGL_CONTEXT_LOST") => {
                        // 全contextを同じowner上で無効化・再構築する。
                        lost.push((index, true));
                        break;
                    }
                    Err(_) => lost.push((index, false)),
                }
            }
            if lost.iter().any(|(_, context)| *context) {
                lost.clear();
                for (index, target) in active.iter_mut().enumerate() {
                    update(state, |s| s.snapshot.context_rebuilds += 1);
                    if busy(state, || target.gpu.rebuild()).is_err() {
                        lost.push((index, false));
                    }
                }
            }
            for (index, _) in lost.into_iter().rev() {
                let key = active[index].report.key;
                busy(state, || drop(active.remove(index)));
                update(state, |s| {
                    s.snapshot.error = Some("SURFACE_LOST");
                    ack(s, key);
                });
            }
            next = Instant::now() + FRAME;
        }
        update(state, |s| {
            s.snapshot.targets = active.iter().map(|a| a.report.clone()).collect()
        });
    }
    // 別threadはEGLを破棄しない。停止完了はこのdropの後。
    busy(state, || drop(active));
}

#[cfg(test)]
mod tests {
    use super::*;
    fn wait(test: impl Fn() -> bool) {
        let deadline = Instant::now() + Duration::from_secs(3);
        while !test() {
            assert!(Instant::now() < deadline);
            thread::sleep(Duration::from_millis(2));
        }
    }
    #[test]
    fn twenty_cycles_late_detach_duplicates_and_pause() {
        let renderer = Controller::start().unwrap();
        renderer.set_visible(true);
        for generation in 1..=20 {
            let key = TargetKey::from_raw(1, 1, 1, generation).unwrap();
            assert_eq!(
                renderer.attach(key, PlatformWindow::default(), 1024, 768),
                Status::Accepted
            );
            wait(|| {
                renderer
                    .snapshot()
                    .targets
                    .first()
                    .is_some_and(|t| t.key == key && t.frames > 0)
            });
            assert_eq!(
                renderer.attach(key, PlatformWindow::default(), 1024, 768),
                Status::Accepted
            );
            if generation > 1 {
                renderer.detach(TargetKey::from_raw(1, 1, 1, generation - 1).unwrap());
                wait(|| renderer.snapshot().stale >= generation as u64 - 1);
                assert_eq!(renderer.snapshot().targets[0].key, key);
            }
            renderer.detach(key);
            wait(|| {
                renderer.snapshot().detached.contains(&key)
                    && renderer.snapshot().targets.is_empty()
            });
        }
        assert_eq!(renderer.snapshot().attaches, 20);
        assert_eq!(renderer.snapshot().duplicates, 20);
        let key = TargetKey::from_raw(1, 1, 1, 21).unwrap();
        renderer.set_visible(false);
        renderer.attach(key, PlatformWindow::default(), 1024, 768);
        wait(|| renderer.snapshot().targets.len() == 1);
        thread::sleep(Duration::from_millis(45));
        assert_eq!(renderer.snapshot().targets[0].frames, 0);
        renderer.request_stop();
        wait(|| renderer.is_finished());
        assert_eq!(renderer.snapshot().workers, 0);
    }
    #[test]
    fn stalled_gpu_keeps_snapshot_and_stop_responsive_and_cannot_finish_early() {
        use std::sync::atomic::Ordering;
        let control = Arc::new(platform::TestControl::default());
        control.blocked.store(true, Ordering::Release);
        let renderer = Controller::start().unwrap();
        renderer.set_visible(true);
        renderer.attach(
            TargetKey::from_raw(1, 1, 1, 1).unwrap(),
            PlatformWindow {
                control: Some(control.clone()),
            },
            1024,
            768,
        );
        wait(|| control.entered.load(Ordering::Acquire));
        // driverがdeadlineを越えた状態を注入。別threadからGPU所有物を壊さない。
        update(&renderer.state, |s| {
            s.busy_since = Some(Instant::now() - STALL - Duration::from_millis(1))
        });
        let started = Instant::now();
        assert_eq!(renderer.snapshot().error, Some("RENDER_STALLED"));
        renderer.request_stop();
        assert!(started.elapsed() < Duration::from_millis(100));
        assert!(!renderer.is_finished());
        control.blocked.store(false, Ordering::Release);
        wait(|| renderer.is_finished());
        assert_eq!(renderer.snapshot().workers, 0);
    }
    #[test]
    fn context_loss_rebuilds_but_surface_loss_acknowledges_only_its_target() {
        assert_eq!(check_gl_error(0x0507), Err("EGL_CONTEXT_LOST"));
        assert_eq!(check_gl_error(0x0502), Err("SURFACE_LOST"));
        assert_eq!(check_gl_error(0), Ok(()));
        use std::sync::atomic::Ordering;
        let control = Arc::new(platform::TestControl::default());
        control.fault.store(2, Ordering::Release);
        let renderer = Controller::start().unwrap();
        renderer.set_visible(true);
        let key = TargetKey::from_raw(1, 1, 1, 1).unwrap();
        renderer.attach(
            key,
            PlatformWindow {
                control: Some(control.clone()),
            },
            1024,
            768,
        );
        wait(|| {
            renderer.snapshot().context_rebuilds == 1
                && renderer
                    .snapshot()
                    .targets
                    .first()
                    .is_some_and(|t| t.frames > 0)
        });
        control.fault.store(1, Ordering::Release);
        wait(|| {
            renderer.snapshot().targets.is_empty() && renderer.snapshot().detached.contains(&key)
        });
        assert_eq!(renderer.snapshot().error, Some("SURFACE_LOST"));
        renderer.request_stop();
        wait(|| renderer.is_finished());
    }
    #[test]
    fn retired_tuple_never_attaches_again_and_budget_is_bounded() {
        let mut generations = Generations::default();
        let a = TargetKey::from_raw(1, 1, 1, 2).unwrap();
        assert_eq!(generations.attach(a), Status::Accepted);
        assert_eq!(generations.attach(a), Status::Stale);
        assert_eq!(
            generations.attach(TargetKey::from_raw(1, 1, 1, 1).unwrap()),
            Status::Stale
        );
        for window in 2..=limits::TOPLEVELS {
            assert_eq!(
                generations.attach(TargetKey::from_raw(1, window as i64, 1, 1).unwrap()),
                Status::Accepted
            );
        }
        assert_eq!(
            generations.attach(TargetKey::from_raw(1, 100, 1, 1).unwrap()),
            Status::Busy
        );
    }
}
