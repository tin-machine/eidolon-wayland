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
    #[cfg(feature = "host-acceptance")]
    host_observation: Option<(
        std::sync::Arc<crate::host_acceptance::Trace>,
        crate::host_acceptance::Capture,
    )>,
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
    durable_local: Option<LocalReclaimed>,
}
impl ReclamationHandoff {
    pub fn take_durable_local_retirement(&mut self) -> Option<LocalReclaimed> {
        self.durable_local.take()
    }

    pub(crate) fn bind_durable_local_retirement(
        &mut self,
        files: &std::path::Path,
        current_boot: Option<u32>,
    ) -> Result<(), &'static str> {
        if !self.deferred.journal.local_retired() {
            if current_boot.is_some_and(|current| self.deferred.journal.predates_android_boot(current)) {
                super::local_directory::retire_after_reboot(files, &self.authority.key.context)?;
            } else if self.deferred.journal.local_launch_was_blocked() {
                super::local_directory::require_absent(files, &self.authority.key.context)?;
            } else {
                return Err("DESKTOP_LOCAL_RETIREMENT_PENDING");
            }
        }
        self.durable_local = Some(LocalReclaimed {
            key: self.authority.key.clone(),
            generation: self.authority.generation,
        });
        Ok(())
    }

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
    #[cfg(feature = "host-acceptance")]
    pub(crate) fn observe_with(&mut self, capture: &super::runtime::RunCapture) {
        self.host_observation = capture.host_trace.as_ref().map(|trace| {
            (
                trace.clone(),
                crate::host_acceptance::Capture::from_run(capture),
            )
        });
    }
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
            durable_local: None,
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
                #[cfg(feature = "host-acceptance")]
                host_observation: None,
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
    #[cfg(feature = "host-acceptance")]
    if let Some((trace, capture)) = &deferred.host_observation {
        trace.record(
            Some(capture.clone()),
            "journal_unlinked",
            serde_json::json!({
                "generation":deferred.generation.to_string(), "directory_fsynced":true
            }),
        );
    }
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
    fn reboot_recovery_requires_an_advanced_boot_and_the_original_directory() {
        use std::os::unix::fs::DirBuilderExt;
        use std::os::unix::net::UnixListener;

        let files = Temp::new();
        let binding = binding();
        let parent = files.0.join("rt");
        let directory = parent.join(binding.context().nonce().as_str());
        std::fs::DirBuilder::new().mode(0o700).recursive(true).create(&directory).unwrap();
        super::super::local_directory::mark(&directory, binding.context()).unwrap();
        let marker = directory.join(".desktop-v3-owner");
        let original = std::fs::read_to_string(&marker).unwrap();
        let mut parts = original.trim_end().split(':');
        let scope = parts.next().unwrap();
        let device = parts.next().unwrap().parse::<u64>().unwrap();
        let inode = parts.next().unwrap();
        std::fs::write(marker, format!("{scope}:{}:{inode}\n", device ^ 1)).unwrap();
        drop(UnixListener::bind(directory.join("w")).unwrap());
        let mut journal = Journal::reserve_inspection(&files.0, &binding, Some(4)).unwrap();
        journal.prepared(BOOT).unwrap();
        journal.open_local_launch().unwrap();
        journal.finish(complete()).unwrap();
        let mut handoff = DeferredReclamation::from_complete(journal, &binding, 7).unwrap();
        for boot in [None, Some(3), Some(4)] {
            assert!(handoff.bind_durable_local_retirement(&files.0, boot).is_err());
            assert!(handoff.take_durable_local_retirement().is_none());
            assert!(directory.join("w").exists());
        }
        let unknown = directory.join("unrelated");
        std::fs::write(&unknown, b"keep").unwrap();
        assert!(handoff.bind_durable_local_retirement(&files.0, Some(5)).is_err());
        assert!(directory.join("w").exists());
        assert!(unknown.exists());
        std::fs::remove_file(unknown).unwrap();
        handoff.bind_durable_local_retirement(&files.0, Some(5)).unwrap();
        let local = handoff.take_durable_local_retirement().unwrap();
        assert!(!directory.exists());
        finalize_reclaimed(&mut handoff.deferred, &local).unwrap();
        assert!(super::super::journal::pending(&files.0).unwrap().is_none());
    }

    #[test]
    fn cold_inspection_recovery_keeps_unexpected_files_and_retries_same_boot() {
        use std::os::unix::fs::DirBuilderExt;

        let files = Temp::new();
        let binding = binding();
        let mut journal = Journal::reserve_inspection(&files.0, &binding, Some(4)).unwrap();
        journal.prepared(BOOT).unwrap();
        assert!(journal.advance(super::super::journal::Stage::ForwardRequested).is_err());
        journal.finish(complete()).unwrap();
        let mut handoff = DeferredReclamation::from_complete(journal, &binding, 7).unwrap();

        let directory = files.0.join("rt").join(binding.context().nonce().as_str());
        std::fs::DirBuilder::new().mode(0o700).recursive(true).create(&directory).unwrap();
        let unexpected = directory.join("unrelated");
        std::fs::write(&unexpected, b"keep").unwrap();
        assert!(handoff.bind_durable_local_retirement(&files.0, Some(4)).is_err());
        assert!(handoff.take_durable_local_retirement().is_none());
        assert_eq!(std::fs::read(&unexpected).unwrap(), b"keep");
        assert!(super::super::journal::pending(&files.0).unwrap().is_some());

        std::fs::remove_file(unexpected).unwrap();
        std::fs::remove_dir(directory).unwrap();
        handoff.bind_durable_local_retirement(&files.0, Some(4)).unwrap();
        let local = handoff.take_durable_local_retirement().unwrap();
        finalize_reclaimed(&mut handoff.deferred, &local).unwrap();
        assert!(super::super::journal::pending(&files.0).unwrap().is_none());
    }

    #[test]
    fn process_death_releases_inspection_owner_without_a_reboot() {
        use super::super::{io_fence::IoGenerationFence, owner::test_support::{profile, request, storage}};
        use eidolon_model::ids::SessionId;
        use std::{process::{Command, Stdio}, thread, time::{Duration, Instant}};

        const CHILD_FILES: &str = "EIDOLON_TEST_INSPECTION_OWNER_FILES";
        let binding = binding();
        if let Some(path) = std::env::var_os(CHILD_FILES) {
            let files = std::path::PathBuf::from(path);
            let _fence = IoGenerationFence::acquire(&files, binding.session(), 1).unwrap();
            let mut journal = Journal::reserve_inspection(&files, &binding, Some(4)).unwrap();
            journal.prepared(BOOT).unwrap();
            journal.finish(complete()).unwrap();
            std::fs::write(files.join("ready"), b"ready").unwrap();
            thread::sleep(Duration::from_secs(30));
            return;
        }

        let files = Temp::new();
        let mut child = Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "linux_desktop::v3::reclamation::tests::process_death_releases_inspection_owner_without_a_reboot"])
            .env(CHILD_FILES, &files.0)
            .stdout(Stdio::null())
            .spawn()
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        let ready = files.0.join("ready");
        while !ready.exists() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        let next = SessionId::new(binding.session().get() + 1).unwrap();
        let fenced_while_live = matches!(IoGenerationFence::acquire(&files.0, next, 2),
            Err("DESKTOP_PREVIOUS_CONNECTION_PENDING"));
        let killed = child.kill();
        let status = child.wait().unwrap();
        killed.unwrap();
        assert!(ready.exists() && fenced_while_live && !status.success());

        let mut fence = IoGenerationFence::acquire(&files.0, next, 2).unwrap();
        let previous = fence.take_previous().unwrap();
        let profile = profile();
        let storage = storage();
        let (journal, captured) = Journal::matching(&request(&files.0, &profile, binding.pins(), &storage))
            .unwrap().unwrap();
        assert!(previous.matches(next, &captured, journal.source_hashes()));
        let mut handoff = DeferredReclamation::from_complete(journal, &captured, 2).unwrap();
        handoff.bind_durable_local_retirement(&files.0, Some(4)).unwrap();
        let local = handoff.take_durable_local_retirement().unwrap();
        finalize_reclaimed(&mut handoff.deferred, &local).unwrap();
        assert!(super::super::journal::pending(&files.0).unwrap().is_none());
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
