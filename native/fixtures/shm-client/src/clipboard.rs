//! 二つの独立Wayland接続で固定selectionを転送する。本文をreportへ含めない。
use super::*;
use std::{
    io::{Read, Write},
    os::fd::OwnedFd,
    sync::mpsc::{Receiver, SyncSender},
    thread,
};
use wayland_client::Proxy;
use wayland_client::protocol::{wl_data_offer, wl_keyboard};
const MIME: &str = "text/plain;charset=utf-8";
const TEXT: &[u8] = "Eidolon 固定テキスト 🥽\n".as_bytes();
pub struct Control {
    pub focus: SyncSender<u32>,
    pub replies: Receiver<()>,
}
#[derive(Debug, Default, Serialize)]
pub struct Report {
    pub copied: bool,
    pub at_limit: bool,
    pub rejected_serial: bool,
    pub rejected_focus: bool,
    pub stale_offer: bool,
    pub invalid_utf8: bool,
    pub oversized: bool,
    pub stalled: bool,
    pub stalled_receiver: bool,
    pub cancelled: bool,
    pub owner_lost: bool,
    pub mime_filtered: bool,
}
#[derive(Default)]
pub(super) struct ClientData {
    pub selection: Option<wl_data_offer::WlDataOffer>,
    payload: Vec<u8>,
    hold: bool,
    held: Vec<OwnedFd>,
    writers: Vec<thread::JoinHandle<()>>,
    mimes: Vec<String>,
}
impl ClientData {
    pub fn send(&mut self, fd: OwnedFd) {
        if self.hold {
            self.held.push(fd);
            return;
        }
        let bytes = self.payload.clone();
        self.writers.push(thread::spawn(move || {
            let _ = File::from(fd).write_all(&bytes);
        }));
    }
}
impl Dispatch<wl_data_offer::WlDataOffer, ()> for State {
    fn event(
        state: &mut Self,
        _: &wl_data_offer::WlDataOffer,
        event: wl_data_offer::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let wl_data_offer::Event::Offer { mime_type } = event
            && let Some(data) = &mut state.clipboard
        {
            data.mimes.push(mime_type);
        }
    }
}
pub(super) struct ClientFixture {
    pub(super) connection: Connection,
    pub(super) queue: EventQueue<State>,
    pub(super) state: State,
    pub(super) device: wl_data_device::WlDataDevice,
    pub(super) surface: wl_surface::WlSurface,
    pub(super) xdg: xdg_surface::XdgSurface,
    pub(super) top: xdg_toplevel::XdgToplevel,
    pub(super) keyboard: wl_keyboard::WlKeyboard,
    pub(super) buffer: wl_buffer::WlBuffer,
    pub(super) pool: wl_shm_pool::WlShmPool,
    sources: Vec<wl_data_source::WlDataSource>,
}
impl ClientFixture {
    pub(super) fn new(
        stream: UnixStream,
        stop: &AtomicBool,
        deadline: Instant,
    ) -> Result<Self, &'static str> {
        let connection = Connection::from_socket(stream).map_err(|_| "CLIENT_CONNECT")?;
        let mut queue = connection.new_event_queue();
        let qh = queue.handle();
        let mut state = State {
            clipboard: Some(ClientData::default()),
            keyboard: Some(keyboard::Report::default()),
            ..Default::default()
        };
        connection.display().get_registry(&qh, ());
        sync(&connection, &mut queue, &mut state, stop, deadline)?;
        let seat = state.seat.as_ref().ok_or("NO_SEAT")?;
        let keyboard = seat.get_keyboard(&qh, ());
        let device = state
            .manager
            .as_ref()
            .ok_or("NO_MANAGER")?
            .get_data_device(seat, &qh, ());
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
        pump(&connection, &mut queue, &mut state, stop, deadline, |s| {
            s.configured
        })?;
        let fd = memfd_create(c"eidolon-shm-fixture-clipboard", MFdFlags::MFD_CLOEXEC)
            .map_err(|_| "MEMFD")?;
        ftruncate(&fd, i64::from(WIDTH * HEIGHT * 4)).map_err(|_| "MEMFD_SIZE")?;
        let file = File::from(fd);
        file.write_all_at(&pattern(0), 0)
            .map_err(|_| "MEMFD_WRITE")?;
        let pool = state.shm.as_ref().ok_or("NO_SHM")?.create_pool(
            file.as_fd(),
            WIDTH * HEIGHT * 4,
            &qh,
            (),
        );
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
        sync(&connection, &mut queue, &mut state, stop, deadline)?;
        Ok(Self {
            connection,
            queue,
            state,
            device,
            surface,
            xdg,
            top,
            keyboard,
            buffer,
            pool,
            sources: vec![],
        })
    }
    pub(super) fn sync(
        &mut self,
        stop: &AtomicBool,
        deadline: Instant,
    ) -> Result<(), &'static str> {
        sync(
            &self.connection,
            &mut self.queue,
            &mut self.state,
            stop,
            deadline,
        )
    }
    fn offer(&mut self, bytes: Vec<u8>, serial: u32, hold: bool) {
        let data = self.state.clipboard.as_mut().unwrap();
        data.payload = bytes;
        data.hold = hold;
        let source = self
            .state
            .manager
            .as_ref()
            .unwrap()
            .create_data_source(&self.queue.handle(), ());
        source.offer(MIME.into());
        source.offer("image/png".into());
        self.device.set_selection(Some(&source), serial);
        self.sources.push(source);
    }
    fn read_offer(&mut self, offer: wl_data_offer::WlDataOffer) -> Result<File, &'static str> {
        let (read, write) = nix::unistd::pipe2(nix::fcntl::OFlag::O_CLOEXEC).map_err(|_| "PIPE")?;
        nix::fcntl::fcntl(
            &read,
            nix::fcntl::FcntlArg::F_SETFL(nix::fcntl::OFlag::O_NONBLOCK),
        )
        .map_err(|_| "NONBLOCK")?;
        offer.receive(MIME.into(), write.as_fd());
        Ok(read.into())
    }
    pub(super) fn finish(
        mut self,
        stop: &AtomicBool,
        deadline: Instant,
    ) -> Result<(), &'static str> {
        self.state.clipboard.as_mut().unwrap().held.clear();
        for source in &self.sources {
            source.destroy();
        }
        if let Some(offer) = self.state.clipboard.as_mut().unwrap().selection.take() {
            offer.destroy();
        }
        self.keyboard.release();
        if self.top.is_alive() {
            self.top.destroy();
        }
        if self.xdg.is_alive() {
            self.xdg.destroy();
        }
        if self.surface.is_alive() {
            self.surface.destroy();
        }
        self.buffer.destroy();
        self.pool.destroy();
        self.device.release();
        self.sync(stop, deadline)?;
        for writer in self.state.clipboard.as_mut().unwrap().writers.drain(..) {
            writer.join().map_err(|_| "WRITER_PANIC")?;
        }
        Ok(())
    }
}
fn focus(control: &Control, window: u32, stop: &AtomicBool) -> Result<(), &'static str> {
    control.focus.try_send(window).map_err(|_| "FOCUS_SEND")?;
    loop {
        if stop.load(Ordering::Acquire) {
            return Err("CANCELLED");
        }
        match control.replies.recv_timeout(Duration::from_millis(20)) {
            Ok(()) => return Ok(()),
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => (),
            Err(_) => return Err("FOCUS_CLOSED"),
        }
    }
}
fn read_all(
    a: &mut ClientFixture,
    b: &mut ClientFixture,
    mut read: File,
    stop: &AtomicBool,
    deadline: Instant,
) -> Result<Vec<u8>, &'static str> {
    let mut bytes = vec![];
    loop {
        if stop.load(Ordering::Acquire) || Instant::now() >= deadline {
            return Err("READ_TIMEOUT");
        }
        a.sync(stop, deadline)?;
        b.sync(stop, deadline)?;
        let mut chunk = [0; 16384];
        match read.read(&mut chunk) {
            Ok(0) => return Ok(bytes),
            Ok(n) => bytes.extend_from_slice(&chunk[..n]),
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(1))
            }
            Err(_) => return Err("READ"),
        }
        if bytes.len() > 1024 * 1024 {
            return Err("UNBOUNDED_TRANSFER");
        }
    }
}
pub fn run(
    streams: [UnixStream; 2],
    stop: Arc<AtomicBool>,
    control: Control,
) -> Result<Report, &'static str> {
    let deadline = Instant::now() + Duration::from_secs(30);
    let [asock, bsock] = streams;
    let mut a = ClientFixture::new(asock, &stop, deadline)?;
    let mut b = ClientFixture::new(bsock, &stop, deadline)?;
    let mut report = Report::default();
    focus(&control, 1, &stop)?;
    a.sync(&stop, deadline)?;
    b.sync(&stop, deadline)?;
    let serial = a.state.input_serial;
    a.offer(TEXT.to_vec(), serial.wrapping_sub(10000), false);
    a.sync(&stop, deadline)?;
    report.rejected_serial = a.state.report.cancellations == 1 && a.state.report.empty_selection;
    b.offer(TEXT.to_vec(), serial, false);
    b.sync(&stop, deadline)?;
    report.rejected_focus = b.state.report.cancellations == 1;
    a.offer(TEXT.to_vec(), serial, false);
    a.sync(&stop, deadline)?;
    focus(&control, 2, &stop)?;
    a.sync(&stop, deadline)?;
    b.sync(&stop, deadline)?;
    report.mime_filtered = b
        .state
        .clipboard
        .as_ref()
        .unwrap()
        .mimes
        .iter()
        .all(|m| m == MIME);
    let initial = b
        .state
        .clipboard
        .as_ref()
        .unwrap()
        .selection
        .clone()
        .ok_or("NO_SELECTION")?;
    let read = b.read_offer(initial.clone())?;
    report.copied = read_all(&mut a, &mut b, read, &stop, deadline)? == TEXT;
    for (bytes, kind) in [
        (vec![b'x'; 1024 * 1024], 0),
        (vec![b'y'; 1024 * 1024 + 1], 1),
        (vec![0xff, 0xfe], 2),
        (vec![], 3),
        (vec![b'z'; 1024 * 1024], 4),
    ] {
        focus(&control, 1, &stop)?;
        a.sync(&stop, deadline)?;
        b.sync(&stop, deadline)?;
        a.offer(bytes, a.state.input_serial, kind == 3);
        a.sync(&stop, deadline)?;
        focus(&control, 2, &stop)?;
        a.sync(&stop, deadline)?;
        b.sync(&stop, deadline)?;
        let selection = b
            .state
            .clipboard
            .as_ref()
            .unwrap()
            .selection
            .clone()
            .ok_or("NO_SELECTION")?;
        let read = b.read_offer(selection)?;
        if kind == 4 {
            // 所有側は送信するが受信側はpipeを読まない。protocol処理は進める。
            let until = Instant::now() + Duration::from_millis(5150);
            while Instant::now() < until {
                a.sync(&stop, deadline)?;
                b.sync(&stop, deadline)?;
                thread::sleep(Duration::from_millis(5));
            }
        }
        let got = read_all(&mut a, &mut b, read, &stop, deadline)?;
        match kind {
            0 => report.at_limit = got.len() == 1024 * 1024 && got.iter().all(|v| *v == b'x'),
            1 => report.oversized = got.is_empty(),
            2 => report.invalid_utf8 = got.is_empty(),
            3 => report.stalled = got.is_empty(),
            _ => report.stalled_receiver = !got.is_empty() && got.len() < 1024 * 1024,
        }
        a.state.clipboard.as_mut().unwrap().held.clear();
    }
    let read = b.read_offer(initial)?;
    report.stale_offer = read_all(&mut a, &mut b, read, &stop, deadline)?.is_empty();
    focus(&control, 1, &stop)?;
    a.sync(&stop, deadline)?;
    b.sync(&stop, deadline)?;
    a.offer(TEXT.to_vec(), a.state.input_serial, true);
    a.sync(&stop, deadline)?;
    focus(&control, 2, &stop)?;
    a.sync(&stop, deadline)?;
    b.sync(&stop, deadline)?;
    let offer = b
        .state
        .clipboard
        .as_ref()
        .unwrap()
        .selection
        .clone()
        .ok_or("NO_SELECTION")?;
    let read = b.read_offer(offer.clone())?;
    b.sync(&stop, deadline)?;
    a.sync(&stop, deadline)?;
    drop(read);
    offer.destroy();
    b.sync(&stop, deadline)?;
    let held = a
        .state
        .clipboard
        .as_mut()
        .unwrap()
        .held
        .pop()
        .ok_or("NO_HELD_SOURCE")?;
    report.cancelled = File::from(held)
        .write_all(b"x")
        .is_err_and(|e| e.kind() == std::io::ErrorKind::BrokenPipe);
    focus(&control, 1, &stop)?;
    a.sync(&stop, deadline)?;
    b.sync(&stop, deadline)?;
    a.offer(TEXT.to_vec(), a.state.input_serial, true);
    a.sync(&stop, deadline)?;
    focus(&control, 2, &stop)?;
    a.sync(&stop, deadline)?;
    b.sync(&stop, deadline)?;
    let selection = b
        .state
        .clipboard
        .as_ref()
        .unwrap()
        .selection
        .clone()
        .ok_or("NO_SELECTION")?;
    let mut read = b.read_offer(selection)?;
    b.sync(&stop, deadline)?;
    a.sync(&stop, deadline)?;
    for writer in a.state.clipboard.as_mut().unwrap().writers.drain(..) {
        writer.join().map_err(|_| "WRITER_PANIC")?;
    }
    drop(a); // 転送中に実Wayland接続を切断する。
    loop {
        if stop.load(Ordering::Acquire) || Instant::now() >= deadline {
            return Err("OWNER_LOST_TIMEOUT");
        }
        b.sync(&stop, deadline)?;
        let mut byte = [0];
        match read.read(&mut byte) {
            Ok(0) => break,
            Ok(_) => return Err("OWNER_LOST_DATA"),
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(1))
            }
            Err(_) => return Err("OWNER_LOST_READ"),
        }
    }
    report.owner_lost = b.state.report.empty_selection;
    b.finish(&stop, deadline)?;
    Ok(report)
}
