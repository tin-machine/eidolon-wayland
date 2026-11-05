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
    pub error: Option<&'static str>,
}

#[derive(Default)]
pub struct Controller {
    next: i64,
    commands: Vec<PanelCommand>,
    failed: Vec<(SessionId, WindowId, SceneEpoch)>,
}
impl Controller {
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
        self.failed.retain(|(session, window, failed_scene)| {
            scene == Some(*failed_scene) && mapped.contains(&(*session, *window))
        });
        let obsolete: Vec<_> = self
            .commands
            .iter()
            .filter(|command| {
                command.action == Action::Create
                    && (scene != Some(command.target.scene)
                        || !mapped.contains(&(command.target.session, command.target.window)))
            })
            .map(|command| command.target)
            .collect();
        for target in obsolete {
            self.retire(target)?;
        }
        let Some(scene) = scene.filter(|_| may_create) else {
            return Ok(());
        };
        for (session, window) in mapped {
            if self.failed.contains(&(*session, *window, scene)) {
                continue;
            }
            if self
                .commands
                .iter()
                .any(|c| c.target.session == *session && c.target.window == *window)
            {
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
                    session: *session,
                    window: *window,
                    scene,
                    generation: SurfaceGeneration::new(seq.get()).unwrap(),
                },
                applied: false,
                error: None,
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
