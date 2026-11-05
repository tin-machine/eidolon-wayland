//! W03固定client。checkpointは観測・server configure・可視性の制御だけで、Wayland要求を代行しない。
use super::*;
use std::sync::mpsc::{Receiver, RecvTimeoutError, SyncSender, TrySendError};
use wayland_client::protocol::wl_surface::WlSurface;
use wayland_protocols::xdg::shell::client::{
    xdg_popup, xdg_surface::XdgSurface, xdg_toplevel::XdgToplevel,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Scenario {
    Lifecycle,
    Transforms,
    FrameGates,
    Tree,
    BadSerial,
    StaleConfigure,
    PrematureBuffer,
    UnconfiguredRemap,
    BadScale,
    PositionerOverflow,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Gate {
    Visible,
    Target,
    Budget,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Stage {
    FirstMapped,
    RejectResize,
    TreePending,
    TreeSynced,
    TreeRestacked,
    TreeRestored,
    TreeDesynced,
    TreePopups,
    TreeUnmapped,
    ConfigureResize,
    ResizeAcked,
    Resized,
    Unmapped,
    RemapAcked,
    Remapped,
    ConfigureTwice,
    TwiceAcked,
    TwiceCommitted,
    Recreated,
    Transform { index: u8, scale: u32 },
    Suspend(Gate),
    Withheld(Gate),
    Resume(Gate),
    Resumed(Gate),
}
pub struct Control {
    pub steps: SyncSender<Stage>,
    pub replies: Receiver<()>,
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub protocol: super::Report,
    pub checkpoints: u32,
    pub protocol_error: bool,
    pub withheld: u32,
    pub popup_positions: Vec<(i32, i32, i32, i32)>,
    pub popup_done: u32,
}

struct Context {
    connection: Connection,
    queue: EventQueue<State>,
    state: State,
    stop: Arc<AtomicBool>,
    deadline: Instant,
    control: Control,
    report: Report,
}
impl Context {
    fn pump(&mut self, done: impl Fn(&State) -> bool) -> Result<(), &'static str> {
        super::pump(
            &self.connection,
            &mut self.queue,
            &mut self.state,
            &self.stop,
            self.deadline,
            done,
        )
    }
    fn sync(&mut self) -> Result<(), &'static str> {
        super::sync(
            &self.connection,
            &mut self.queue,
            &mut self.state,
            &self.stop,
            self.deadline,
        )
    }
    fn checkpoint(&mut self, stage: Stage) -> Result<(), &'static str> {
        self.sync()?;
        let mut sending = stage;
        loop {
            if self.stop.load(Ordering::Acquire) {
                return Err("CANCELLED");
            }
            if Instant::now() >= self.deadline {
                return Err("CHECKPOINT_TIMEOUT");
            }
            match self.control.steps.try_send(sending) {
                Ok(()) => break,
                Err(TrySendError::Full(stage)) => {
                    sending = stage;
                    std::thread::sleep(Duration::from_millis(1));
                }
                Err(TrySendError::Disconnected(_)) => return Err("CHECKPOINT_CLOSED"),
            }
        }
        loop {
            if self.stop.load(Ordering::Acquire) {
                return Err("CANCELLED");
            }
            if Instant::now() >= self.deadline {
                return Err("CHECKPOINT_TIMEOUT");
            }
            match self.control.replies.recv_timeout(Duration::from_millis(5)) {
                Ok(()) => {
                    self.report.checkpoints += 1;
                    return Ok(());
                }
                Err(RecvTimeoutError::Timeout) => (),
                Err(RecvTimeoutError::Disconnected) => return Err("CHECKPOINT_CLOSED"),
            }
        }
    }
    fn root(&mut self) -> Result<Root, &'static str> {
        let qh = self.queue.handle();
        let surface = self
            .state
            .compositor
            .as_ref()
            .ok_or("MISSING_GLOBAL")?
            .create_surface(&qh, ());
        let xdg = self
            .state
            .shell
            .as_ref()
            .ok_or("MISSING_GLOBAL")?
            .get_xdg_surface(&surface, &qh, ());
        let top = xdg.get_toplevel(&qh, ());
        Ok(Root { surface, xdg, top })
    }
    fn configure(&mut self, root: &Root) -> Result<(), &'static str> {
        let next = self.state.report.configures + 1;
        root.surface.commit();
        self.pump(|s| s.report.configures == next)
    }
    fn image(&mut self, width: i32, height: i32, bytes: &[u8]) -> Result<Image, &'static str> {
        let fd =
            memfd_create(c"eidolon-shm-fixture", MFdFlags::MFD_CLOEXEC).map_err(|_| "MEMFD")?;
        ftruncate(&fd, (width * height * 4) as i64).map_err(|_| "MEMFD_SIZE")?;
        let file = File::from(fd);
        file.write_all_at(bytes, 0).map_err(|_| "MEMFD_WRITE")?;
        let qh = self.queue.handle();
        let pool = self
            .state
            .shm
            .as_ref()
            .ok_or("MISSING_GLOBAL")?
            .create_pool(file.as_fd(), width * height * 4, &qh, ());
        let buffer = pool.create_buffer(
            0,
            width,
            height,
            width * 4,
            wl_shm::Format::Argb8888,
            &qh,
            (),
        );
        pool.destroy();
        Ok(Image { file, buffer })
    }
    fn present(&mut self, root: &Root, image: &Image) -> Result<(), &'static str> {
        self.present_surface(&root.surface, image)
    }
    fn present_surface(&mut self, surface: &WlSurface, image: &Image) -> Result<(), &'static str> {
        let release = self.state.report.releases + 1;
        let callback = self.state.report.callbacks + 1;
        surface.attach(Some(&image.buffer), 0, 0);
        surface.damage(0, 0, i32::MAX, i32::MAX);
        surface.frame(&self.queue.handle(), Callback::Frame);
        surface.commit();
        self.pump(|s| s.report.releases >= release && s.report.callbacks >= callback)
    }
    fn next_frame(&mut self, surface: &WlSurface) -> Result<(), &'static str> {
        let next = self.state.report.callbacks + 1;
        surface.frame(&self.queue.handle(), Callback::Frame);
        surface.commit();
        self.pump(|s| s.report.callbacks == next)
    }
    fn popup(
        &mut self,
        parent: &XdgSurface,
        size: i32,
        anchor_x: i32,
        anchor_y: i32,
    ) -> Result<Popup, &'static str> {
        use wayland_protocols::xdg::shell::client::xdg_positioner;
        let qh = self.queue.handle();
        let shell = self.state.shell.as_ref().ok_or("MISSING_GLOBAL")?;
        let positioner = shell.create_positioner(&qh, ());
        positioner.set_size(size, size);
        positioner.set_anchor_rect(anchor_x, anchor_y, 1, 1);
        positioner.set_anchor(xdg_positioner::Anchor::BottomRight);
        positioner.set_gravity(xdg_positioner::Gravity::BottomRight);
        let surface = self
            .state
            .compositor
            .as_ref()
            .ok_or("MISSING_GLOBAL")?
            .create_surface(&qh, ());
        let xdg = shell.get_xdg_surface(&surface, &qh, ());
        let popup = xdg.get_popup(Some(parent), &positioner, &qh, ());
        positioner.destroy();
        let next = self.state.report.configures + 1;
        surface.commit();
        self.pump(|s| s.report.configures == next)?;
        if self.state.popup_positions.last().copied()
            != Some((anchor_x + 1, anchor_y + 1, size, size))
        {
            return Err("POPUP_CONFIGURE");
        }
        Ok(Popup {
            surface,
            xdg,
            popup,
        })
    }
    fn expect_error(&mut self) -> Result<(), &'static str> {
        let outcome = self.pump(|_| false);
        if outcome.is_err() && self.connection.protocol_error().is_some() {
            self.report.protocol_error = true;
            Ok(())
        } else {
            Err("BAD_CLIENT_NOT_REJECTED")
        }
    }
}
struct Root {
    surface: WlSurface,
    xdg: XdgSurface,
    top: XdgToplevel,
}
impl Root {
    fn destroy(self) {
        self.top.destroy();
        self.xdg.destroy();
        self.surface.destroy();
    }
}
struct Popup {
    surface: WlSurface,
    xdg: XdgSurface,
    popup: xdg_popup::XdgPopup,
}
impl Popup {
    fn destroy(self) {
        self.popup.destroy();
        self.xdg.destroy();
        self.surface.destroy();
    }
}
struct Image {
    file: File,
    buffer: wl_buffer::WlBuffer,
}
impl Image {
    fn destroy(self) {
        self.buffer.destroy();
    }
}

