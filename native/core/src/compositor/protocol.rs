//! renderer/SSHを所有しないWayland state。Displayのdispatchはprotocol ownerだけが行う。
use super::{
    shm,
    shm_budget::{PoolBudget, PoolLease, Usage},
};
use eidolon_model::{
    limits,
    pixels::{OwnedRgba, PixelBudget},
};
use smithay::{
    delegate_compositor, delegate_output, delegate_seat, delegate_xdg_shell,
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
            wl_data_device_manager::WlDataDeviceManager,
            wl_data_source::WlDataSource,
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
                ClientDndGrabHandler, DataDeviceHandler, DataDeviceState, DataDeviceUserData,
                DataSourceUserData, ServerDndGrabHandler,
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

/// 所有済みsnapshot以外はprotocol ownerから持ち出さない。
pub struct ProtocolState {
    compositor: CompositorState,
    shell: XdgShellState,
    shm: ShmState,
    seats: SeatState<Self>,
    _seat: Seat<Self>,
    data_device: DataDeviceState,
    output: Output,
    _globals: Vec<GlobalId>,
    surfaces: Vec<WlSurface>,
    toplevels: HashMap<ObjectId, ToplevelSurface>,
    pixels: HashMap<ObjectId, Arc<OwnedRgba>>,
    pixel_budget: PixelBudget,
    pools: PoolBudget,
    pool_leases: HashMap<ObjectId, Weak<PoolLease>>,
    release_queue: HashMap<ObjectId, WlBuffer>,
    buffer_uses: HashMap<ObjectId, Weak<()>>,
    data_devices: HashMap<ObjectId, WlDataDevice>,
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
    pub fn new(dh: &DisplayHandle) -> Self {
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
        ];
        Self {
            compositor,
            shell,
            shm,
            seats,
            _seat: seat,
            data_device,
            output,
            _globals: globals,
            surfaces: vec![],
            toplevels: HashMap::new(),
            pixels: HashMap::new(),
            pixel_budget: PixelBudget::default(),
            pools: PoolBudget::default(),
            pool_leases: HashMap::new(),
            release_queue: HashMap::new(),
            buffer_uses: HashMap::new(),
            data_devices: HashMap::new(),
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
        let mut clients = 0;
        dh.backend_handle().with_all_clients(|_| clients += 1);
        if clients >= limits::CLIENTS {
            return Err("RESOURCE_LIMIT");
        }
        dh.clone()
            .insert_client(stream, Arc::new(ClientState::default()))
            .map_err(|_| "CLIENT_ACCEPT")
    }
    pub fn pool_usage(&self) -> Usage {
        self.pools.usage()
    }
    pub fn surface_count(&self) -> usize {
        self.surfaces.len()
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
        let backend = dh.backend_handle();
        let mut clients = vec![];
        backend.with_all_clients(|client| clients.push(client));
        for client in clients {
            let mut ids = vec![];
            let _ = backend.with_all_objects_for(client, |id| {
                if id.interface().name == "wl_data_device" {
                    ids.push(id);
                }
            });
            for id in ids {
                if !self.data_devices.contains_key(&id)
                    && let Ok(device) = WlDataDevice::from_id(dh, id.clone())
                {
                    device.selection(None);
                    self.data_devices.insert(id, device);
                }
            }
        }
    }

    /// visible/presentが確認できたsurfaceだけに呼ぶ。protocol fixtureは明示的な仮想clockを使う。
    pub fn complete_frame(&mut self, surface: &WlSurface, time_ms: u32) {
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

    fn copy_committed(&mut self, surface: &WlSurface) {
        let assignment = with_states(surface, |states| {
            states
                .cached_state
                .get::<SurfaceAttributes>()
                .current()
                .buffer
                .take()
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
                self.pixels.remove(&surface.id());
                self.output.leave(surface);
            }
            None => (),
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
            surface.post_error(wl_surface::Error::InvalidSize, "surface budget exceeded");
        } else {
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
        if let Some(toplevel) = self.toplevels.get(&surface.id())
            && !toplevel.is_initial_configure_sent()
        {
            toplevel.send_configure();
            self.counters.configures += 1;
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
    }
    fn destroyed(&mut self, surface: &WlSurface) {
        self.pixels.remove(&surface.id());
        self.toplevels.remove(&surface.id());
        self.surfaces.retain(|s| s != surface);
        self.output.leave(surface);
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
        if self.toplevels.len() >= limits::TOPLEVELS {
            toplevel
                .wl_surface()
                .post_error(wl_surface::Error::InvalidSize, "toplevel budget exceeded");
        } else {
            self.toplevels.insert(toplevel.wl_surface().id(), toplevel);
        }
    }
    fn new_popup(&mut self, popup: PopupSurface, _: PositionerState) {
        popup.send_configure().ok();
    }
    fn grab(&mut self, _: PopupSurface, _: WlSeat, _: Serial) {}
    fn reposition_request(&mut self, _: PopupSurface, _: PositionerState, _: u32) {}
    fn ack_configure(&mut self, _: WlSurface, _: Configure) {
        self.counters.acks += 1;
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
        self.toplevels.remove(&surface.wl_surface().id());
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
    fn focus_changed(&mut self, _: &Seat<Self>, _: Option<&WlSurface>) {}
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

impl Dispatch<WlDataDevice, DataDeviceUserData> for ProtocolState {
    fn request(
        state: &mut Self,
        client: &Client,
        device: &WlDataDevice,
        request: wl_data_device::Request,
        data: &DataDeviceUserData,
        dh: &DisplayHandle,
        init: &mut DataInit<'_, Self>,
    ) {
        match request {
            // keyboard/pointer/touch未提供なので有効なfocus/serialは存在しない。I02/I03/I07で拡張する。
            wl_data_device::Request::SetSelection { source, .. }
            | wl_data_device::Request::StartDrag { source, .. } => {
                if let Some(source) = source {
                    source.cancelled();
                    state.counters.cancellations += 1;
                }
                device.selection(None);
            }
            request => {
                <DataDeviceState as Dispatch<WlDataDevice, DataDeviceUserData, Self>>::request(
                    state, client, device, request, data, dh, init,
                )
            }
        }
    }
}

delegate_compositor!(ProtocolState);
delegate_xdg_shell!(ProtocolState);
delegate_output!(ProtocolState);
delegate_seat!(ProtocolState);
delegate_global_dispatch!(ProtocolState: [WlShm: ()] => ShmState);
delegate_dispatch!(ProtocolState: [WlBuffer: ShmBufferUserData] => ShmState);
delegate_global_dispatch!(ProtocolState: [WlDataDeviceManager: ()] => DataDeviceState);
delegate_dispatch!(ProtocolState: [WlDataDeviceManager: ()] => DataDeviceState);
delegate_dispatch!(ProtocolState: [WlDataSource: DataSourceUserData] => DataDeviceState);
