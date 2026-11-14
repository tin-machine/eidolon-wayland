//! 一つのSSH SessionIdに束縛したDisplay。core ownerのtick以外でWaylandを操作しない。
use crate::{
    compositor::protocol::{Counters, ProtocolState, WindowReport},
    helper::RuntimeDirectory,
    linux_desktop_v3::{I3WindowCut, WindowHistory},
    waypipe::{ClientReport, LocalClient},
};
use eidolon_model::{
    commands::AppMode,
    ids::{CommandSeq, SessionId, WindowId},
    linux_desktop_v3::presentation::NativeOrigin,
    window::{LogicalSize, WindowLife},
};
use serde::Serialize;
use smithay::reexports::wayland_server::Display;
use std::{
    collections::BTreeSet,
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
    pub remote_gui_verified: bool,
    /// 通常 GUI の検証済み Started と main/dialog 所有関係が揃った窓だけを公開する。
    pub gui_windows: Vec<WindowId>,
    pub non_uniform: bool,
    pub last_size: Option<LogicalSize>,
    pub window_ids: Vec<WindowId>,
    pub visible_windows: Vec<WindowId>,
    pub windows: Vec<WindowReport>,
    pub workloads: Vec<crate::workload::WindowWorkload>,
    pub clients: usize,
    pub objects: usize,
    pub surfaces: usize,
    pub pixel_bytes: usize,
    pub pool_bytes: usize,
    pub pool_count: usize,
    pub pool_rejections: u64,
    pub surface_rejections: u64,
    pub last_rejected_pool_bytes: Option<usize>,
    pub toplevel_rejections: u64,
    pub rejected_toplevels: usize,
    pub client_rejections: u64,
    pub memfd_fds_after: Option<usize>,
    pub memfd_maps_after: Option<usize>,
    pub directory_cleaned: bool,
    /// Synchronous construction failed before a LocalClient worker existed.
    pub startup_failed: bool,
    /// That unstarted owner has explicitly released all resources; this is not a child wait.
    pub startup_cleanup_complete: bool,
    pub error: Option<&'static str>,
}

pub struct Session {
    report: Report,
    directory: Option<RuntimeDirectory>,
    listener: Option<UnixListener>,
    display: Option<Display<ProtocolState>>,
    protocol: Option<ProtocolState>,
    local: Option<LocalClient>,
    startup_allocation_absent: bool,
    base: Counters,
    copied: u64,
    virtual_clock: bool,
    virtual_tick: u64,
    closing: bool,
    published: Vec<WindowId>,
    started: Instant,
    workload: crate::workload::Tracker,
    desktop_request: Option<CommandSeq>,
    desktop_started: bool,
    v3_origin: Option<NativeOrigin>,
    v3_history: Option<WindowHistory>,
    /// Current admission AND current SDK target; cleared on every revocation.
    v3_input_allowed: BTreeSet<WindowId>,
}
pub struct Options {
    pub virtual_clock: bool,
    pub keyboard_layout: eidolon_model::keyboard::KeyboardLayout,
    pub pixels: eidolon_model::pixels::PixelBudget,
}
impl Session {
    pub fn start(
        session: SessionId,
        files: &Path,
        executable: &Path,
        nonce: &str,
        mode: AppMode,
        options: Options,
    ) -> Self {
        Self::start_with_setup(session, files, executable, nonce, mode, options, |_| {})
    }
    /// The origin was minted by I2 LaunchPermit. This path owns the same
    /// listener, directory, display, worker and partial-failure cleanup as v2.
    /// Never use this constructor for an unverified remote-supplied origin.
    pub fn start_v3(
        session: SessionId,
        files: &Path,
        executable: &Path,
        permit: &eidolon_transport::linux_desktop::v3::runtime::LaunchPermit,
        options: Options,
    ) -> Self {
        let origin = permit.origin().clone();
        let nonce = permit.local_nonce().as_str();
        if origin.session() != session {
            return Self::failed_start(
                session,
                nonce,
                AppMode::LinuxDesktop,
                options.virtual_clock,
                "APP_ORIGIN_UNKNOWN",
            );
        }
        let mut current = Self::start(
            session,
            files,
            executable,
            nonce,
            AppMode::LinuxDesktop,
            options,
        );
        // No client can be accepted until the owner's first dispatch, so the
        // immutable provenance is installed before dispatch is possible.
        if !current.report.startup_failed {
            current.v3_history = Some(WindowHistory::new(origin.clone(), []));
            current.v3_origin = Some(origin);
        }
        current
    }

