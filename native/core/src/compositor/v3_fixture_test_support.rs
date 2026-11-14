//! Host-only fixture ownership. Assertions must not detach a live client thread.
use super::ProtocolState;
use crate::compositor::checks::object_counts;
use smithay::reexports::wayland_server::Display;
use std::{
    net::Shutdown,
    os::unix::net::UnixStream,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

pub(super) struct FixtureWorker {
    interrupt: UnixStream,
    stop: Arc<AtomicBool>,
    worker: Option<JoinHandle<()>>,
}

impl FixtureWorker {
    pub(super) fn spawn(
        interrupt: UnixStream,
        stop: Arc<AtomicBool>,
        run: impl FnOnce() + Send + 'static,
    ) -> Self {
        Self {
            interrupt,
            stop,
            worker: Some(thread::spawn(run)),
        }
    }

    fn stop_and_join(&mut self) -> thread::Result<()> {
        self.stop.store(true, Ordering::Release);
        let _ = self.interrupt.shutdown(Shutdown::Both);
        match self.worker.take() {
            Some(worker) => worker.join(),
            None => Ok(()),
        }
    }

    pub(super) fn join(mut self) -> thread::Result<()> {
        self.stop_and_join()
    }
}

impl Drop for FixtureWorker {
    fn drop(&mut self) {
        // Fixture pumps and control-channel waits check this cancellation flag
        // and have deadlines. Preserve the original assertion during unwind.
        let _ = self.stop_and_join();
    }
}

pub(super) fn run_serially(case: fn()) {
    let guard = crate::compositor::FIXTURE_LOCK.lock().unwrap();
    let outcome = std::panic::catch_unwind(case);
    // Report the original failure, but do not poison the shared fixture lock
    // and turn unrelated tests into a cascade of PoisonError diagnostics.
    drop(guard);
    if let Err(payload) = outcome {
        std::panic::resume_unwind(payload);
    }
}

pub(super) fn drain_fixture(display: &mut Display<ProtocolState>, protocol: &mut ProtocolState) {
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        display.dispatch_clients(protocol).unwrap();
        protocol.finish_dispatch(&display.handle());
        // The peer has already exited; a closed-client flush is not a receipt.
        let _ = display.flush_clients();
        let objects = object_counts(display);
        let pools = protocol.pool_usage();
        if objects == (0, 0)
            && protocol.surface_count() == 0
            && protocol.windows().is_empty()
            && protocol.pixel_bytes() == 0
            && pools.pools == 0
            && pools.bytes == 0
        {
            return;
        }
        assert!(
            Instant::now() < deadline,
            "fixture cleanup pending: objects={objects:?}, surfaces={}, pools={}, bytes={}",
            protocol.surface_count(),
            pools.pools,
            pools.bytes
        );
        thread::sleep(Duration::from_millis(1));
    }
}

#[test]
fn assertion_unwind_cancels_and_joins_fixture_worker() {
    use std::io::Read;
    let (_server, mut client) = UnixStream::pair().unwrap();
    client
        .set_read_timeout(Some(Duration::from_secs(2)))
        .unwrap();
    let interrupt = client.try_clone().unwrap();
    let stop = Arc::new(AtomicBool::new(false));
    let exited = Arc::new(AtomicBool::new(false));
    let cancellation = stop.clone();
    let finished = exited.clone();
    let result = std::panic::catch_unwind(move || {
        let _worker = FixtureWorker::spawn(interrupt, cancellation, move || {
            let mut byte = [0u8];
            assert!(matches!(client.read(&mut byte), Ok(0)));
            finished.store(true, Ordering::Release);
        });
        panic!("intentional fixture assertion");
    });
    assert!(result.is_err());
    assert!(stop.load(Ordering::Acquire));
    assert!(exited.load(Ordering::Acquire));
}

#[test]
fn explicit_join_preserves_fixture_worker_failure() {
    let (_server, client) = UnixStream::pair().unwrap();
    let stop = Arc::new(AtomicBool::new(false));
    let worker = FixtureWorker::spawn(client, stop.clone(), || {
        panic!("intentional fixture worker failure");
    });
    assert!(worker.join().is_err());
    assert!(stop.load(Ordering::Acquire));
}

#[test]
fn failed_case_does_not_poison_the_shared_fixture_lock() {
    let result = std::panic::catch_unwind(|| {
        run_serially(|| panic!("intentional fixture case failure"));
    });
    assert!(result.is_err());
    run_serially(|| {});
}
