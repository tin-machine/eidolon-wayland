//! 実xdg_popup grabと親内clipを、合成入力からwireで検査する。
use super::{
    checks::{fixture_resources, object_counts},
    protocol::ProtocolState,
};
use eidolon_model::{
    ids::{InputSourceId, SessionId, WindowId},
    keyboard::{KeyAction, KeyPacket, KeyboardLayout},
    pointer::{PointerEvent, PointerPacket},
};
use eidolon_shm_fixture::popup::{self as fixture, Control, Stage};
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
    pub state: &'static str,
    pub fixture_revision: &'static str,
    pub synthetic_input: bool,
    pub client: Option<fixture::Report>,
    pub client_error: Option<&'static str>,
    pub clipped: bool,
    pub nested_clipped: bool,
    pub outside_consumed: bool,
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
fn point(
    state: &mut ProtocolState,
    window: i64,
    p: [f64; 2],
    event: PointerEvent,
) -> Result<(), &'static str> {
    state.pointer(
        WindowId::new(window).unwrap(),
        PointerPacket {
            source: InputSourceId::new(1).unwrap(),
            uv: Some([p[0] / 32., p[1] / 32.]),
            event,
            time_ms: super::protocol::monotonic_time_ms(0),
        },
        32,
        32,
    )
}
pub fn run(stop: Arc<AtomicBool>) -> Report {
    let mut report = Report {
        state: "running",
        fixture_revision: "eidolon-popup-wire-fixture-v1",
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
        let (server, client) = UnixStream::pair().map_err(|_| "SOCKET")?;
        state.accept(&display.handle(), server)?;
        let (tx, rx) = mpsc::sync_channel(1);
        let (reply, answers) = mpsc::sync_channel(1);
        let child_stop = stop.clone();
        let worker = thread::spawn(move || {
            fixture::run(
                client,
                child_stop,
                Control {
                    steps: tx,
                    replies: answers,
                },
            )
        });
        let deadline = Instant::now() + Duration::from_secs(25);
        let drive = (|| {
            while !worker.is_finished() {
                if stop.load(Ordering::Acquire) || Instant::now() >= deadline {
                    return Err("CANCELLED_OR_TIMEOUT");
                }
                display
                    .dispatch_clients(&mut state)
                    .map_err(|_| "DISPATCH")?;
                state.finish_dispatch(&display.handle());
                if let Ok(stage) = rx.try_recv() {
                    match stage {
                        Stage::Mapped | Stage::PressAgain => {
                            point(
                                &mut state,
                                1,
                                [4., 4.],
                                PointerEvent::Buttons {
                                    changed: 1,
                                    state: 1,
                                },
                            )?;
                        }
                        Stage::Good => {
                            let snapshot = state
                                .latest_snapshot(WindowId::new(1).unwrap())
                                .ok_or("NO_SNAPSHOT")?;
                            let clip = snapshot.layers.last().ok_or("NO_POPUP")?.clip_logical;
                            report.clipped = snapshot.layers.len() == 2
                                && clip.origin.x == 20
                                && clip.origin.y == 20
                                && clip.size.width == 12
                                && clip.size.height == 12
                                && state.input_report().focus_surface
                                    == Some(snapshot.layers[1].surface_id);
                            point(
                                &mut state,
                                1,
                                [26., 26.],
                                PointerEvent::Buttons {
                                    changed: 1,
                                    state: 0,
                                },
                            )?;
                            for action in [KeyAction::Pressed, KeyAction::Released] {
                                state.key_at_focus(
                                    WindowId::new(1).unwrap(),
                                    state.input_report().focus_token.ok_or("FOCUS")?,
                                    KeyPacket {
                                        action,
                                        device: 1,
                                        scan_code: 30,
                                        key_code: 0,
                                        repeat: 0,
                                        meta: 0,
                                        time_ms: super::protocol::monotonic_time_ms(0),
                                    },
                                )?;
                            }
                        }
                        Stage::Input => {
                            point(
                                &mut state,
                                1,
                                [26., 26.],
                                PointerEvent::Buttons {
                                    changed: 1,
                                    state: 1,
                                },
                            )?;
                        }
                        Stage::Nested => {
                            let snapshot = state
                                .latest_snapshot(WindowId::new(1).unwrap())
                                .ok_or("NO_SNAPSHOT")?;
                            let clip = snapshot.layers.last().ok_or("NO_NESTED")?.clip_logical;
                            report.nested_clipped = snapshot.layers.len() == 3
                                && clip.origin.x == 25
                                && clip.origin.y == 25
                                && clip.size.width == 7
                                && clip.size.height == 7
                                && state.input_report().focus_surface
                                    == Some(snapshot.layers[2].surface_id);
                            point(
                                &mut state,
                                1,
                                [26., 26.],
                                PointerEvent::Buttons {
                                    changed: 1,
                                    state: 0,
                                },
                            )?;
                            point(&mut state, 2, [8., 8.], PointerEvent::Move)?;
                            let hover_blocked =
                                state.input_report().pointer.hover_surface.is_none();
                            point(
                                &mut state,
                                2,
                                [8., 8.],
                                PointerEvent::Buttons {
                                    changed: 1,
                                    state: 1,
                                },
                            )?;
                            point(
                                &mut state,
                                2,
                                [8., 8.],
                                PointerEvent::Buttons {
                                    changed: 1,
                                    state: 0,
                                },
                            )?;
                            report.outside_consumed = hover_blocked
                                && state.input_report().focus_window
                                    == Some(WindowId::new(1).unwrap())
                                && state
                                    .latest_snapshot(WindowId::new(1).unwrap())
                                    .is_some_and(|s| s.layers.len() == 1);
                        }
                        Stage::Closed => {
                            point(
                                &mut state,
                                2,
                                [8., 8.],
                                PointerEvent::Buttons {
                                    changed: 1,
                                    state: 1,
                                },
                            )?;
                            point(
                                &mut state,
                                2,
                                [8., 8.],
                                PointerEvent::Buttons {
                                    changed: 1,
                                    state: 0,
                                },
                            )?;
                        }
                        Stage::ParentPopup => {
                            point(
                                &mut state,
                                1,
                                [26., 26.],
                                PointerEvent::Buttons {
                                    changed: 1,
                                    state: 0,
                                },
                            )?;
                        }
                        Stage::ParentDestroyed => {
                            if state.input_report().focus_window.is_some()
                                || state.input_report().pointer.pressed != 0
                            {
                                return Err("PARENT_FOCUS_LEAK");
                            }
                        }
                        Stage::SecondFocused => (),
                    }
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
        match worker.join().map_err(|_| "CLIENT_PANIC")? {
            Ok(client) => report.client = Some(client),
            Err(error) => report.client_error = Some(error),
        }
        for _ in 0..10 {
            display
                .dispatch_clients(&mut state)
                .map_err(|_| "DISPATCH")?;
            state.finish_dispatch(&display.handle());
        }
        (report.clients_after, report.objects_after) = object_counts(&display);
        report.surfaces_after = state.surface_count();
        report.windows_after = state.windows().len();
        report.pixel_bytes_after = state.pixel_bytes();
        report.pool_bytes_after = state.pool_usage().bytes;
        (report.fixture_fds_after, report.fixture_maps_after) = fixture_resources()?;
        drive?;
        let c = report.client.as_ref().ok_or("CLIENT_FAILED")?;
        if !(report.clipped
            && report.nested_clipped
            && report.outside_consumed
            && c.denied_serial
            && c.denied_parent
            && c.popup_keyboard
            && c.nested_keyboard
            && c.outside_dismissed
            && c.other_window_focused
            && c.parent_destroyed
            && c.popup_keys == 2
            && c.popup_done == 5)
        {
            return Err("POPUP_PROOF");
        }
        if report.clients_after != 0
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
    fn popup_grab_clip_focus_dismiss_and_parent_destroy() {
        let _lock = super::super::FIXTURE_LOCK.lock().unwrap();
        let report = super::run(std::sync::Arc::new(std::sync::atomic::AtomicBool::new(
            false,
        )));
        assert_eq!(report.state, "pass", "{report:#?}");
    }
}
