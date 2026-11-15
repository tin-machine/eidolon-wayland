//! Host-only synthetic SDK owner for a normal v3 native Create plan.
//!
//! This is S evidence, NEVER a Meta SDK Entity. Native issues TargetKey and
//! CreateSeq; neither an external descriptor nor a receipt may construct them.
//! No SDK command here can bypass the ordinary Registry parser/guarded Runtime.
use eidolon_core::runtime::{HostReadbackPoll, HostReadbackTicket, Runtime};
use eidolon_model::{
    commands::PanelAction,
    ids::{CommandSeq, RuntimeHandle},
    state::Status,
    surface::TargetKey,
};
use eidolon_native::registry::Registry;
use eidolon_render::{HostReadbackRequest, HostReadbackRoi, PlatformWindow, RenderMode};
use serde_json::{Value, json};
use std::{
    num::NonZeroU64,
    sync::atomic::{AtomicU64, Ordering},
};

const MAX_OBSERVATIONS: usize = 128;
static NEXT_ADAPTER: AtomicU64 = AtomicU64::new(1);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Phase {
    Reserved,
    Registered,
    Attached,
    Current,
    ReadbackPending,
    Verified,
    AckQueued,
    Applied,
    Retiring,
    Retired,
}

/// Only this adapter can create a producer, seal it, drain owned callbacks and
/// dispose it. These synthetic states cannot be supplied by JSON input.
struct SyntheticEntity {
    sealed: bool,
    callback_producers: usize,
    callbacks_inflight: usize,
    disposed: bool,
}
impl SyntheticEntity {
    fn new() -> Self {
        Self { sealed: false, callback_producers: 1, callbacks_inflight: 0, disposed: false }
    }
    fn callback_start(&mut self) -> bool {
        if self.sealed || self.disposed || self.callback_producers == 0 {
            return false;
        }
        self.callbacks_inflight += 1;
        true
    }
    fn callback_done(&mut self) -> bool {
        if self.callbacks_inflight == 0 {
            return false;
        }
        self.callbacks_inflight -= 1;
        true
    }
    fn seal(&mut self) {
        self.sealed = true;
        self.callback_producers = 0;
    }
    fn dispose(&mut self) -> bool {
        if !self.sealed || self.callbacks_inflight != 0 {
            return false;
        }
        self.disposed = true;
        true
    }
    fn drained(&self) -> bool {
        self.sealed && self.callback_producers == 0 && self.callbacks_inflight == 0 && self.disposed
    }
}

struct Lease {
    target: TargetKey,
    create: CommandSeq,
    phase: Phase,
    entity: Option<SyntheticEntity>,
    ticket: Option<HostReadbackTicket>,
    readback: Option<Value>,
    ack_request: Option<u64>,
}

