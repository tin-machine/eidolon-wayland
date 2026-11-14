//! renderer/SSHを所有しないWayland state。Displayのdispatchはprotocol ownerだけが行う。
use super::{
    shm,
    shm_budget::{PoolBudget, PoolLease, Usage},
};
use eidolon_model::{
    ids::{ContentSeq, SessionId, SurfaceId, WindowId},
    limits,
    linux_desktop_v3::presentation::NativeOrigin,
    pixels::{OwnedRgba, PixelBudget},
};
use smithay::{
    delegate_compositor, delegate_output, delegate_seat,
    input::{Seat, SeatHandler, SeatState, pointer::CursorImageStatus},
    output::{Mode, Output, PhysicalProperties, Scale, Subpixel},
    reexports::wayland_server::{
        Client, DataInit, Dispatch, DisplayHandle, Resource, WEnum,
        backend::{ClientData, ClientId, DisconnectReason, GlobalId, ObjectId},
        delegate_dispatch, delegate_global_dispatch,
        protocol::{
            wl_buffer::WlBuffer,
            wl_compositor::WlCompositor,
            wl_data_device::{self, WlDataDevice},
            wl_data_device_manager::{self, WlDataDeviceManager},
            wl_output::WlOutput,
            wl_seat::WlSeat,
            wl_shm::{self, WlShm},
            wl_shm_pool::{self, WlShmPool},
            wl_surface::{self, WlSurface},
        },
    },
    utils::{Serial, Transform},
    wayland::{
        buffer::BufferHandler,
        compositor::{
            BufferAssignment, Cacheable, CompositorClientState, CompositorHandler, CompositorState,
            SurfaceAttributes, TraversalAction, add_pre_commit_hook, is_sync_subsurface,
            with_states, with_surface_tree_upward,
        },
        output::OutputHandler,
        selection::{
            SelectionHandler,
            data_device::{
                ClientDndGrabHandler, DataDeviceHandler, DataDeviceState, ServerDndGrabHandler,
            },
        },
        shell::xdg::{
            Configure, PopupSurface, PositionerState, ToplevelSurface, XdgShellHandler,
            XdgShellState, XdgToplevelSurfaceData,
        },
        shm::{BufferData, ShmBufferUserData, ShmHandler, ShmPoolUserData, ShmState},
    },
};
use std::{
    collections::{HashMap, HashSet},
    num::NonZeroUsize,
    os::fd::OwnedFd,
    sync::{Arc, Weak},
};

#[derive(Default)]
pub struct ClientState {
    compositor: CompositorClientState,
    /// Set exactly once at native accept. A v2 or Termux client has no v3 origin.
    pub(crate) origin: Option<NativeOrigin>,
}
impl ClientData for ClientState {
    fn initialized(&self, _: ClientId) {}
    fn disconnected(&self, _: ClientId, _: DisconnectReason) {}
}

#[derive(Clone, Copy, Debug, Default)]
pub struct Counters {
    pub commits: u64,
    pub configures: u64,
    pub acks: u64,
    pub copies: u64,
    pub releases: u64,
    pub callbacks: u64,
    pub bad_maps: u64,
    pub cancellations: u64,
    pub maps: u64,
    pub unmaps: u64,
    pub pool_rejections: u64,
    pub surface_rejections: u64,
    pub last_rejected_pool_bytes: usize,
    pub toplevel_rejections: u64,
    pub client_rejections: u64,
}

/// SurfaceAttributesと同じtransaction/cacheを通る使用token。未適用の同期commitも保持する。
/// None=変更なし、Some(None)=detach、Some(Some(token))=新buffer。
#[derive(Default)]
struct BufferUse {
    update: Option<Option<Arc<()>>>,
}
impl Cacheable for BufferUse {
    fn commit(&mut self, _: &DisplayHandle) -> Self {
        Self {
            update: self.update.take(),
        }
    }
    fn merge_into(self, into: &mut Self, _: &DisplayHandle) {
        if self.update.is_some() {
            into.update = self.update;
        }
    }
}

