//! Trusted, bounded ownership ledger for Linux Desktop v3 reclamation.
//!
//! A panel reservation is recorded before publishing Create. Sealing forbids
//! new allocations, but never forbids receipts for already-owned generations.
//! Completed generations are folded into counters; unresolved ones are never
//! evicted. Reports/JSON cannot construct either proof below.
use crate::{session::Session, startup_observation::V3PresentationObserver};
use eidolon_model::{
    ids::{CommandSeq, WindowId},
    linux_desktop_v3::{Context, presentation::NativeOrigin, report::LocalRetirement},
    state::Status,
    surface::TargetKey,
};
use eidolon_render::{Controller as Renderer, RetirementFence};
use eidolon_transport::linux_desktop::v3::reclamation::{
    FinalizedReclamation, LocalReclaimed, LocalRetirementGate, ReclamationHandoff,
    finalize_reclaimed,
};
use std::{
    sync::{Arc, Mutex},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

const LIMIT: usize = crate::linux_desktop_v3::MAX_RETAINED_WINDOWS;
const PENDING: &str = "DESKTOP_LOCAL_RETIREMENT_PENDING";
const STALE: &str = "DESKTOP_V3_CLEANUP_SCOPE_MISMATCH";

struct Target {
    key: TargetKey,
    create: CommandSeq,
    registered: bool,
    sealed: bool,
    sdk_disposed: bool,
    renderer: Option<RetirementFence>,
}
impl Target {
    fn complete(&self, renderer: &Renderer) -> bool {
        self.sealed
            && (!self.registered || self.sdk_disposed)
            && self
                .renderer
                .as_ref()
                .is_some_and(|f| renderer.retirement_complete(f))
    }
}

pub(crate) struct WindowRetirementProof {
    origin: NativeOrigin,
    window: WindowId,
}
impl WindowRetirementProof {
    pub(crate) fn origin(&self) -> &NativeOrigin {
        &self.origin
    }
    pub(crate) fn window(&self) -> WindowId {
        self.window
    }
}

pub(crate) struct OriginRetirementProof {
    context: Context,
    generation: u64,
    local: LocalRetirement,
}
impl LocalRetirementGate for OriginRetirementProof {
    fn verify(&self, context: &Context, generation: u64) -> Result<LocalRetirement, &'static str> {
        if &self.context != context || self.generation != generation {
            return Err(STALE);
        }
        Ok(self.local.clone())
    }
}

