//! verified foot起動・現在Board・native targetからだけグループ候補を作る。
//! このcaptureはSDK/Hostのoperation排他を代用しない。操作とACKの直前に現在性を再検査する。
use crate::{diagnostics::Snapshot, panels::Action};
use eidolon_model::{
    commands::AppMode,
    ids::RuntimeHandle,
    state::{RuntimeState, Status, XrState},
    surface::TargetKey,
    window::WindowLife,
    window_groups::{Application, Member, Scope},
    window_launch::{Candidate, Outcome, correlate},
    window_slots::{Entry, Origin, Phase},
};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FootOrigin {
    nonce: String,
    pid: u32,
    start: String,
    group: u32,
    uid: u32,
}
#[derive(Clone, Debug)]
pub struct FootCapture {
    pub scope: Scope,
    pub origin: FootOrigin,
    pub members: Vec<Member>,
}
impl FootCapture {
    pub fn member(&self, target: TargetKey) -> Option<&Member> {
        self.members.iter().find(|member| member.target == target)
    }
    pub fn same_owner(&self, other: &Self) -> bool {
        self.scope == other.scope && self.origin == other.origin
    }
}

#[derive(Clone, Debug)]
pub struct FootInspection {
    pub scope: Scope,
    pub origin: FootOrigin,
    /// 分類は確定したが、SDK/full targetの準備はcapture_footが別に検査する。
    pub entries: Vec<Entry>,
}

/// title/PIDやapp_id文字列だけでは分類しない。受理済launchと一意なmapped markerを照合する。
/// 背面のcontent_frameは意図的に古くなり得るため、ここでは描画実績と所有を確認する。
/// 最新content/SDKgeometryの確認は移動終点の別ACKで必須とする。
pub fn inspect_foot(
    runtime: RuntimeHandle,
    snapshot: &Snapshot,
    transport: &eidolon_transport::session::Snapshot,
    render: &eidolon_render::Snapshot,
) -> Result<FootInspection, Status> {
    if snapshot.state != RuntimeState::Ready
        || !snapshot.activity_visible
        || snapshot.xr_state != XrState::Focused
        || transport.state != eidolon_transport::session::State::Ready
        || transport.error.is_some()
        || render.mode != eidolon_render::RenderMode::Windows
        || render.error.is_some()
        || render.paused
    {
        return Err(Status::NotReady);
    }
    let scene = snapshot.scene.ok_or(Status::NotReady)?;
    let app = snapshot.app.as_ref().ok_or(Status::NotReady)?;
    let session = app.session.ok_or(Status::NotReady)?;
    if app.mode != Some(AppMode::Foot)
        || app.state != "running"
        || app.callback_clock != "target-driven"
        || app.error.is_some()
        || transport.session != Some(session)
        || !(app.nonce.len() == 16
            && app
                .nonce
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)))
    {
        return Err(Status::NotReady);
    }
    let forward = transport.app_forward.as_ref().ok_or(Status::NotReady)?;
    let remote = forward.remote.as_ref().ok_or(Status::NotReady)?;
    let tools = remote.tools.as_ref().ok_or(Status::NotReady)?;
    let child = remote.child.as_ref().ok_or(Status::NotReady)?;
    if forward.state != "running"
        || forward.mode != "foot"
        || forward.nonce != app.nonce
        || forward.error.is_some()
        || !forward.registered
        || !forward.pending_cleanup.is_empty()
        || remote.phase != "running"
        || remote.error.is_some()
        // cleanup_pendingはrunning中も回収責務を表す。終了通知/EOF/exitだけをlive喪失として扱う。
        || remote.finished.is_some()
        || remote.eof
        || remote.exit_status.is_some()
        || remote.exit_signal
        || tools.schema != 1
        || tools.nonce != app.nonce
        || tools.mode != "foot"
        || tools.uid != forward.uid
        || tools.foot.is_none()
        || child.pid == 0
        || child.pid != child.group
        || child.start.is_empty()
        || child.start.len() > 32
        || !child.start.bytes().all(|b| b.is_ascii_digit())
        || child.state.len() != 1
        || matches!(child.state.as_str(), "Z" | "X" | "x")
    {
        return Err(Status::Stale);
    }
    let board = &snapshot.window_slots;
    if board.cleanup_required
        || board.occupied != board.entries.len()
        || board.occupied > eidolon_model::window_slots::CAPACITY
    {
        return Err(Status::NotReady);
    }
    let candidates: Vec<_> = app
        .windows
        .iter()
        .map(|window| Candidate {
            window: window.window,
            app_id: window.app_id.as_deref(),
            life: window.life,
        })
        .collect();
    let mut entries = Vec::new();
    for entry in &board.entries {
        let Phase::Bound { window } = entry.state else {
            continue;
        };
        if entry.lease.runtime() != runtime || entry.lease.session() != session {
            return Err(Status::Stale);
        }
        let request = match entry.lease.origin() {
            Origin::Launch(request) => Some(request),
            Origin::Unrequested(id) if id == window => None,
            _ => return Err(Status::Stale),
        };
        if !matches!(correlate(&app.nonce,request,&remote.terminal_launches,&candidates,&[]),Outcome::Matched {window:id,..} if id==window)
        {
            continue; // GTK等の別アプリをfootへ推測分類しない。
        }
        entries.push(*entry);
    }
    if entries.is_empty() {
        return Err(Status::NotReady);
    }
    Ok(FootInspection {
        scope: Scope {
            runtime,
            session,
            scene,
            application: Application::Foot,
        },
        origin: FootOrigin {
            nonce: app.nonce.clone(),
            pid: child.pid,
            start: child.start.clone(),
            group: child.group,
            uid: tools.uid,
        },
        entries,
    })
}

