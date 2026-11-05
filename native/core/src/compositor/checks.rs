//! 同じDisplayへ悪性clientと正常clientを順に接続する。製品描画の成功判定には使わない。
use super::protocol::ProtocolState;
use eidolon_shm_fixture::{FRAMES, Mode};
use serde::Serialize;
use sha2::{Digest, Sha256};
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
    pub mode: String,
    pub uid: u32,
    pub client: Option<eidolon_shm_fixture::Report>,
    pub recovery: Option<eidolon_shm_fixture::Report>,
    pub verified_hashes: u32,
    pub server_configures: u64,
    pub server_acks: u64,
    pub server_copies: u64,
    pub server_releases: u64,
    pub server_callbacks: u64,
    pub bad_maps: u64,
    pub surfaces_after: usize,
    pub clients_after: usize,
    pub objects_after: usize,
    pub pixel_bytes_after: usize,
    pub pool_bytes_after: usize,
    pub pool_count_after: usize,
    pub fixture_fds_after: usize,
    pub fixture_maps_after: usize,
    pub elapsed_ms: u64,
    pub error: Option<&'static str>,
}

pub(super) fn object_counts(display: &Display<ProtocolState>) -> (usize, usize) {
    let backend = display.handle().backend_handle();
    let mut clients = vec![];
    backend.with_all_clients(|client| clients.push(client));
    let mut objects = 0;
    for client in &clients {
        let _ = backend.with_all_objects_for(client.clone(), |_| objects += 1);
    }
    (clients.len(), objects)
}

pub(super) fn fixture_resources() -> Result<(usize, usize), &'static str> {
    let fds = std::fs::read_dir("/proc/self/fd")
        .map_err(|_| "PROC_FD")?
        .filter_map(Result::ok)
        .filter_map(|entry| std::fs::read_link(entry.path()).ok())
        .filter(|path| path.to_string_lossy().contains("memfd:eidolon-shm-fixture"))
        .count();
    let maps = std::fs::read_to_string("/proc/self/maps")
        .map_err(|_| "PROC_MAPS")?
        .lines()
        .filter(|line| line.contains("memfd:eidolon-shm-fixture"))
        .count();
    Ok((fds, maps))
}

fn client(
    display: &mut Display<ProtocolState>,
    state: &mut ProtocolState,
    mode: Mode,
    stop: &Arc<AtomicBool>,
    verified: &mut u32,
) -> Result<eidolon_shm_fixture::Report, &'static str> {
    let (server, peer) = UnixStream::pair().map_err(|_| "SOCKET_PAIR")?;
    let interrupt = peer.try_clone().map_err(|_| "SOCKET_CLONE")?;
    state.accept(&display.handle(), server)?;
    let (tx, rx) = mpsc::sync_channel(1);
    let client_stop = stop.clone();
    let worker = thread::Builder::new()
        .name("shm-fixture".into())
        .spawn(move || {
            let _ = tx.send(eidolon_shm_fixture::run(peer, mode, client_stop));
        })
        .map_err(|_| "FIXTURE_THREAD")?;
    let deadline = Instant::now() + Duration::from_secs(25);
    let mut copied = state.counters.copies;
    let mut virtual_tick = 0u64;
    let outcome = loop {
        if stop.load(Ordering::Acquire) {
            break Err("CANCELLED");
        }
        if Instant::now() >= deadline {
            break Err("SERVER_TIMEOUT");
        }
        if display.dispatch_clients(state).is_err() {
            break Err("SERVER_DISPATCH");
        }
        state.finish_dispatch(&display.handle());
        if state.counters.copies > copied {
            if !matches!(mode, Mode::Pattern | Mode::SharedCache)
                || state.counters.copies != copied + 1
            {
                break Err("COPY_SEQUENCE");
            }
            let surfaces = state.surfaces_for_check();
            let images = surfaces
                .iter()
                .filter_map(|surface| state.image(surface))
                .collect::<Vec<_>>();
            let frame = if mode == Mode::SharedCache {
                0
            } else {
                *verified % FRAMES
            };
            let count = if mode == Mode::SharedCache {
                (*verified + 1) as usize
            } else {
                1
            };
            if images.len() != count
                || images.iter().any(|image| {
                    Sha256::digest(image.pixels())
                        .iter()
                        .map(|byte| format!("{byte:02x}"))
                        .collect::<String>()
                        != eidolon_shm_fixture::expected_hash(frame)
                })
            {
                break Err("PIXEL_HASH");
            }
            *verified += 1;
            copied += 1;
        }
        // このfixed fixtureだけに仮想presentを通知する。通常製品のcallback schedulerとは別。
        let virtual_now = Duration::from_nanos(virtual_tick * 16_666_667);
        virtual_tick += 1;
        for window in state.windows() {
            state.frame_callbacks(window.window, virtual_now, true, true, true);
        }
        if display.flush_clients().is_err() {
            break Err("SERVER_FLUSH");
        }
        match rx.try_recv() {
            Ok(result) => break result,
            Err(mpsc::TryRecvError::Disconnected) => break Err("FIXTURE_PANIC"),
            Err(mpsc::TryRecvError::Empty) => (),
        }
        thread::sleep(Duration::from_millis(1));
    };
    // エラー時もsocketをinterruptして期限付きpumpを終え、所有threadを必ずjoinする。
    let _ = interrupt.shutdown(std::net::Shutdown::Both);
    worker.join().map_err(|_| "FIXTURE_PANIC")?;
    drop(interrupt);
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        display
            .dispatch_clients(state)
            .map_err(|_| "SERVER_DISPATCH")?;
        state.finish_dispatch(&display.handle());
        let _ = display.flush_clients();
        if object_counts(display) == (0, 0) && state.pool_usage().pools == 0 {
            break;
        }
        if Instant::now() >= deadline {
            return Err("CLIENT_CLEANUP");
        }
        thread::sleep(Duration::from_millis(1));
    }
    outcome
}