    fn start_with_setup(
        session: SessionId,
        files: &Path,
        executable: &Path,
        nonce: &str,
        mode: AppMode,
        options: Options,
        before_bind: impl FnOnce(&Path),
    ) -> Self {
        let Options {
            virtual_clock,
            keyboard_layout,
            pixels,
        } = options;
        let mut current = Self::unstarted(session, nonce, mode, virtual_clock);
        let setup = (|| {
            if virtual_clock && !cfg!(debug_assertions) {
                return Err("NOT_READY");
            }
            let directory = RuntimeDirectory::create_tracked(files, nonce).map_err(|failure| {
                current.startup_allocation_absent = failure.allocation_absent;
                failure.error
            })?;
            current.startup_allocation_absent = false;
            current.directory = Some(directory);
            let path = &current.directory.as_ref().unwrap().path;
            before_bind(path);
            current.listener =
                Some(UnixListener::bind(path.join("w")).map_err(|_| "LOCAL_SOCKET_FAILED")?);
            current
                .listener
                .as_ref()
                .unwrap()
                .set_nonblocking(true)
                .map_err(|_| "LOCAL_SOCKET_FAILED")?;
            current.display = Some(Display::<ProtocolState>::new().map_err(|_| "DISPLAY_CREATE")?);
            let mut protocol = ProtocolState::new_with_budget(
                &current.display.as_ref().unwrap().handle(),
                session,
                pixels,
            );
            if !virtual_clock {
                protocol.configure_keyboard(keyboard_layout)?;
            }
            current.protocol = Some(protocol);
            // This is the last fallible construction step. On synchronous failure no worker
            // or child can exist, and the partial Session still owns all earlier allocations.
            current.local = Some(LocalClient::start(executable, path)?);
            Ok(())
        })();
        if let Err(error) = setup {
            current.report.startup_failed = true;
            current.fail(error);
        }
        current
    }
    pub(crate) fn failed_start(
        session: SessionId,
        nonce: &str,
        mode: AppMode,
        virtual_clock: bool,
        error: &'static str,
    ) -> Self {
        let mut current = Self::unstarted(session, nonce, mode, virtual_clock);
        current.report.startup_failed = true;
        current.fail(error);
        current
    }
    fn unstarted(session: SessionId, nonce: &str, mode: AppMode, virtual_clock: bool) -> Self {
        Self {
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
            directory: None,
            listener: None,
            display: None,
            protocol: None,
            local: None,
            startup_allocation_absent: true,
            base: Default::default(),
            copied: 0,
            virtual_clock,
            virtual_tick: 0,
            closing: false,
            published: vec![],
            started: Instant::now(),
            workload: Default::default(),
            desktop_request: None,
            desktop_started: false,
            v3_origin: None,
            v3_history: None,
            v3_input_allowed: BTreeSet::new(),
        }
    }
    pub fn snapshot(&self) -> Report {
        self.report.clone()
    }
    pub fn layout_windows(&self) -> Vec<WindowReport> {
        let mut windows = self.report.windows.clone();
        if self.mode() == AppMode::LinuxDesktop {
            windows.retain(|window| self.report.gui_windows.contains(&window.window));
        }
        for window in &mut windows {
            if let Some(base) = self
                .report
                .workloads
                .iter()
                .find(|w| w.window == window.window)
                .and_then(|w| w.base_app_id)
            {
                window.app_id = Some(base.into());
            }
        }
        windows
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
    fn owns_window(&self, window: WindowId) -> bool {
        if self.v3_origin.is_some() {
            return self.v3_input_allowed.contains(&window);
        }
        self.mode() != AppMode::LinuxDesktop
            || (self.desktop_started && self.report.gui_windows.contains(&window))
    }

    pub fn is_v3(&self) -> bool {
        self.v3_origin.is_some()
    }

    pub fn v3_origin(&self) -> Option<&NativeOrigin> {
        self.v3_origin.as_ref()
    }

    pub fn v3_transport_started(&mut self, origin: &NativeOrigin) -> Result<(), &'static str> {
        if self.closing || self.v3_origin.as_ref() != Some(origin) {
            return Err("APP_ORIGIN_UNKNOWN");
        }
        self.v3_history.as_mut().ok_or("NOT_READY")?.started();
        Ok(())
    }