#[path = "clipboard.rs"]
mod clipboard;
#[path = "ime.rs"]
mod ime;
#[cfg(any(test, feature = "debug-fixtures"))]
#[path = "ime_checks.rs"]
pub mod ime_checks;
#[path = "input.rs"]
mod input;
#[path = "pointer.rs"]
mod pointer;
#[path = "popup.rs"]
mod popup;
#[path = "positioner.rs"]
mod positioner;
/// 所有済みsnapshot以外はprotocol ownerから持ち出さない。
#[path = "snapshot.rs"]
mod snapshot;
pub use ime::ImeReport;
pub use input::InputReport;
pub(crate) use input::monotonic_time_ms;
pub use pointer::PointerReport;
pub use snapshot::WindowReport;

pub struct ProtocolState {
    display_handle: DisplayHandle,
    compositor: CompositorState,
    shell: XdgShellState,
    shm: ShmState,
    seats: SeatState<Self>,
    _seat: Seat<Self>,
    input: input::InputState,
    ime: ime::ImeState,
    data_device: DataDeviceState,
    output: Output,
    _globals: Vec<GlobalId>,
    surfaces: Vec<WlSurface>,
    toplevels: HashMap<ObjectId, snapshot::WindowRecord>,
    // wl_surface全体のSURFACES上限内。拒否roleをWindowId/描画へ登録しない。
    rejected_toplevels: HashSet<ObjectId>,
    popups: Vec<PopupSurface>,
    dismissed_popups: HashSet<ObjectId>,
    popup_grabs: Vec<ObjectId>,
    popup_focused: Option<WlSurface>,
    session: SessionId,
    next_window: Option<WindowId>,
    next_surface: Option<SurfaceId>,
    next_content: Option<ContentSeq>,
    surface_meta: HashMap<ObjectId, snapshot::SurfaceMeta>,
    pixels: HashMap<ObjectId, Arc<OwnedRgba>>,
    pixel_budget: PixelBudget,
    pools: PoolBudget,
    pool_leases: HashMap<ObjectId, Weak<PoolLease>>,
    release_queue: HashMap<ObjectId, WlBuffer>,
    buffer_uses: HashMap<ObjectId, Weak<()>>,
    data_devices: HashMap<ObjectId, WlDataDevice>,
    clipboard: clipboard::Clipboard,
    pub counters: Counters,
}

/// client受入前だけに使う。元handlerには非公開seat/output dataも保持される。
fn cap<I: Resource + 'static>(dh: &DisplayHandle, old: GlobalId, version: u32) -> GlobalId {
    let backend = dh.backend_handle();
    let handler = backend
        .get_global_handler::<ProtocolState>(old.clone())
        .expect("new global");
    backend.remove_global::<ProtocolState>(old);
    backend.create_global::<ProtocolState>(I::interface(), version, handler)
}

impl ProtocolState {
    pub fn new(dh: &DisplayHandle, session: SessionId) -> Self {
        Self::new_with_budget(dh, session, PixelBudget::default())
    }
    pub fn new_with_budget(
        dh: &DisplayHandle,
        session: SessionId,
        pixel_budget: PixelBudget,
    ) -> Self {
        let compositor = CompositorState::new::<Self>(dh);
        let shell = XdgShellState::new::<Self>(dh);
        let shm = ShmState::new::<Self>(dh, []);
        let mut seats = SeatState::new();
        let seat = seats.new_wl_seat(dh, "eidolon");
        let data_device = DataDeviceState::new::<Self>(dh);
        let output = Output::new(
            "Eidolon-1".into(),
            PhysicalProperties {
                size: (0, 0).into(),
                subpixel: Subpixel::Unknown,
                make: "Eidolon".into(),
                model: "Virtual".into(),
            },
        );
        let mode = Mode {
            size: (1024, 768).into(),
            refresh: 60000,
        };
        output.change_current_state(
            Some(mode),
            Some(Transform::Normal),
            Some(Scale::Integer(1)),
            Some((0, 0).into()),
        );
        output.set_preferred(mode);
        let output_global = output.create_global::<Self>(dh);
        let globals = vec![
            cap::<WlCompositor>(dh, compositor.compositor_global(), 4),
            compositor.subcompositor_global(),
            cap::<WlShm>(dh, shm.global(), 1),
            cap::<WlOutput>(dh, output_global, 3),
            cap::<WlSeat>(dh, seat.global().expect("Wayland seat"), 5),
            cap::<smithay::reexports::wayland_protocols::xdg::shell::server::xdg_wm_base::XdgWmBase>(
                dh,
                shell.global(),
                1,
            ),
            data_device.global(),
            ime::create_global(dh),
        ];
        Self {
            display_handle: dh.clone(),
            compositor,
            shell,
            shm,
            seats,
            _seat: seat,
            input: Default::default(),
            ime: Default::default(),
            data_device,
            output,
            _globals: globals,
            surfaces: vec![],
            toplevels: HashMap::new(),
            rejected_toplevels: HashSet::new(),
            popups: vec![],
            dismissed_popups: HashSet::new(),
            popup_grabs: vec![],
            popup_focused: None,
            session,
            next_window: WindowId::new(1).ok(),
            next_surface: SurfaceId::new(1).ok(),
            next_content: ContentSeq::new(1).ok(),
            surface_meta: HashMap::new(),
            pixels: HashMap::new(),
            pixel_budget,
            pools: PoolBudget::default(),
            pool_leases: HashMap::new(),
            release_queue: HashMap::new(),
            buffer_uses: HashMap::new(),
            data_devices: HashMap::new(),
            clipboard: Default::default(),
            counters: Counters::default(),
        }
    }

