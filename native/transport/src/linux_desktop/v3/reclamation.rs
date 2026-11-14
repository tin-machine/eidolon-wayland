//! Two-phase v3 retirement: an SSH actor can finish its remote side without
//! waiting on local Wayland and Spatial SDK owners. Complete journal does NOT
//! mean the window, renderer target or SDK Entity has been retired.
use super::{Binding, Context, Pins, journal::{Journal, Cleanup}, sources::SourceHashes};
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
}

pub struct ReclamationHandoff {
    pub deferred: DeferredReclamation,
    pub authority: ReclamationAuthority,
}

pub struct LocalReclaimed {
    key: ReclaimKey,
    generation: u64,
}

pub struct FinalizedReclamation {
    pub generation: u64,
}

impl DeferredReclamation {
    /// May be called after a new recovery or a live session, but only after
    /// Journal::finish(Complete) is durably revalidated. No clear occurs here.
    pub(crate) fn from_complete(
        journal: Journal, binding: &Binding, generation: u64,
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
            authority: ReclamationAuthority { key: key.clone(), generation },
            deferred: Self { journal, key, generation, cleared: false },
        })
    }
}

impl ReclamationAuthority {
    /// This must be called by the trusted core *after closing admission* and
    /// observing retirement of ALL owned windows, dialogs, fallback targets,
    /// listeners, renderer bindings and SDK Entities. The booleans are only
    /// an observation summary, never a UI/remote-authorized proof by themselves.
    pub fn confirm(self, observed: &LocalRetirement) -> Result<LocalReclaimed, &'static str> {
        if !observed.complete() {
            return Err("DESKTOP_LOCAL_RETIREMENT_PENDING");
        }
        Ok(LocalReclaimed { key: self.key, generation: self.generation })
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
    Ok(FinalizedReclamation { generation: deferred.generation })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::linux_desktop::v3::{journal::Journal, owner::test_support::{Temp, binding, complete}};

    fn retired() -> LocalRetirement {
        LocalRetirement {
            waypipe_stopped: true, listener_retired: true,
            windows_retired: true, sdk_bindings_released: true,
            sdk_entities_disposed: true,
        }
    }

    #[test]
    fn remote_complete_does_not_unlink_until_matching_local_retirement() {
        let dir = Temp::new();
        let binding = binding();
        let mut journal = Journal::reserve(&dir.0, &binding).unwrap();
        journal.finish(complete()).unwrap();
        let ReclamationHandoff { mut deferred, authority } =
            DeferredReclamation::from_complete(journal, &binding, 7).unwrap();
        assert!(super::super::journal::pending(&dir.0).unwrap().is_some());
        let mut not_ready = retired();
        not_ready.sdk_entities_disposed = false;
        // Failed confirmation consumes this authority; a new verified handoff
        // must be obtained by the actual owner, not from user-supplied JSON.
        assert!(authority.confirm(&not_ready).is_err());
        assert!(super::super::journal::pending(&dir.0).unwrap().is_some());
        drop(deferred);
    }

    #[test]
    fn finalization_requires_same_generation_and_all_local_proofs() {
        let dir = Temp::new();
        let binding = binding();
        let mut journal = Journal::reserve(&dir.0, &binding).unwrap();
        journal.finish(complete()).unwrap();
        let ReclamationHandoff { mut deferred, authority } =
            DeferredReclamation::from_complete(journal, &binding, 7).unwrap();
        let local = authority.confirm(&retired()).unwrap();
        let wrong = LocalReclaimed { key: local.key.clone(), generation: 8 };
        assert!(finalize_reclaimed(&mut deferred, &wrong).is_err());
        assert!(super::super::journal::pending(&dir.0).unwrap().is_some());
        assert_eq!(finalize_reclaimed(&mut deferred, &local).unwrap().generation, 7);
        assert!(super::super::journal::pending(&dir.0).unwrap().is_none());
        assert!(finalize_reclaimed(&mut deferred, &local).is_err());
    }
}
