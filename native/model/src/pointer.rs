//! 一つのWayland pointerに対するsource/captureと押下。座標・resourceはowner側で解決する。
use crate::ids::{InputSourceId, SurfaceId, WindowId};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PointerButton {
    Primary,
    Secondary,
    Middle,
}
impl PointerButton {
    pub const ALL: [Self; 3] = [Self::Primary, Self::Secondary, Self::Middle];
    pub fn bit(self) -> u8 {
        match self {
            Self::Primary => 1,
            Self::Secondary => 2,
            Self::Middle => 4,
        }
    }
    pub fn evdev(self) -> u32 {
        match self {
            Self::Primary => 0x110,
            Self::Secondary => 0x111,
            Self::Middle => 0x112,
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum PointerEvent {
    Move,
    Buttons { changed: u8, state: u8 },
    Scroll { delta: [f64; 2], stop: [bool; 2] },
    Leave,
    Cancel,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PointerPacket {
    pub source: InputSourceId,
    pub uv: Option<[f64; 2]>,
    pub event: PointerEvent,
    pub time_ms: u32,
}
impl PointerPacket {
    pub fn valid(self) -> bool {
        if self
            .uv
            .is_some_and(|uv| uv.iter().any(|v| !v.is_finite() || v.abs() > 16.))
        {
            return false;
        }
        match self.event {
            PointerEvent::Buttons { changed, state } => (changed | state) & !7 == 0,
            PointerEvent::Scroll { delta, stop } => delta
                .iter()
                .enumerate()
                .all(|(i, v)| v.is_finite() && v.abs() <= 4096. && (!stop[i] || *v == 0.)),
            _ => true,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PointerCapture {
    pub source: InputSourceId,
    pub window: WindowId,
    pub surface: SurfaceId,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ButtonDelta {
    pub target: PointerCapture,
    pub button: PointerButton,
    pub pressed: bool,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PointerError {
    InvalidMask,
    InvalidTarget,
}

#[derive(Default)]
pub struct PointerTracker {
    capture: Option<PointerCapture>,
    buttons: u8,
}
impl PointerTracker {
    pub fn capture(&self) -> Option<PointerCapture> {
        self.capture
    }
    pub fn pressed(&self) -> u32 {
        self.buttons.count_ones()
    }
    pub fn update(
        &mut self,
        source: InputSourceId,
        changed: u8,
        state: u8,
        hit: Option<PointerCapture>,
    ) -> Result<Vec<ButtonDelta>, PointerError> {
        if (changed | state) & !7 != 0 {
            return Err(PointerError::InvalidMask);
        }
        if hit.is_some_and(|hit| hit.source != source) {
            return Err(PointerError::InvalidTarget);
        }
        // capture中の別controllerはhoverを含めてownerを奪わない。
        if self.capture.is_some_and(|capture| capture.source != source) {
            return Ok(vec![]);
        }
        let mut result = vec![];
        for button in PointerButton::ALL {
            let bit = button.bit();
            if changed & bit == 0 {
                continue;
            }
            let pressed = state & bit != 0;
            if (self.buttons & bit != 0) == pressed {
                continue;
            }
            let target = match self.capture.or(hit) {
                Some(target) => target,
                None => continue,
            };
            if pressed {
                self.buttons |= bit;
                self.capture = Some(target);
            } else {
                self.buttons &= !bit;
            }
            result.push(ButtonDelta {
                target,
                button,
                pressed,
            });
            if self.buttons == 0 {
                self.capture = None;
            }
        }
        Ok(result)
    }
    pub fn reset(&mut self) -> Vec<ButtonDelta> {
        let target = self.capture.take();
        let buttons = std::mem::take(&mut self.buttons);
        target
            .into_iter()
            .flat_map(|target| {
                PointerButton::ALL.into_iter().filter_map(move |button| {
                    (buttons & button.bit() != 0).then_some(ButtonDelta {
                        target,
                        button,
                        pressed: false,
                    })
                })
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn hit(source: i64, window: i64) -> PointerCapture {
        PointerCapture {
            source: InputSourceId::new(source).unwrap(),
            window: WindowId::new(window).unwrap(),
            surface: SurfaceId::new(window).unwrap(),
        }
    }
    #[test]
    fn capture_keeps_original_surface_through_other_hit_and_off_panel_release() {
        let mut tracker = PointerTracker::default();
        let a = hit(1, 1);
        let b = hit(1, 2);
        assert_eq!(
            tracker.update(a.source, 1, 1, Some(a)).unwrap()[0].target,
            a
        );
        assert!(tracker.update(a.source, 1, 1, Some(b)).unwrap().is_empty());
        assert_eq!(
            tracker.update(a.source, 2, 3, Some(b)).unwrap()[0].target,
            a
        );
        assert_eq!(tracker.pressed(), 2);
        let events = tracker.update(a.source, 3, 0, None).unwrap();
        assert_eq!(events.len(), 2);
        assert!(events.iter().all(|e| e.target == a && !e.pressed));
        assert_eq!(tracker.capture(), None);
        assert_eq!(tracker.pressed(), 0);
        assert!(tracker.update(a.source, 3, 0, Some(b)).unwrap().is_empty());
        assert_eq!(
            tracker.update(a.source, 1, 1, Some(b)).unwrap()[0].target,
            b
        );
    }
    #[test]
    fn reset_releases_once_and_late_up_or_second_source_cannot_replay_or_steal() {
        let mut tracker = PointerTracker::default();
        let a = hit(1, 1);
        let b = hit(2, 2);
        tracker.update(a.source, 7, 7, Some(a)).unwrap();
        assert!(tracker.update(b.source, 1, 1, Some(b)).unwrap().is_empty());
        assert_eq!(tracker.capture(), Some(a));
        let releases = tracker.reset();
        assert_eq!(releases.len(), 3);
        assert_eq!(
            releases
                .iter()
                .map(|d| d.button.evdev())
                .collect::<Vec<_>>(),
            [0x110, 0x111, 0x112]
        );
        assert!(releases.iter().all(|e| e.target == a && !e.pressed));
        assert!(tracker.reset().is_empty());
        assert!(tracker.update(a.source, 7, 0, None).unwrap().is_empty());
        assert_eq!(
            tracker.update(b.source, 1, 1, Some(b)).unwrap()[0].target,
            b
        );
    }
    #[test]
    fn invalid_dto_or_mask_does_not_mutate_capture() {
        let a = hit(1, 1);
        let mut tracker = PointerTracker::default();
        assert_eq!(
            tracker.update(a.source, 8, 8, Some(a)),
            Err(PointerError::InvalidMask)
        );
        assert_eq!(
            tracker.update(a.source, 1, 1, Some(hit(2, 1))),
            Err(PointerError::InvalidTarget)
        );
        assert_eq!(tracker.capture(), None);
        let mut packet = PointerPacket {
            source: a.source,
            uv: Some([0.5, 0.5]),
            event: PointerEvent::Move,
            time_ms: 0,
        };
        assert!(packet.valid());
        packet.uv = Some([f64::NAN, 0.]);
        assert!(!packet.valid());
        packet.uv = None;
        packet.event = PointerEvent::Scroll {
            delta: [0., 1.],
            stop: [false, true],
        };
        assert!(!packet.valid());
        packet.event = PointerEvent::Scroll {
            delta: [0., 0.],
            stop: [false, true],
        };
        assert!(packet.valid());
    }
}
