//! 設定の正本。保存DTOへsession/window/Surfaceの寿命付きIDを入れない。
use crate::{
    ids::CommandSeq, keyboard::KeyboardLayout, limits, resize::PhysicalSize, surface::TargetKey,
    window::LogicalSize,
};
use serde::{Deserialize, Serialize};

pub const SETTINGS_BYTES: usize = 64 * 1024;
pub const SAVED_PLACEMENTS: usize = 16;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Background {
    #[default]
    Passthrough,
    Virtual,
}

#[derive(Clone, Copy, Debug, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PanelPose {
    pub position_m: [f32; 3],
    pub rotation_xyzw: [f32; 4],
}
impl PanelPose {
    pub fn at_slot(slot: usize) -> Self {
        Self {
            position_m: crate::window_slots::Position::ORDER.get(slot).map_or(
                [f32::NAN; 3],
                |position| {
                    let [x, y] = position.grid();
                    [f32::from(x) * 1.8, 2.0 + f32::from(y) * 1.8, 1.5]
                },
            ),
            rotation_xyzw: [0., 0., 0., 1.],
        }
    }
    pub fn valid(self) -> bool {
        let [x, y, z] = self.position_m;
        let norm = self.rotation_xyzw.iter().map(|v| v * v).sum::<f32>();
        x.is_finite()
            && y.is_finite()
            && z.is_finite()
            && (-10. ..=10.).contains(&x)
            && (0. ..=4.).contains(&y)
            && (-10. ..=10.).contains(&z)
            && self.rotation_xyzw.iter().all(|v| v.is_finite())
            && (norm - 1.).abs() <= 0.001
    }
    pub fn near(self, other: Self) -> bool {
        self.valid()
            && other.valid()
            && self
                .position_m
                .iter()
                .zip(other.position_m)
                .all(|(a, b)| (*a - b).abs() <= 0.0001)
            && [1., -1.].into_iter().any(|sign| {
                self.rotation_xyzw
                    .iter()
                    .zip(other.rotation_xyzw)
                    .all(|(a, b)| (*a - sign * b).abs() <= 0.0001)
            })
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct AppliedPose {
    pub target: TargetKey,
    pub pose_command: Option<CommandSeq>,
    pub pose: PanelPose,
}
#[derive(Clone, Debug, Serialize)]
pub struct Placement {
    pub pose: PanelPose,
    pub pose_command: Option<CommandSeq>,
    pub applied_pose: Option<AppliedPose>,
    #[serde(skip)]
    bound: Option<TargetKey>,
}
impl Placement {
    pub fn new(pose: PanelPose) -> Result<Self, &'static str> {
        if !pose.valid() {
            return Err("INVALID_ARGUMENT");
        }
        Ok(Self {
            pose,
            pose_command: None,
            applied_pose: None,
            bound: None,
        })
    }
    pub fn accepts(&self, target: TargetKey) -> bool {
        self.bound == Some(target)
    }
    pub fn bind(&mut self, target: Option<TargetKey>) {
        if self.bound != target {
            self.bound = target;
            self.applied_pose = None;
        }
    }
    pub fn request(&mut self, command: CommandSeq, pose: PanelPose) -> Result<(), &'static str> {
        if !pose.valid() {
            return Err("INVALID_ARGUMENT");
        }
        self.pose = pose;
        self.pose_command = Some(command);
        self.applied_pose = None;
        Ok(())
    }
    pub fn acknowledge(
        &mut self,
        target: TargetKey,
        command: Option<CommandSeq>,
        actual: PanelPose,
    ) -> bool {
        if self.bound != Some(target) || self.pose_command != command || !self.pose.near(actual) {
            return false;
        }
        self.applied_pose = Some(AppliedPose {
            target,
            pose_command: command,
            pose: actual,
        });
        true
    }
}

