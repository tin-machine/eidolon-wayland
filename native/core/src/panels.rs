//! SDKの希望操作と適用済み状態を分ける、小さな再送可能command表。
pub use eidolon_model::commands::PanelAction as Action;
use eidolon_model::{
    ids::{CommandSeq, SceneEpoch, SessionId, SurfaceGeneration, WindowId},
    limits,
    state::Status,
    surface::TargetKey,
};
use serde::Serialize;

#[derive(Clone, Debug, Serialize)]
pub struct PanelCommand {
    pub command_seq: CommandSeq,
    pub action: Action,
    pub target: TargetKey,
    pub applied: bool,
    pub slot: usize,
    pub error: Option<&'static str>,
    pub decoration: Option<DecorationLayout>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct DecorationLayout {
    pub content: eidolon_model::window::LogicalSize,
    pub canvas: eidolon_model::window::LogicalSize,
    pub content_origin: [u32; 2],
}

#[derive(Default)]
pub struct Controller {
    next: i64,
    commands: Vec<PanelCommand>,
    failed: Vec<(SessionId, WindowId, SceneEpoch)>,
    visible: [Option<(SessionId, WindowId)>; limits::VISIBLE_PANELS],
}
impl Controller {
    pub fn visible(&self) -> Vec<(SessionId, WindowId)> {
        self.visible.iter().flatten().copied().collect()
    }
    pub fn show(&mut self, session: SessionId, window: WindowId, slot: usize) -> Status {
        if slot >= limits::VISIBLE_PANELS {
            return Status::InvalidArgument;
        }
        if let Some(previous) = self
            .visible
            .iter()
            .position(|key| *key == Some((session, window)))
        {
            self.visible.swap(previous, slot);
        } else {
            self.visible[slot] = Some((session, window));
        }
        Status::Accepted
    }
    pub fn advance_past(&mut self, generation: i64) {
        self.next = self.next.max(generation);
    }
    fn sequence(&mut self) -> Result<CommandSeq, &'static str> {
        self.next = self.next.checked_add(1).ok_or("ID_EXHAUSTED")?;
        CommandSeq::new(self.next).map_err(|_| "ID_EXHAUSTED")
    }
    pub fn snapshot(&self) -> Vec<PanelCommand> {
        self.commands.clone()
    }
    /// 同じwindowの旧Entityはdestroy ackまで残し、新世代を先行作成しない。
    pub fn reconcile(
        &mut self,
        scene: Option<SceneEpoch>,
        mapped: &[(SessionId, WindowId)],
        may_create: bool,
    ) -> Result<(), &'static str> {
        self.reconcile_layouts(scene, mapped, may_create, &[])
    }
    pub fn reconcile_layouts(
        &mut self,
        scene: Option<SceneEpoch>,
        mapped: &[(SessionId, WindowId)],
        may_create: bool,
        layouts: &[(SessionId, WindowId, DecorationLayout)],
    ) -> Result<(), &'static str> {
        let layout = |session, window| {
            layouts
                .iter()
                .find(|(s, w, _)| *s == session && *w == window)
                .map(|(_, _, l)| l.clone())
        };
        for slot in &mut self.visible {
            if slot.is_some_and(|key| !mapped.contains(&key)) {
                *slot = None;
            }
        }
        for key in mapped {
            if !self.visible.contains(&Some(*key))
                && let Some(slot) = self.visible.iter_mut().find(|slot| slot.is_none())
            {
                *slot = Some(*key);
            }
        }
        self.failed.retain(|(session, window, failed_scene)| {
            scene == Some(*failed_scene)
                && mapped.contains(&(*session, *window))
                && !self.commands.iter().any(|command| {
                    command.target.session == *session
                        && command.target.window == *window
                        && command.target.scene == *failed_scene
                        && command.error.is_some()
                        && command.decoration != layout(*session, *window)
                })
        });
        let obsolete: Vec<_> = self
            .commands
            .iter()
            .filter(|command| {
                command.action == Action::Create
                    && (scene != Some(command.target.scene)
                        || command.decoration
                            != layout(command.target.session, command.target.window)
                        || self.visible[command.slot]
                            != Some((command.target.session, command.target.window)))
            })
            .map(|command| command.target)
            .collect();
        for target in obsolete {
            self.retire(target)?;
        }
        let Some(scene) = scene.filter(|_| may_create) else {
            return Ok(());
        };
        for (slot, selected) in self
            .visible
            .iter()
            .enumerate()
            .filter_map(|(slot, key)| key.map(|key| (slot, key)))
            .collect::<Vec<_>>()
        {
            let (session, window) = selected;
            if self.failed.contains(&(session, window, scene)) {
                continue;
            }
            if self.commands.iter().any(|c| {
                c.slot == slot || (c.target.session == session && c.target.window == window)
            }) {
                continue;
            }
            if self.commands.len() >= limits::TOPLEVELS * 2 {
                return Err("RESOURCE_LIMIT");
            }
            let seq = self.sequence()?;
            self.commands.push(PanelCommand {
                command_seq: seq,
                action: Action::Create,
                target: TargetKey {
                    session,
                    window,
                    scene,
                    generation: SurfaceGeneration::new(seq.get()).unwrap(),
                },
                applied: false,
                slot,
                error: None,
                decoration: layout(session, window),
            });
        }
        Ok(())
    }
    pub fn accepts(&self, target: TargetKey) -> bool {
        self.commands.iter().any(|command| {
            command.target == target && command.action == Action::Create && command.error.is_none()
        })
    }
    pub fn contains(&self, seq: CommandSeq, target: TargetKey, action: Action) -> bool {
        self.commands
            .iter()
            .any(|c| c.command_seq == seq && c.target == target && c.action == action)
    }
    pub fn retire(&mut self, target: TargetKey) -> Result<(), &'static str> {
        let Some(index) = self.commands.iter().position(|c| c.target == target) else {
            return Ok(());
        };
        if self.commands[index].action == Action::Destroy {
            return Ok(());
        }
        let seq = self.sequence()?;
        let command = &mut self.commands[index];
        command.command_seq = seq;
        command.action = Action::Destroy;
        command.applied = false;
        command.error = None;
        Ok(())
    }
    pub fn acknowledge(
        &mut self,
        seq: CommandSeq,
        target: TargetKey,
        action: Action,
        success: bool,
    ) -> Status {
        let Some(index) = self
            .commands
            .iter()
            .position(|c| c.command_seq == seq && c.target == target && c.action == action)
        else {
            return Status::Stale;
        };
        if self.commands[index].error.is_some() {
            return Status::Failed;
        }
        if !success {
            self.commands[index].error = Some("SDK_PANEL_FAILED");
            let target = self.commands[index].target;
            let failed = (target.session, target.window, target.scene);
            if !self.failed.contains(&failed) {
                self.failed.push(failed);
            }
        } else if action == Action::Destroy {
            self.commands.remove(index);
        } else {
            self.commands[index].applied = true;
        }
        Status::Accepted
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn disabling_decoration_can_recover_from_sdk_creation_failure_without_automatic_retry() {
        use eidolon_model::window::LogicalSize;
        let mut panels = Controller::default();
        let scene = Some(SceneEpoch::new(1).unwrap());
        let mapped = mapped();
        let layouts = [(
            mapped[0].0,
            mapped[0].1,
            DecorationLayout {
                content: LogicalSize::new(1024, 768).unwrap(),
                canvas: LogicalSize::new(1280, 1280).unwrap(),
                content_origin: [128, 256],
            },
        )];
        panels
            .reconcile_layouts(scene, &mapped, true, &layouts)
            .unwrap();
        let failed = panels.snapshot()[0].clone();
        panels.acknowledge(failed.command_seq, failed.target, Action::Create, false);
        panels
            .reconcile_layouts(scene, &mapped, true, &layouts)
            .unwrap();
        assert!(panels.snapshot()[0].error.is_some()); // 同じ希望値を自動再試行しない。
        panels.reconcile(scene, &mapped, true).unwrap();
        let retiring = panels.snapshot()[0].clone();
        assert_eq!(retiring.action, Action::Destroy);
        panels.acknowledge(retiring.command_seq, retiring.target, Action::Destroy, true);
        panels.reconcile(scene, &mapped, true).unwrap();
        let plain = panels.snapshot()[0].clone();
        assert_eq!(plain.action, Action::Create);
        assert!(plain.decoration.is_none() && plain.error.is_none());
        assert!(plain.target.generation > failed.target.generation);
    }
    #[test]
    fn decoration_layout_change_retires_old_tuple_and_keeps_content_independent_of_canvas() {
        use eidolon_model::window::LogicalSize;
        let mut panels = Controller::default();
        let scene = Some(SceneEpoch::new(1).unwrap());
        let mapped = mapped();
        panels.reconcile(scene, &mapped, true).unwrap();
        let old = panels.snapshot()[0].clone();
        let layout = DecorationLayout {
            content: LogicalSize::new(1024, 768).unwrap(),
            canvas: LogicalSize::new(1280, 1280).unwrap(),
            content_origin: [128, 256],
        };
        let layouts = [(mapped[0].0, mapped[0].1, layout.clone())];
        panels
            .reconcile_layouts(scene, &mapped, true, &layouts)
            .unwrap();
        let retiring = panels.snapshot()[0].clone();
        assert_eq!(retiring.action, Action::Destroy);
        assert!(!panels.accepts(old.target));
        panels
            .reconcile_layouts(scene, &mapped, true, &layouts)
            .unwrap();
        assert_eq!(panels.snapshot().len(), 1);
        panels.acknowledge(retiring.command_seq, retiring.target, Action::Destroy, true);
        panels
            .reconcile_layouts(scene, &mapped, true, &layouts)
            .unwrap();
        let current = panels.snapshot()[0].clone();
        assert!(current.target.generation > old.target.generation);
        assert_eq!(current.decoration, Some(layout));
        assert_eq!(
            panels.acknowledge(old.command_seq, old.target, Action::Create, true),
            Status::Stale
        );
        panels.reconcile(scene, &mapped, true).unwrap();
        assert_eq!(panels.snapshot()[0].action, Action::Destroy);
    }
    #[test]
    fn two_slots_keep_third_logical_and_wait_for_destroy_before_reuse() {
        let session = SessionId::new(7).unwrap();
        let scene = Some(SceneEpoch::new(4).unwrap());
        let mapped: Vec<_> = (1..=3)
            .map(|id| (session, WindowId::new(id).unwrap()))
            .collect();
        let mut panels = Controller::default();
        panels.reconcile(scene, &mapped, true).unwrap();
        let first = panels.snapshot();
        assert_eq!(first.len(), 2);
        assert_eq!(panels.visible(), mapped[..2]);
        assert_eq!(panels.show(session, mapped[2].1, 0), Status::Accepted);
        panels.reconcile(scene, &mapped, true).unwrap();
        let retiring = panels.snapshot().into_iter().find(|p| p.slot == 0).unwrap();
        assert_eq!(retiring.action, Action::Destroy);
        assert!(!panels.accepts(first[0].target));
        assert_eq!(panels.snapshot().len(), 2);
        assert_eq!(
            panels.acknowledge(first[0].command_seq, first[0].target, Action::Create, true),
            Status::Stale
        );
        assert_eq!(
            panels.acknowledge(retiring.command_seq, retiring.target, Action::Destroy, true),
            Status::Accepted
        );
        panels.reconcile(scene, &mapped, true).unwrap();
        let next = panels.snapshot().into_iter().find(|p| p.slot == 0).unwrap();
        assert_eq!(next.target.window, mapped[2].1);
        assert!(next.target.generation > first[0].target.generation);
        // hidden first window remains logical and is not automatically reselected.
        panels.reconcile(scene, &mapped, true).unwrap();
        assert_eq!(panels.visible(), [mapped[2], mapped[1]]);
        assert_eq!(
            panels.show(session, mapped[0].1, 2),
            Status::InvalidArgument
        );
        panels.reconcile(scene, &mapped[..2], true).unwrap();
        assert_eq!(panels.visible(), mapped[..2]);
    }
    fn mapped() -> [(SessionId, WindowId); 1] {
        [(SessionId::new(1).unwrap(), WindowId::new(1).unwrap())]
    }
    #[test]
    fn scene_unmap_and_late_ack_cannot_apply_or_recreate_a_retired_entity() {
        let mut panels = Controller::default();
        let scene = Some(SceneEpoch::new(1).unwrap());
        panels.reconcile(scene, &mapped(), true).unwrap();
        let first = panels.snapshot()[0].clone();
        assert!(panels.accepts(first.target));
        assert!(!first.applied);
        panels.reconcile(scene, &[], true).unwrap();
        let destroy = panels.snapshot()[0].clone();
        assert!(!panels.accepts(first.target));
        assert_eq!(
            panels.acknowledge(first.command_seq, first.target, Action::Create, true),
            Status::Stale
        );
        panels.reconcile(scene, &mapped(), true).unwrap();
        assert_eq!(panels.snapshot().len(), 1);
        assert_eq!(
            panels.acknowledge(destroy.command_seq, destroy.target, Action::Destroy, true),
            Status::Accepted
        );
        panels.reconcile(scene, &mapped(), true).unwrap();
        let second = panels.snapshot()[0].clone();
        assert!(second.target.newer_than(first.target));
        assert_eq!(
            panels.acknowledge(first.command_seq, first.target, Action::Create, true),
            Status::Stale
        );
        panels
            .reconcile(Some(SceneEpoch::new(2).unwrap()), &mapped(), true)
            .unwrap();
        assert_eq!(panels.snapshot()[0].action, Action::Destroy);
    }
    #[test]
    fn failed_sdk_operation_is_durable_and_has_no_automatic_retry() {
        let mut panels = Controller::default();
        let scene = Some(SceneEpoch::new(1).unwrap());
        panels.reconcile(scene, &mapped(), true).unwrap();
        let first = panels.snapshot()[0].clone();
        assert_eq!(
            panels.acknowledge(first.command_seq, first.target, Action::Create, false),
            Status::Accepted
        );
        for _ in 0..100 {
            panels.reconcile(scene, &mapped(), true).unwrap();
        }
        assert_eq!(panels.snapshot().len(), 1);
        assert_eq!(panels.snapshot()[0].command_seq, first.command_seq);
        assert!(!panels.accepts(first.target));
        assert_eq!(panels.snapshot()[0].error, Some("SDK_PANEL_FAILED"));
        panels.retire(first.target).unwrap();
        let destroy = panels.snapshot()[0].clone();
        assert_eq!(
            panels.acknowledge(destroy.command_seq, destroy.target, Action::Destroy, true),
            Status::Accepted
        );
        panels.reconcile(scene, &mapped(), true).unwrap();
        assert!(panels.snapshot().is_empty());
    }
    #[test]
    fn product_generation_advances_past_diagnostic_and_never_wraps() {
        let mut panels = Controller::default();
        panels.advance_past(42);
        panels
            .reconcile(Some(SceneEpoch::new(1).unwrap()), &mapped(), true)
            .unwrap();
        assert_eq!(panels.snapshot()[0].target.generation.get(), 43);
        let mut exhausted = Controller::default();
        exhausted.advance_past(i64::MAX);
        assert_eq!(
            exhausted.reconcile(Some(SceneEpoch::new(1).unwrap()), &mapped(), true),
            Err("ID_EXHAUSTED")
        );
    }
}
