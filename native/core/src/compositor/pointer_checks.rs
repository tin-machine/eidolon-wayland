//! 実clientと同期し、pointerのregion/capture/axis/focus/resetをwireで検査する。
use super::{
    checks::{fixture_resources, object_counts},
    protocol::{InputReport, ProtocolState},
};
use eidolon_model::{
    ids::{InputSourceId, SessionId, WindowId},
    keyboard::{KeyAction, KeyPacket, KeyboardLayout},
    pointer::{PointerEvent, PointerPacket},
};
use eidolon_shm_fixture::pointer::{self as fixture, Event, Stage};
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
    pub uid: u32,
    pub synthetic_input: bool,
    pub physical_input_verified: bool,
    pub checkpoints: u32,
    pub input_time_ms: u32,
    pub letterbox_rejected: bool,
    pub region_checked: bool,
    pub source_capture_checked: bool,
    pub off_panel_release: bool,
    pub hover_keeps_keyboard: bool,
    pub focus_changed: bool,
    pub duplicate_reset: bool,
    pub late_source_ignored: bool,
    pub unmap_released: bool,
    pub destroy_released: bool,
    pub input: InputReport,
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
fn point(
    state: &mut ProtocolState,
    window: WindowId,
    source: i64,
    position: Option<[f64; 2]>,
    event: PointerEvent,
) -> Result<(), &'static str> {
    // target64x96にlogical32x32、上下16pxのletterbox。
    let uv = position.map(|p| [p[0] / 32., (16. + p[1] * 2.) / 96.]);
    state.pointer(
        window,
        PointerPacket {
            source: InputSourceId::new(source).unwrap(),
            uv,
            event,
            time_ms: super::protocol::monotonic_time_ms(0),
        },
        64,
        96,
    )
}
pub fn run(stop: Arc<AtomicBool>) -> Report {
    let mut report = Report {
        state: "running",
        fixture_revision: "eidolon-pointer-wire-fixture-v1",
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
        state.configure_keyboard(KeyboardLayout::Us)?;
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
        let a = WindowId::new(1).unwrap();
        let b = WindowId::new(2).unwrap();
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
                    match stage {
                        Stage::Mapped => {
                            state.pointer(
                                a,
                                PointerPacket {
                                    source: InputSourceId::new(1).unwrap(),
                                    uv: Some([0.5, 0.05]),
                                    event: PointerEvent::Move,
                                    time_ms: super::protocol::monotonic_time_ms(0),
                                },
                                64,
                                96,
                            )?;
                            report.letterbox_rejected =
                                state.input_report().pointer.hover_surface.is_none();
                            point(&mut state, a, 1, Some([16., 14.]), PointerEvent::Move)?;
                        }
                        Stage::HoverChild => {
                            let input = state.input_report();
                            let child = state
                                .latest_snapshot(a)
                                .ok_or("NO_SNAPSHOT")?
                                .layers
                                .iter()
                                .find(|l| l.offset_logical.x == 8 && l.offset_logical.y == 6)
                                .map(|l| l.surface_id)
                                .ok_or("NO_CHILD")?;
                            if input.pointer.hover_surface != Some(child)
                                || input.focus_window.is_some()
                            {
                                return Err("CHILD_HOVER_FOCUS");
                            }
                            point(&mut state, a, 1, Some([10., 8.]), PointerEvent::Move)?;
                            report.region_checked =
                                state.input_report().pointer.hover_surface != Some(child);
                        }
                        Stage::Hole => {
                            point(
                                &mut state,
                                a,
                                1,
                                Some([16., 14.]),
                                PointerEvent::Buttons {
                                    changed: 1,
                                    state: 1,
                                },
                            )?;
                            if state.input_report().focus_window != Some(a)
                                || state.input_report().pointer.pressed != 1
                            {
                                return Err("PRIMARY_FOCUS");
                            }
                        }
                        Stage::Pressed => {
                            let old = state.input_report().pointer;
                            point(
                                &mut state,
                                b,
                                2,
                                Some([4., 4.]),
                                PointerEvent::Buttons {
                                    changed: 1,
                                    state: 1,
                                },
                            )?;
                            let new = state.input_report().pointer;
                            report.source_capture_checked = old.capture_surface
                                == new.capture_surface
                                && old.motions == new.motions
                                && new.pressed == 1;
                            point(&mut state, a, 1, Some([-4., 14.]), PointerEvent::Move)?;
                            point(
                                &mut state,
                                a,
                                1,
                                None,
                                PointerEvent::Buttons {
                                    changed: 1,
                                    state: 0,
                                },
                            )?;
                            let input = state.input_report();
                            report.off_panel_release = input.pointer.pressed == 0
                                && input.pointer.capture_surface.is_none()
                                && input.pointer.hover_surface.is_none()
                                && input.focus_window == Some(a);
                        }
                        Stage::Released => {
                            point(&mut state, b, 1, Some([4., 4.]), PointerEvent::Move)?;
                            point(
                                &mut state,
                                b,
                                1,
                                Some([4., 4.]),
                                PointerEvent::Buttons {
                                    changed: 2,
                                    state: 2,
                                },
                            )?;
                            point(
                                &mut state,
                                b,
                                1,
                                Some([4., 4.]),
                                PointerEvent::Buttons {
                                    changed: 2,
                                    state: 0,
                                },
                            )?;
                            report.hover_keeps_keyboard =
                                state.input_report().focus_window == Some(a);
                            point(
                                &mut state,
                                b,
                                1,
                                Some([4., 4.]),
                                PointerEvent::Buttons {
                                    changed: 1,
                                    state: 1,
                                },
                            )?;
                            report.focus_changed = state.input_report().focus_window == Some(b);
                        }
                        Stage::FocusedB => {
                            point(
                                &mut state,
                                b,
                                1,
                                Some([4., 4.]),
                                PointerEvent::Scroll {
                                    delta: [2.5, -15.],
                                    stop: [false; 2],
                                },
                            )?;
                            point(
                                &mut state,
                                b,
                                1,
                                Some([4., 4.]),
                                PointerEvent::Scroll {
                                    delta: [0.; 2],
                                    stop: [true; 2],
                                },
                            )?;
                            point(
                                &mut state,
                                b,
                                1,
                                Some([4., 4.]),
                                PointerEvent::Buttons {
                                    changed: 4,
                                    state: 5,
                                },
                            )?;
                            point(
                                &mut state,
                                b,
                                1,
                                Some([4., 4.]),
                                PointerEvent::Scroll {
                                    delta: [0., 3.],
                                    stop: [false; 2],
                                },
                            )?;
                            let token = state.input_report().focus_token.ok_or("NO_FOCUS_TOKEN")?;
                            state.key_at_focus(
                                b,
                                token,
                                KeyPacket {
                                    action: KeyAction::Pressed,
                                    device: 7,
                                    scan_code: 42,
                                    key_code: 59,
                                    repeat: 0,
                                    meta: 0,
                                    time_ms: super::protocol::monotonic_time_ms(0),
                                },
                            )?;
                            state.reset_input();
                            let before = state.input_report();
                            state.reset_input();
                            let after = state.input_report();
                            report.duplicate_reset = before.pointer.resets == after.pointer.resets
                                && before.pointer.buttons == after.pointer.buttons
                                && before.resets == after.resets
                                && after.pointer.pressed == 0
                                && after.pressed == 0
                                && after.focus_window.is_none();
                        }
                        Stage::Reset => {
                            let before = state.input_report().pointer.buttons;
                            point(
                                &mut state,
                                b,
                                1,
                                None,
                                PointerEvent::Buttons {
                                    changed: 5,
                                    state: 0,
                                },
                            )?;
                            point(
                                &mut state,
                                b,
                                2,
                                Some([12., 12.]),
                                PointerEvent::Buttons {
                                    changed: 1,
                                    state: 1,
                                },
                            )?;
                            point(
                                &mut state,
                                b,
                                1,
                                None,
                                PointerEvent::Buttons {
                                    changed: 1,
                                    state: 0,
                                },
                            )?;
                            let input = state.input_report().pointer;
                            report.late_source_ignored = input.buttons == before + 1
                                && input.pressed == 1
                                && input.capture_window == Some(b);
                        }
                        Stage::UnmappedB => {
                            let input = state.input_report();
                            report.unmap_released = input.pointer.pressed == 0
                                && input.pointer.capture_surface.is_none()
                                && input.focus_window.is_none();
                            point(
                                &mut state,
                                a,
                                1,
                                Some([16., 14.]),
                                PointerEvent::Buttons {
                                    changed: 1,
                                    state: 1,
                                },
                            )?;
                        }
                        Stage::DestroyedChild => {
                            let input = state.input_report();
                            report.destroy_released = input.pointer.pressed == 0
                                && input.pointer.capture_surface.is_none()
                                && input.pointer.hover_surface.is_none()
                                && input.focus_window == Some(a);
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
        report.input = state.input_report();
        report.surfaces_after = state.surface_count();
        report.windows_after = state.windows().len();
        report.pixel_bytes_after = state.pixel_bytes();
        report.pool_bytes_after = state.pool_usage().bytes;
        (report.fixture_fds_after, report.fixture_maps_after) = fixture_resources()?;
        drive?;
        report.client = Some(client?);
        let client = report.client.as_ref().unwrap();
        let reset = client
            .checkpoints
            .iter()
            .find(|c| c.stage == "Reset")
            .ok_or("NO_RESET_CHECKPOINT")?;
        let normal = &client.events[..reset.events];
        let buttons: Vec<_> = normal
            .iter()
            .filter_map(|e| {
                if let Event::Button {
                    surface,
                    code,
                    state,
                    ..
                } = e
                {
                    Some((*surface, *code, *state))
                } else {
                    None
                }
            })
            .collect();
        let expected = [
            (Some("child"), 0x110, 1),
            (Some("child"), 0x110, 0),
            (Some("root_b"), 0x111, 1),
            (Some("root_b"), 0x111, 0),
            (Some("root_b"), 0x110, 1),
            (Some("root_b"), 0x112, 1),
            (Some("root_b"), 0x110, 0),
            (Some("root_b"), 0x112, 0),
        ];
        let axes: Vec<_> = normal
            .iter()
            .filter_map(|e| {
                if let Event::Axis { axis, value, .. } = e {
                    Some((*axis, *value))
                } else {
                    None
                }
            })
            .collect();
        let stops: Vec<_> = normal
            .iter()
            .filter_map(|e| {
                if let Event::AxisStop { axis } = e {
                    Some(*axis)
                } else {
                    None
                }
            })
            .collect();
        let unmapped = client
            .checkpoints
            .iter()
            .find(|c| c.stage == "UnmappedB")
            .ok_or("NO_UNMAP_CHECKPOINT")?;
        let unmapped_buttons: Vec<_> = client.events[..unmapped.events]
            .iter()
            .filter_map(|e| {
                if let Event::Button {
                    surface,
                    code,
                    state,
                    ..
                } = e
                {
                    Some((*surface, *code, *state))
                } else {
                    None
                }
            })
            .collect();
        if client.events.iter().any(|event| matches!(event,Event::Button {time,..} if time.wrapping_sub(report.input_time_ms)>=30_000)) || buttons!=expected || axes!=[(1,2.5),(0,-15.),(0,3.)] || stops!=[1,0,0]
            || !normal.iter().any(|e|matches!(e,Event::AxisSource {source:2}))
            || !normal.iter().any(|e|matches!(e,Event::Motion {surface:Some("child"),position} if *position==[-12.,8.]))
            || !matches!(normal.first(),Some(Event::Enter {surface:"child",position}) if *position==[8.,8.])
            || reset.keyboard_enters!=[client.root_a,client.root_b] || reset.keyboard_leaves!=2 || reset.keys!=2
            || client.keyboard.keys.iter().map(|(k,s,_)|(*k,*s)).collect::<Vec<_>>()!=[(42,1),(42,0)]
            || client.keyboard.keys.iter().any(|e|e.2.wrapping_sub(report.input_time_ms)>=30_000) || unmapped_buttons.len()!=10
            || unmapped_buttons[8..]!=[(Some("root_b"),0x110,1),(Some("root_b"),0x110,0)]
            || report.input.pointer.pressed!=0 || report.input.pointer.capture_surface.is_some() || report.input.pointer.hover_surface.is_some()
            || report.input.pressed!=0 || report.input.focus_window.is_some() || report.input.focus_token.is_some()
            || ![report.letterbox_rejected,report.region_checked,report.source_capture_checked,report.off_panel_release,
                report.hover_keeps_keyboard,report.focus_changed,report.duplicate_reset,report.late_source_ignored,
                report.unmap_released,report.destroy_released].iter().all(|v|*v) || report.checkpoints!=9
            || [report.clients_after,report.objects_after,report.surfaces_after,report.windows_after,report.pixel_bytes_after,
                report.pool_bytes_after,report.fixture_fds_after,report.fixture_maps_after].iter().any(|v|*v!=0) {return Err("POINTER_WIRE_PROOF");}
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
    fn actual_pointer_client_verifies_regions_capture_scroll_focus_reset_and_destroy() {
        let _guard = crate::compositor::FIXTURE_LOCK.lock().unwrap();
        let report = run(Arc::new(AtomicBool::new(false)));
        assert_eq!(report.state, "pass", "{report:#?}");
    }
}