    pub fn v3_cut(&mut self) -> Result<I3WindowCut, &'static str> {
        self.v3_history.as_mut().ok_or("NOT_READY")?.cut()
    }

    pub fn v3_commit_main(&mut self, main: WindowId) -> Result<(), &'static str> {
        self.v3_history
            .as_mut()
            .ok_or("NOT_READY")?
            .commit_main(main)
    }

    /// Caller may only supply I4 current-target evidence. This method is the
    /// last barrier for all input methods including popup and IME.
    pub fn set_v3_input_allowed(&mut self, allowed: BTreeSet<WindowId>) {
        if self.v3_origin.is_none() {
            return;
        }
        let next = if self.closing || self.v3_history.as_ref().is_none_or(|h| !h.is_open()) {
            BTreeSet::new()
        } else {
            // Even a future caller supplying SDK evidence must not promote a
            // candidate, an unmapped dialog or a now-retired WindowId. The
            // compositor's admitted cut is the mandatory final intersection.
            let admitted = self
                .v3_history
                .as_mut()
                .and_then(|history| history.cut().ok())
                .map(|cut| cut.admitted)
                .unwrap_or_default();
            allowed.intersection(&admitted).copied().collect()
        };
        if self.v3_input_allowed != next {
            self.v3_input_allowed = next;
            self.reset_input();
        }
    }

    /// Native reservation と request を先に捕捉し、後の transport ACK と照合する。
    pub fn reserve_desktop(&mut self, request: CommandSeq) -> Result<(), &'static str> {
        if self.mode() != AppMode::LinuxDesktop
            || self.desktop_request.is_some()
            || self.report.forward_sent
        {
            return Err("STALE");
        }
        self.desktop_request = Some(request);
        Ok(())
    }
    pub fn focus_window(&mut self, window: WindowId) -> Result<(), &'static str> {
        if self.closing || !self.owns_window(window) {
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
        if self.closing || !self.owns_window(window) {
            return Err("STALE");
        }
        self.protocol
            .as_mut()
            .ok_or("NOT_READY")?
            .key_at_focus(window, focus_token, key)
    }
    pub fn set_ime_backend(&mut self, enabled: bool) -> Result<(), &'static str> {
        if self.closing {
            return Err("STALE");
        }
        self.protocol
            .as_mut()
            .ok_or("NOT_READY")?
            .set_ime_backend_enabled(enabled);
        Ok(())
    }
    /// 本文を含むためReport/snapshotへ格納しない。UI専用の短命な取得経路。
    pub fn ime_snapshot(&self, input_epoch: u64) -> Option<eidolon_model::ime::ImeSnapshot> {
        if self.closing {
            return None;
        }
        let snapshot = self.protocol.as_ref()?.ime_snapshot(input_epoch)?;
        if !self.owns_window(snapshot.target.window) {
            return None;
        }
        Some(snapshot)
    }
    pub fn ime(
        &mut self,
        input_epoch: u64,
        transaction: &eidolon_model::ime::ImeTransaction,
    ) -> Result<(), &'static str> {
        if self.closing || !self.owns_window(transaction.target.window) {
            return Err("STALE");
        }
        self.protocol
            .as_mut()
            .ok_or("NOT_READY")?
            .apply_ime(input_epoch, transaction)
    }
    pub fn set_visible_windows(&mut self, selected: &[(SessionId, WindowId)]) {
        self.report.visible_windows = selected
            .iter()
            .filter_map(|(session, window)| (*session == self.session()).then_some(*window))
            .collect();
    }
    pub fn close_window(&mut self, window: WindowId) -> Result<(), &'static str> {
        if self.closing || !self.owns_window(window) {
            return Err("STALE");
        }
        self.protocol
            .as_mut()
            .ok_or("NOT_READY")?
            .close_window(window)
    }
    pub fn current_window(&self, window: WindowId) -> Option<WindowReport> {
        if !self.owns_window(window) {
            return None;
        }
        self.protocol
            .as_ref()?
            .windows()
            .into_iter()
            .find(|w| w.window == window)
    }
    pub fn restore_sizes(&mut self, action: crate::workspace::Restore) -> Result<(), &'static str> {
        self.protocol.as_mut().ok_or("NOT_READY")?.restore_sizes(
            action.window,
            action.physical_size_m,
            action.logical_size,
        )
    }
    pub fn set_keyboard_layout(
        &mut self,
        layout: eidolon_model::keyboard::KeyboardLayout,
    ) -> Result<(), &'static str> {
        if self.closing {
            return Err("STALE");
        }
        self.protocol
            .as_mut()
            .ok_or("NOT_READY")?
            .change_keyboard_layout(layout)
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
        if self.closing || !self.owns_window(window) {
            return Err("STALE");
        }
        self.protocol
            .as_mut()
            .ok_or("NOT_READY")?
            .request_resize(window, size)
    }
    pub fn resize_logical_owned(
        &mut self,
        target: eidolon_model::surface::TargetKey,
        request: eidolon_model::ids::CommandSeq,
        size: LogicalSize,
    ) -> Result<(), &'static str> {
        if self.closing || target.session != self.session() || !self.owns_window(target.window) {
            return Err("STALE");
        }
        self.protocol
            .as_mut()
            .ok_or("NOT_READY")?
            .request_resize_owned(target, request, size)
    }
    pub fn set_physical_size(
        &mut self,
        window: WindowId,
        request: eidolon_model::ids::CommandSeq,
        size: eidolon_model::resize::PhysicalSize,
    ) -> Result<(), &'static str> {
        if self.closing || !self.owns_window(window) {
            return Err("STALE");
        }
        self.protocol
            .as_mut()
            .ok_or("NOT_READY")?
            .set_physical_size(window, request, size)
    }
    pub fn step_physical_size(
        &mut self,
        window: WindowId,
        request: eidolon_model::ids::CommandSeq,
        expected_command: Option<eidolon_model::ids::CommandSeq>,
        expected: eidolon_model::resize::PhysicalSize,
        step: eidolon_model::resize::PhysicalStep,
    ) -> Result<(), &'static str> {
        if self.closing || !self.owns_window(window) {
            return Err("STALE");
        }
        self.protocol
            .as_mut()
            .ok_or("NOT_READY")?
            .step_physical_size(window, request, expected_command, expected, step)
    }
    pub fn physical_size_applied(
        &mut self,
        window: WindowId,
        command: Option<eidolon_model::ids::CommandSeq>,
        size: eidolon_model::resize::PhysicalSize,
    ) -> Result<(), &'static str> {
        if self.closing || !self.owns_window(window) {
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
        if self.closing || !self.owns_window(window) {
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
        let mut windows = state.windows();
        if self.report.mode == Some(AppMode::LinuxDesktop) {
            windows.retain(|window| self.report.gui_windows.contains(&window.window));
        }
        let logical: Vec<_> = windows.iter().map(|window| window.window).collect();
        if gl.windows(session, &logical) != eidolon_model::state::Status::Accepted {
            self.fail("RENDER_BUDGET");
            return;
        }
        let mapped: Vec<_> = windows
            .iter()
            .filter(|w| {
                w.life == WindowLife::Mapped && self.report.visible_windows.contains(&w.window)
            })
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
            if window.life != WindowLife::Mapped
                || !self.report.visible_windows.contains(&window.window)
            {
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
                // producerは実Surfaceの大きさ。app logical_size/resize configureと混同しない。
                eidolon_model::window::BufferSizePx::new(t.gpu.width as u32, t.gpu.height as u32)
                    .ok()
                    .map(|size| (t.key.window, size))
            })
            .collect::<Vec<_>>();
        state.observe_producers(&producers);
        for window in &windows {
            let presented = self.report.visible_windows.contains(&window.window)
                && render.targets.iter().any(|target| {
                    target.role == eidolon_render::RenderMode::Windows
                        && target.key.session == session
                        && target.key.window == window.window
                        && target.content_frame == window.frame
                        && !target.presentation_paused
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
        if self.closing
            || self.closed()
            || self.mode() == AppMode::LinuxDesktop
            || mode == AppMode::LinuxDesktop
        {
            return Err("STALE");
        }
        self.base = self.protocol.as_ref().unwrap().counters;
        self.copied = self.base.copies;
        self.report.nonce = nonce.into();
        self.report.mode = Some(mode);
        self.report.forward_sent = false;
        self.report.verified_hashes = 0;
        self.report.remote_gui_verified = false;
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
        if let Some(remote) = forward.remote.as_ref() {
            self.workload.ingest(remote, self.started.elapsed());
        }
        if let Some(error) = forward.error {
            self.fail(error);
        } else if forward.state == "pass" {
            self.report.state = "pass";
        } else if forward.state == "running" {
            self.report.state = "running";
        }
    }
    pub fn desktop_forwarding(&mut self, forward: &eidolon_transport::linux_desktop::Report) {
        if self.closing || self.closed() || self.mode() != AppMode::LinuxDesktop {
            return;
        }
        if forward.session != Some(self.session()) || forward.nonce != self.report.nonce {
            self.fail("DESKTOP_RECEIPT_SCOPE");
            return;
        }
        // Inspection is not a launch receipt. Only the captured explicit Start request can admit a GUI.
        if let Some(request) = forward.request_id {
            if Some(request) != self.desktop_request {
                self.fail("DESKTOP_RECEIPT_SCOPE");
                return;
            }
            self.desktop_started |= forward.started;
        }
        if let Some(error) = forward.error {
            self.fail(error);
        } else if self.desktop_started
            && forward.state == "running"
            && !forward.finished
            && !forward.remote.terminal_event
            && !forward.remote.channel_closed
        {
            self.report.state = "running";
        }
    }
    #[cfg(any(test, feature = "debug-fixtures"))]
    pub fn linux_forwarding(&mut self, forward: &eidolon_transport::linux_probe::Report) {
        if self.closing
            || self.closed()
            || forward.session != Some(self.session())
            || forward.nonce != self.report.nonce
        {
            return;
        }
        let expected = self.mode().linux_gtk_case();
        if forward.gtk.as_ref().map(|gtk| gtk.case) != expected {
            // 実report前はNone。異なるcaseの結果を取り込まず、running到達後は失敗へ。
            if forward.gtk.is_some() || forward.state == "pass" {
                self.fail("LINUX_GTK_SCOPE_MISMATCH");
            }
            return;
        }
        self.report.remote_gui_verified = forward.error.is_none()
            && forward.state == "pass"
            && forward.gtk.as_ref().is_some_and(|gtk| gtk.helper_verified);
        if let Some(error) = forward.error {
            self.fail(error);
        } else if forward.state == "pass" {
            self.report.state = "pass";
        } else if forward.state == "running" {
            self.report.state = "running";
        }
    }
    pub fn fail(&mut self, error: &'static str) {
        if self.v3_origin.is_some() {
            if let Some(protocol) = self.protocol.as_mut() {
                protocol.seal_new_roles();
            }
            if let Some(history) = self.v3_history.as_mut() {
                history.seal();
            }
            self.v3_input_allowed.clear();
            self.reset_input();
        }
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
        let admitted_before = self.report.gui_windows.clone();
        self.closing |= close;
        if self.closing {
            if let Some(history) = self.v3_history.as_mut() {
                history.seal();
                self.v3_input_allowed.clear();
            }
            if self.v3_origin.is_some() {
                if let Some(protocol) = self.protocol.as_mut() {
                    protocol.seal_new_roles();
                }
            }
            self.reset_input();
            self.report.state = "closing";
            self.report.ready = false;
            if remote_gone && let Some(local) = self.local.as_ref() {
                local.request_stop();
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
        if self.mode() == AppMode::LinuxDesktop
            && admitted_before
                .iter()
                .any(|window| !self.report.gui_windows.contains(window))
        {
            // app_id / parent / map の喪失は SDK retire ACK より先に入力所有を失効させる。
            self.reset_input();
            if let Some(protocol) = self.protocol.as_ref() {
                self.report.input = protocol.input_report();
            }
        }
        if let Some(local) = self.local.as_ref() {
            self.report.local = local.snapshot();
        }
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
        if !remote_gone || self.local.as_mut().is_some_and(|local| !local.finished()) {
            return;
        }
        if let Some(local) = self.local.as_ref() {
            self.report.local = local.snapshot();
        }
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
            } else if !self.report.directory_cleaned && !self.startup_allocation_absent {
                // A collided path or allocation without a captured identity is not ours to delete.
                return Err("CLEANUP_FAILED");
            }
            self.report.directory_cleaned = true;
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
        self.report.startup_cleanup_complete = self.report.startup_failed && self.local.is_none();
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
                        let accepted = match self.v3_origin.as_ref() {
                            Some(origin) => {
                                state.accept_with_origin(&display.handle(), stream, origin)
                            }
                            None => state.accept(&display.handle(), stream),
                        };
                        match accepted {
                            Ok(_) => self.report.accepted += 1,
                            Err("RESOURCE_LIMIT") => (), // 新接続だけdrop。既存clientのSessionを失敗させない。
                            Err(error) => return Err(error),
                        }
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
        if self.v3_origin.is_some() && self.closing {
            self.report.gui_windows.clear();
            self.report.remote_gui_verified = false;
            self.v3_input_allowed.clear();
        } else if self.v3_origin.is_some() {
            let v3_result = self
                .v3_history
                .as_mut()
                .ok_or("NOT_READY")
                .and_then(|history| {
                    history
                        .observe(&state.v3_samples())
                        .and_then(|()| history.cut())
                });
            match v3_result {
                Ok(cut) => {
                    self.report.gui_windows = cut.admitted.into_iter().collect();
                    self.report.remote_gui_verified = !self.report.gui_windows.is_empty();
                }
                Err(error) => {
                    self.report.gui_windows.clear();
                    self.v3_input_allowed.clear();
                    // This function still borrows the compositor state for
                    // stats; record the fatal outcome without re-borrowing self.
                    self.report.error.get_or_insert(error);
                    self.report.state = "fail";
                    self.closing = true;
                }
            }
        } else if self.report.mode == Some(AppMode::LinuxDesktop) {
            self.report.gui_windows =
                if self.desktop_started && !self.closing && self.report.error.is_none() {
                    let windows = self
                        .report
                        .windows
                        .iter()
                        .map(|window| eidolon_model::linux_desktop::Candidate {
                            window: window.window,
                            app_id: window.app_id.as_deref(),
                            parent: window.parent,
                            life: window.life,
                        })
                        .collect::<Vec<_>>();
                    eidolon_model::linux_desktop::owned_windows(&self.report.nonce, &windows)
                } else {
                    vec![]
                };
            self.report.remote_gui_verified =
                self.desktop_started && !self.report.gui_windows.is_empty();
        }
        self.report.workloads = self.workload.sync(
            self.session(),
            &self.report.nonce,
            &self.report.windows,
            self.started.elapsed(),
            !self.closing,
        );
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
        self.report.toplevel_rejections = state.counters.toplevel_rejections;
        self.report.rejected_toplevels = state.rejected_toplevel_count();
        self.report.client_rejections = state.counters.client_rejections;
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
        self.report.surface_rejections = state
            .counters
            .surface_rejections
            .saturating_sub(self.base.surface_rejections);
        self.report.last_rejected_pool_bytes =
            (self.report.pool_rejections > 0).then_some(state.counters.last_rejected_pool_bytes);
    }
}
impl Drop for Session {
    fn drop(&mut self) {
        if let Some(local) = self.local.as_mut() {
            local.request_stop();
            while !local.finished() {
                std::thread::sleep(Duration::from_millis(5));
            }
        }
        drop(self.listener.take());
        drop(self.display.take());
        drop(self.protocol.take());
    }
}
fn named_memfds() -> Result<(usize, usize), &'static str> {
    fn owned(text: &str) -> bool {
        text.contains("memfd:waypipe")
            || text.contains("memfd:eidolon-shm-fixture")
            || text.contains("memfd:smithay-keymap")
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
    fn synchronous_desktop_start_failure_retires_before_allowing_retry() {
        use eidolon_model::ids::RuntimeHandle;
        let _guard = crate::compositor::FIXTURE_LOCK.lock().unwrap();
        let files = std::env::temp_dir().join(format!("ew-start-fail-{}", std::process::id()));
        fs::create_dir(&files).unwrap();
        let id = SessionId::new(31).unwrap();
        let nonce = "1122334455667788";
        let directory = files.join("rt").join(nonce);
        let options = || Options {
            virtual_clock: false,
            keyboard_layout: Default::default(),
            pixels: Default::default(),
        };
        let mut slots = crate::window_slots::Controller::new(RuntimeHandle::new(1).unwrap());
        slots.begin(id, nonce).unwrap();
        let lease = slots.reserve(id, CommandSeq::new(1).unwrap()).unwrap();
        let mut session = Session::start_with_setup(
            id,
            &files,
            Path::new("/missing-waypipe-startup-test"),
            nonce,
            AppMode::LinuxDesktop,
            options(),
            |directory| fs::write(directory.join("w"), b"occupied socket path").unwrap(),
        );
        assert_eq!(session.snapshot().error, Some("LOCAL_SOCKET_FAILED"));
        assert!(session.snapshot().startup_failed);
        assert!(session.local.is_none());
        slots.not_enqueued(lease);
        // Core disconnects the inspected SSH helper before allowing this owner to retire.
        session.tick(true, false);
        assert!(!session.closed());
        assert!(!session.snapshot().startup_cleanup_complete);
        assert!(directory.is_dir());
        session.tick(true, true);
        let failed = session.snapshot();
        assert!(session.closed() && failed.startup_cleanup_complete && failed.directory_cleaned);
        assert_eq!(failed.error, Some("LOCAL_SOCKET_FAILED"));
        assert_eq!((failed.local.workers, failed.local.owned_groups), (0, 0));
        assert!(!failed.local.waited); // There never was a worker or a child to wait for.
        assert_eq!(
            (failed.memfd_fds_after, failed.memfd_maps_after),
            (Some(0), Some(0))
        );
        assert!(!directory.exists());
        slots
            .observe(crate::window_slots::Observation {
                session: id,
                nonce,
                windows: &[],
                session_closed: session.closed(),
                native: &[],
                sdk: &[],
                panels: &[],
            })
            .unwrap();
        assert_eq!(slots.report().occupied, 0);
        assert!(slots.can_begin(id, nonce));
        slots.reserve(id, CommandSeq::new(2).unwrap()).unwrap();
        drop(session);
        let mut retry = Session::start(
            id,
            &files,
            Path::new("/missing-waypipe-startup-test"),
            nonce,
            AppMode::LinuxDesktop,
            options(),
        );
        assert!(
            !retry.snapshot().startup_failed && retry.local.is_some(),
            "{:?}",
            retry.snapshot()
        );
        let deadline = Instant::now() + Duration::from_secs(2);
        while !retry.closed() {
            assert!(Instant::now() < deadline);
            retry.tick(true, true);
            thread::sleep(Duration::from_millis(1));
        }
        assert!(!retry.snapshot().startup_cleanup_complete);
        drop(retry);
        fs::remove_dir_all(files).unwrap();
    }
    #[test]
    fn desktop_start_collision_cannot_claim_an_unknown_directory_was_cleaned() {
        use std::os::unix::fs::DirBuilderExt;
        let _guard = crate::compositor::FIXTURE_LOCK.lock().unwrap();
        let files = std::env::temp_dir().join(format!("ew-start-collision-{}", std::process::id()));
        fs::create_dir(&files).unwrap();
        fs::DirBuilder::new()
            .mode(0o700)
            .create(files.join("rt"))
            .unwrap();
        let nonce = "1122334455667788";
        let directory = files.join("rt").join(nonce);
        fs::create_dir(&directory).unwrap();
        fs::write(directory.join("unrelated-owner"), b"preserve").unwrap();
        let mut session = Session::start(
            SessionId::new(32).unwrap(),
            &files,
            Path::new("/unused"),
            nonce,
            AppMode::LinuxDesktop,
            Options {
                virtual_clock: false,
                keyboard_layout: Default::default(),
                pixels: Default::default(),
            },
        );
        session.tick(true, true);
        let report = session.snapshot();
        assert!(report.startup_failed);
        assert_eq!(report.error, Some("CONFIG_INVALID"));
        assert!(!session.closed() && !report.startup_cleanup_complete && !report.directory_cleaned);
        assert_eq!(
            fs::read(directory.join("unrelated-owner")).unwrap(),
            b"preserve"
        );
        drop(session);
        assert!(directory.is_dir());
        fs::remove_dir_all(files).unwrap();
    }
    #[test]
    fn rejected_desktop_setup_without_allocations_still_waits_for_remote_retirement() {
        let _guard = crate::compositor::FIXTURE_LOCK.lock().unwrap();
        let mut session = Session::failed_start(
            SessionId::new(33).unwrap(),
            "1122334455667788",
            AppMode::LinuxDesktop,
            false,
            "STALE",
        );
        session.tick(true, false);
        assert!(!session.closed());
        session.tick(true, true);
        let report = session.snapshot();
        assert!(
            session.closed()
                && report.startup_failed
                && report.startup_cleanup_complete
                && report.directory_cleaned
        );
        assert!(!report.local.waited);
        assert_eq!(report.error, Some("STALE"));
    }
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
            Options {
                virtual_clock: true,
                keyboard_layout: Default::default(),
                pixels: Default::default(),
            },
        );
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