    pub fn pixel_bytes(&self) -> usize {
        self.pixel_budget.used_bytes()
    }
    pub fn accept(
        &mut self,
        dh: &DisplayHandle,
        stream: std::os::unix::net::UnixStream,
    ) -> Result<Client, &'static str> {
        self.accept_captured(dh, stream, None)
    }

    /// Only the owned v3 Session may call this route with its captured I2
    /// LaunchPermit origin. Remote app_id/title/PID/UID cannot supply origin.
    pub fn accept_with_origin(
        &mut self,
        dh: &DisplayHandle,
        stream: std::os::unix::net::UnixStream,
        origin: &NativeOrigin,
    ) -> Result<Client, &'static str> {
        if origin.session() != self.session {
            return Err("APP_ORIGIN_UNKNOWN");
        }
        self.accept_captured(dh, stream, Some(origin.clone()))
    }

    fn accept_captured(
        &mut self,
        dh: &DisplayHandle,
        stream: std::os::unix::net::UnixStream,
        origin: Option<NativeOrigin>,
    ) -> Result<Client, &'static str> {
        let mut clients = 0;
        dh.backend_handle().with_all_clients(|_| clients += 1);
        if clients >= limits::CLIENTS {
            self.counters.client_rejections += 1;
            return Err("RESOURCE_LIMIT");
        }
        dh.clone()
            .insert_client(
                stream,
                Arc::new(ClientState {
                    compositor: CompositorClientState::default(),
                    origin,
                }),
            )
            .map_err(|_| "CLIENT_ACCEPT")
    }
    pub fn pool_usage(&self) -> Usage {
        self.pools.usage()
    }
    pub fn surface_count(&self) -> usize {
        self.surfaces.len()
    }
    pub fn rejected_toplevel_count(&self) -> usize {
        self.rejected_toplevels.len()
    }
    #[cfg(any(test, feature = "debug-fixtures"))]
    pub(super) fn surfaces_for_check(&self) -> Vec<WlSurface> {
        self.surfaces.clone()
    }
    pub fn image(&self, surface: &WlSurface) -> Option<&Arc<OwnedRgba>> {
        self.pixels.get(&surface.id())
    }

    /// dispatch後に呼ぶ。未commitの別surfaceも同bufferを保持していればreleaseを遅らせる。
    pub fn finish_dispatch(&mut self, dh: &DisplayHandle) {
        self.refresh_input_focus();
        self.refresh_popup_focus();
        self.clipboard_tick(dh);
        self.pool_leases.retain(|_, lease| lease.strong_count() > 0);
        let mut attached = HashSet::new();
        for surface in &self.surfaces {
            with_states(surface, |states| {
                let mut attrs = states.cached_state.get::<SurfaceAttributes>();
                if let Some(BufferAssignment::NewBuffer(buffer)) = &attrs.current().buffer {
                    attached.insert(buffer.id());
                }
                if let Some(BufferAssignment::NewBuffer(buffer)) = &attrs.pending().buffer {
                    attached.insert(buffer.id());
                }
            });
        }
        self.release_queue.retain(|id, buffer| {
            if attached.contains(id)
                || self
                    .buffer_uses
                    .get(id)
                    .is_some_and(|token| token.strong_count() > 0)
            {
                return true;
            }
            if buffer.is_alive() {
                buffer.release();
                self.counters.releases += 1;
            }
            false
        });
        self.buffer_uses
            .retain(|id, token| token.strong_count() > 0 || self.release_queue.contains_key(id));
        self.data_devices.retain(|_, device| device.is_alive());
    }

    /// visible/presentが確認できたsurfaceだけに呼ぶ。protocol fixtureは明示的な仮想clockを使う。
    pub(super) fn complete_frame(&mut self, surface: &WlSurface, time_ms: u32) {
        if self.pixels.contains_key(&surface.id()) {
            with_states(surface, |states| {
                for callback in states
                    .cached_state
                    .get::<SurfaceAttributes>()
                    .current()
                    .frame_callbacks
                    .drain(..)
                {
                    callback.done(time_ms);
                    self.counters.callbacks += 1;
                }
            });
        }
    }

    fn in_rejected_toplevel(&self, surface: &WlSurface) -> bool {
        let mut current = surface.clone();
        let mut seen = HashSet::new();
        for _ in 0..=limits::SURFACES {
            if !seen.insert(current.id()) {
                return false;
            }
            if self.rejected_toplevels.contains(&current.id()) {
                return true;
            }
            if let Some(parent) = smithay::wayland::compositor::get_parent(&current) {
                current = parent;
            } else if let Some(parent) = self
                .popups
                .iter()
                .find(|p| p.wl_surface() == &current)
                .and_then(|p| p.get_parent_surface())
            {
                current = parent;
            } else {
                return false;
            }
        }
        false
    }
    /// 拒否roleへ交差したbufferをcopyせず解放する。他surface/cacheの使用tokenは維持する。
    fn discard_committed(&mut self, surface: &WlSurface) {
        let assignment = with_states(surface, |states| {
            let mut cached = states.cached_state.get::<SurfaceAttributes>();
            let attrs = cached.current();
            attrs.damage.clear();
            attrs.buffer_delta.take();
            attrs.buffer.take()
        });
        let changed = assignment.is_some();
        if let Some(BufferAssignment::NewBuffer(buffer)) = assignment {
            self.release_queue.insert(buffer.id(), buffer);
        }
        if changed {
            with_states(surface, |states| {
                states
                    .cached_state
                    .get::<BufferUse>()
                    .current()
                    .update
                    .take();
            });
        }
        self.pixels.remove(&surface.id());
        self.output.leave(surface);
    }

    fn copy_committed(&mut self, surface: &WlSurface) {
        let assignment = with_states(surface, |states| {
            let mut cached = states.cached_state.get::<SurfaceAttributes>();
            let attrs = cached.current();
            // owned画像は全体copyするため、damageを次frameへ蓄積しない。
            attrs.damage.clear();
            attrs.buffer_delta.take();
            attrs.buffer.take()
        });
        let changed = assignment.is_some();
        match assignment {
            Some(BufferAssignment::NewBuffer(buffer)) => {
                match shm::copy_buffer(&buffer, &self.pixel_budget) {
                    Ok(pixels) => {
                        let new = self.pixels.insert(surface.id(), pixels).is_none();
                        if new {
                            self.output.enter(surface);
                        }
                        self.counters.copies += 1;
                        self.update_surface_metadata(surface, true);
                    }
                    Err(shm::CopyError::BadMap) => {
                        self.counters.bad_maps += 1;
                    }
                    Err(_) => {
                        surface.post_error(wl_surface::Error::InvalidSize, "SHM buffer rejected")
                    }
                }
                self.release_queue.insert(buffer.id(), buffer);
            }
            Some(BufferAssignment::Removed) => {
                self.reset_unmapped_toplevel(surface);
                self.pixels.remove(&surface.id());
                self.output.leave(surface);
            }
            None => {
                self.update_surface_metadata(surface, false);
            }
        }
        if changed {
            // 実copyの終了後だけ、このsurfaceのcurrent使用tokenを外す。
            // 他surface・未適用cacheのtokenが残ればfinish_dispatchはreleaseしない。
            with_states(surface, |states| {
                states
                    .cached_state
                    .get::<BufferUse>()
                    .current()
                    .update
                    .take();
            });
        }
    }
}

