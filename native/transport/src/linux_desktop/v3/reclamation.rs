//! Two-phase v3 retirement: an SSH actor can finish its remote side without
//! waiting on local Wayland and Spatial SDK owners. Complete journal does NOT
//! mean the window, renderer target or SDK Entity has been retired.
use super::{
    Binding, Context, Pins,
    journal::{Cleanup, Journal},
    sources::SourceHashes,
};
use eidolon_model::linux_desktop_v3::report::LocalRetirement;

#[derive(Clone, PartialEq, Eq)]
struct ReclaimKey {
    context: Context,
    pins: Pins,
    sources: SourceHashes,
}

/// Holds the journal's lock while core retires the local generation. This type
/// intentionally cannot hold or extend an SSH handle, router or Waypipe lease.
pub struct DeferredReclamation {
    journal: Journal,
    key: ReclaimKey,
    generation: u64,
    cleared: bool,
}

/// Only transport can mint one with a verified, durable remote Complete. The
/// private key binds trusted local retirement observation to that same owner.
pub struct ReclamationAuthority {
    key: ReclaimKey,
    generation: u64,
    recovered_local: Option<super::io_fence::RecoveryGenerationBridge>,
}

pub struct ReclamationHandoff {
    pub deferred: DeferredReclamation,
    pub authority: ReclamationAuthority,
    pub no_local_allocation: Option<super::runtime::NoLocalAllocation>,
}
impl ReclamationHandoff {
    /// Keep publication bound to the new I/O generation while checking local
    /// retirement against the original live capture. The pre-existing I/O
    /// lock and complete journal must both name that exact captured owner.
    pub(crate) fn bind_live_recovery(
        &mut self,
        previous: &super::io_fence::PreviousIoClosed,
        capture: &super::runtime::RunCapture,
    ) -> Result<(), &'static str> {
        let bridge = previous.bridge_live_capture(capture, self.authority.generation)?;
        let key = &self.authority.key;
        if self.authority.recovered_local.is_some()
            || !bridge.matches(
                &key.context,
                &key.pins,
                &key.sources,
                self.authority.generation,
            )
        {
            return Err("DESKTOP_V3_CLEANUP_SCOPE_MISMATCH");
        }
        self.authority.recovered_local = Some(bridge);
        Ok(())
    }
}

pub struct LocalReclaimed {
    key: ReclaimKey,
    generation: u64,
}

pub struct FinalizedReclamation {
    generation: u64,
}

/// Core implements this on its private proof after observing the exact local
/// ownership ledger. UI/remote report booleans are not this capability.
pub trait LocalRetirementGate {
    fn verify(&self, context: &Context, generation: u64) -> Result<LocalRetirement, &'static str>;
}
impl FinalizedReclamation {
    pub fn generation(&self) -> u64 {
        self.generation
    }
}

impl DeferredReclamation {
    /// May be called after a new recovery or a live session, but only after
    /// Journal::finish(Complete) is durably revalidated. No clear occurs here.
    pub(crate) fn from_complete(
        journal: Journal,
        binding: &Binding,
        generation: u64,
    ) -> Result<ReclamationHandoff, &'static str> {
        if generation == 0 {
            return Err("DESKTOP_V3_JOURNAL_INVALID");
        }
        let _cleanup: Cleanup = journal.ensure_complete_durable()?;
        let key = ReclaimKey {
            context: binding.context().clone(),
            pins: binding.pins().clone(),
            sources: journal.source_hashes().clone(),
        };
        Ok(ReclamationHandoff {
            no_local_allocation: None,
            authority: ReclamationAuthority {
                key: key.clone(),
                generation,
                recovered_local: None,
            },
            deferred: Self {
                journal,
                key,
                generation,
                cleared: false,
            },
        })
    }
}

impl ReclamationAuthority {
    /// I3 may compare its retained native capture and I/O generation before
    /// trusting any locally observed retirement. Unknown is never success.
    pub fn matches_capture(&self, context: &Context, generation: u64) -> bool {
        self.local_generation() == generation && &self.key.context == context
    }