pub(crate) struct Owner {
    origin: NativeOrigin,
    context: Context,
    generation: u64,
    sealed: bool,
    targets: Vec<Target>,
    completed_targets: Vec<(TargetKey, CommandSeq)>,
    window_fences: Vec<(WindowId, RetirementFence)>,
    last_create: i64,
    issued: u64,
    completed: u64,
}
impl Owner {
    pub(crate) fn new(
        origin: NativeOrigin,
        context: Context,
        generation: u64,
    ) -> Result<Self, &'static str> {
        let identity = origin.record();
        let capture = context.record();
        if generation == 0
            || identity["session"] != capture["session"]
            || identity["nonce"] != capture["nonce"]
            || identity["scope"].as_str() != Some(context.scope().as_str())
        {
            return Err(STALE);
        }
        Ok(Self {
            origin,
            context,
            generation,
            sealed: false,
            targets: vec![],
            completed_targets: vec![],
            window_fences: vec![],
            last_create: 0,
            issued: 0,
            completed: 0,
        })
    }

    pub(crate) fn origin(&self) -> &NativeOrigin {
        &self.origin
    }
    pub(crate) fn is_sealed(&self) -> bool {
        self.sealed
    }
    pub(crate) fn pending_targets(&self) -> usize {
        self.targets.len() + self.completed_targets.len()
    }
    pub(crate) fn completed_targets(&self) -> Vec<TargetKey> {
        self.completed_targets
            .iter()
            .map(|(target, _)| *target)
            .collect()
    }
    /// The native owner calls this after consuming the exact retirement
    /// receipt to close its pending Destroy plan. It is not an SDK observation.
    pub(crate) fn acknowledge_completed_target(&mut self, target: TargetKey) -> bool {
        if let Some(index) = self
            .completed_targets
            .iter()
            .position(|(key, _)| *key == target)
        {
            self.completed_targets.remove(index);
            true
        } else {
            false
        }
    }
    pub(crate) fn owns_target(&self, key: TargetKey, create: CommandSeq) -> bool {
        self.targets
            .iter()
            .any(|t| t.key == key && t.create == create)
    }

    /// Must run under the same lock as panel publication and SDK registration.
    pub(crate) fn reserve_target(
        &mut self,
        key: TargetKey,
        create: CommandSeq,
    ) -> Result<(), &'static str> {
        if let Some(target) = self.targets.iter().find(|t| t.key == key) {
            return if target.create == create {
                Ok(())
            } else {
                Err(STALE)
            };
        }
        if self.sealed || key.session != self.origin.session() || create.get() <= self.last_create {
            return Err(STALE);
        }
        if self.targets.len() + self.completed_targets.len() >= LIMIT {
            return Err("RESOURCE_LIMIT");
        }
        self.issued = self.issued.checked_add(1).ok_or("ID_EXHAUSTED")?;
        self.last_create = create.get();
        self.targets.push(Target {
            key,
            create,
            registered: false,
            sealed: false,
            sdk_disposed: false,
            renderer: None,
        });
        Ok(())
    }

    /// The SDK must register successfully before any Entity/Surface side effect.
    pub(crate) fn sdk_registered(
        &mut self,
        key: TargetKey,
        create: CommandSeq,
    ) -> Result<(), &'static str> {
        let target = self
            .targets
            .iter_mut()
            .find(|t| t.key == key && t.create == create)
            .ok_or(STALE)?;
        if self.sealed || target.sealed {
            return Err(STALE);
        }
        target.registered = true;
        Ok(())
    }

    pub(crate) fn retire_target(
        &mut self,
        key: TargetKey,
        renderer: &Renderer,
    ) -> Result<(), &'static str> {
        if self
            .completed_targets
            .iter()
            .any(|(target, _)| *target == key)
        {
            return Ok(());
        }
        let target = self
            .targets
            .iter_mut()
            .find(|t| t.key == key)
            .ok_or(STALE)?;
        target.sealed = true;
        if target.renderer.is_none() {
            target.renderer = Some(renderer.retire_target(key).map_err(|_| PENDING)?);
        }
        Ok(())
    }

    /// Sent after SDK object disposal and callback/producer drain. Native
    /// release is accepted only after the exact renderer fence has completed.
    pub(crate) fn sdk_retired(
        &mut self,
        key: TargetKey,
        create: CommandSeq,
        renderer: &Renderer,
    ) -> Result<(), &'static str> {
        if !self.owns_target(key, create) {
            return Err(STALE);
        }
        self.retire_target(key, renderer)?;
        let target = self
            .targets
            .iter_mut()
            .find(|t| t.key == key && t.create == create)
            .ok_or(STALE)?;
        if !target.registered {
            return Err(STALE);
        }
        if !target
            .renderer
            .as_ref()
            .is_some_and(|f| renderer.retirement_complete(f))
        {
            return Err(PENDING);
        }
        target.sdk_disposed = true;
        Ok(())
    }

    pub(crate) fn seal(&mut self, renderer: &Renderer) -> Result<(), &'static str> {
        self.sealed = true;
        let keys: Vec<_> = self.targets.iter().map(|t| t.key).collect();
        let mut result = Ok(());
        for key in keys {
            if let Err(error) = self.retire_target(key, renderer) {
                result = Err(error);
            }
        }
        result
    }

    /// Ordinary scene/remap churn can complete targets while the WindowId
    /// remains alive. Waiting for Stop here would make long sessions unbounded.
    pub(crate) fn collect_completed(&mut self, renderer: &Renderer) -> Result<(), &'static str> {
        // Read each asynchronous fence once. A second read during retain could
        // remove a newly completed target without counting its receipt.
        let complete: Vec<_> = self.targets.iter().map(|t| t.complete(renderer)).collect();
        let count = complete.iter().filter(|value| **value).count() as u64;
        let next = self.completed.checked_add(count).ok_or("ID_EXHAUSTED")?;
        if next > self.issued {
            return Err(STALE);
        }
        self.completed_targets.extend(
            self.targets
                .iter()
                .zip(&complete)
                .filter_map(|(target, done)| done.then_some((target.key, target.create))),
        );
        let mut complete = complete.into_iter();
        self.targets.retain(|_| !complete.next().unwrap());
        self.completed = next;
        Ok(())
    }

    fn matches(&self, session: &Session) -> bool {
        session.v3_origin() == Some(&self.origin)
            && session.v3_capture().is_some_and(|(context, generation)| {
                context == &self.context && generation == self.generation
            })
    }

    pub(crate) fn poll_window(
        &mut self,
        session: &Session,
        window: WindowId,
        renderer: &Renderer,
    ) -> Option<WindowRetirementProof> {
        if !self.matches(session) || !session.v3_window_retired(window) {
            return None;
        }
        let keys: Vec<_> = self
            .targets
            .iter()
            .filter(|t| t.key.window == window)
            .map(|t| t.key)
            .collect();
        for key in keys {
            let _ = self.retire_target(key, renderer);
        }
        if !self.window_fences.iter().any(|(w, _)| *w == window) {
            if self.window_fences.len() >= LIMIT {
                return None;
            }
            let fence = renderer.retire_window(self.origin.session(), window).ok()?;
            self.window_fences.push((window, fence));
        }
        self.collect_completed(renderer).ok()?;
        if self.targets.iter().any(|t| t.key.window == window)
            || self
                .completed_targets
                .iter()
                .any(|(t, _)| t.window == window)
            || !self
                .window_fences
                .iter()
                .any(|(w, f)| *w == window && renderer.retirement_complete(f))
        {
            return None;
        }
        Some(WindowRetirementProof {
            origin: self.origin.clone(),
            window,
        })
    }

    pub(crate) fn window_gc_complete(&mut self, proof: &WindowRetirementProof) {
        if proof.origin == self.origin {
            self.window_fences.retain(|(w, _)| *w != proof.window);
        }
    }

    /// Keep the two histories synchronized with one private proof. Main stays
    /// fixed in B1 until the whole origin owner is released.
    pub(crate) fn collect_windows(
        &mut self,
        session: &mut Session,
        observer: &mut V3PresentationObserver,
        renderer: &Renderer,
    ) -> Result<(), &'static str> {
        if observer.current() != &self.origin || !self.matches(session) {
            return Err(STALE);
        }
        for window in session.v3_pending_retirements() {
            if let Some(proof) = self.poll_window(session, window, renderer) {
                observer
                    .retire_confirmed_window(&proof)
                    .map_err(|_| STALE)?;
                if !session.v3_confirm_retired(&proof) {
                    return Err(STALE);
                }
                self.window_gc_complete(&proof);
            }
        }
        Ok(())
    }

    pub(crate) fn poll_origin(
        &mut self,
        session: &Session,
        renderer: &Renderer,
    ) -> Option<OriginRetirementProof> {
        if !self.sealed || !self.matches(session) || !session.v3_local_retired() {
            return None;
        }
        self.seal(renderer).ok()?;
        self.collect_completed(renderer).ok()?;
        if !self.targets.is_empty()
            || !self.completed_targets.is_empty()
            || self.issued != self.completed
            || !self.window_fences.is_empty()
            || session.v3_retained_windows() != 0
        {
            return None;
        }
        Some(OriginRetirementProof {
            context: self.context.clone(),
            generation: self.generation,
            local: LocalRetirement {
                waypipe_stopped: true,
                listener_retired: true,
                windows_retired: true,
                sdk_bindings_released: true,
                sdk_entities_disposed: true,
            },
        })
    }
}