/// 分類だけで新しい窓の入力を許さず、現在のpanel/render/placementを全候補で確認する。
pub fn capture_foot(
    runtime: RuntimeHandle,
    snapshot: &Snapshot,
    transport: &eidolon_transport::session::Snapshot,
    render: &eidolon_render::Snapshot,
) -> Result<FootCapture, Status> {
    let inspection = inspect_foot(runtime, snapshot, transport, render)?;
    let session = inspection.scope.session;
    let scene = inspection.scope.scene;
    let app = snapshot.app.as_ref().ok_or(Status::NotReady)?;
    let mut members = Vec::new();
    for entry in &inspection.entries {
        let Phase::Bound { window } = entry.state else {
            return Err(Status::Stale);
        };
        let windows: Vec<_> = app
            .windows
            .iter()
            .filter(|row| row.window == window && row.life == WindowLife::Mapped)
            .collect();
        if windows.len() != 1 {
            return Err(Status::Stale);
        }
        let row = windows[0];
        let plans: Vec<_> = snapshot
            .panels
            .iter()
            .filter(|plan| {
                plan.target.session == session
                    && plan.target.window == window
                    && plan.target.scene == scene
                    && plan.action == Action::Create
                    && plan.applied
                    && plan.error.is_none()
            })
            .collect();
        if plans.len() != 1 {
            return Err(Status::NotReady);
        }
        let plan = plans[0];
        if plan.slot != entry.lease.position().index() {
            return Err(Status::Stale);
        }
        let draws: Vec<_> = render
            .targets
            .iter()
            .filter(|draw| {
                draw.key == plan.target
                    && draw.role == eidolon_render::RenderMode::Windows
                    && draw.frames > 0
            })
            .collect();
        if draws.len() != 1 || draws[0].content_frame.is_none() || row.frame.is_none() {
            return Err(Status::NotReady);
        }
        let placements: Vec<_> = snapshot
            .workspace
            .windows
            .iter()
            .filter(|placement| placement.session == session && placement.window == window)
            .collect();
        if placements.len() != 1 {
            return Err(Status::NotReady);
        }
        let placement = placements[0];
        if placement.slot != Some(plan.slot)
            || !placement.placement.accepts(plan.target)
            || !placement.placement.pose.valid()
            || !row.sizing.physical_size_m.valid()
        {
            return Err(Status::Stale);
        }
        members.push(Member {
            entry: *entry,
            target: plan.target,
            application: Application::Foot,
            pose: placement.placement.pose,
            physical_size: row.sizing.physical_size_m,
            locked: false,
            operation_active: false,
        });
    }
    if members.is_empty() {
        return Err(Status::NotReady);
    }
    members.sort_by_key(|member| member.entry.lease.position().index());
    Ok(FootCapture {
        scope: inspection.scope,
        origin: inspection.origin,
        members,
    })
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use eidolon_model::{
        ids::{
            CommandSeq, FrameSeq, SceneEpoch, SessionId, SurfaceGeneration, TerminalId, WindowId,
        },
        resize::Sizing,
        surface::TargetKey,
        window::LogicalSize,
        window_launch::TerminalLaunch,
        window_slots::Board,
        workload::terminal_app_id,
    };
    const NONCE: &str = "0123456789abcdef";
    pub(crate) fn fixture(
        n: usize,
    ) -> (
        RuntimeHandle,
        Snapshot,
        eidolon_transport::session::Snapshot,
        eidolon_render::Snapshot,
    ) {
        let runtime = RuntimeHandle::new(4).unwrap();
        let session = SessionId::new(5).unwrap();
        let scene = SceneEpoch::new(6).unwrap();
        let mut snapshot = Snapshot {
            state: RuntimeState::Ready,
            scene: Some(scene),
            xr_state: XrState::Focused,
            activity_visible: true,
            ..Default::default()
        };
        let mut board = Board::new(runtime, session);
        let mut app = crate::session::Report {
            state: "running",
            session: Some(session),
            nonce: NONCE.into(),
            mode: Some(AppMode::Foot),
            callback_clock: "target-driven",
            ..Default::default()
        };
        let mut render = eidolon_render::Snapshot {
            mode: eidolon_render::RenderMode::Windows,
            ..Default::default()
        };
        let mut launches = vec![];
        for i in 0..n {
            let window = WindowId::new(i as i64 + 1).unwrap();
            let request = CommandSeq::new(i as i64 + 10).unwrap();
            let terminal_id = TerminalId::new(i as i64 + 2).unwrap();
            let lease = board.reserve(request).unwrap().lease();
            board.enqueued(lease).unwrap();
            board.bind(lease, window).unwrap();
            let target = TargetKey {
                session,
                window,
                scene,
                generation: SurfaceGeneration::new(i as i64 + 20).unwrap(),
            };
            app.windows.push(crate::compositor::protocol::WindowReport {
                window,
                app_id: Some(terminal_app_id(NONCE, terminal_id).unwrap()),
                life: WindowLife::Mapped,
                frame: Some(FrameSeq::new(1).unwrap()),
                logical_size: Some(LogicalSize::new(640, 360).unwrap()),
                layers: 1,
                resize: Default::default(),
                sizing: Sizing::default(),
            });
            snapshot.panels.push(crate::panels::PanelCommand {
                command_seq: request,
                action: Action::Create,
                target,
                applied: true,
                slot: i,
                error: None,
                decoration: None,
            });
            render.targets.push(eidolon_render::TargetReport {
                key: target,
                frames: 3,
                presentation_paused: false,
                player_clock_ms: None,
                content_frame: Some(FrameSeq::new(1).unwrap()),
                role: eidolon_render::RenderMode::Windows,
                canvas: None,
                content_size: Some(LogicalSize::new(640, 360).unwrap()),
                decoration_frames: 0,
                io_draw: None,
                frame_timing: Default::default(),
                gpu: Default::default(),
            });
            launches.push(TerminalLaunch {
                terminal_id,
                request_id: Some(request),
                accepted: Some(true),
            });
        }
        let mut workspace = crate::workspace::Controller::load(None);
        workspace.scene(Some(scene));
        workspace.sync(session, &app.windows, &snapshot.panels);
        snapshot.workspace = workspace.snapshot();
        snapshot.window_slots.entries = board.snapshot();
        snapshot.window_slots.occupied = board.occupied();
        snapshot.app = Some(app);
        let tools = eidolon_transport::launcher::Tools {
            schema: 1,
            nonce: NONCE.into(),
            uid: 10158,
            mode: "foot".into(),
            waypipe: eidolon_transport::launcher::WaypipeTools {
                version: "0.11.3".into(),
                features: vec!["lz4".into(), "zstd".into()],
                unavailable: vec!["dmabuf".into(), "video".into(), "vaapi".into()],
            },
            fixture: None,
            foot: Some(eidolon_transport::launcher::FootTools {
                version: "1.28.0".into(),
            }),
        };
        let remote = eidolon_transport::launcher::Report {
            phase: "running",
            cleanup_pending: true,
            terminal_launches: launches,
            tools: Some(tools),
            child: Some(eidolon_transport::launcher::Identity {
                pid: 100,
                start: "200".into(),
                group: 100,
                state: "S".into(),
            }),
            ..Default::default()
        };
        let transport = eidolon_transport::session::Snapshot {
            state: eidolon_transport::session::State::Ready,
            session: Some(session),
            app_forward: Some(eidolon_transport::forward_app::Report {
                state: "running",
                mode: "foot".into(),
                nonce: NONCE.into(),
                uid: 10158,
                registered: true,
                remote: Some(remote),
                ..Default::default()
            }),
            ..Default::default()
        };
        (runtime, snapshot, transport, render)
    }
    #[test]
    fn matched_launches_current_bound_slots_and_applied_targets_form_one_foot_capture() {
        let (runtime, snapshot, transport, render) = fixture(3);
        let capture = capture_foot(runtime, &snapshot, &transport, &render).unwrap();
        assert_eq!(capture.members.len(), 3);
        assert_eq!(capture.scope.runtime, runtime);
        assert_eq!(capture.scope.application, Application::Foot);
        let key = capture.members[0].target;
        assert!(capture.member(key).is_some());
        assert!(
            capture
                .member(TargetKey {
                    generation: SurfaceGeneration::new(99).unwrap(),
                    ..key
                })
                .is_none()
        );
        for member in &capture.members {
            assert!(member.pose.valid());
            assert_eq!(
                member.entry.lease.position().index(),
                member.target.window.get() as usize - 1
            );
        }
    }
    #[test]
    fn real_initial_unrequested_window_uses_only_the_null_request_primary_receipt() {
        let (runtime, mut snapshot, mut transport, render) = fixture(2);
        let session = snapshot.app.as_ref().unwrap().session.unwrap();
        let first = WindowId::new(1).unwrap();
        let second = WindowId::new(2).unwrap();
        let mut board = Board::new(runtime, session);
        board.adopt_unrequested(first).unwrap();
        let request = CommandSeq::new(11).unwrap();
        let lease = board.reserve(request).unwrap().lease();
        board.enqueued(lease).unwrap();
        board.bind(lease, second).unwrap();
        snapshot.window_slots.entries = board.snapshot();
        snapshot.window_slots.occupied = 2;
        snapshot.app.as_mut().unwrap().windows[0].app_id =
            Some(terminal_app_id(NONCE, TerminalId::new(1).unwrap()).unwrap());
        let launch = &mut transport
            .app_forward
            .as_mut()
            .unwrap()
            .remote
            .as_mut()
            .unwrap()
            .terminal_launches[0];
        launch.request_id = None;
        launch.terminal_id = TerminalId::new(1).unwrap();
        let captured = capture_foot(runtime, &snapshot, &transport, &render).unwrap();
        assert_eq!(captured.members.len(), 2);
        // Noneを別requestへ読み替えず、主窓の受領が欠けたら一件目を分類しない。
        transport
            .app_forward
            .as_mut()
            .unwrap()
            .remote
            .as_mut()
            .unwrap()
            .terminal_launches[0]
            .request_id = Some(CommandSeq::new(99).unwrap());
        assert_eq!(
            capture_foot(runtime, &snapshot, &transport, &render)
                .unwrap()
                .members
                .len(),
            1
        );
    }
    #[test]
    fn claimed_app_id_without_accepted_unique_receipt_never_joins_the_group() {
        let (runtime, snapshot, mut transport, render) = fixture(3);
        transport
            .app_forward
            .as_mut()
            .unwrap()
            .remote
            .as_mut()
            .unwrap()
            .terminal_launches
            .pop();
        let capture = capture_foot(runtime, &snapshot, &transport, &render).unwrap();
        assert_eq!(capture.members.len(), 2);
        assert!(
            capture
                .members
                .iter()
                .all(|m| m.target.window != WindowId::new(3).unwrap())
        );
        transport
            .app_forward
            .as_mut()
            .unwrap()
            .remote
            .as_mut()
            .unwrap()
            .terminal_launches[0]
            .accepted = Some(false);
        let capture = capture_foot(runtime, &snapshot, &transport, &render).unwrap();
        assert_eq!(capture.members.len(), 1);
        assert!(
            capture
                .members
                .iter()
                .all(|m| m.target.window != WindowId::new(1).unwrap())
        );
    }
    #[test]
    fn duplicate_marker_does_not_select_either_ambiguous_window() {
        let (runtime, mut snapshot, transport, render) = fixture(3);
        snapshot.app.as_mut().unwrap().windows[2].app_id =
            snapshot.app.as_ref().unwrap().windows[0].app_id.clone();
        let capture = capture_foot(runtime, &snapshot, &transport, &render).unwrap();
        assert_eq!(capture.members.len(), 1);
        assert!(
            capture
                .members
                .iter()
                .any(|m| m.target.window == WindowId::new(2).unwrap())
        );
    }
    #[test]
    fn other_runtime_scene_slot_or_unapplied_surface_cannot_be_adopted() {
        let (runtime, snapshot, transport, render) = fixture(2);
        assert!(
            capture_foot(
                RuntimeHandle::new(9).unwrap(),
                &snapshot,
                &transport,
                &render
            )
            .is_err()
        );
        let mut other = snapshot.clone();
        other.scene = Some(SceneEpoch::new(99).unwrap());
        assert!(capture_foot(runtime, &other, &transport, &render).is_err());
        other = snapshot.clone();
        other.panels[0].slot = 1;
        assert!(capture_foot(runtime, &other, &transport, &render).is_err());
        other = snapshot.clone();
        other.panels[0].applied = false;
        assert!(capture_foot(runtime, &other, &transport, &render).is_err());
        let mut no_content = render.clone();
        no_content.targets[0].content_frame = None;
        assert!(capture_foot(runtime, &snapshot, &transport, &no_content).is_err());
    }
    #[test]
    fn running_remote_can_own_cleanup_duty_but_eof_exit_or_finished_never_proves_live() {
        let (runtime, snapshot, transport, render) = fixture(2);
        assert!(
            transport
                .app_forward
                .as_ref()
                .unwrap()
                .remote
                .as_ref()
                .unwrap()
                .cleanup_pending
        );
        assert_eq!(
            capture_foot(runtime, &snapshot, &transport, &render)
                .unwrap()
                .members
                .len(),
            2
        );
        for case in 0..4 {
            let mut changed = transport.clone();
            let remote = changed
                .app_forward
                .as_mut()
                .unwrap()
                .remote
                .as_mut()
                .unwrap();
            match case {
                0 => remote.eof = true,
                1 => remote.exit_status = Some(0),
                2 => remote.exit_signal = true,
                _ => {
                    remote.finished = Some(eidolon_transport::launcher::Finished {
                        schema: 1,
                        nonce: NONCE.into(),
                        uid: 10158,
                        reason: "Stop".into(),
                        child: None,
                        exit_status: Some(0),
                        exit_signal: None,
                        term_sent: false,
                        kill_sent: false,
                        cleaned: true,
                        group_alive: false,
                        pipes_closed: true,
                        stdout: eidolon_transport::launcher::Metrics {
                            bytes: 0,
                            truncated: false,
                            retained: 0,
                        },
                        stderr: eidolon_transport::launcher::Metrics {
                            bytes: 0,
                            truncated: false,
                            retained: 0,
                        },
                        echo: None,
                        pattern: None,
                        error: None,
                    })
                }
            }
            assert!(capture_foot(runtime, &snapshot, &changed, &render).is_err());
        }
    }
    #[test]
    fn nonce_uid_child_scope_and_forward_failures_are_not_a_fresh_capture() {
        let (runtime, snapshot, transport, render) = fixture(2);
        let original = capture_foot(runtime, &snapshot, &transport, &render).unwrap();
        for case in 0..5 {
            let mut changed = transport.clone();
            let forward = changed.app_forward.as_mut().unwrap();
            match case {
                0 => forward.nonce = "f".repeat(16),
                1 => forward.uid = 10159,
                2 => {
                    forward
                        .remote
                        .as_mut()
                        .unwrap()
                        .child
                        .as_mut()
                        .unwrap()
                        .state = "Z".into()
                }
                3 => forward.remote.as_mut().unwrap().error = Some("FIXTURE_FAILURE"),
                _ => forward.registered = false,
            };
            assert!(capture_foot(runtime, &snapshot, &changed, &render).is_err());
        }
        let mut changed = transport.clone();
        changed
            .app_forward
            .as_mut()
            .unwrap()
            .remote
            .as_mut()
            .unwrap()
            .child
            .as_mut()
            .unwrap()
            .start = "201".into();
        assert!(
            !original.same_owner(&capture_foot(runtime, &snapshot, &changed, &render).unwrap())
        );
    }
    #[test]
    fn paused_or_failed_runtime_and_unknown_gui_modes_do_not_classify_as_foot() {
        let (runtime, snapshot, transport, render) = fixture(2);
        let mut changed = snapshot.clone();
        changed.activity_visible = false;
        assert!(capture_foot(runtime, &changed, &transport, &render).is_err());
        changed = snapshot.clone();
        changed.app.as_mut().unwrap().mode = Some(AppMode::Pattern);
        assert!(capture_foot(runtime, &changed, &transport, &render).is_err());
        let mut paused = render.clone();
        paused.paused = true;
        assert!(capture_foot(runtime, &snapshot, &transport, &paused).is_err());
    }
    #[test]
    fn classified_new_foot_is_known_before_sdk_ready_but_cannot_yet_be_admitted() {
        let (runtime, mut snapshot, mut transport, render) = fixture(3);
        snapshot.panels[2].applied = false;
        assert_eq!(
            inspect_foot(runtime, &snapshot, &transport, &render)
                .unwrap()
                .entries
                .len(),
            3
        );
        assert!(capture_foot(runtime, &snapshot, &transport, &render).is_err());
        transport
            .app_forward
            .as_mut()
            .unwrap()
            .remote
            .as_mut()
            .unwrap()
            .terminal_launches[2]
            .accepted = Some(false);
        assert_eq!(
            inspect_foot(runtime, &snapshot, &transport, &render)
                .unwrap()
                .entries
                .len(),
            2
        );
        assert_eq!(
            capture_foot(runtime, &snapshot, &transport, &render)
                .unwrap()
                .members
                .len(),
            2
        );
    }
    #[test]
    fn hidden_back_latest_content_can_wait_for_the_separate_geometry_and_fresh_frame_ack() {
        let (runtime, mut snapshot, transport, mut render) = fixture(2);
        snapshot.app.as_mut().unwrap().windows[1].frame = Some(FrameSeq::new(2).unwrap());
        render.targets[1].presentation_paused = true;
        let capture = capture_foot(runtime, &snapshot, &transport, &render).unwrap();
        assert_eq!(capture.members.len(), 2);
        assert_eq!(
            render.targets[1].content_frame,
            Some(FrameSeq::new(1).unwrap())
        );
    }
}
