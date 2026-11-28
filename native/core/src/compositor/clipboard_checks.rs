//! 二つの実Wayland clientと、上限/timeout/切断を製品protocolで検査する。
use super::{
    checks::{fixture_resources, object_counts},
    protocol::ProtocolState,
};
use eidolon_model::{
    ids::{SessionId, WindowId},
    keyboard::KeyboardLayout,
};
use eidolon_shm_fixture::clipboard::{self as fixture, Control};
use serde::Serialize;
use smithay::reexports::wayland_server::Display;
use std::{
    os::unix::net::UnixStream,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    thread,
    time::{Duration, Instant},
};
#[derive(Debug, Default, Serialize)]
pub struct Report {
    pub uid: u32,
    pub physical_input_verified: bool,
    pub elapsed_ms: u64,
    pub state: &'static str,
    pub fixture_revision: &'static str,
    pub synthetic_input: bool,
    pub client: Option<fixture::Report>,
    pub client_error: Option<&'static str>,
    pub input: Option<super::protocol::InputReport>,
    pub clients_after: usize,
    pub objects_after: usize,
    pub surfaces_after: usize,
    pub windows_after: usize,
    pub pixel_bytes_after: usize,
    pub pool_bytes_after: usize,
    pub fixture_fds_after: usize,
    pub fixture_maps_after: usize,
    pub error: Option<&'static str>,
}
pub fn run(stop: Arc<AtomicBool>) -> Report {
    let started = Instant::now();
    let mut report = Report {
        state: "running",
        fixture_revision: "eidolon-clipboard-wire-fixture-v1",
        synthetic_input: true,
        ..Default::default()
    };
    // SAFETY: getuidはpointerを受け取らない。
    report.uid = unsafe { libc::getuid() };
    let outcome = (|| {
        if fixture_resources()? != (0, 0) {
            return Err("FIXTURE_ALREADY_RUNNING");
        }
        let mut display = Display::<ProtocolState>::new().map_err(|_| "DISPLAY")?;
        let mut state = ProtocolState::new(&display.handle(), SessionId::new(1).unwrap());
        state.configure_keyboard(KeyboardLayout::Us)?;
        let (sa, ca) = UnixStream::pair().map_err(|_| "SOCKET")?;
        let (sb, cb) = UnixStream::pair().map_err(|_| "SOCKET")?;
        state.accept(&display.handle(), sa)?;
        state.accept(&display.handle(), sb)?;
        let (tx, rx) = mpsc::sync_channel(1);
        let (reply, answers) = mpsc::sync_channel(1);
        let child_stop = stop.clone();
        let worker = thread::spawn(move || {
            fixture::run(
                [ca, cb],
                child_stop,
                Control {
                    focus: tx,
                    replies: answers,
                },
            )
        });
        let deadline = Instant::now() + Duration::from_secs(35);
        let drive = (|| {
            while !worker.is_finished() {
                if stop.load(Ordering::Acquire) || Instant::now() >= deadline {
                    return Err("CANCELLED_OR_TIMEOUT");
                }
                display
                    .dispatch_clients(&mut state)
                    .map_err(|_| "DISPATCH")?;
                state.finish_dispatch(&display.handle());
                if let Ok(window) = rx.try_recv() {
                    state.focus_window(WindowId::new(i64::from(window)).unwrap())?;
                    reply.send(()).map_err(|_| "REPLY")?;
                }
                display.flush_clients().map_err(|_| "FLUSH")?;
                thread::sleep(Duration::from_millis(1));
            }
            Ok(())
        })();
        if drive.is_err() {
            stop.store(true, Ordering::Release);
        }
        let client = worker.join().map_err(|_| "CLIENT_PANIC")?;
        // Smithay releases SHM mappings/FDs and their PoolLease on its drop
        // thread. A fixed number of dispatches cannot prove that work finished.
        // Wait for this display's actual reclamation before sampling the report.
        let cleanup_deadline = Instant::now() + Duration::from_secs(3);
        let reclaimed = loop {
            display
                .dispatch_clients(&mut state)
                .map_err(|_| "DISPATCH")?;
            state.finish_dispatch(&display.handle());
            let pools = state.pool_usage();
            if object_counts(&display) == (0, 0)
                && state.surface_count() == 0
                && state.windows().is_empty()
                && state.pixel_bytes() == 0
                && pools.pools == 0
                && pools.bytes == 0
            {
                break true;
            }
            if Instant::now() >= cleanup_deadline {
                break false;
            }
            thread::sleep(Duration::from_millis(1));
        };
        match client {
            Ok(client) => report.client = Some(client),
            Err(error) => report.client_error = Some(error),
        }
        report.input = Some(state.input_report());
        (report.clients_after, report.objects_after) = object_counts(&display);
        report.surfaces_after = state.surface_count();
        report.windows_after = state.windows().len();
        report.pixel_bytes_after = state.pixel_bytes();
        report.pool_bytes_after = state.pool_usage().bytes;
        (report.fixture_fds_after, report.fixture_maps_after) = fixture_resources()?;
        drive?;
        let c = report.client.as_ref().ok_or("CLIENT_FAILED")?;
        if !(c.copied
            && c.at_limit
            && c.rejected_serial
            && c.rejected_focus
            && c.stale_offer
            && c.invalid_utf8
            && c.oversized
            && c.stalled
            && c.stalled_receiver
            && c.cancelled
            && c.owner_lost
            && c.mime_filtered)
        {
            return Err("CLIENT_PROOF");
        }
        let clip = state.clipboard_report();
        if clip.completed != 2
            || clip.oversized != 1
            || clip.invalid_utf8 != 1
            || clip.timed_out != 2
            || clip.cancelled < 1
            || clip.active != 0
            || clip.selection
        {
            return Err("CLIPBOARD_COUNTERS");
        }
        if !reclaimed
            || report.clients_after != 0
            || report.objects_after != 0
            || report.surfaces_after != 0
            || report.windows_after != 0
            || report.pixel_bytes_after != 0
            || report.pool_bytes_after != 0
            || report.fixture_fds_after != 0
            || report.fixture_maps_after != 0
        {
            return Err("RESOURCE_LEAK");
        }
        Ok(())
    })();
    report.elapsed_ms = started.elapsed().as_millis() as u64;
    report.error = outcome.err();
    report.state = if report.error.is_none() {
        "pass"
    } else {
        "fail"
    };
    report
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::clipboard_shelf::{ClipboardGrant, ClipboardPermit};
    use eidolon_model::ids::ClipboardCardId;
    use fixture::ShelfStep;

    #[test]
    fn clipboard_two_clients_bounds_timeout_and_disconnect() {
        let _lock = super::super::FIXTURE_LOCK.lock().unwrap();
        let report = super::run(std::sync::Arc::new(std::sync::atomic::AtomicBool::new(
            false,
        )));
        assert_eq!(report.state, "pass", "{report:#?}");
    }

    struct DisplayOwner {
        display: Display<ProtocolState>,
        protocol: ProtocolState,
    }

    impl DisplayOwner {
        fn new(session: i64) -> Self {
            let display = Display::new().unwrap();
            let mut protocol =
                ProtocolState::new(&display.handle(), SessionId::new(session).unwrap());
            protocol.configure_keyboard(KeyboardLayout::Us).unwrap();
            Self { display, protocol }
        }

        fn connect(&mut self) -> UnixStream {
            let (server, client) = UnixStream::pair().unwrap();
            self.protocol
                .accept(&self.display.handle(), server)
                .unwrap();
            client
        }

        fn tick(&mut self) {
            self.display.dispatch_clients(&mut self.protocol).unwrap();
            self.protocol.finish_dispatch(&self.display.handle());
            self.display.flush_clients().unwrap();
        }
    }

    struct Clients {
        steps: mpsc::SyncSender<ShelfStep>,
        results: mpsc::Receiver<Result<(), &'static str>>,
        thread: thread::JoinHandle<Result<(), &'static str>>,
    }

    impl Clients {
        fn new(server: &mut DisplayOwner, stop: &Arc<AtomicBool>) -> Self {
            let stream = server.connect();
            let (steps, requests) = mpsc::sync_channel(1);
            let (replies, results) = mpsc::sync_channel(1);
            let stop = Arc::clone(stop);
            let thread =
                thread::spawn(move || fixture::run_shelf_client(stream, stop, requests, replies));
            Self {
                steps,
                results,
                thread,
            }
        }

        fn step(&self, servers: &mut [DisplayOwner], step: ShelfStep) {
            self.steps
                .try_send(step)
                .unwrap_or_else(|_| panic!("fixture mailbox busy"));
            drive(servers, || match self.results.try_recv() {
                Ok(result) => {
                    result.unwrap();
                    Some(())
                }
                Err(mpsc::TryRecvError::Empty) => None,
                Err(_) => panic!("client disconnected"),
            });
        }
    }

    fn drive<T>(servers: &mut [DisplayOwner], mut observe: impl FnMut() -> Option<T>) -> T {
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            for server in servers.iter_mut() {
                server.tick();
            }
            if let Some(result) = observe() {
                return result;
            }
            assert!(Instant::now() < deadline, "fixture deadline");
            thread::sleep(Duration::from_millis(1));
        }
    }

    #[test]
    fn shelf_copies_between_displays_preserving_destination_selection_and_focus() {
        let _lock = super::super::FIXTURE_LOCK.lock().unwrap();
        let stop = Arc::new(AtomicBool::new(false));
        let mut servers = [DisplayOwner::new(1), DisplayOwner::new(2)];
        let source = Clients::new(&mut servers[0], &stop);
        let destination = Clients::new(&mut servers[1], &stop);
        let [first, second] = [1, 2].map(|n| WindowId::new(n).unwrap());
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            for server in &mut servers {
                server.tick();
            }
            let source_ready = servers[0].protocol.focus_window(first).is_ok();
            let destination_ready = servers[1].protocol.focus_window(first).is_ok();
            if source_ready && destination_ready {
                break;
            }
            assert!(
                Instant::now() < deadline
                    && !source.thread.is_finished()
                    && !destination.thread.is_finished()
            );
            thread::sleep(Duration::from_millis(1));
        }
        let other = Clients::new(&mut servers[1], &stop);
        let text = "日本語\nsecond line".as_bytes().to_vec();
        let original = b"destination's original clipboard".to_vec();
        source.step(&mut servers, ShelfStep::Copy(text.clone()));
        destination.step(&mut servers, ShelfStep::Copy(original.clone()));
        let source_focus = servers[0].protocol.input_report().focus_token.unwrap();
        assert!(
            servers[0]
                .protocol
                .capture_selection(second, source_focus)
                .is_err()
        );
        let capture = servers[0]
            .protocol
            .capture_selection(first, source_focus)
            .unwrap();
        let bytes = drive(&mut servers, || match capture.try_recv() {
            Ok(result) => Some(result.unwrap()),
            Err(mpsc::TryRecvError::Empty) => None,
            Err(_) => panic!("capture disconnected"),
        });
        assert_eq!(bytes, text);
        let target_focus = servers[1].protocol.input_report().focus_token.unwrap();
        let permit = ClipboardPermit::new(Instant::now() + Duration::from_secs(2));
        let grant = || ClipboardGrant {
            card: ClipboardCardId::new(1).unwrap(),
            text: Arc::from(std::str::from_utf8(&bytes).unwrap()),
            permit: permit.clone(),
        };
        assert_eq!(
            servers[1]
                .protocol
                .grant_shelf_clipboard(second, target_focus, grant()),
            Err("STALE")
        );
        servers[1]
            .protocol
            .grant_shelf_clipboard(first, target_focus, grant())
            .unwrap();
        other.step(&mut servers, ShelfStep::NoOffer);
        destination.step(&mut servers, ShelfStep::Paste(text));
        permit.revoke();
        destination.step(&mut servers, ShelfStep::Paste(original));
        source.step(&mut servers, ShelfStep::Finish);
        destination.step(&mut servers, ShelfStep::Finish);
        other.step(&mut servers, ShelfStep::Finish);
        assert!(source.thread.join().unwrap().is_ok());
        assert!(destination.thread.join().unwrap().is_ok());
        assert!(other.thread.join().unwrap().is_ok());
    }
}
