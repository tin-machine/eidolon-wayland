//! Read-only, default-off Host observations. This trace grants no product authority.
//! A contended/full trace drops evidence and stays incomplete; it never blocks SSH.
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::sync::{
    Mutex,
    atomic::{AtomicBool, Ordering},
};

pub const MAX_EVENTS: usize = 4096;
pub const MAX_BYTES: usize = 1024 * 1024;

#[derive(Clone, Serialize, PartialEq, Eq)]
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
    events: Vec<Event>,
    bytes: usize,
    current: Option<Capture>,
}
#[derive(Default)]
pub struct Trace {
    entries: Mutex<Entries>,
    overflow: AtomicBool,
}
impl Trace {
    /// Internal product owners only. No public mutation or deserialization API.
    pub(crate) fn record(&self, capture: Option<Capture>, kind: &'static str, data: Value) {
        let Ok(mut entries) = self.entries.try_lock() else {
            self.overflow.store(true, Ordering::Release);
            return;
        };
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
        entries.events.push(event);
    }
    pub(crate) fn record_current(&self, kind: &'static str, data: Value) {
        let current = match self.entries.try_lock() {
            Ok(entries) => entries.current.clone(),
            Err(_) => {
                self.overflow.store(true, Ordering::Release);
                return;
            }
        };
        self.record(current, kind, data);
    }
    pub fn snapshot(&self) -> Snapshot {
        let entries = self.entries.lock().unwrap_or_else(|e| e.into_inner());
        Snapshot {
            overflow: self.overflow.load(Ordering::Acquire),
            events: entries.events.clone(),
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
    fn oversized_event_is_not_admitted() {
        let trace = Trace::default();
        trace.record(None, "test", serde_json::json!({"bytes":"x".repeat(65537)}));
        assert!(trace.snapshot().overflow);
        assert!(trace.snapshot().events.is_empty());
    }
}
