//! 九つの独立Wayland接続。仮想presentのhost検査でありSDK/SSH/HMDの結果ではない。
use super::{
    checks::{fixture_resources, object_counts},
    protocol::ProtocolState,
};
use eidolon_model::{ids::SessionId, limits, window::WindowLife};
use eidolon_shm_fixture::{FRAMES, Mode};
use sha2::{Digest, Sha256};
use smithay::reexports::wayland_server::{Display, Resource};
use std::{
    collections::HashMap,
    net::Shutdown,
    os::unix::net::UnixStream,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    thread,
    time::{Duration, Instant},
};

fn run() -> Result<(), &'static str> {
    if fixture_resources()? != (0, 0) {
        return Err("FIXTURE_BUSY");
    }
    let mut display = Display::<ProtocolState>::new().map_err(|_| "DISPLAY_CREATE")?;
    let mut state = ProtocolState::new(&display.handle(), SessionId::new(1).unwrap());
    let stop = Arc::new(AtomicBool::new(false));
    let (sender, receiver) = mpsc::channel();
    let mut peers = Vec::new();
    let mut interrupts = Vec::new();
    // Socket生成と受入れを先に済ませ、全clientを同じ私有Displayへ束縛する。
    for _ in 0..limits::TOPLEVELS {
        let (server, peer) = UnixStream::pair().map_err(|_| "SOCKET_PAIR")?;
        interrupts.push(peer.try_clone().map_err(|_| "SOCKET_CLONE")?);
        state.accept(&display.handle(), server)?;
        peers.push(peer);
    }
    let mut workers = Vec::new();
    let mut spawn_error = false;
    for peer in peers {
        let sender = sender.clone();
        let stop = stop.clone();
        match thread::Builder::new()
            .name("independent-shm".into())
            .spawn(move || {
                let result = eidolon_shm_fixture::run(peer, Mode::Pattern, stop);
                let _ = sender.send(result);
            }) {
            Ok(worker) => workers.push(worker),
            Err(_) => {
                spawn_error = true;
                break;
            }
        }
    }
    drop(sender);
    let mut verified = HashMap::new();
    let mut completed = 0;
    let mut all_mapped = false;
    let started = Instant::now();
    let outcome = (|| {
        if spawn_error {
            return Err("FIXTURE_THREAD");
        }
        loop {
            if started.elapsed() > Duration::from_secs(15) {
                return Err("SERVER_TIMEOUT");
            }
            display
                .dispatch_clients(&mut state)
                .map_err(|_| "SERVER_DISPATCH")?;
            state.finish_dispatch(&display.handle());
            let windows = state.windows();
            if !all_mapped
                && windows.len() == limits::TOPLEVELS
                && windows.iter().all(|w| w.life == WindowLife::Mapped)
            {
                if object_counts(&display).0 != limits::TOPLEVELS
                    || state.pool_usage().pools != limits::TOPLEVELS
                {
                    return Err("INDEPENDENT_OWNERS_MISSING");
                }
                all_mapped = true;
            }
            for surface in state.surfaces_for_check() {
                let Some(image) = state.image(&surface) else {
                    continue;
                };
                let hash = Sha256::digest(image.pixels())
                    .iter()
                    .map(|b| format!("{b:02x}"))
                    .collect::<String>();
                let (count, last) = verified
                    .entry(surface.id())
                    .or_insert((0u32, String::new()));
                if *last == hash {
                    continue;
                }
                if *count >= FRAMES || hash != eidolon_shm_fixture::expected_hash(*count) {
                    return Err("CLIENT_PIXEL_SEQUENCE");
                }
                *count += 1;
                *last = hash;
            }
            // 全九窓の同時mapを観測するまでcallbackを保留する。producerを順番に走らせない。
            if all_mapped {
                let tick =
                    Duration::from_nanos((started.elapsed().as_millis() as u64 + 1) * 16_666_667);
                for window in windows {
                    state.frame_callbacks(window.window, tick, true, true, true);
                }
            }
            display.flush_clients().map_err(|_| "SERVER_FLUSH")?;
            while let Ok(result) = receiver.try_recv() {
                let report = result?;
                if report.frames != FRAMES || report.callbacks != FRAMES || report.protocol_error {
                    return Err("CLIENT_PROOF_INCOMPLETE");
                }
                completed += 1;
            }
            if completed == limits::TOPLEVELS {
                break;
            }
            thread::sleep(Duration::from_millis(1));
        }
        if !all_mapped
            || verified.len() != limits::TOPLEVELS
            || verified.values().any(|(n, _)| *n != FRAMES)
            || state.counters.copies != limits::TOPLEVELS as u64 * u64::from(FRAMES)
            || state.counters.callbacks != limits::TOPLEVELS as u64 * u64::from(FRAMES)
            || state.counters.toplevel_rejections != 0
            || state.counters.client_rejections != 0
            || state.counters.pool_rejections != 0
            || state.counters.surface_rejections != 0
        {
            return Err("NINE_CLIENT_PROOF_INCOMPLETE");
        }
        Ok(())
    })();
    stop.store(true, Ordering::Release);
    for interrupt in &interrupts {
        let _ = interrupt.shutdown(Shutdown::Both);
    }
    let mut panicked = false;
    for worker in workers {
        if worker.join().is_err() {
            panicked = true;
        }
    }
    drop(interrupts);
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        display
            .dispatch_clients(&mut state)
            .map_err(|_| "CLEANUP_DISPATCH")?;
        state.finish_dispatch(&display.handle());
        display.flush_clients().map_err(|_| "CLEANUP_FLUSH")?;
        if object_counts(&display) == (0, 0) && state.pool_usage().pools == 0 {
            break;
        }
        if Instant::now() > deadline {
            return Err("CLEANUP_TIMEOUT");
        }
        thread::sleep(Duration::from_millis(1));
    }
    if state.surface_count() != 0
        || !state.windows().is_empty()
        || state.pixel_bytes() != 0
        || state.pool_usage().bytes != 0
        || fixture_resources()? != (0, 0)
    {
        return Err("RESOURCE_LEAK");
    }
    if panicked {
        return Err("FIXTURE_PANIC");
    }
    outcome
}

#[test]
fn nine_independent_clients_present_every_pattern_and_release_their_owners() {
    let _owner = super::FIXTURE_LOCK.lock().unwrap();
    assert_eq!(run(), Ok(()));
}
