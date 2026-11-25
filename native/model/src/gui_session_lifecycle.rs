use crate::gui_session_policy::GuiUnresponsiveGrace;
use std::{num::NonZeroU64, time::Duration};

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct LivenessTick {
    pub owner: NonZeroU64,
    pub sequence: NonZeroU64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum StopReason {
    Requested,
    LastWindowClosed,
    TransferLost,
    LeaseExpired,
    StartupFailed,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Phase {
    Starting,
    Running,
    Waiting,
    Stopping(StopReason),
    Reconcile(StopReason),
    Retired(StopReason),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Renewal {
    Apply,
    Probe,
    Ignore,
    Stop(StopReason),
}

/// Lifetime of one generation. Times are samples from the host lease's monotonic clock.
/// An actor checks the host's current ownership and timer state before executing a renewal.
pub struct GuiSessionLifecycle {
    generation: [u8; 16],
    grace: Duration,
    deadline: Duration,
    activity_owner: NonZeroU64,
    activity_visible: bool,
    last_tick: Option<LivenessTick>,
    in_flight: Option<LivenessTick>,
    needs_probe: bool,
    local_retired: bool,
    remote_retired: bool,
    phase: Phase,
}

impl GuiSessionLifecycle {
    pub fn armed(
        generation: [u8; 16],
        grace: GuiUnresponsiveGrace,
        armed_at: Duration,
        activity_owner: NonZeroU64,
    ) -> Self {
        let grace = Duration::from_secs(u64::from(grace.seconds()));
        Self {
            generation,
            grace,
            deadline: armed_at.saturating_add(grace),
            activity_owner,
            activity_visible: true,
            last_tick: None,
            in_flight: None,
            needs_probe: false,
            local_retired: false,
            remote_retired: false,
            phase: Phase::Starting,
        }
    }

    pub fn phase(&self) -> Phase {
        self.phase
    }

    pub fn observe_deadline(&mut self, host_now: Duration) -> Option<StopReason> {
        if self.in_flight.is_some() || self.needs_probe || host_now < self.deadline {
            return None;
        }
        self.stop(self.generation, StopReason::LeaseExpired)
            .then_some(StopReason::LeaseExpired)
    }

    pub fn adopt_display(&mut self, host_now: Duration) -> bool {
        self.observe_deadline(host_now);
        if self.phase != Phase::Starting {
            return false;
        }
        self.phase = if self.activity_visible {
            Phase::Running
        } else {
            Phase::Waiting
        };
        true
    }

    pub fn activity(&mut self, owner: NonZeroU64, visible: bool) {
        if owner < self.activity_owner
            || !matches!(
                self.phase,
                Phase::Starting | Phase::Running | Phase::Waiting
            )
        {
            return;
        }
        self.activity_owner = owner;
        self.activity_visible = visible;
        if !visible && self.phase == Phase::Running {
            self.phase = Phase::Waiting;
        }
    }

    pub fn request_renewal(
        &mut self,
        generation: [u8; 16],
        tick: LivenessTick,
        host_now: Duration,
    ) -> Renewal {
        if generation != self.generation || tick.owner != self.activity_owner {
            return Renewal::Ignore;
        }
        if let Some(reason) = self.observe_deadline(host_now) {
            return Renewal::Stop(reason);
        }
        if !matches!(self.phase, Phase::Running | Phase::Waiting)
            || !self.activity_visible
            || self.in_flight.is_some()
            || self.last_tick.is_some_and(|previous| tick <= previous)
        {
            return Renewal::Ignore;
        }
        if self.needs_probe {
            return Renewal::Probe;
        }
        self.last_tick = Some(tick);
        self.in_flight = Some(tick);
        Renewal::Apply
    }

    /// ACK is specific to the exact update and can only extend a still-live generation.
    /// An ambiguous result requires a fresh host observation before any further change.
    pub fn renewal_result(
        &mut self,
        generation: [u8; 16],
        tick: LivenessTick,
        accepted_host_at: Option<Duration>,
    ) -> bool {
        if (generation, Some(tick)) != (self.generation, self.in_flight) {
            return false;
        }
        self.in_flight = None;
        let Some(at) = accepted_host_at else {
            self.needs_probe = true;
            return false;
        };
        if !matches!(self.phase, Phase::Running | Phase::Waiting)
            || self.observe_deadline(at).is_some()
            || at.saturating_add(self.grace) <= self.deadline
        {
            return false;
        }
        self.deadline = at.saturating_add(self.grace);
        if tick.owner == self.activity_owner && self.activity_visible {
            self.phase = Phase::Running;
        }
        true
    }

    /// Record a confirmed waiting timer for the same host boot and invocation.
    pub fn confirm_waiting_timer(
        &mut self,
        generation: [u8; 16],
        host_now: Duration,
        deadline: Duration,
    ) -> bool {
        if generation != self.generation
            || !self.needs_probe
            || deadline < self.deadline
            || deadline <= host_now
        {
            return false;
        }
        self.deadline = deadline;
        self.needs_probe = false;
        true
    }

    /// True only for the event that first acquired responsibility to stop this generation.
    pub fn stop(&mut self, generation: [u8; 16], reason: StopReason) -> bool {
        if generation != self.generation
            || !matches!(
                self.phase,
                Phase::Starting | Phase::Running | Phase::Waiting
            )
        {
            return false;
        }
        self.in_flight = None;
        self.needs_probe = false;
        self.phase = Phase::Stopping(reason);
        true
    }

    pub fn finish(&mut self, local_retired: bool, remote_retired: bool) -> bool {
        let reason = match self.phase {
            Phase::Stopping(reason) | Phase::Reconcile(reason) => reason,
            _ => return false,
        };
        self.local_retired |= local_retired;
        self.remote_retired |= remote_retired;
        let retired = self.local_retired && self.remote_retired;
        self.phase = if retired {
            Phase::Retired(reason)
        } else {
            Phase::Reconcile(reason)
        };
        retired
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const GENERATION: [u8; 16] = [1; 16];
    const OTHER: [u8; 16] = [2; 16];

    fn t(seconds: u64) -> Duration {
        Duration::from_secs(seconds)
    }

    fn tick(owner: u64, sequence: u64) -> LivenessTick {
        LivenessTick {
            owner: owner.try_into().unwrap(),
            sequence: sequence.try_into().unwrap(),
        }
    }

    fn running() -> GuiSessionLifecycle {
        let mut lease = GuiSessionLifecycle::armed(
            GENERATION,
            GuiUnresponsiveGrace::DEFAULT,
            t(0),
            tick(3, 1).owner,
        );
        assert!(lease.adopt_display(t(1)));
        lease
    }

    #[test]
    fn lost_heartbeat_keeps_transfer_then_resumes_same_generation() {
        let mut lease = running();
        lease.activity(tick(3, 1).owner, false);
        assert_eq!(lease.phase(), Phase::Waiting);
        assert_eq!(lease.observe_deadline(t(3599)), None);
        assert_eq!(
            lease.request_renewal(GENERATION, tick(3, 1), t(3599)),
            Renewal::Ignore
        );
        lease.activity(tick(3, 1).owner, true);
        assert_eq!(
            lease.request_renewal(GENERATION, tick(3, 1), t(3599)),
            Renewal::Apply
        );
        assert!(lease.renewal_result(GENERATION, tick(3, 1), Some(t(3599))));
        assert_eq!(lease.phase(), Phase::Running);
        assert_eq!(lease.observe_deadline(t(3600)), None);
        assert_eq!(lease.observe_deadline(t(7198)), None);
        assert_eq!(
            lease.observe_deadline(t(7199)),
            Some(StopReason::LeaseExpired)
        );
        assert_eq!(lease.observe_deadline(t(7200)), None);
    }

    #[test]
    fn deadline_or_transfer_loss_wins_over_late_ack() {
        for (at, reason) in [
            (t(5), StopReason::TransferLost),
            (t(3600), StopReason::LeaseExpired),
        ] {
            let mut lease = running();
            assert_eq!(
                lease.request_renewal(GENERATION, tick(3, 1), t(2)),
                Renewal::Apply
            );
            assert_eq!(lease.observe_deadline(at), None);
            assert!(lease.stop(GENERATION, reason));
            assert!(!lease.renewal_result(GENERATION, tick(3, 1), Some(t(2))));
            assert_eq!(
                lease.request_renewal(GENERATION, tick(3, 2), at),
                Renewal::Ignore
            );
            assert!(!lease.stop(GENERATION, StopReason::Requested));
            assert_eq!(lease.phase(), Phase::Stopping(reason));
            assert!(!lease.finish(true, false));
            assert_eq!(lease.phase(), Phase::Reconcile(reason));
            assert!(lease.finish(false, true));
            assert_eq!(lease.phase(), Phase::Retired(reason));
        }
    }

    #[test]
    fn old_owner_other_generation_and_duplicate_cannot_extend() {
        let mut lease = running();
        lease.activity(tick(4, 1).owner, false);
        lease.activity(tick(3, 1).owner, true);
        assert_eq!(
            lease.request_renewal(GENERATION, tick(4, 1), t(60)),
            Renewal::Ignore
        );
        lease.activity(tick(4, 1).owner, true);
        assert!(!lease.stop(OTHER, StopReason::Requested));
        assert_eq!(
            lease.request_renewal(GENERATION, tick(3, 8), t(60)),
            Renewal::Ignore
        );
        assert_eq!(
            lease.request_renewal(OTHER, tick(4, 1), t(60)),
            Renewal::Ignore
        );
        assert_eq!(
            lease.request_renewal(GENERATION, tick(4, 1), t(60)),
            Renewal::Apply
        );
        assert!(!lease.renewal_result(GENERATION, tick(4, 1), None));
        assert_eq!(
            lease.request_renewal(GENERATION, tick(4, 1), t(61)),
            Renewal::Ignore
        );
        assert_eq!(lease.observe_deadline(t(3601)), None);
        assert_eq!(
            lease.request_renewal(GENERATION, tick(4, 2), t(3601)),
            Renewal::Probe
        );
        assert!(!lease.confirm_waiting_timer(OTHER, t(3601), t(3650)));
        assert!(!lease.confirm_waiting_timer(GENERATION, t(3601), t(3600)));
        assert!(lease.confirm_waiting_timer(GENERATION, t(3601), t(3650)));
        assert_eq!(
            lease.request_renewal(GENERATION, tick(4, 2), t(3601)),
            Renewal::Apply
        );
        assert!(lease.renewal_result(GENERATION, tick(4, 2), Some(t(3601))));
        assert_eq!(
            lease.observe_deadline(t(7201)),
            Some(StopReason::LeaseExpired)
        );
    }

    #[test]
    fn expiry_during_startup_cannot_adopt_display() {
        let mut lease = GuiSessionLifecycle::armed(
            GENERATION,
            GuiUnresponsiveGrace::try_from(60).unwrap(),
            t(10),
            tick(3, 1).owner,
        );
        assert!(!lease.adopt_display(t(70)));
        assert_eq!(lease.phase(), Phase::Stopping(StopReason::LeaseExpired));
    }
}
