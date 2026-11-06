//! 固定入力だけを受けるwire検査。ユーザーのkeyboardを記録する入口には使わない。
use super::*;
use std::sync::mpsc::{Receiver, RecvTimeoutError, SyncSender};
use wayland_client::Proxy;
use wayland_client::protocol::wl_keyboard;

#[derive(Clone, Copy, Debug)]
pub enum Stage {
    Mapped,
    Focused,
    Keys,
    Reset,
    Unmapped,
}
pub struct Control {
    pub steps: SyncSender<Stage>,
    pub replies: Receiver<()>,
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub keymap_format: u32,
    pub keymap_sha256: String,
    pub keymap_bytes: usize,
    pub repeat: Option<(i32, i32)>,
    pub enters: Vec<Vec<u32>>,
    pub enter_surfaces: Vec<u32>,
    pub leaves: u32,
    pub keys: Vec<(u32, u32, u32)>,
    pub modifiers: Vec<[u32; 4]>,
    pub order: Vec<&'static str>,
    pub error: Option<&'static str>,
}
fn step(
    control: &Control,
    stage: Stage,
    stop: &AtomicBool,
    deadline: Instant,
) -> Result<(), &'static str> {
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
    std::hint::black_box("eidolon-keyboard-wire-fixture-v1");
    let deadline = Instant::now() + Duration::from_secs(15);
    let connection = Connection::from_socket(stream).map_err(|_| "CLIENT_CONNECT")?;
    let mut queue = connection.new_event_queue();
    let qh = queue.handle();
    let mut state = State {
        keyboard: Some(Report::default()),
        ..Default::default()
    };
    connection.display().get_registry(&qh, ());
    sync(&connection, &mut queue, &mut state, &stop, deadline)?;
    let keyboard = state.seat.as_ref().ok_or("NO_SEAT")?.get_keyboard(&qh, ());
    // foot同様にdata deviceも持つ。終了時にreleaseを送らず切断する経路の回収を検査する。
    let _device = state.manager.as_ref().ok_or("NO_MANAGER")?.get_data_device(
        state.seat.as_ref().ok_or("NO_SEAT")?,
        &qh,
        (),
    );
    let surface = state
        .compositor
        .as_ref()
        .ok_or("NO_COMPOSITOR")?
        .create_surface(&qh, ());
    let xdg = state
        .shell
        .as_ref()
        .ok_or("NO_SHELL")?
        .get_xdg_surface(&surface, &qh, ());
    let top = xdg.get_toplevel(&qh, ());
    surface.commit();
    pump(&connection, &mut queue, &mut state, &stop, deadline, |s| {
        s.configured
    })?;
    let fd = memfd_create(c"eidolon-shm-fixture-keyboard", MFdFlags::MFD_CLOEXEC)
        .map_err(|_| "MEMFD")?;
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
    let buffer = pool.create_buffer(
        0,
        WIDTH,
        HEIGHT,
        WIDTH * 4,
        wl_shm::Format::Argb8888,
        &qh,
        (),
    );
    surface.attach(Some(&buffer), 0, 0);
    surface.damage_buffer(0, 0, WIDTH, HEIGHT);
    surface.commit();
    sync(&connection, &mut queue, &mut state, &stop, deadline)?;
    for stage in [Stage::Mapped, Stage::Focused, Stage::Keys, Stage::Reset] {
        step(&control, stage, &stop, deadline)?;
        sync(&connection, &mut queue, &mut state, &stop, deadline)?;
    }
    surface.attach(None, 0, 0);
    surface.commit();
    sync(&connection, &mut queue, &mut state, &stop, deadline)?;
    step(&control, Stage::Unmapped, &stop, deadline)?;
    sync(&connection, &mut queue, &mut state, &stop, deadline)?;
    keyboard.release();
    buffer.destroy();
    pool.destroy();
    top.destroy();
    xdg.destroy();
    surface.destroy();
    sync(&connection, &mut queue, &mut state, &stop, deadline)?;
    let report = state.keyboard.take().unwrap();
    if let Some(error) = report.error {
        return Err(error);
    }
    Ok(report)
}
impl Dispatch<wl_keyboard::WlKeyboard, ()> for State {
    fn event(
        state: &mut Self,
        _: &wl_keyboard::WlKeyboard,
        event: wl_keyboard::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        let Some(report) = state.keyboard.as_mut() else {
            return;
        };
        if report.order.len() >= 128 {
            report.error = Some("KEYBOARD_EVENT_LIMIT");
            return;
        }
        match event {
            wl_keyboard::Event::Keymap { format, fd, size } => {
                report.order.push("keymap");
                report.keymap_format = match format {
                    WEnum::Value(f) => f as u32,
                    WEnum::Unknown(f) => f,
                };
                if size == 0 || size > 1024 * 1024 {
                    report.error = Some("KEYMAP_SIZE");
                    return;
                }
                let mut text = vec![0; size as usize];
                // SCM_RIGHTSはopen-file offsetを共有する。stream readの現在位置へ依存しない。
                if File::from(fd).read_exact_at(&mut text, 0).is_err() || text.pop() != Some(0) {
                    report.error = Some("KEYMAP_FD");
                    return;
                }
                report.keymap_bytes = text.len();
                report.keymap_sha256 = Sha256::digest(text)
                    .iter()
                    .map(|b| format!("{b:02x}"))
                    .collect();
            }
            wl_keyboard::Event::RepeatInfo { rate, delay } => {
                report.repeat = Some((rate, delay));
                report.order.push("repeat");
            }
            wl_keyboard::Event::Enter {
                keys,
                surface,
                serial,
            } => {
                state.input_serial = serial;
                report.enter_surfaces.push(surface.id().protocol_id());
                if keys.len() % 4 != 0 {
                    report.error = Some("ENTER_KEYS");
                    return;
                }
                report.enters.push(
                    keys.chunks_exact(4)
                        .map(|b| u32::from_ne_bytes(b.try_into().unwrap()))
                        .collect(),
                );
                report.order.push("enter");
            }
            wl_keyboard::Event::Leave { .. } => {
                report.leaves += 1;
                report.order.push("leave");
            }
            wl_keyboard::Event::Key {
                serial,
                key,
                state: key_state,
                time,
                ..
            } => {
                state.input_serial = serial;
                let state = match key_state {
                    WEnum::Value(s) => s as u32,
                    WEnum::Unknown(s) => s,
                };
                report.keys.push((key, state, time));
                report.order.push("key");
            }
            wl_keyboard::Event::Modifiers {
                mods_depressed,
                mods_latched,
                mods_locked,
                group,
                ..
            } => {
                report
                    .modifiers
                    .push([mods_depressed, mods_latched, mods_locked, group]);
                report.order.push("modifiers");
            }
            _ => (),
        }
    }
}