pub fn solid(width: i32, height: i32, rgba: [u8; 4]) -> Vec<u8> {
    let bgra = [rgba[2], rgba[1], rgba[0], rgba[3]];
    bgra.repeat((width * height) as usize)
}

fn lifecycle(c: &mut Context) -> Result<(), &'static str> {
    let root = c.root()?;
    c.configure(&root)?;
    let first = c.image(32, 32, &solid(32, 32, [128, 0, 0, 128]))?;
    c.present(&root, &first)?;
    c.checkpoint(Stage::FirstMapped)?;
    c.checkpoint(Stage::RejectResize)?;
    let next = c.state.report.configures + 1;
    c.checkpoint(Stage::ConfigureResize)?;
    c.pump(|s| s.report.configures == next)?;
    c.checkpoint(Stage::ResizeAcked)?;
    let resized = c.image(48, 32, &solid(48, 32, [0, 0, 128, 128]))?;
    c.present(&root, &resized)?;
    first.destroy();
    c.checkpoint(Stage::Resized)?;

    let callbacks = c.state.report.callbacks;
    root.surface.attach(None, 0, 0);
    root.surface.frame(&c.queue.handle(), Callback::Frame);
    root.surface.commit();
    c.checkpoint(Stage::Unmapped)?;
    if c.state.report.callbacks != callbacks {
        return Err("UNMAPPED_CALLBACK");
    }
    c.report.withheld += 1;
    c.configure(&root)?;
    c.checkpoint(Stage::RemapAcked)?;
    let remapped = c.image(64, 32, &solid(64, 32, [0, 128, 0, 128]))?;
    c.present(&root, &remapped)?;
    resized.destroy();
    c.checkpoint(Stage::Remapped)?;

    let next = c.state.report.configures + 2;
    c.checkpoint(Stage::ConfigureTwice)?;
    c.pump(|s| s.report.configures == next)?;
    c.checkpoint(Stage::TwiceAcked)?;
    let twice = c.image(96, 32, &solid(96, 32, [64, 64, 64, 128]))?;
    c.present(&root, &twice)?;
    remapped.destroy();
    c.checkpoint(Stage::TwiceCommitted)?;
    root.destroy();
    twice.destroy();
    c.sync()?;

    let new = c.root()?;
    c.configure(&new)?;
    let image = c.image(32, 32, &solid(32, 32, [10, 20, 30, 255]))?;
    c.present(&new, &image)?;
    c.checkpoint(Stage::Recreated)?;
    new.destroy();
    image.destroy();
    c.sync()
}

