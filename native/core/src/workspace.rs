//! 生存中windowの配置と、寿命付きIDを含めない保存設定を対応付ける。
use crate::{
    compositor::protocol::WindowReport,
    panels::{Action, PanelCommand},
    preferences,
};
use eidolon_model::{
    ids::{CommandSeq, SceneEpoch, SessionId, WindowId},
    keyboard::KeyboardLayout,
    resize::PhysicalSize,
    surface::TargetKey,
    window::{LogicalSize, WindowLife},
    workspace::{Background, PanelPose, Placement, SavedPlacement},
};
use serde::Serialize;
use std::path::Path;

#[derive(Clone, Debug, Serialize)]
pub struct WindowPlacement {
    pub session: SessionId,
    pub window: WindowId,
    pub app_id: Option<String>,
    pub slot: Option<usize>,
    pub ambiguous: bool,
    pub placement: Placement,
    #[serde(skip)]
    last_slot: Option<usize>,
}
#[derive(Clone, Debug, Serialize)]
pub struct AppliedBackground {
    pub scene: SceneEpoch,
    pub background: Background,
    pub background_command: Option<CommandSeq>,
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub background: Background,
    pub background_command: Option<CommandSeq>,
    pub applied_background: Option<AppliedBackground>,
    pub keyboard_layout: KeyboardLayout,
    pub workload_observation: bool,
    pub storage: preferences::Report,
    pub pending_save: bool,
    pub windows: Vec<WindowPlacement>,
}
pub struct Restore {
    pub window: WindowId,
    pub physical_size_m: Option<PhysicalSize>,
    pub logical_size: Option<LogicalSize>,
}
pub struct Controller {
    store: preferences::Store,
    pending_save: bool,
    windows: Vec<WindowPlacement>,
    session: Option<SessionId>,
    scene: Option<SceneEpoch>,
    background_command: Option<CommandSeq>,
    applied_background: Option<AppliedBackground>,
}
impl Controller {
    pub fn load(root: Option<&Path>) -> Self {
        Self {
            store: preferences::Store::load(root),
            pending_save: false,
            windows: vec![],
            session: None,
            scene: None,
            background_command: None,
            applied_background: None,
        }
    }
    pub fn snapshot(&self) -> Report {
        Report {
            background: self.store.value.background,
            background_command: self.background_command,
            applied_background: self.applied_background.clone(),
            keyboard_layout: self.store.value.keyboard_layout,
            workload_observation: self.store.value.workload_observation,
            storage: self.store.report.clone(),
            pending_save: self.pending_save,
            windows: self.windows.clone(),
        }
    }
    /// runtime ownerはこのstorageだけをMutex外へ持ち出す。model側は値と結果だけ保持する。
    pub fn detach_storage(&mut self) -> preferences::Store {
        let copy = self.store.value.clone();
        let report = self.store.report.clone();
        let storage = std::mem::replace(&mut self.store, preferences::Store::load(None));
        self.store.value = copy;
        self.store.report = report;
        storage
    }
    pub fn take_pending_save(&mut self) -> Option<eidolon_model::workspace::Preferences> {
        if !self.pending_save {
            return None;
        }
        self.pending_save = false;
        Some(self.store.value.clone())
    }
    pub fn storage_result(&mut self, report: preferences::Report) {
        self.store.report = report;
    }
    pub fn workload_observation(&self) -> bool {
        self.store.value.workload_observation
    }
    pub fn set_workload_observation(&mut self, enabled: bool) {
        if self.store.value.workload_observation != enabled {
            self.store.value.workload_observation = enabled;
            self.pending_save = true;
        }
    }
    pub fn keyboard_layout(&self) -> KeyboardLayout {
        self.store.value.keyboard_layout
    }
    pub fn scene(&mut self, scene: Option<SceneEpoch>) {
        if self.scene != scene {
            self.scene = scene;
            self.applied_background = None;
            for w in &mut self.windows {
                w.placement.bind(None);
            }
        }
    }
    pub fn sync(
        &mut self,
        session: SessionId,
        windows: &[WindowReport],
        panels: &[PanelCommand],
    ) -> Vec<Restore> {
        if self.session != Some(session) {
            self.session = Some(session);
            self.windows.clear();
        }
        self.windows
            .retain(|p| windows.iter().any(|w| w.window == p.window));
        for state in &mut self.windows {
            if windows
                .iter()
                .find(|w| w.window == state.window)
                .is_none_or(|w| w.life != WindowLife::Mapped)
            {
                state.slot = None;
                state.placement.bind(None);
            }
        }
        let mut restored = vec![];
        for window in windows.iter().filter(|w| w.life == WindowLife::Mapped) {
            let plan = panels.iter().find(|p| {
                p.target.session == session
                    && Some(p.target.scene) == self.scene
                    && p.slot < eidolon_model::limits::VISIBLE_PANELS
                    && p.target.window == window.window
                    && p.action == Action::Create
                    && p.error.is_none()
            });
            let slot = plan.map(|p| p.slot);
            let matches = window.app_id.as_ref().map_or(0, |id| {
                windows
                    .iter()
                    .filter(|w| w.life == WindowLife::Mapped && w.app_id.as_ref() == Some(id))
                    .count()
            });
            let ambiguous = matches > 1;
            let mut index = self.windows.iter().position(|w| w.window == window.window);
            if index.is_none() {
                index = Some(self.windows.len());
                self.windows.push(WindowPlacement {
                    session,
                    window: window.window,
                    app_id: window.app_id.clone(),
                    slot: None,
                    ambiguous: false,
                    placement: Placement::new(PanelPose::at_slot(0)).unwrap(),
                    last_slot: None,
                });
            }
            let state = &mut self.windows[index.unwrap()];
            let changed_id = state.app_id != window.app_id;
            if let Some(slot) = slot {
                if state.last_slot != Some(slot) || changed_id {
                    let saved = window
                        .app_id
                        .as_ref()
                        .and_then(|id| self.store.value.restore(id, slot, matches));
                    let pose = saved.as_ref().map_or(PanelPose::at_slot(slot), |s| s.pose);
                    state.placement = Placement::new(pose).unwrap();
                    restored.push(Restore {
                        window: window.window,
                        physical_size_m: Some(
                            saved
                                .as_ref()
                                .map_or(PhysicalSize::default(), |s| s.physical_size_m),
                        ),
                        logical_size: saved.and_then(|s| s.logical_size),
                    });
                }
                state.last_slot = Some(slot);
            }
            state.slot = slot;
            state.ambiguous = ambiguous;
            state.app_id = window.app_id.clone();
            let rebound = plan.is_some_and(|p| !state.placement.accepts(p.target));
            if rebound && !restored.iter().any(|a| a.window == window.window) {
                restored.push(Restore {
                    window: window.window,
                    physical_size_m: None,
                    logical_size: None,
                });
            }
            state.placement.bind(plan.map(|p| p.target));
        }
        restored
    }
    pub fn set_pose(
        &mut self,
        target: TargetKey,
        request: CommandSeq,
        pose: PanelPose,
    ) -> Result<(), &'static str> {
        let window = self
            .windows
            .iter_mut()
            .find(|w| {
                w.session == target.session
                    && w.window == target.window
                    && w.slot.is_some()
                    && w.placement.accepts(target)
            })
            .ok_or("STALE")?;
        window.placement.request(request, pose)
    }
    pub fn pose_applied(
        &mut self,
        target: TargetKey,
        request: Option<CommandSeq>,
        pose: PanelPose,
    ) -> bool {
        self.windows
            .iter_mut()
            .find(|w| w.session == target.session && w.window == target.window)
            .is_some_and(|w| w.placement.acknowledge(target, request, pose))
    }
    pub fn remember(&mut self, window: &WindowReport) {
        let Some(state) = self.windows.iter().find(|w| w.window == window.window) else {
            return;
        };
        let (Some(app_id), Some(slot)) = (state.app_id.as_ref(), state.slot) else {
            return;
        };
        let logical = window
            .resize
            .requested_logical_size
            .or(window.logical_size)
            .filter(|s| eidolon_model::resize::logical_size(s.width, s.height).is_ok());
        let saved = SavedPlacement {
            app_id: app_id.clone(),
            slot,
            pose: state.placement.pose,
            physical_size_m: window.sizing.physical_size_m,
            logical_size: logical,
        };
        if self
            .store
            .value
            .remember(saved, if state.ambiguous { 2 } else { 1 })
            == Ok(true)
        {
            self.pending_save = true;
        }
    }
    pub fn set_background(&mut self, request: CommandSeq, background: Background) {
        self.store.value.background = background;
        self.background_command = Some(request);
        self.applied_background = None;
        self.pending_save = true;
    }
    pub fn background_applied(
        &mut self,
        scene: SceneEpoch,
        request: Option<CommandSeq>,
        background: Background,
    ) -> bool {
        if self.scene != Some(scene)
            || self.background_command != request
            || self.store.value.background != background
        {
            return false;
        }
        self.applied_background = Some(AppliedBackground {
            scene,
            background_command: request,
            background,
        });
        true
    }
    pub fn set_keyboard_layout(&mut self, layout: KeyboardLayout) {
        self.store.value.keyboard_layout = layout;
        self.pending_save = true;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use eidolon_model::ids::{FrameSeq, SurfaceGeneration};
    fn session() -> SessionId {
        SessionId::new(1).unwrap()
    }
    fn scene() -> SceneEpoch {
        SceneEpoch::new(1).unwrap()
    }
    fn window(id: i64) -> WindowReport {
        WindowReport {
            window: WindowId::new(id).unwrap(),
            app_id: Some("terminal".into()),
            life: WindowLife::Mapped,
            frame: Some(FrameSeq::new(1).unwrap()),
            logical_size: Some(LogicalSize::new(1024, 768).unwrap()),
            layers: 1,
            resize: Default::default(),
            sizing: Default::default(),
        }
    }
    fn panel(window: &WindowReport, slot: usize, generation: i64) -> PanelCommand {
        PanelCommand {
            command_seq: CommandSeq::new(generation).unwrap(),
            target: TargetKey {
                session: session(),
                window: window.window,
                scene: scene(),
                generation: SurfaceGeneration::new(generation).unwrap(),
            },
            action: Action::Create,
            slot,
            applied: true,
            error: None,
            decoration: None,
        }
    }
    #[test]
    fn initial_nine_poses_precede_sdk_creation_panel_ack_and_pose_ack() {
        let mut c = Controller::load(None);
        c.scene(Some(scene()));
        let windows = (1..=9).map(window).collect::<Vec<_>>();
        // First map is visible before the panel reconciler has produced its commands.
        c.sync(session(), &windows, &[]);
        assert!(c.snapshot().windows.iter().all(|w| w.slot.is_none()));
        let panels = windows
            .iter()
            .enumerate()
            .map(|(slot, w)| {
                let mut p = panel(w, slot, slot as i64 + 1);
                p.applied = false;
                p
            })
            .collect::<Vec<_>>();
        let restores = c.sync(session(), &windows, &panels);
        assert_eq!(restores.len(), 9);
        for (slot, w) in c.snapshot().windows.iter().enumerate() {
            assert_eq!(w.slot, Some(slot));
            assert_eq!(w.placement.pose, PanelPose::at_slot(slot));
            assert!(w.placement.pose.valid());
            assert!(w.placement.applied_pose.is_none());
            assert!(w.placement.accepts(panels[slot].target));
        }
        // A stale scene must not supply a creation slot, even though its desired pose is retained.
        c.scene(Some(SceneEpoch::new(2).unwrap()));
        c.sync(session(), &windows, &panels);
        assert!(c.snapshot().windows.iter().all(|w| w.slot.is_none()));
    }
    #[test]
    fn duplicate_app_keeps_existing_pose_and_stale_target_cannot_move_live_window() {
        let mut c = Controller::load(None);
        c.scene(Some(scene()));
        let a = window(1);
        let b = window(2);
        let pa = panel(&a, 0, 1);
        let pb = panel(&b, 1, 2);
        c.sync(
            session(),
            std::slice::from_ref(&a),
            std::slice::from_ref(&pa),
        );
        let mut moved = PanelPose::at_slot(0);
        moved.position_m[0] = 0.25;
        let request = CommandSeq::new(5).unwrap();
        c.set_pose(pa.target, request, moved).unwrap();
        c.remember(&a);
        assert!(c.store.value.restore("terminal", 0, 1).is_some());
        let restored = c.sync(
            session(),
            &[a.clone(), b.clone()],
            &[pa.clone(), pb.clone()],
        );
        assert_eq!(restored.len(), 1);
        assert_eq!(c.windows[0].placement.pose, moved);
        assert!(c.windows.iter().all(|w| w.ambiguous));
        c.set_pose(pa.target, request, moved).unwrap();
        c.remember(&a);
        c.sync(session(), &[a.clone(), b.clone()], &[pa.clone(), pb]);
        assert_eq!(c.windows[0].placement.pose, moved);
        let newer = panel(&a, 0, 9);
        c.sync(session(), &[a.clone(), b], std::slice::from_ref(&newer));
        assert!(
            c.set_pose(pa.target, request, PanelPose::at_slot(0))
                .is_err()
        );
        assert!(!c.pose_applied(pa.target, Some(request), moved));
        assert!(c.pose_applied(newer.target, Some(request), moved));
        let mut unmap = a;
        unmap.life = WindowLife::Unmapped;
        c.sync(session(), &[unmap], &[]);
        assert!(c.windows[0].slot.is_none());
        assert!(c.windows[0].placement.applied_pose.is_none());
    }
    #[test]
    fn background_ack_does_not_survive_scene_or_new_request_and_does_not_change_session() {
        let mut c = Controller::load(None);
        c.scene(Some(scene()));
        let a = window(1);
        let p = panel(&a, 0, 1);
        c.sync(session(), &[a], &[p]);
        let request = CommandSeq::new(3).unwrap();
        c.set_background(request, Background::Virtual);
        assert!(!c.background_applied(scene(), None, Background::Virtual));
        assert!(c.background_applied(scene(), Some(request), Background::Virtual));
        c.scene(Some(SceneEpoch::new(2).unwrap()));
        assert!(c.applied_background.is_none());
        assert!(!c.background_applied(scene(), Some(request), Background::Virtual));
        assert_eq!(c.session, Some(session()));
        assert_eq!(c.snapshot().background, Background::Virtual);
    }
}
