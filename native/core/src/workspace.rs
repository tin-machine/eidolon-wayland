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
    pub restored_pose: bool,
    pub placement: Placement,
    pub summoned: bool,
    #[serde(skip)]
    temporary: Option<Placement>,
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
    initial_offset: [f32; 2],
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
            initial_offset: [0., 0.],
        }
    }
    pub fn set_initial_offset(&mut self, render_slot: eidolon_render::ExecutorSlot) {
        self.initial_offset = match render_slot {
            eidolon_render::ExecutorSlot::Primary => [0., 0.],
            eidolon_render::ExecutorSlot::Companion(2) => [1.8, 0.],
            eidolon_render::ExecutorSlot::Companion(n) => {
                let i = n - 3;
                let y = [1., -1., 2., -2., 3., -3., 4.][usize::from(i / 3)];
                [(f32::from(i % 3) - 1.) * 1.8, y]
            }
            eidolon_render::ExecutorSlot::Termux => [-1.8, 0.],
        };
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
            windows: self
                .windows
                .iter()
                .cloned()
                .map(|mut window| {
                    window.summoned = window.temporary.is_some();
                    if let Some(temporary) = window.temporary.take() {
                        window.placement = temporary;
                    }
                    window
                })
                .collect(),
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
                w.temporary = None;
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
                state.temporary = None;
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
                    restored_pose: false,
                    placement: Placement::new(PanelPose::at_slot(0)).unwrap(),
                    summoned: false,
                    temporary: None,
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
                    state.restored_pose = saved.is_some();
                    let pose = saved.as_ref().map_or_else(
                        || PanelPose::at_workspace_slot(slot, self.initial_offset),
                        |s| s.pose,
                    );
                    state.placement = Placement::new(pose).unwrap();
                    state.temporary = None;
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
            if state
                .temporary
                .as_ref()
                .is_some_and(|pose| plan.is_none_or(|plan| !pose.accepts(plan.target)))
            {
                state.temporary = None;
            }
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
        window
            .temporary
            .as_mut()
            .unwrap_or(&mut window.placement)
            .request(request, pose)
    }

    /// 全ての表示世代を検証してから一時配置を切り替える。通常の保存poseは書き換えない。
    pub fn summon_poses(
        &mut self,
        request: CommandSeq,
        targets: &[(TargetKey, PanelPose)],
    ) -> Result<(), &'static str> {
        if targets.is_empty() || targets.len() > eidolon_model::limits::VISIBLE_PANELS {
            return Err("INVALID_ARGUMENT");
        }
        let mut placements = Vec::with_capacity(targets.len());
        for (target, pose) in targets {
            let index = self.summon_target(*target)?;
            if placements.iter().any(|(old, _)| *old == index) {
                return Err("INVALID_ARGUMENT");
            }
            let mut effective = Placement::new(*pose)?;
            effective.bind(Some(*target));
            effective.request(request, *pose)?;
            placements.push((index, effective));
        }
        for (index, placement) in placements {
            self.windows[index].temporary = Some(placement);
        }
        Ok(())
    }

    pub fn return_summoned_poses(
        &mut self,
        request: CommandSeq,
        targets: &[TargetKey],
    ) -> Result<(), &'static str> {
        if targets.is_empty() || targets.len() > eidolon_model::limits::VISIBLE_PANELS {
            return Err("INVALID_ARGUMENT");
        }
        let mut indexes = Vec::with_capacity(targets.len());
        for target in targets {
            let index = self.summon_target(*target)?;
            if indexes.contains(&index) || self.windows[index].temporary.is_none() {
                return Err("STALE");
            }
            indexes.push(index);
        }
        for index in indexes {
            let window = &mut self.windows[index];
            window.placement.request(request, window.placement.pose)?;
            window.temporary = None;
        }
        Ok(())
    }

    pub fn keep_summoned_pose(&mut self, target: TargetKey) -> Result<(), &'static str> {
        let index = self.summon_target(target)?;
        let window = &mut self.windows[index];
        let effective = window.temporary.as_ref().ok_or("STALE")?;
        if effective.applied_pose.is_none() {
            return Err("NOT_READY");
        }
        window.placement = window.temporary.take().expect("temporary pose checked");
        Ok(())
    }

    pub fn clear_summoned_poses(&mut self) {
        for window in &mut self.windows {
            if window.temporary.take().is_some() {
                window.placement.applied_pose = None;
            }
        }
    }

    fn summon_target(&self, target: TargetKey) -> Result<usize, &'static str> {
        self.windows
            .iter()
            .position(|window| {
                window.session == target.session
                    && window.window == target.window
                    && window.slot.is_some()
                    && window.placement.accepts(target)
            })
            .ok_or("STALE")
    }

    /// The initial positioning request applies once, before a panel is created.
    pub fn set_initial_dialog_pose(
        &mut self,
        target: TargetKey,
        request: CommandSeq,
        pose: PanelPose,
    ) -> Result<(), &'static str> {
        let initial = self.windows.iter().any(|window| {
            !window.restored_pose
                && window.placement.accepts(target)
                && window.placement.pose_command.is_none()
        });
        if !initial {
            return Err("STALE");
        }
        self.set_pose(target, request, pose)
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
            .is_some_and(|w| {
                w.temporary
                    .as_mut()
                    .unwrap_or(&mut w.placement)
                    .acknowledge(target, request, pose)
            })
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
            parent: None,
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
            v3_evidence_required: false,
        }
    }
    #[test]
    fn all_launcher_workspaces_can_map_all_their_windows() {
        use eidolon_render::ExecutorSlot;
        let executors = [ExecutorSlot::Primary, ExecutorSlot::Termux]
            .into_iter()
            .chain((2..=eidolon_model::limits::LAST_WORKSPACE).map(ExecutorSlot::Companion));
        let windows: Vec<_> = (1..=9).map(window).collect();
        let panels: Vec<_> = windows
            .iter()
            .enumerate()
            .map(|(i, w)| panel(w, i, i as i64 + 1))
            .collect();
        for executor in executors {
            let mut workspace = Controller::load(None);
            workspace.set_initial_offset(executor);
            workspace.scene(Some(scene()));
            assert_eq!(
                workspace.sync(session(), &windows, &panels).len(),
                windows.len()
            );
            let snapshot = workspace.snapshot();
            for (i, w) in snapshot.windows.iter().enumerate() {
                let pose = w.placement.pose;
                assert!(pose.valid());
                assert!(
                    snapshot.windows[..i]
                        .iter()
                        .all(|earlier| earlier.placement.pose != pose)
                );
            }
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
    fn initial_dialog_pose_applies_once_and_does_not_move_the_parent() {
        let mut workspace = Controller::load(None);
        workspace.scene(Some(scene()));
        let parent = window(1);
        let dialog = window(2);
        let parent_panel = panel(&parent, 0, 1);
        let dialog_panel = panel(&dialog, 1, 2);
        let mut arc = PanelPose::at_slot(1);
        arc.position_m[0] = 1.0;
        let request = CommandSeq::new(5).unwrap();
        workspace.sync(
            session(),
            &[parent, dialog],
            &[parent_panel, dialog_panel.clone()],
        );
        workspace
            .set_initial_dialog_pose(dialog_panel.target, request, arc)
            .unwrap();
        assert!(
            workspace
                .set_initial_dialog_pose(
                    dialog_panel.target,
                    CommandSeq::new(6).unwrap(),
                    PanelPose::at_slot(1),
                )
                .is_err()
        );
        let state = workspace.snapshot();
        assert_eq!(state.windows[0].placement.pose, PanelPose::at_slot(0));
        assert_eq!(state.windows[1].placement.pose, arc);
    }
    #[test]
    fn automatic_facing_preserves_restored_user_pose() {
        let mut workspace = Controller::load(None);
        workspace.scene(Some(scene()));
        let a = window(1);
        let pa = panel(&a, 0, 1);
        workspace.sync(
            session(),
            std::slice::from_ref(&a),
            std::slice::from_ref(&pa),
        );
        let mut moved = PanelPose::at_slot(0);
        moved.position_m[0] = 0.25;
        let request = CommandSeq::new(3).unwrap();
        workspace.set_pose(pa.target, request, moved).unwrap();
        workspace.remember(&a);
        workspace.sync(session(), &[], &[]);
        workspace.sync(session(), &[a], std::slice::from_ref(&pa));
        assert!(workspace.windows[0].restored_pose);
        assert!(
            workspace
                .set_initial_dialog_pose(pa.target, request, PanelPose::at_slot(0))
                .is_err()
        );
        assert_eq!(workspace.windows[0].placement.pose, moved);
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
    fn temporary_pose_uses_geometry_ack_and_does_not_replace_saved_home() {
        let mut workspace = Controller::load(None);
        workspace.scene(Some(scene()));
        let app = window(1);
        let panel = panel(&app, 0, 1);
        workspace.sync(
            session(),
            std::slice::from_ref(&app),
            std::slice::from_ref(&panel),
        );
        let home = workspace.snapshot().windows[0].placement.pose;
        let mut summoned = home;
        summoned.position_m[2] = 1.0;
        let request = CommandSeq::new(10).unwrap();
        workspace
            .summon_poses(request, &[(panel.target, summoned)])
            .unwrap();
        assert!(workspace.snapshot().windows[0].summoned);
        assert!(!workspace.pose_applied(panel.target, Some(request), home));
        assert!(workspace.pose_applied(panel.target, Some(request), summoned));
        let mut adjusted = summoned;
        adjusted.position_m[0] = 0.3;
        let drag = request.checked_next().unwrap();
        workspace.set_pose(panel.target, drag, adjusted).unwrap();
        workspace.remember(&app);
        assert_eq!(
            workspace
                .store
                .value
                .restore("terminal", 0, 1)
                .unwrap()
                .pose,
            home
        );
        assert_eq!(workspace.snapshot().windows[0].placement.pose, adjusted);

        let restore = drag.checked_next().unwrap();
        workspace
            .return_summoned_poses(restore, &[panel.target])
            .unwrap();
        let report = &workspace.snapshot().windows[0];
        assert!(!report.summoned);
        assert_eq!(report.placement.pose, home);
        assert!(report.placement.applied_pose.is_none());
        assert!(!workspace.pose_applied(panel.target, Some(drag), adjusted));
        assert!(workspace.pose_applied(panel.target, Some(restore), home));

        let explicit = restore.checked_next().unwrap();
        workspace
            .summon_poses(explicit, &[(panel.target, adjusted)])
            .unwrap();
        assert_eq!(workspace.keep_summoned_pose(panel.target), Err("NOT_READY"));
        assert!(workspace.pose_applied(panel.target, Some(explicit), adjusted));
        workspace.keep_summoned_pose(panel.target).unwrap();
        workspace.remember(&app);
        assert_eq!(
            workspace
                .store
                .value
                .restore("terminal", 0, 1)
                .unwrap()
                .pose,
            adjusted
        );
    }

    #[test]
    fn temporary_batch_refuses_stale_members_before_applying_any_pose() {
        let mut workspace = Controller::load(None);
        workspace.scene(Some(scene()));
        let windows = [window(1), window(2)];
        let first = panel(&windows[0], 0, 1);
        let second = panel(&windows[1], 1, 2);
        workspace.sync(session(), &windows, &[first.clone(), second.clone()]);
        let old = panel(&windows[1], 1, 1).target;
        let pose = PanelPose::at_slot(0);
        let request = CommandSeq::new(2).unwrap();
        assert_eq!(
            workspace.summon_poses(request, &[(first.target, pose), (old, pose)]),
            Err("STALE")
        );
        assert!(
            workspace
                .snapshot()
                .windows
                .iter()
                .all(|window| !window.summoned)
        );
        workspace
            .summon_poses(request, &[(first.target, pose), (second.target, pose)])
            .unwrap();
        workspace.scene(Some(SceneEpoch::new(2).unwrap()));
        assert!(
            workspace
                .snapshot()
                .windows
                .iter()
                .all(|window| !window.summoned)
        );
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