impl CompositorHandler for ProtocolState {
    fn compositor_state(&mut self) -> &mut CompositorState {
        &mut self.compositor
    }
    fn client_compositor_state<'a>(&self, client: &'a Client) -> &'a CompositorClientState {
        &client
            .get_data::<ClientState>()
            .expect("accepted client data")
            .compositor
    }
    fn new_surface(&mut self, surface: &WlSurface) {
        if self.surfaces.len() >= limits::SURFACES {
            self.counters.surface_rejections += 1;
            surface.post_error(wl_surface::Error::InvalidSize, "surface budget exceeded");
        } else {
            if !self.register_surface(surface) {
                return;
            }
            self.surfaces.push(surface.clone());
            add_pre_commit_hook::<Self, _>(surface, |state, _, surface| {
                let pending = with_states(surface, |states| {
                    match &states
                        .cached_state
                        .get::<SurfaceAttributes>()
                        .pending()
                        .buffer
                    {
                        Some(BufferAssignment::NewBuffer(buffer)) => Some(Some(buffer.id())),
                        Some(BufferAssignment::Removed) => Some(None),
                        None => None,
                    }
                });
                if let Some(pending) = pending {
                    let token = pending.map(|id| {
                        state
                            .buffer_uses
                            .get(&id)
                            .and_then(Weak::upgrade)
                            .unwrap_or_else(|| {
                                let token = Arc::new(());
                                state.buffer_uses.insert(id, Arc::downgrade(&token));
                                token
                            })
                    });
                    with_states(surface, |states| {
                        states.cached_state.get::<BufferUse>().pending().update = Some(token);
                    });
                }
            });
        }
    }
    fn commit(&mut self, surface: &WlSurface) {
        self.counters.commits += 1;
        if self.in_rejected_toplevel(surface) {
            let mut ignored = vec![];
            with_surface_tree_upward(
                surface,
                (),
                |_, _, _| TraversalAction::DoChildren(()),
                |surface, _, _| ignored.push(surface.clone()),
                |_, _, _| true,
            );
            for surface in ignored {
                self.discard_committed(&surface);
            }
            return;
        }
        if !self.prepare_role_commit(surface) {
            return;
        }
        if is_sync_subsurface(surface) {
            return;
        }
        let mut applied = vec![];
        with_surface_tree_upward(
            surface,
            (),
            |_, _, _| TraversalAction::DoChildren(()),
            |surface, _, _| applied.push(surface.clone()),
            |_, _, _| true,
        );
        for surface in applied {
            self.copy_committed(&surface);
        }
        if let Some(root) = self.root_for_surface(surface) {
            self.refresh_window(&root);
        }
    }
    fn destroyed(&mut self, surface: &WlSurface) {
        self.ime_surface_destroyed(surface);
        let window = self
            .surface_meta
            .get(&surface.id())
            .and_then(|meta| meta.window);
        self.rejected_toplevels.remove(&surface.id());
        self.forget_toplevel(surface);
        self.forget_popup(surface);
        self.surface_meta.remove(&surface.id());
        self.popups.retain(|popup| popup.wl_surface() != surface);
        self.dismissed_popups.remove(&surface.id());
        self.pixels.remove(&surface.id());
        self.surfaces.retain(|s| s != surface);
        self.output.leave(surface);
        if let Some(window) = window {
            let root = self
                .toplevels
                .iter()
                .find(|(_, record)| record.id == window)
                .map(|(id, _)| id.clone());
            if let Some(root) = root {
                self.refresh_window(&root);
            }
        }
    }
}