#[derive(Default)]
struct Finalization {
    handoff: Option<ReclamationHandoff>,
    proof: Option<OriginRetirementProof>,
    local: Option<LocalReclaimed>,
    receipt: Option<FinalizedReclamation>,
    done: bool,
    error: Option<&'static str>,
    retry_after: Option<Instant>,
}

/// Own outside the core catch_unwind body. Both journal I/O and publication's
/// filesystem recheck run on its worker; the core/SDK tick never waits for I/O.
#[derive(Default)]
pub(crate) struct Finalizer {
    state: Arc<Mutex<Finalization>>,
    worker: Option<JoinHandle<()>>,
}
impl Finalizer {
    pub(crate) fn accept_handoff(
        &mut self,
        handoff: ReclamationHandoff,
    ) -> Result<(), &'static str> {
        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        if state.handoff.is_some() {
            return Err(STALE);
        }
        if state.done {
            *state = Finalization::default();
        }
        let local = handoff
            .no_local_allocation
            .as_ref()
            .map(|gate| handoff.authority.confirm(gate))
            .transpose();
        state.handoff = Some(handoff);
        match local {
            Ok(local) => {
                state.local = local;
                Ok(())
            }
            Err(error) => {
                state.error.get_or_insert(error);
                Err(error)
            }
        }
    }
    pub(crate) fn worker_pending(&self) -> bool {
        self.worker.as_ref().is_some_and(|w| !w.is_finished())
    }
    pub(crate) fn pending(&self) -> bool {
        self.state
            .try_lock()
            .map_or(true, |s| s.handoff.is_some() && !s.done)
    }
    pub(crate) fn completed(&self) -> bool {
        self.state.try_lock().is_ok_and(|state| state.done)
    }
    pub(crate) fn last_error(&self) -> Option<&'static str> {
        self.state.try_lock().ok().and_then(|s| s.error)
    }
    /// Remote cleanup may need a fresh SSH connection after every local
    /// resource is gone. Keep the old owner as the Launch barrier, but allow
    /// explicit recovery when this retained private proof names that owner.
    pub(crate) fn permits_recovery(&self, owner: &Owner) -> bool {
        owner.is_sealed()
            && !self.worker_pending()
            && self.state.try_lock().is_ok_and(|state| {
                !state.done
                    && state.handoff.is_none()
                    && state.local.is_none()
                    && state.receipt.is_none()
                    && state.proof.as_ref().is_some_and(|proof| {
                        proof
                            .verify(&owner.context, owner.generation)
                            .is_ok_and(|local| local.complete())
                    })
            })
    }
    pub(crate) fn tick(
        &mut self,
        proof: Option<OriginRetirementProof>,
        transport: &Arc<eidolon_transport::session::Controller>,
    ) {
        if self.worker_pending() {
            return;
        }
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
        {
            let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
            if let Some(proof) = proof
                && !state.done
            {
                state.proof = Some(proof);
            }
            if state.done
                || state.handoff.is_none()
                || state.retry_after.is_some_and(|t| Instant::now() < t)
            {
                return;
            }
            if state.local.is_none() {
                let Some(proof) = state.proof.as_ref() else {
                    return;
                };
                let authority = &state.handoff.as_ref().unwrap().authority;
                if !authority.matches_capture(&proof.context, proof.generation) {
                    state.error.get_or_insert(STALE);
                    return;
                }
                match authority.confirm(proof) {
                    Ok(local) => state.local = Some(local),
                    Err(error) => {
                        state.error.get_or_insert(error);
                        return;
                    }
                }
            }
        }
        let shared = self.state.clone();
        let transport = transport.clone();
        match thread::Builder::new()
            .name("eidolon-v3-finalize".into())
            .spawn(move || {
                let mut state = shared.lock().unwrap_or_else(|e| e.into_inner());
                let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    if state.receipt.is_none() {
                        let Finalization { handoff, local, .. } = &mut *state;
                        let receipt = finalize_reclaimed(
                            &mut handoff.as_mut().unwrap().deferred,
                            local.as_ref().unwrap(),
                        )?;
                        state.receipt = Some(receipt);
                    }
                    if transport.publish_v3_finalized(state.receipt.as_ref().unwrap())
                        != Status::Accepted
                    {
                        return Err("DESKTOP_V3_FINALIZATION_PUBLISH_PENDING");
                    }
                    state.done = true;
                    state.handoff = None;
                    Ok::<(), &'static str>(())
                }))
                .unwrap_or(Err("DESKTOP_V3_FINALIZATION_WORKER_FAILED"));
                if let Err(error) = outcome {
                    state.error.get_or_insert(error);
                    state.retry_after = Some(Instant::now() + Duration::from_millis(100));
                }
            }) {
            Ok(worker) => self.worker = Some(worker),
            Err(_) => {
                let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
                state
                    .error
                    .get_or_insert("DESKTOP_V3_FINALIZATION_WORKER_FAILED");
                state.retry_after = Some(Instant::now() + Duration::from_millis(100));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn owner() -> Owner {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../../scripts/tests/fixtures/linux-desktop-v3-contract.json"
        ))
        .unwrap();
        let raw = |id| {
            fixture["records"]
                .as_array()
                .unwrap()
                .iter()
                .find(|r| r["id"] == id)
                .unwrap()["raw"]
                .as_str()
                .unwrap()
                .as_bytes()
        };
        Owner::new(
            NativeOrigin::parse(raw("origin")).unwrap(),
            Context::parse(raw("context")).unwrap(),
            7,
        )
        .unwrap()
    }
    fn key(owner: &Owner, generation: i64) -> TargetKey {
        TargetKey::from_raw(owner.origin.session().get(), 1, 1, generation).unwrap()
    }
    fn wait(mut condition: impl FnMut() -> bool) {
        let deadline = Instant::now() + Duration::from_secs(5);
        while !condition() {
            assert!(Instant::now() < deadline);
            thread::sleep(Duration::from_millis(2));
        }
    }
    fn stop(renderer: &Renderer) {
        renderer.request_stop();
        wait(|| renderer.owner_reclaimed());
    }

    #[test]
    fn every_registered_generation_requires_its_own_sdk_receipt() {
        let mut owner = owner();
        let renderer = Renderer::start().unwrap();
        let first = key(&owner, 1);
        let second = key(&owner, 2);
        let one = CommandSeq::new(1).unwrap();
        let two = CommandSeq::new(2).unwrap();
        owner.reserve_target(first, one).unwrap();
        owner.sdk_registered(first, one).unwrap();
        owner.reserve_target(second, two).unwrap();
        owner.sdk_registered(second, two).unwrap();
        owner.seal(&renderer).unwrap();
        wait(|| owner.sdk_retired(second, two, &renderer).is_ok());
        owner.collect_completed(&renderer).unwrap();
        assert_eq!(owner.completed_targets(), vec![second]);
        assert!(owner.acknowledge_completed_target(second));
        assert_eq!(owner.pending_targets(), 1);
        assert_eq!((owner.issued, owner.completed), (2, 1));
        assert!(owner.owns_target(first, one));
        assert_eq!(owner.sdk_retired(first, two, &renderer), Err(STALE));
        wait(|| owner.sdk_retired(first, one, &renderer).is_ok());
        owner.collect_completed(&renderer).unwrap();
        assert!(owner.acknowledge_completed_target(first));
        assert_eq!(owner.pending_targets(), 0);
        assert_eq!(owner.issued, owner.completed);
        assert_eq!(
            owner.reserve_target(key(&owner, 3), CommandSeq::new(3).unwrap()),
            Err(STALE)
        );
        stop(&renderer);
    }

    #[test]
    fn never_registered_create_is_zero_effect_only_after_seal_and_fence() {
        let mut owner = owner();
        let renderer = Renderer::start().unwrap();
        let target = key(&owner, 1);
        let create = CommandSeq::new(1).unwrap();
        owner.reserve_target(target, create).unwrap();
        owner.collect_completed(&renderer).unwrap();
        assert_eq!(owner.pending_targets(), 1);
        owner.retire_target(target, &renderer).unwrap();
        assert_eq!(owner.sdk_registered(target, create), Err(STALE));
        wait(|| {
            owner.collect_completed(&renderer).unwrap();
            owner.completed_targets().contains(&target)
        });
        assert_eq!(owner.retire_target(target, &renderer), Ok(()));
        assert_eq!(owner.pending_targets(), 1);
        assert!(owner.acknowledge_completed_target(target));
        assert_eq!(owner.pending_targets(), 0);
        assert_eq!(owner.reserve_target(target, create), Err(STALE));
        assert_eq!((owner.issued, owner.completed), (1, 1));
        stop(&renderer);
    }

    #[test]
    fn completed_remap_generations_do_not_accumulate_until_window_destroy() {
        let mut owner = owner();
        let renderer = Renderer::start().unwrap();
        for generation in 1..=128 {
            let target = key(&owner, generation);
            let create = CommandSeq::new(generation).unwrap();
            owner.reserve_target(target, create).unwrap();
            owner.sdk_registered(target, create).unwrap();
            wait(|| owner.sdk_retired(target, create, &renderer).is_ok());
            owner.collect_completed(&renderer).unwrap();
            assert!(owner.acknowledge_completed_target(target));
            assert_eq!(owner.pending_targets(), 0);
        }
        assert!(!owner.is_sealed());
        assert_eq!((owner.issued, owner.completed), (128, 128));
        stop(&renderer);
    }

    #[test]
    fn unrelated_renderer_completion_cannot_discharge_a_reserved_target() {
        let mut owner = owner();
        let renderer = Renderer::start().unwrap();
        let foreign = Renderer::start().unwrap();
        let target = key(&owner, 1);
        let create = CommandSeq::new(1).unwrap();
        owner.reserve_target(target, create).unwrap();
        owner.sdk_registered(target, create).unwrap();
        owner.retire_target(target, &renderer).unwrap();
        stop(&foreign);
        assert_eq!(owner.sdk_retired(target, create, &foreign), Err(PENDING));
        owner.collect_completed(&foreign).unwrap();
        assert_eq!(owner.pending_targets(), 1);
        wait(|| owner.sdk_retired(target, create, &renderer).is_ok());
        owner.collect_completed(&renderer).unwrap();
        assert!(owner.acknowledge_completed_target(target));
        assert_eq!(owner.pending_targets(), 0);
        stop(&renderer);
    }

    #[test]
    fn missing_remote_handoff_keeps_local_proof_for_later_join() {
        let mut owner = owner();
        let transport = Arc::new(eidolon_transport::session::Controller::start().unwrap());
        let mut finalizer = Finalizer::default();
        assert!(!finalizer.permits_recovery(&owner));
        let proof = OriginRetirementProof {
            context: owner.context.clone(),
            generation: owner.generation,
            local: LocalRetirement {
                waypipe_stopped: true,
                listener_retired: true,
                windows_retired: true,
                sdk_bindings_released: true,
                sdk_entities_disposed: true,
            },
        };
        finalizer.tick(Some(proof), &transport);
        assert!(!finalizer.worker_pending());
        assert!(finalizer.state.lock().unwrap().proof.is_some());
        assert!(finalizer.state.lock().unwrap().receipt.is_none());
        assert!(!finalizer.permits_recovery(&owner));
        owner.sealed = true;
        assert!(finalizer.permits_recovery(&owner));
        owner.generation += 1;
        assert!(!finalizer.permits_recovery(&owner));
        transport.request_stop();
        wait(|| transport.is_finished());
    }
}
