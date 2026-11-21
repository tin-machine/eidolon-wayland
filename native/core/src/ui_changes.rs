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
        Self {
            revision: Mutex::new(1),
            changed: Condvar::new(),
        }
    }

    fn publish(&self) {
        let mut revision = self.revision.lock().unwrap_or_else(|e| e.into_inner());
        *revision = revision.wrapping_add(1);
        self.changed.notify_all();
    }

    fn wait(&self, previous: i64, timeout: Duration) -> i64 {
        let revision = self.revision.lock().unwrap_or_else(|e| e.into_inner());
        let (revision, _) = self
            .changed
            .wait_timeout_while(revision, timeout, |r| *r == previous)
            .unwrap_or_else(|e| e.into_inner());
        *revision
    }
}

static CHANGES: Changes = Changes::new();

pub(crate) fn publish() {
    CHANGES.publish()
}

pub fn wait(previous: i64, timeout: Duration) -> i64 {
    CHANGES.wait(previous, timeout)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn waking_waiters_does_not_require_polling_or_consuming_an_event() {
        let changes = Changes::new();
        let ready = std::sync::Barrier::new(3);
        let baseline = changes.wait(0, Duration::ZERO);
        std::thread::scope(|scope| {
            let read = || {
                ready.wait();
                changes.wait(baseline, Duration::from_secs(2))
            };
            let host = scope.spawn(read);
            let panel = scope.spawn(read);
            ready.wait();
            changes.publish();
            let latest = changes.wait(baseline, Duration::ZERO);
            assert_ne!(latest, baseline);
            assert_eq!(host.join().unwrap(), latest);
            assert_eq!(panel.join().unwrap(), latest);
        });
    }

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
