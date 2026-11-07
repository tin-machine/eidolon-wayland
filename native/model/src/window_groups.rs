//! NX17の純粋な配置契約。分類・Board予約・SDK適用・描画停止は呼出元が別々に証明する。
//! 中間poseをworkspaceへ保存せず、確定したgroup配置と展開元を分離する。
use crate::{
    ids::{CommandSeq, RuntimeHandle, SceneEpoch, SessionId, WindowId},
    resize::PhysicalSize,
    surface::TargetKey,
    window_slots::{CAPACITY, Entry, Phase as SlotPhase},
    workspace::PanelPose,
};
use serde::{Deserialize, Serialize};
use std::time::Duration;

pub const ANIMATION: Duration = Duration::from_millis(500);
const SETTLE_LIMIT: Duration = Duration::from_secs(5);

/// app_id/title/PIDからの推測は受け付けない。runtimeは検証済みlaunch相関からだけ構築する。
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", content = "id", rename_all = "snake_case")]
pub enum Application {
    Foot,
    Catalog(String),
}
impl Application {
    fn valid(&self) -> bool {
        match self {
            Self::Foot => true,
            Self::Catalog(id) => {
                !id.is_empty()
                    && id.len() <= 64
                    && id.bytes().all(|b| {
                        b.is_ascii_lowercase() || b.is_ascii_digit() || b"._-".contains(&b)
                    })
            }
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Scope {
    pub runtime: RuntimeHandle,
    pub session: SessionId,
    pub scene: SceneEpoch,
    pub application: Application,
}
#[derive(Clone, Debug)]
pub struct Member {
    pub entry: Entry,
    pub target: TargetKey,
    pub application: Application,
    pub pose: PanelPose,
    pub physical_size: PhysicalSize,
    pub locked: bool,
    pub operation_active: bool,
}
impl Member {
    fn valid(&self, scope: &Scope) -> bool {
        self.entry.lease.runtime() == scope.runtime
            && self.entry.lease.session() == scope.session
            && self.target.session == scope.session
            && self.target.scene == scope.scene
            && self.entry.state
                == (SlotPhase::Bound {
                    window: self.target.window,
                })
            && self.application == scope.application
            && self.pose.valid()
            && self.physical_size.valid()
            && !self.locked
            && !self.operation_active
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Action {
    Collapse,
    Expand,
    NextFront,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Layout {
    Expanded,
    Collapsed,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Stable,
    Animating,
    Settling,
    Blocked,
    Empty,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Error {
    InvalidMember,
    Busy,
    Stale,
    Full,
    Bounds,
    TimeReversed,
    IdExhausted,
}
#[derive(Clone, Debug, Serialize)]
pub struct Presentation {
    pub target: TargetKey,
    pub pose: PanelPose,
    pub physical_size: PhysicalSize,
    pub front: bool,
    pub input_allowed: bool,
    /// 背面SDK quadの既存画像は束の端として残せるが、新presentとring clockは止める。
    pub present: bool,
    pub clock_running: bool,
}
struct Destination {
    layout: Layout,
    front: WindowId,
    anchor: PanelPose,
}
#[derive(Clone)]
struct Transition {
    request: Option<CommandSeq>,
    revision: i64,
    goal: Layout,
    front: WindowId,
    anchor: PanelPose,
    starts: Vec<(WindowId, PanelPose)>,
    goals: Vec<(WindowId, PanelPose)>,
    started: Duration,
    duration: Duration,
    acknowledged: Vec<WindowId>,
}

#[derive(Clone)]
pub struct Group {
    scope: Scope,
    members: Vec<Member>, // poseは常に展開元。sample/tickでは変更しない。
    stable: Layout,
    front: WindowId,
    anchor: PanelPose,
    transition: Option<Transition>,
    last_request: i64,
    last_now: Duration,
    last_cancel: Option<&'static str>,
    revision: i64,
    blocked: bool,
}
impl Group {
    pub fn new(
        scope: Scope,
        mut members: Vec<Member>,
        front: WindowId,
        now: Duration,
    ) -> Result<Self, Error> {
        if !scope.application.valid()
            || members.is_empty()
            || members.len() > CAPACITY
            || members.iter().any(|m| !m.valid(&scope))
        {
            return Err(Error::InvalidMember);
        }
        for (i, member) in members.iter().enumerate() {
            if members[..i].iter().any(|old| {
                old.target.window == member.target.window
                    || old.entry.lease.position() == member.entry.lease.position()
            }) {
                return Err(Error::InvalidMember);
            }
        }
        members.sort_by_key(|m| m.entry.lease.position().index());
        let anchor = members
            .iter()
            .find(|m| m.target.window == front)
            .ok_or(Error::Stale)?
            .pose;
        Ok(Self {
            scope,
            members,
            stable: Layout::Expanded,
            front,
            anchor,
            transition: None,
            last_request: 0,
            last_now: now,
            last_cancel: None,
            revision: 0,
            blocked: false,
        })
    }
    /// 再構成したmemberへ最後の確定layoutを渡す。callerは別group_idを発行し、旧ACKを拒否する。
    pub fn restore(
        scope: Scope,
        members: Vec<Member>,
        front: WindowId,
        anchor: PanelPose,
        layout: Layout,
        now: Duration,
    ) -> Result<Self, Error> {
        let mut group = Self::new(scope, members, front, now)?;
        if !anchor.valid() {
            return Err(Error::Bounds);
        }
        group.stable = layout;
        group.anchor = anchor;
        group.poses(layout, front, anchor)?;
        group.cancel("scope_restored")?;
        Ok(group)
    }
    pub fn current_revision(&self) -> i64 {
        self.revision
    }
    pub fn current_layout(&self) -> Layout {
        self.transition.as_ref().map_or(self.stable, |t| t.goal)
    }
    pub fn current_front(&self) -> WindowId {
        self.transition.as_ref().map_or(self.front, |t| t.front)
    }
    pub fn confirmed_front(&self) -> WindowId {
        self.front
    }
    pub fn confirmed_anchor(&self) -> PanelPose {
        self.anchor
    }
    pub fn scope(&self) -> &Scope {
        &self.scope
    }
    pub fn saved(&self) -> Vec<(TargetKey, PanelPose, PhysicalSize)> {
        self.members
            .iter()
            .map(|m| (m.target, m.pose, m.physical_size))
            .collect()
    }
    pub fn phase(&self, now: Duration) -> Phase {
        if self.members.is_empty() {
            Phase::Empty
        } else if self.blocked {
            Phase::Blocked
        } else if let Some(t) = &self.transition {
            if now.saturating_sub(t.started) < t.duration {
                Phase::Animating
            } else {
                Phase::Settling
            }
        } else {
            Phase::Stable
        }
    }
    pub fn stable_layout(&self) -> Layout {
        self.stable
    }
    pub fn request(&self) -> Option<CommandSeq> {
        self.transition.as_ref().and_then(|t| t.request)
    }
    pub fn revision(&self) -> Option<i64> {
        self.transition.as_ref().map(|t| t.revision)
    }
    pub fn cancel_reason(&self) -> Option<&'static str> {
        self.last_cancel
    }
    fn time(&mut self, now: Duration) -> Result<(), Error> {
        if now < self.last_now {
            return Err(Error::TimeReversed);
        }
        self.last_now = now;
        Ok(())
    }
    fn target(&self, target: TargetKey) -> Result<&Member, Error> {
        self.members
            .iter()
            .find(|m| m.target == target)
            .ok_or(Error::Stale)
    }
    fn poses(
        &self,
        layout: Layout,
        front: WindowId,
        anchor: PanelPose,
    ) -> Result<Vec<(WindowId, PanelPose)>, Error> {
        let mut rank = 0;
        self.members
            .iter()
            .map(|member| {
                let pose = if layout == Layout::Expanded {
                    member.pose
                } else if member.target.window == front {
                    anchor
                } else {
                    rank += 1;
                    // 全grid段で内側へずらす。範囲外を軸別clampして保存配置を壊さない。
                    let mut pose = anchor;
                    let offset = rotate(
                        anchor.rotation_xyzw,
                        [
                            (if anchor.position_m[0] > 0. {
                                -0.045
                            } else {
                                0.045
                            }) * rank as f32,
                            (if anchor.position_m[1] > 2. {
                                -0.035
                            } else {
                                0.035
                            }) * rank as f32,
                            0.025 * rank as f32,
                        ],
                    );
                    pose.position_m = std::array::from_fn(|i| anchor.position_m[i] + offset[i]);
                    pose
                };
                if pose.valid() {
                    Ok((member.target.window, pose))
                } else {
                    Err(Error::Bounds)
                }
            })
            .collect()
    }
    fn observed_poses(&self, now: Duration) -> Result<Vec<(WindowId, PanelPose)>, Error> {
        if let Some(t) = &self.transition {
            let u = if t.duration.is_zero() {
                1.
            } else {
                (now.saturating_sub(t.started).as_secs_f64() / t.duration.as_secs_f64())
                    .clamp(0., 1.)
            };
            let eased = (u * u * (3. - 2. * u)) as f32;
            t.starts
                .iter()
                .zip(&t.goals)
                .map(|((window, start), (other, end))| {
                    if window != other {
                        return Err(Error::Stale);
                    }
                    interpolate(*start, *end, eased).map(|pose| (*window, pose))
                })
                .collect()
        } else {
            self.poses(self.stable, self.front, self.anchor)
        }
    }
    fn start(
        &mut self,
        request: CommandSeq,
        destination: Destination,
        starts: Vec<(WindowId, PanelPose)>,
        now: Duration,
        reduced: bool,
    ) -> Result<(), Error> {
        let Destination {
            layout: goal,
            front,
            anchor,
        } = destination;
        if self.blocked {
            return Err(Error::Busy);
        }
        if request.get() <= self.last_request {
            return Err(Error::Stale);
        }
        let revision = self.revision.checked_add(1).ok_or(Error::IdExhausted)?;
        let goals = self.poses(goal, front, anchor)?;
        if starts.len() != goals.len() || starts.iter().zip(&goals).any(|(a, b)| a.0 != b.0) {
            return Err(Error::Stale);
        }
        self.last_request = request.get();
        self.last_cancel = None;
        self.revision = revision;
        self.transition = Some(Transition {
            request: Some(request),
            revision,
            goal,
            front,
            anchor,
            starts,
            goals,
            started: now,
            duration: if reduced { Duration::ZERO } else { ANIMATION },
            acknowledged: Vec::new(),
        });
        Ok(())
    }
    /// collapse/expand/reverseは同じ開始時刻。反転は現表示poseからで、旧開始位置へ戻らない。
    pub fn arrange(
        &mut self,
        request: CommandSeq,
        goal: Layout,
        front: TargetKey,
        now: Duration,
        reduced: bool,
        external_operation_active: bool,
    ) -> Result<(), Error> {
        self.time(now)?;
        self.target(front)?;
        if external_operation_active || self.members.iter().any(|m| m.locked || m.operation_active)
        {
            return Err(Error::Busy);
        }
        let starts = self.observed_poses(now)?;
        let anchor = if self
            .transition
            .as_ref()
            .is_some_and(|t| t.goal == Layout::Collapsed)
            || self.stable == Layout::Collapsed
        {
            self.transition.as_ref().map_or(self.anchor, |t| t.anchor)
        } else {
            self.target(front)?.pose
        };
        self.start(
            request,
            Destination {
                layout: goal,
                front: front.window,
                anchor,
            },
            starts,
            now,
            reduced,
        )
    }
    pub fn sample(&mut self, now: Duration) -> Result<Vec<Presentation>, Error> {
        self.time(now)?;
        if self
            .transition
            .as_ref()
            .is_some_and(|t| now.saturating_sub(t.started) >= t.duration + SETTLE_LIMIT)
        {
            if self
                .transition
                .as_ref()
                .is_some_and(|t| t.request.is_none())
            {
                self.blocked = true;
                self.transition = None;
                self.last_cancel = Some("restore_timeout");
            } else {
                self.cancel("geometry_timeout")?;
            }
        }
        let poses = self.observed_poses(now)?;
        let moving = self.transition.is_some() || self.blocked;
        let layout = self.transition.as_ref().map_or(self.stable, |t| t.goal);
        let front = self.transition.as_ref().map_or(self.front, |t| t.front);
        Ok(self
            .members
            .iter()
            .zip(poses)
            .map(|(member, (_, pose))| {
                let active = layout == Layout::Expanded || member.target.window == front;
                Presentation {
                    target: member.target,
                    pose,
                    physical_size: member.physical_size,
                    front: member.target.window == front,
                    input_allowed: !moving && active,
                    present: moving || active,
                    clock_running: moving || active,
                }
            })
            .collect())
    }
    /// 最終位置の実SDK geometryと最新frameを、同request/full targetで確認してから入力を再開する。
    pub fn applied(
        &mut self,
        revision: i64,
        target: TargetKey,
        pose: PanelPose,
        fresh_frame: bool,
        now: Duration,
    ) -> Result<bool, Error> {
        self.time(now)?;
        self.target(target)?;
        let t = self.transition.as_mut().ok_or(Error::Stale)?;
        if t.revision != revision {
            return Err(Error::Stale);
        }
        if now.saturating_sub(t.started) >= t.duration + SETTLE_LIMIT {
            if t.request.is_none() {
                self.blocked = true;
                self.transition = None;
                self.last_cancel = Some("restore_timeout");
            } else {
                self.cancel("geometry_timeout")?;
            }
            return Ok(false);
        }
        if now.saturating_sub(t.started) < t.duration
            || !fresh_frame
            || !t
                .goals
                .iter()
                .any(|(window, goal)| *window == target.window && goal.near(pose))
        {
            return Ok(false);
        }
        if !t.acknowledged.contains(&target.window) {
            t.acknowledged.push(target.window);
        }
        if t.acknowledged.len() != self.members.len() {
            return Ok(false);
        }
        let t = self.transition.take().ok_or(Error::Stale)?;
        self.stable = t.goal;
        self.front = t.front;
        self.anchor = t.anchor;
        Ok(true)
    }
    /// 呼出元はwindow closeとBoard drainを別途行う。ここではleaseを解放しない。
    pub fn remove(
        &mut self,
        request: CommandSeq,
        target: TargetKey,
        now: Duration,
        reduced: bool,
    ) -> Result<(), Error> {
        self.time(now)?;
        self.target(target)?;
        if request.get() <= self.last_request {
            return Err(Error::Stale);
        }
        let before = self.clone();
        let mut starts = self.observed_poses(now)?;
        let goal = self.transition.as_ref().map_or(self.stable, |t| t.goal);
        let prior_front = self.transition.as_ref().map_or(self.front, |t| t.front);
        let anchor = self.transition.as_ref().map_or(self.anchor, |t| t.anchor);
        self.members.retain(|m| m.target != target);
        starts.retain(|(w, _)| *w != target.window);
        if self.members.is_empty() {
            self.last_request = request.get();
            self.transition = None;
            return Ok(());
        }
        if self.front == target.window {
            self.front = self.members[0].target.window;
        }
        if self.blocked {
            self.last_request = request.get();
            return Ok(());
        }
        let front = if prior_front == target.window {
            self.members[0].target.window
        } else {
            prior_front
        };
        if let Err(error) = self.start(
            request,
            Destination {
                layout: goal,
                front,
                anchor,
            },
            starts,
            now,
            reduced,
        ) {
            *self = before;
            return Err(error);
        }
        Ok(())
    }
    /// 元の展開slotは先にBoardで確保し、boundした一窓だけを渡す。既存窓を追い出さない。
    pub fn admit(
        &mut self,
        request: CommandSeq,
        member: Member,
        now: Duration,
        reduced: bool,
    ) -> Result<(), Error> {
        self.time(now)?;
        if self.members.is_empty() {
            return Err(Error::Stale);
        }
        if self.blocked {
            return Err(Error::Busy);
        }
        if self.members.len() >= CAPACITY {
            return Err(Error::Full);
        }
        if !member.valid(&self.scope)
            || self.members.iter().any(|m| {
                m.target.window == member.target.window
                    || m.entry.lease.position() == member.entry.lease.position()
            })
        {
            return Err(Error::InvalidMember);
        }
        if request.get() <= self.last_request {
            return Err(Error::Stale);
        }
        let current = self.observed_poses(now)?;
        let goal = self.transition.as_ref().map_or(self.stable, |t| t.goal);
        let anchor = self.transition.as_ref().map_or(self.anchor, |t| t.anchor);
        let front = if goal == Layout::Collapsed {
            member.target.window
        } else {
            self.front
        };
        let mut next = self.members.clone();
        next.push(member.clone());
        next.sort_by_key(|m| m.entry.lease.position().index());
        let starts = next
            .iter()
            .map(|m| {
                (
                    m.target.window,
                    current
                        .iter()
                        .find(|(w, _)| *w == m.target.window)
                        .map_or(m.pose, |(_, p)| *p),
                )
            })
            .collect();
        let old = std::mem::replace(&mut self.members, next);
        if let Err(error) = self.start(
            request,
            Destination {
                layout: goal,
                front,
                anchor,
            },
            starts,
            now,
            reduced,
        ) {
            self.members = old;
            return Err(error);
        }
        Ok(())
    }
    /// 取消にも新revisionを使い、確定配置への実geometry復帰を確認するまで入力を止める。
    pub fn cancel(&mut self, reason: &'static str) -> Result<(), Error> {
        if self.members.is_empty() {
            return Err(Error::Stale);
        }
        if self.blocked {
            return Err(Error::Busy);
        }
        let revision = self.revision.checked_add(1).ok_or(Error::IdExhausted)?;
        let goals = self.poses(self.stable, self.front, self.anchor)?;
        self.revision = revision;
        self.last_cancel = Some(reason);
        self.transition = Some(Transition {
            request: None,
            revision,
            goal: self.stable,
            front: self.front,
            anchor: self.anchor,
            starts: goals.clone(),
            goals,
            started: self.last_now,
            duration: Duration::ZERO,
            acknowledged: Vec::new(),
        });
        Ok(())
    }
}

fn interpolate(a: PanelPose, b: PanelPose, t: f32) -> Result<PanelPose, Error> {
    let mut q = b.rotation_xyzw;
    let mut dot = a
        .rotation_xyzw
        .iter()
        .zip(q)
        .map(|(x, y)| *x * y)
        .sum::<f32>();
    if dot < 0. {
        q = q.map(|x| -x);
        dot = -dot;
    }
    let rotation = if dot > 0.9995 {
        std::array::from_fn(|i| a.rotation_xyzw[i] + t * (q[i] - a.rotation_xyzw[i]))
    } else {
        let theta = dot.clamp(-1., 1.).acos();
        let sine = theta.sin();
        std::array::from_fn(|i| {
            (((1. - t) * theta).sin() * a.rotation_xyzw[i] + (t * theta).sin() * q[i]) / sine
        })
    };
    let norm = rotation.iter().map(|x| x * x).sum::<f32>().sqrt();
    let pose = PanelPose {
        position_m: std::array::from_fn(|i| {
            a.position_m[i] + t * (b.position_m[i] - a.position_m[i])
        }),
        rotation_xyzw: rotation.map(|x| x / norm),
    };
    if pose.valid() {
        Ok(pose)
    } else {
        Err(Error::Bounds)
    }
}
fn multiply(a: [f32; 4], b: [f32; 4]) -> [f32; 4] {
    let [x, y, z, w] = a;
    let [u, v, s, t] = b;
    [
        w * u + x * t + y * s - z * v,
        w * v - x * s + y * t + z * u,
        w * s + x * v - y * u + z * t,
        w * t - x * u - y * v - z * s,
    ]
}
fn rotate(q: [f32; 4], p: [f32; 3]) -> [f32; 3] {
    let r = multiply(
        multiply(q, [p[0], p[1], p[2], 0.]),
        [-q[0], -q[1], -q[2], q[3]],
    );
    [r[0], r[1], r[2]]
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{ids::SurfaceGeneration, window_slots::Board};
    fn request(n: i64) -> CommandSeq {
        CommandSeq::new(n).unwrap()
    }
    fn ms(n: u64) -> Duration {
        Duration::from_millis(n)
    }
    fn fixture(n: usize) -> (Board, Scope, Vec<Member>) {
        let runtime = RuntimeHandle::new(4).unwrap();
        let session = SessionId::new(5).unwrap();
        let scene = SceneEpoch::new(6).unwrap();
        let scope = Scope {
            runtime,
            session,
            scene,
            application: Application::Foot,
        };
        let mut board = Board::new(runtime, session);
        let mut members = vec![];
        for i in 0..n {
            let window = WindowId::new(i as i64 + 1).unwrap();
            let lease = board.reserve(request(10 + i as i64)).unwrap().lease();
            board.enqueued(lease).unwrap();
            board.bind(lease, window).unwrap();
            members.push(Member {
                entry: board
                    .snapshot()
                    .into_iter()
                    .find(|e| e.lease == lease)
                    .unwrap(),
                target: TargetKey {
                    session,
                    window,
                    scene,
                    generation: SurfaceGeneration::new(20 + i as i64).unwrap(),
                },
                application: Application::Foot,
                pose: PanelPose::at_slot(i),
                physical_size: PhysicalSize::default(),
                locked: false,
                operation_active: false,
            });
        }
        (board, scope, members)
    }
    fn group(n: usize) -> (Board, Group) {
        let (b, s, m) = fixture(n);
        let front = m[0].target.window;
        (b, Group::new(s, m, front, ms(0)).unwrap())
    }
    fn settle(g: &mut Group, now: u64) {
        let revision = g.revision().unwrap();
        let rows = g.sample(ms(now)).unwrap();
        for (i, row) in rows.iter().enumerate() {
            assert_eq!(
                g.applied(revision, row.target, row.pose, true, ms(now))
                    .unwrap(),
                i + 1 == rows.len()
            );
        }
        assert_eq!(g.phase(ms(now)), Phase::Stable);
    }
    #[test]
    fn default_is_expanded_and_all_bound_slots_and_originals_stay_owned() {
        let (board, mut g) = group(9);
        let saved = g.saved();
        let before = board.snapshot();
        let rows = g.sample(ms(0)).unwrap();
        assert_eq!(rows.len(), 9);
        assert!(
            rows.iter()
                .all(|r| r.input_allowed && r.present && r.clock_running)
        );
        g.arrange(
            request(100),
            Layout::Collapsed,
            rows[0].target,
            ms(0),
            false,
            false,
        )
        .unwrap();
        assert_eq!(g.saved(), saved);
        assert_eq!(board.snapshot(), before);
        assert_eq!(board.occupied(), 9);
    }
    #[test]
    fn animation_uses_one_common_500ms_clock_and_geometry_ack_is_not_time_completion() {
        let (_, mut g) = group(3);
        let target = g.members[0].target;
        let original = g.saved();
        g.arrange(
            request(100),
            Layout::Collapsed,
            target,
            ms(100),
            false,
            false,
        )
        .unwrap();
        let revision = g.revision().unwrap();
        let begin = g.sample(ms(100)).unwrap();
        let halfway = g.sample(ms(350)).unwrap();
        let end = g.sample(ms(600)).unwrap();
        assert_eq!(begin[1].pose, original[1].1);
        assert!(halfway.iter().all(|r| !r.input_allowed));
        for i in 1..3 {
            for axis in 0..3 {
                assert!(
                    (halfway[i].pose.position_m[axis]
                        - (begin[i].pose.position_m[axis] + end[i].pose.position_m[axis]) * 0.5)
                        .abs()
                        < 0.0001
                );
            }
        }
        assert_eq!(g.phase(ms(600)), Phase::Settling);
        assert!(end.iter().all(|r| !r.input_allowed));
        assert!(
            !g.applied(revision, end[0].target, end[0].pose, false, ms(600))
                .unwrap()
        );
        settle(&mut g, 600);
        let rows = g.sample(ms(601)).unwrap();
        assert_eq!(rows.iter().filter(|r| r.input_allowed).count(), 1);
        assert_eq!(
            rows.iter().filter(|r| r.present || r.clock_running).count(),
            1
        );
    }
    #[test]
    fn reverse_starts_at_current_visible_pose_and_old_completion_never_wins() {
        let (_, mut g) = group(3);
        let key = g.members[0].target;
        g.arrange(request(100), Layout::Collapsed, key, ms(0), false, false)
            .unwrap();
        let old = g.revision().unwrap();
        let current = g.sample(ms(200)).unwrap();
        g.arrange(request(101), Layout::Expanded, key, ms(200), false, false)
            .unwrap();
        let fresh = g.sample(ms(200)).unwrap();
        assert_eq!(
            current.iter().map(|r| r.pose).collect::<Vec<_>>(),
            fresh.iter().map(|r| r.pose).collect::<Vec<_>>()
        );
        assert_eq!(
            g.applied(old, key, key_pose(), true, ms(700)),
            Err(Error::Stale)
        );
        settle(&mut g, 700);
        assert_eq!(g.stable_layout(), Layout::Expanded);
        assert!(g.sample(ms(701)).unwrap().iter().all(|r| r.input_allowed));
    }
    fn key_pose() -> PanelPose {
        PanelPose::at_slot(0)
    }
    #[test]
    fn shortest_quaternion_path_handles_antipodal_and_normalizes_midpoints() {
        let a = key_pose();
        let b = PanelPose {
            rotation_xyzw: [0., 0., 0., -1.],
            ..a
        };
        assert!(interpolate(a, b, 0.5).unwrap().near(a));
        let quarter = PanelPose {
            rotation_xyzw: [
                0.,
                std::f32::consts::FRAC_1_SQRT_2,
                0.,
                std::f32::consts::FRAC_1_SQRT_2,
            ],
            ..a
        };
        let middle = interpolate(a, quarter, 0.5).unwrap();
        assert!(middle.valid());
        assert!((middle.rotation_xyzw[1] - (std::f32::consts::FRAC_PI_8).sin()).abs() < 0.0001);
    }
    #[test]
    fn reduced_motion_skips_interpolation_but_never_skips_fresh_geometry_and_frame_gates() {
        let (_, mut g) = group(2);
        let key = g.members[0].target;
        g.arrange(request(100), Layout::Collapsed, key, ms(0), true, false)
            .unwrap();
        assert_eq!(g.phase(ms(0)), Phase::Settling);
        assert!(g.sample(ms(0)).unwrap().iter().all(|r| !r.input_allowed));
        settle(&mut g, 0);
        assert_eq!(g.stable_layout(), Layout::Collapsed);
    }
    #[test]
    fn front_switch_is_one_target_only_and_never_enables_hidden_controls_or_clock() {
        let (_, mut g) = group(3);
        let key = g.members[0].target;
        g.arrange(request(100), Layout::Collapsed, key, ms(0), true, false)
            .unwrap();
        settle(&mut g, 0);
        let next = g.members[2].target;
        g.arrange(request(101), Layout::Collapsed, next, ms(1), true, false)
            .unwrap();
        settle(&mut g, 1);
        let rows = g.sample(ms(2)).unwrap();
        assert!(
            rows.iter()
                .filter(|r| r.input_allowed || r.present || r.clock_running)
                .all(|r| r.target == next)
        );
        assert_eq!(rows.iter().filter(|r| r.front).count(), 1);
    }
    #[test]
    fn expand_restores_manual_positions_sizes_and_order_after_front_changes() {
        let (_, mut g) = group(3);
        g.members[1].pose.position_m[1] = 1.234;
        g.members[2].physical_size = PhysicalSize {
            width_m: 1.5,
            height_m: 0.75,
        };
        let saved = g.saved();
        let key = g.members[1].target;
        g.arrange(request(100), Layout::Collapsed, key, ms(0), true, false)
            .unwrap();
        settle(&mut g, 0);
        g.arrange(
            request(101),
            Layout::Collapsed,
            g.members[2].target,
            ms(1),
            true,
            false,
        )
        .unwrap();
        settle(&mut g, 1);
        g.arrange(request(102), Layout::Expanded, key, ms(2), false, false)
            .unwrap();
        settle(&mut g, 502);
        let rows = g.sample(ms(503)).unwrap();
        assert_eq!(g.saved(), saved);
        for (row, (_, pose, size)) in rows.iter().zip(saved) {
            assert_eq!(row.pose, pose);
            assert_eq!(row.physical_size, size);
        }
    }
    #[test]
    fn close_only_removes_exact_member_and_keeps_survivor_originals_and_board_reservation() {
        let (board, mut g) = group(3);
        let removed = g.members[0].target;
        let saved = g.saved()[1..].to_vec();
        let before = board.snapshot();
        g.arrange(
            request(100),
            Layout::Collapsed,
            removed,
            ms(0),
            false,
            false,
        )
        .unwrap();
        let old = g.revision().unwrap();
        g.remove(request(101), removed, ms(200), false).unwrap();
        assert_eq!(g.saved(), saved);
        assert_eq!(board.snapshot(), before);
        assert_eq!(
            g.applied(old, removed, key_pose(), true, ms(700)),
            Err(Error::Stale)
        );
        settle(&mut g, 700);
        assert_eq!(
            g.sample(ms(701))
                .unwrap()
                .iter()
                .filter(|r| r.input_allowed)
                .count(),
            1
        );
    }
    #[test]
    fn admission_requires_already_bound_free_slot_and_new_front_keeps_its_expansion_destination() {
        let (board, scope, members) = fixture(3);
        let mut g = Group::new(
            scope,
            members[..2].to_vec(),
            members[0].target.window,
            ms(0),
        )
        .unwrap();
        g.arrange(
            request(100),
            Layout::Collapsed,
            members[0].target,
            ms(0),
            true,
            false,
        )
        .unwrap();
        settle(&mut g, 0);
        g.admit(request(101), members[2].clone(), ms(1), false)
            .unwrap();
        settle(&mut g, 501);
        let rows = g.sample(ms(502)).unwrap();
        assert_eq!(
            rows.iter().find(|r| r.input_allowed).unwrap().target,
            members[2].target
        );
        assert_eq!(g.saved()[2].1, members[2].pose);
        assert_eq!(board.occupied(), 3);
    }
    #[test]
    fn tenth_member_duplicate_slot_unbound_and_other_scope_are_rejected_without_eviction() {
        let (_, mut full) = group(9);
        let old = full.saved();
        let extra = full.members[0].clone();
        assert_eq!(
            full.admit(request(100), extra, ms(0), true),
            Err(Error::Full)
        );
        assert_eq!(full.saved(), old);
        let (_, scope, members) = fixture(3);
        let mut g = Group::new(
            scope.clone(),
            members[..2].to_vec(),
            members[0].target.window,
            ms(0),
        )
        .unwrap();
        let mut unbound = members[2].clone();
        unbound.entry.state = SlotPhase::Reserved { enqueued: true };
        let mut other_session = members[2].clone();
        other_session.target.session = SessionId::new(99).unwrap();
        let mut duplicate_slot = members[2].clone();
        duplicate_slot.entry = members[0].entry;
        for member in [members[0].clone(), unbound, other_session, duplicate_slot] {
            assert_eq!(
                g.admit(request(100), member, ms(0), true),
                Err(Error::InvalidMember)
            );
        }
        let mut member = members[2].clone();
        member.target.scene = SceneEpoch::new(99).unwrap();
        assert_eq!(
            g.admit(request(100), member, ms(0), true),
            Err(Error::InvalidMember)
        );
        member = members[2].clone();
        member.application = Application::Catalog("other".into());
        assert_eq!(
            g.admit(request(100), member, ms(0), true),
            Err(Error::InvalidMember)
        );
    }
    #[test]
    fn locked_or_captured_members_and_existing_drag_exclude_group_start() {
        for locked in [true, false] {
            let (_, s, mut m) = fixture(2);
            m[0].locked = locked;
            m[0].operation_active = !locked;
            assert!(matches!(
                Group::new(s, m, WindowId::new(1).unwrap(), ms(0)),
                Err(Error::InvalidMember)
            ));
        }
        let (_, mut g) = group(2);
        assert_eq!(
            g.arrange(
                request(100),
                Layout::Collapsed,
                g.members[0].target,
                ms(0),
                false,
                true
            ),
            Err(Error::Busy)
        );
        assert_eq!(g.phase(ms(0)), Phase::Stable);
    }
    #[test]
    fn pause_surface_scene_cancel_uses_new_revision_and_requires_restore_geometry_before_input() {
        for reason in ["pause", "scene_changed", "surface_changed", "disconnect"] {
            let (_, mut g) = group(2);
            let key = g.members[0].target;
            g.arrange(request(100), Layout::Collapsed, key, ms(0), false, false)
                .unwrap();
            let old = g.revision().unwrap();
            g.sample(ms(200)).unwrap();
            g.cancel(reason).unwrap();
            assert_eq!(g.cancel_reason(), Some(reason));
            assert_eq!(g.request(), None);
            let revision = g.revision().unwrap();
            assert_ne!(old, revision);
            assert_eq!(
                g.applied(old, key, key_pose(), true, ms(200)),
                Err(Error::Stale)
            );
            assert!(g.sample(ms(200)).unwrap().iter().all(|r| !r.input_allowed));
            settle(&mut g, 200);
            assert_eq!(g.stable_layout(), Layout::Expanded);
        }
    }
    #[test]
    fn geometry_timeout_restores_once_then_stays_blocked_without_automatic_retry() {
        let (_, mut g) = group(2);
        let key = g.members[0].target;
        g.arrange(request(100), Layout::Collapsed, key, ms(0), false, false)
            .unwrap();
        assert!(g.sample(ms(5500)).unwrap().iter().all(|r| !r.input_allowed));
        let revision = g.revision().unwrap();
        assert_eq!(g.cancel_reason(), Some("geometry_timeout"));
        assert!(
            g.sample(ms(10500))
                .unwrap()
                .iter()
                .all(|r| !r.input_allowed)
        );
        assert_eq!(g.phase(ms(10500)), Phase::Blocked);
        assert_eq!(g.cancel_reason(), Some("restore_timeout"));
        assert_eq!(
            g.arrange(request(101), Layout::Collapsed, key, ms(10501), true, false),
            Err(Error::Busy)
        );
        assert_eq!(
            g.applied(revision, key, key_pose(), true, ms(10501)),
            Err(Error::Stale)
        );
    }
    #[test]
    fn changed_target_wrong_pose_old_frame_and_old_request_cannot_finish_new_operation() {
        let (_, mut g) = group(2);
        let key = g.members[0].target;
        g.arrange(request(100), Layout::Collapsed, key, ms(0), false, false)
            .unwrap();
        let revision = g.revision().unwrap();
        let changed = TargetKey {
            generation: SurfaceGeneration::new(99).unwrap(),
            ..key
        };
        assert_eq!(
            g.applied(revision, changed, key_pose(), true, ms(500)),
            Err(Error::Stale)
        );
        let rows = g.sample(ms(500)).unwrap();
        let mut wrong = rows[1].pose;
        wrong.position_m[0] += 0.1;
        assert!(
            !g.applied(revision, rows[1].target, wrong, true, ms(500))
                .unwrap()
        );
        assert!(
            !g.applied(revision, rows[1].target, rows[1].pose, false, ms(500))
                .unwrap()
        );
        assert_eq!(
            g.arrange(request(100), Layout::Expanded, key, ms(500), false, false),
            Err(Error::Stale)
        );
        settle(&mut g, 500);
    }
    #[test]
    fn invalid_pose_size_or_duplicate_window_never_partially_changes_the_group() {
        let (_, s, m) = fixture(2);
        for change in [0, 1, 2] {
            let mut rows = m.clone();
            match change {
                0 => rows[1].pose.position_m[0] = f32::NAN,
                1 => rows[1].physical_size.width_m = 0.,
                _ => rows[1].target = rows[0].target,
            };
            assert!(Group::new(s.clone(), rows, m[0].target.window, ms(0)).is_err());
        }
        let (_, mut g) = group(2);
        let before = g.saved();
        g.revision = i64::MAX;
        assert_eq!(
            g.remove(request(100), g.members[0].target, ms(1), true),
            Err(Error::IdExhausted)
        );
        assert_eq!(g.saved(), before);
    }
    #[test]
    fn recaptured_scene_restores_only_confirmed_layout_and_waits_for_new_target_geometry() {
        let (_, mut old) = group(2);
        let front = old.members[0].target;
        old.arrange(request(100), Layout::Collapsed, front, ms(0), true, false)
            .unwrap();
        settle(&mut old, 0);
        old.arrange(request(101), Layout::Expanded, front, ms(1), false, false)
            .unwrap();
        old.sample(ms(200)).unwrap();
        let mut members = old.members.clone();
        let mut scope = old.scope.clone();
        scope.scene = SceneEpoch::new(99).unwrap();
        for member in &mut members {
            member.target.scene = scope.scene;
            member.target.generation =
                SurfaceGeneration::new(member.target.generation.get() + 100).unwrap();
        }
        let mut restored = Group::restore(
            scope,
            members,
            old.confirmed_front(),
            old.confirmed_anchor(),
            old.stable_layout(),
            ms(0),
        )
        .unwrap();
        assert_eq!(restored.current_layout(), Layout::Collapsed);
        assert!(
            restored
                .sample(ms(0))
                .unwrap()
                .iter()
                .all(|row| !row.input_allowed)
        );
        let revision = restored.revision().unwrap();
        assert_eq!(
            restored.applied(revision, front, key_pose(), true, ms(0)),
            Err(Error::Stale)
        );
        settle(&mut restored, 0);
        assert_eq!(restored.stable_layout(), Layout::Collapsed);
    }
    #[test]
    fn empty_group_is_terminal_and_cannot_admit_into_a_retired_front_or_scope() {
        let (_, scope, members) = fixture(2);
        let mut group = Group::new(
            scope,
            members[..1].to_vec(),
            members[0].target.window,
            ms(0),
        )
        .unwrap();
        group
            .remove(request(100), members[0].target, ms(1), true)
            .unwrap();
        assert_eq!(group.phase(ms(1)), Phase::Empty);
        assert!(group.sample(ms(1)).unwrap().is_empty());
        assert_eq!(
            group.admit(request(101), members[1].clone(), ms(2), true),
            Err(Error::Stale)
        );
        assert_eq!(group.cancel("pause"), Err(Error::Stale));
    }
    #[test]
    fn skipped_frames_are_time_based_and_clock_regression_is_rejected() {
        let (_, mut g) = group(2);
        let key = g.members[0].target;
        g.arrange(request(100), Layout::Collapsed, key, ms(0), false, false)
            .unwrap();
        let rows = g.sample(ms(700)).unwrap();
        assert_eq!(g.phase(ms(700)), Phase::Settling);
        assert_eq!(g.sample(ms(699)).err(), Some(Error::TimeReversed));
        assert!(rows.iter().all(|r| r.pose.valid()));
        settle(&mut g, 700);
    }
}