fn transforms(c: &mut Context) -> Result<(), &'static str> {
    let root = c.root()?;
    c.configure(&root)?;
    let transforms = [
        wl_output::Transform::Normal,
        wl_output::Transform::_90,
        wl_output::Transform::_180,
        wl_output::Transform::_270,
        wl_output::Transform::Flipped,
        wl_output::Transform::Flipped90,
        wl_output::Transform::Flipped180,
        wl_output::Transform::Flipped270,
    ];
    for (index, transform) in transforms.into_iter().enumerate() {
        for scale in [1, 2] {
            let width = 2 * scale;
            let height = 3 * scale;
            let mut bytes = vec![];
            for y in 0..height {
                for x in 0..width {
                    bytes.extend_from_slice(&[
                        0,
                        0,
                        b'A' + ((y / scale) * 2 + x / scale) as u8,
                        255,
                    ]);
                }
            }
            let image = c.image(width, height, &bytes)?;
            root.surface.set_buffer_transform(transform);
            root.surface.set_buffer_scale(scale);
            c.present(&root, &image)?;
            c.checkpoint(Stage::Transform {
                index: index as u8,
                scale: scale as u32,
            })?;
            image.destroy();
        }
    }
    root.destroy();
    c.sync()
}

fn frame_gates(c: &mut Context) -> Result<(), &'static str> {
    let root = c.root()?;
    c.configure(&root)?;
    let image = c.image(32, 32, &solid(32, 32, [20, 30, 40, 255]))?;
    c.present(&root, &image)?;
    c.checkpoint(Stage::FirstMapped)?;
    for gate in [Gate::Visible, Gate::Target, Gate::Budget] {
        c.checkpoint(Stage::Suspend(gate))?;
        let callback = c.state.report.callbacks;
        let release = c.state.report.releases + 1;
        image
            .file
            .write_all_at(&solid(32, 32, [30, 40, 50, 255]), 0)
            .map_err(|_| "MEMFD_WRITE")?;
        root.surface.attach(Some(&image.buffer), 0, 0);
        root.surface.frame(&c.queue.handle(), Callback::Frame);
        root.surface.commit();
        c.pump(|s| s.report.releases == release)?;
        c.checkpoint(Stage::Withheld(gate))?;
        if c.state.report.callbacks != callback {
            return Err("INELIGIBLE_CALLBACK");
        }
        c.report.withheld += 1;
        c.checkpoint(Stage::Resume(gate))?;
        c.pump(|s| s.report.callbacks == callback + 1)?;
        c.checkpoint(Stage::Resumed(gate))?;
    }
    root.destroy();
    image.destroy();
    c.sync()
}