fn limit_text(value: &mut Option<String>) {
    if let Some(value) = value {
        let mut end = value.len().min(512);
        while !value.is_char_boundary(end) {
            end -= 1;
        }
        value.truncate(end);
    }
}
impl XdgShellHandler for ProtocolState {
    fn xdg_shell_state(&mut self) -> &mut XdgShellState {
        &mut self.shell
    }
    fn new_toplevel(&mut self, toplevel: ToplevelSurface) {
        // surface作成で既に拒否したobjectを別の拒否表へ蓄積しない。
        if !self.surface_meta.contains_key(&toplevel.wl_surface().id()) {
            return;
        }
        if self.toplevels.len() >= limits::TOPLEVELS {
            // protocol errorは同じfoot serverの既存窓も切る。新しい余剰roleだけ閉じる。
            // 拒否後のin-flight commitもcopyしない。clientが従わなくてもsurface予算は不変。
            self.rejected_toplevels.insert(toplevel.wl_surface().id());
            self.counters.toplevel_rejections += 1;
            toplevel.send_close();
        } else {
            self.register_toplevel(toplevel);
        }
    }
    fn new_popup(&mut self, popup: PopupSurface, _: PositionerState) {
        if popup.get_parent_surface().is_none() {
            popup
                .wl_surface()
                .post_error(wl_surface::Error::InvalidSize, "popup parent required");
        } else {
            let rejected = self.in_rejected_toplevel(popup.get_parent_surface().as_ref().unwrap());
            if rejected {
                self.dismissed_popups.insert(popup.wl_surface().id());
                popup.send_popup_done();
            }
            self.popups.push(popup);
        }
    }
    fn grab(&mut self, popup: PopupSurface, seat: WlSeat, serial: Serial) {
        self.grab_popup(popup, seat, serial);
    }
    fn reposition_request(&mut self, _: PopupSurface, _: PositionerState, _: u32) {}
    fn ack_configure(&mut self, surface: WlSurface, configure: Configure) {
        self.counters.acks += 1;
        if let Configure::Toplevel(configure) = configure
            && let Some(window) = self.toplevels.get_mut(&surface.id())
        {
            window.resize.acknowledge(configure.serial.into());
        }
        self.role_acked(&surface);
    }
    fn title_changed(&mut self, surface: ToplevelSurface) {
        with_states(surface.wl_surface(), |states| {
            limit_text(
                &mut states
                    .data_map
                    .get::<XdgToplevelSurfaceData>()
                    .unwrap()
                    .lock()
                    .unwrap()
                    .title,
            )
        });
    }
    fn app_id_changed(&mut self, surface: ToplevelSurface) {
        with_states(surface.wl_surface(), |states| {
            limit_text(
                &mut states
                    .data_map
                    .get::<XdgToplevelSurfaceData>()
                    .unwrap()
                    .lock()
                    .unwrap()
                    .app_id,
            )
        });
    }
    fn toplevel_destroyed(&mut self, surface: ToplevelSurface) {
        self.rejected_toplevels.remove(&surface.wl_surface().id());
        self.forget_toplevel(surface.wl_surface());
    }
    fn popup_destroyed(&mut self, surface: PopupSurface) {
        self.forget_popup(surface.wl_surface());
    }
}
impl OutputHandler for ProtocolState {}
impl SeatHandler for ProtocolState {
    type KeyboardFocus = WlSurface;
    type PointerFocus = WlSurface;
    type TouchFocus = WlSurface;
    fn seat_state(&mut self) -> &mut SeatState<Self> {
        &mut self.seats
    }
    fn focus_changed(&mut self, _: &Seat<Self>, focus: Option<&WlSurface>) {
        self.ime_new_focus(focus);
        self.clipboard_new_focus(focus);
        self.clipboard_focus(&self.display_handle.clone());
    }
    fn cursor_image(&mut self, _: &Seat<Self>, _: CursorImageStatus) {}
}
impl ShmHandler for ProtocolState {
    fn shm_state(&self) -> &ShmState {
        &self.shm
    }
}
impl BufferHandler for ProtocolState {
    fn buffer_destroyed(&mut self, _: &WlBuffer) {}
}
impl SelectionHandler for ProtocolState {
    type SelectionUserData = ();
}
impl DataDeviceHandler for ProtocolState {
    fn data_device_state(&self) -> &DataDeviceState {
        &self.data_device
    }
}
impl ClientDndGrabHandler for ProtocolState {}
impl ServerDndGrabHandler for ProtocolState {
    fn send(&mut self, _: String, _: OwnedFd, _: Seat<Self>) {}
}