pub fn run(mode: Mode, stop: Arc<AtomicBool>) -> Report {
    let started = Instant::now();
    let mut report = Report {
        state: "running",
        fixture_revision: "eidolon-shm-client-v1",
        mode: format!("{mode:?}"),
        ..Default::default()
    };
    // SAFETY: getuidは引数・ポインターなし。
    report.uid = unsafe { libc::getuid() };
    let outcome = (|| {
        if fixture_resources()? != (0, 0) {
            return Err("FIXTURE_ALREADY_RUNNING");
        }
        let mut display = Display::<ProtocolState>::new().map_err(|_| "DISPLAY_CREATE")?;
        let mut state = ProtocolState::new(
            &display.handle(),
            eidolon_model::ids::SessionId::new(1).unwrap(),
        );
        let outcome = (|| {
            let first = client(
                &mut display,
                &mut state,
                mode,
                &stop,
                &mut report.verified_hashes,
            )?;
            if !matches!(mode, Mode::Pattern | Mode::SharedCache) && !first.protocol_error {
                return Err("BAD_CLIENT_NOT_REJECTED");
            }
            report.client = Some(first);
            if !matches!(mode, Mode::Pattern | Mode::SharedCache) {
                report.recovery = Some(client(
                    &mut display,
                    &mut state,
                    Mode::Pattern,
                    &stop,
                    &mut report.verified_hashes,
                )?);
            }
            Ok(())
        })();
        report.server_configures = state.counters.configures;
        report.server_acks = state.counters.acks;
        report.server_copies = state.counters.copies;
        report.server_releases = state.counters.releases;
        report.server_callbacks = state.counters.callbacks;
        report.bad_maps = state.counters.bad_maps;
        report.surfaces_after = state.surface_count();
        report.pixel_bytes_after = state.pixel_bytes();
        report.pool_bytes_after = state.pool_usage().bytes;
        report.pool_count_after = state.pool_usage().pools;
        (report.clients_after, report.objects_after) = object_counts(&display);
        (report.fixture_fds_after, report.fixture_maps_after) = fixture_resources()?;
        outcome?;
        let normal = report
            .recovery
            .as_ref()
            .or(report.client.as_ref())
            .ok_or("MISSING_REPORT")?;
        let mut globals = normal.globals.clone();
        globals.sort();
        let mut expected = vec![
            ("wl_compositor".to_owned(), 4),
            ("wl_subcompositor".into(), 1),
            ("wl_shm".into(), 1),
            ("wl_output".into(), 3),
            ("wl_seat".into(), 5),
            ("xdg_wm_base".into(), 1),
            ("wl_data_device_manager".into(), 3),
        ];
        expected.sort();
        let (frames, configurations, mapped, copies) = if mode == Mode::SharedCache {
            (1, 2, 2, 2)
        } else {
            (FRAMES, 1, 1, FRAMES)
        };
        if globals != expected
            || normal.frames != frames
            || normal.configures != configurations
            || normal.acks != configurations
            || normal.callbacks != frames
            || normal.releases != frames
            || normal.enters != mapped
            || normal.leaves != mapped
            || normal.cancellations != 2
            || !normal.empty_selection
            || normal.output_mode != Some((1024, 768, 60000))
            || normal.output_scale != 1
            || !normal.output_geometry
            || !normal.output_done
            || normal.seat_capabilities != Some(0)
            || report.verified_hashes != copies
            || report.server_copies != u64::from(copies)
            || report.server_callbacks != u64::from(frames)
            || report.server_releases != u64::from(frames)
            || report.bad_maps != u64::from(mode == Mode::Truncate)
        {
            return Err("PROTOCOL_EVIDENCE");
        }
        if mode == Mode::SharedCache && !normal.release_deferred {
            return Err("RELEASE_NOT_DEFERRED");
        }
        if report.surfaces_after != 0
            || report.clients_after != 0
            || report.objects_after != 0
            || report.pixel_bytes_after != 0
            || report.pool_bytes_after != 0
            || report.pool_count_after != 0
            || report.fixture_fds_after != 0
            || report.fixture_maps_after != 0
        {
            return Err("RESOURCE_CLEANUP");
        }
        Ok(())
    })();
    report.elapsed_ms = started.elapsed().as_millis() as u64;
    report.error = outcome.err();
    report.state = if report.error.is_some() {
        "fail"
    } else {
        "pass"
    };
    report
}