fn tree(c: &mut Context) -> Result<(), &'static str> {
    let root = c.root()?;
    c.configure(&root)?;
    root.xdg.set_window_geometry(2, 1, 28, 30);
    let red = c.image(32, 32, &solid(32, 32, [255, 0, 0, 255]))?;
    c.present(&root, &red)?;
    c.checkpoint(Stage::FirstMapped)?;
    let qh = c.queue.handle();
    let compositor = c.state.compositor.as_ref().ok_or("MISSING_GLOBAL")?.clone();
    let subcompositor = c
        .state
        .subcompositor
        .as_ref()
        .ok_or("MISSING_GLOBAL")?
        .clone();
    let child = compositor.create_surface(&qh, ());
    let sub = subcompositor.get_subsurface(&child, &root.surface, &qh, ());
    sub.set_position(4, 5);
    let grand = compositor.create_surface(&qh, ());
    let sub_grand = subcompositor.get_subsurface(&grand, &child, &qh, ());
    sub_grand.set_position(3, 2);
    let blue = c.image(12, 10, &solid(12, 10, [0, 0, 255, 255]))?;
    let green = c.image(6, 4, &solid(6, 4, [0, 255, 0, 255]))?;
    grand.attach(Some(&green.buffer), 0, 0);
    grand.commit();
    child.attach(Some(&blue.buffer), 0, 0);
    child.commit();
    c.checkpoint(Stage::TreePending)?;
    c.next_frame(&root.surface)?;
    c.checkpoint(Stage::TreeSynced)?;
    sub.place_below(&root.surface);
    c.next_frame(&root.surface)?;
    c.checkpoint(Stage::TreeRestacked)?;
    sub.place_above(&root.surface);
    c.next_frame(&root.surface)?;
    c.checkpoint(Stage::TreeRestored)?;
    sub.set_desync();
    let cyan = c.image(12, 10, &solid(12, 10, [0, 255, 255, 255]))?;
    c.present_surface(&child, &cyan)?;
    blue.destroy();
    c.checkpoint(Stage::TreeDesynced)?;
    sub.set_sync();
    let popup = c.popup(&root.xdg, 8, 9, 9)?;
    let yellow = c.image(8, 8, &solid(8, 8, [255, 255, 0, 255]))?;
    c.present_surface(&popup.surface, &yellow)?;
    let nested = c.popup(&popup.xdg, 6, 5, 5)?;
    let magenta = c.image(6, 6, &solid(6, 6, [255, 0, 255, 255]))?;
    c.present_surface(&nested.surface, &magenta)?;
    let near = c.popup(&root.xdg, 8, 23, 25)?;
    let orange = c.image(8, 8, &solid(8, 8, [255, 128, 0, 255]))?;
    c.present_surface(&near.surface, &orange)?;
    c.checkpoint(Stage::TreePopups)?;
    root.surface.attach(None, 0, 0);
    root.surface.commit();
    c.checkpoint(Stage::TreeUnmapped)?;
    if c.state.popup_done != 3 {
        return Err("POPUPS_NOT_DISMISSED");
    }
    near.destroy();
    nested.destroy();
    popup.destroy();
    orange.destroy();
    magenta.destroy();
    yellow.destroy();
    sub_grand.destroy();
    grand.destroy();
    green.destroy();
    sub.destroy();
    child.destroy();
    cyan.destroy();
    root.destroy();
    red.destroy();
    c.sync()
}

