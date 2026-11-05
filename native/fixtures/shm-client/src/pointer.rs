//! 二つの実toplevelとinput-region付きchildへ、固定pointerイベントを受信する。
use super::*;
use std::sync::mpsc::{Receiver, RecvTimeoutError, SyncSender};
use wayland_client::{Proxy, protocol::wl_pointer};

#[derive(Clone, Copy, Debug)]
pub enum Stage {
    Mapped,
    HoverChild,
    Hole,
    Pressed,
    Released,
    FocusedB,
    Reset,
    UnmappedB,
    DestroyedChild,
}
pub struct Control {
    pub steps: SyncSender<Stage>,
    pub replies: Receiver<()>,
}
#[derive(Clone, Debug, Serialize)]
#[serde(tag = "event", rename_all = "snake_case")]
pub enum Event {
    Enter {
        surface: &'static str,
        position: [f64; 2],
    },
    Leave {
        surface: &'static str,
    },
    Motion {
        surface: Option<&'static str>,
        position: [f64; 2],
    },
    Button {
        surface: Option<&'static str>,
        code: u32,
        state: u32,
        time: u32,
    },
    Axis {
        surface: Option<&'static str>,
        axis: u32,
        value: f64,
    },
    AxisSource {
        source: u32,
    },
    AxisStop {
        axis: u32,
    },
    Frame,
}
#[derive(Clone, Debug, Serialize)]
pub struct Checkpoint {
    pub stage: String,
    pub events: usize,
    pub keyboard_enters: Vec<u32>,
    pub keyboard_leaves: u32,
    pub keys: usize,
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub events: Vec<Event>,
    pub checkpoints: Vec<Checkpoint>,
    pub root_a: u32,
    pub root_b: u32,
    pub child: u32,
    pub keyboard: keyboard::Report,
    pub error: Option<&'static str>,
}
fn step(
    control: &Control,
    stage: Stage,
    state: &mut State,
    stop: &AtomicBool,
    deadline: Instant,
) -> Result<(), &'static str> {
    let keyboard = state.keyboard.as_ref().ok_or("NO_KEYBOARD_REPORT")?;
    let report = state.pointer.as_mut().ok_or("NO_POINTER_REPORT")?;
    report.checkpoints.push(Checkpoint {
        stage: format!("{stage:?}"),
        events: report.events.len(),
        keyboard_enters: keyboard.enter_surfaces.clone(),
        keyboard_leaves: keyboard.leaves,
        keys: keyboard.keys.len(),
    });
    control
        .steps
        .try_send(stage)
        .map_err(|_| "CHECKPOINT_SEND")?;
    loop {
        if stop.load(Ordering::Acquire) || Instant::now() > deadline {
            return Err("CHECKPOINT_TIMEOUT");
        }
        match control.replies.recv_timeout(Duration::from_millis(20)) {
            Ok(()) => return Ok(()),
            Err(RecvTimeoutError::Timeout) => (),
            Err(_) => return Err("CHECKPOINT_CLOSED"),
        }
    }
}
pub fn run(
    stream: UnixStream,
    stop: Arc<AtomicBool>,
    control: Control,
) -> Result<Report, &'static str> {
    std::hint::black_box("eidolon-pointer-wire-fixture-v1");
    let deadline = Instant::now() + Duration::from_secs(15);
    let connection = Connection::from_socket(stream).map_err(|_| "CLIENT_CONNECT")?;
    let mut queue = connection.new_event_queue();
    let qh = queue.handle();
    let mut state = State {
        pointer: Some(Report::default()),
        keyboard: Some(keyboard::Report::default()),
        ..Default::default()
    };
    connection.display().get_registry(&qh, ());
    sync(&connection, &mut queue, &mut state, &stop, deadline)?;
    let seat = state.seat.as_ref().ok_or("NO_SEAT")?;
    let pointer = seat.get_pointer(&qh, ());
    let keyboard = seat.get_keyboard(&qh, ());
    let compositor = state.compositor.clone().ok_or("NO_COMPOSITOR")?;
    let shell = state.shell.clone().ok_or("NO_SHELL")?;
    let mut roots = vec![];
    for label in ["root_a", "root_b"] {
        state.configured = false;
        let surface = compositor.create_surface(&qh, ());
        state.pointer_labels.insert(surface.id(), label);
        let xdg = shell.get_xdg_surface(&surface, &qh, ());
        let top = xdg.get_toplevel(&qh, ());
        surface.commit();
        pump(&connection, &mut queue, &mut state, &stop, deadline, |s| {
            s.configured
        })?;
        roots.push((surface, xdg, top));
    }
    let child = compositor.create_surface(&qh, ());
    state.pointer_labels.insert(child.id(), "child");
    let sub = state
        .subcompositor
        .as_ref()
        .ok_or("NO_SUBCOMPOSITOR")?
        .get_subsurface(&child, &roots[0].0, &qh, ());
    sub.set_position(8, 6);
    let region = compositor.create_region(&qh, ());
    region.add(4, 4, 8, 8);
    child.set_input_region(Some(&region));
    region.destroy();
    let fd =
        memfd_create(c"eidolon-shm-fixture-pointer", MFdFlags::MFD_CLOEXEC).map_err(|_| "MEMFD")?;
    ftruncate(&fd, i64::from(WIDTH * HEIGHT * 4)).map_err(|_| "MEMFD_SIZE")?;
    let file = File::from(fd);
    file.write_all_at(&pattern(0), 0)
        .map_err(|_| "MEMFD_WRITE")?;
    let pool =
        state
            .shm
            .as_ref()
            .ok_or("NO_SHM")?
            .create_pool(file.as_fd(), WIDTH * HEIGHT * 4, &qh, ());
    let root_buffer = pool.create_buffer(
        0,
        WIDTH,
        HEIGHT,
        WIDTH * 4,
        wl_shm::Format::Argb8888,
        &qh,
        (),
    );
    let child_buffer = pool.create_buffer(0, 16, 16, WIDTH * 4, wl_shm::Format::Argb8888, &qh, ());
    child.attach(Some(&child_buffer), 0, 0);
    child.damage_buffer(0, 0, 16, 16);
    child.commit();
    for (surface, _, _) in &roots {
        surface.attach(Some(&root_buffer), 0, 0);
        surface.damage_buffer(0, 0, WIDTH, HEIGHT);
        surface.commit();
    }
    {
        let report = state.pointer.as_mut().unwrap();
        report.root_a = roots[0].0.id().protocol_id();
        report.root_b = roots[1].0.id().protocol_id();
        report.child = child.id().protocol_id();
    }
    sync(&connection, &mut queue, &mut state, &stop, deadline)?;
    for stage in [
        Stage::Mapped,
        Stage::HoverChild,
        Stage::Hole,
        Stage::Pressed,
        Stage::Released,
        Stage::FocusedB,
        Stage::Reset,
    ] {
        step(&control, stage, &mut state, &stop, deadline)?;
        sync(&connection, &mut queue, &mut state, &stop, deadline)?;
    }
    roots[1].0.attach(None, 0, 0);
    roots[1].0.commit();
    sync(&connection, &mut queue, &mut state, &stop, deadline)?;
    step(&control, Stage::UnmappedB, &mut state, &stop, deadline)?;
    sync(&connection, &mut queue, &mut state, &stop, deadline)?;
    sub.destroy();
    child.destroy();
    sync(&connection, &mut queue, &mut state, &stop, deadline)?;
    step(&control, Stage::DestroyedChild, &mut state, &stop, deadline)?;
    sync(&connection, &mut queue, &mut state, &stop, deadline)?;
    pointer.release();
    keyboard.release();
    root_buffer.destroy();
    child_buffer.destroy();
    pool.destroy();
    for (surface, xdg, top) in roots {
        top.destroy();
        xdg.destroy();
        surface.destroy();
    }
    sync(&connection, &mut queue, &mut state, &stop, deadline)?;
    let mut report = state.pointer.take().unwrap();
    report.keyboard = state.keyboard.take().unwrap();
    if let Some(error) = report.error.or(report.keyboard.error) {
        return Err(error);
    }
    Ok(report)
}
impl Dispatch<wl_pointer::WlPointer, ()> for State {
    fn event(
        state: &mut Self,
        _: &wl_pointer::WlPointer,
        event: wl_pointer::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        let Some(report) = state.pointer.as_mut() else {
            return;
        };
        if report.events.len() >= 512 {
            report.error = Some("POINTER_EVENT_LIMIT");
            return;
        }
        let value = match event {
            wl_pointer::Event::Enter {
                surface,
                surface_x,
                surface_y,
                ..
            } => {
                let Some(label) = state.pointer_labels.get(&surface.id()).copied() else {
                    report.error = Some("UNKNOWN_POINTER_SURFACE");
                    return;
                };
                state.pointer_current = Some(label);
                Event::Enter {
                    surface: label,
                    position: [surface_x, surface_y],
                }
            }
            wl_pointer::Event::Leave { surface, .. } => {
                let Some(label) = state.pointer_labels.get(&surface.id()).copied() else {
                    report.error = Some("UNKNOWN_POINTER_SURFACE");
                    return;
                };
                state.pointer_current = None;
                Event::Leave { surface: label }
            }
            wl_pointer::Event::Motion {
                surface_x,
                surface_y,
                ..
            } => Event::Motion {
                surface: state.pointer_current,
                position: [surface_x, surface_y],
            },
            wl_pointer::Event::Button {
                serial,
                button,
                state: WEnum::Value(button_state),
                time,
                ..
            } => {
                state.pointer_serial = serial;
                Event::Button {
                    surface: state.pointer_current,
                    code: button,
                    state: button_state as u32,
                    time,
                }
            }
            wl_pointer::Event::Axis {
                axis: WEnum::Value(axis),
                value,
                ..
            } => Event::Axis {
                surface: state.pointer_current,
                axis: axis as u32,
                value,
            },
            wl_pointer::Event::AxisSource {
                axis_source: WEnum::Value(source),
            } => Event::AxisSource {
                source: source as u32,
            },
            wl_pointer::Event::AxisStop {
                axis: WEnum::Value(axis),
                ..
            } => Event::AxisStop { axis: axis as u32 },
            wl_pointer::Event::Frame => Event::Frame,
            _ => {
                report.error = Some("UNEXPECTED_POINTER_EVENT");
                return;
            }
        };
        report.events.push(value);
    }
}
delegate_noop!(State: ignore wayland_client::protocol::wl_region::WlRegion);
