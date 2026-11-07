//! Render ownerのwindow単位時計。SDK Surface/GL contextの世代から独立する。
use crate::decoration::{Composition, Rect};
use eidolon_model::{
    ids::{SessionId, WindowId},
    window::LogicalSize,
};
use eidolon_theme::{
    manifest::Slot,
    player::{Frame, Player},
    raster::CompiledTheme,
};
use serde::Serialize;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::Arc,
    time::Duration,
};

#[derive(Clone, Default, PartialEq)]
pub struct SemanticState {
    pub focused: bool,
    pub busy: bool,
    pub cpu_cores: Option<f64>,
    pub rss_bytes: Option<u64>,
    pub io_bytes_per_second: Option<f64>,
    pub workload_identity: Option<String>,
}
impl SemanticState {
    pub fn valid(&self) -> bool {
        self.io_bytes_per_second
            .is_none_or(|v| v.is_finite() && v >= 0.)
            && self.cpu_cores.is_none_or(|v| v.is_finite() && v >= 0.)
            && self
                .workload_identity
                .as_ref()
                .is_none_or(|s| s.len() <= 256)
    }
    fn classes(&self) -> BTreeSet<String> {
        [("focused", self.focused), ("busy", self.busy)]
            .into_iter()
            .filter(|(_, yes)| *yes)
            .map(|(name, _)| name.into())
            .collect()
    }
}
#[derive(Clone, Debug, Serialize)]
pub struct Canvas {
    pub size: LogicalSize,
    pub content_origin: [u32; 2],
}
impl Canvas {
    pub fn for_content(content: LogicalSize) -> Result<Self, &'static str> {
        LogicalSize::new(content.width, content.height).map_err(|_| "THEME_LAYOUT_INVALID")?;
        // 正方形外周。大きいappではリングの規定sizeを維持し、文字をresizeしない。
        let side = content
            .width
            .max(
                content
                    .height
                    .checked_add(32)
                    .ok_or("THEME_LAYOUT_INVALID")?,
            )
            .clamp(1280, eidolon_model::limits::BUFFER_DIMENSION);
        let size = LogicalSize::new(side, side).map_err(|_| "THEME_LAYOUT_INVALID")?;
        Ok(Self {
            size,
            content_origin: [(side - content.width) / 2, (side - content.height) / 2],
        })
    }
    fn slots(&self, content: LogicalSize) -> BTreeMap<Slot, Rect> {
        let [x, y] = self.content_origin.map(|v| v as f32);
        let [w, h] = [content.width as f32, content.height as f32];
        BTreeMap::from([
            (
                Slot::WindowBackdrop,
                Rect {
                    origin: [0., 0.],
                    size: [self.size.width as f32, self.size.height as f32],
                },
            ),
            (
                Slot::WindowFrame,
                Rect {
                    origin: [x, y],
                    size: [w, h],
                },
            ),
            (
                Slot::TitlebarEnd,
                Rect {
                    origin: [x + w - 24., y - 24.],
                    size: [24., 24.],
                },
            ),
            (
                Slot::TitlebarBottom,
                Rect {
                    origin: [x, y - 3.],
                    size: [w, 3.],
                },
            ),
        ])
    }
}
pub struct Drawing {
    pub canvas: Canvas,
    pub frame: Frame,
    slots: BTreeMap<Slot, Rect>,
}
/// 最後の成功drawへ使ったI/Oだけ。受信予定値や室内/入力本文は含めない。
#[derive(Clone, Debug, Serialize)]
pub struct IoDraw {
    pub workload_identity: Option<String>,
    pub bytes_per_second: Option<f64>,
    pub inner_rotation_degrees: Option<f64>,
    pub theme_revision: u64,
}
impl Drawing {
    pub fn io_draw(&self, state: &SemanticState, theme_revision: u64) -> IoDraw {
        use eidolon_theme::style::{Property, Transform, Value};
        let angle = self
            .frame
            .parts
            .get("ring-inner")
            .and_then(|part| part.get(&Property::Transform))
            .and_then(|value| match value {
                Value::Transform(items) => items.iter().rev().find_map(|item| match item {
                    Transform::Rotate(angle) => Some(*angle),
                    _ => None,
                }),
                _ => None,
            });
        IoDraw {
            workload_identity: state.workload_identity.clone(),
            bytes_per_second: state.io_bytes_per_second,
            inner_rotation_degrees: angle,
            theme_revision,
        }
    }
    pub fn composition<'a>(&'a self, theme: &'a CompiledTheme) -> Composition<'a> {
        Composition {
            theme,
            frame: &self.frame,
            canvas: self.canvas.size,
            content_origin: self.canvas.content_origin,
            slots: &self.slots,
        }
    }
}
struct Window {
    player: Player,
    visible: bool,
    workload_identity: Option<String>,
}
#[derive(Default)]
pub struct Players {
    windows: BTreeMap<(SessionId, WindowId), Window>,
}
impl Players {
    pub fn retain(&mut self, logical: &[(SessionId, WindowId)]) {
        self.windows.retain(|key, _| logical.contains(key));
    }
    pub fn clear(&mut self) {
        self.windows.clear();
    }
    pub fn visibility(
        &mut self,
        now: Duration,
        visible: &[(SessionId, WindowId)],
    ) -> Result<(), &'static str> {
        for (key, window) in &mut self.windows {
            let next = visible.contains(key);
            if next != window.visible {
                window
                    .player
                    .set_visible(now, next)
                    .map_err(|_| "THEME_CLOCK_INVALID")?;
                window.visible = next;
            }
        }
        Ok(())
    }
    pub fn elapsed(&self, key: (SessionId, WindowId), now: Duration) -> Option<Duration> {
        self.windows.get(&key)?.player.elapsed(now).ok()
    }
    pub fn draw(
        &mut self,
        key: (SessionId, WindowId),
        theme: &Arc<CompiledTheme>,
        now: Duration,
        content: LogicalSize,
        reduced: bool,
        state: &SemanticState,
    ) -> Result<Drawing, &'static str> {
        let window = self.windows.entry(key).or_insert_with(|| Window {
            player: Player::new(now),
            visible: true,
            workload_identity: None,
        });
        if window.workload_identity != state.workload_identity {
            window.player.clear_metrics();
            window.workload_identity = state.workload_identity.clone();
        }
        let frame = window
            .player
            .evaluate_with_metrics(
                theme,
                now,
                true,
                reduced,
                &state.classes(),
                eidolon_theme::player::Metrics {
                    cpu_cores: state.cpu_cores,
                    rss_bytes: state.rss_bytes,
                    io_bytes_per_second: state.io_bytes_per_second,
                },
            )
            .map_err(|_| "THEME_EVALUATION_FAILED")?;
        let canvas = Canvas::for_content(content)?;
        let slots = canvas.slots(content);
        Ok(Drawing {
            canvas,
            frame,
            slots,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use eidolon_theme::{
        raster,
        style::{Property, Transform, Value},
    };
    #[test]
    fn workload_change_clears_hold_last_without_replacing_window_clock() {
        let theme = Arc::new(
            raster::compile(
                &eidolon_theme::cyan_orbit(),
                &BTreeMap::new(),
                &Default::default(),
                &Default::default(),
            )
            .unwrap(),
        );
        let mut players = Players::default();
        let key = (SessionId::new(1).unwrap(), WindowId::new(1).unwrap());
        let size = LogicalSize::new(1024, 768).unwrap();
        let mut state = SemanticState {
            workload_identity: Some("first".into()),
            cpu_cores: Some(1.),
            busy: true,
            ..Default::default()
        };
        let first = players
            .draw(key, &theme, Duration::ZERO, size, false, &state)
            .unwrap();
        assert!(first.frame.parts["cpu-meter"].contains_key(&Property::Value));
        let moving = players
            .draw(key, &theme, Duration::from_secs(1), size, false, &state)
            .unwrap();
        assert_ne!(
            first.frame.parts["ring-outer"],
            moving.frame.parts["ring-outer"]
        );
        state.cpu_cores = None;
        assert!(
            players
                .draw(key, &theme, Duration::from_secs(1), size, false, &state)
                .unwrap()
                .frame
                .parts["cpu-meter"]
                .contains_key(&Property::Value)
        );
        state.workload_identity = Some("next".into());
        let changed = players
            .draw(key, &theme, Duration::from_secs(2), size, false, &state)
            .unwrap();
        assert!(!changed.frame.parts["cpu-meter"].contains_key(&Property::Value));
        assert_eq!(
            moving.frame.parts["ring-outer"][&Property::Transform],
            changed.frame.parts["ring-outer"][&Property::Transform]
        );
    }
    #[test]
    fn window_clocks_survive_surface_absence_and_resize_without_sharing_phase() {
        let theme = Arc::new(
            raster::compile(
                &eidolon_theme::cyan_orbit(),
                &BTreeMap::new(),
                &Default::default(),
                &Default::default(),
            )
            .unwrap(),
        );
        let mut players = Players::default();
        let key = (SessionId::new(1).unwrap(), WindowId::new(1).unwrap());
        let other = (key.0, WindowId::new(2).unwrap());
        let size = LogicalSize::new(1024, 768).unwrap();
        let state = SemanticState {
            busy: true,
            cpu_cores: Some(4.),
            ..Default::default()
        };
        let sec = Duration::from_secs;
        let angle = |d: Drawing| d.frame.parts["ring-outer"][&Property::Transform].clone();
        players
            .draw(key, &theme, sec(0), size, false, &state)
            .unwrap();
        players
            .draw(other, &theme, sec(2), size, false, &state)
            .unwrap();
        assert_eq!(
            angle(
                players
                    .draw(key, &theme, sec(3), size, false, &state)
                    .unwrap()
            ),
            Value::Transform(vec![Transform::Rotate(172.5)])
        );
        assert_eq!(
            angle(
                players
                    .draw(other, &theme, sec(3), size, false, &state)
                    .unwrap()
            ),
            Value::Transform(vec![Transform::Rotate(52.5)])
        );
        players.visibility(sec(3), &[other]).unwrap(); // old Surface gone; logical window remains
        players.visibility(sec(100), &[key, other]).unwrap();
        let resized = players
            .draw(
                key,
                &theme,
                sec(100),
                LogicalSize::new(800, 600).unwrap(),
                false,
                &state,
            )
            .unwrap();
        assert_eq!(resized.canvas.content_origin, [240, 340]);
        assert_eq!(
            angle(resized),
            Value::Transform(vec![Transform::Rotate(172.5)])
        );
        players.retain(&[other]); // true logical unmap resets only this clock
        assert_eq!(
            angle(
                players
                    .draw(key, &theme, sec(101), size, false, &state)
                    .unwrap()
            ),
            Value::Transform(vec![Transform::Rotate(0.)])
        );
    }
}
