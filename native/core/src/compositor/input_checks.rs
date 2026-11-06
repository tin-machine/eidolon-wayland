//! 独立した実Wayland clientがseat/keymap FD/key/leaveを受信する固定検査。
use super::{
    checks::{fixture_resources, object_counts},
    protocol::{InputReport, ProtocolState},
};
use eidolon_model::{
    ids::SessionId,
    keyboard::{KeyAction, KeyPacket, KeyboardLayout},
    window::WindowLife,
};
use eidolon_shm_fixture::keyboard::{self as fixture, Stage};
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

#[derive(Default, Debug, Serialize)]
pub struct Report {
    pub state: &'static str,
    pub fixture_revision: &'static str,
    pub uid: u32,
    pub layout: KeyboardLayout,
    pub synthetic_input: bool,
    pub physical_input_verified: bool,
    pub checkpoints: u32,
    pub input_time_ms: u32,
    pub shifted_two: u32,
    pub old_focus_rejected: bool,
    pub keyboard: InputReport,
    pub client: Option<fixture::Report>,
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
pub fn run(layout: KeyboardLayout, stop: Arc<AtomicBool>) -> Report {
    let mut report = Report {
        state: "running",
        fixture_revision: "eidolon-keyboard-wire-fixture-v1",
        layout,
        synthetic_input: true,
        input_time_ms: super::protocol::monotonic_time_ms(0),
        ..Default::default()
    };
    // SAFETY: getuidはpointerを受け取らない。
    report.uid = unsafe { libc::getuid() };
    let outcome = (|| {
        if fixture_resources()? != (0, 0) {
            return Err("FIXTURE_ALREADY_RUNNING");
        }
        let mut display = Display::<ProtocolState>::new().map_err(|_| "DISPLAY_CREATE")?;
        let mut state = ProtocolState::new(&display.handle(), SessionId::new(1).unwrap());
        state.configure_keyboard(layout)?;
        let (server, client) = UnixStream::pair().map_err(|_| "SOCKET_PAIR")?;
        state.accept(&display.handle(), server)?;
        let (tx, steps) = mpsc::sync_channel(1);
        let (reply, rx) = mpsc::sync_channel(1);
        let client_stop = stop.clone();
        let worker = thread::spawn(move || {
            fixture::run(
                client,
                client_stop,
                fixture::Control {
                    steps: tx,
                    replies: rx,
                },
            )
        });
        let deadline = Instant::now() + Duration::from_secs(18);
        let drive = (|| {
            while !worker.is_finished() {
                if stop.load(Ordering::Acquire) || Instant::now() > deadline {
                    return Err("CANCELLED_OR_TIMEOUT");
                }
                display
                    .dispatch_clients(&mut state)
                    .map_err(|_| "SERVER_DISPATCH")?;
                state.finish_dispatch(&display.handle());
                if let Ok(stage) = steps.try_recv() {
                    report.checkpoints += 1;
                    let window = state
                        .windows()
                        .iter()
                        .find(|w| w.life == WindowLife::Mapped)
                        .map(|w| w.window);
                    let focus_token = state.input_report().focus_token;
                    let key = |state: &mut ProtocolState, code, action, device, repeat| {
                        state.key_at_focus(
                            window.ok_or("NO_WINDOW")?,
                            focus_token.ok_or("NO_FOCUS_TOKEN")?,
                            KeyPacket {
                                action,
                                device,
                                scan_code: code,
                                key_code: 0,
                                repeat,
                                meta: 0,
                                time_ms: report.input_time_ms,
                            },
                        )
                    };
                    match stage {
                        Stage::Mapped => state.focus_window(window.ok_or("NO_WINDOW")?)?,
                        Stage::Focused => {
                            key(&mut state, 42, KeyAction::Pressed, 7, 0)?;
                            key(&mut state, 3, KeyAction::Pressed, 7, 0)?;
                            report.shifted_two = state
                                .keyboard_symbol(
                                    eidolon_model::keyboard::EvdevCode::new(3).unwrap(),
                                )
                                .ok_or("NO_KEYBOARD")?;
                            key(&mut state, 3, KeyAction::Pressed, 7, 1)?;
                            key(&mut state, 3, KeyAction::Released, 7, 0)?;
                            key(&mut state, 42, KeyAction::Released, 7, 0)?;
                            key(&mut state, 30, KeyAction::Pressed, 7, 0)?;
                            key(&mut state, 30, KeyAction::Pressed, 8, 0)?;
                            key(&mut state, 30, KeyAction::Released, 7, 0)?;
                            key(&mut state, 30, KeyAction::Released, 8, 0)?;
                            key(&mut state, 30, KeyAction::Released, 8, 0)?;
                        }
                        Stage::Keys => {
                            for code in [29, 42, 30] {
                                key(&mut state, code, KeyAction::Pressed, 7, 0)?;
                            }
                            state.reset_input();
                            state.reset_input();
                            if key(&mut state, 30, KeyAction::Released, 7, 0) != Err("STALE") {
                                return Err("LATE_UP");
                            }
                            let input = state.input_report();
                            if input.pressed != 0
                                || input.focus_window.is_some()
                                || input.resets != 1
                            {
                                return Err("RESET_STATE");
                            }
                            state.focus_window(window.ok_or("NO_WINDOW")?)?;
                        }
                        Stage::Reset => {
                            if state.input_report().pressed != 0 {
                                return Err("REPLAYED_KEYS");
                            }
                            let old = eidolon_model::ids::CommandSeq::new(1).unwrap();
                            if state.input_report().focus_token == Some(old) {
                                return Err("REUSED_FOCUS_TOKEN");
                            }
                            report.old_focus_rejected = state.key_at_focus(
                                window.ok_or("NO_WINDOW")?,
                                old,
                                KeyPacket {
                                    action: KeyAction::Pressed,
                                    device: 7,
                                    scan_code: 30,
                                    key_code: 29,
                                    repeat: 0,
                                    meta: 0,
                                    time_ms: report.input_time_ms,
                                },
                            ) == Err("STALE");
                            if !report.old_focus_rejected {
                                return Err("OLD_FOCUS_KEY");
                            }
                        }
                        Stage::Unmapped => {
                            if window.is_some() || state.input_report().focus_window.is_some() {
                                return Err("UNMAP_FOCUS");
                            }
                        }
                    }
                    reply.try_send(()).map_err(|_| "CHECKPOINT_REPLY")?;
                }
                display.flush_clients().map_err(|_| "SERVER_FLUSH")?;
                thread::sleep(Duration::from_millis(1));
            }
            Ok(())
        })();
        drop(reply);
        let client = worker.join().map_err(|_| "CLIENT_PANIC")?;
        // client socketのEOFとSmithayの遅延SHM pool解放まで待つ。
        let cleanup = Instant::now() + Duration::from_secs(2);
        loop {
            display
                .dispatch_clients(&mut state)
                .map_err(|_| "SERVER_DISPATCH")?;
            state.finish_dispatch(&display.handle());
            (report.clients_after, report.objects_after) = object_counts(&display);
            if report.clients_after == 0 && state.pool_usage().bytes == 0
                || Instant::now() > cleanup
            {
                break;
            }
            thread::sleep(Duration::from_millis(2));
        }
        report.keyboard = state.input_report();
        report.surfaces_after = state.surface_count();
        report.windows_after = state.windows().len();
        report.pixel_bytes_after = state.pixel_bytes();
        report.pool_bytes_after = state.pool_usage().bytes;
        (report.fixture_fds_after, report.fixture_maps_after) = fixture_resources()?;
        drive?;
        report.client = Some(client?);
        let client = report.client.as_ref().unwrap();
        let expected = [
            (42, 1),
            (3, 1),
            (3, 0),
            (42, 0),
            (30, 1),
            (30, 0),
            (29, 1),
            (42, 1),
            (30, 1),
            (29, 0),
            (30, 0),
            (42, 0),
        ];
        if client.keymap_format != 1
            || Some(&client.keymap_sha256) != report.keyboard.wire_keymap_sha256.as_ref()
            || client.repeat != Some((25, 600))
            || client.enters != [Vec::<u32>::new(), Vec::new()]
            || client.leaves != 2
            || client
                .keys
                .iter()
                .map(|(k, s, _)| (*k, *s))
                .collect::<Vec<_>>()
                != expected
            || !client.modifiers.iter().any(|m| m[0] != 0)
            || client.modifiers.last() != Some(&[0; 4])
            || report.shifted_two
                != u32::from(if layout == KeyboardLayout::Us {
                    b'@'
                } else {
                    b'"'
                })
            || report.keyboard.pressed != 0
            || report.keyboard.focus_window.is_some()
            || report.keyboard.resets != 2
            || report.checkpoints != 5
            || [
                report.clients_after,
                report.objects_after,
                report.surfaces_after,
                report.windows_after,
                report.pixel_bytes_after,
                report.pool_bytes_after,
                report.fixture_fds_after,
                report.fixture_maps_after,
            ]
            .iter()
            .any(|n| *n != 0)
        {
            return Err("KEYBOARD_WIRE_PROOF");
        }
        Ok(())
    })();
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
    #[test]
    fn disconnected_clients_release_keymap_files_across_sessions() {
        let _guard = crate::compositor::FIXTURE_LOCK.lock().unwrap();
        let keymaps = || {
            std::fs::read_dir("/proc/self/fd")
                .unwrap()
                .filter_map(Result::ok)
                .filter_map(|entry| std::fs::read_link(entry.path()).ok())
                .filter(|path| path.to_string_lossy().contains("memfd:smithay-keymap"))
                .count()
        };
        let baseline = keymaps();
        {
            let display = Display::<ProtocolState>::new().unwrap();
            let mut state = ProtocolState::new(&display.handle(), SessionId::new(1).unwrap());
            state.configure_keyboard(KeyboardLayout::Us).unwrap();
            drop(display);
            drop(state);
            assert_eq!(keymaps(), baseline, "client受入前のkeymap FDが残っています");
        }
        for _ in 0..3 {
            let report = run(KeyboardLayout::Us, Arc::new(AtomicBool::new(false)));
            assert_eq!(report.state, "pass", "{report:#?}");
            assert_eq!(
                keymaps(),
                baseline,
                "切断したsessionのkeymap FDが残っています"
            );
        }
    }
    #[test]
    fn real_client_receives_us_jis_keymap_evdev_modifiers_repeat_and_release_before_leave() {
        let _guard = crate::compositor::FIXTURE_LOCK.lock().unwrap();
        for layout in [KeyboardLayout::Us, KeyboardLayout::Jis] {
            let report = run(layout, Arc::new(AtomicBool::new(false)));
            assert_eq!(report.state, "pass", "{report:#?}");
            let client = report.client.unwrap();
            let leave = client.order.iter().position(|e| *e == "leave").unwrap();
            assert_eq!(
                client.order[..leave]
                    .iter()
                    .filter(|e| **e == "key")
                    .count(),
                12
            );
            assert!(client.keys[..9].iter().all(|e| e.2 == report.input_time_ms));
            assert!(
                client.keys[9..]
                    .iter()
                    .all(|e| e.2.wrapping_sub(report.input_time_ms) < 30_000)
            );
        }
    }
}
