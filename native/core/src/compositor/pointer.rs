//! UV/hitとWayland pointerの境界。Wayland resourceはこのownerから持ち出さない。
use super::{ProtocolState, SurfaceAttributes, with_states};
use eidolon_model::{
    ids::{InputSourceId, SurfaceId, WindowId},
    input::{UvOrigin, surface_hits, window_at},
    layout::Viewport,
    pointer::{PointerButton, PointerCapture, PointerEvent, PointerPacket, PointerTracker},
    window::{LogicalSize, WindowLife},
};
use serde::Serialize;
use smithay::{
    backend::input::{Axis, AxisSource, ButtonState},
    input::pointer::{AxisFrame, ButtonEvent, MotionEvent, PointerHandle},
    reexports::wayland_server::{Resource, protocol::wl_surface::WlSurface},
    utils::SERIAL_COUNTER,
};

#[derive(Clone, Debug, Default, Serialize)]
pub struct PointerReport {
    pub ready: bool,
    pub hover_window: Option<WindowId>,
    pub hover_surface: Option<SurfaceId>,
    pub capture_window: Option<WindowId>,
    pub capture_surface: Option<SurfaceId>,
    pub pressed: u32,
    pub motions: u64,
    pub buttons: u64,
    pub axis_frames: u64,
    pub resets: u64,
}
#[derive(Clone, Copy)]
struct GrabGeometry {
    origin: [f64; 2],
    size: LogicalSize,
}
#[derive(Default)]
pub(super) struct PointerState {
    tracker: PointerTracker,
    hover: Option<PointerCapture>,
    location: [f64; 2],
    grab: Option<GrabGeometry>,
    axes: [bool; 2],
    last_time: u32,
}
struct Hit {
    target: PointerCapture,
    surface: WlSurface,
    origin: [f64; 2],
    location: [f64; 2],
}

