//! 一つのSSH SessionIdに束縛したDisplay。core ownerのtick以外でWaylandを操作しない。
use crate::{
    compositor::protocol::{Counters, ProtocolState, WindowReport},
    helper::RuntimeDirectory,
    waypipe::{ClientReport, LocalClient},
};
use eidolon_model::{
    commands::AppMode,
    ids::{SessionId, WindowId},
    window::{LogicalSize, WindowLife},
};
use serde::Serialize;
use smithay::reexports::wayland_server::Display;
use std::{
    fs,
    os::unix::net::UnixListener,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub state: &'static str,
    pub session: Option<SessionId>,
    pub nonce: String,
    pub mode: Option<AppMode>,
    pub callback_clock: &'static str,
    pub input: crate::compositor::protocol::InputReport,
    pub ready: bool,
    pub local: ClientReport,
    pub accepted: u32,
    pub forward_sent: bool,
    pub copies: u64,
    pub configures: u64,
    pub acks: u64,
    pub releases: u64,
    pub callbacks: u64,
    pub verified_hashes: u32,
    pub non_uniform: bool,
    pub last_size: Option<LogicalSize>,
    pub window_ids: Vec<WindowId>,
    pub windows: Vec<WindowReport>,
    pub clients: usize,
    pub objects: usize,
    pub surfaces: usize,
    pub pixel_bytes: usize,
    pub pool_bytes: usize,
    pub pool_count: usize,
    pub pool_rejections: u64,
    pub last_rejected_pool_bytes: Option<usize>,
    pub memfd_fds_after: Option<usize>,
    pub memfd_maps_after: Option<usize>,
    pub directory_cleaned: bool,
    pub error: Option<&'static str>,
}

