//! 実Wayland clientで余剰role拒否と、同clientの既存窓継続・後始末を検査する。
use super::{
    checks::{fixture_resources, object_counts},
    protocol::ProtocolState,
};
use eidolon_model::{
    ids::{SessionId, WindowId},
    limits,
    window::WindowLife,
};
use eidolon_shm_fixture::capacity::{self as fixture, Stage};
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
    pub state: &'static str,
    pub fixture_revision: &'static str,
    pub capacity: usize,
    pub checkpoints: usize,
    pub rejected_not_copied: bool,
    pub existing_client_preserved: bool,
    pub vacancy_reused: bool,
    pub extra_client_rejected: bool,
    pub client: Option<fixture::Report>,
    pub clients_after: usize,
    pub objects_after: usize,
    pub surfaces_after: usize,
    pub windows_after: usize,
    pub rejected_after: usize,
    pub pixel_bytes_after: usize,
    pub pool_bytes_after: usize,
    pub fixture_fds_after: usize,
    pub fixture_maps_after: usize,
    pub error: Option<&'static str>,
}
pub fn run(stop: Arc<AtomicBool>) -> Report {
    let mut report = Report {
        state: "running",
        fixture_revision: "eidolon-toplevel-capacity-v1",
        capacity: limits::TOPLEVELS,
        ..Default::default()
    };
    let result = (|| {
        if fixture_resources()? != (0, 0) {
            return Err("FIXTURE_BUSY");
        }
        let mut display = Display::<ProtocolState>::new().map_err(|_| "DISPLAY_CREATE")?;
        let mut state = ProtocolState::new(&display.handle(), SessionId::new(1).unwrap());
        let (server, peer) = UnixStream::pair().map_err(|_| "SOCKET_PAIR")?;
        let interrupt = peer.try_clone().map_err(|_| "SOCKET_CLONE")?;
        state.accept(&display.handle(), server)?;
        let (done_tx, done_rx) = mpsc::sync_channel(1);
        let (steps_tx, steps_rx) = mpsc::sync_channel(1);
        let (replies_tx, replies_rx) = mpsc::sync_channel(1);
        let cancel = Arc::new(AtomicBool::new(false));
        let client_stop = cancel.clone();
        let worker = thread::Builder::new()
            .name("capacity-fixture".into())
            .spawn(move || {
                let _ = done_tx.send(fixture::run(
                    peer,
                    limits::TOPLEVELS,
                    client_stop,
                    fixture::Control {
                        steps: steps_tx,
                        replies: replies_rx,
                    },
                ));
            })
            .map_err(|_| "FIXTURE_THREAD")?;
        let deadline = Instant::now() + Duration::from_secs(25);
        let mut idle_peers = vec![];
        let mut original = vec![];
        let mut removed = None;
        let mut copies = 0;
        let mut bytes = 0;
        let outcome = loop {
            if stop.load(Ordering::Acquire) {
                break Err("CANCELLED");
            }
            if Instant::now() >= deadline {
                break Err("SERVER_TIMEOUT");
            }
            if display.dispatch_clients(&mut state).is_err() {
                break Err("SERVER_DISPATCH");
            }
            state.finish_dispatch(&display.handle());
            if display.flush_clients().is_err() {
                break Err("SERVER_FLUSH");
            }
            if let Ok(stage) = steps_rx.try_recv() {
                let mut windows = state.windows();
                windows.sort_by_key(|w| w.window.get());
                let ids = windows.iter().map(|w| w.window).collect::<Vec<_>>();
                let check = (|| {
                    if object_counts(&display).0
                        != if idle_peers.is_empty() {
                            1
                        } else {
                            limits::CLIENTS
                        }
                        || windows.iter().any(|w| w.life != WindowLife::Mapped)
                    {
                        return Err("SURVIVOR_NOT_MAPPED");
                    }
                    match stage {
                        Stage::Full => {
                            if ids.len() != limits::TOPLEVELS
                                || state.rejected_toplevel_count() != 0
                            {
                                return Err("INITIAL_COUNT");
                            }
                            for _ in 1..limits::CLIENTS {
                                let (server, peer) =
                                    UnixStream::pair().map_err(|_| "SOCKET_PAIR")?;
                                state.accept(&display.handle(), server)?;
                                idle_peers.push(peer);
                            }
                            let (extra, peer) = UnixStream::pair().map_err(|_| "SOCKET_PAIR")?;
                            if !matches!(
                                state.accept(&display.handle(), extra),
                                Err("RESOURCE_LIMIT")
                            ) || state.counters.client_rejections != 1
                                || object_counts(&display).0 != limits::CLIENTS
                            {
                                return Err("EXTRA_CLIENT_NOT_ISOLATED");
                            }
                            drop(peer);
                            report.extra_client_rejected = true;
                            original = ids;
                            copies = state.counters.copies;
                            bytes = state.pixel_bytes();
                            removed = windows
                                .iter()
                                .find(|w| {
                                    w.app_id.as_deref()
                                        == Some(
                                            format!("fixture.capacity.{}", limits::TOPLEVELS - 1)
                                                .as_str(),
                                        )
                                })
                                .map(|w| w.window);
                            if removed.is_none() {
                                return Err("MISSING_REMOVED");
                            }
                        }
                        Stage::RejectedCommit => {
                            if ids != original
                                || state.rejected_toplevel_count() != 1
                                || state.counters.toplevel_rejections != 1
                                || state.counters.copies != copies
                                || state.pixel_bytes() != bytes
                            {
                                return Err("REJECTED_CHANGED_EXISTING");
                            }
                            report.rejected_not_copied = true;
                        }
                        Stage::RejectedDestroyed => {
                            if ids != original || state.rejected_toplevel_count() != 0 {
                                return Err("REJECTED_NOT_COLLECTED");
                            }
                        }
                        Stage::SurvivorUpdated => {
                            if ids != original
                                || state.counters.copies != copies + 1
                                || state.pixel_bytes() != bytes
                            {
                                return Err("SURVIVOR_NOT_UPDATING");
                            }
                            report.existing_client_preserved = true;
                        }
                        Stage::Vacancy => {
                            let expected = original
                                .iter()
                                .filter(|id| Some(**id) != removed)
                                .copied()
                                .collect::<Vec<_>>();
                            if ids != expected {
                                return Err("VACANCY_CHANGED_OTHER_WINDOWS");
                            }
                        }
                        Stage::Replacement => {
                            let survivors = original
                                .iter()
                                .filter(|id| Some(**id) != removed)
                                .copied()
                                .collect::<Vec<_>>();
                            let added = ids
                                .iter()
                                .filter(|id| !original.contains(id))
                                .copied()
                                .collect::<Vec<WindowId>>();
                            if ids.len() != limits::TOPLEVELS
                                || !survivors.iter().all(|id| ids.contains(id))
                                || added.len() != 1
                                || added[0].get()
                                    <= original.iter().map(|id| id.get()).max().unwrap()
                                || state.rejected_toplevel_count() != 0
                            {
                                return Err("REPLACEMENT_IDENTITY");
                            }
                            report.vacancy_reused = true;
                        }
                    }
                    report.checkpoints += 1;
                    Ok(())
                })();
                if let Err(error) = check {
                    break Err(error);
                }
                if replies_tx.try_send(()).is_err() {
                    break Err("CHECKPOINT_REPLY");
                }
            }
            match done_rx.try_recv() {
                Ok(result) => break result,
                Err(mpsc::TryRecvError::Disconnected) => break Err("FIXTURE_PANIC"),
                Err(mpsc::TryRecvError::Empty) => (),
            }
            thread::sleep(Duration::from_millis(1));
        };
        cancel.store(true, Ordering::Release);
        let _ = interrupt.shutdown(std::net::Shutdown::Both);
        worker.join().map_err(|_| "FIXTURE_PANIC")?;
        drop(interrupt);
        drop(idle_peers);
        let cleanup_deadline = Instant::now() + Duration::from_secs(3);
        loop {
            display
                .dispatch_clients(&mut state)
                .map_err(|_| "SERVER_DISPATCH")?;
            state.finish_dispatch(&display.handle());
            let _ = display.flush_clients();
            if object_counts(&display) == (0, 0) && state.pool_usage().pools == 0 {
                break;
            }
            if Instant::now() >= cleanup_deadline {
                return Err("CLIENT_CLEANUP");
            }
            thread::sleep(Duration::from_millis(1));
        }
        (report.clients_after, report.objects_after) = object_counts(&display);
        report.surfaces_after = state.surface_count();
        report.windows_after = state.windows().len();
        report.rejected_after = state.rejected_toplevel_count();
        report.pixel_bytes_after = state.pixel_bytes();
        report.pool_bytes_after = state.pool_usage().bytes;
        (report.fixture_fds_after, report.fixture_maps_after) = fixture_resources()?;
        report.client = Some(outcome?);
        if report.checkpoints != 6
            || !report.rejected_not_copied
            || !report.existing_client_preserved
            || !report.vacancy_reused
            || !report.extra_client_rejected
        {
            return Err("PROOF_INCOMPLETE");
        }
        if [
            report.clients_after,
            report.objects_after,
            report.surfaces_after,
            report.windows_after,
            report.rejected_after,
            report.pixel_bytes_after,
            report.pool_bytes_after,
            report.fixture_fds_after,
            report.fixture_maps_after,
        ]
        .iter()
        .any(|n| *n != 0)
        {
            return Err("RESOURCE_LEAK");
        }
        Ok(())
    })();
    report.error = result.err();
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
    fn excess_toplevel_keeps_existing_shared_client_alive_and_collects_resources() {
        let _lock = super::super::FIXTURE_LOCK.lock().unwrap();
        let report = super::run(std::sync::Arc::new(std::sync::atomic::AtomicBool::new(
            false,
        )));
        assert_eq!(report.state, "pass", "{report:#?}");
    }
}