#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SavedPlacement {
    pub app_id: String,
    pub slot: usize,
    pub pose: PanelPose,
    pub physical_size_m: PhysicalSize,
    pub logical_size: Option<LogicalSize>,
}
impl SavedPlacement {
    pub fn valid(&self) -> bool {
        !self.app_id.is_empty()
            && self.app_id.len() <= 512
            && !self.app_id.chars().any(char::is_control)
            && self.slot < limits::VISIBLE_PANELS
            && self.pose.valid()
            && self.physical_size_m.valid()
            && self
                .logical_size
                .is_none_or(|size| crate::resize::logical_size(size.width, size.height).is_ok())
    }
}
fn default_observation() -> bool {
    true
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Preferences {
    pub schema: u32,
    pub background: Background,
    pub keyboard_layout: KeyboardLayout,
    #[serde(default = "default_observation")]
    pub workload_observation: bool,
    pub placements: Vec<SavedPlacement>,
}
impl Default for Preferences {
    fn default() -> Self {
        Self {
            schema: 1,
            background: Background::Passthrough,
            keyboard_layout: KeyboardLayout::Us,
            workload_observation: true,
            placements: vec![],
        }
    }
}
impl Preferences {
    pub fn valid(&self) -> bool {
        self.schema == 1
            && self.placements.len() <= SAVED_PLACEMENTS
            && self.placements.iter().enumerate().all(|(i, p)| {
                p.valid()
                    && !self.placements[..i]
                        .iter()
                        .any(|old| old.app_id == p.app_id && old.slot == p.slot)
            })
    }
    pub fn decode(bytes: &[u8]) -> Result<Self, &'static str> {
        if bytes.len() > SETTINGS_BYTES {
            return Err("LAYOUT_TOO_LARGE");
        }
        let value: Self = serde_json::from_slice(bytes).map_err(|_| "LAYOUT_INVALID")?;
        if !value.valid() {
            return Err(if value.schema != 1 {
                "LAYOUT_SCHEMA"
            } else {
                "LAYOUT_INVALID"
            });
        }
        Ok(value)
    }
    pub fn encode(&self) -> Result<Vec<u8>, &'static str> {
        if !self.valid() {
            return Err("LAYOUT_INVALID");
        }
        let bytes = serde_json::to_vec(self).map_err(|_| "LAYOUT_INVALID")?;
        if bytes.len() > SETTINGS_BYTES {
            return Err("LAYOUT_TOO_LARGE");
        }
        Ok(bytes)
    }
    /// 同じapp_idが複数なら、保存済みslotから現在windowを推測しない。
    pub fn restore(
        &self,
        app_id: &str,
        slot: usize,
        matching_windows: usize,
    ) -> Option<SavedPlacement> {
        if matching_windows != 1 {
            return None;
        }
        self.placements
            .iter()
            .find(|p| p.app_id == app_id && p.slot == slot)
            .cloned()
    }
    pub fn remember(
        &mut self,
        placement: SavedPlacement,
        matching_windows: usize,
    ) -> Result<bool, &'static str> {
        if !placement.valid() {
            return Err("LAYOUT_INVALID");
        }
        if matching_windows != 1 {
            return Ok(false);
        }
        self.placements
            .retain(|p| p.app_id != placement.app_id || p.slot != placement.slot);
        if self.placements.len() == SAVED_PLACEMENTS {
            self.placements.remove(0);
        }
        self.placements.push(placement);
        Ok(true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ids::{SceneEpoch, SessionId, SurfaceGeneration, WindowId};
    fn target(generation: i64) -> TargetKey {
        TargetKey {
            session: SessionId::new(4).unwrap(),
            window: WindowId::new(3).unwrap(),
            scene: SceneEpoch::new(2).unwrap(),
            generation: SurfaceGeneration::new(generation).unwrap(),
        }
    }
    fn saved() -> SavedPlacement {
        SavedPlacement {
            app_id: "terminal".into(),
            slot: 0,
            pose: PanelPose::at_slot(0),
            physical_size_m: PhysicalSize::default(),
            logical_size: Some(LogicalSize::new(800, 600).unwrap()),
        }
    }
    #[test]
    fn observation_setting_migrates_old_records_and_roundtrips_disabled() {
        let mut value: Preferences = serde_json::from_str(
            r#"{"schema":1,"background":"passthrough","keyboard_layout":"us","placements":[]}"#,
        )
        .unwrap();
        assert!(value.workload_observation);
        value.workload_observation = false;
        let restored: Preferences =
            serde_json::from_slice(&serde_json::to_vec(&value).unwrap()).unwrap();
        assert!(!restored.workload_observation && restored.valid());
    }

    #[test]
    fn settings_bound_schema_values_duplicates_and_never_persist_runtime_ids() {
        let mut p = Preferences::default();
        p.remember(saved(), 1).unwrap();
        let bytes = p.encode().unwrap();
        let text = String::from_utf8(bytes.clone()).unwrap();
        assert!(
            !text.contains("session") && !text.contains("window") && !text.contains("generation")
        );
        assert_eq!(
            Preferences::decode(&bytes).unwrap().placements,
            p.placements
        );
        assert!(
            Preferences::decode(text.replace("\"schema\":1", "\"schema\":2").as_bytes()).is_err()
        );
        assert!(Preferences::decode(&vec![b' '; SETTINGS_BYTES + 1]).is_err());
        let mut bad = saved();
        bad.pose.position_m[0] = f32::INFINITY;
        assert!(!bad.valid());
        bad = saved();
        bad.pose.rotation_xyzw = [0.; 4];
        assert!(!bad.valid());
        bad = saved();
        bad.logical_size = Some(LogicalSize {
            width: 1,
            height: 1,
        });
        assert!(!bad.valid());
        bad = saved();
        bad.app_id = "bad\napp".into();
        assert!(!bad.valid());
        p.placements.push(saved());
        assert!(p.encode().is_err());
    }
    #[test]
    fn ambiguous_app_id_restores_defaults_and_bounded_recent_slots_replace_by_app_slot() {
        let mut p = Preferences::default();
        p.remember(saved(), 1).unwrap();
        assert!(p.restore("terminal", 0, 2).is_none());
        assert!(p.restore("terminal", 0, 1).is_some());
        assert!(!p.remember(saved(), 2).unwrap());
        for i in 0..40 {
            let mut next = saved();
            next.app_id = format!("app-{i}");
            p.remember(next, 1).unwrap();
        }
        assert_eq!(p.placements.len(), SAVED_PLACEMENTS);
        assert!(p.valid());
        assert!(p.restore("terminal", 0, 1).is_none());
    }
    #[test]
    fn pose_ack_is_bound_to_request_target_generation_and_equivalent_orientation() {
        let mut p = Placement::new(PanelPose::at_slot(0)).unwrap();
        p.bind(Some(target(1)));
        assert!(p.acknowledge(target(1), None, p.pose));
        p.bind(Some(target(2)));
        assert!(p.applied_pose.is_none());
        let command = CommandSeq::new(7).unwrap();
        let pose = PanelPose::at_slot(1);
        p.request(command, pose).unwrap();
        assert!(!p.acknowledge(target(1), Some(command), pose));
        assert!(!p.acknowledge(target(2), None, pose));
        let mut near = pose;
        near.rotation_xyzw = [0., 0., 0., -1.];
        assert!(p.acknowledge(target(2), Some(command), near));
        near.position_m[0] += 0.02;
        assert!(!p.acknowledge(target(2), Some(command), near));
    }
}