impl Dispatch<WlShm, ()> for ProtocolState {
    fn request(
        state: &mut Self,
        client: &Client,
        shm: &WlShm,
        request: wl_shm::Request,
        _: &(),
        _: &DisplayHandle,
        data_init: &mut DataInit<'_, Self>,
    ) {
        if let wl_shm::Request::CreatePool { id, fd, size } = request {
            let Some(size) = usize::try_from(size).ok().and_then(NonZeroUsize::new) else {
                shm.post_error(wl_shm::Error::InvalidStride, "invalid SHM pool size");
                return;
            };
            let Ok(lease) = state.pools.reserve(client.id(), size.get()) else {
                state.counters.pool_rejections += 1;
                state.counters.last_rejected_pool_bytes = size.get();
                shm.post_error(wl_shm::Error::InvalidFd, "SHM pool budget exceeded");
                return;
            };
            let weak = Arc::downgrade(&lease);
            match ShmPoolUserData::new_with_drop_guard(fd, size, Box::new(lease)) {
                Ok(data) => {
                    let pool = data_init.init(id, data);
                    state.pool_leases.insert(pool.id(), weak);
                }
                Err(_) => shm.post_error(wl_shm::Error::InvalidFd, "SHM pool map failed"),
            }
        }
    }
}
impl Dispatch<WlShmPool, ShmPoolUserData> for ProtocolState {
    fn request(
        state: &mut Self,
        client: &Client,
        pool: &WlShmPool,
        request: wl_shm_pool::Request,
        data: &ShmPoolUserData,
        dh: &DisplayHandle,
        init: &mut DataInit<'_, Self>,
    ) {
        match &request {
            wl_shm_pool::Request::CreateBuffer {
                offset,
                width,
                height,
                stride,
                format,
                ..
            } => {
                let valid = match format {
                    WEnum::Value(format) => shm::validate_layout(
                        BufferData {
                            offset: *offset,
                            width: *width,
                            height: *height,
                            stride: *stride,
                            format: *format,
                        },
                        data.size(),
                    ),
                    WEnum::Unknown(_) => Err(shm::CopyError::UnsupportedFormat),
                };
                if let Err(error) = valid {
                    // requestのNewは未初期化のままでも、creating objectへのerrorでclientを終了する。
                    let code = if error == shm::CopyError::UnsupportedFormat {
                        wl_shm::Error::InvalidFormat
                    } else {
                        wl_shm::Error::InvalidStride
                    };
                    pool.post_error(code, "invalid SHM buffer layout");
                    return;
                }
            }
            wl_shm_pool::Request::Resize { size } => {
                let result = usize::try_from(*size)
                    .ok()
                    .filter(|size| *size > 0)
                    .and_then(|size| {
                        state
                            .pool_leases
                            .get(&pool.id())?
                            .upgrade()?
                            .grow(size)
                            .ok()
                    });
                if result.is_none() {
                    pool.post_error(wl_shm::Error::InvalidFd, "SHM resize rejected");
                    return;
                }
            }
            _ => (),
        }
        <ShmState as Dispatch<WlShmPool, ShmPoolUserData, Self>>::request(
            state, client, pool, request, data, dh, init,
        );
    }
    fn destroyed(state: &mut Self, _: ClientId, pool: &WlShmPool, _: &ShmPoolUserData) {
        state.pool_leases.remove(&pool.id());
    }
}

