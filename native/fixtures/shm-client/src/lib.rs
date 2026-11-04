//! debug専用、font/PNG/rendererに依存しない固定Wayland client。全read待ちは期限付き。
use nix::{
    poll::{PollFd, PollFlags, poll},
    sys::memfd::{MFdFlags, memfd_create},
    unistd::ftruncate,
};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    os::{
        fd::AsFd,
        unix::{fs::FileExt, net::UnixStream},
    },
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};
use wayland_client::{
    Connection, Dispatch, EventQueue, QueueHandle, WEnum, delegate_noop,
    protocol::{
        wl_buffer, wl_callback, wl_compositor, wl_data_device, wl_data_device_manager,
        wl_data_source, wl_output, wl_registry, wl_seat, wl_shm, wl_shm_pool, wl_subcompositor,
        wl_subsurface, wl_surface,
    },
};
use wayland_protocols::xdg::shell::client::{xdg_surface, xdg_toplevel, xdg_wm_base};

pub const WIDTH: i32 = 32;
pub const HEIGHT: i32 = 32;
pub const FRAMES: u32 = 100;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    Pattern,
    SharedCache,
    InvalidDimensions,
    InvalidStride,
    InvalidOffset,
    InvalidFormat,
    Truncate,
    NegativeResize,
    OversizedPool,
    PoolLimit,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub globals: Vec<(String, u32)>,
    pub frames: u32,
    pub configures: u32,
    pub acks: u32,
    pub releases: u32,
    pub callbacks: u32,
    pub enters: u32,
    pub leaves: u32,
    pub cancellations: u32,
    pub empty_selection: bool,
    pub output_mode: Option<(i32, i32, i32)>,
    pub output_scale: i32,
    pub output_geometry: bool,
    pub output_done: bool,
    pub seat_capabilities: Option<u32>,
    pub protocol_error: bool,
    pub release_deferred: bool,
}

#[derive(Default)]
struct State {
    compositor: Option<wl_compositor::WlCompositor>,
    shell: Option<xdg_wm_base::XdgWmBase>,
    subcompositor: Option<wl_subcompositor::WlSubcompositor>,
    shm: Option<wl_shm::WlShm>,
    seat: Option<wl_seat::WlSeat>,
    manager: Option<wl_data_device_manager::WlDataDeviceManager>,
    configured: bool,
    syncs: u32,
    report: Report,
}

#[derive(Clone, Copy)]
enum Callback {
    Sync,
    Frame,
}

/// packed little-endian BGRA。alphaはpremultipliedのまま128、色はalpha以下。
pub fn pattern(frame: u32) -> Vec<u8> {
    let mut bytes = Vec::with_capacity((WIDTH * HEIGHT * 4) as usize);
    for y in 0..HEIGHT as u32 {
        for x in 0..WIDTH as u32 {
            bytes.extend_from_slice(&[
                (x + frame) as u8 % 128,
                (y + frame) as u8 % 128,
                frame as u8 % 128,
                128,
            ]);
        }
    }
    bytes
}
pub fn expected_hash(frame: u32) -> String {
    let mut rgba = pattern(frame);
    for pixel in rgba.chunks_exact_mut(4) {
        pixel.swap(0, 2);
    }
    Sha256::digest(rgba)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn pump(
    connection: &Connection,
    queue: &mut EventQueue<State>,
    state: &mut State,
    stop: &AtomicBool,
    deadline: Instant,
    done: impl Fn(&State) -> bool,
) -> Result<(), &'static str> {
    loop {
        queue
            .dispatch_pending(state)
            .map_err(|_| "CLIENT_DISPATCH")?;
        if done(state) {
            return Ok(());
        }
        if stop.load(Ordering::Acquire) {
            return Err("CANCELLED");
        }
        if Instant::now() >= deadline {
            return Err("CLIENT_TIMEOUT");
        }
        connection.flush().map_err(|_| "CLIENT_FLUSH")?;
        if let Some(guard) = connection.prepare_read() {
            let mut fds = [PollFd::new(connection.as_fd(), PollFlags::POLLIN)];
            match poll(&mut fds, 20u16) {
                Ok(0) => (),
                Ok(_) => {
                    guard.read().map_err(|_| "CLIENT_READ")?;
                }
                Err(nix::errno::Errno::EINTR) => (),
                Err(_) => return Err("CLIENT_POLL"),
            }
        }
    }
}

