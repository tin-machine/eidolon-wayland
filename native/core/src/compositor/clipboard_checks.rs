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
    #[test]
    fn clipboard_two_clients_bounds_timeout_and_disconnect() {
        let _lock = super::super::FIXTURE_LOCK.lock().unwrap();
        let report = super::run(std::sync::Arc::new(std::sync::atomic::AtomicBool::new(
            false,
        )));
        assert_eq!(report.state, "pass", "{report:#?}");
    }
}
