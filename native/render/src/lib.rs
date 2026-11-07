//! EGL所有thread。共有lockの保持中にはplatform/GL関数を呼ばない。
#[cfg(target_os = "android")]
mod android;
pub mod compositor;
pub mod decoration;
#[cfg(target_os = "android")]
pub mod egl;
mod executor;
#[cfg(any(test, feature = "debug-fixtures"))]
pub mod fixtures;
#[cfg(not(target_os = "android"))]
mod host;
pub mod theme_state;
#[cfg(target_os = "android")]
use android as platform;
use eidolon_model::{
    ids::{FrameSeq, SessionId, WindowId},
    limits,
    state::Status,
    surface::{TargetKey, validate_dimensions},
    window::WindowSnapshot,
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

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RenderMode {
    #[default]
    Diagnostic,
    Windows,
}

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
/// 成功したdraw+swapのwall time。GPU timerではなく、待ち時間を含む。累積差分で区間を測る。
#[derive(Clone, Debug, Default, Serialize)]
pub struct FrameTiming {
    pub samples: u64,
    pub total_us: u64,
    pub max_us: u64,
    // 0..=1000us, 1001..=2000us, ... 31001us以上のoverflow bin。
    pub histogram_ms: [u64; 32],
}
impl FrameTiming {
    fn record(&mut self, duration: Duration) {
        let us = duration.as_micros().min(u64::MAX as u128) as u64;
        self.samples = self.samples.saturating_add(1);
        self.total_us = self.total_us.saturating_add(us);
        self.max_us = self.max_us.max(us);
        let bin = us.saturating_sub(1).checked_div(1000).unwrap_or(0).min(31) as usize;
        self.histogram_ms[bin] = self.histogram_ms[bin].saturating_add(1);
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct TargetReport {
    pub key: TargetKey,
    pub frames: u64,
    pub content_frame: Option<FrameSeq>,
    pub role: RenderMode,
    pub canvas: Option<theme_state::Canvas>,
    pub content_size: Option<eidolon_model::window::LogicalSize>,
    pub decoration_frames: u64,
    pub frame_timing: FrameTiming,
    #[serde(flatten)]
    pub gpu: GpuInfo,
}
#[derive(Clone, Default, Debug, Serialize)]
pub struct Snapshot {
    pub mode: RenderMode,
    /// runtimeのGL所有job数。process共用threadはexecutorへ別記する。
    pub workers: usize,
    pub owner_tid: i32,
    pub executor: Option<executor::Report>,
    pub targets: Vec<TargetReport>,
    pub detached: VecDeque<TargetKey>,
    pub queued: usize,
    pub paused: bool,
    pub attaches: u64,
    pub detaches: u64,
    pub duplicates: u64,
    pub stale: u64,
    pub context_rebuilds: u64,
    pub contexts: usize,
    pub latest_images: usize,
    pub composition: compositor::Report,
    pub theme_revision: u64,
    pub theme_attempted_revision: u64,
    pub theme_discarded: u64,
    pub theme_id: Option<String>,
    pub theme_error: Option<&'static str>,
    pub error: Option<&'static str>,
}
enum Message {
    Attach {
        key: TargetKey,
        window: PlatformWindow,
        width: i32,
        height: i32,
        role: RenderMode,
    },
    Detach(TargetKey),
    Forget(SessionId, WindowId),
}
struct Shared {
    queue: VecDeque<Message>,
    snapshot: Snapshot,
    stop: bool,
    visible: bool,
    busy_since: Option<Instant>,
    latest: Vec<Arc<Submission>>,
    mode: RenderMode,
    logical: Vec<(SessionId, WindowId)>,
    theme: Option<Arc<eidolon_theme::raster::CompiledTheme>>,
    theme_revision: u64,
    reduced_motion: bool,
    accepted_theme: Option<Arc<eidolon_theme::raster::CompiledTheme>>,
    accepted_reduced: bool,
    #[cfg(test)]
    theme_hook: Option<Arc<platform::TestControl>>,
    semantic: Vec<((SessionId, WindowId), theme_state::SemanticState)>,
}
struct Submission {
    snapshot: Arc<WindowSnapshot>,
    probes: Vec<compositor::Probe>,
}
type State = Arc<(Mutex<Shared>, Condvar)>;
pub struct Controller {
    state: State,
    worker: Worker,
    pixels: eidolon_model::pixels::PixelBudget,
}

enum Worker {
    Dedicated(JoinHandle<()>),
    Reusable {
        executor: Arc<executor::Executor>,
        finished: Arc<std::sync::atomic::AtomicBool>,
    },
}

fn thread_id() -> i32 {
    #[cfg(any(target_os = "linux", target_os = "android"))]
    // SAFETY: gettidは引数/所有権を持たない現在threadの識別だけ。
    unsafe {
        libc::gettid()
    }
    #[cfg(not(any(target_os = "linux", target_os = "android")))]
    {
        0
    }
}

/// 世代の履歴はwindow数で制限する。古いdetachは新世代を選択できない。
#[derive(Default)]
struct Generations {
    latest: Vec<TargetKey>,
    diagnostic_floor: Vec<TargetKey>,
}
impl Generations {
    fn attach(&mut self, key: TargetKey) -> Status {
        if self
            .diagnostic_floor
            .iter()
            .any(|floor| floor.same_window(key) && !key.newer_than(*floor))
        {
            return Status::Stale;
        }
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
    needs_redraw: bool,
}

impl Controller {
    pub fn start() -> std::io::Result<Self> {
        Self::start_with_budget(Default::default())
    }
    pub fn pixel_budget(&self) -> eidolon_model::pixels::PixelBudget {
        self.pixels.clone()
    }
    pub fn start_with_budget(pixels: eidolon_model::pixels::PixelBudget) -> std::io::Result<Self> {
        #[cfg(target_os = "android")]
        let executor = Some(executor::Executor::global()?);
        #[cfg(not(target_os = "android"))]
        let executor = None;
        Self::start_on(pixels, executor)
    }
    fn start_on(
        pixels: eidolon_model::pixels::PixelBudget,
        executor: Option<Arc<executor::Executor>>,
    ) -> std::io::Result<Self> {
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
                latest: vec![],
                mode: RenderMode::Diagnostic,
                logical: vec![],
                theme: None,
                theme_revision: 0,
                reduced_motion: false,
                accepted_theme: None,
                accepted_reduced: false,
                #[cfg(test)]
                theme_hook: None,
                semantic: vec![],
            }),
            Condvar::new(),
        ));
        let worker = if let Some(executor) = executor {
            let finished = executor.submit(state.clone())?;
            Worker::Reusable { executor, finished }
        } else {
            let owned = state.clone();
            Worker::Dedicated(thread::Builder::new().name("eidolon-gl".into()).spawn(
                move || {
                    run_owner(&owned);
                },
            )?)
        };
        Ok(Self {
            state,
            worker,
            pixels,
        })
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
        self.attach_role(key, window, width, height, RenderMode::Diagnostic)
    }
    pub fn attach_window(
        &self,
        key: TargetKey,
        window: PlatformWindow,
        width: i32,
        height: i32,
    ) -> Status {
        self.attach_role(key, window, width, height, RenderMode::Windows)
    }
    fn attach_role(
        &self,
        key: TargetKey,
        window: PlatformWindow,
        width: i32,
        height: i32,
        role: RenderMode,
    ) -> Status {
        if let Err(status) = validate_dimensions(width, height) {
            return status;
        }
        {
            let state = self.state.0.lock().unwrap_or_else(|e| e.into_inner());
            if state.mode != role {
                return Status::NotReady;
            }
            if state
                .snapshot
                .targets
                .iter()
                .any(|target| target.key == key && target.role != role)
            {
                return Status::Stale;
            }
            if role == RenderMode::Windows
                && !state.latest.iter().any(|image| {
                    image.snapshot.session == key.session && image.snapshot.window == key.window
                })
            {
                return Status::Stale;
            }
        }
        self.enqueue(Message::Attach {
            key,
            window,
            width,
            height,
            role,
        })
    }
    /// 製品window modeへ一方向に移る。診断へ戻すには新runtimeを作る。
    pub fn use_windows(&self) {
        let mut state = self.state.0.lock().unwrap_or_else(|e| e.into_inner());
        if state.mode == RenderMode::Windows {
            return;
        }
        state.mode = RenderMode::Windows;
        state.snapshot.mode = RenderMode::Windows;
        let old = std::mem::take(&mut state.latest);
        state.snapshot.latest_images = 0;
        drop(state);
        drop(old);
        self.state.1.notify_one();
    }
    /// unmap中もlogical windowは登録を保ち、古いSurface世代を再利用させない。
    pub fn windows(&self, session: SessionId, windows: &[WindowId]) -> Status {
        if windows.len() > limits::TOPLEVELS
            || windows
                .iter()
                .enumerate()
                .any(|(index, id)| windows[..index].contains(id))
        {
            return Status::InvalidArgument;
        }
        let mut state = self.state.0.lock().unwrap_or_else(|e| e.into_inner());
        if state.logical.iter().filter(|(s, _)| *s != session).count() + windows.len()
            > limits::TOPLEVELS
        {
            return Status::Busy;
        }
        state.logical.retain(|(s, _)| *s != session);
        state.logical.extend(windows.iter().map(|w| (session, *w)));
        self.state.1.notify_one();
        Status::Accepted
    }
    pub fn detach(&self, key: TargetKey) -> Status {
        self.enqueue(Message::Detach(key))
    }
    /// 同じwindowの古い画像をcoalesceし、frameを通常command queueへためない。
    pub fn submit(&self, snapshot: Arc<WindowSnapshot>, probes: Vec<compositor::Probe>) -> Status {
        if snapshot.layers.len() > limits::SURFACES || probes.len() > 1024 {
            return Status::InvalidArgument;
        }
        let mut state = self.state.0.lock().unwrap_or_else(|e| e.into_inner());
        if state.stop {
            return Status::NotReady;
        }
        let index = state.latest.iter().position(|old| {
            old.snapshot.session == snapshot.session && old.snapshot.window == snapshot.window
        });
        if let Some(index) = index {
            if state.latest[index].snapshot.frame_seq >= snapshot.frame_seq {
                return Status::Stale;
            }
        } else if state.latest.len() >= limits::TOPLEVELS {
            return Status::Busy;
        }
        let image = Arc::new(Submission { snapshot, probes });
        let old = if let Some(index) = index {
            Some(std::mem::replace(&mut state.latest[index], image))
        } else {
            state.latest.push(image);
            None
        };
        state.snapshot.latest_images = state.latest.len();
        drop(state);
        drop(old);
        self.state.1.notify_one();
        Status::Accepted
    }
    pub fn forget_window(&self, session: SessionId, window: WindowId) -> Status {
        let mut state = self.state.0.lock().unwrap_or_else(|e| e.into_inner());
        let old = state
            .latest
            .iter()
            .position(|image| image.snapshot.session == session && image.snapshot.window == window)
            .map(|index| state.latest.swap_remove(index));
        state.snapshot.latest_images = state.latest.len();
        let code = if state.stop {
            Status::Accepted
        } else if state.queue.len() == QUEUE {
            Status::Busy
        } else {
            state.queue.push_back(Message::Forget(session, window));
            Status::Accepted
        };
        drop(state);
        drop(old);
        self.state.1.notify_one();
        code
    }
    /// CPU loaderの最新結果だけを渡す。upload成功後にownerがadoptする。
    /// themeはruntimeの共通PixelBudgetでcompileし、Surfaceサイズ変更はSDK adapterが同期する。
    pub fn set_theme(
        &self,
        theme: Option<Arc<eidolon_theme::raster::CompiledTheme>>,
        reduced_motion: bool,
    ) -> Status {
        self.request_theme(theme, reduced_motion)
            .map_or_else(|status| status, |_| Status::Accepted)
    }
    pub fn request_theme(
        &self,
        theme: Option<Arc<eidolon_theme::raster::CompiledTheme>>,
        reduced_motion: bool,
    ) -> Result<u64, Status> {
        let mut state = self.state.0.lock().unwrap_or_else(|e| e.into_inner());
        if state.stop {
            return Err(Status::NotReady);
        }
        let Some(next) = state.theme_revision.checked_add(1) else {
            return Err(Status::Failed);
        };
        state.theme = theme;
        state.theme_revision = next;
        state.reduced_motion = reduced_motion;
        self.state.1.notify_one();
        Ok(next)
    }
    /// 新しいCPU読み込みの開始時にも、転送中の旧候補を失効させる。
    pub fn cancel_pending_theme(&self) -> Result<Snapshot, Status> {
        let mut state = self.state.0.lock().unwrap_or_else(|e| e.into_inner());
        if state.stop {
            return Err(Status::NotReady);
        }
        state.theme_revision = state.theme_revision.checked_add(1).ok_or(Status::Failed)?;
        state.theme = state.accepted_theme.clone();
        state.reduced_motion = state.accepted_reduced;
        self.state.1.notify_one();
        Ok(state.snapshot.clone())
    }
    pub fn set_semantic_state(
        &self,
        session: SessionId,
        window: WindowId,
        semantic: theme_state::SemanticState,
    ) -> Status {
        if !semantic.valid() {
            return Status::InvalidArgument;
        }
        let mut state = self.state.0.lock().unwrap_or_else(|e| e.into_inner());
        if state.stop {
            return Status::NotReady;
        }
        if !state.logical.contains(&(session, window)) {
            return Status::Stale;
        }
        if let Some((_, value)) = state
            .semantic
            .iter_mut()
            .find(|(key, _)| *key == (session, window))
        {
            if *value == semantic {
                return Status::Accepted;
            }
            *value = semantic;
        } else if state.semantic.len() < limits::TOPLEVELS {
            state.semantic.push(((session, window), semantic));
        } else {
            return Status::Busy;
        }
        self.state.1.notify_one();
        Status::Accepted
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
    pub fn is_stopping(&self) -> bool {
        self.state.0.lock().unwrap_or_else(|e| e.into_inner()).stop
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
        if let Worker::Reusable { executor, .. } = &self.worker {
            result.executor = Some(executor.snapshot());
        }
        result
    }
    pub fn is_finished(&self) -> bool {
        match &self.worker {
            Worker::Dedicated(worker) => worker.is_finished(),
            Worker::Reusable { finished, .. } => {
                finished.load(std::sync::atomic::Ordering::Acquire)
            }
        }
    }
}

fn run_owner(owned: &State) -> bool {
    update(owned, |state| state.snapshot.owner_tid = thread_id());
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| owner(owned)));
    let mut state = owned.0.lock().unwrap_or_else(|e| e.into_inner());
    if result.is_err() {
        state.snapshot.error = Some("JNI_FAILURE");
    }
    state.queue.clear(); // queued ANativeWindowの追加参照もここで回収。
    state.snapshot.targets.clear();
    state.latest.clear();
    state.logical.clear();
    state.theme = None;
    state.accepted_theme = None;
    state.semantic.clear();
    state.snapshot.latest_images = 0;
    state.snapshot.contexts = 0;
    state.snapshot.composition = Default::default();
    state.snapshot.workers = 0;
    state.busy_since = None;
    result.is_ok() && state.snapshot.error != Some("RENDER_STALLED")
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
    let mut device: Option<platform::Device> = None;
    let mut generations = Generations::default();
    let mut next = Instant::now();
    let mut owner_mode = RenderMode::Diagnostic;
    let mut was_visible = false;
    let clock = Instant::now();
    let mut players = theme_state::Players::default();
    let mut theme: Option<Arc<eidolon_theme::raster::CompiledTheme>> = None;
    let mut theme_revision = 0;
    let mut reduced_motion = false;
    loop {
        let (
            message,
            visible,
            stop,
            mode,
            logical,
            requested_theme,
            requested_revision,
            requested_reduced,
            semantic,
        ) = {
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
            (
                shared.queue.pop_front(),
                shared.visible,
                shared.stop,
                shared.mode,
                shared.logical.clone(),
                shared.theme.clone(),
                shared.theme_revision,
                shared.reduced_motion,
                shared.semantic.clone(),
            )
        };
        if stop {
            break;
        }
        if visible && !was_visible {
            for target in &mut active {
                target.needs_redraw = true;
            }
        }
        was_visible = visible;
        if owner_mode != mode {
            if let Some(device) = device.as_mut() {
                let _ = busy(state, || device.clear());
            }
            generations.diagnostic_floor = std::mem::take(&mut generations.latest);
            owner_mode = mode;
        }
        if mode == RenderMode::Windows {
            generations
                .latest
                .retain(|key| logical.contains(&(key.session, key.window)));
            update(state, |s| {
                s.semantic.retain(|(key, _)| logical.contains(key))
            });
        }
        if let Some(message) = message {
            match message {
                Message::Attach {
                    key,
                    window,
                    width,
                    height,
                    role,
                } => {
                    if let Some(prior) = active.iter().find(|a| a.report.key == key) {
                        update(state, |s| {
                            if prior.report.role == role && role == mode {
                                s.snapshot.duplicates += 1;
                            } else {
                                s.snapshot.stale += 1;
                            }
                        });
                        // 別roleで拒否しても、生きた同tupleのdetach ackを捏造しない。
                        continue;
                    }
                    if role != mode
                        || (role == RenderMode::Windows
                            && !logical.contains(&(key.session, key.window)))
                    {
                        update(state, |s| {
                            s.snapshot.stale += 1;
                            ack(s, key)
                        });
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
                    match busy(state, || {
                        if device.is_none() {
                            device = Some(platform::Device::new()?);
                        }
                        platform::Target::new(device.as_mut().unwrap(), window, width, height)
                    }) {
                        Ok(gpu) => {
                            active.push(Active {
                                report: TargetReport {
                                    key,
                                    frames: 0,
                                    content_frame: None,
                                    role,
                                    canvas: None,
                                    content_size: None,
                                    decoration_frames: 0,
                                    frame_timing: FrameTiming::default(),
                                    gpu: gpu.info(),
                                },
                                gpu,
                                needs_redraw: true,
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
                        if let Some(device) = device.as_mut()
                            && let Err(error) =
                                busy(state, || device.forget(key.session, key.window))
                        {
                            update(state, |s| s.snapshot.error = Some(error));
                        }
                        busy(state, || drop(active.remove(index)));
                    } else {
                        update(state, |s| s.snapshot.stale += 1);
                    }
                    // full tupleのみack。遅い旧世代detachは現targetへ触れない。
                    update(state, |s| ack(s, key));
                }
                Message::Forget(session, window) => {
                    if let Some(device) = device.as_mut()
                        && let Err(error) = busy(state, || device.forget(session, window))
                    {
                        update(state, |s| s.snapshot.error = Some(error));
                    }
                }
            }
        }
        players.retain(&logical);
        let mut theme_context_lost = false;
        if requested_revision != theme_revision {
            let mut result = busy(state, || {
                if requested_theme.is_some() && device.is_none() {
                    device = Some(platform::Device::new()?);
                }
                device
                    .as_mut()
                    .map_or(Ok(()), |d| d.stage_theme(requested_theme.as_deref()))
            });
            #[cfg(test)]
            {
                let hook = state.0.lock().unwrap().theme_hook.clone();
                if let Some(hook) = hook {
                    use std::sync::atomic::Ordering;
                    hook.entered.store(true, Ordering::Release);
                    while hook.blocked.load(Ordering::Acquire) {
                        thread::sleep(Duration::from_millis(1));
                    }
                }
            }
            // 採用の線形化点。GL呼び出しを短い共有lockの外で行う。
            let current = {
                let mut shared = state.0.lock().unwrap_or_else(|e| e.into_inner());
                let current = !shared.stop && shared.theme_revision == requested_revision;
                if current && result.is_ok() {
                    shared.accepted_theme = requested_theme.clone();
                    shared.accepted_reduced = requested_reduced;
                    shared.snapshot.theme_revision = requested_revision;
                    shared.snapshot.theme_attempted_revision = requested_revision;
                    shared.snapshot.theme_id =
                        requested_theme.as_ref().map(|t| t.manifest.id.clone());
                    shared.snapshot.theme_error = None;
                }
                current
            };
            if !current {
                update(state, |s| s.snapshot.theme_discarded += 1);
            }
            if let Some(device) = device.as_mut() {
                let finish = busy(state, || device.finish_theme(current && result.is_ok()));
                if finish.is_err() {
                    result = finish;
                }
            }
            // この要求の失敗では旧themeを維持する。再要求またはcontext再生成時だけ再試行。
            if result == Err("EGL_CONTEXT_LOST") {
                for target in &mut active {
                    target.needs_redraw = true;
                }
                theme_context_lost = true;
            } else if current {
                theme_revision = requested_revision;
                if result.is_ok() {
                    theme = requested_theme;
                    reduced_motion = requested_reduced;
                    if theme.is_none() {
                        players.clear();
                    }
                    for target in &mut active {
                        target.needs_redraw = true;
                    }
                }
                update(state, |s| {
                    s.snapshot.theme_attempted_revision = requested_revision;
                    s.snapshot.theme_error = result.err();
                    if result.is_ok() {
                        s.snapshot.theme_revision = theme_revision;
                        s.snapshot.theme_id = theme.as_ref().map(|t| t.manifest.id.clone());
                    }
                });
            }
        }
        let visible_windows: Vec<_> = active
            .iter()
            .filter(|a| {
                visible
                    && a.report.role == RenderMode::Windows
                    && logical.contains(&(a.report.key.session, a.report.key.window))
            })
            .map(|a| (a.report.key.session, a.report.key.window))
            .collect();
        if let Err(error) = players.visibility(clock.elapsed(), &visible_windows) {
            update(state, |s| s.snapshot.theme_error = Some(error));
        }
        if (visible && Instant::now() >= next) || theme_context_lost {
            let mut lost = if theme_context_lost {
                vec![(0, "EGL_CONTEXT_LOST")]
            } else {
                Vec::new()
            };
            for (index, target) in active.iter_mut().enumerate() {
                if theme_context_lost {
                    break;
                }
                if target.report.role != mode
                    || (mode == RenderMode::Windows
                        && !logical
                            .contains(&(target.report.key.session, target.report.key.window)))
                {
                    continue;
                }
                let image = state
                    .0
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .latest
                    .iter()
                    .find(|image| {
                        image.snapshot.session == target.report.key.session
                            && image.snapshot.window == target.report.key.window
                    })
                    .cloned();
                if mode == RenderMode::Windows && image.is_none() {
                    continue;
                }
                let drawing = if let (Some(theme), Some(image)) = (&theme, &image) {
                    let key = (target.report.key.session, target.report.key.window);
                    let semantic = semantic
                        .iter()
                        .find(|(k, _)| *k == key)
                        .map(|(_, s)| s.clone())
                        .unwrap_or_default();
                    match players.draw(
                        key,
                        theme,
                        clock.elapsed(),
                        image.snapshot.logical_size,
                        reduced_motion,
                        &semantic,
                    ) {
                        Ok(drawing) => Some(drawing),
                        Err(error) => {
                            update(state, |s| s.snapshot.theme_error = Some(error));
                            None
                        }
                    }
                } else {
                    None
                };
                // 装飾の時計はapp由来FrameSeqを増やさず、最終frame/停止も独立して反映する。
                if mode == RenderMode::Windows
                    && !target.needs_redraw
                    && drawing
                        .as_ref()
                        .is_none_or(|d| !d.frame.active && !d.frame.changed)
                    && target.report.content_frame == image.as_ref().map(|i| i.snapshot.frame_seq)
                {
                    continue;
                }
                let decoration = drawing
                    .as_ref()
                    .zip(theme.as_ref())
                    .map(|(d, t)| d.composition(t));
                let frame_started = Instant::now();
                match busy(state, || {
                    target.gpu.frame(
                        device.as_mut().unwrap(),
                        target.report.frames,
                        image.as_ref().map(|image| image.snapshot.as_ref()),
                        image
                            .as_ref()
                            .map(|image| image.probes.as_slice())
                            .unwrap_or(&[]),
                        decoration.as_ref(),
                    )
                }) {
                    Ok(()) => {
                        target.report.frame_timing.record(frame_started.elapsed());
                        target.needs_redraw = false;
                        target.report.canvas = drawing.as_ref().map(|d| d.canvas.clone());
                        target.report.content_size =
                            image.as_ref().map(|i| i.snapshot.logical_size);
                        target.report.decoration_frames += u64::from(drawing.is_some());
                        target.report.frames += 1;
                        target.report.content_frame =
                            image.as_ref().map(|image| image.snapshot.frame_seq);
                        target.report.gpu = target.gpu.info();
                    }
                    Err("EGL_CONTEXT_LOST") => {
                        // 全contextを同じowner上で無効化・再構築する。
                        lost.push((index, "EGL_CONTEXT_LOST"));
                        break;
                    }
                    Err(error) => lost.push((index, error)),
                }
            }
            if lost.iter().any(|(_, error)| *error == "EGL_CONTEXT_LOST") {
                lost.clear();
                busy(state, || {
                    if let Some(device) = device.as_mut() {
                        let _ = device.clear();
                    }
                    for target in &mut active {
                        target.gpu.invalidate();
                    }
                    drop(device.take());
                    device = platform::Device::new().ok();
                });
                for (index, target) in active.iter_mut().enumerate() {
                    target.needs_redraw = true;
                    update(state, |s| s.snapshot.context_rebuilds += 1);
                    if device
                        .as_mut()
                        .is_none_or(|device| busy(state, || target.gpu.rebuild(device)).is_err())
                    {
                        lost.push((index, "SURFACE_LOST"));
                    }
                }
            }
            for (index, error) in lost.into_iter().rev() {
                let key = active[index].report.key;
                if let Some(device) = device.as_mut() {
                    let _ = busy(state, || device.forget(key.session, key.window));
                }
                busy(state, || drop(active.remove(index)));
                update(state, |s| {
                    s.snapshot.error = Some(error);
                    ack(s, key);
                });
            }
            next = Instant::now() + FRAME;
        }
        update(state, |s| {
            s.snapshot.targets = active.iter().map(|a| a.report.clone()).collect();
            s.snapshot.contexts = usize::from(device.is_some());
            s.snapshot.composition = device
                .as_ref()
                .map(|device| device.report())
                .unwrap_or_default();
        });
    }
    // 別threadはEGLを破棄しない。停止完了はこのdropの後。
    if let Some(device) = device.as_mut()
        && let Err(error) = busy(state, || device.clear())
    {
        update(state, |s| s.snapshot.error = Some(error));
    }
    busy(state, || drop(active));
    busy(state, || drop(device));
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn reused_executor_releases_each_runtime_before_lending_the_same_thread() {
        let executor = Arc::new(executor::Executor::new().unwrap());
        let mut tid = None;
        for cycle in 1..=12 {
            let renderer =
                Controller::start_on(Default::default(), Some(executor.clone())).unwrap();
            let state = Arc::downgrade(&renderer.state);
            renderer.set_visible(true);
            let key = TargetKey::from_raw(1, cycle, 1, 1).unwrap();
            assert_eq!(
                renderer.attach(key, PlatformWindow::default(), 64, 64),
                Status::Accepted
            );
            wait(|| {
                renderer
                    .snapshot()
                    .targets
                    .first()
                    .is_some_and(|t| t.frames >= 1)
            });
            let id = renderer.snapshot().owner_tid;
            assert!(id > 0);
            assert_eq!(*tid.get_or_insert(id), id);
            renderer.request_stop();
            wait(|| renderer.is_finished());
            let stopped = renderer.snapshot();
            assert_eq!(
                (
                    stopped.workers,
                    stopped.contexts,
                    stopped.latest_images,
                    stopped.queued
                ),
                (0, 0, 0, 0)
            );
            assert!(stopped.targets.is_empty());
            assert_eq!(stopped.executor.as_ref().unwrap().active_jobs, 0);
            assert_eq!(
                stopped.executor.as_ref().unwrap().completed_jobs,
                cycle as u64
            );
            assert_eq!(
                renderer.attach(key, PlatformWindow::default(), 64, 64),
                Status::NotReady
            );
            drop(renderer);
            assert!(
                state.upgrade().is_none(),
                "idle executor must not retain an old runtime"
            );
        }
        let report = executor.snapshot();
        assert_eq!((report.threads, report.active_jobs), (1, 0));
        assert!(!report.failed);
    }

    #[test]
    fn busy_executor_cannot_create_a_replacement_or_finish_before_gpu_cleanup() {
        use std::sync::atomic::Ordering;
        let executor = Arc::new(executor::Executor::new().unwrap());
        let renderer = Controller::start_on(Default::default(), Some(executor.clone())).unwrap();
        let hook = Arc::new(platform::TestControl::default());
        hook.blocked.store(true, Ordering::Release);
        renderer.set_visible(true);
        renderer.attach(
            TargetKey::from_raw(1, 1, 1, 1).unwrap(),
            PlatformWindow {
                control: Some(hook.clone()),
            },
            64,
            64,
        );
        wait(|| hook.entered.load(Ordering::Acquire));
        renderer.request_stop();
        assert!(!renderer.is_finished());
        let error = Controller::start_on(Default::default(), Some(executor.clone()))
            .err()
            .unwrap();
        assert_eq!(error.kind(), std::io::ErrorKind::WouldBlock);
        assert_eq!(
            (executor.snapshot().threads, executor.snapshot().active_jobs),
            (1, 1)
        );
        hook.blocked.store(false, Ordering::Release);
        wait(|| renderer.is_finished());
        let next = Controller::start_on(Default::default(), Some(executor)).unwrap();
        wait(|| next.snapshot().owner_tid > 0);
        assert_eq!(next.snapshot().owner_tid, renderer.snapshot().owner_tid);
        next.request_stop();
        wait(|| next.is_finished());
    }

    #[test]
    fn panicked_executor_is_poisoned_instead_of_spawning_another_thread() {
        use std::sync::atomic::Ordering;
        let executor = Arc::new(executor::Executor::new().unwrap());
        let renderer = Controller::start_on(Default::default(), Some(executor.clone())).unwrap();
        let hook = Arc::new(platform::TestControl::default());
        hook.fault.store(4, Ordering::Release);
        renderer.set_visible(true);
        renderer.attach(
            TargetKey::from_raw(1, 1, 1, 1).unwrap(),
            PlatformWindow {
                control: Some(hook),
            },
            64,
            64,
        );
        wait(|| renderer.is_finished());
        assert_eq!(renderer.snapshot().error, Some("JNI_FAILURE"));
        assert!(executor.snapshot().failed);
        assert!(Controller::start_on(Default::default(), Some(executor.clone())).is_err());
        assert_eq!(executor.snapshot().completed_jobs, 1);
    }

    #[test]
    fn stalled_executor_remains_failed_even_if_the_gpu_eventually_returns() {
        use std::sync::atomic::Ordering;
        let executor = Arc::new(executor::Executor::new().unwrap());
        let renderer = Controller::start_on(Default::default(), Some(executor.clone())).unwrap();
        let hook = Arc::new(platform::TestControl::default());
        hook.blocked.store(true, Ordering::Release);
        renderer.set_visible(true);
        renderer.attach(
            TargetKey::from_raw(1, 1, 1, 1).unwrap(),
            PlatformWindow {
                control: Some(hook.clone()),
            },
            64,
            64,
        );
        wait(|| hook.entered.load(Ordering::Acquire));
        // 実GPU待ちの経過時刻だけを進め、snapshotによる製品のstall判定を通す。
        renderer.state.0.lock().unwrap().busy_since =
            Some(Instant::now() - STALL - Duration::from_millis(1));
        assert_eq!(renderer.snapshot().error, Some("RENDER_STALLED"));
        assert!(!renderer.is_finished());
        assert!(Controller::start_on(Default::default(), Some(executor.clone())).is_err());
        hook.blocked.store(false, Ordering::Release);
        wait(|| renderer.is_finished());
        assert_eq!(renderer.snapshot().contexts, 0);
        assert!(executor.snapshot().failed);
        assert!(Controller::start_on(Default::default(), Some(executor.clone())).is_err());
        assert_eq!(executor.snapshot().completed_jobs, 1);
    }

    #[test]
    fn theme_upload_is_cancelled_without_blocking_commands_or_resurrecting_old_candidate() {
        use std::sync::atomic::Ordering;
        let renderer = Controller::start().unwrap();
        let hook = Arc::new(platform::TestControl::default());
        hook.blocked.store(true, Ordering::Release);
        renderer.state.0.lock().unwrap().theme_hook = Some(hook.clone());
        let theme = Arc::new(
            eidolon_theme::raster::compile(
                &eidolon_theme::cyan_orbit(),
                &Default::default(),
                &renderer.pixel_budget(),
                &Default::default(),
            )
            .unwrap(),
        );
        let first = renderer.request_theme(Some(theme), false).unwrap();
        wait(|| hook.entered.load(Ordering::Acquire));
        let cancelled = renderer.cancel_pending_theme().unwrap();
        let off = renderer.request_theme(None, true).unwrap();
        assert!(off > first);
        assert_eq!(cancelled.theme_revision, 0);
        assert_eq!(renderer.snapshot().theme_revision, 0);
        hook.blocked.store(false, Ordering::Release);
        wait(|| renderer.snapshot().theme_revision == off);
        let snapshot = renderer.snapshot();
        assert_eq!(snapshot.theme_attempted_revision, off);
        assert_eq!(snapshot.theme_discarded, 1);
        assert!(snapshot.theme_id.is_none());
        renderer.request_stop();
        wait(|| renderer.is_finished());
    }
    #[test]
    fn static_window_stops_presenting_and_redraws_on_resume_new_image_and_rebuild() {
        use std::sync::atomic::Ordering;
        let renderer = Controller::start().unwrap();
        renderer.use_windows();
        renderer.set_visible(true);
        let key = TargetKey::from_raw(1, 1, 1, 1).unwrap();
        renderer.windows(key.session, &[key.window]);
        let control = Arc::new(platform::TestControl::default());
        let (image, probes) = fixtures::make(0, 1, key, 64, 64).unwrap();
        renderer.submit(image, probes);
        renderer.attach_window(
            key,
            PlatformWindow {
                control: Some(control.clone()),
            },
            64,
            64,
        );
        wait(|| {
            renderer
                .snapshot()
                .targets
                .first()
                .is_some_and(|t| t.frames == 1)
        });
        thread::sleep(FRAME * 4);
        assert_eq!(renderer.snapshot().targets[0].frames, 1);
        renderer.set_visible(false);
        // ownerがpauseを観測してからresumeする。
        thread::sleep(FRAME * 2);
        renderer.set_visible(true);
        wait(|| renderer.snapshot().targets[0].frames == 2);
        assert_eq!(
            renderer.snapshot().targets[0].content_frame,
            Some(FrameSeq::new(1).unwrap())
        );
        // 次の実画像更新でcontext lossを起こし、更新画像を同じseqで描き直す。
        control.fault.store(2, Ordering::Release);
        let (image, probes) = fixtures::make(0, 2, key, 64, 64).unwrap();
        renderer.submit(image, probes);
        wait(|| {
            renderer.snapshot().context_rebuilds == 1
                && renderer.snapshot().targets[0].content_frame == Some(FrameSeq::new(2).unwrap())
        });
        let frames = renderer.snapshot().targets[0].frames;
        thread::sleep(FRAME * 4);
        assert_eq!(renderer.snapshot().targets[0].frames, frames);
        renderer.detach(key);
        wait(|| renderer.snapshot().targets.is_empty());
        let replacement = TargetKey::from_raw(1, 1, 1, 2).unwrap();
        renderer.attach_window(replacement, PlatformWindow::default(), 64, 64);
        wait(|| {
            renderer
                .snapshot()
                .targets
                .first()
                .is_some_and(|t| t.content_frame == Some(FrameSeq::new(2).unwrap()))
        });
        renderer.request_stop();
        wait(|| renderer.is_finished());
    }
    #[test]
    fn decoration_ticks_two_static_windows_without_content_sequence_and_reduced_motion_stops() {
        let renderer = Controller::start().unwrap();
        let theme = Arc::new(
            eidolon_theme::raster::compile(
                &eidolon_theme::cyan_orbit(),
                &Default::default(),
                &renderer.pixel_budget(),
                &Default::default(),
            )
            .unwrap(),
        );
        renderer.use_windows();
        let key = TargetKey::from_raw(1, 1, 1, 1).unwrap();
        let other = TargetKey::from_raw(1, 2, 1, 1).unwrap();
        renderer.windows(key.session, &[key.window, other.window]);
        renderer.set_theme(Some(theme.clone()), false);
        for key in [key, other] {
            renderer.set_semantic_state(
                key.session,
                key.window,
                theme_state::SemanticState {
                    busy: true,
                    cpu_cores: Some(2.),
                    rss_bytes: Some(268435456),
                    ..Default::default()
                },
            );
            let (image, probes) = fixtures::make(16, 1, key, 64, 64).unwrap();
            renderer.submit(image, probes);
            renderer.attach_window(key, PlatformWindow::default(), 64, 64);
        }
        renderer.set_visible(true);
        wait(|| {
            let snapshot = renderer.snapshot();
            snapshot.targets.len() == 2 && snapshot.targets.iter().all(|t| t.decoration_frames >= 3)
        });
        assert!(
            renderer
                .snapshot()
                .targets
                .iter()
                .all(|t| t.content_frame == Some(FrameSeq::new(1).unwrap()) && t.canvas.is_some())
        );
        let before = renderer
            .snapshot()
            .targets
            .iter()
            .map(|t| t.frames)
            .collect::<Vec<_>>();
        renderer.set_theme(Some(theme.clone()), true);
        wait(|| {
            let s = renderer.snapshot();
            s.theme_revision == 2
                && s.targets
                    .iter()
                    .zip(&before)
                    .all(|(t, old)| t.frames > *old)
        });
        let stable = renderer
            .snapshot()
            .targets
            .iter()
            .map(|t| t.frames)
            .collect::<Vec<_>>();
        thread::sleep(FRAME * 4);
        assert_eq!(
            renderer
                .snapshot()
                .targets
                .iter()
                .map(|t| t.frames)
                .collect::<Vec<_>>(),
            stable
        );
        renderer.set_theme(None, false);
        wait(|| {
            renderer.snapshot().theme_revision == 3
                && renderer
                    .snapshot()
                    .targets
                    .iter()
                    .all(|t| t.canvas.is_none())
        });
        renderer.request_stop();
        wait(|| renderer.is_finished());
        drop(theme);
        assert_eq!(renderer.pixel_budget().used_bytes(), 0);
    }
    fn wait(test: impl Fn() -> bool) {
        let deadline = Instant::now() + Duration::from_secs(3);
        while !test() {
            assert!(Instant::now() < deadline);
            thread::sleep(Duration::from_millis(2));
        }
    }
    #[test]
    fn window_mode_rejects_diagnostic_role_and_retired_generations_after_unmap() {
        let renderer = Controller::start().unwrap();
        renderer.set_visible(true);
        let diagnostic = TargetKey::from_raw(1, 1, 1, 1).unwrap();
        renderer.attach(diagnostic, PlatformWindow::default(), 64, 64);
        wait(|| {
            renderer
                .snapshot()
                .targets
                .first()
                .is_some_and(|target| target.frames > 0)
        });
        renderer.use_windows();
        assert_eq!(
            renderer.attach(diagnostic, PlatformWindow::default(), 64, 64),
            Status::NotReady
        );
        assert_eq!(
            renderer.windows(diagnostic.session, &[diagnostic.window]),
            Status::Accepted
        );
        let next = TargetKey::from_raw(1, 1, 1, 2).unwrap();
        let (image, probes) = fixtures::make(0, 2, next, 64, 64).unwrap();
        renderer.submit(image, probes);
        assert_eq!(
            renderer.attach_window(diagnostic, PlatformWindow::default(), 64, 64),
            Status::Stale
        );
        thread::sleep(Duration::from_millis(50));
        assert_eq!(
            renderer.snapshot().targets[0].content_frame,
            None,
            "診断targetへ製品画像を流さない"
        );
        renderer.detach(diagnostic);
        wait(|| renderer.snapshot().targets.is_empty());
        renderer.attach_window(next, PlatformWindow::default(), 64, 64);
        wait(|| {
            renderer
                .snapshot()
                .targets
                .first()
                .is_some_and(|target| target.content_frame == Some(FrameSeq::new(2).unwrap()))
        });
        assert_eq!(renderer.snapshot().targets[0].role, RenderMode::Windows);
        renderer.detach(next);
        wait(|| renderer.snapshot().targets.is_empty());
        renderer.forget_window(next.session, next.window);
        let remap = TargetKey::from_raw(1, 1, 1, 3).unwrap();
        let (image, probes) = fixtures::make(0, 3, remap, 64, 64).unwrap();
        renderer.submit(image, probes);
        let stale = renderer.snapshot().stale;
        renderer.attach_window(next, PlatformWindow::default(), 64, 64);
        wait(|| renderer.snapshot().stale > stale);
        assert!(renderer.snapshot().targets.is_empty());
        renderer.attach_window(remap, PlatformWindow::default(), 64, 64);
        wait(|| {
            renderer
                .snapshot()
                .targets
                .first()
                .is_some_and(|target| target.content_frame == Some(FrameSeq::new(3).unwrap()))
        });
        renderer.request_stop();
        wait(|| renderer.is_finished());
    }
    #[test]
    fn retired_logical_windows_do_not_exhaust_eight_entry_history_across_twenty_sessions() {
        let renderer = Controller::start().unwrap();
        renderer.use_windows();
        renderer.set_visible(true);
        for session in 1..=20 {
            let key = TargetKey::from_raw(session, 1, 1, 1).unwrap();
            assert_eq!(
                renderer.windows(key.session, &[key.window]),
                Status::Accepted
            );
            let (image, probes) = fixtures::make(0, 1, key, 64, 64).unwrap();
            renderer.submit(image, probes);
            renderer.attach_window(key, PlatformWindow::default(), 64, 64);
            wait(|| {
                renderer
                    .snapshot()
                    .targets
                    .first()
                    .is_some_and(|target| target.key == key && target.frames > 0)
            });
            renderer.detach(key);
            wait(|| renderer.snapshot().targets.is_empty());
            renderer.forget_window(key.session, key.window);
            assert_eq!(renderer.windows(key.session, &[]), Status::Accepted);
        }
        renderer.request_stop();
        wait(|| renderer.is_finished());
    }
    #[test]
    fn coalesces_owned_snapshots_before_attach_rejects_old_frames_and_releases_at_stop() {
        let renderer = Controller::start().unwrap();
        let key = TargetKey::from_raw(1, 1, 1, 1).unwrap();
        let (first, probes) = fixtures::make(0, 1, key, 64, 64).unwrap();
        let weak = Arc::downgrade(&first);
        assert_eq!(renderer.submit(first, probes), Status::Accepted);
        for sequence in 2..=1000 {
            let (image, probes) = fixtures::make(0, sequence, key, 64, 64).unwrap();
            assert_eq!(renderer.submit(image, probes), Status::Accepted);
        }
        assert!(
            weak.upgrade().is_none(),
            "old snapshotを1000frame保持しない"
        );
        let (old, probes) = fixtures::make(0, 999, key, 64, 64).unwrap();
        assert_eq!(renderer.submit(old, probes), Status::Stale);
        assert_eq!(renderer.snapshot().latest_images, 1);
        assert_eq!(renderer.snapshot().queued, 0);
        assert_eq!(
            renderer.attach(key, PlatformWindow::default(), 64, 64),
            Status::Accepted
        );
        renderer.set_visible(true);
        wait(|| {
            renderer
                .snapshot()
                .targets
                .first()
                .is_some_and(|target| target.content_frame == Some(FrameSeq::new(1000).unwrap()))
        });
        assert_eq!(renderer.snapshot().contexts, 1);
        renderer.request_stop();
        wait(|| renderer.is_finished());
        assert_eq!(renderer.snapshot().latest_images, 0);
        assert_eq!(renderer.snapshot().contexts, 0);
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
        let next = TargetKey::from_raw(1, 1, 1, 2).unwrap();
        control.fault.store(3, Ordering::Release);
        renderer.attach(
            next,
            PlatformWindow {
                control: Some(control),
            },
            1024,
            768,
        );
        wait(|| renderer.snapshot().detached.contains(&next));
        assert_eq!(renderer.snapshot().error, Some("COMPOSITE_MISMATCH"));
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