#[cfg(test)]
mod nine_grid_tests {
    use super::*;
    use crate::{
        window_controls::{ControlAnchor, WindowControls},
        window_slots::Envelope,
    };
    #[test]
    fn nine_defaults_follow_priority_and_fit_pose_range_with_body_controls_and_default_terminal_canvas()
     {
        let poses = (0..crate::window_slots::CAPACITY)
            .map(PanelPose::at_slot)
            .collect::<Vec<_>>();
        assert!(poses.iter().all(|p| p.valid()));
        let center = poses[0].position_m;
        for (index, [dx, dy]) in [
            [0, 0],
            [1, 0],
            [-1, 0],
            [0, 1],
            [0, -1],
            [-1, 1],
            [1, 1],
            [-1, -1],
            [1, -1],
        ]
        .iter()
        .enumerate()
        {
            let p = poses[index].position_m;
            let direction = |delta: f32| {
                if delta > 0. {
                    1
                } else if delta < 0. {
                    -1
                } else {
                    0
                }
            };
            assert_eq!(direction(p[0] - center[0]), *dx);
            assert_eq!(direction(p[1] - center[1]), *dy);
            assert_eq!(p[2], center[2]);
        }
        for anchor in [
            ControlAnchor::TopLeft,
            ControlAnchor::TopRight,
            ControlAnchor::BottomLeft,
            ControlAnchor::BottomRight,
        ] {
            let controls = WindowControls {
                anchor,
                diameter_m: 0.12,
                gap_m: 0.08,
                offset_m: [0.2, 0.2],
            };
            let e = Envelope::for_window(1.2, 0.9, &controls)
                .unwrap()
                .with_canvas(
                    PhysicalSize::default(),
                    LogicalSize::new(1024, 768).unwrap(),
                    LogicalSize::new(1280, 1280).unwrap(),
                    [128, 256],
                )
                .unwrap();
            for (i, a) in poses.iter().enumerate() {
                for b in &poses[i + 1..] {
                    let ax = f64::from(a.position_m[0]);
                    let ay = f64::from(a.position_m[1]);
                    let bx = f64::from(b.position_m[0]);
                    let by = f64::from(b.position_m[1]);
                    assert!(
                        ax + e.right < bx + e.left
                            || bx + e.right < ax + e.left
                            || ay + e.top < by + e.bottom
                            || by + e.top < ay + e.bottom
                    );
                }
            }
        }
        assert!(!PanelPose::at_slot(crate::window_slots::CAPACITY).valid());
    }
}
