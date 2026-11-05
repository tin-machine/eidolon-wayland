//! clipped popup/子popupのwire focusとpopup_doneを固定clientで観測する。
use super::clipboard::ClientFixture;
use super::*;
use std::sync::mpsc::{Receiver, RecvTimeoutError, SyncSender};
use wayland_client::Proxy;
use wayland_protocols::xdg::shell::client::{xdg_popup, xdg_positioner};
#[derive(Clone, Copy, Debug)]
pub enum Stage {
    Mapped,
    Good,
    Input,
    Nested,
    Closed,
    SecondFocused,
    PressAgain,
    ParentPopup,
    ParentDestroyed,
}
pub struct Control {
    pub steps: SyncSender<Stage>,
    pub replies: Receiver<()>,
}
#[derive(Debug, Default, Serialize)]
pub struct Report {
    pub denied_serial: bool,
    pub denied_parent: bool,
    pub popup_keyboard: bool,
    pub nested_keyboard: bool,
    pub outside_dismissed: bool,
    pub other_window_focused: bool,
    pub parent_destroyed: bool,
    pub popup_done: u32,
    pub popup_keys: usize,
}
fn step(
    c: &mut ClientFixture,
    control: &Control,
    stage: Stage,
    stop: &AtomicBool,
    deadline: Instant,
) -> Result<(), &'static str> {
    control.steps.try_send(stage).map_err(|_| "STEP_SEND")?;
    loop {
        if stop.load(Ordering::Acquire) || Instant::now() >= deadline {
            return Err("STEP_TIMEOUT");
        }
        match control.replies.recv_timeout(Duration::from_millis(20)) {
            Ok(()) => break,
            Err(RecvTimeoutError::Timeout) => (),
            Err(_) => return Err("STEP_CLOSED"),
        }
    }
    c.sync(stop, deadline)
}
struct Popup {
    surface: wl_surface::WlSurface,
    xdg: xdg_surface::XdgSurface,
    popup: xdg_popup::XdgPopup,
    buffer: Option<wl_buffer::WlBuffer>,
}
impl Popup {
    fn create(
        c: &mut ClientFixture,
        parent: &xdg_surface::XdgSurface,
        geometry: (i32, i32),
        identity: (&'static str, u32, bool),
        stop: &AtomicBool,
        deadline: Instant,
    ) -> Result<Self, &'static str> {
        let (size, offset) = geometry;
        let (label, serial, denied) = identity;
        let qh = c.queue.handle();
        let shell = c.state.shell.as_ref().unwrap();
        let positioner = shell.create_positioner(&qh, ());
        positioner.set_size(size, size);
        positioner.set_anchor_rect(offset - 1, offset - 1, 1, 1);
        positioner.set_anchor(xdg_positioner::Anchor::BottomRight);
        positioner.set_gravity(xdg_positioner::Gravity::BottomRight);
        let surface = c.state.compositor.as_ref().unwrap().create_surface(&qh, ());
        c.state.pointer_labels.insert(surface.id(), label);
        let xdg = shell.get_xdg_surface(&surface, &qh, ());
        let popup = xdg.get_popup(Some(parent), &positioner, &qh, ());
        positioner.destroy();
        popup.grab(c.state.seat.as_ref().unwrap(), serial);
        let previous = c.state.popup_positions.len();
        surface.commit();
        if denied {
            c.sync(stop, deadline)?;
            return Ok(Self {
                surface,
                xdg,
                popup,
                buffer: None,
            });
        }
        pump(
            &c.connection,
            &mut c.queue,
            &mut c.state,
            stop,
            deadline,
            |s| s.popup_positions.len() > previous,
        )?;
        let buffer =
            c.pool
                .create_buffer(0, size, size, size * 4, wl_shm::Format::Argb8888, &qh, ());
        surface.attach(Some(&buffer), 0, 0);
        surface.damage_buffer(0, 0, size, size);
        surface.commit();
        c.sync(stop, deadline)?;
        Ok(Self {
            surface,
            xdg,
            popup,
            buffer: Some(buffer),
        })
    }
    fn destroy(self) {
        self.popup.destroy();
        self.xdg.destroy();
        self.surface.destroy();
        if let Some(buffer) = self.buffer {
            buffer.destroy();
        }
    }
}
pub fn run(
    stream: UnixStream,
    stop: Arc<AtomicBool>,
    control: Control,
) -> Result<Report, &'static str> {
    let deadline = Instant::now() + Duration::from_secs(20);
    let mut c = ClientFixture::new(stream, &stop, deadline)?;
    c.state.pointer = Some(pointer::Report::default());
    c.state.pointer_labels.insert(c.surface.id(), "root_a");
    let pointer = c
        .state
        .seat
        .as_ref()
        .unwrap()
        .get_pointer(&c.queue.handle(), ());
    let surface = c
        .state
        .compositor
        .as_ref()
        .unwrap()
        .create_surface(&c.queue.handle(), ());
    c.state.pointer_labels.insert(surface.id(), "root_b");
    let xdg = c
        .state
        .shell
        .as_ref()
        .unwrap()
        .get_xdg_surface(&surface, &c.queue.handle(), ());
    let top = xdg.get_toplevel(&c.queue.handle(), ());
    c.state.configured = false;
    surface.commit();
    pump(
        &c.connection,
        &mut c.queue,
        &mut c.state,
        &stop,
        deadline,
        |s| s.configured,
    )?;
    surface.attach(Some(&c.buffer), 0, 0);
    surface.damage_buffer(0, 0, WIDTH, HEIGHT);
    surface.commit();
    c.sync(&stop, deadline)?;
    let mut report = Report::default();
    step(&mut c, &control, Stage::Mapped, &stop, deadline)?;
    let parent = c.xdg.clone();
    let serial = c.state.pointer_serial;
    let denied = Popup::create(
        &mut c,
        &parent,
        (20, 20),
        ("denied", serial.wrapping_add(10000), true),
        &stop,
        deadline,
    )?;
    report.denied_serial = c.state.popup_done == 1;
    denied.destroy();
    c.sync(&stop, deadline)?;
    let denied = Popup::create(
        &mut c,
        &xdg,
        (20, 20),
        ("denied_parent", serial, true),
        &stop,
        deadline,
    )?;
    report.denied_parent = c.state.popup_done == 2;
    denied.destroy();
    c.sync(&stop, deadline)?;
    let good = Popup::create(
        &mut c,
        &parent,
        (20, 20),
        ("popup", serial, false),
        &stop,
        deadline,
    )?;
    step(&mut c, &control, Stage::Good, &stop, deadline)?;
    report.popup_keyboard = c.state.keyboard.as_ref().unwrap().enter_surfaces.last()
        == Some(&good.surface.id().protocol_id());
    report.popup_keys = c.state.keyboard.as_ref().unwrap().keys.len();
    step(&mut c, &control, Stage::Input, &stop, deadline)?;
    let nested_serial = c.state.pointer_serial;
    let nested = Popup::create(
        &mut c,
        &good.xdg,
        (10, 5),
        ("nested", nested_serial, false),
        &stop,
        deadline,
    )?;
    step(&mut c, &control, Stage::Nested, &stop, deadline)?;
    report.nested_keyboard = c
        .state
        .keyboard
        .as_ref()
        .unwrap()
        .enter_surfaces
        .iter()
        .any(|id| *id == nested.surface.id().protocol_id());
    report.outside_dismissed = c.state.popup_done == 4
        && c.state.keyboard.as_ref().unwrap().enter_surfaces.last()
            == Some(&c.surface.id().protocol_id());
    nested.destroy();
    good.destroy();
    c.sync(&stop, deadline)?;
    step(&mut c, &control, Stage::Closed, &stop, deadline)?;
    report.other_window_focused = c.state.keyboard.as_ref().unwrap().enter_surfaces.last()
        == Some(&surface.id().protocol_id());
    step(&mut c, &control, Stage::SecondFocused, &stop, deadline)?;
    step(&mut c, &control, Stage::PressAgain, &stop, deadline)?;
    let serial = c.state.pointer_serial;
    let last = Popup::create(
        &mut c,
        &parent,
        (20, 20),
        ("parent_gone", serial, false),
        &stop,
        deadline,
    )?;
    step(&mut c, &control, Stage::ParentPopup, &stop, deadline)?;
    c.top.destroy();
    c.xdg.destroy();
    c.surface.destroy();
    c.sync(&stop, deadline)?;
    report.parent_destroyed = c.state.popup_done == 5;
    last.destroy();
    step(&mut c, &control, Stage::ParentDestroyed, &stop, deadline)?;
    report.popup_done = c.state.popup_done;
    pointer.release();
    top.destroy();
    xdg.destroy();
    surface.destroy();
    c.finish(&stop, deadline)?;
    Ok(report)
}