    fn local_generation(&self) -> u64 {
        self.recovered_local
            .as_ref()
            .map_or(self.generation, |bridge| bridge.local_generation())
    }

    /// This must be called by the trusted core *after closing admission* and
    /// observing retirement of ALL owned windows, dialogs, fallback targets,
    /// listeners, renderer bindings and SDK Entities. The booleans are only
    /// an observation summary, never a UI/remote-authorized proof by themselves.
    pub fn confirm(&self, gate: &impl LocalRetirementGate) -> Result<LocalReclaimed, &'static str> {
        let observed = gate.verify(&self.key.context, self.local_generation())?;
        if !observed.complete() {
            return Err("DESKTOP_LOCAL_RETIREMENT_PENDING");
        }
        Ok(LocalReclaimed {
            key: self.key.clone(),
            generation: self.generation,
        })
    }
}

/// Does not use SSH or wait for SDK callbacks. A sync failure after successful
/// unlink leaves the in-memory journal able to retry directory fsync without
/// unlinking a newly-created pending.json from another generation.
pub fn finalize_reclaimed(
    deferred: &mut DeferredReclamation,
    local: &LocalReclaimed,
) -> Result<FinalizedReclamation, &'static str> {
    if deferred.key != local.key || deferred.generation != local.generation {
        return Err("DESKTOP_V3_CLEANUP_SCOPE_MISMATCH");
    }
    if deferred.cleared {
        return Err("DESKTOP_V3_JOURNAL_CLEAR_FAILED");
    }
    deferred.journal.clear()?;
    deferred.cleared = true;
    Ok(FinalizedReclamation {
        generation: deferred.generation,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::linux_desktop::v3::{
        journal::Journal,
        owner::test_support::{BOOT, Temp, binding, complete, evidence},
    };

    fn retired() -> LocalRetirement {
        LocalRetirement {
            waypipe_stopped: true,
            listener_retired: true,
            windows_retired: true,
            sdk_bindings_released: true,
            sdk_entities_disposed: true,
        }
    }

    struct Observed {
        context: Context,
        generation: u64,
        local: LocalRetirement,
    }
    impl LocalRetirementGate for Observed {
        fn verify(
            &self,
            context: &Context,
            generation: u64,
        ) -> Result<LocalRetirement, &'static str> {
            if context != &self.context || generation != self.generation {
                return Err("DESKTOP_V3_CLEANUP_SCOPE_MISMATCH");
            }
            Ok(self.local.clone())
        }
    }
    fn observed(binding: &Binding, local: LocalRetirement) -> Observed {
        Observed {
            context: binding.context().clone(),
            generation: 7,
            local,
        }
    }

    #[test]
    fn inspect_only_cleanup_requires_the_live_unreserved_capture_gate() {
        use super::super::runtime::{LaunchAdmission, RunCapture};
        use eidolon_model::ids::CommandSeq;
        let dir = Temp::new();
        let binding = binding();
        let capture =
            RunCapture::new(binding.clone(), evidence(), CommandSeq::new(6).unwrap(), 7).unwrap();
        let (sender, _receiver) = tokio::sync::mpsc::channel(1);
        let admission =
            LaunchAdmission::new(capture, sender, tokio_util::sync::CancellationToken::new());
        let gate = admission.seal_without_local_allocation().unwrap();
        let mut journal = Journal::reserve(&dir.0, &binding).unwrap();
        journal.prepared(BOOT).unwrap();
        journal.finish(complete()).unwrap();
        let mut handoff = DeferredReclamation::from_complete(journal, &binding, 7).unwrap();
        assert!(
            handoff.no_local_allocation.is_none(),
            "recovery/Complete never guesses an empty local owner"
        );
        handoff.no_local_allocation = Some(gate);
        let local = handoff
            .authority
            .confirm(handoff.no_local_allocation.as_ref().unwrap())
            .unwrap();
        finalize_reclaimed(&mut handoff.deferred, &local).unwrap();
        assert!(super::super::journal::pending(&dir.0).unwrap().is_none());
    }

    #[test]
    fn live_recovery_bridges_only_the_fenced_capture_and_publishes_the_new_generation() {
        use super::super::{io_fence::IoGenerationFence, runtime::RunCapture};
        use eidolon_model::ids::{CommandSeq, SessionId};
        let dir = Temp::new();
        let binding = binding();
        let capture =
            RunCapture::new(binding.clone(), evidence(), CommandSeq::new(6).unwrap(), 7).unwrap();
        let old_io = IoGenerationFence::acquire(&dir.0, binding.session(), 7).unwrap();
        let mut journal = Journal::reserve(&dir.0, &binding).unwrap();
        journal.prepared(BOOT).unwrap();
        journal.finish(complete()).unwrap();
        assert!(IoGenerationFence::acquire(&dir.0, SessionId::new(8).unwrap(), 8).is_err());
        drop(old_io);
        let mut recovery_io =
            IoGenerationFence::acquire(&dir.0, SessionId::new(8).unwrap(), 8).unwrap();
        let previous = recovery_io.take_previous().unwrap();
        let mut handoff = DeferredReclamation::from_complete(journal, &binding, 8).unwrap();
        let proof = observed(&binding, retired());
        assert!(handoff.authority.confirm(&proof).is_err());
        assert!(previous.bridge_live_capture(&capture, 9).is_err());

        let mut foreign = binding.context().record();
        foreign["nonce"] = serde_json::json!("ffffffffffffffff");
        let foreign = Context::parse(&super::super::canonical(&foreign).unwrap()).unwrap();
        let foreign = Binding::from_captured(foreign, binding.pins().clone()).unwrap();
        let foreign = RunCapture::new(foreign, evidence(), CommandSeq::new(6).unwrap(), 7).unwrap();
        assert!(handoff.bind_live_recovery(&previous, &foreign).is_err());
        assert!(handoff.authority.confirm(&proof).is_err());

        handoff.bind_live_recovery(&previous, &capture).unwrap();
        assert!(handoff.authority.matches_capture(binding.context(), 7));
        assert!(!handoff.authority.matches_capture(binding.context(), 8));
        assert!(handoff.bind_live_recovery(&previous, &capture).is_err());
        let mut stale = observed(&binding, retired());
        stale.generation = 8;
        assert!(handoff.authority.confirm(&stale).is_err());
        let local = handoff.authority.confirm(&proof).unwrap();
        let receipt = finalize_reclaimed(&mut handoff.deferred, &local).unwrap();
        assert_eq!(receipt.generation(), 8);
        assert!(super::super::journal::pending(&dir.0).unwrap().is_none());
    }

    #[test]
    fn inspect_only_unknown_cleanup_retains_its_live_gate_across_recovery() {
        use super::super::{
            io_fence::IoGenerationFence,
            runtime::{LaunchAdmission, RunCapture},
        };
        use eidolon_model::ids::{CommandSeq, SessionId};
        let dir = Temp::new();
        let binding = binding();
        let capture =
            RunCapture::new(binding.clone(), evidence(), CommandSeq::new(6).unwrap(), 7).unwrap();
        let (sender, _receiver) = tokio::sync::mpsc::channel(1);
        let admission =
            LaunchAdmission::new(capture, sender, tokio_util::sync::CancellationToken::new());
        let capture = admission.capture();
        let no_allocation = admission.seal_without_local_allocation().unwrap();
        let old_io = IoGenerationFence::acquire(&dir.0, binding.session(), 7).unwrap();
        let mut journal = Journal::reserve(&dir.0, &binding).unwrap();
        journal.prepared(BOOT).unwrap();
        journal.finish(complete()).unwrap();
        drop(old_io);
        let mut recovery_io =
            IoGenerationFence::acquire(&dir.0, SessionId::new(8).unwrap(), 8).unwrap();
        let previous = recovery_io.take_previous().unwrap();
        let mut handoff = DeferredReclamation::from_complete(journal, &binding, 8).unwrap();
        assert!(handoff.no_local_allocation.is_none());
        assert!(handoff.authority.confirm(&no_allocation).is_err());
        handoff.bind_live_recovery(&previous, &capture).unwrap();
        handoff.no_local_allocation = Some(no_allocation);
        let local = handoff
            .authority
            .confirm(handoff.no_local_allocation.as_ref().unwrap())
            .unwrap();
        assert_eq!(
            finalize_reclaimed(&mut handoff.deferred, &local)
                .unwrap()
                .generation(),
            8
        );
    }

    #[test]
    fn remote_complete_does_not_unlink_until_matching_local_retirement() {
        let dir = Temp::new();
        let binding = binding();
        let mut journal = Journal::reserve(&dir.0, &binding).unwrap();
        journal.prepared(BOOT).unwrap();
        journal.finish(complete()).unwrap();
        let ReclamationHandoff {
            deferred,
            authority,
            ..
        } = DeferredReclamation::from_complete(journal, &binding, 7).unwrap();
        assert!(super::super::journal::pending(&dir.0).unwrap().is_some());
        let mut not_ready = retired();
        not_ready.sdk_entities_disposed = false;
        // An incomplete observation keeps the authority so the same owner may
        // retry after the actual local/SDK callbacks arrive.
        assert!(authority.confirm(&observed(&binding, not_ready)).is_err());
        assert!(super::super::journal::pending(&dir.0).unwrap().is_some());
        drop(deferred);
    }

    #[test]
    fn incomplete_retirement_can_be_rechecked_without_losing_authority() {
        let dir = Temp::new();
        let binding = binding();
        let mut journal = Journal::reserve(&dir.0, &binding).unwrap();
        journal.prepared(BOOT).unwrap();
        journal.finish(complete()).unwrap();
        let ReclamationHandoff {
            mut deferred,
            authority,
            ..
        } = DeferredReclamation::from_complete(journal, &binding, 7).unwrap();
        assert!(authority.matches_capture(binding.context(), 7));
        assert!(!authority.matches_capture(binding.context(), 8));
        let mut foreign = binding.context().record();
        foreign["nonce"] = serde_json::json!("ffffffffffffffff");
        let foreign = Context::parse(&super::super::canonical(&foreign).unwrap()).unwrap();
        assert!(!authority.matches_capture(&foreign, 7));
        let mut incomplete = retired();
        incomplete.sdk_bindings_released = false;
        assert!(authority.confirm(&observed(&binding, incomplete)).is_err());
        assert!(super::super::journal::pending(&dir.0).unwrap().is_some());
        let mut foreign = observed(&binding, retired());
        foreign.generation = 8;
        assert!(authority.confirm(&foreign).is_err());
        let local = authority.confirm(&observed(&binding, retired())).unwrap();
        assert_eq!(
            finalize_reclaimed(&mut deferred, &local)
                .unwrap()
                .generation(),
            7
        );
        assert!(super::super::journal::pending(&dir.0).unwrap().is_none());
    }

    #[test]
    fn finalization_requires_same_generation_and_all_local_proofs() {
        let dir = Temp::new();
        let binding = binding();
        let mut journal = Journal::reserve(&dir.0, &binding).unwrap();
        journal.prepared(BOOT).unwrap();
        journal.finish(complete()).unwrap();
        let ReclamationHandoff {
            mut deferred,
            authority,
            ..
        } = DeferredReclamation::from_complete(journal, &binding, 7).unwrap();
        let local = authority.confirm(&observed(&binding, retired())).unwrap();
        let wrong = LocalReclaimed {
            key: local.key.clone(),
            generation: 8,
        };
        assert!(finalize_reclaimed(&mut deferred, &wrong).is_err());
        assert!(super::super::journal::pending(&dir.0).unwrap().is_some());
        assert_eq!(
            finalize_reclaimed(&mut deferred, &local)
                .unwrap()
                .generation(),
            7
        );
        assert!(super::super::journal::pending(&dir.0).unwrap().is_none());
        assert!(finalize_reclaimed(&mut deferred, &local).is_err());
    }
}