fn sync(
    connection: &Connection,
    queue: &mut EventQueue<State>,
    state: &mut State,
    stop: &AtomicBool,
    deadline: Instant,
) -> Result<(), &'static str> {
    let expected = state.syncs + 1;
    connection.display().sync(&queue.handle(), Callback::Sync);
    pump(connection, queue, state, stop, deadline, |state| {
        state.syncs == expected
    })
}

pub fn run(stream: UnixStream, mode: Mode, stop: Arc<AtomicBool>) -> Result<Report, &'static str> {
    let deadline = Instant::now() + Duration::from_secs(20);
    let connection = Connection::from_socket(stream).map_err(|_| "CLIENT_CONNECT")?;
    let mut queue = connection.new_event_queue();
    let qh = queue.handle();
    let mut state = State::default();
    connection.display().get_registry(&qh, ());
    sync(&connection, &mut queue, &mut state, &stop, deadline)?;
    let compositor = state.compositor.clone().ok_or("MISSING_GLOBAL")?;
    let shell = state.shell.clone().ok_or("MISSING_GLOBAL")?;
    let shm = state.shm.clone().ok_or("MISSING_GLOBAL")?;
    let seat = state.seat.clone().ok_or("MISSING_GLOBAL")?;
    let manager = state.manager.clone().ok_or("MISSING_GLOBAL")?;
    let surface = compositor.create_surface(&qh, ());
    let xdg = shell.get_xdg_surface(&surface, &qh, ());
    let toplevel = xdg.get_toplevel(&qh, ());
    toplevel.set_title("再現用SHM fixture".repeat(100));
    toplevel.set_app_id("eidolon.shm.fixture".into());
    surface.commit();
    pump(&connection, &mut queue, &mut state, &stop, deadline, |s| {
        s.configured
    })?;
    let device = manager.get_data_device(&seat, &qh, ());
    let selection = manager.create_data_source(&qh, ());
    selection.offer("text/plain;charset=utf-8".into());
    device.set_selection(Some(&selection), 0);
    let drag = manager.create_data_source(&qh, ());
    drag.offer("text/plain".into());
    drag.set_actions(wl_data_device_manager::DndAction::Copy);
    device.start_drag(Some(&drag), &surface, None, 0);
    let fd = memfd_create(c"eidolon-shm-fixture", MFdFlags::MFD_CLOEXEC).map_err(|_| "MEMFD")?;
    ftruncate(&fd, (WIDTH * HEIGHT * 4) as i64).map_err(|_| "MEMFD_SIZE")?;
    let file = File::from(fd);
    if mode == Mode::OversizedPool {
        let _ = shm.create_pool(file.as_fd(), 64 * 1024 * 1024 + 1, &qh, ());
    } else if mode == Mode::PoolLimit {
        let mut buffers = vec![];
        for _ in 0..9 {
            let pool = shm.create_pool(file.as_fd(), 4096, &qh, ());
            buffers.push(pool.create_buffer(
                0,
                WIDTH,
                HEIGHT,
                WIDTH * 4,
                wl_shm::Format::Argb8888,
                &qh,
                (),
            ));
            pool.destroy();
        }
    } else {
        let pool = shm.create_pool(file.as_fd(), WIDTH * HEIGHT * 4, &qh, ());
        if mode == Mode::NegativeResize {
            pool.resize(-1);
        } else if matches!(
            mode,
            Mode::InvalidDimensions
                | Mode::InvalidStride
                | Mode::InvalidOffset
                | Mode::InvalidFormat
        ) {
            let (width, height, stride, offset, format) = match mode {
                Mode::InvalidDimensions => (2049, 1, 8196, 0, wl_shm::Format::Argb8888),
                Mode::InvalidStride => (WIDTH, HEIGHT, WIDTH * 4 - 1, 0, wl_shm::Format::Argb8888),
                Mode::InvalidOffset => (WIDTH, HEIGHT, WIDTH * 4, 4, wl_shm::Format::Argb8888),
                Mode::InvalidFormat => (WIDTH, HEIGHT, WIDTH * 4, 0, wl_shm::Format::Rgb565),
                _ => unreachable!(),
            };
            let _ = pool.create_buffer(offset, width, height, stride, format, &qh, ());
        } else {
            let buffer = pool.create_buffer(
                0,
                WIDTH,
                HEIGHT,
                WIDTH * 4,
                wl_shm::Format::Argb8888,
                &qh,
                (),
            );
            pool.destroy(); // bufferだけがmappingを保持する期間も予算から外さない。
            sync(&connection, &mut queue, &mut state, &stop, deadline)?;
            if mode == Mode::Truncate {
                ftruncate(&file, 0).map_err(|_| "MEMFD_TRUNCATE")?;
            }
            if mode == Mode::SharedCache {
                let subcompositor = state.subcompositor.clone().ok_or("MISSING_GLOBAL")?;
                let parent = compositor.create_surface(&qh, ());
                let parent_xdg = shell.get_xdg_surface(&parent, &qh, ());
                let parent_top = parent_xdg.get_toplevel(&qh, ());
                parent.commit();
                pump(&connection, &mut queue, &mut state, &stop, deadline, |s| {
                    s.report.configures == 2
                })?;
                let child = compositor.create_surface(&qh, ());
                let sub = subcompositor.get_subsurface(&child, &parent, &qh, ());
                file.write_all_at(&pattern(0), 0)
                    .map_err(|_| "MEMFD_WRITE")?;
                child.attach(Some(&buffer), 0, 0);
                child.commit(); // parentが未commitなので、このbuffer使用はcache内にある。
                surface.attach(Some(&buffer), 0, 0);
                surface.frame(&qh, Callback::Frame);
                surface.commit();
                pump(&connection, &mut queue, &mut state, &stop, deadline, |s| {
                    s.report.callbacks == 1
                })?;
                sync(&connection, &mut queue, &mut state, &stop, deadline)?;
                if state.report.releases != 0 {
                    return Err("RELEASE_BEFORE_CACHED_CONSUMER");
                }
                state.report.release_deferred = true;
                parent.commit();
                pump(&connection, &mut queue, &mut state, &stop, deadline, |s| {
                    s.report.releases == 1
                })?;
                state.report.frames = 1;
                child.attach(None, 0, 0);
                child.commit();
                parent.commit();
                sync(&connection, &mut queue, &mut state, &stop, deadline)?;
                sub.destroy();
                child.destroy();
                parent_top.destroy();
                parent_xdg.destroy();
                parent.destroy();
            } else {
                for frame in 0..if mode == Mode::Pattern { FRAMES } else { 1 } {
                    if mode == Mode::Pattern {
                        file.write_all_at(&pattern(frame), 0)
                            .map_err(|_| "MEMFD_WRITE")?;
                    }
                    surface.attach(Some(&buffer), 0, 0);
                    surface.damage(0, 0, WIDTH, HEIGHT);
                    surface.frame(&qh, Callback::Frame);
                    surface.commit();
                    let outcome = pump(&connection, &mut queue, &mut state, &stop, deadline, |s| {
                        s.report.callbacks > frame && s.report.releases > frame
                    });
                    if mode == Mode::Truncate {
                        if outcome.is_err() && connection.protocol_error().is_some() {
                            state.report.protocol_error = true;
                            return Ok(state.report);
                        }
                        return Err("TRUNCATE_NOT_REJECTED");
                    }
                    outcome?;
                    state.report.frames += 1;
                }
            }
            surface.attach(None, 0, 0);
            surface.commit();
            buffer.destroy();
            toplevel.destroy();
            xdg.destroy();
            // Leaveを受信してからsurfaceを破棄し、event自体も検証する。
            sync(&connection, &mut queue, &mut state, &stop, deadline)?;
            surface.destroy();
            selection.destroy();
            drag.destroy();
            device.release();
            seat.release();
            sync(&connection, &mut queue, &mut state, &stop, deadline)?;
            return Ok(state.report);
        }
    }
    let outcome = pump(&connection, &mut queue, &mut state, &stop, deadline, |_| {
        false
    });
    if outcome.is_err() && connection.protocol_error().is_some() {
        state.report.protocol_error = true;
        Ok(state.report)
    } else {
        Err("BAD_CLIENT_NOT_REJECTED")
    }
}