fn bad(c: &mut Context, scenario: Scenario) -> Result<(), &'static str> {
    let root = c.root()?;
    if scenario != Scenario::PrematureBuffer {
        c.configure(&root)?;
    }
    let image = c.image(32, 32, &solid(32, 32, [1, 2, 3, 255]))?;
    match scenario {
        Scenario::BadSerial => root.xdg.ack_configure(u32::MAX),
        Scenario::StaleConfigure => {
            c.present(&root, &image)?;
            c.checkpoint(Stage::FirstMapped)?;
            c.state.suppress_ack = true;
            let next = c.state.report.configures + 1;
            c.checkpoint(Stage::ConfigureResize)?;
            c.pump(|s| s.report.configures == next)?;
            let old_serial = c.state.last_serial;
            root.surface.attach(None, 0, 0);
            root.surface.commit();
            c.sync()?;
            root.xdg.ack_configure(old_serial);
        }
        Scenario::PrematureBuffer => {
            root.surface.attach(Some(&image.buffer), 0, 0);
            root.surface.commit();
        }
        Scenario::UnconfiguredRemap => {
            c.present(&root, &image)?;
            root.surface.attach(None, 0, 0);
            root.surface.commit();
            c.sync()?;
            root.surface.attach(Some(&image.buffer), 0, 0);
            root.surface.commit();
        }
        Scenario::BadScale => {
            root.surface.set_buffer_scale(3);
            root.surface.attach(Some(&image.buffer), 0, 0);
            root.surface.commit();
        }
        Scenario::PositionerOverflow => {
            let positioner = c
                .state
                .shell
                .as_ref()
                .ok_or("MISSING_GLOBAL")?
                .create_positioner(&c.queue.handle(), ());
            positioner.set_size(8, 8);
            positioner.set_anchor_rect(i32::MAX, i32::MAX, 8, 8);
        }
        _ => return Err("BAD_SCENARIO"),
    }
    c.expect_error()
}

pub fn run(
    stream: UnixStream,
    scenario: Scenario,
    stop: Arc<AtomicBool>,
    control: Control,
) -> Result<Report, &'static str> {
    let connection = Connection::from_socket(stream).map_err(|_| "CLIENT_CONNECT")?;
    let queue = connection.new_event_queue();
    let mut c = Context {
        connection,
        queue,
        state: State::default(),
        stop,
        deadline: Instant::now() + Duration::from_secs(20),
        control,
        report: Report::default(),
    };
    c.connection.display().get_registry(&c.queue.handle(), ());
    c.sync()?;
    match scenario {
        Scenario::Lifecycle => lifecycle(&mut c)?,
        Scenario::Transforms => transforms(&mut c)?,
        Scenario::FrameGates => frame_gates(&mut c)?,
        Scenario::Tree => tree(&mut c)?,
        scenario => bad(&mut c, scenario)?,
    }
    c.report.popup_positions = c.state.popup_positions;
    c.report.popup_done = c.state.popup_done;
    c.report.protocol = c.state.report;
    Ok(c.report)
}

impl Dispatch<xdg_popup::XdgPopup, ()> for State {
    fn event(
        state: &mut Self,
        _: &xdg_popup::XdgPopup,
        event: xdg_popup::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        match event {
            xdg_popup::Event::Configure {
                x,
                y,
                width,
                height,
            } => state.popup_positions.push((x, y, width, height)),
            xdg_popup::Event::PopupDone => state.popup_done += 1,
            _ => (),
        }
    }
}
delegate_noop!(State: ignore wayland_protocols::xdg::shell::client::xdg_positioner::XdgPositioner);
