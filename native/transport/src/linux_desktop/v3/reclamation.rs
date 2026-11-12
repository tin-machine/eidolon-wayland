//! SSH-free handoff for v3 remote Complete. Display booleans from JSON may
//! describe observations but cannot themselves authorize journal deletion.
use super::{
    Binding, Context, Pins,
    journal::Journal,
    sources::SourceHashes,
};
use eidolon_model::linux_desktop_v3::report::LocalRetirement;

/// Exact immutable identity of the remote owner and its compiled source set.
#[derive(Clone, PartialEq, Eq)]
struct ReclaimKey {
    context: Context,
    pins: Pins,
    sources: SourceHashes,
}

/// A move-only, private capability. The I3/I4 core retirement adapter must
/// obtain this from an actual owner handoff, not from UI/remote metadata.
pub struct ReclamationAuthority {
    key: ReclaimKey,
    ticket: u64,
}

pub struct LocalReclaimed {
    key: ReclaimKey,
    ticket: u64,
}

pub struct FinalizedReclamation {
    /// Correlates with the original deferred handoff; not remote evidence.
    pub ticket: u64,
}

/// No channel, router or forward lease is retained across this boundary.
pub struct DeferredReclamation {
    journal: Journal,
    key: ReclaimKey,
    ticket: u64,
    finalized: bool,
}

impl DeferredReclamation {
    pub(crate) fn from_remote(
        journal: Journal,
        binding: &Binding,
    ) -> Result<Self, &'static str> {
        if journal.durable_complete()?.is_none() {
            return Err("DESKTOP_CLEANUP_PENDING");
        }
        let key = ReclaimKey {
            context: binding.context().clone(),
            pins: binding.pins().clone(),
            sources: journal.source_hashes().clone(),
        };
        // Distinguish multiple finalization tickets even for an identical
        // key reloaded from a durable Complete across actor generations.
        let ticket = rand::random::<u64>().max(1);
        Ok(Self {
            journal,
            key,
            ticket,
            finalized: false,
        })
    }

    /// Must be delivered only after the authenticated I/O generation has
    /// ended. I2 cannot make this a presentation/SDK success assertion.
    pub fn authority(&self) -> ReclamationAuthority {
        ReclamationAuthority {
            key: self.key.clone(),
            ticket: self.ticket,
        }
    }

    /// The core performs local/SDK retirement outside this API, then calls
    /// confirm() through the trusted authority. The actual owner must have
    /// stopped all admission, renderer targets, Waypipe and SDK generations.
    pub fn finalize_reclaimed(
        &mut self,
        local: &LocalReclaimed,
    ) -> Result<FinalizedReclamation, &'static str> {
        if self.key != local.key || self.ticket != local.ticket {
            return Err("DESKTOP_V3_CLEANUP_SCOPE_MISMATCH");
        }
        if !self.finalized {
            self.journal.clear()?;
            self.finalized = true;
        }
        Ok(FinalizedReclamation {
            ticket: self.ticket,
        })
    }
}

impl ReclamationAuthority {
    /// This consumes the one-shot authority; the observing core adapter
    /// guarantees the supplied summary describes *real* owned resources.
    /// Do not expose this constructor to JNI/serialized command dispatch.
    pub fn confirm(self, observed: &LocalRetirement) -> Result<LocalReclaimed, &'static str> {
        if !observed.complete() {
            return Err("DESKTOP_CLEANUP_PENDING");
        }
        Ok(LocalReclaimed {
            key: self.key,
            ticket: self.ticket,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::linux_desktop::v3::{
        journal::{self, Cleanup},
        owner::test_support::{Temp, binding, complete},
    };

    fn local(ready: bool) -> LocalRetirement {
        LocalRetirement {
            waypipe_stopped: ready,
            listener_retired: ready,
            windows_retired: ready,
            sdk_bindings_released: ready,
            sdk_entities_disposed: ready,
        }
    }

    fn deferred() -> (Temp, DeferredReclamation) {
        let dir = Temp::new();
        let binding = binding();
        let mut journal = journal::Journal::reserve(&dir.0, &binding).unwrap();
        journal.finish(complete()).unwrap();
        let deferred = DeferredReclamation::from_remote(journal, &binding).unwrap();
        (dir, deferred)
    }

    #[test]
    fn incomplete_local_owner_cannot_clear_a_remote_complete_journal() {
        let (dir, mut deferred) = deferred();
        let authority = deferred.authority();
        assert!(authority.confirm(&local(false)).is_err());
        assert!(journal::pending(&dir.0).unwrap().is_some());
        let valid = deferred.authority().confirm(&local(true)).unwrap();
        assert!(deferred.finalize_reclaimed(&valid).is_ok());
        assert!(journal::pending(&dir.0).unwrap().is_none());
    }

    #[test]
    fn mismatched_finalizer_ticket_is_not_transferable() {
        let (first_root, mut first) = deferred();
        let (_second_root, second) = deferred();
        let foreign = second.authority().confirm(&local(true)).unwrap();
        assert_eq!(
            first.finalize_reclaimed(&foreign).err(),
            Some("DESKTOP_V3_CLEANUP_SCOPE_MISMATCH")
        );
        assert!(journal::pending(&first_root.0).unwrap().is_some());
    }

    #[test]
    fn complete_does_not_require_boot_eof_if_reboot_is_verified() {
        let dir = Temp::new();
        let binding = binding();
        let mut journal = journal::Journal::reserve(&dir.0, &binding).unwrap();
        let mut proof: Cleanup = complete();
        proof.pipes_eof = false;
        proof.boot_changed = true;
        journal.finish(proof).unwrap();
        let mut deferred = DeferredReclamation::from_remote(journal, &binding).unwrap();
        let local = deferred.authority().confirm(&local(true)).unwrap();
        deferred.finalize_reclaimed(&local).unwrap();
        assert!(journal::pending(&dir.0).unwrap().is_none());
    }
}