// DataDeviceStateのsource作成delegateが要求するuserdata型にも対応するが、
// 製品deviceは下のmanagerからunit userdataで生成し、Seatとの強参照循環を作らない。
impl<U: Send + Sync + 'static> Dispatch<WlDataDevice, U> for ProtocolState {
    fn request(
        state: &mut Self,
        client: &Client,
        device: &WlDataDevice,
        request: wl_data_device::Request,
        _: &U,
        dh: &DisplayHandle,
        _: &mut DataInit<'_, Self>,
    ) {
        match request {
            wl_data_device::Request::SetSelection { source, serial } => {
                let valid = state.valid_input_serial(serial, client, false)
                    && source.as_ref().is_none_or(|s| {
                        s.is_alive()
                            && s.client().is_some_and(|c| c.id() == client.id())
                            && smithay::wayland::selection::data_device::with_source_metadata(
                                s,
                                |m| !m.mime_types.is_empty(),
                            )
                            .unwrap_or(false)
                    });
                if valid {
                    state.set_clipboard(dh, source);
                } else {
                    if let Some(source) = source {
                        source.cancelled();
                        state.counters.cancellations += 1;
                    }
                    device.selection(None);
                }
            }
            wl_data_device::Request::StartDrag { source, .. } => {
                if let Some(source) = source {
                    source.cancelled();
                    state.counters.cancellations += 1;
                }
                device.selection(None);
            }
            wl_data_device::Request::Release => {
                state.data_devices.remove(&device.id());
            }
            _ => unreachable!(),
        }
    }
    fn destroyed(state: &mut Self, _: ClientId, device: &WlDataDevice, _: &U) {
        state.data_devices.remove(&device.id());
    }
}

