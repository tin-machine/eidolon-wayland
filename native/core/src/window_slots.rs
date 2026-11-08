//! 単一core workerが予約を所有し、窓消滅とnative/SDK回収を別々に観測する。
use eidolon_model::{
    ids::{CommandSeq, RuntimeHandle, SessionId, WindowId},
    state::Status,
    surface::TargetKey,
    window::WindowLife,
    window_launch::Outcome,
    window_slots::{Ack, Board, CAPACITY, Entry, Lease, Origin, Phase, Released, Reserved},
    workload::claimed_terminal,
};
use serde::Serialize;

#[derive(Clone, Debug, Serialize)]
pub struct Report {
    pub capacity: usize,
    pub display_limit: usize,
    pub occupied: usize,
    pub entries: Vec<Entry>,
    pub unassigned: Vec<WindowId>,
    pub cleanup_required: bool,
    pub initial_window_seen: bool,
}
impl Default for Report {
    fn default() -> Self {
        Self {
            capacity: CAPACITY,
            display_limit: eidolon_model::limits::VISIBLE_PANELS,
            occupied: 0,
            entries: vec![],
            unassigned: vec![],
            cleanup_required: false,
            initial_window_seen: false,
        }
    }
}
#[derive(Clone, Copy)]
pub struct Window<'a> {
    pub id: WindowId,
    pub app_id: Option<&'a str>,
    pub life: WindowLife,
    /// 新規採用の許可。false でも実在する Bound 窓の回収証拠から除かない。
    pub admissible: bool,
}
pub struct Observation<'a> {
    pub session: SessionId,
    pub nonce: &'a str,
    pub windows: &'a [Window<'a>],
    /// Session::closedはremote/forward/local/protocol/SHM/directoryの回収後だけtrue。
    pub session_closed: bool,
    pub native: &'a [TargetKey],
    pub sdk: &'a [TargetKey],
    pub panels: &'a [TargetKey],
}
pub struct Controller {
    runtime: RuntimeHandle,
    board: Option<Board>,
    session: Option<SessionId>,
    nonce: String,
    unassigned: Vec<WindowId>,
    initial_window_seen: bool,
}
impl Controller {
    pub fn new(runtime: RuntimeHandle) -> Self {
        Self {
            runtime,
            board: None,
            session: None,
            nonce: String::new(),
            unassigned: vec![],
            initial_window_seen: false,
        }
    }
    pub fn can_begin(&self, session: SessionId, nonce: &str) -> bool {
        self.session == Some(session) && self.nonce == nonce
            || self.board.as_ref().is_none_or(|b| b.occupied() == 0)
    }
    pub fn begin(&mut self, session: SessionId, nonce: &str) -> Result<(), Status> {
        if !self.can_begin(session, nonce) {
            return Err(Status::Busy);
        }
        if self.session != Some(session) || self.nonce != nonce {
            self.initial_window_seen = false;
        }
        if self.session != Some(session) {
            self.board = Some(Board::new(self.runtime, session));
            self.session = Some(session);
        }
        // 同sessionでnonceが切り替わっても単調request/token floorは初期化しない。
        self.nonce = nonce.to_owned();
        self.unassigned.clear();
        Ok(())
    }
    pub fn report(&self) -> Report {
        let entries = self.board.as_ref().map_or_else(Vec::new, Board::snapshot);
        Report {
            capacity: CAPACITY,
            display_limit: eidolon_model::limits::VISIBLE_PANELS,
            occupied: entries.len(),
            cleanup_required: entries
                .iter()
                .any(|e| matches!(e.state, Phase::Retiring { .. })),
            entries,
            unassigned: self.unassigned.clone(),
            initial_window_seen: self.initial_window_seen,
        }
    }
    pub fn has_mapped_initial(&self, session: SessionId) -> bool {
        self.session == Some(session) && self.initial_window_seen
    }
    pub fn reserve(&mut self, session: SessionId, request: CommandSeq) -> Result<Lease, Status> {
        if self.session != Some(session) {
            return Err(Status::Stale);
        }
        match self
            .board
            .as_mut()
            .ok_or(Status::NotReady)?
            .reserve(request)
        {
            Ok(Reserved::New(lease)) => Ok(lease),
            Ok(Reserved::Existing(_)) => Err(Status::Stale), // 再enqueueしない。
            Err(
                eidolon_model::window_slots::Error::Full
                | eidolon_model::window_slots::Error::CleaningUp,
            ) => Err(Status::Busy),
            Err(_) => Err(Status::Stale),
        }
    }
    pub fn enqueued(&mut self, lease: Lease) -> Result<(), Status> {
        self.board
            .as_mut()
            .ok_or(Status::NotReady)?
            .enqueued(lease)
            .map_err(|_| Status::Stale)
    }
    pub fn not_enqueued(&mut self, lease: Lease) {
        if let Some(board) = self.board.as_mut() {
            let _ = board.cancel(lease);
        }
    }
    pub fn outcome(
        &mut self,
        session: SessionId,
        request: CommandSeq,
        outcome: Outcome,
    ) -> Result<(), Status> {
        if self.session != Some(session) {
            return Err(Status::Stale);
        }
        let board = self.board.as_mut().ok_or(Status::NotReady)?;
        let Some(entry) = board
            .snapshot()
            .into_iter()
            .find(|e| e.lease.origin() == Origin::Launch(request))
        else {
            return Err(Status::Stale);
        };
        match outcome {
            Outcome::Matched { window, .. } => {
                board.bind(entry.lease, window).map_err(|_| Status::Stale)
            }
            Outcome::Rejected { .. } | Outcome::Cancelled => board
                .cancel(entry.lease)
                .map(|_| ())
                .map_err(|_| Status::Stale),
            _ => Ok(()),
        }
    }
    pub fn observe(&mut self, observation: Observation<'_>) -> Result<(), Status> {
        if self.session != Some(observation.session) || self.nonce != observation.nonce {
            return Err(Status::Stale);
        }
        let board = self.board.as_mut().ok_or(Status::NotReady)?;
        self.initial_window_seen |= observation
            .windows
            .iter()
            .any(|w| w.life == WindowLife::Mapped && w.admissible);
        for entry in board.snapshot() {
            match entry.state {
                Phase::Bound { window }
                    if !observation
                        .windows
                        .iter()
                        .any(|w| w.id == window && w.life != WindowLife::Destroyed) =>
                {
                    board
                        .window_gone(entry.lease, window)
                        .map_err(|_| Status::Failed)?;
                }
                Phase::Reserved { .. } if observation.session_closed => {
                    board.cancel(entry.lease).map_err(|_| Status::Failed)?;
                }
                _ => (),
            }
        }
        for entry in board.snapshot() {
            let Phase::Retiring { ticket, window, .. } = entry.state else {
                continue;
            };
            let contains = |keys: &[TargetKey]| {
                keys.iter().any(|k| {
                    k.session == observation.session && window.is_none_or(|id| k.window == id)
                })
            };
            let native_done = !contains(observation.native);
            if window.is_some() {
                if native_done
                    && board
                        .acknowledge(ticket, Ack::Native)
                        .map_err(|_| Status::Failed)?
                        == Released::Complete
                {
                    continue;
                }
                if !contains(observation.sdk) && !contains(observation.panels) {
                    board
                        .acknowledge(ticket, Ack::Sdk)
                        .map_err(|_| Status::Failed)?;
                }
            } else if observation.session_closed && native_done {
                // 受付失敗/timeoutをremote終了へ読み替えない。完全session終了だけを証拠にする。
                board
                    .acknowledge(ticket, Ack::Native)
                    .map_err(|_| Status::Failed)?;
                board
                    .acknowledge(ticket, Ack::Remote)
                    .map_err(|_| Status::Failed)?;
            }
        }
        self.unassigned.clear();
        let has_pending = board.snapshot().iter().any(|e| {
            matches!(
                e.state,
                Phase::Reserved { .. } | Phase::Retiring { window: None, .. }
            )
        });
        for window in observation
            .windows
            .iter()
            .filter(|w| w.life == WindowLife::Mapped && w.admissible)
        {
            let known=board.snapshot().iter().any(|e|matches!(e.state,Phase::Bound{window:id}|Phase::Retiring{window:Some(id),..} if id==window.id));
            if known {
                continue;
            }
            // 受付よりmapが先でも起動結果を自発窓として予約から奪わない。
            if has_pending
                && window.app_id.is_some_and(|id| {
                    claimed_terminal(id, observation.nonce).is_some()
                        || eidolon_model::linux_desktop::claim(id, observation.nonce)
                            == Some(eidolon_model::linux_desktop::Role::Main)
                })
            {
                continue;
            }
            if board.adopt_unrequested(window.id).is_err() {
                self.unassigned.push(window.id);
            }
        }
        Ok(())
    }
    /// panel割当の次segmentで用いる。Unmapped/隠れたBoundもleaseは保持する。
    pub fn assignments(&self) -> Vec<(usize, SessionId, WindowId)> {
        self.board
            .as_ref()
            .map_or_else(Vec::new, Board::snapshot)
            .into_iter()
            .filter_map(|entry| match entry.state {
                Phase::Bound { window } => Some((
                    entry.lease.position().index(),
                    entry.lease.session(),
                    window,
                )),
                _ => None,
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use eidolon_model::{
        ids::{SceneEpoch, SurfaceGeneration, TerminalId},
        workload::terminal_app_id,
    };
    const NONCE: &str = "0123456789abcdef";
    fn session() -> SessionId {
        SessionId::new(1).unwrap()
    }
    fn id(n: i64) -> WindowId {
        WindowId::new(n).unwrap()
    }
    fn req(n: i64) -> CommandSeq {
        CommandSeq::new(n).unwrap()
    }
    fn window(n: i64) -> Window<'static> {
        Window {
            id: id(n),
            app_id: None,
            life: WindowLife::Mapped,
            admissible: true,
        }
    }
    fn controller() -> Controller {
        let mut c = Controller::new(RuntimeHandle::new(9).unwrap());
        c.begin(session(), NONCE).unwrap();
        c
    }
    fn key(n: i64, generation: i64) -> TargetKey {
        TargetKey {
            session: session(),
            window: id(n),
            scene: SceneEpoch::new(1).unwrap(),
            generation: SurfaceGeneration::new(generation).unwrap(),
        }
    }
    fn observe(
        c: &mut Controller,
        windows: &[Window<'_>],
        closed: bool,
        native: &[TargetKey],
        sdk: &[TargetKey],
        panels: &[TargetKey],
    ) {
        c.observe(Observation {
            session: session(),
            nonce: NONCE,
            windows,
            session_closed: closed,
            native,
            sdk,
            panels,
        })
        .unwrap();
    }
    #[test]
    fn reservation_precedes_unrelated_mapping_and_reverse_launch_completion() {
        let mut c = controller();
        observe(&mut c, &[window(1)], false, &[], &[], &[]);
        let a = c.reserve(session(), req(10)).unwrap();
        c.enqueued(a).unwrap();
        let b = c.reserve(session(), req(20)).unwrap();
        c.enqueued(b).unwrap();
        observe(&mut c, &[window(1), window(7)], false, &[], &[], &[]);
        c.outcome(
            session(),
            req(20),
            Outcome::Matched {
                terminal_id: TerminalId::new(2).unwrap(),
                window: id(6),
            },
        )
        .unwrap();
        c.outcome(
            session(),
            req(10),
            Outcome::Matched {
                terminal_id: TerminalId::new(1).unwrap(),
                window: id(8),
            },
        )
        .unwrap();
        assert_eq!(
            c.assignments(),
            [
                (0, session(), id(1)),
                (1, session(), id(8)),
                (2, session(), id(6)),
                (3, session(), id(7))
            ]
        );
    }
    #[test]
    fn foot_result_mapping_before_receipt_does_not_steal_reserved_or_other_slot() {
        let mut c = controller();
        observe(&mut c, &[window(1)], false, &[], &[], &[]);
        let lease = c.reserve(session(), req(10)).unwrap();
        c.enqueued(lease).unwrap();
        let marker = terminal_app_id(NONCE, TerminalId::new(2).unwrap()).unwrap();
        let result = Window {
            id: id(2),
            app_id: Some(&marker),
            life: WindowLife::Mapped,
            admissible: true,
        };
        observe(&mut c, &[window(1), result], false, &[], &[], &[]);
        assert_eq!(c.assignments(), [(0, session(), id(1))]);
        assert_eq!(c.report().occupied, 2);
        c.outcome(
            session(),
            req(10),
            Outcome::Matched {
                terminal_id: TerminalId::new(2).unwrap(),
                window: id(2),
            },
        )
        .unwrap();
        assert_eq!(c.assignments()[1], (1, session(), id(2)));
    }
    #[test]
    fn unmap_holds_slot_but_destruction_waits_for_native_and_sdk_commands() {
        let mut c = controller();
        observe(&mut c, &[window(1)], false, &[], &[], &[]);
        let mut unmapped = window(1);
        unmapped.life = WindowLife::Unmapped;
        observe(&mut c, &[unmapped], false, &[], &[], &[]);
        assert_eq!(c.report().occupied, 1);
        observe(&mut c, &[], false, &[key(1, 1)], &[key(1, 1)], &[key(1, 1)]);
        assert_eq!(c.report().occupied, 1);
        observe(&mut c, &[], false, &[], &[], &[key(1, 1)]);
        assert_eq!(c.report().occupied, 1);
        observe(&mut c, &[], false, &[], &[], &[]);
        assert_eq!(c.report().occupied, 0);
        assert!(c.has_mapped_initial(session())); // 全窓close後にも新しいterminalを開ける。
        observe(&mut c, &[window(2)], false, &[], &[], &[]);
        assert_eq!(c.assignments(), [(0, session(), id(2))]);
    }
    #[test]
    fn linux_main_maps_before_started_without_stealing_reservation_and_dialog_retires_separately() {
        let mut c = controller();
        let lease = c.reserve(session(), req(10)).unwrap();
        // Local Session setup repeats begin after the command reserved its slot.
        c.begin(session(), NONCE).unwrap();
        assert_eq!(c.report().entries[0].lease, lease);
        c.enqueued(lease).unwrap();
        let main_id = eidolon_model::linux_desktop::main_app_id(NONCE).unwrap();
        let dialog_id = eidolon_model::linux_desktop::dialog_app_id(NONCE).unwrap();
        let main = Window {
            id: id(1),
            app_id: Some(&main_id),
            life: WindowLife::Mapped,
            admissible: true,
        };
        let dialog = Window {
            id: id(2),
            app_id: Some(&dialog_id),
            life: WindowLife::Mapped,
            admissible: true,
        };
        observe(&mut c, &[main], false, &[], &[], &[]);
        assert!(c.assignments().is_empty());
        assert_eq!(c.report().occupied, 1);
        c.outcome(
            session(),
            req(10),
            Outcome::Matched {
                terminal_id: TerminalId::new(1).unwrap(),
                window: id(1),
            },
        )
        .unwrap();
        observe(
            &mut c,
            &[main, dialog],
            false,
            &[key(1, 1), key(2, 1)],
            &[key(1, 1), key(2, 1)],
            &[],
        );
        assert_eq!(
            c.assignments(),
            [(0, session(), id(1)), (1, session(), id(2))]
        );
        // Admission loss hides targets but is not proof that the Wayland owner destroyed the windows.
        observe(
            &mut c,
            &[
                Window {
                    admissible: false,
                    ..main
                },
                Window {
                    admissible: false,
                    ..dialog
                },
            ],
            false,
            &[],
            &[],
            &[],
        );
        assert_eq!(c.report().occupied, 2);
        assert!(!c.report().cleanup_required);
        observe(
            &mut c,
            &[main],
            false,
            &[key(1, 1)],
            &[key(1, 1), key(2, 1)],
            &[],
        );
        assert_eq!(c.report().occupied, 2);
        observe(&mut c, &[main], false, &[key(1, 1)], &[key(1, 1)], &[]);
        assert_eq!(c.report().occupied, 1);
        observe(&mut c, &[], true, &[key(1, 1)], &[key(1, 1)], &[]);
        assert!(!c.can_begin(SessionId::new(2).unwrap(), "ffffffffffffffff"));
        observe(&mut c, &[], true, &[], &[], &[]);
        assert!(c.can_begin(SessionId::new(2).unwrap(), "ffffffffffffffff"));
    }
    #[test]
    fn sdk_can_finish_first_without_late_ack_failing_the_fresh_native_release() {
        let mut c = controller();
        observe(&mut c, &[window(1)], false, &[], &[], &[]);
        observe(&mut c, &[], false, &[key(1, 1)], &[], &[]);
        assert_eq!(c.report().occupied, 1);
        observe(&mut c, &[], false, &[], &[], &[]);
        assert_eq!(c.report().occupied, 0);
    }
    #[test]
    fn rejected_accepted_launch_is_held_until_full_session_cleanup_not_timeout() {
        let mut c = controller();
        let lease = c.reserve(session(), req(10)).unwrap();
        c.enqueued(lease).unwrap();
        c.outcome(
            session(),
            req(10),
            Outcome::Rejected {
                terminal_id: TerminalId::new(1).unwrap(),
            },
        )
        .unwrap();
        for _ in 0..100 {
            observe(&mut c, &[], false, &[], &[], &[]);
        }
        assert!(c.report().cleanup_required);
        assert_eq!(c.report().occupied, 1);
        assert!(!c.can_begin(SessionId::new(2).unwrap(), "fedcba9876543210"));
        observe(&mut c, &[], true, &[], &[], &[]);
        assert_eq!(c.report().occupied, 0);
    }
    #[test]
    fn failed_enqueue_releases_only_its_own_pending_reservation() {
        let mut c = controller();
        let lease = c.reserve(session(), req(10)).unwrap();
        c.not_enqueued(lease);
        assert_eq!(c.report().occupied, 0);
        assert_eq!(c.reserve(session(), req(10)), Err(Status::Stale));
        let lease = c.reserve(session(), req(20)).unwrap();
        c.enqueued(lease).unwrap();
        c.not_enqueued(lease);
        assert!(c.report().cleanup_required); // 誤ったproducer呼出しでも受付済み枠を早期解放しない。
    }
    #[test]
    fn ninth_bound_and_pending_count_together_tenth_request_never_removes_old_windows() {
        let mut c = controller();
        let windows = (1..=8).map(window).collect::<Vec<_>>();
        observe(&mut c, &windows, false, &[], &[], &[]);
        let ninth = c.reserve(session(), req(10)).unwrap();
        c.enqueued(ninth).unwrap();
        let before = c.assignments();
        assert_eq!(c.reserve(session(), req(20)), Err(Status::Busy));
        assert_eq!(c.assignments(), before);
        let windows = (1..=9).map(window).collect::<Vec<_>>();
        observe(&mut c, &windows, false, &[], &[], &[]);
        assert_eq!(c.report().unassigned, [id(9)]);
        assert_eq!(c.report().occupied, 9);
    }
    #[test]
    fn wrong_session_nonce_and_old_request_cannot_reset_scope_or_token_floor() {
        let mut c = controller();
        let lease = c.reserve(session(), req(10)).unwrap();
        c.not_enqueued(lease);
        assert_eq!(
            c.observe(Observation {
                session: session(),
                nonce: "fedcba9876543210",
                windows: &[],
                session_closed: true,
                native: &[],
                sdk: &[],
                panels: &[]
            }),
            Err(Status::Stale)
        );
        c.begin(session(), "fedcba9876543210").unwrap();
        assert_eq!(c.reserve(session(), req(10)), Err(Status::Stale));
        assert_eq!(
            c.outcome(SessionId::new(2).unwrap(), req(10), Outcome::Cancelled),
            Err(Status::Stale)
        );
    }
}