pub struct Session {
    report: Report,
    directory: Option<RuntimeDirectory>,
    listener: Option<UnixListener>,
    display: Option<Display<ProtocolState>>,
    protocol: Option<ProtocolState>,
    local: LocalClient,
    base: Counters,
    copied: u64,
    virtual_clock: bool,
    virtual_tick: u64,
    closing: bool,
    published: Vec<WindowId>,
    started: Instant,
}
impl Session {
    pub fn start(
        session: SessionId,
        files: &Path,
        executable: &Path,
        nonce: &str,
        mode: AppMode,
        virtual_clock: bool,
        keyboard_layout: eidolon_model::keyboard::KeyboardLayout,
    ) -> Result<Self, &'static str> {
        if virtual_clock && !cfg!(debug_assertions) {
            return Err("NOT_READY");
        }
        let directory = RuntimeDirectory::create(files, nonce)?;
        let listener =
            UnixListener::bind(directory.path.join("w")).map_err(|_| "LOCAL_SOCKET_FAILED")?;
        listener
            .set_nonblocking(true)
            .map_err(|_| "LOCAL_SOCKET_FAILED")?;
        let display = Display::<ProtocolState>::new().map_err(|_| "DISPLAY_CREATE")?;
        let mut protocol = ProtocolState::new(&display.handle(), session);
        if !virtual_clock {
            protocol.configure_keyboard(keyboard_layout)?;
        }
        let local = LocalClient::start(executable, &directory.path)?;
        Ok(Self {
            report: Report {
                state: "preparing",
                session: Some(session),
                nonce: nonce.into(),
                mode: Some(mode),
                callback_clock: if virtual_clock {
                    "virtual"
                } else {
                    "target-driven"
                },
                ..Default::default()
            },
            directory: Some(directory),
            listener: Some(listener),
            display: Some(display),
            protocol: Some(protocol),
            local,
            base: Default::default(),
            copied: 0,
            virtual_clock,
            virtual_tick: 0,
            closing: false,
            published: vec![],
            started: Instant::now(),
        })
    }
    pub fn snapshot(&self) -> Report {
        self.report.clone()
    }
    pub fn session(&self) -> SessionId {
        self.report.session.unwrap()
    }
    pub fn mode(&self) -> AppMode {
        self.report.mode.unwrap()
    }
    pub fn nonce(&self) -> &str {
        &self.report.nonce
    }
    pub fn virtual_clock(&self) -> bool {
        self.virtual_clock
    }
    pub fn focus_window(&mut self, window: WindowId) -> Result<(), &'static str> {
        if self.closing {
            return Err("STALE");
        }
        self.protocol
            .as_mut()
            .ok_or("NOT_READY")?
            .focus_window(window)
    }
    pub fn key(
        &mut self,
        window: WindowId,
        focus_token: eidolon_model::ids::CommandSeq,
        key: eidolon_model::keyboard::KeyPacket,
    ) -> Result<(), &'static str> {
        if self.closing {
            return Err("STALE");
        }
        self.protocol
            .as_mut()
            .ok_or("NOT_READY")?
            .key_at_focus(window, focus_token, key)
    }
    pub fn reset_input(&mut self) {
        if let Some(protocol) = self.protocol.as_mut() {
            protocol.reset_input();
        }
    }
    pub fn resize_logical(
        &mut self,
        window: WindowId,
        size: LogicalSize,
    ) -> Result<(), &'static str> {
        if self.closing {
            return Err("STALE");
        }
        self.protocol
            .as_mut()
            .ok_or("NOT_READY")?
            .request_resize(window, size)
    }
    pub fn set_physical_size(
        &mut self,
        window: WindowId,
        request: eidolon_model::ids::CommandSeq,
        size: eidolon_model::resize::PhysicalSize,
    ) -> Result<(), &'static str> {
        if self.closing {
            return Err("STALE");
        }
        self.protocol
            .as_mut()
            .ok_or("NOT_READY")?
            .set_physical_size(window, request, size)
    }
    pub fn physical_size_applied(
        &mut self,
        window: WindowId,
        command: Option<eidolon_model::ids::CommandSeq>,
        size: eidolon_model::resize::PhysicalSize,
    ) -> Result<(), &'static str> {
        if self.closing {
            return Err("STALE");
        }
        self.protocol
            .as_mut()
            .ok_or("NOT_READY")?
            .physical_size_applied(window, command, size)
    }
    pub fn pointer(
        &mut self,
        window: WindowId,
        pointer: eidolon_model::pointer::PointerPacket,
        width: i32,
        height: i32,
    ) -> Result<(), &'static str> {
        if self.closing {
            return Err("STALE");
        }
        self.protocol
            .as_mut()
            .ok_or("NOT_READY")?
            .pointer(window, pointer, width, height)
    }
    /// normal modeだけが実際のpresented snapshotに応じてclientを進める。
    pub fn render_tick(&mut self, gl: &eidolon_render::Controller, now: Duration) {
        if self.virtual_clock || gl.is_stopping() {
            return;
        }
        let session = self.session();
        if self.closing || self.closed() {
            let _ = gl.windows(session, &[]);
            for window in self.published.drain(..) {
                let _ = gl.forget_window(session, window);
            }
            return;
        }
        let Some(state) = self.protocol.as_mut() else {
            return;
        };
        let windows = state.windows();
        let logical: Vec<_> = windows.iter().map(|window| window.window).collect();
        if gl.windows(session, &logical) != eidolon_model::state::Status::Accepted {
            self.fail("RENDER_BUDGET");
            return;
        }
        let mapped: Vec<_> = windows
            .iter()
            .filter(|w| w.life == WindowLife::Mapped)
            .map(|w| w.window)
            .collect();
        for old in self.published.iter().filter(|old| !mapped.contains(old)) {
            if gl.forget_window(session, *old) != eidolon_model::state::Status::Accepted {
                self.fail("RENDER_BUSY");
                return;
            }
        }
        self.published = mapped;
        for window in &windows {
            if window.life != WindowLife::Mapped {
                continue;
            }
            if let Some(snapshot) = state.latest_snapshot(window.window) {
                let code = gl.submit(snapshot, vec![]);
                if !matches!(
                    code,
                    eidolon_model::state::Status::Accepted | eidolon_model::state::Status::Stale
                ) {
                    // request_stopは任意threadから来る。正常な停止との競合だけを除外する。
                    if code != eidolon_model::state::Status::NotReady || !gl.is_stopping() {
                        self.fail("RENDER_BUDGET");
                    }
                    return;
                }
            }
        }
        let render = gl.snapshot();
        let producers = render
            .targets
            .iter()
            .filter(|t| {
                t.role == eidolon_render::RenderMode::Windows
                    && t.key.session == session
                    && t.frames > 0
            })
            .filter_map(|t| {
                eidolon_model::window::BufferSizePx::new(t.gpu.width as u32, t.gpu.height as u32)
                    .ok()
                    .map(|size| (t.key.window, size))
            })
            .collect::<Vec<_>>();
        state.observe_producers(&producers);
        for window in &windows {
            let presented = render.targets.iter().any(|target| {
                target.role == eidolon_render::RenderMode::Windows
                    && target.key.session == session
                    && target.key.window == window.window
                    && target.content_frame == window.frame
                    && target.frames > 0
            });
            state.frame_callbacks(
                window.window,
                now,
                !render.paused,
                presented,
                render.error.is_none(),
            );
        }
        self.refresh_stats();
    }
    pub fn socket(&self) -> Option<PathBuf> {
        self.directory.as_ref().map(|d| d.path.join("p"))
    }
    pub fn closed(&self) -> bool {
        self.report.state == "stopped"
    }
    pub fn select_app(&mut self, nonce: &str, mode: AppMode) -> Result<(), &'static str> {
        if !eidolon_transport::forward_probe::valid_nonce(nonce) {
            return Err("INVALID_ARGUMENT");
        }
        if self.closing || self.closed() {
            return Err("STALE");
        }
        self.base = self.protocol.as_ref().unwrap().counters;
        self.copied = self.base.copies;
        self.report.nonce = nonce.into();
        self.report.mode = Some(mode);
        self.report.forward_sent = false;
        self.report.verified_hashes = 0;
        self.report.non_uniform = false;
        self.report.last_size = None;
        self.report.state = "preparing";
        Ok(())
    }
    pub fn forward_started(&mut self) {
        self.report.forward_sent = true;
        self.report.state = "starting_remote";
    }
    pub fn forwarding(&mut self, forward: &eidolon_transport::forward_app::Report) {
        if self.closing || self.closed() || forward.nonce != self.report.nonce {
            return;
        }
        if let Some(error) = forward.error {
            self.fail(error);
        } else if forward.state == "pass" {
            self.report.state = "pass";
        } else if forward.state == "running" {
            self.report.state = "running";
        }
    }
    pub fn fail(&mut self, error: &'static str) {
        if self.report.error.is_none() {
            self.report.error = Some(error);
        }
        if !self.closing {
            self.report.state = "fail";
        }
    }
    pub fn frame_callbacks(
        &mut self,
        window: WindowId,
        now: Duration,
        visible: bool,
        target: bool,
        budget: bool,
    ) {
        if !self.closing
            && let Some(protocol) = self.protocol.as_mut()
        {
            protocol.frame_callbacks(window, now, visible, target, budget);
        }
    }
    /// remote/forwardの終了後にだけlocalを止める。プロセス待ちはworkerに任せる。
    pub fn tick(&mut self, close: bool, remote_gone: bool) {
        if self.closed() {
            return;
        }
        self.closing |= close;
        if self.closing {
            self.reset_input();
            self.report.state = "closing";
            self.report.ready = false;
            if remote_gone {
                self.local.request_stop();
            }
        }
        if let Err(error) = self.dispatch() {
            self.fail(error);
        }
        if !self.closing
            && let Some(protocol) = self.protocol.as_mut()
        {
            protocol.resize_tick(self.started.elapsed());
        }
        self.refresh_stats();
        self.report.local = self.local.snapshot();
        if let Some(error) = self.report.local.error {
            self.fail(error);
        }
        self.report.ready = !self.closing
            && self.report.local.ready
            && self.report.accepted > 0
            && self.report.error.is_none();
        if !self.closing {
            return;
        }
        if !remote_gone || !self.local.finished() {
            return;
        }
        self.report.local = self.local.snapshot();
        if self.report.clients > 0
            && let Some(display) = self.display.as_ref()
        {
            let backend = display.handle().backend_handle();
            let mut clients = vec![];
            backend.with_all_clients(|id| clients.push(id));
            for client in clients {
                backend.kill_client(
                    client,
                    smithay::reexports::wayland_server::backend::DisconnectReason::ConnectionClosed,
                );
            }
            if let Err(error) = self.dispatch() {
                self.fail(error);
            }
            self.refresh_stats();
        }
        // actual pool dropはSmithayの遅延thread。予算が戻るまでは回収完了を出さない。
        if self.report.clients != 0
            || self.report.objects != 0
            || self.report.surfaces != 0
            || !self.report.windows.is_empty()
            || self.report.pixel_bytes != 0
            || self.report.pool_bytes != 0
            || self.report.pool_count != 0
            || self.report.local.owned_groups != 0
        {
            return;
        }
        drop(self.listener.take());
        drop(self.display.take());
        drop(self.protocol.take());
        let result = (|| {
            if let Some(directory) = self.directory.as_ref() {
                directory.cleanup()?;
                self.directory.take();
                self.report.directory_cleaned = true;
            }
            let (fds, maps) = named_memfds()?;
            self.report.memfd_fds_after = Some(fds);
            self.report.memfd_maps_after = Some(maps);
            if fds != 0 || maps != 0 {
                return Err("CLEANUP_FAILED");
            }
            Ok(())
        })();
        if let Err(error) = result {
            self.fail(error);
            return;
        }
        self.report.input = Default::default();
        self.report.state = "stopped";
    }
    fn dispatch(&mut self) -> Result<(), &'static str> {
        let (Some(display), Some(state)) = (self.display.as_mut(), self.protocol.as_mut()) else {
            return Ok(());
        };
        if !self.closing {
            for _ in 0..8 {
                let Some(listener) = self.listener.as_ref() else {
                    break;
                };
                match listener.accept() {
                    Ok((stream, _)) => {
                        state.accept(&display.handle(), stream)?;
                        self.report.accepted += 1;
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => break,
                    Err(_) => return Err("CLIENT_ACCEPT"),
                }
            }
        }
        display
            .dispatch_clients(state)
            .map_err(|_| "SERVER_DISPATCH")?;
        state.finish_dispatch(&display.handle());
        let windows = state.windows();
        if state.counters.copies > self.copied {
            if self.report.mode == Some(AppMode::Pattern) && !self.closing {
                #[cfg(any(test, feature = "debug-fixtures"))]
                {
                    use sha2::{Digest, Sha256};
                    let mapped: Vec<_> = windows
                        .iter()
                        .filter(|w| w.life == WindowLife::Mapped)
                        .collect();
                    if state.counters.copies != self.copied + 1 || mapped.len() != 1 {
                        return Err("COPY_SEQUENCE");
                    }
                    let snapshot = state
                        .latest_snapshot(mapped[0].window)
                        .ok_or("MISSING_SNAPSHOT")?;
                    if snapshot.layers.len() != 1
                        || self.report.verified_hashes >= eidolon_shm_fixture::FRAMES
                    {
                        return Err("PIXEL_HASH");
                    }
                    let hash: String = Sha256::digest(snapshot.layers[0].pixels.pixels())
                        .iter()
                        .map(|b| format!("{b:02x}"))
                        .collect();
                    if hash != eidolon_shm_fixture::expected_hash(self.report.verified_hashes) {
                        return Err("PIXEL_HASH");
                    }
                    self.report.verified_hashes += 1;
                }
                #[cfg(not(any(test, feature = "debug-fixtures")))]
                return Err("NOT_READY");
            }
            self.copied = state.counters.copies;
            for window in windows.iter().filter(|w| w.life == WindowLife::Mapped) {
                if !self.report.window_ids.contains(&window.window) {
                    if self.report.window_ids.len() == 64 {
                        self.report.window_ids.remove(0);
                    }
                    self.report.window_ids.push(window.window);
                }
                if let Some(snapshot) = state.latest_snapshot(window.window) {
                    self.report.last_size = Some(snapshot.logical_size);
                    // terminal本文・pixel/hashを診断へ含めず、非一様な描画が来た事実だけを測る。
                    for layer in &snapshot.layers {
                        let pixels = layer.pixels.pixels();
                        self.report.non_uniform |=
                            pixels.chunks_exact(4).any(|p| p != &pixels[..4]);
                    }
                }
            }
        }
        if self.virtual_clock && !self.closing {
            let now = Duration::from_nanos(self.virtual_tick * 16_666_667);
            self.virtual_tick += 1;
            for window in &windows {
                state.frame_callbacks(window.window, now, true, true, true);
            }
        }
        display.flush_clients().map_err(|_| "SERVER_FLUSH")?;
        Ok(())
    }
    fn refresh_stats(&mut self) {
        let (Some(display), Some(state)) = (self.display.as_ref(), self.protocol.as_ref()) else {
            return;
        };
        self.report.windows = state.windows();
        self.report.input = state.input_report();
        self.report.clients = 0;
        self.report.objects = 0;
        let backend = display.handle().backend_handle();
        let mut clients = vec![];
        backend.with_all_clients(|client| clients.push(client));
        self.report.clients = clients.len();
        for client in clients {
            let _ = backend.with_all_objects_for(client, |_| self.report.objects += 1);
        }
        self.report.surfaces = state.surface_count();
        self.report.pixel_bytes = state.pixel_bytes();
        let pool = state.pool_usage();
        self.report.pool_bytes = pool.bytes;
        self.report.pool_count = pool.pools;
        self.report.copies = state.counters.copies.saturating_sub(self.base.copies);
        self.report.configures = state
            .counters
            .configures
            .saturating_sub(self.base.configures);
        self.report.acks = state.counters.acks.saturating_sub(self.base.acks);
        self.report.releases = state.counters.releases.saturating_sub(self.base.releases);
        self.report.callbacks = state.counters.callbacks.saturating_sub(self.base.callbacks);
        self.report.pool_rejections = state
            .counters
            .pool_rejections
            .saturating_sub(self.base.pool_rejections);
        self.report.last_rejected_pool_bytes =
            (self.report.pool_rejections > 0).then_some(state.counters.last_rejected_pool_bytes);
    }
}
impl Drop for Session {
    fn drop(&mut self) {
        self.local.request_stop();
        while !self.local.finished() {
            std::thread::sleep(Duration::from_millis(5));
        }
        drop(self.listener.take());
        drop(self.display.take());
        drop(self.protocol.take());
    }
}
fn named_memfds() -> Result<(usize, usize), &'static str> {
    fn owned(text: &str) -> bool {
        text.contains("memfd:waypipe") || text.contains("memfd:eidolon-shm-fixture")
    }
    let fds = fs::read_dir("/proc/self/fd")
        .map_err(|_| "PROC_FD")?
        .filter_map(Result::ok)
        .filter_map(|f| fs::read_link(f.path()).ok())
        .filter(|p| owned(&p.to_string_lossy()))
        .count();
    let maps = fs::read_to_string("/proc/self/maps")
        .map_err(|_| "PROC_MAPS")?
        .lines()
        .filter(|s| owned(s))
        .count();
    Ok((fds, maps))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        process::Command,
        sync::{
            Arc,
            atomic::{AtomicBool, Ordering},
            mpsc,
        },
        thread,
    };
    #[test]
    fn real_waypipe_server_producer_and_owned_compositor_verify_100_frames() {
        let _guard = crate::compositor::FIXTURE_LOCK.lock().unwrap();
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let marker: serde_json::Value = serde_json::from_slice(
            &fs::read(root.join("native/generated/waypipe-host.json")).unwrap(),
        )
        .unwrap();
        let executable = PathBuf::from(marker["executable"].as_str().unwrap());
        let producer = root.join("target/debug/eidolon-shm-fixture");
        assert!(
            producer.is_file(),
            "先にcargo build -p eidolon-shm-fixture --bin eidolon-shm-fixtureを実行する"
        );
        let files = std::env::temp_dir().join(format!("ew5-{}", std::process::id()));
        fs::create_dir(&files).unwrap();
        let remote = files.join("remote");
        fs::create_dir(&remote).unwrap();
        let mut session = Session::start(
            SessionId::new(17).unwrap(),
            &files,
            &executable,
            "1122334455667788",
            AppMode::Pattern,
            true,
            Default::default(),
        )
        .unwrap();
        let cancel = Arc::new(AtomicBool::new(false));
        let mut worker = None;
        let (tx, rx) = mpsc::sync_channel(1);
        let mut sender = Some(tx);
        let deadline = std::time::Instant::now() + Duration::from_secs(30);
        let outcome = loop {
            session.tick(false, false);
            let report = session.snapshot();
            if let Some(error) = report.error {
                break Err(format!("{error}: {report:?}"));
            }
            if report.ready && worker.is_none() {
                let (executable, producer, transfer, remote, cancel, tx) = (
                    executable.clone(),
                    producer.clone(),
                    session.socket().unwrap(),
                    remote.clone(),
                    cancel.clone(),
                    sender.take().unwrap(),
                );
                worker = Some(thread::spawn(move || {
                    let result = crate::waypipe::OwnedProcess::spawn(
                        Command::new(executable)
                            .args([
                                "--no-gpu",
                                "--compress",
                                "none",
                                "--threads",
                                "1",
                                "--socket",
                            ])
                            .arg(transfer)
                            .arg("--display")
                            .arg(remote.join("w"))
                            .args(["server", "--"])
                            .arg(producer)
                            .arg("--pattern")
                            .env("XDG_RUNTIME_DIR", remote),
                    )
                    .and_then(|child| child.capture_until(&cancel, Duration::from_secs(25)));
                    let _ = tx.send(result);
                }));
            }
            if let Ok(result) = rx.try_recv() {
                break result.map_err(str::to_owned);
            }
            if std::time::Instant::now() >= deadline {
                break Err(format!("TIMEOUT: {report:?}"));
            }
            thread::sleep(Duration::from_millis(1));
        };
        cancel.store(true, Ordering::Release);
        if let Some(worker) = worker {
            worker.join().unwrap();
        }
        let before = session.snapshot();
        let closing = std::time::Instant::now() + Duration::from_secs(8);
        while !session.closed() && std::time::Instant::now() < closing {
            session.tick(true, true);
            thread::sleep(Duration::from_millis(5));
        }
        let after = session.snapshot();
        drop(session);
        fs::remove_dir_all(files).unwrap();
        let output =
            outcome.unwrap_or_else(|error| panic!("{error}; before={before:?}; after={after:?}"));
        let remote: serde_json::Value = serde_json::from_slice(&output).unwrap();
        assert_eq!(before.verified_hashes, 100, "{before:?}");
        assert_eq!(before.copies, 100);
        assert_eq!(remote["protocol"]["frames"], 100);
        assert_eq!(remote["protocol"]["releases"], 100);
        assert_eq!(remote["protocol"]["callbacks"], 100);
        assert_eq!(after.state, "stopped", "{after:?}");
        assert!(after.directory_cleaned && after.local.waited);
        assert_eq!(
            (
                after.clients,
                after.objects,
                after.surfaces,
                after.pixel_bytes,
                after.pool_bytes,
                after.pool_count
            ),
            (0, 0, 0, 0, 0, 0)
        );
        assert_eq!(
            (after.memfd_fds_after, after.memfd_maps_after),
            (Some(0), Some(0))
        );
    }
}
