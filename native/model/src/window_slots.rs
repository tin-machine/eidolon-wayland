//! NX16の予約モデル。製品の窓数上限はここでは変更しない。leaseは保存設定ではなくruntime/session内だけで使う。
use crate::ids::{CommandSeq, RuntimeHandle, SessionId, WindowId};
use serde::Serialize;

pub const CAPACITY: usize = 9;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
#[repr(usize)]
pub enum Position {
    Center,
    Right,
    Left,
    Up,
    Down,
    UpperLeft,
    UpperRight,
    LowerLeft,
    LowerRight,
}
impl Position {
    pub const ORDER: [Self; CAPACITY] = [
        Self::Center,
        Self::Right,
        Self::Left,
        Self::Up,
        Self::Down,
        Self::UpperLeft,
        Self::UpperRight,
        Self::LowerLeft,
        Self::LowerRight,
    ];
    pub fn index(self) -> usize {
        self as usize
    }
    pub fn grid(self) -> [i8; 2] {
        match self {
            Self::Center => [0, 0],
            Self::Right => [1, 0],
            Self::Left => [-1, 0],
            Self::Up => [0, 1],
            Self::Down => [0, -1],
            Self::UpperLeft => [-1, 1],
            Self::UpperRight => [1, 1],
            Self::LowerLeft => [-1, -1],
            Self::LowerRight => [1, -1],
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Origin {
    Launch(CommandSeq),
    Unrequested(WindowId),
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub struct Lease {
    runtime: RuntimeHandle,
    session: SessionId,
    token: CommandSeq,
    position: Position,
    origin: Origin,
}
impl Lease {
    pub fn runtime(self) -> RuntimeHandle {
        self.runtime
    }
    pub fn position(self) -> Position {
        self.position
    }
    pub fn origin(self) -> Origin {
        self.origin
    }
    pub fn session(self) -> SessionId {
        self.session
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub struct Retirement {
    lease: Lease,
    drain: CommandSeq,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Reserved {
    New(Lease),
    Existing(Lease),
}
impl Reserved {
    pub fn lease(self) -> Lease {
        match self {
            Self::New(l) | Self::Existing(l) => l,
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Cancelled {
    Released,
    Awaiting(Retirement),
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Released {
    Waiting,
    Complete,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Ack {
    Native,
    Sdk,
    Remote,
}
impl Ack {
    fn mask(self) -> u8 {
        match self {
            Self::Native => 1,
            Self::Sdk => 2,
            Self::Remote => 4,
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Error {
    Full,
    StaleRequest,
    StaleLease,
    CleaningUp,
    NotEnqueued,
    AlreadyBound,
    DuplicateWindow,
    WindowAlive,
    UnexpectedAck,
    IdExhausted,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(tag = "phase", rename_all = "snake_case")]
pub enum Phase {
    Reserved {
        enqueued: bool,
    },
    Bound {
        window: WindowId,
    },
    Retiring {
        ticket: Retirement,
        window: Option<WindowId>,
        required: u8,
        seen: u8,
    },
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub struct Entry {
    pub lease: Lease,
    pub state: Phase,
}

/// 呼出元はlaunchを単一ownerで受付順に予約する。逆転してよいのは完了順であり、request採番は単調増加。
/// 受付済み取消ではRemote/Native、窓消滅ではNative/SDKの捕捉済みdrain ACKを待つ。timeoutでは解放しない。
pub struct Board {
    runtime: RuntimeHandle,
    session: SessionId,
    next_token: i64,
    last_request: i64,
    slots: [Option<Entry>; CAPACITY],
}
impl Board {
    pub fn new(runtime: RuntimeHandle, session: SessionId) -> Self {
        Self {
            runtime,
            session,
            next_token: 0,
            last_request: 0,
            slots: [None; CAPACITY],
        }
    }
    pub fn occupied(&self) -> usize {
        self.slots.iter().flatten().count()
    }
    pub fn snapshot(&self) -> Vec<Entry> {
        self.slots.iter().flatten().copied().collect()
    }
    pub fn get(&self, lease: Lease) -> Result<Entry, Error> {
        self.slots[lease.position.index()]
            .filter(|entry| {
                entry.lease == lease
                    && lease.runtime == self.runtime
                    && lease.session == self.session
            })
            .ok_or(Error::StaleLease)
    }
    fn sequence(&mut self) -> Result<CommandSeq, Error> {
        self.next_token = self.next_token.checked_add(1).ok_or(Error::IdExhausted)?;
        CommandSeq::new(self.next_token).map_err(|_| Error::IdExhausted)
    }
    fn allocate(&mut self, origin: Origin, state: Phase) -> Result<Lease, Error> {
        let position = Position::ORDER
            .into_iter()
            .find(|p| self.slots[p.index()].is_none())
            .ok_or(Error::Full)?;
        let lease = Lease {
            runtime: self.runtime,
            session: self.session,
            token: self.sequence()?,
            position,
            origin,
        };
        self.slots[position.index()] = Some(Entry { lease, state });
        Ok(lease)
    }
    pub fn reserve(&mut self, request: CommandSeq) -> Result<Reserved, Error> {
        if let Some(entry) = self
            .slots
            .iter()
            .flatten()
            .find(|e| e.lease.origin == Origin::Launch(request))
        {
            return if matches!(entry.state, Phase::Retiring { .. }) {
                Err(Error::CleaningUp)
            } else {
                Ok(Reserved::Existing(entry.lease))
            };
        }
        if request.get() <= self.last_request {
            return Err(Error::StaleRequest);
        }
        // 拒否済みrequestも再実行しない。利用者の再試行は新requestで予約する。
        self.last_request = request.get();
        self.allocate(Origin::Launch(request), Phase::Reserved { enqueued: false })
            .map(Reserved::New)
    }
    pub fn adopt_unrequested(&mut self, window: WindowId) -> Result<Reserved, Error> {
        if let Some(entry) = self.window_owner(window) {
            return if matches!(entry.state, Phase::Retiring { .. }) {
                Err(Error::CleaningUp)
            } else {
                Ok(Reserved::Existing(entry.lease))
            };
        }
        self.allocate(Origin::Unrequested(window), Phase::Bound { window })
            .map(Reserved::New)
    }
    fn window_owner(&self, window: WindowId) -> Option<Entry> {
        self.slots
            .iter()
            .flatten()
            .find(|e| match e.state {
                Phase::Bound { window: w }
                | Phase::Retiring {
                    window: Some(w), ..
                } => w == window,
                _ => false,
            })
            .copied()
    }
    pub fn enqueued(&mut self, lease: Lease) -> Result<(), Error> {
        let entry = self.get(lease)?;
        match entry.state {
            Phase::Reserved { .. } => {
                self.slots[lease.position.index()].as_mut().unwrap().state =
                    Phase::Reserved { enqueued: true }
            }
            Phase::Retiring { .. } => return Err(Error::CleaningUp),
            Phase::Bound { .. } => return Err(Error::AlreadyBound),
        }
        Ok(())
    }
    pub fn bind(&mut self, lease: Lease, window: WindowId) -> Result<(), Error> {
        let entry = self.get(lease)?;
        if entry.state == (Phase::Bound { window }) {
            return Ok(());
        }
        if self.window_owner(window).is_some() {
            return Err(Error::DuplicateWindow);
        }
        match entry.state {
            Phase::Reserved { enqueued: true } => {
                self.slots[lease.position.index()].as_mut().unwrap().state = Phase::Bound { window }
            }
            Phase::Reserved { enqueued: false } => return Err(Error::NotEnqueued),
            Phase::Bound { .. } => return Err(Error::AlreadyBound),
            Phase::Retiring { .. } => return Err(Error::CleaningUp),
        }
        Ok(())
    }
    pub fn cancel(&mut self, lease: Lease) -> Result<Cancelled, Error> {
        match self.get(lease)?.state {
            Phase::Reserved { enqueued: false } => {
                self.slots[lease.position.index()] = None;
                Ok(Cancelled::Released)
            }
            Phase::Reserved { enqueued: true } => {
                let ticket = Retirement {
                    lease,
                    drain: self.sequence()?,
                };
                self.slots[lease.position.index()].as_mut().unwrap().state = Phase::Retiring {
                    ticket,
                    window: None,
                    required: 5,
                    seen: 0,
                };
                Ok(Cancelled::Awaiting(ticket))
            }
            Phase::Retiring {
                ticket,
                window: None,
                ..
            } => Ok(Cancelled::Awaiting(ticket)),
            _ => Err(Error::WindowAlive),
        }
    }
    /// Unmapped/hiddenでは呼ばない。xdg_toplevel自体の消滅を確認したownerだけがdrainを開始する。
    pub fn window_gone(&mut self, lease: Lease, window: WindowId) -> Result<Retirement, Error> {
        match self.get(lease)?.state {
            Phase::Bound { window: w } if w == window => {
                let ticket = Retirement {
                    lease,
                    drain: self.sequence()?,
                };
                self.slots[lease.position.index()].as_mut().unwrap().state = Phase::Retiring {
                    ticket,
                    window: Some(window),
                    required: 3,
                    seen: 0,
                };
                Ok(ticket)
            }
            Phase::Retiring {
                ticket,
                window: Some(w),
                ..
            } if w == window => Ok(ticket),
            _ => Err(Error::WindowAlive),
        }
    }
    /// ACKはproducer側でも同じdrainが捕捉した対象群の消滅を検査する。受付code/要求送信はACKではない。
    pub fn acknowledge(&mut self, ticket: Retirement, side: Ack) -> Result<Released, Error> {
        let entry = self.get(ticket.lease)?;
        let Phase::Retiring {
            ticket: current,
            window,
            required,
            seen,
        } = entry.state
        else {
            return Err(Error::StaleLease);
        };
        if current != ticket {
            return Err(Error::StaleLease);
        }
        let mask = side.mask();
        if required & mask == 0 {
            return Err(Error::UnexpectedAck);
        }
        let seen = seen | mask;
        if seen == required {
            self.slots[ticket.lease.position.index()] = None;
            Ok(Released::Complete)
        } else {
            self.slots[ticket.lease.position.index()]
                .as_mut()
                .unwrap()
                .state = Phase::Retiring {
                ticket,
                window,
                required,
                seen,
            };
            Ok(Released::Waiting)
        }
    }
}

/// 本文とNX12b操作部の同一plane上の占有範囲。保存済み自由配置へ適用しない。
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Envelope {
    pub left: f64,
    pub right: f64,
    pub bottom: f64,
    pub top: f64,
}
impl Envelope {
    pub fn for_window(
        width: f64,
        height: f64,
        controls: &crate::window_controls::WindowControls,
    ) -> Option<Self> {
        use crate::window_controls::ControlAnchor;
        if !controls.valid()
            || ![width, height]
                .into_iter()
                .all(|n| n.is_finite() && (0.25..=3.).contains(&n))
        {
            return None;
        }
        let right = matches!(
            controls.anchor,
            ControlAnchor::TopRight | ControlAnchor::BottomRight
        );
        let top = matches!(
            controls.anchor,
            ControlAnchor::TopRight | ControlAnchor::TopLeft
        );
        let d = controls.diameter_m;
        let gap = controls.gap_m;
        let end = width / 2. + controls.offset_m[0];
        let min = (end - 2. * (d + gap) - d / 2.)
            .min(end - (d + gap) - 0.21)
            .min(-width / 2.);
        let max = (end + d / 2.).max(end - (d + gap) + 0.21).max(width / 2.);
        let outer = height / 2. + gap + d + controls.offset_m[1] + 0.079;
        Some(Self {
            left: if right { min } else { -max },
            right: if right { max } else { -min },
            bottom: if top { -height / 2. } else { -outer },
            top: if top { outer } else { height / 2. },
        })
    }
    pub fn valid(self) -> bool {
        [self.left, self.right, self.bottom, self.top]
            .into_iter()
            .all(f64::is_finite)
            && self.left < self.right
            && self.bottom < self.top
    }
    /// 装飾canvasの透明余白も含めたい場合は採用済みpanel planの実寸を合成する。
    pub fn with_canvas(
        self,
        body: crate::resize::PhysicalSize,
        content: crate::window::LogicalSize,
        canvas: crate::window::LogicalSize,
        origin: [u32; 2],
    ) -> Option<Self> {
        if !self.valid()
            || !body.valid()
            || content.width == 0
            || content.height == 0
            || origin[0].checked_add(content.width)? > canvas.width
            || origin[1].checked_add(content.height)? > canvas.height
        {
            return None;
        }
        let w = f64::from(body.width_m);
        let h = f64::from(body.height_m);
        let left = -w / 2. - f64::from(origin[0]) / f64::from(content.width) * w;
        let top = h / 2. + f64::from(origin[1]) / f64::from(content.height) * h;
        let right = left + f64::from(canvas.width) / f64::from(content.width) * w;
        let bottom = top - f64::from(canvas.height) / f64::from(content.height) * h;
        let combined = Self {
            left: self.left.min(left),
            right: self.right.max(right),
            bottom: self.bottom.min(bottom),
            top: self.top.max(top),
        };
        combined.valid().then_some(combined)
    }
    pub fn offset(self, position: Position, gutter: f64) -> Option<[f64; 2]> {
        if !self.valid() || !gutter.is_finite() || !(0.02..=0.5).contains(&gutter) {
            return None;
        }
        let [x, y] = position.grid();
        Some([
            f64::from(x) * (self.right - self.left + gutter),
            f64::from(y) * (self.top - self.bottom + gutter),
        ])
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn request(n: i64) -> CommandSeq {
        CommandSeq::new(n).unwrap()
    }
    fn window(n: i64) -> WindowId {
        WindowId::new(n).unwrap()
    }
    fn board() -> Board {
        Board::new(RuntimeHandle::new(1).unwrap(), SessionId::new(1).unwrap())
    }
    fn reserve(b: &mut Board, n: i64) -> Lease {
        let Reserved::New(l) = b.reserve(request(n)).unwrap() else {
            panic!("new lease required")
        };
        l
    }
    #[test]
    fn nine_acceptances_reserve_priority_before_reverse_completions() {
        let mut b = board();
        let leases: Vec<_> = (1..=9).map(|n| reserve(&mut b, n)).collect();
        for (index, lease) in leases.iter().enumerate() {
            assert_eq!(lease.position(), Position::ORDER[index]);
            b.enqueued(*lease).unwrap();
        }
        assert_eq!(b.reserve(request(10)), Err(Error::Full));
        assert_eq!(b.occupied(), 9);
        for (index, lease) in leases.iter().enumerate().rev() {
            b.bind(*lease, window(index as i64 + 1)).unwrap();
        }
        for (index, entry) in b.snapshot().iter().enumerate() {
            assert_eq!(
                entry.state,
                Phase::Bound {
                    window: window(index as i64 + 1)
                }
            );
        }
    }
    #[test]
    fn pre_enqueue_cancel_reuses_hole_without_moving_other_windows_or_old_lease() {
        let mut b = board();
        let a = reserve(&mut b, 1);
        let other = reserve(&mut b, 2);
        b.enqueued(other).unwrap();
        b.bind(other, window(2)).unwrap();
        assert_eq!(b.cancel(a), Ok(Cancelled::Released));
        let next = reserve(&mut b, 3);
        assert_eq!(next.position(), Position::Center);
        assert_eq!(b.bind(a, window(1)), Err(Error::StaleLease));
        assert_eq!(b.get(other).unwrap().lease.position(), Position::Right);
        assert_eq!(b.occupied(), 2);
    }
    #[test]
    fn accepted_cancel_retains_capacity_until_remote_and_native_drain_ack() {
        let mut b = board();
        let a = reserve(&mut b, 1);
        b.enqueued(a).unwrap();
        let Cancelled::Awaiting(retiring) = b.cancel(a).unwrap() else {
            panic!()
        };
        assert_eq!(b.bind(a, window(1)), Err(Error::CleaningUp));
        assert_eq!(b.reserve(request(1)), Err(Error::CleaningUp));
        assert_eq!(b.acknowledge(retiring, Ack::Sdk), Err(Error::UnexpectedAck));
        assert_eq!(b.acknowledge(retiring, Ack::Remote), Ok(Released::Waiting));
        assert_eq!(b.occupied(), 1);
        assert_eq!(b.acknowledge(retiring, Ack::Native), Ok(Released::Complete));
        let next = reserve(&mut b, 2);
        assert_eq!(next.position(), a.position());
        assert_ne!(next, a);
        assert_eq!(b.acknowledge(retiring, Ack::Native), Err(Error::StaleLease));
    }
    #[test]
    fn closed_window_waits_for_both_native_and_sdk_ack_in_either_order() {
        for order in [[Ack::Native, Ack::Sdk], [Ack::Sdk, Ack::Native]] {
            let mut b = board();
            let a = reserve(&mut b, 1);
            b.enqueued(a).unwrap();
            b.bind(a, window(1)).unwrap();
            assert_eq!(b.cancel(a), Err(Error::WindowAlive));
            let ticket = b.window_gone(a, window(1)).unwrap();
            assert_eq!(b.acknowledge(ticket, order[0]), Ok(Released::Waiting));
            assert_eq!(b.acknowledge(ticket, order[0]), Ok(Released::Waiting));
            assert_eq!(b.acknowledge(ticket, order[1]), Ok(Released::Complete));
            assert_eq!(b.occupied(), 0);
        }
    }
    #[test]
    fn duplicate_request_and_window_cannot_allocate_or_replace_an_owner() {
        let mut b = board();
        let a = reserve(&mut b, 1);
        assert_eq!(b.reserve(request(1)), Ok(Reserved::Existing(a)));
        assert_eq!(b.bind(a, window(1)), Err(Error::NotEnqueued));
        b.enqueued(a).unwrap();
        b.bind(a, window(1)).unwrap();
        b.bind(a, window(1)).unwrap();
        assert_eq!(b.bind(a, window(2)), Err(Error::AlreadyBound));
        let other = reserve(&mut b, 2);
        b.enqueued(other).unwrap();
        assert_eq!(b.bind(other, window(1)), Err(Error::DuplicateWindow));
        assert_eq!(b.occupied(), 2);
    }
    #[test]
    fn unrequested_dialog_uses_unreserved_capacity_and_never_steals_launch_slot() {
        let mut b = board();
        let launch = reserve(&mut b, 1);
        let dialog = b.adopt_unrequested(window(9)).unwrap().lease();
        assert_eq!(dialog.position(), Position::Right);
        assert_eq!(
            b.adopt_unrequested(window(9)),
            Ok(Reserved::Existing(dialog))
        );
        for n in 2..=8 {
            reserve(&mut b, n);
        }
        assert_eq!(b.adopt_unrequested(window(10)), Err(Error::Full));
        b.enqueued(launch).unwrap();
        b.bind(launch, window(1)).unwrap();
        assert_eq!(b.occupied(), 9);
    }
    #[test]
    fn stale_epoch_drain_request_and_generation_do_not_affect_new_reservations() {
        let mut b = board();
        let a = reserve(&mut b, 1);
        b.enqueued(a).unwrap();
        b.bind(a, window(1)).unwrap();
        let ticket = b.window_gone(a, window(1)).unwrap();
        let mut forged = ticket;
        forged.drain = forged.drain.checked_next().unwrap();
        assert_eq!(b.acknowledge(forged, Ack::Sdk), Err(Error::StaleLease));
        let mut fresh = Board::new(RuntimeHandle::new(2).unwrap(), SessionId::new(1).unwrap());
        let next = reserve(&mut fresh, 1);
        assert_eq!(fresh.bind(a, window(1)), Err(Error::StaleLease));
        assert_eq!(
            fresh.acknowledge(ticket, Ack::Native),
            Err(Error::StaleLease)
        );
        assert_eq!(fresh.occupied(), 1);
        assert_eq!(next.position(), a.position());
    }
    #[test]
    fn retired_or_out_of_order_request_is_not_reexecuted_and_ids_never_wrap() {
        let mut b = board();
        let a = reserve(&mut b, 5);
        b.cancel(a).unwrap();
        assert_eq!(b.reserve(request(5)), Err(Error::StaleRequest));
        assert_eq!(b.reserve(request(4)), Err(Error::StaleRequest));
        b.next_token = i64::MAX;
        assert_eq!(b.reserve(request(6)), Err(Error::IdExhausted));
        assert_eq!(b.occupied(), 0);
    }
    #[test]
    fn grid_order_matches_user_priority_and_uses_shared_local_plane() {
        assert_eq!(
            Position::ORDER.map(Position::grid),
            [
                [0, 0],
                [1, 0],
                [-1, 0],
                [0, 1],
                [0, -1],
                [-1, 1],
                [1, 1],
                [-1, -1],
                [1, -1]
            ]
        );
        let c = crate::window_controls::WindowControls::default();
        let e = Envelope::for_window(1.2, 0.9, &c).unwrap();
        assert_eq!(e.offset(Position::Center, 0.08), Some([0., 0.]));
        assert!(e.offset(Position::Right, 0.08).unwrap()[0] > 1.3);
        assert!(e.offset(Position::Up, 0.08).unwrap()[1] > 1.1);
    }
    #[test]
    fn body_three_buttons_and_mode_envelopes_do_not_overlap_for_all_corners_and_sizes() {
        use crate::window_controls::{ControlAnchor, WindowControls};
        for anchor in [
            ControlAnchor::TopLeft,
            ControlAnchor::TopRight,
            ControlAnchor::BottomLeft,
            ControlAnchor::BottomRight,
        ] {
            for size in [0.25, 1.2, 3.] {
                let e = Envelope::for_window(
                    size,
                    size,
                    &WindowControls {
                        anchor,
                        ..Default::default()
                    },
                )
                .unwrap();
                let points = Position::ORDER.map(|p| e.offset(p, 0.08).unwrap());
                for (i, a) in points.iter().enumerate() {
                    for b in points.iter().skip(i + 1) {
                        assert!(
                            a[0] + e.right < b[0] + e.left
                                || b[0] + e.right < a[0] + e.left
                                || a[1] + e.top < b[1] + e.bottom
                                || b[1] + e.top < a[1] + e.bottom
                        );
                    }
                }
            }
        }
        assert!(Envelope::for_window(f64::NAN, 1., &Default::default()).is_none());
        let e = Envelope::for_window(1., 1., &Default::default()).unwrap();
        assert!(e.offset(Position::Up, 0.).is_none());
    }
    #[test]
    fn adopted_decoration_canvas_can_expand_spacing_without_touching_body_size() {
        use crate::{resize::PhysicalSize, window::LogicalSize};
        let e = Envelope::for_window(1.2, 0.9, &Default::default()).unwrap();
        let expanded = e
            .with_canvas(
                PhysicalSize::default(),
                LogicalSize::new(1024, 768).unwrap(),
                LogicalSize::new(1280, 1280).unwrap(),
                [128, 256],
            )
            .unwrap();
        assert!((expanded.left + 0.75).abs() < 0.000001 && (expanded.top - 0.75).abs() < 0.000001);
        assert!(expanded.offset(Position::Up, 0.08).unwrap()[1] > 1.57);
        assert!(
            e.with_canvas(
                PhysicalSize::default(),
                LogicalSize::new(1024, 768).unwrap(),
                LogicalSize::new(1280, 1280).unwrap(),
                [257, 256]
            )
            .is_none()
        );
        assert!(
            Envelope {
                left: 1.,
                right: 0.,
                bottom: 0.,
                top: 1.
            }
            .offset(Position::Center, 0.08)
            .is_none()
        );
    }
}