impl Dispatch<wl_registry::WlRegistry, ()> for State {
    fn event(
        state: &mut Self,
        registry: &wl_registry::WlRegistry,
        event: wl_registry::Event,
        _: &(),
        _: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        if let wl_registry::Event::Global {
            name,
            interface,
            version,
        } = event
        {
            state.report.globals.push((interface.clone(), version));
            match interface.as_str() {
                "wl_compositor" => {
                    state.compositor = Some(registry.bind(name, version.min(4), qh, ()))
                }
                "wl_shm" => state.shm = Some(registry.bind(name, 1, qh, ())),
                "wl_subcompositor" => state.subcompositor = Some(registry.bind(name, 1, qh, ())),
                "wl_output" => {
                    let _: wl_output::WlOutput = registry.bind(name, version.min(3), qh, ());
                }
                "wl_seat" => state.seat = Some(registry.bind(name, version.min(5), qh, ())),
                "xdg_wm_base" => state.shell = Some(registry.bind(name, 1, qh, ())),
                "wl_data_device_manager" => {
                    state.manager = Some(registry.bind(name, version.min(3), qh, ()))
                }
                _ => (),
            }
        }
    }
}
impl Dispatch<xdg_wm_base::XdgWmBase, ()> for State {
    fn event(
        _: &mut Self,
        proxy: &xdg_wm_base::XdgWmBase,
        event: xdg_wm_base::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let xdg_wm_base::Event::Ping { serial } = event {
            proxy.pong(serial);
        }
    }
}
impl Dispatch<xdg_surface::XdgSurface, ()> for State {
    fn event(
        state: &mut Self,
        proxy: &xdg_surface::XdgSurface,
        event: xdg_surface::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let xdg_surface::Event::Configure { serial } = event {
            state.report.configures += 1;
            proxy.ack_configure(serial);
            state.report.acks += 1;
            state.configured = true;
        }
    }
}
impl Dispatch<wl_callback::WlCallback, Callback> for State {
    fn event(
        state: &mut Self,
        _: &wl_callback::WlCallback,
        _: wl_callback::Event,
        kind: &Callback,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        match kind {
            Callback::Sync => state.syncs += 1,
            Callback::Frame => state.report.callbacks += 1,
        }
    }
}
impl Dispatch<wl_buffer::WlBuffer, ()> for State {
    fn event(
        state: &mut Self,
        _: &wl_buffer::WlBuffer,
        _: wl_buffer::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        state.report.releases += 1;
    }
}
impl Dispatch<wl_surface::WlSurface, ()> for State {
    fn event(
        state: &mut Self,
        _: &wl_surface::WlSurface,
        event: wl_surface::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        match event {
            wl_surface::Event::Enter { .. } => state.report.enters += 1,
            wl_surface::Event::Leave { .. } => state.report.leaves += 1,
            _ => (),
        }
    }
}
impl Dispatch<wl_output::WlOutput, ()> for State {
    fn event(
        state: &mut Self,
        _: &wl_output::WlOutput,
        event: wl_output::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        match event {
            wl_output::Event::Geometry { .. } => state.report.output_geometry = true,
            wl_output::Event::Mode {
                width,
                height,
                refresh,
                ..
            } => state.report.output_mode = Some((width, height, refresh)),
            wl_output::Event::Scale { factor } => state.report.output_scale = factor,
            wl_output::Event::Done => state.report.output_done = true,
            _ => (),
        }
    }
}
impl Dispatch<wl_seat::WlSeat, ()> for State {
    fn event(
        state: &mut Self,
        _: &wl_seat::WlSeat,
        event: wl_seat::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let wl_seat::Event::Capabilities {
            capabilities: WEnum::Value(capabilities),
        } = event
        {
            state.report.seat_capabilities = Some(capabilities.bits());
        }
    }
}
impl Dispatch<wl_data_source::WlDataSource, ()> for State {
    fn event(
        state: &mut Self,
        _: &wl_data_source::WlDataSource,
        event: wl_data_source::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let wl_data_source::Event::Cancelled = event {
            state.report.cancellations += 1;
        }
    }
}
impl Dispatch<wl_data_device::WlDataDevice, ()> for State {
    fn event(
        state: &mut Self,
        _: &wl_data_device::WlDataDevice,
        event: wl_data_device::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let wl_data_device::Event::Selection { id } = event {
            state.report.empty_selection = id.is_none();
        }
    }
}
delegate_noop!(State: ignore wl_compositor::WlCompositor);
delegate_noop!(State: ignore wl_subcompositor::WlSubcompositor);
delegate_noop!(State: ignore wl_subsurface::WlSubsurface);
delegate_noop!(State: ignore wl_shm::WlShm);
delegate_noop!(State: ignore wl_shm_pool::WlShmPool);
delegate_noop!(State: ignore xdg_toplevel::XdgToplevel);
delegate_noop!(State: ignore wl_data_device_manager::WlDataDeviceManager);

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fixture_frame_has_premultiplied_asymmetric_colors_and_number() {
        assert_eq!(&pattern(3)[0..8], &[3, 3, 3, 128, 4, 3, 3, 128]);
        assert_eq!(
            &pattern(3)[(WIDTH * 4) as usize..(WIDTH * 4 + 4) as usize],
            &[3, 4, 3, 128]
        );
        assert_ne!(expected_hash(3), expected_hash(4));
    }
}