/// 固定scenarioをdebug専用workerへ振り分け、応答を同じnonceへ束縛する。
pub fn run_scenario(
    scenario: eidolon_model::commands::CompositorScenario,
    stop: Arc<AtomicBool>,
    nonce: String,
) -> serde_json::Value {
    use eidolon_model::commands::CompositorScenario as S;
    use eidolon_shm_fixture::state::Scenario as F;
    if matches!(scenario, S::Pointer) {
        let mut report = serde_json::to_value(super::pointer_checks::run(stop)).unwrap();
        report["nonce"] = nonce.into();
        return report;
    }
    if matches!(scenario, S::KeyboardUs | S::KeyboardJis) {
        let layout = if matches!(scenario, S::KeyboardUs) {
            eidolon_model::keyboard::KeyboardLayout::Us
        } else {
            eidolon_model::keyboard::KeyboardLayout::Jis
        };
        let mut report = serde_json::to_value(super::input_checks::run(layout, stop)).unwrap();
        report["nonce"] = nonce.into();
        return report;
    }
    enum Probe {
        Shm(Mode),
        State(F),
    }
    let probe = match scenario {
        S::Pointer | S::KeyboardUs | S::KeyboardJis => unreachable!(),
        S::Lifecycle => Probe::State(F::Lifecycle),
        S::Transforms => Probe::State(F::Transforms),
        S::FrameGates => Probe::State(F::FrameGates),
        S::Tree => Probe::State(F::Tree),
        S::BadSerial => Probe::State(F::BadSerial),
        S::StaleConfigure => Probe::State(F::StaleConfigure),
        S::PrematureBuffer => Probe::State(F::PrematureBuffer),
        S::UnconfiguredRemap => Probe::State(F::UnconfiguredRemap),
        S::BadScale => Probe::State(F::BadScale),
        S::PositionerOverflow => Probe::State(F::PositionerOverflow),
        S::Pattern => Probe::Shm(Mode::Pattern),
        S::SharedCache => Probe::Shm(Mode::SharedCache),
        S::InvalidDimensions => Probe::Shm(Mode::InvalidDimensions),
        S::InvalidStride => Probe::Shm(Mode::InvalidStride),
        S::InvalidOffset => Probe::Shm(Mode::InvalidOffset),
        S::InvalidFormat => Probe::Shm(Mode::InvalidFormat),
        S::Truncate => Probe::Shm(Mode::Truncate),
        S::NegativeResize => Probe::Shm(Mode::NegativeResize),
        S::OversizedPool => Probe::Shm(Mode::OversizedPool),
        S::PoolLimit => Probe::Shm(Mode::PoolLimit),
    };
    let mut report = match probe {
        Probe::Shm(mode) => serde_json::to_value(run(mode, stop)),
        Probe::State(scenario) => serde_json::to_value(super::state_checks::run(scenario, stop)),
    }
    .expect("bounded compositor report");
    report["nonce"] = serde_json::Value::String(nonce);
    report
}

#[cfg(test)]
mod tests {
    use super::*;
    // /procのfixture名とprocess-wide signal handlerを共有するため、同じ実例群は直列にする。
    #[test]
    fn real_client_patterns_and_malformed_pools_recover_in_same_display() {
        let _guard = crate::compositor::FIXTURE_LOCK.lock().unwrap();
        for mode in [
            Mode::Pattern,
            Mode::SharedCache,
            Mode::InvalidDimensions,
            Mode::InvalidStride,
            Mode::InvalidOffset,
            Mode::InvalidFormat,
            Mode::Truncate,
            Mode::NegativeResize,
            Mode::OversizedPool,
            Mode::PoolLimit,
        ] {
            let report = run(mode, Arc::new(AtomicBool::new(false)));
            assert_eq!(report.state, "pass", "{report:#?}");
        }
    }
}