impl ProtocolState {
    pub(super) fn configure_pointer(&mut self) {
        if self._seat.get_pointer().is_none() {
            self._seat.add_pointer();
        }
        self.input.report.pointer.ready = true;
    }
    pub(super) fn pointer_report(&self) -> PointerReport {
        let mut report = self.input.report.pointer.clone();
        let state = &self.input.pointer;
        report.hover_window = state.hover.map(|hit| hit.window);
        report.hover_surface = state.hover.map(|hit| hit.surface);
        report.capture_window = state.tracker.capture().map(|hit| hit.window);
        report.capture_surface = state.tracker.capture().map(|hit| hit.surface);
        report.pressed = state.tracker.pressed();
        report
    }
    fn pointer_surface(&self, id: SurfaceId) -> Option<WlSurface> {
        self.surfaces
            .iter()
            .find(|s| s.is_alive() && self.surface_meta.get(&s.id()).is_some_and(|m| m.id == id))
            .cloned()
    }
    fn grab_geometry(&self, target: PointerCapture) -> Option<(WlSurface, GrabGeometry)> {
        let snapshot = self.latest_snapshot(target.window)?;
        if !self
            .windows()
            .iter()
            .any(|w| w.window == target.window && w.life == WindowLife::Mapped)
        {
            return None;
        }
        let layer = snapshot
            .layers
            .iter()
            .find(|layer| layer.surface_id == target.surface)?;
        Some((
            self.pointer_surface(target.surface)?,
            GrabGeometry {
                origin: [
                    f64::from(layer.offset_logical.x),
                    f64::from(layer.offset_logical.y),
                ],
                size: snapshot.logical_size,
            },
        ))
    }
    fn pointer_hit(
        &self,
        window: WindowId,
        source: InputSourceId,
        uv: Option<[f64; 2]>,
        width: i32,
        height: i32,
    ) -> Option<Hit> {
        let [u, v] = uv?;
        let snapshot = self.latest_snapshot(window)?;
        let viewport = Viewport::new(snapshot.logical_size, width, height).ok()?;
        let location = window_at(&viewport, u, v, UvOrigin::TopLeft)?;
        for hit in surface_hits(&snapshot, location[0], location[1]) {
            let surface = self.pointer_surface(hit.surface)?;
            let accepts = with_states(&surface, |states| {
                states
                    .cached_state
                    .get::<SurfaceAttributes>()
                    .current()
                    .input_region
                    .as_ref()
                    .is_none_or(|region| {
                        region.contains((
                            hit.position[0].floor() as i32,
                            hit.position[1].floor() as i32,
                        ))
                    })
            });
            if accepts {
                return Some(Hit {
                    target: PointerCapture {
                        source,
                        window,
                        surface: hit.surface,
                    },
                    surface,
                    origin: [location[0] - hit.position[0], location[1] - hit.position[1]],
                    location,
                });
            }
        }
        None
    }
    fn stop_pointer_axes(&mut self, pointer: &PointerHandle<Self>, time: u32) {
        if !self.input.pointer.axes.iter().any(|v| *v) {
            return;
        }
        let mut frame = AxisFrame::new(time).source(AxisSource::Continuous);
        for (i, axis) in [Axis::Horizontal, Axis::Vertical].into_iter().enumerate() {
            if self.input.pointer.axes[i] {
                frame = frame.stop(axis);
            }
        }
        self.input.pointer.axes = [false; 2];
        pointer.axis(self, frame);
        pointer.frame(self);
        self.input.report.pointer.axis_frames += 1;
    }
    fn pointer_motion(&mut self, pointer: &PointerHandle<Self>, hit: Option<&Hit>, time: u32) {
        let next = hit.map(|hit| hit.target);
        if self.input.pointer.hover != next {
            self.stop_pointer_axes(pointer, time);
        }
        if hit.is_none() && self.input.pointer.hover.is_none() {
            return;
        }
        let location = hit.map_or(self.input.pointer.location, |hit| hit.location);
        let focus = hit.map(|hit| (hit.surface.clone(), (hit.origin[0], hit.origin[1]).into()));
        pointer.motion(
            self,
            focus,
            &MotionEvent {
                location: (location[0], location[1]).into(),
                serial: SERIAL_COUNTER.next_serial(),
                time,
            },
        );
        self.input.pointer.hover = next;
        self.input.pointer.location = location;
        self.input.report.pointer.motions += 1;
    }
    pub fn pointer(
        &mut self,
        window: WindowId,
        packet: PointerPacket,
        width: i32,
        height: i32,
    ) -> Result<(), &'static str> {
        if !packet.valid() {
            return Err("INVALID_ARGUMENT");
        }
        let pointer = self._seat.get_pointer().ok_or("NOT_READY")?;
        self.refresh_pointer();
        let captured = self.input.pointer.tracker.capture();
        if captured.is_some_and(|capture| capture.source != packet.source) {
            return Ok(());
        }
        self.input.pointer.last_time = packet.time_ms;
        if matches!(packet.event, PointerEvent::Leave | PointerEvent::Cancel) {
            if self
                .input
                .pointer
                .hover
                .is_some_and(|hit| hit.source == packet.source)
                || captured.is_some()
            {
                self.reset_pointer();
            }
            return Ok(());
        }
        let mut hit = if let Some(capture) = captured {
            let (surface, geometry) = self.grab_geometry(capture).ok_or("STALE")?;
            let mut location = self.input.pointer.location;
            // SDKが別面のHitInfoを旧receiverへ渡した場合はuv=Noneで最後の有効座標を保つ。
            if capture.window == window
                && let Some(uv) = packet.uv
            {
                let viewport =
                    Viewport::new(geometry.size, width, height).map_err(|_| "INVALID_ARGUMENT")?;
                location = [
                    (uv[0] * f64::from(width) - viewport.origin[0]) / viewport.scale,
                    (uv[1] * f64::from(height) - viewport.origin[1]) / viewport.scale,
                ];
            }
            Some(Hit {
                target: capture,
                surface,
                origin: geometry.origin,
                location,
            })
        } else {
            self.pointer_hit(window, packet.source, packet.uv, width, height)
        };
        if captured.is_none()
            && !self.popup_grabs.is_empty()
            && hit.as_ref().is_none_or(|h| !self.popup_allows(&h.surface))
        {
            if matches!(packet.event,PointerEvent::Buttons {changed,state} if changed&state!=0) {
                self.dismiss_popup_grab(true);
                self.reset_pointer();
                return Ok(());
            }
            hit = None;
        }
        self.pointer_motion(&pointer, hit.as_ref(), packet.time_ms);
        match packet.event {
            PointerEvent::Buttons { changed, state } => {
                let was_captured = self.input.pointer.tracker.capture().is_some();
                let deltas = self
                    .input
                    .pointer
                    .tracker
                    .update(
                        packet.source,
                        changed,
                        state,
                        hit.as_ref().map(|h| h.target),
                    )
                    .map_err(|_| "INVALID_ARGUMENT")?;
                if !was_captured && self.input.pointer.tracker.capture().is_some() {
                    self.input.pointer.grab = hit
                        .as_ref()
                        .and_then(|h| self.grab_geometry(h.target).map(|(_, g)| g));
                }
                for delta in deltas {
                    if delta.pressed && delta.button == PointerButton::Primary {
                        self.focus_keyboard(delta.target.window)?;
                    }
                    let serial = SERIAL_COUNTER.next_serial();
                    if delta.pressed
                        && let Some(hit) = &hit
                    {
                        self.input_serial(serial.into(), hit.surface.clone(), true);
                    }
                    pointer.button(
                        self,
                        &ButtonEvent {
                            serial,
                            time: packet.time_ms,
                            button: delta.button.evdev(),
                            state: if delta.pressed {
                                ButtonState::Pressed
                            } else {
                                ButtonState::Released
                            },
                        },
                    );
                    self.input.report.pointer.buttons += 1;
                }
                if was_captured && self.input.pointer.tracker.capture().is_none() {
                    self.input.pointer.grab = None;
                    let hover = self.pointer_hit(window, packet.source, packet.uv, width, height);
                    self.pointer_motion(&pointer, hover.as_ref(), packet.time_ms);
                }
            }
            PointerEvent::Scroll { delta, stop } if hit.is_some() => {
                let mut frame = AxisFrame::new(packet.time_ms).source(AxisSource::Continuous);
                let mut changed = false;
                for (i, axis) in [Axis::Horizontal, Axis::Vertical].into_iter().enumerate() {
                    if delta[i] != 0. {
                        frame = frame.value(axis, delta[i]);
                        self.input.pointer.axes[i] = true;
                        changed = true;
                    }
                    if stop[i] && self.input.pointer.axes[i] {
                        frame = frame.stop(axis);
                        self.input.pointer.axes[i] = false;
                        changed = true;
                    }
                }
                if changed {
                    pointer.axis(self, frame);
                    self.input.report.pointer.axis_frames += 1;
                }
            }
            _ => (),
        }
        pointer.frame(self);
        Ok(())
    }
    pub(super) fn reset_pointer(&mut self) {
        let releases = self.input.pointer.tracker.reset();
        let had_state = !releases.is_empty()
            || self.input.pointer.hover.is_some()
            || self.input.pointer.axes.iter().any(|v| *v);
        if let Some(pointer) = self._seat.get_pointer() {
            let now = super::monotonic_time_ms(self.input.pointer.last_time);
            for delta in releases {
                pointer.button(
                    self,
                    &ButtonEvent {
                        serial: SERIAL_COUNTER.next_serial(),
                        time: now,
                        button: delta.button.evdev(),
                        state: ButtonState::Released,
                    },
                );
                self.input.report.pointer.buttons += 1;
            }
            self.stop_pointer_axes(&pointer, now);
            self.pointer_motion(&pointer, None, now);
            pointer.frame(self);
        }
        self.input.pointer.hover = None;
        self.input.pointer.grab = None;
        if had_state {
            self.input.report.pointer.resets += 1;
        }
    }
    pub(super) fn refresh_pointer(&mut self) {
        let invalid = self.input.pointer.tracker.capture().is_some_and(|capture| {
            self.grab_geometry(capture).is_none_or(|(_, current)| {
                self.input
                    .pointer
                    .grab
                    .is_none_or(|old| old.origin != current.origin || old.size != current.size)
            })
        }) || self
            .input
            .pointer
            .hover
            .is_some_and(|hit| self.grab_geometry(hit).is_none());
        if invalid {
            self.reset_pointer();
        }
    }
}
