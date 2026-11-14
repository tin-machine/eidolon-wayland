//! Synchronous SDK facts keyed by a native-issued Create and captured adapter.
//! Diagnostics and current-handle lookups cannot create entries in this ledger.
use eidolon_model::{
    commands::SdkEvidencePhase,
    ids::{CommandSeq, SceneEpoch, WindowId},
    state::Status,
    surface::TargetKey,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Lease {
    pub target: TargetKey,
    pub create: CommandSeq,
    pub adapter: u64,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Phase {
    Registered,
    Current,
    Retiring,
    Retired,
}

struct Record {
    lease: Lease,
    phase: Phase,
}

#[derive(Default)]
pub(crate) struct Ledger {
    records: Vec<Record>,
}

impl Ledger {
    pub fn register(&mut self, lease: Lease) -> Status {
        if lease.adapter == 0 || lease.adapter > i64::MAX as u64 {
            return Status::InvalidArgument;
        }
        if let Some(record) = self.records.iter().find(|r| r.lease.target == lease.target) {
            return if record.lease == lease
                && matches!(record.phase, Phase::Registered | Phase::Current)
            {
                Status::Accepted
            } else {
                Status::Stale
            };
        }
        // Completed receipts may be compacted. Unresolved obligations are
        // never evicted; their native retirement owner keeps the actual proof.
        if self.records.len() >= crate::linux_desktop_v3::MAX_RETAINED_WINDOWS {
            if let Some(index) = self.records.iter().position(|r| r.phase == Phase::Retired) {
                self.records.remove(index);
            } else {
                return Status::Busy;
            }
        }
        self.records.push(Record {
            lease,
            phase: Phase::Registered,
        });
        Status::Accepted
    }

    pub fn matches(&self, lease: Lease) -> bool {
        self.records.iter().any(|r| r.lease == lease)
    }

    pub fn contains(&self, target: TargetKey) -> bool {
        self.records.iter().any(|r| r.lease.target == target)
    }

    pub fn current(&self, target: TargetKey, create: CommandSeq) -> bool {
        self.records.iter().any(|r| {
            r.lease.target == target && r.lease.create == create && r.phase == Phase::Current
        })
    }

    pub fn retired(&self, lease: Lease) -> bool {
        self.records
            .iter()
            .any(|r| r.lease == lease && r.phase == Phase::Retired)
    }

    /// The caller verifies native issuance, current Scene/admission and, for
    /// Retired, the actual renderer fence before accepting the SDK receipt.
    pub fn observe(&mut self, lease: Lease, phase: SdkEvidencePhase) -> Status {
        let Some(record) = self.records.iter_mut().find(|r| r.lease == lease) else {
            return Status::Stale;
        };
        match phase {
            SdkEvidencePhase::Current => {
                if !matches!(record.phase, Phase::Registered | Phase::Current) {
                    return Status::Stale;
                }
                record.phase = Phase::Current;
            }
            SdkEvidencePhase::Retiring => {
                if record.phase != Phase::Retired {
                    record.phase = Phase::Retiring;
                }
            }
            SdkEvidencePhase::Retired => {
                if !matches!(record.phase, Phase::Retiring | Phase::Retired) {
                    return Status::NotReady;
                }
                record.phase = Phase::Retired;
            }
        }
        Status::Accepted
    }

    pub fn seal_target(&mut self, target: TargetKey) {
        for record in &mut self.records {
            if record.lease.target == target && record.phase != Phase::Retired {
                record.phase = Phase::Retiring;
            }
        }
    }

    pub fn seal_scene(&mut self, scene: SceneEpoch) {
        for record in &mut self.records {
            if record.lease.target.scene == scene && record.phase != Phase::Retired {
                record.phase = Phase::Retiring;
            }
        }
    }

    pub fn seal(&mut self) {
        for record in &mut self.records {
            if record.phase != Phase::Retired {
                record.phase = Phase::Retiring;
            }
        }
    }

    pub fn forget_window(&mut self, window: WindowId) {
        self.records
            .retain(|r| r.lease.target.window != window || r.phase != Phase::Retired);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lease(generation: i64, adapter: u64) -> Lease {
        Lease {
            target: TargetKey::from_raw(1, 1, 1, generation).unwrap(),
            create: CommandSeq::new(generation).unwrap(),
            adapter,
        }
    }

    #[test]
    fn current_requires_registered_exact_adapter_and_cannot_resurrect_after_seal() {
        let mut ledger = Ledger::default();
        let old = lease(1, 1);
        assert_eq!(
            ledger.observe(old, SdkEvidencePhase::Current),
            Status::Stale
        );
        assert_eq!(ledger.register(old), Status::Accepted);
        assert_eq!(
            ledger.observe(lease(1, 2), SdkEvidencePhase::Current),
            Status::Stale
        );
        assert_eq!(
            ledger.observe(old, SdkEvidencePhase::Current),
            Status::Accepted
        );
        assert!(ledger.current(old.target, old.create));
        ledger.seal();
        assert!(!ledger.current(old.target, old.create));
        assert_eq!(
            ledger.observe(old, SdkEvidencePhase::Current),
            Status::Stale
        );
        assert_eq!(ledger.register(old), Status::Stale);
        assert_eq!(
            ledger.observe(old, SdkEvidencePhase::Retired),
            Status::Accepted
        );
        assert_eq!(
            ledger.observe(old, SdkEvidencePhase::Retired),
            Status::Accepted
        );
    }

    #[test]
    fn old_generation_receipt_does_not_change_new_current_binding() {
        let mut ledger = Ledger::default();
        let old = lease(1, 1);
        let new = lease(2, 2);
        assert_eq!(ledger.register(old), Status::Accepted);
        ledger.seal_target(old.target);
        assert_eq!(ledger.register(new), Status::Accepted);
        assert_eq!(
            ledger.observe(new, SdkEvidencePhase::Current),
            Status::Accepted
        );
        assert_eq!(
            ledger.observe(old, SdkEvidencePhase::Retired),
            Status::Accepted
        );
        assert!(ledger.current(new.target, new.create));
        assert!(!ledger.current(new.target, old.create));
        assert_eq!(
            ledger.observe(lease(3, 1), SdkEvidencePhase::Retired),
            Status::Stale
        );
    }

    #[test]
    fn only_completed_receipts_can_be_compacted_under_churn() {
        let mut ledger = Ledger::default();
        for generation in 1..=1000 {
            let id = lease(generation, 1);
            assert_eq!(ledger.register(id), Status::Accepted);
            ledger.seal_target(id.target);
            assert_eq!(
                ledger.observe(id, SdkEvidencePhase::Retired),
                Status::Accepted
            );
            assert!(ledger.records.len() <= crate::linux_desktop_v3::MAX_RETAINED_WINDOWS);
        }
        let mut pending = Ledger::default();
        for generation in 1..=64 {
            assert_eq!(pending.register(lease(generation, 1)), Status::Accepted);
        }
        assert_eq!(pending.register(lease(65, 1)), Status::Busy);
        assert_eq!(pending.records.len(), 64);
    }
}