/// One instance per *actual* Registry Runtime; adapter identity is private and
/// is never read from a fixture/receipt. Failed or pending leases remain owned.
pub struct HostSdkAdapter {
    runtime: i64,
    adapter: u64,
    next_request: u64,
    leases: Vec<Lease>,
    observations: Vec<Value>,
}
impl HostSdkAdapter {
    pub fn new(runtime: i64) -> Result<Self, Status> {
        RuntimeHandle::new(runtime).map_err(|_| Status::InvalidArgument)?;
        let adapter = NEXT_ADAPTER
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |n| n.checked_add(1))
            .map_err(|_| Status::Failed)?;
        Ok(Self { runtime, adapter, next_request: 100, leases: Vec::new(), observations: Vec::new() })
    }

    fn next(&mut self) -> Result<u64, Status> {
        self.next_request = self.next_request.checked_add(1).ok_or(Status::Failed)?;
        Ok(self.next_request)
    }

    fn observe(&mut self, kind: &'static str, data: Value) -> Result<(), Status> {
        if self.observations.len() >= MAX_OBSERVATIONS {
            return Err(Status::Busy);
        }
        self.observations.push(json!({
            "sequence": (self.observations.len() + 1).to_string(),
            "class": "S", "kind": kind, "runtime": self.runtime.to_string(),
            "adapter": self.adapter.to_string(), "data": data,
        }));
        Ok(())
    }

    fn issue(&self, registry: &Registry, value: Value) -> Status {
        registry.command(self.runtime, &value.to_string())
    }

    /// Read the native panel ledger itself. A caller cannot inject a plan,
    /// generation, CreateSeq or successful SDK evidence via a JSON descriptor.
    fn reserve_native_plan(&mut self, registry: &Registry) -> Result<(), Status> {
        let snapshot = registry.guarded(self.runtime, Runtime::snapshot)?;
        for plan in snapshot.panels {
            if plan.action != PanelAction::Create || plan.applied || plan.error.is_some()
                || !plan.v3_evidence_required
            {
                continue;
            }
            if self.leases.iter().any(|lease| lease.target == plan.target) {
                continue;
            }
            if self.leases.len() >= 16 {
                return Err(Status::Busy);
            }
            if snapshot.scene != Some(plan.target.scene) {
                return Err(Status::Stale);
            }
            self.leases.push(Lease {
                target: plan.target,
                create: plan.command_seq,
                phase: Phase::Reserved,
                entity: None,
                ticket: None,
                readback: None,
                ack_request: None,
            });
            self.observe("native_create_reserved", json!({
                "target": plan.target, "create_seq": plan.command_seq,
            }))?;
        }
        Ok(())
    }

    /// Advance only one bounded step; caller must continue ordinary pump/poll.
    /// Returns actual readback after the native Create has been applied.
    pub fn tick_create(
        &mut self, registry: &Registry, completed: &std::collections::BTreeMap<String, Value>,
    ) -> Result<Option<Value>, Status> {
        self.reserve_native_plan(registry)?;
        for i in 0..self.leases.len() {
            let target = self.leases[i].target;
            let create = self.leases[i].create;
            match self.leases[i].phase {
                Phase::Reserved => {
                    let request = self.next()?;
                    let status = self.issue(registry, json!({
                        "type":"register_sdk_binding", "schema":1, "request_id":request.to_string(),
                        "target":target, "create_command":create,
                        "adapter":self.adapter, "diagnostic":false,
                    }));
                    if status != Status::Accepted {
                        return Err(status);
                    }
                    self.leases[i].phase = Phase::Registered;
                    self.leases[i].entity = Some(SyntheticEntity::new());
                    self.observe("registered", json!({"target":target,"create_seq":create,"status":0}))?;
                }
                Phase::Registered => {
                    let snapshot = registry.guarded(self.runtime, Runtime::snapshot)?;
                    let plan = snapshot.panels.iter().find(|plan| {
                        plan.target == target && plan.command_seq == create
                            && plan.action == PanelAction::Create && plan.error.is_none()
                    }).ok_or(Status::Stale)?;
                    let size = plan.decoration.as_ref().map(|layout| layout.canvas)
                        .or_else(|| snapshot.app.as_ref().and_then(|app| app.last_size))
                        .ok_or(Status::NotReady)?;
                    let status = registry.guarded(self.runtime, |runtime| {
                        runtime.attach_window_surface(target, PlatformWindow::default(),
                            size.width as i32, size.height as i32)
                    })?;
                    if status == Status::NotReady {
                        continue;
                    }
                    if status != Status::Accepted {
                        return Err(status);
                    }
                    self.leases[i].phase = Phase::Attached;
                    self.observe("host_surface_attached", json!({
                        "target":target,"create_seq":create,"width":size.width,"height":size.height,
                    }))?;
                }
                Phase::Attached => {
                    let request = self.next()?;
                    let status = self.issue(registry, json!({
                        "type":"sdk_binding_evidence","schema":1,"request_id":request.to_string(),
                        "target":target,"create_command":create,"adapter":self.adapter,"phase":"current",
                    }));
                    if status == Status::NotReady {
                        continue;
                    }
                    if status != Status::Accepted {
                        return Err(status);
                    }
                    self.leases[i].phase = Phase::Current;
                    self.observe("sdk_current", json!({"target":target,"create_seq":create,"status":0}))?;
                }
                Phase::Current => {
                    let snapshot = registry.guarded(self.runtime, Runtime::snapshot)?;
                    let report = match snapshot.render.targets.iter().find(|r| r.key == target
                        && r.role == RenderMode::Windows && !r.presentation_paused
                        && r.content_frame.is_some() && r.frames > 0) {
                        Some(report) => report,
                        None => continue,
                    };
                    let Some(min_content_frame) = report.content_frame else { continue };
                    if report.gpu.width < 4 || report.gpu.height < 4 { return Err(Status::InvalidArgument); }
                    let request_seq = NonZeroU64::new(self.next()?).ok_or(Status::Failed)?;
                    let roi = HostReadbackRoi {
                        id: 1, x: (report.gpu.width / 2 - 2) as u32,
                        y: (report.gpu.height / 2 - 2) as u32, width: 4, height: 4,
                    };
                    let request = HostReadbackRequest {
                        target, min_content_frame, request_seq, rois: vec![roi],
                    };
                    match registry.guarded(self.runtime, |runtime| runtime.request_host_readback(request))? {
                        Ok(ticket) => {
                            self.leases[i].ticket = Some(ticket);
                            self.leases[i].phase = Phase::ReadbackPending;
                        }
                        Err(Status::NotReady | Status::Busy) => continue,
                        Err(status) => return Err(status),
                    }
                }
                Phase::ReadbackPending => {
                    let ticket = self.leases[i].ticket.as_ref().ok_or(Status::Failed)?;
                    let result = registry.guarded(self.runtime, |runtime| runtime.poll_host_readback(ticket))?;
                    match result {
                        Ok(HostReadbackPoll::Pending) | Err(Status::NotReady | Status::Busy) => continue,
                        Err(status) => return Err(status),
                        Ok(HostReadbackPoll::Complete(readback)) => {
                            // No diagnostic or decoration-only frame can satisfy
                            // real-content evidence. Pixel hashes are not semantic goldens.
                            let r = &readback.renderer;
                            if r.layers.is_empty() || r.rois.is_empty()
                                || r.rois.iter().any(|roi| roi.rgba.len() != 4 * 4 * 4)
                                || r.pixel_sha256.len() != r.rois.len()
                                || r.backend.renderer.is_empty() || r.backend.vendor.is_empty()
                                || readback.runtime.get() != self.runtime || readback.create != create
                                || r.target != target
                            {
                                return Err(Status::Failed);
                            }
                            let value = serde_json::to_value(&readback).map_err(|_| Status::Failed)?;
                            self.leases[i].ticket = None;
                            self.leases[i].readback = Some(value.clone());
                            self.leases[i].phase = Phase::Verified;
                            self.observe("real_readback", json!({
                                "target":target,"create_seq":create,"readback":value,
                            }))?;
                        }
                    }
                }
                Phase::Verified => {
                    let request = self.next()?;
                    let status = self.issue(registry, json!({
                        "type":"adapter_applied","schema":1,"request_id":request.to_string(),
                        "command_seq":create,"target":target,"action":"create",
                    }));
                    if status == Status::Busy { continue; }
                    if status != Status::Accepted { return Err(status); }
                    self.leases[i].ack_request = Some(request);
                    self.leases[i].phase = Phase::AckQueued;
                    self.observe("create_queued", json!({
                        "target":target,"create_seq":create,"request_id":request.to_string(),
                    }))?;
                }
                Phase::AckQueued => {
                    let request = self.leases[i].ack_request.ok_or(Status::Failed)?;
                    if completed.get(&request.to_string()) != Some(&json!("ACCEPTED")) {
                        continue;
                    }
                    let snapshot = registry.guarded(self.runtime, Runtime::snapshot)?;
                    let applied = snapshot.panels.iter().any(|panel| {
                        panel.target == target && panel.command_seq == create
                            && panel.action == PanelAction::Create && panel.applied
                    });
                    if !applied { return Err(Status::Stale); }
                    self.leases[i].phase = Phase::Applied;
                    self.observe("create_applied", json!({
                        "target":target,"create_seq":create,"request_id":request.to_string(),
                    }))?;
                    return Ok(self.leases[i].readback.clone());
                }
                Phase::Applied => return Ok(self.leases[i].readback.clone()),
                Phase::Retiring | Phase::Retired => {}
            }
        }
        Ok(None)
    }

    /// The synthetic callback producer is sealed before retirement. Native
    /// holds the authoritative renderer fence and may return NotReady, which
    /// leaves exactly the same lease pending for a later attempt.
    pub fn tick_retire(&mut self, registry: &Registry) -> Result<bool, Status> {
        for i in 0..self.leases.len() {
            if self.leases[i].phase == Phase::Retired { continue; }
            let target = self.leases[i].target;
            let create = self.leases[i].create;
            let entity = self.leases[i].entity.as_mut().ok_or(Status::NotReady)?;
            entity.seal();
            if !entity.dispose() { return Ok(false); }
            if self.leases[i].phase != Phase::Retiring {
                let request = self.next()?;
                let code = self.issue(registry, json!({
                    "type":"sdk_binding_evidence","schema":1,"request_id":request.to_string(),
                    "target":target,"create_command":create,"adapter":self.adapter,"phase":"retiring",
                }));
                if code == Status::NotReady { return Ok(false); }
                if code != Status::Accepted { return Err(code); }
                self.leases[i].phase = Phase::Retiring;
                self.observe("sdk_retiring", json!({"target":target,"create_seq":create}))?;
            }
            let request = self.next()?;
            let code = self.issue(registry, json!({
                "type":"sdk_binding_evidence","schema":1,"request_id":request.to_string(),
                "target":target,"create_command":create,"adapter":self.adapter,"phase":"retired",
            }));
            if code == Status::NotReady { return Ok(false); }
            if code != Status::Accepted { return Err(code); }
            if !self.leases[i].entity.as_ref().is_some_and(SyntheticEntity::drained) {
                return Err(Status::Failed);
            }
            self.leases[i].phase = Phase::Retired;
            self.observe("sdk_retired", json!({"target":target,"create_seq":create,"status":0}))?;
        }
        Ok(self.leases.iter().all(|lease| lease.phase == Phase::Retired))
    }

    pub fn observations(&self) -> &[Value] { &self.observations }
    pub fn owns_lease(&self) -> bool { !self.leases.is_empty() }
    pub fn has_retired_all(&self) -> bool {
        !self.leases.is_empty() && self.leases.iter().all(|l| l.phase == Phase::Retired)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn synthetic_callback_seal_drain_dispose_requires_real_completion() {
        let mut entity = SyntheticEntity::new();
        assert!(entity.callback_start());
        entity.seal();
        assert!(!entity.callback_start());
        assert!(!entity.dispose());
        assert!(!entity.drained());
        assert!(entity.callback_done());
        assert!(entity.dispose());
        assert!(entity.drained());
        assert!(!entity.callback_start());
        assert!(!entity.callback_done());
    }
    #[test]
    fn two_adapter_ids_are_different_and_not_from_an_external_receipt() {
        let a = HostSdkAdapter::new(1).unwrap();
        let b = HostSdkAdapter::new(1).unwrap();
        assert_ne!(a.adapter, b.adapter);
        assert!(!a.owns_lease() && !a.has_retired_all());
        assert!(!b.owns_lease() && !b.has_retired_all());
    }
    #[test]
    fn invalid_runtime_is_not_an_owner() {
        assert!(matches!(HostSdkAdapter::new(0), Err(Status::InvalidArgument)));
    }
}
