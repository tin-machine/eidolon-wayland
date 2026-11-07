//! 単一core workerのgroup owner。保存workspaceやBoardの寿命を所有しない。
use crate::{
    diagnostics::Snapshot,
    window_groups::{FootCapture, FootOrigin, capture_foot, inspect_foot},
};
use eidolon_model::{
    ids::{CommandSeq, FrameSeq, RuntimeHandle, SessionId, WindowId},
    resize::PhysicalSize,
    state::{RuntimeState, Status, XrState},
    surface::TargetKey,
    window::WindowLife,
    window_groups::{Action, Group, Layout, Phase, Presentation, Scope},
    workspace::PanelPose,
};
use serde::Serialize;
use std::{collections::VecDeque, time::Duration};

#[derive(Clone, Copy)]
pub struct Ticket {
    pub request: CommandSeq,
    pub target: TargetKey,
    pub controls_revision: u64,
    pub expected_group: Option<CommandSeq>,
    pub expected_revision: Option<CommandSeq>,
    pub action: Action,
}
#[derive(Clone, Copy)]
pub struct GeometryAck {
    pub group: CommandSeq,
    pub revision: CommandSeq,
    pub target: TargetKey,
    pub pose: PanelPose,
    pub physical_size: PhysicalSize,
    pub content_frame: FrameSeq,
}
#[derive(Clone, Copy)]
pub struct World<'a> {
    pub snapshot: &'a Snapshot,
    pub transport: &'a eidolon_transport::session::Snapshot,
    pub render: &'a eidolon_render::Snapshot,
    pub now: Duration,
}
#[derive(Clone, Debug, Serialize)]
pub struct Candidate {
    pub session: SessionId,
    pub window: WindowId,
}
#[derive(Clone, Debug, Serialize)]
pub struct WindowReport {
    #[serde(flatten)]
    pub presentation: Presentation,
    pub required_frame: Option<FrameSeq>,
    pub geometry_applied: bool,
}
#[derive(Clone, Debug, Serialize)]
pub struct GroupReport {
    pub id: CommandSeq,
    pub revision: CommandSeq,
    pub scope: Scope,
    pub phase: Phase,
    pub layout: Layout,
    pub source_request: Option<CommandSeq>,
    pub source_ready: bool,
    pub windows: Vec<WindowReport>,
    pub reason: Option<&'static str>,
}
#[derive(Clone, Debug, Serialize)]
pub struct Completion {
    pub group: CommandSeq,
    pub revision: CommandSeq,
    pub request: CommandSeq,
    pub scope: Scope,
    pub layout: Layout,
    pub completed: bool,
    pub reason: Option<&'static str>,
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub candidates: Vec<Candidate>,
    pub groups: Vec<GroupReport>,
    pub completions: VecDeque<Completion>,
    pub blocked_session: Option<SessionId>,
    pub error: Option<&'static str>,
}
#[derive(Clone)]
struct Record {
    id: CommandSeq,
    model: Group,
    origin: FootOrigin,
    source_request: Option<CommandSeq>,
    required: Vec<(TargetKey, FrameSeq)>,
    acknowledged: Vec<TargetKey>,
    revision: i64,
    source_ready: bool,
    last_capture: FootCapture,
    theme: u64,
    reason: Option<&'static str>,
}
#[derive(Default)]
struct Clock {
    wall: Option<Duration>,
    logical: Duration,
    running: bool,
}
impl Clock {
    fn step(&mut self, now: Duration, running: bool) -> Result<(), Status> {
        if let Some(last) = self.wall {
            let delta = now.checked_sub(last).ok_or(Status::Stale)?;
            if self.running {
                self.logical = self.logical.checked_add(delta).ok_or(Status::Failed)?;
            }
        }
        self.wall = Some(now);
        self.running = running;
        Ok(())
    }
}
pub struct Controller {
    runtime: RuntimeHandle,
    record: Option<Record>,
    next_group: i64,
    next_operation: i64,
    clock: Clock,
    was_visible: bool,
    report: Report,
    reset_requested: bool,
    failed: bool,
}
impl Controller {
    pub fn new(runtime: RuntimeHandle) -> Self {
        Self {
            runtime,
            record: None,
            next_group: 0,
            next_operation: 0,
            clock: Clock::default(),
            was_visible: false,
            report: Report::default(),
            reset_requested: false,
            failed: false,
        }
    }
    fn group_id(&mut self) -> Result<CommandSeq, Status> {
        self.next_group = self.next_group.checked_add(1).ok_or(Status::Failed)?;
        CommandSeq::new(self.next_group).map_err(|_| Status::Failed)
    }
    fn operation(&mut self) -> Result<CommandSeq, Status> {
        self.next_operation = self.next_operation.checked_add(1).ok_or(Status::Failed)?;
        CommandSeq::new(self.next_operation).map_err(|_| Status::Failed)
    }
    fn visible(world: World<'_>) -> bool {
        world.snapshot.state == RuntimeState::Ready
            && world.snapshot.activity_visible
            && world.snapshot.xr_state == XrState::Focused
            && !world.render.paused
    }
    fn frames(world: World<'_>, group: &Group) -> Vec<(TargetKey, FrameSeq)> {
        group
            .saved()
            .iter()
            .filter_map(|(key, _, _)| {
                world
                    .snapshot
                    .app
                    .as_ref()?
                    .windows
                    .iter()
                    .find(|window| window.window == key.window)?
                    .frame
                    .map(|frame| (*key, frame))
            })
            .collect()
    }
    fn completion(&mut self, record: &mut Record, completed: bool, reason: Option<&'static str>) {
        if let Some(request) = record.source_request.take() {
            if self.report.completions.len() == 8 {
                self.report.completions.pop_front();
            }
            self.report.completions.push_back(Completion {
                group: record.id,
                revision: CommandSeq::new(record.model.current_revision()).unwrap(),
                request,
                scope: record.model.scope().clone(),
                layout: record.model.current_layout(),
                completed,
                reason,
            });
        }
    }
    fn quiesce_if_member_focused(&mut self, world: World<'_>, record: &Record) {
        if world
            .snapshot
            .app
            .as_ref()
            .and_then(|app| app.input.focus_window)
            .is_some_and(|focused| {
                record
                    .model
                    .saved()
                    .iter()
                    .any(|(target, _, _)| target.window == focused)
            })
        {
            self.reset_requested = true;
        }
    }
    pub fn take_input_reset(&mut self) -> bool {
        std::mem::take(&mut self.reset_requested)
    }
    pub fn snapshot(&self) -> Report {
        self.report.clone()
    }
    pub fn hidden_targets(&self) -> Vec<TargetKey> {
        self.report
            .groups
            .iter()
            .flat_map(|group| group.windows.iter())
            .filter(|window| !window.presentation.present)
            .map(|window| window.presentation.target)
            .collect()
    }
    pub fn input_allowed(&self, target: TargetKey) -> bool {
        if self.report.blocked_session == Some(target.session) {
            return false;
        }
        if let Some(window) = self
            .report
            .groups
            .iter()
            .flat_map(|group| group.windows.iter())
            .find(|window| {
                window.presentation.target.session == target.session
                    && window.presentation.target.window == target.window
            })
        {
            return window.presentation.target == target && window.presentation.input_allowed;
        }
        self.report.groups.is_empty()
            || !self.report.candidates.iter().any(|candidate| {
                candidate.session == target.session && candidate.window == target.window
            })
    }
    pub fn blocks_geometry(&self, target: TargetKey) -> bool {
        if self.failed
            && self.record.as_ref().is_some_and(|record| {
                record.model.scope().session == target.session
                    && record
                        .model
                        .saved()
                        .iter()
                        .any(|(key, _, _)| key.window == target.window)
            })
        {
            return true;
        }
        self.report.groups.iter().any(|group| {
            group.scope.session == target.session
                && group
                    .windows
                    .iter()
                    .any(|window| window.presentation.target.window == target.window)
        })
    }
    /// 内部の採番/時刻/モデル失敗でもcaptureと配送gateを捨てない。回復はruntime再起動だけ。
    pub fn observe(&mut self, world: World<'_>) -> Result<(), Status> {
        if self.failed {
            return Err(Status::NotReady);
        }
        let retained = self.record.clone();
        let previous_report = self.report.clone();
        if let Err(code) = self.observe_inner(world) {
            self.failed = true;
            self.record = self.record.take().or(retained);
            self.report = previous_report;
            self.report.error = Some("GROUP_OBSERVATION_FAILED");
            if let Some(record) = &self.record {
                self.report.blocked_session = Some(record.model.scope().session);
            }
            for window in self
                .report
                .groups
                .iter_mut()
                .flat_map(|group| &mut group.windows)
            {
                window.presentation.input_allowed = false;
            }
            self.reset_requested = self.record.is_some();
            return Err(code);
        }
        Ok(())
    }
    fn observe_inner(&mut self, world: World<'_>) -> Result<(), Status> {
        let visible = Self::visible(world);
        self.clock.step(world.now, visible)?;
        let inspection =
            inspect_foot(self.runtime, world.snapshot, world.transport, world.render).ok();
        self.report.candidates = inspection.as_ref().map_or_else(Vec::new, |value| {
            value
                .entries
                .iter()
                .filter_map(|entry| {
                    if let eidolon_model::window_slots::Phase::Bound { window } = entry.state {
                        Some(Candidate {
                            session: value.scope.session,
                            window,
                        })
                    } else {
                        None
                    }
                })
                .collect()
        });
        self.report.groups.clear();
        self.report.blocked_session = None;
        let Some(mut record) = self.record.take() else {
            self.was_visible = visible;
            return Ok(());
        };
        let session = record.model.scope().session;
        if world
            .snapshot
            .app
            .as_ref()
            .is_none_or(|app| app.session != Some(session) || app.state == "stopped")
        {
            self.completion(&mut record, false, Some("GROUP_SESSION_ENDED"));
            self.was_visible = visible;
            return Ok(());
        }
        if self.was_visible && !visible && record.model.phase(self.clock.logical) != Phase::Blocked
        {
            record
                .model
                .sample(self.clock.logical)
                .map_err(|_| Status::Stale)?;
            record.model.cancel("paused").map_err(|_| Status::Stale)?;
            self.completion(&mut record, false, Some("GROUP_PAUSED"));
        }
        for (target, _, _) in record.model.saved() {
            let live = world
                .snapshot
                .app
                .as_ref()
                .unwrap()
                .windows
                .iter()
                .any(|window| {
                    window.window == target.window && window.life != WindowLife::Destroyed
                });
            if !live {
                self.quiesce_if_member_focused(world, &record);
                record
                    .model
                    .remove(
                        self.operation()?,
                        target,
                        self.clock.logical,
                        world.snapshot.theme.reduced_motion,
                    )
                    .map_err(|_| Status::Stale)?;
                self.quiesce_if_member_focused(world, &record);
            }
        }
        if record.model.phase(self.clock.logical) == Phase::Empty {
            self.completion(&mut record, false, Some("GROUP_EMPTY"));
            self.was_visible = visible;
            return Ok(());
        }
        let origin_current = inspection.as_ref().is_some_and(|value| {
            value.origin == record.origin
                && value.scope.runtime == self.runtime
                && value.scope.session == session
        });
        if visible && origin_current {
            match capture_foot(self.runtime, world.snapshot, world.transport, world.render) {
                Ok(mut capture) => {
                    let saved = record.model.saved();
                    let replaced = record.model.scope() != &capture.scope
                        || saved.iter().any(|(key, _, _)| {
                            capture.members.iter().any(|member| {
                                member.target.window == key.window && member.target != *key
                            })
                        });
                    if replaced {
                        self.quiesce_if_member_focused(world, &record);
                        self.completion(&mut record, false, Some("GROUP_TARGET_REBOUND"));
                        for member in &mut capture.members {
                            if record.last_capture.members.iter().any(|old| {
                                old.target.window == member.target.window
                                    && old.entry.lease == member.entry.lease
                            }) && let Some((_, pose, size)) = saved
                                .iter()
                                .find(|(key, _, _)| key.window == member.target.window)
                            {
                                member.pose = *pose;
                                member.physical_size = *size;
                            }
                        }
                        let old_front = record.model.confirmed_front();
                        let front = if capture
                            .members
                            .iter()
                            .any(|member| member.target.window == old_front)
                        {
                            old_front
                        } else {
                            capture.members[0].target.window
                        };
                        let model = Group::restore(
                            capture.scope.clone(),
                            capture.members.clone(),
                            front,
                            record.model.confirmed_anchor(),
                            record.model.stable_layout(),
                            self.clock.logical,
                        )
                        .map_err(|_| Status::Stale)?;
                        record.id = self.group_id()?;
                        record.model = model;
                        record.origin = capture.origin.clone();
                        record.source_request = None;
                        record.last_capture = capture;
                        record.acknowledged.clear();
                        record.required = Self::frames(world, &record.model);
                        record.theme = world.render.theme_revision;
                    } else {
                        if record.model.phase(self.clock.logical) != Phase::Empty {
                            for member in &capture.members {
                                if record
                                    .model
                                    .saved()
                                    .iter()
                                    .all(|(target, _, _)| target.window != member.target.window)
                                    && record.model.phase(self.clock.logical) != Phase::Blocked
                                {
                                    record
                                        .model
                                        .admit(
                                            self.operation()?,
                                            member.clone(),
                                            self.clock.logical,
                                            world.snapshot.theme.reduced_motion,
                                        )
                                        .map_err(|_| Status::Stale)?;
                                    self.quiesce_if_member_focused(world, &record);
                                }
                            }
                        }
                        record.last_capture = capture;
                    }
                    record.source_ready = true;
                    record.reason = None;
                    if record.theme != world.render.theme_revision
                        && record.model.phase(self.clock.logical) != Phase::Blocked
                    {
                        record
                            .model
                            .sample(self.clock.logical)
                            .map_err(|_| Status::Stale)?;
                        record
                            .model
                            .cancel("theme_changed")
                            .map_err(|_| Status::Stale)?;
                        self.completion(&mut record, false, Some("GROUP_THEME_CHANGED"));
                        self.quiesce_if_member_focused(world, &record);
                        record.theme = world.render.theme_revision;
                    }
                }
                Err(_) => {
                    // 全captureが揃うまで既存memberも止める。部分的なpanel一致を分類証明に代用しない。
                    record.source_ready = false;
                    record.reason = Some("GROUP_CAPTURE_PENDING");
                }
            }
        } else {
            record.source_ready = false;
            record.reason = Some(if visible {
                "GROUP_SOURCE_CHANGED"
            } else {
                "GROUP_PAUSED"
            });
        }
        if record.model.phase(self.clock.logical) == Phase::Empty {
            self.completion(&mut record, false, Some("GROUP_EMPTY"));
            self.was_visible = visible;
            return Ok(());
        }
        let mut presentations = record
            .model
            .sample(self.clock.logical)
            .map_err(|_| Status::Stale)?;
        if record.revision != record.model.current_revision() {
            record.revision = record.model.current_revision();
            record.acknowledged.clear();
            record.required = Self::frames(world, &record.model);
        }
        if !record.source_ready || !visible {
            self.quiesce_if_member_focused(world, &record);
            for presentation in &mut presentations {
                presentation.input_allowed = false;
                presentation.present = true;
                presentation.clock_running = true;
            }
            if !origin_current || !visible {
                self.report.blocked_session = Some(session);
            }
        }
        let phase = record.model.phase(self.clock.logical);
        if phase == Phase::Stable && record.source_ready {
            let reason = record.model.cancel_reason();
            self.completion(&mut record, reason.is_none(), reason);
            if record.model.stable_layout() == Layout::Expanded {
                self.was_visible = visible;
                return Ok(());
            }
        }
        self.report.groups.push(GroupReport {
            id: record.id,
            revision: CommandSeq::new(record.model.current_revision())
                .map_err(|_| Status::Failed)?,
            scope: record.model.scope().clone(),
            phase,
            layout: record.model.current_layout(),
            source_request: record.source_request,
            source_ready: record.source_ready,
            reason: record.reason.or(record.model.cancel_reason()),
            windows: presentations
                .into_iter()
                .map(|presentation| WindowReport {
                    required_frame: record
                        .required
                        .iter()
                        .find(|(key, _)| *key == presentation.target)
                        .map(|(_, frame)| *frame),
                    geometry_applied: record.acknowledged.contains(&presentation.target),
                    presentation,
                })
                .collect(),
        });
        self.record = Some(record);
        self.was_visible = visible;
        Ok(())
    }
    fn base_geometry_ready(capture: &FootCapture, world: World<'_>) -> bool {
        capture.members.iter().all(|member| {
            world.snapshot.workspace.windows.iter().any(|window| {
                window.session == member.target.session
                    && window.window == member.target.window
                    && window
                        .placement
                        .applied_pose
                        .as_ref()
                        .is_some_and(|applied| {
                            applied.target == member.target
                                && applied.pose_command == window.placement.pose_command
                                && applied.pose.near(member.pose)
                        })
            }) && world.snapshot.app.as_ref().is_some_and(|app| {
                app.windows.iter().any(|window| {
                    window.window == member.target.window
                        && window.sizing.applied_physical_size_m == Some(member.physical_size)
                })
            })
        })
    }
    pub fn start(&mut self, ticket: Ticket, world: World<'_>) -> Status {
        if self.observe(world).is_err() {
            return Status::NotReady;
        }
        if ticket.controls_revision != world.render.theme_revision {
            return Status::Stale;
        }
        let Ok(capture) = capture_foot(self.runtime, world.snapshot, world.transport, world.render)
        else {
            return Status::NotReady;
        };
        if capture.member(ticket.target).is_none() {
            return Status::Stale;
        }
        let had_record = self.record.is_some();
        let mut record = if let Some(record) = self.record.take() {
            if ticket.expected_group != Some(record.id)
                || ticket.expected_revision.map(|value| value.get())
                    != Some(record.model.current_revision())
                || record.origin != capture.origin
                || record.model.scope() != &capture.scope
            {
                self.record = Some(record);
                return Status::Stale;
            }
            record
        } else {
            if ticket.expected_group.is_some()
                || ticket.expected_revision.is_some()
                || ticket.action != Action::Collapse
                || capture.members.len() < 2
                || !Self::base_geometry_ready(&capture, world)
            {
                return Status::NotReady;
            }
            let Ok(id) = self.group_id() else {
                return Status::Failed;
            };
            let Ok(model) = Group::new(
                capture.scope.clone(),
                capture.members.clone(),
                ticket.target.window,
                self.clock.logical,
            ) else {
                return Status::NotReady;
            };
            Record {
                id,
                model,
                origin: capture.origin.clone(),
                source_request: None,
                required: vec![],
                acknowledged: vec![],
                revision: 0,
                source_ready: true,
                last_capture: capture.clone(),
                theme: world.render.theme_revision,
                reason: None,
            }
        };
        let goal = if ticket.action == Action::Expand {
            Layout::Expanded
        } else {
            Layout::Collapsed
        };
        let front = if ticket.action == Action::NextFront {
            let current = record.model.current_front();
            let Some(index) = capture
                .members
                .iter()
                .position(|member| member.target.window == current)
            else {
                self.record = Some(record);
                return Status::Stale;
            };
            capture.members[(index + 1) % capture.members.len()].target
        } else {
            ticket.target
        };
        let mut superseded = record.clone();
        let operation = match self.operation() {
            Ok(value) => value,
            Err(code) => {
                self.record = Some(record);
                return code;
            }
        };
        if record
            .model
            .arrange(
                operation,
                goal,
                front,
                self.clock.logical,
                world.snapshot.theme.reduced_motion,
                false,
            )
            .is_err()
        {
            if had_record {
                self.record = Some(record);
            }
            return Status::NotReady;
        }
        self.completion(&mut superseded, false, Some("GROUP_REVERSED"));
        record.source_request = Some(ticket.request);
        record.required = Self::frames(world, &record.model);
        record.acknowledged.clear();
        record.revision = record.model.current_revision();
        record.reason = None;
        self.quiesce_if_member_focused(world, &record);
        self.record = Some(record);
        if self.observe(world).is_err() {
            Status::NotReady
        } else {
            Status::Accepted
        }
    }
    pub fn applied(&mut self, ack: GeometryAck, world: World<'_>) -> Status {
        if self.observe(world).is_err() {
            return Status::NotReady;
        }
        let Some(mut record) = self.record.take() else {
            return Status::Stale;
        };
        let code = (|| {
            if ack.group != record.id
                || ack.revision.get() != record.model.current_revision()
                || !record.source_ready
                || record.model.phase(self.clock.logical) != Phase::Settling
            {
                return Status::Stale;
            }
            let Ok(capture) =
                capture_foot(self.runtime, world.snapshot, world.transport, world.render)
            else {
                return Status::NotReady;
            };
            if capture.origin != record.origin
                || capture.scope != *record.model.scope()
                || capture.member(ack.target).is_none()
            {
                return Status::Stale;
            }
            let Some(required) = record
                .required
                .iter()
                .find(|(key, _)| *key == ack.target)
                .map(|(_, frame)| *frame)
            else {
                return Status::NotReady;
            };
            if ack.content_frame < required
                || !world.render.targets.iter().any(|target| {
                    target.key == ack.target
                        && target.frames > 0
                        && !target.presentation_paused
                        && target
                            .content_frame
                            .is_some_and(|frame| frame >= ack.content_frame)
                })
            {
                return Status::NotReady;
            }
            let Ok(presentations) = record.model.sample(self.clock.logical) else {
                return Status::Stale;
            };
            if !presentations.iter().any(|presentation| {
                presentation.target == ack.target
                    && presentation.pose.near(ack.pose)
                    && presentation.physical_size == ack.physical_size
            }) {
                return Status::Stale;
            }
            if record
                .model
                .applied(
                    ack.revision.get(),
                    ack.target,
                    ack.pose,
                    true,
                    self.clock.logical,
                )
                .is_err()
            {
                return Status::Stale;
            }
            if !record.acknowledged.contains(&ack.target) {
                record.acknowledged.push(ack.target);
            }
            Status::Accepted
        })();
        self.record = Some(record);
        if code == Status::Accepted && self.observe(world).is_err() {
            Status::NotReady
        } else {
            code
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::window_groups::tests::fixture;
    use eidolon_model::ids::{SceneEpoch, SurfaceGeneration};

    struct Fixture {
        runtime: RuntimeHandle,
        snapshot: Snapshot,
        transport: eidolon_transport::session::Snapshot,
        render: eidolon_render::Snapshot,
        now: Duration,
    }
    impl Fixture {
        fn new(count: usize) -> Self {
            let (runtime, mut snapshot, transport, render) = fixture(count);
            for window in &mut snapshot.workspace.windows {
                let target = render
                    .targets
                    .iter()
                    .find(|target| target.key.window == window.window)
                    .unwrap()
                    .key;
                assert!(window.placement.acknowledge(
                    target,
                    window.placement.pose_command,
                    window.placement.pose
                ));
            }
            for window in &mut snapshot.app.as_mut().unwrap().windows {
                window.sizing.applied_physical_size_m = Some(window.sizing.physical_size_m);
            }
            Self {
                runtime,
                snapshot,
                transport,
                render,
                now: Duration::ZERO,
            }
        }
        fn world(&self) -> World<'_> {
            World {
                snapshot: &self.snapshot,
                transport: &self.transport,
                render: &self.render,
                now: self.now,
            }
        }
        fn key(&self, index: usize) -> TargetKey {
            self.render.targets[index].key
        }
        fn ticket(&self, owner: &Controller, action: Action, request: i64) -> Ticket {
            let group = owner.snapshot().groups.into_iter().next();
            Ticket {
                request: CommandSeq::new(request).unwrap(),
                target: self.key(0),
                controls_revision: self.render.theme_revision,
                expected_group: group.as_ref().map(|g| g.id),
                expected_revision: group.map(|g| g.revision),
                action,
            }
        }
        fn advance(&mut self, owner: &mut Controller, ms: u64) {
            self.now += Duration::from_millis(ms);
            owner.observe(self.world()).unwrap();
        }
        fn ack(&self, group: &GroupReport, index: usize) -> GeometryAck {
            let window = &group.windows[index];
            GeometryAck {
                group: group.id,
                revision: group.revision,
                target: window.presentation.target,
                pose: window.presentation.pose,
                physical_size: window.presentation.physical_size,
                content_frame: window.required_frame.unwrap(),
            }
        }
        fn finish(&self, owner: &mut Controller) {
            let group = owner.snapshot().groups[0].clone();
            for i in 0..group.windows.len() {
                assert_eq!(
                    owner.applied(self.ack(&group, i), self.world()),
                    Status::Accepted
                );
            }
        }
    }
    #[test]
    fn collapse_requires_every_full_target_geometry_and_only_front_can_receive_input() {
        let mut f = Fixture::new(3);
        let mut owner = Controller::new(f.runtime);
        assert_eq!(
            owner.start(f.ticket(&owner, Action::Collapse, 100), f.world()),
            Status::Accepted
        );
        assert!(
            f.render
                .targets
                .iter()
                .all(|target| !owner.input_allowed(target.key))
        );
        assert!(owner.hidden_targets().is_empty());
        let early = owner.snapshot().groups[0].clone();
        assert_eq!(owner.applied(f.ack(&early, 0), f.world()), Status::Stale);
        f.advance(&mut owner, 500);
        let group = owner.snapshot().groups[0].clone();
        assert_eq!(owner.applied(f.ack(&group, 0), f.world()), Status::Accepted);
        assert!(!owner.input_allowed(f.key(0)));
        assert_eq!(owner.applied(f.ack(&group, 1), f.world()), Status::Accepted);
        assert!(!owner.input_allowed(f.key(0)));
        assert_eq!(owner.applied(f.ack(&group, 2), f.world()), Status::Accepted);
        assert!(owner.input_allowed(f.key(0)));
        assert!(!owner.input_allowed(f.key(1)));
        assert_eq!(owner.hidden_targets(), vec![f.key(1), f.key(2)]);
        assert_eq!(owner.snapshot().completions[0].request.get(), 100);
        assert!(owner.snapshot().completions[0].completed);
        assert_eq!(f.snapshot.window_slots.occupied, 3);
    }
    #[test]
    fn first_group_never_uses_unacknowledged_workspace_pose_or_size_as_animation_start() {
        let mut f = Fixture::new(2);
        let mut owner = Controller::new(f.runtime);
        let saved = f.snapshot.workspace.windows[1]
            .placement
            .applied_pose
            .take();
        assert_eq!(
            owner.start(f.ticket(&owner, Action::Collapse, 100), f.world()),
            Status::NotReady
        );
        assert!(owner.snapshot().groups.is_empty());
        f.snapshot.workspace.windows[1].placement.applied_pose = saved;
        f.snapshot.app.as_mut().unwrap().windows[1]
            .sizing
            .applied_physical_size_m = None;
        assert_eq!(
            owner.start(f.ticket(&owner, Action::Collapse, 101), f.world()),
            Status::NotReady
        );
        assert!(owner.snapshot().groups.is_empty());
        f.snapshot.app.as_mut().unwrap().windows[1]
            .sizing
            .applied_physical_size_m = Some(PhysicalSize::default());
        assert_eq!(
            owner.start(f.ticket(&owner, Action::Collapse, 102), f.world()),
            Status::Accepted
        );
    }
    #[test]
    fn expanded_dissolve_then_new_group_rejects_old_ack_even_same_target_and_revision() {
        let mut f = Fixture::new(2);
        let mut owner = Controller::new(f.runtime);
        owner.start(f.ticket(&owner, Action::Collapse, 100), f.world());
        f.advance(&mut owner, 500);
        let first = owner.snapshot().groups[0].clone();
        f.finish(&mut owner);
        owner.start(f.ticket(&owner, Action::Expand, 101), f.world());
        f.advance(&mut owner, 500);
        f.finish(&mut owner);
        assert!(owner.snapshot().groups.is_empty());
        assert!(owner.hidden_targets().is_empty());
        assert!(owner.input_allowed(f.key(1)));
        owner.start(f.ticket(&owner, Action::Collapse, 102), f.world());
        f.advance(&mut owner, 500);
        let next = owner.snapshot().groups[0].clone();
        assert_eq!(first.revision, next.revision);
        assert_ne!(first.id, next.id);
        assert_eq!(owner.applied(f.ack(&first, 0), f.world()), Status::Stale);
        assert!(
            owner.snapshot().groups[0]
                .windows
                .iter()
                .all(|w| !w.geometry_applied)
        );
    }
    #[test]
    fn reverse_uses_current_pose_rejects_old_ack_and_keeps_old_completion_revision() {
        let mut f = Fixture::new(2);
        let mut owner = Controller::new(f.runtime);
        owner.start(f.ticket(&owner, Action::Collapse, 100), f.world());
        f.advance(&mut owner, 250);
        let first = owner.snapshot().groups[0].clone();
        owner.start(f.ticket(&owner, Action::Expand, 101), f.world());
        let next = owner.snapshot().groups[0].clone();
        assert_ne!(first.revision, next.revision);
        assert!(
            first.windows[1]
                .presentation
                .pose
                .near(next.windows[1].presentation.pose)
        );
        assert_eq!(owner.snapshot().completions[0].revision, first.revision);
        assert_eq!(
            owner.snapshot().completions[0].reason,
            Some("GROUP_REVERSED")
        );
        f.advance(&mut owner, 500);
        assert_eq!(owner.applied(f.ack(&first, 0), f.world()), Status::Stale);
        f.finish(&mut owner);
        assert!(owner.snapshot().groups.is_empty());
    }
    #[test]
    fn source_and_theme_ticket_changes_do_not_supersede_an_inflight_request() {
        let mut f = Fixture::new(2);
        let mut owner = Controller::new(f.runtime);
        owner.start(f.ticket(&owner, Action::Collapse, 100), f.world());
        let mut wrong = f.ticket(&owner, Action::Expand, 101);
        wrong.controls_revision += 1;
        assert_eq!(owner.start(wrong, f.world()), Status::Stale);
        wrong = f.ticket(&owner, Action::Expand, 102);
        wrong.expected_group = Some(CommandSeq::new(999).unwrap());
        assert_eq!(owner.start(wrong, f.world()), Status::Stale);
        assert_eq!(
            owner.snapshot().groups[0].source_request.unwrap().get(),
            100
        );
        assert!(owner.snapshot().completions.is_empty());
        f.advance(&mut owner, 500);
        f.finish(&mut owner);
        assert!(owner.snapshot().completions[0].completed);
    }
    #[test]
    fn pending_verified_new_foot_is_gated_before_admission_but_unknown_gui_stays_independent() {
        let mut f = Fixture::new(3);
        let mut owner = Controller::new(f.runtime);
        // A fresh classified member has not yet acquired a successful SDK target.
        let last = f.snapshot.panels.pop().unwrap();
        let render = f.render.targets.pop().unwrap();
        // Initial capture cannot form with a pending member; keep the third unclassified until setup.
        let launch = f
            .transport
            .app_forward
            .as_mut()
            .unwrap()
            .remote
            .as_mut()
            .unwrap()
            .terminal_launches
            .pop()
            .unwrap();
        owner.start(f.ticket(&owner, Action::Collapse, 100), f.world());
        f.advance(&mut owner, 500);
        f.finish(&mut owner);
        f.transport
            .app_forward
            .as_mut()
            .unwrap()
            .remote
            .as_mut()
            .unwrap()
            .terminal_launches
            .push(launch);
        owner.observe(f.world()).unwrap();
        assert!(!owner.input_allowed(render.key));
        let gui = TargetKey {
            window: WindowId::new(99).unwrap(),
            ..render.key
        };
        assert!(owner.input_allowed(gui));
        assert!(!owner.blocks_geometry(gui));
        f.snapshot.panels.push(last);
        f.render.targets.push(render);
        owner.observe(f.world()).unwrap();
        assert_eq!(owner.snapshot().groups[0].windows.len(), 3);
        assert!(
            owner.snapshot().groups[0]
                .windows
                .iter()
                .all(|w| !w.presentation.input_allowed)
        );
        f.advance(&mut owner, 500);
        f.finish(&mut owner);
        assert!(owner.input_allowed(f.key(2)));
        assert!(!owner.input_allowed(f.key(0)));
    }
    #[test]
    fn unrelated_focus_does_not_cancel_group_or_request_its_key_reset() {
        let mut f = Fixture::new(2);
        let mut owner = Controller::new(f.runtime);
        f.snapshot.app.as_mut().unwrap().input.focus_window = Some(WindowId::new(99).unwrap());
        owner.start(f.ticket(&owner, Action::Collapse, 100), f.world());
        assert!(!owner.take_input_reset());
        f.advance(&mut owner, 500);
        f.finish(&mut owner);
        assert_eq!(owner.snapshot().groups[0].phase, Phase::Stable);
        let gui = TargetKey {
            window: WindowId::new(99).unwrap(),
            ..f.key(0)
        };
        assert!(owner.input_allowed(gui));
    }
    #[test]
    fn focused_member_requests_reset_on_start_and_source_loss_is_fail_closed() {
        let mut f = Fixture::new(2);
        let mut owner = Controller::new(f.runtime);
        f.snapshot.app.as_mut().unwrap().input.focus_window = Some(f.key(0).window);
        owner.start(f.ticket(&owner, Action::Collapse, 100), f.world());
        assert!(owner.take_input_reset());
        f.transport
            .app_forward
            .as_mut()
            .unwrap()
            .remote
            .as_mut()
            .unwrap()
            .child
            .as_mut()
            .unwrap()
            .start = "999".into();
        owner.observe(f.world()).unwrap();
        assert!(owner.take_input_reset());
        assert_eq!(owner.snapshot().blocked_session, Some(f.key(0).session));
        assert!(!owner.input_allowed(f.key(0)));
        assert!(!owner.input_allowed(f.key(1)));
        assert_eq!(
            owner.snapshot().groups[0].reason,
            Some("GROUP_SOURCE_CHANGED")
        );
    }
    #[test]
    fn close_uses_real_lifecycle_without_releasing_board_and_last_close_retires_owner() {
        let mut f = Fixture::new(2);
        let mut owner = Controller::new(f.runtime);
        owner.start(f.ticket(&owner, Action::Collapse, 100), f.world());
        f.advance(&mut owner, 500);
        f.finish(&mut owner);
        f.snapshot.app.as_mut().unwrap().windows.remove(0);
        f.snapshot.panels.remove(0);
        f.render.targets.remove(0);
        f.snapshot.window_slots.entries.remove(0);
        f.snapshot.window_slots.occupied -= 1;
        owner.observe(f.world()).unwrap();
        assert_eq!(owner.snapshot().groups[0].windows.len(), 1);
        f.advance(&mut owner, 500);
        f.finish(&mut owner);
        assert!(owner.input_allowed(f.key(0)));
        assert_eq!(f.snapshot.window_slots.occupied, 1);
        f.snapshot.app.as_mut().unwrap().windows.clear();
        f.snapshot.panels.clear();
        f.render.targets.clear();
        owner.observe(f.world()).unwrap();
        assert!(owner.snapshot().groups.is_empty());
        assert_eq!(f.snapshot.window_slots.occupied, 1); // Board lifetime is never owned here.
    }
    #[test]
    fn global_pause_cancels_to_confirmed_layout_and_does_not_consume_restore_deadline() {
        let mut f = Fixture::new(2);
        let mut owner = Controller::new(f.runtime);
        owner.start(f.ticket(&owner, Action::Collapse, 100), f.world());
        f.advance(&mut owner, 250);
        f.snapshot.xr_state = XrState::Idle;
        f.render.paused = true;
        owner.observe(f.world()).unwrap();
        assert_eq!(owner.snapshot().completions[0].reason, Some("GROUP_PAUSED"));
        f.advance(&mut owner, 60000);
        assert_eq!(owner.snapshot().groups[0].phase, Phase::Settling);
        assert!(!owner.input_allowed(f.key(0)));
        f.snapshot.xr_state = XrState::Focused;
        f.render.paused = false;
        owner.observe(f.world()).unwrap();
        f.finish(&mut owner);
        assert!(owner.snapshot().groups.is_empty());
        assert!(owner.input_allowed(f.key(0)));
    }
    #[test]
    fn required_frame_fences_operation_start_without_chasing_continuous_repaint() {
        let mut f = Fixture::new(2);
        let mut owner = Controller::new(f.runtime);
        for window in &mut f.snapshot.app.as_mut().unwrap().windows {
            window.frame = Some(FrameSeq::new(10).unwrap());
        }
        owner.start(f.ticket(&owner, Action::Collapse, 100), f.world());
        f.advance(&mut owner, 500);
        let group = owner.snapshot().groups[0].clone();
        assert_eq!(owner.applied(f.ack(&group, 0), f.world()), Status::NotReady);
        // Native draw catches the captured boundary while the producer has already moved ahead.
        for window in &mut f.snapshot.app.as_mut().unwrap().windows {
            window.frame = Some(FrameSeq::new(100).unwrap());
        }
        for target in &mut f.render.targets {
            target.content_frame = Some(FrameSeq::new(10).unwrap());
        }
        f.finish(&mut owner);
        assert!(owner.input_allowed(f.key(0)));
    }
    #[test]
    fn reduced_motion_still_needs_every_geometry_and_successful_draw() {
        let mut f = Fixture::new(2);
        f.snapshot.theme.reduced_motion = true;
        let mut owner = Controller::new(f.runtime);
        owner.start(f.ticket(&owner, Action::Collapse, 100), f.world());
        assert_eq!(owner.snapshot().groups[0].phase, Phase::Settling);
        assert!(!owner.input_allowed(f.key(0)));
        f.finish(&mut owner);
        assert!(owner.input_allowed(f.key(0)));
    }
    #[test]
    fn scene_rebind_uses_new_group_id_and_confirmed_layout_with_fresh_geometry() {
        let mut f = Fixture::new(2);
        let mut owner = Controller::new(f.runtime);
        owner.start(f.ticket(&owner, Action::Collapse, 100), f.world());
        f.advance(&mut owner, 500);
        f.finish(&mut owner);
        let old = owner.snapshot().groups[0].clone();
        let scene = SceneEpoch::new(100).unwrap();
        f.snapshot.scene = Some(scene);
        for i in 0..2 {
            let key = TargetKey {
                scene,
                generation: SurfaceGeneration::new(100 + i as i64).unwrap(),
                ..f.key(i)
            };
            f.render.targets[i].key = key;
            f.snapshot.panels[i].target = key;
            // Workspace keeps expanded pose, rebound to only this current target.
        }
        let mut workspace = crate::workspace::Controller::load(None);
        workspace.scene(Some(scene));
        workspace.sync(
            f.key(0).session,
            &f.snapshot.app.as_ref().unwrap().windows,
            &f.snapshot.panels,
        );
        f.snapshot.workspace = workspace.snapshot();
        owner.observe(f.world()).unwrap();
        let next = owner.snapshot().groups[0].clone();
        assert_ne!(old.id, next.id);
        assert_eq!(next.layout, Layout::Collapsed);
        assert_eq!(next.phase, Phase::Settling);
        assert!(owner.hidden_targets().is_empty());
        assert!(!owner.input_allowed(f.key(0)));
        assert_eq!(owner.applied(f.ack(&old, 0), f.world()), Status::Stale);
        f.finish(&mut owner);
        assert_eq!(owner.hidden_targets().len(), 1);
        assert!(owner.input_allowed(f.key(0)));
    }
    #[test]
    fn theme_change_requires_new_geometry_and_old_ack_is_rejected() {
        let mut f = Fixture::new(2);
        let mut owner = Controller::new(f.runtime);
        owner.start(f.ticket(&owner, Action::Collapse, 100), f.world());
        f.advance(&mut owner, 500);
        f.finish(&mut owner);
        let old = owner.snapshot().groups[0].clone();
        f.render.theme_revision = 9;
        owner.observe(f.world()).unwrap();
        assert_eq!(owner.snapshot().groups[0].phase, Phase::Settling);
        assert!(!owner.input_allowed(f.key(0)));
        assert_eq!(owner.applied(f.ack(&old, 0), f.world()), Status::Stale);
        f.finish(&mut owner);
        assert!(owner.input_allowed(f.key(0)));
    }
    #[test]
    fn owner_internal_failure_retains_capture_and_input_geometry_gates_until_runtime_restart() {
        let mut f = Fixture::new(2);
        let mut owner = Controller::new(f.runtime);
        owner.start(f.ticket(&owner, Action::Collapse, 100), f.world());
        owner.next_operation = i64::MAX;
        f.snapshot.app.as_mut().unwrap().windows.remove(0);
        assert_eq!(owner.observe(f.world()), Err(Status::Failed));
        assert!(owner.record.is_some());
        assert_eq!(owner.snapshot().error, Some("GROUP_OBSERVATION_FAILED"));
        assert!(!owner.input_allowed(f.key(0)));
        assert!(owner.blocks_geometry(f.key(0)));
        owner.next_operation = 0;
        assert_eq!(owner.observe(f.world()), Err(Status::NotReady));
        assert_eq!(
            owner.start(f.ticket(&owner, Action::Expand, 101), f.world()),
            Status::NotReady
        );
    }
    #[test]
    fn geometry_timeout_restores_once_then_remains_blocked_without_automatic_retry() {
        let mut f = Fixture::new(2);
        let mut owner = Controller::new(f.runtime);
        owner.start(f.ticket(&owner, Action::Collapse, 100), f.world());
        f.advance(&mut owner, 5500);
        assert_eq!(owner.snapshot().groups[0].reason, Some("geometry_timeout"));
        f.advance(&mut owner, 5000);
        assert_eq!(owner.snapshot().groups[0].phase, Phase::Blocked);
        assert!(!owner.input_allowed(f.key(0)));
        let revision = owner.snapshot().groups[0].revision;
        f.advance(&mut owner, 5000);
        assert_eq!(owner.snapshot().groups[0].revision, revision);
        assert_eq!(
            owner.start(f.ticket(&owner, Action::Expand, 101), f.world()),
            Status::NotReady
        );
    }
}
