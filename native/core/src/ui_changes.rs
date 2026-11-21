//! Shared invalidation for Android's independent host and SDK readers.
//! A wake gives no authority over a runtime. Each reader revalidates its own handles.
use std::sync::{Condvar, Mutex};
use std::time::Duration;

struct Changes {
    revision: Mutex<i64>,
    changed: Condvar,
}

impl Changes {
    const fn new() -> Self {
        Self { revision: Mutex::new(1), changed: Condvar::new() }
    }

    fn publish(&self) {
        let mut revision = self.revision.lock().unwrap_or_else(|e| e.into_inner());
        *revision = revision.wrapping_add(1);
        self.changed.notify_all();
    }

    fn wait(&self, previous: i64, timeout: Duration) -> i64 {
        let revision = self.revision.lock().unwrap_or_else(|e| e.into_inner());
        let (revision, _) = self.changed.wait_timeout_while(revision, timeout, |r| *r == previous)
            .unwrap_or_else(|e| e.into_inner());
        *revision
    }
}

static CHANGES: Changes = Changes::new();

pub(crate) fn publish() { CHANGES.publish() }

pub fn wait(previous: i64, timeout: Duration) -> i64 { CHANGES.wait(previous, timeout) }

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn readers_can_catch_up_independently_after_coalesced_changes() {
        let changes = Changes::new();
        let start = changes.wait(0, Duration::ZERO);
        changes.publish();
        changes.publish();
        let latest = changes.wait(start, Duration::ZERO);
        assert_ne!(latest, start);
        assert_eq!(changes.wait(start, Duration::ZERO), latest);
        assert_eq!(changes.wait(latest, Duration::from_millis(1)), latest);
    }
}
