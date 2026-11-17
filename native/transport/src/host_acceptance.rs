//! Read-only, default-off Host observations. This trace grants no product authority.
//! A contended/full trace drops evidence and stays incomplete; it never blocks SSH.
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::sync::{
    Arc, Mutex, MutexGuard, TryLockError,
    atomic::{AtomicBool, Ordering},
};

pub const MAX_EVENTS: usize = 4096;
pub const MAX_BYTES: usize = 1024 * 1024;

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct Capture {
    pub session: String,
    pub inspect_request: String,
    pub io_generation: String,
    pub nonce: String,
    pub scope: String,
    pub context_digest: String,
    pub capture_id: String,
}
impl Capture {
    pub(crate) fn from_run(run: &crate::linux_desktop::v3::runtime::RunCapture) -> Self {
        let binding = run.binding();
        let context_digest = binding.context().sha256();
        let input = serde_json::json!({
            "context":binding.context().record(), "pins":binding.pins().record(),
            "sources":run.evidence().source_hashes(),
            "inspect_request":run.inspect_request, "generation":run.generation.to_string()
        });
        let canonical = crate::linux_desktop::v3::canonical(&input).expect("validated capture");
        Self {
            session: binding.session().get().to_string(),
            inspect_request: run.inspect_request.get().to_string(),
            io_generation: run.generation.to_string(),
            nonce: binding.nonce(),
            scope: binding.scope(),
            context_digest,
            capture_id: Sha256::digest(canonical)
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect(),
        }
    }
}

#[derive(Clone, Serialize)]
pub struct Event {
    pub sequence: String,
    pub kind: &'static str,
    pub capture: Option<Capture>,
    pub data: Value,
}
#[derive(Clone, Serialize)]
pub struct Snapshot {
    pub overflow: bool,
    pub events: Vec<Event>,
}
#[derive(Default)]
struct Entries {
    // Cheap Arc references keep Snapshot's deep clone outside the producer
    // lock. Host polling may request many snapshots during long idle runs.
    events: Vec<Arc<Event>>,
    bytes: usize,
    current: Option<Capture>,
}
#[derive(Default)]
pub struct Trace {
    entries: Mutex<Entries>,
    overflow: AtomicBool,
}
impl Trace {
    /// A bounded, nonblocking retry protects producer evidence from a reader
    /// taking a very short snapshot of the Arc handles. Continuous contention,
    /// poison and any capacity failure still fail closed with sticky overflow.
    /// This is Host-only observation; the product SSH/Controller does not wait
    /// indefinitely on this mutex or downgrade a lost event to success.
    fn try_entries(&self) -> Option<MutexGuard<'_, Entries>> {
        for _ in 0..16 {
            match self.entries.try_lock() {
                Ok(entries) => return Some(entries),
                Err(TryLockError::Poisoned(_)) => break,
                Err(TryLockError::WouldBlock) => std::thread::yield_now(),
            }
        }
        self.overflow.store(true, Ordering::Release);
        None
    }

    fn append_locked(
        &self,
        entries: &mut Entries,
        capture: Option<Capture>,
        kind: &'static str,
        data: Value,
    ) {
        let event = Event {
            sequence: (entries.events.len() + 1).to_string(),
            kind,
            capture,
            data,
        };
        let bytes = serde_json::to_vec(&event).map_or(MAX_BYTES + 1, |bytes| bytes.len());
        if bytes > 65536
            || entries.events.len() >= MAX_EVENTS
            || entries.bytes.saturating_add(bytes) > MAX_BYTES
        {
            self.overflow.store(true, Ordering::Release);
            return;
        }
        entries.bytes += bytes;
        if event.capture.is_some() {
            entries.current = event.capture.clone();
        }
        entries.events.push(Arc::new(event));
    }

    /// Internal product owners only. No public mutation or deserialization API.
    pub(crate) fn record(&self, capture: Option<Capture>, kind: &'static str, data: Value) {
        if let Some(mut entries) = self.try_entries() {
            self.append_locked(&mut entries, capture, kind, data);
        }
    }

    /// Preserve the exact last captured owner and append under ONE lock.
    /// Previously a two-lock read/append gap could lose the stop observation
    /// when an asynchronous cleanup event arrived between the two try_locks.
    pub(crate) fn record_current(&self, kind: &'static str, data: Value) {
        if let Some(mut entries) = self.try_entries() {
            let current = entries.current.clone();
            self.append_locked(&mut entries, current, kind, data);
        }
    }

    pub fn snapshot(&self) -> Snapshot {
        // Do not clone the potentially 1 MiB deep event payload while
        // holding the writer mutex. Only Arc pointers are cloned here.
        let events = self
            .entries
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .events
            .clone();
        Snapshot {
            overflow: self.overflow.load(Ordering::Acquire),
            events: events.iter().map(|event| (**event).clone()).collect(),
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn overflow_and_contention_are_sticky_and_never_change_product_state() {
        let trace = Trace::default();
        let lock = trace.entries.lock().unwrap();
        trace.record(None, "test", serde_json::json!({}));
        drop(lock);
        assert!(trace.snapshot().overflow);
        assert!(trace.snapshot().events.is_empty());
        for _ in 0..MAX_EVENTS + 1 {
            trace.record(None, "test", serde_json::json!({}));
        }
        let snapshot = trace.snapshot();
        assert_eq!(snapshot.events.len(), MAX_EVENTS);
        assert!(snapshot.overflow);
        assert_eq!(
            snapshot.events.last().unwrap().sequence,
            MAX_EVENTS.to_string()
        );
    }
    #[test]
    fn snapshot_and_current_capture_do_not_share_mutable_event_storage() {
        let trace = Trace::default();
        let capture = Capture {
            session: "1".into(),
            inspect_request: "20".into(),
            io_generation: "1".into(),
            nonce: "1111111111111111".into(),
            scope: "capture".into(),
            context_digest: "a".repeat(64),
            capture_id: "b".repeat(64),
        };
        trace.record(
            Some(capture.clone()),
            "created",
            serde_json::json!({"stage":1}),
        );
        let before = trace.snapshot();
        trace.record_current("controller_stop_requested", serde_json::json!({}));
        let after = trace.snapshot();
        assert!(!after.overflow);
        assert_eq!(before.events.len(), 1);
        assert_eq!(after.events.len(), 2);
        assert_eq!(after.events[0].capture, Some(capture.clone()));
        assert_eq!(after.events[1].capture, Some(capture));
        assert_eq!(after.events[1].sequence, "2");
        assert_eq!(after.events[1].kind, "controller_stop_requested");
    }

    #[test]
    fn overflowing_after_maximum_bytes_or_events_still_fails_closed() {
        let trace = Trace::default();
        for i in 0..MAX_EVENTS {
            trace.record(None, "test", serde_json::json!({"value":i}));
        }
        assert!(!trace.snapshot().overflow);
        trace.record_current("excess", serde_json::json!({}));
        let snap = trace.snapshot();
        assert_eq!(snap.events.len(), MAX_EVENTS);
        assert!(snap.overflow);
    }

    #[test]
    fn oversized_event_is_not_admitted() {
        let trace = Trace::default();
        trace.record(None, "test", serde_json::json!({"bytes":"x".repeat(65537)}));
        assert!(trace.snapshot().overflow);
        assert!(trace.snapshot().events.is_empty());
    }
}
