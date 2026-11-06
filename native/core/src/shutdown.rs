//! 停止要求から所有者の回収までを測る。timeoutで回収済みと偽装しない。
use serde::Serialize;
use std::time::{Duration, Instant};

pub const DEADLINE: Duration = Duration::from_secs(10);

#[derive(Clone, Debug, Serialize)]
pub struct Report {
    pub requested: bool,
    pub complete: bool,
    pub elapsed_ms: u64,
    pub deadline_ms: u64,
    pub timed_out: bool,
    pub pending_owners: Vec<&'static str>,
    pub sdk_bindings: usize,
}
impl Default for Report {
    fn default() -> Self {
        Self {
            requested: false,
            complete: false,
            elapsed_ms: 0,
            deadline_ms: DEADLINE.as_millis() as u64,
            timed_out: false,
            pending_owners: vec![],
            sdk_bindings: 0,
        }
    }
}

#[derive(Default)]
pub struct Watch {
    started: Option<Instant>,
    finished: Option<Duration>,
}
impl Watch {
    pub fn start(&mut self, now: Instant) {
        self.started.get_or_insert(now);
    }
    pub fn finish(&mut self, now: Instant) {
        if let Some(start) = self.started {
            self.finished
                .get_or_insert(now.saturating_duration_since(start));
        }
    }
    pub fn report(&self, now: Instant, pending: Vec<&'static str>, sdk: usize) -> Report {
        let Some(start) = self.started else {
            return Report {
                sdk_bindings: sdk,
                ..Default::default()
            };
        };
        let elapsed = self
            .finished
            .unwrap_or_else(|| now.saturating_duration_since(start));
        Report {
            requested: true,
            complete: self.finished.is_some() && pending.is_empty(),
            elapsed_ms: elapsed.as_millis().try_into().unwrap_or(u64::MAX),
            timed_out: elapsed > DEADLINE,
            pending_owners: pending,
            sdk_bindings: sdk,
            ..Default::default()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn repeated_stop_cannot_extend_deadline_and_late_cleanup_keeps_timeout() {
        let now = Instant::now();
        let mut watch = Watch::default();
        watch.start(now);
        watch.start(now + Duration::from_secs(9));
        let late = now + Duration::from_secs(11);
        let waiting = watch.report(late, vec!["sdk"], 1);
        assert!(waiting.timed_out && !waiting.complete);
        assert_eq!(waiting.pending_owners, ["sdk"]);
        watch.finish(late);
        let done = watch.report(late + Duration::from_secs(60), vec![], 0);
        assert!(done.complete && done.timed_out);
        assert_eq!(done.elapsed_ms, 11000);
    }
    #[test]
    fn completion_requires_all_owners_and_reading_later_does_not_fail_a_timely_stop() {
        let now = Instant::now();
        let mut watch = Watch::default();
        assert!(!watch.report(now, vec![], 0).complete);
        watch.start(now);
        assert!(!watch.report(now, vec![], 0).complete);
        watch.finish(now + Duration::from_millis(100));
        assert!(!watch.report(now, vec!["core"], 0).complete);
        let done = watch.report(now + Duration::from_secs(60), vec![], 0);
        assert!(done.complete && !done.timed_out);
        assert_eq!(done.elapsed_ms, 100);
    }
}