impl Dispatch<WlDataDeviceManager, ()> for ProtocolState {
    fn request(
        state: &mut Self,
        client: &Client,
        manager: &WlDataDeviceManager,
        request: wl_data_device_manager::Request,
        data: &(),
        dh: &DisplayHandle,
        init: &mut DataInit<'_, Self>,
    ) {
        if let wl_data_device_manager::Request::GetDataDevice { id, seat } = request {
            if !state._seat.owns(&seat) {
                init.post_error(id, 0u32, "unmanaged seat");
                return;
            }
            // selectionは製品側で実装済み。Smithay 0.7のSeatDataに二重登録すると
            // releaseなしの切断でdevice→seat→deviceが残り、keymap FDも回収されない。
            let device = init.init(id, ());
            state.advertise_clipboard(dh, &device);
            state.data_devices.insert(device.id(), device);
        } else {
            <DataDeviceState as Dispatch<WlDataDeviceManager, (), Self>>::request(
                state, client, manager, request, data, dh, init,
            );
        }
    }
}

delegate_compositor!(ProtocolState);
use smithay::reexports::wayland_protocols::xdg::shell::server::{
    xdg_popup::XdgPopup, xdg_surface::XdgSurface, xdg_toplevel::XdgToplevel, xdg_wm_base::XdgWmBase,
};
use smithay::wayland::shell::xdg::{
    XdgShellSurfaceUserData, XdgSurfaceUserData, XdgWmBaseUserData,
};
delegate_global_dispatch!(ProtocolState: [XdgWmBase: ()] => XdgShellState);
delegate_dispatch!(ProtocolState: [XdgWmBase: XdgWmBaseUserData] => XdgShellState);
delegate_dispatch!(ProtocolState: [XdgSurface: XdgSurfaceUserData] => XdgShellState);
delegate_dispatch!(ProtocolState: [XdgPopup: XdgShellSurfaceUserData] => XdgShellState);
delegate_dispatch!(ProtocolState: [XdgToplevel: XdgShellSurfaceUserData] => XdgShellState);
delegate_output!(ProtocolState);
delegate_seat!(ProtocolState);
delegate_global_dispatch!(ProtocolState: [WlShm: ()] => ShmState);
delegate_dispatch!(ProtocolState: [WlBuffer: ShmBufferUserData] => ShmState);
delegate_global_dispatch!(ProtocolState: [WlDataDeviceManager: ()] => DataDeviceState);

#[cfg(test)]
mod v3_accept_tests {
    use super::*;
    use std::os::unix::net::UnixStream;

    fn captured_fixture_origin() -> NativeOrigin {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../scripts/tests/fixtures/linux-desktop-v3-contract.json"
        ))
        .unwrap();
        let raw = fixture["records"]
            .as_array()
            .unwrap()
            .iter()
            .find(|record| record["id"] == "origin")
            .unwrap()["raw"]
            .as_str()
            .unwrap();
        NativeOrigin::parse(raw.as_bytes()).unwrap()
    }

    #[test]
    fn actual_wayland_client_accept_captures_only_v3_origin() {
        let origin = captured_fixture_origin();
        let display = smithay::reexports::wayland_server::Display::<ProtocolState>::new().unwrap();
        let handle = display.handle();
        let mut protocol = ProtocolState::new(&handle, origin.session());
        let (server, _remote) = UnixStream::pair().unwrap();
        let verified = protocol
            .accept_with_origin(&handle, server, &origin)
            .unwrap();
        assert!(verified.get_data::<ClientState>().unwrap().origin.as_ref() == Some(&origin));

        // Same Linux uid and protocol handler do not grant the other client
        // the verified origin. v2/Termux accepts remain untagged.
        let (server, _termux) = UnixStream::pair().unwrap();
        let unverified = protocol.accept(&handle, server).unwrap();
        assert!(
            unverified
                .get_data::<ClientState>()
                .unwrap()
                .origin
                .is_none()
        );
    }

    #[test]
    fn foreign_session_origin_is_not_assigned_at_accept() {
        let origin = captured_fixture_origin();
        let session = SessionId::new(origin.session().get() + 1).unwrap();
        let display = smithay::reexports::wayland_server::Display::<ProtocolState>::new().unwrap();
        let handle = display.handle();
        let mut protocol = ProtocolState::new(&handle, session);
        let (server, _remote) = UnixStream::pair().unwrap();
        assert!(
            protocol
                .accept_with_origin(&handle, server, &origin)
                .is_err()
        );
    }
}
