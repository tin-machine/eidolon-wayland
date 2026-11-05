//! configure serialは不透明なu32。送信順のticketとroot contentでack/画像commitを区別する。
use crate::{
    ids::{CommandSeq, ContentSeq},
    window::{BufferSizePx, LogicalSize},
};
use serde::{Deserialize, Serialize};
use std::{collections::VecDeque, time::Duration};

pub fn logical_size(width: u32, height: u32) -> Result<LogicalSize, &'static str> {
    if !(320..=2048).contains(&width) || !(200..=2048).contains(&height) {
        return Err("INVALID_ARGUMENT");
    }
    LogicalSize::new(width, height).map_err(|_| "INVALID_ARGUMENT")
}

#[derive(Clone, Copy, Debug, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PhysicalSize {
    pub width_m: f32,
    pub height_m: f32,
}
impl Default for PhysicalSize {
    fn default() -> Self {
        Self {
            width_m: 1.2,
            height_m: 0.9,
        }
    }
}
impl PhysicalSize {
    pub fn valid(self) -> bool {
        [self.width_m, self.height_m]
            .into_iter()
            .all(|v| v.is_finite() && (0.25..=3.).contains(&v))
    }
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct Sizing {
    pub physical_size_m: PhysicalSize,
    pub applied_physical_size_m: Option<PhysicalSize>,
    pub size_command: Option<CommandSeq>,
    pub producer_size_px: Option<BufferSizePx>,
}
impl Sizing {
    pub fn request(&mut self, request: CommandSeq, size: PhysicalSize) -> Result<(), &'static str> {
        if !size.valid() {
            return Err("INVALID_ARGUMENT");
        }
        self.physical_size_m = size;
        self.size_command = Some(request);
        self.applied_physical_size_m = None;
        Ok(())
    }
    pub fn applied(
        &mut self,
        command: Option<CommandSeq>,
        size: PhysicalSize,
    ) -> Result<(), &'static str> {
        if self.size_command != command || self.physical_size_m != size {
            return Err("STALE");
        }
        self.applied_physical_size_m = Some(size);
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    #[default]
    Idle,
    Queued,
    AwaitingAck,
    AwaitingCommit,
    Applied,
    Adjusted,
    TimedOut,
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub requested_logical_size: Option<LogicalSize>,
    pub configured_serial: Option<u32>,
    pub configured_logical_size: Option<LogicalSize>,
    pub acknowledged_serial: Option<u32>,
    pub committed_serial: Option<u32>,
    pub committed_size: Option<LogicalSize>,
    pub phase: Phase,
    pub error: Option<&'static str>,
}
#[derive(Clone, Copy)]
struct Ticket {
    serial: u32,
    generation: u64,
    size: LogicalSize,
    baseline: Option<ContentSeq>,
}
struct Flight {
    ticket: Ticket,
    since: Duration,
    acked: bool,
}
#[derive(Default)]
pub struct ResizeState {
    report: Report,
    generation: u64,
    applied: u64,
    timed_out: Option<u64>,
    flight: Option<Flight>,
    tickets: VecDeque<Ticket>,
    committed_content: Option<ContentSeq>,
}
impl ResizeState {
    pub const TIMEOUT: Duration = Duration::from_secs(5);
    const TICKETS: usize = 64;
    pub fn report(&self) -> Report {
        self.report.clone()
    }
    pub fn request(&mut self, size: LogicalSize) -> Result<(), &'static str> {
        logical_size(size.width, size.height)?;
        if self.tickets.len() >= Self::TICKETS {
            return Err("RESOURCE_LIMIT");
        }
        if self.report.requested_logical_size == Some(size)
            && !matches!(self.report.phase, Phase::TimedOut | Phase::Adjusted)
        {
            return Ok(());
        }
        self.generation = self.generation.checked_add(1).ok_or("ID_EXHAUSTED")?;
        self.report.requested_logical_size = Some(size);
        self.report.error = None;
        if self.flight.is_none() && self.report.committed_size == Some(size) {
            self.applied = self.generation;
        }
        self.update_phase();
        Ok(())
    }
    pub fn next_size(&self) -> Option<LogicalSize> {
        (self.tickets.len() < Self::TICKETS
            && self.flight.is_none()
            && self.applied != self.generation
            && self.timed_out != Some(self.generation))
        .then_some(self.report.requested_logical_size)
        .flatten()
    }
    pub fn sent(
        &mut self,
        serial: u32,
        content: Option<ContentSeq>,
        now: Duration,
    ) -> Result<(), &'static str> {
        let size = self.next_size().ok_or("NOT_READY")?;
        let ticket = Ticket {
            serial,
            generation: self.generation,
            size,
            baseline: content,
        };
        self.push(ticket)?;
        self.flight = Some(Flight {
            ticket,
            since: now,
            acked: false,
        });
        self.report.configured_serial = Some(serial);
        self.report.configured_logical_size = Some(size);
        self.report.acknowledged_serial = None;
        self.report.error = None;
        self.update_phase();
        Ok(())
    }
    /// Activated等のconfigureも同じsize要求を含む。clientがそのserialだけackしてもよい。
    pub fn alias(&mut self, serial: u32, size: LogicalSize) -> Result<(), &'static str> {
        let ticket = self
            .flight
            .as_ref()
            .map(|f| f.ticket)
            .or_else(|| self.tickets.back().copied());
        if let Some(ticket) = ticket
            && ticket.size == size
            && ticket.generation > self.applied
        {
            self.push(Ticket { serial, ..ticket })?;
        }
        Ok(())
    }
    fn push(&mut self, ticket: Ticket) -> Result<(), &'static str> {
        if self.tickets.len() >= Self::TICKETS
            || self.tickets.iter().any(|t| t.serial == ticket.serial)
        {
            return Err("RESOURCE_LIMIT");
        }
        self.tickets.push_back(ticket);
        Ok(())
    }
    pub fn acknowledge(&mut self, serial: u32) {
        if let Some(index) = self.tickets.iter().position(|t| t.serial == serial) {
            let ticket = self.tickets[index];
            self.report.acknowledged_serial = Some(serial);
            if let Some(flight) = &mut self.flight
                && flight.ticket.generation == ticket.generation
            {
                flight.acked = true;
            }
            self.tickets.drain(..index);
            self.update_phase();
        }
    }
    /// falseなら旧owned snapshotを維持する。ackだけ/空commit/別serialの画像では完了しない。
    pub fn commit(
        &mut self,
        serial: Option<u32>,
        content: Option<ContentSeq>,
        size: LogicalSize,
    ) -> bool {
        if content.is_none() {
            return false;
        }
        let ticket = self
            .tickets
            .iter()
            .position(|t| Some(t.serial) == serial)
            .map(|i| (i, self.tickets[i]));
        if let Some((index, ticket)) = ticket
            && self.report.acknowledged_serial == serial
            && content.is_some()
            && content > ticket.baseline
        {
            self.report.committed_size = Some(size);
            self.report.committed_serial = serial;
            self.committed_content = content;
            self.applied = self.applied.max(ticket.generation);
            if self
                .flight
                .as_ref()
                .is_some_and(|f| f.ticket.generation == ticket.generation)
            {
                self.flight = None;
            }
            self.tickets.drain(..index);
            if self.applied == self.generation {
                self.report.error = None;
            }
            self.update_phase();
            return true;
        }
        let pending = self.flight.is_some() || self.applied != self.generation;
        if !pending || self.report.committed_size == Some(size) {
            self.report.committed_size = Some(size);
            if content != self.committed_content {
                self.report.committed_serial = serial;
                self.committed_content = content;
            }
            self.update_phase();
            return true;
        }
        false
    }
    pub fn tick(&mut self, now: Duration) {
        if let Some(flight) = &self.flight
            && now.saturating_sub(flight.since) >= Self::TIMEOUT
        {
            self.timed_out = Some(flight.ticket.generation);
            self.flight = None;
            self.report.error = Some("RESIZE_TIMEOUT");
            self.update_phase();
        }
    }
    pub fn unmap(&mut self) {
        *self = Self::default();
    }
    fn update_phase(&mut self) {
        self.report.phase = if let Some(flight) = &self.flight {
            if flight.ticket.generation != self.generation {
                Phase::Queued
            } else if flight.acked {
                Phase::AwaitingCommit
            } else {
                Phase::AwaitingAck
            }
        } else if self.timed_out == Some(self.generation) && self.applied != self.generation {
            Phase::TimedOut
        } else if self.applied != self.generation {
            Phase::Queued
        } else if self.report.requested_logical_size.is_some() {
            if self.report.requested_logical_size == self.report.committed_size {
                Phase::Applied
            } else {
                Phase::Adjusted
            }
        } else {
            Phase::Idle
        };
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn size(w: u32, h: u32) -> LogicalSize {
        LogicalSize::new(w, h).unwrap()
    }
    fn content(n: i64) -> Option<ContentSeq> {
        Some(ContentSeq::new(n).unwrap())
    }
    #[test]
    fn ack_empty_commit_and_wrong_image_keep_previous_size() {
        let mut state = ResizeState::default();
        assert!(state.commit(Some(10), content(1), size(640, 480)));
        state.request(size(800, 600)).unwrap();
        state.sent(11, content(1), Duration::ZERO).unwrap();
        assert!(!state.commit(Some(10), content(2), size(800, 600)));
        state.acknowledge(11);
        assert_eq!(state.report().phase, Phase::AwaitingCommit);
        assert!(state.commit(Some(11), content(1), size(640, 480)));
        assert_eq!(state.report().committed_serial, Some(10));
        assert_eq!(state.report().phase, Phase::AwaitingCommit);
        assert!(!state.commit(Some(11), content(1), size(800, 600)));
        assert!(state.commit(Some(11), content(2), size(799, 600)));
        assert_eq!(state.report().phase, Phase::Adjusted);
        assert_eq!(state.report().requested_logical_size, Some(size(800, 600)));
        assert_eq!(state.report().committed_size, Some(size(799, 600)));
        assert!(state.commit(Some(11), content(3), size(800, 600)));
        assert_eq!(state.report().phase, Phase::Applied);
    }
    #[test]
    fn coalescing_wrap_focus_alias_and_late_old_ack_never_complete_new_request() {
        let mut state = ResizeState::default();
        state.commit(None, content(1), size(640, 480));
        state.request(size(800, 600)).unwrap();
        state.sent(u32::MAX, content(1), Duration::ZERO).unwrap();
        state.request(size(900, 700)).unwrap();
        state.request(size(1000, 800)).unwrap();
        assert_eq!(state.next_size(), None);
        state.acknowledge(u32::MAX);
        assert!(state.commit(Some(u32::MAX), content(2), size(800, 600)));
        assert_eq!(state.next_size(), Some(size(1000, 800)));
        state.sent(0, content(2), Duration::from_secs(1)).unwrap();
        state.acknowledge(u32::MAX);
        assert!(state.commit(Some(u32::MAX), content(3), size(1000, 800)));
        assert_eq!(state.report().phase, Phase::AwaitingAck);
        state.alias(1, size(1000, 800)).unwrap();
        state.acknowledge(1);
        assert!(state.commit(Some(1), content(3), size(1000, 800)));
        assert_eq!(state.report().committed_serial, Some(1));
        assert_eq!(state.report().phase, Phase::Applied);
    }
    #[test]
    fn timeout_retains_image_and_accepts_late_matching_commit_or_new_request() {
        let mut state = ResizeState::default();
        state.commit(None, content(1), size(640, 480));
        state.request(size(800, 600)).unwrap();
        state.sent(9, content(1), Duration::ZERO).unwrap();
        state.tick(Duration::from_secs(5));
        assert_eq!(state.report().phase, Phase::TimedOut);
        assert_eq!(state.report().committed_size, Some(size(640, 480)));
        assert_eq!(state.next_size(), None);
        state.request(size(900, 700)).unwrap();
        state.sent(10, content(1), Duration::from_secs(6)).unwrap();
        state.acknowledge(9);
        assert!(state.commit(Some(9), content(2), size(800, 600)));
        assert_eq!(state.report().phase, Phase::AwaitingAck);
        state.acknowledge(10);
        assert!(state.commit(Some(10), content(3), size(900, 700)));
        assert_eq!(state.report().phase, Phase::Applied);
        assert_eq!(state.report().error, None);
        state.unmap();
        assert_eq!(state.report().committed_size, None);
        assert_eq!(state.next_size(), None);
        assert!(!state.tickets.iter().any(|t| t.serial == 9));
    }
    #[test]
    fn bounds_and_unresponsive_client_history_are_bounded() {
        for (w, h) in [(319, 200), (320, 199), (2049, 200), (320, 2049)] {
            assert!(logical_size(w, h).is_err());
        }
        assert!(logical_size(320, 200).is_ok());
        assert!(logical_size(2048, 2048).is_ok());
        let mut state = ResizeState::default();
        state.request(size(800, 600)).unwrap();
        state.sent(1, None, Duration::ZERO).unwrap();
        for n in 2..=64 {
            state.alias(n, size(800, 600)).unwrap();
        }
        assert_eq!(state.alias(65, size(800, 600)), Err("RESOURCE_LIMIT"));
        assert_eq!(state.request(size(900, 700)), Err("RESOURCE_LIMIT"));
    }
    #[test]
    fn physical_meters_and_observed_producer_pixels_are_independent_and_ack_is_fenced() {
        let mut sizing = Sizing {
            producer_size_px: Some(BufferSizePx::new(1024, 768).unwrap()),
            ..Default::default()
        };
        let size = PhysicalSize {
            width_m: 1.6,
            height_m: 1.0,
        };
        let id = CommandSeq::new(1).unwrap();
        sizing.request(id, size).unwrap();
        assert_eq!(
            sizing.producer_size_px,
            Some(BufferSizePx::new(1024, 768).unwrap())
        );
        assert_eq!(sizing.applied(None, size), Err("STALE"));
        assert_eq!(
            sizing.applied(Some(id), PhysicalSize::default()),
            Err("STALE")
        );
        sizing.applied(Some(id), size).unwrap();
        for v in [0., 0.249, 3.001, f32::NAN, f32::INFINITY] {
            assert_eq!(
                sizing.request(
                    id,
                    PhysicalSize {
                        width_m: v,
                        height_m: 1.
                    }
                ),
                Err("INVALID_ARGUMENT")
            );
        }
    }
}
