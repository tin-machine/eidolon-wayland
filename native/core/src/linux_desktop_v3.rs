//! Verified I3 window provenance and bounded history for a single v3 launch.
//!
//! Only the compositor owner may feed WindowSample values. This module never
//! creates NativeOrigin from app_id, PID, title, JSON or SDK diagnostics.
//! B1 Startup is the sole authority for first main adoption: commit_main()
//! is called only after V3PresentationObserver::tick() returns that decision.
use eidolon_model::{
    ids::WindowId,
    linux_desktop_v3::{
        AppId,
        presentation::{Life, NativeOrigin, NativeWindowFact, Parent, Role},
    },
};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone)]
pub struct WindowSample {
    pub origin: Option<NativeOrigin>,
    pub window: WindowId,
    pub app_id: Option<String>,
    pub parent: Parent,
    pub life: Life,
}

struct Entry {
    sample: WindowSample,
    role: Option<Role>,
    /// A destroyed toplevel remains until native/SDK retirement is observed.
    retirement_pending: bool,
}

pub struct I3WindowCut {
    pub origin: NativeOrigin,
    pub global_window_count: u32,
    pub windows: Vec<NativeWindowFact>,
    pub admitted: BTreeSet<WindowId>,
    pub fixed_main: Option<WindowId>,
    pub admission_open: bool,
}

// Retired/awaiting-ACK records must remain available for reclamation.
// Once the bounded owner ledger is full, fail closed rather than dropping
// unresolved SDK/native receipts or consuming unbounded memory.
pub(crate) const MAX_RETAINED_WINDOWS: usize = 64;

pub struct WindowHistory {
    origin: NativeOrigin,
    before: BTreeSet<WindowId>,
    entries: BTreeMap<WindowId, Entry>,
    // The compositor's per-session WindowId allocator (snapshot.rs::register_toplevel)
    // uses checked_next() and never reuses an ID. Preserve the highest
    // *confirmed retired* ID without retaining an unbounded tombstone set.
    // Live entries may have lower IDs than this floor.
    retired_floor: i64,
    fixed_main: Option<WindowId>,
    started: bool,
    admission_open: bool,
}
impl WindowHistory {
    pub fn new(origin: NativeOrigin, before: impl IntoIterator<Item = WindowId>) -> Self {
        Self {
            origin,
            before: before.into_iter().collect(),
            entries: BTreeMap::new(),
            retired_floor: 0,
            fixed_main: None,
            started: false,
            admission_open: true,
        }
    }

    pub fn origin(&self) -> &NativeOrigin {
        &self.origin
    }
    pub fn fixed_main(&self) -> Option<WindowId> {
        self.fixed_main
    }
    pub fn started(&mut self) {
        if self.admission_open {
            self.started = true;
        }
    }
    pub fn seal(&mut self) {
        self.admission_open = false;
    }
    pub fn is_open(&self) -> bool {
        self.admission_open
    }
    pub fn pending_retirements(&self) -> usize {
        self.entries
            .values()
            .filter(|e| e.retirement_pending)
            .count()
    }
    pub fn retained_entries(&self) -> usize {
        self.entries.len()
    }

    /// An observed Destroy is not a native/SDK retirement acknowledgment.
    /// Only the owner supplying both proofs may release the tombstone.
    pub fn confirm_retired(&mut self, window: WindowId, native: bool, sdk: bool) -> bool {
        let confirmed = native
            && sdk
            && self
                .entries
                .get(&window)
                .is_some_and(|e| e.retirement_pending);
        if !confirmed {
            return false;
        }
        // A retired ID can never be admitted again within this session.
        // Importantly, raising the floor must not revoke *other live IDs*.
        self.entries.remove(&window);
        self.retired_floor = self.retired_floor.max(window.get());
        true
    }

    /// Reconcile one protocol dispatch, including disappearances observed
    /// after xdg_toplevel.destroy. Parent evidence must be captured BEFORE
    /// Smithay clears role attributes on unmap/destroy.
    pub fn observe(&mut self, samples: &[WindowSample]) -> Result<(), &'static str> {
        if !self.admission_open {
            return if samples.is_empty() {
                Ok(())
            } else {
                Err("STALE")
            };
        }
        if samples.len() > 9 {
            return Err("APP_WINDOW_UNSUPPORTED");
        }
        let mut seen = BTreeSet::new();
        for sample in samples {
            if sample.origin.as_ref() != Some(&self.origin)
                || !seen.insert(sample.window)
                // Existing live IDs predate a newer dialog's retirement and
                // remain valid. An unknown ID below the retired high-water
                // mark is a stale/reused ID and must be rejected instead.
                || (sample.window.get() <= self.retired_floor
                    && !self.entries.contains_key(&sample.window))
            {
                return Err("APP_ORIGIN_UNKNOWN");
            }
            if self.entries.get(&sample.window).is_some_and(|old| {
                old.retirement_pending
                    || old.sample.origin != sample.origin
                    || (old.sample.life == Life::Destroyed && sample.life != Life::Destroyed)
                    || (old.role == Some(Role::Main)
                        && (!matches!(sample.parent, Parent::None {})
                            || sample.app_id.as_deref() != Some(self.main_app_id().as_str())))
                    || (old.role == Some(Role::Dialog) && matches!(sample.parent, Parent::None {}))
            }) {
                return Err("APP_WINDOW_UNSUPPORTED");
            }
        }
        let newly_observed = samples
            .iter()
            .filter(|sample| !self.entries.contains_key(&sample.window))
            .count();
        if newly_observed > MAX_RETAINED_WINDOWS.saturating_sub(self.entries.len()) {
            self.seal();
            return Err("APP_WINDOW_UNSUPPORTED");
        }
        for (id, entry) in &mut self.entries {
            if !seen.contains(id) && entry.sample.life != Life::Destroyed {
                entry.sample.life = Life::Destroyed;
                entry.retirement_pending = true;
            }
        }
        for sample in samples {
            if let Some(entry) = self.entries.get_mut(&sample.window) {
                // Never promote an unresolved/lost parent to an explicit root.
                if !matches!(entry.sample.parent, Parent::None {})
                    && matches!(sample.parent, Parent::None {})
                {
                    return Err("APP_WINDOW_UNSUPPORTED");
                }
                entry.sample = sample.clone();
                if sample.life == Life::Destroyed {
                    entry.retirement_pending = true;
                }
            } else {
                self.entries.insert(
                    sample.window,
                    Entry {
                        sample: sample.clone(),
                        role: None,
                        retirement_pending: sample.life == Life::Destroyed,
                    },
                );
            }
        }
        if self.fixed_main.is_some_and(|main| {
            self.entries
                .get(&main)
                .is_none_or(|e| e.sample.life == Life::Destroyed)
        }) {
            self.seal();
            return Err("APP_WINDOW_UNSUPPORTED");
        }
        Ok(())
    }

    fn main_app_id(&self) -> String {
        // The B1 manifest binds Editor to Mousepad and Demo to the verified
        // nonce-derived fixture ID. Never use a remote supplied app identifier.
        match self.origin.app_id() {
            AppId::Editor => "mousepad".into(),
            AppId::Demo => {
                // Demo is intentionally not a product v3 launch.
                String::new()
            }
        }
    }

    fn chain_to_main(&self, id: WindowId, main: WindowId) -> bool {
        let mut visited = BTreeSet::from([id]);
        let mut cursor = id;
        loop {
            let Some(entry) = self.entries.get(&cursor) else {
                return false;
            };
            if entry.sample.life == Life::Destroyed
                || entry.sample.origin.as_ref() != Some(&self.origin)
            {
                return false;
            }
            let Parent::Resolved { window: parent } = entry.sample.parent else {
                return false;
            };
            if !visited.insert(parent) {
                return false;
            }
            let Some(ancestor) = self.entries.get(&parent) else {
                return false;
            };
            // A mapped child cannot remain admitted through an unmapped
            // parent. Its native surface may survive, but the parent chain
            // is no longer a live *mapped* display path (I3-T06/T07/T09).
            if ancestor.sample.origin.as_ref() != Some(&self.origin)
                || ancestor.sample.life != Life::Mapped
            {
                return false;
            }
            if parent == main {
                return true;
            }
            cursor = parent;
        }
    }

    /// Candidate facts for the one B1 Startup reducer; not a second admission
    /// state machine. Call only after this tick's Wayland dispatch and I2 cut.
    pub fn cut(&mut self) -> Result<I3WindowCut, &'static str> {
        let active = self
            .entries
            .values()
            .filter(|e| e.sample.life != Life::Destroyed)
            .count();
        if active > 9 {
            return Err("APP_WINDOW_UNSUPPORTED");
        }
        let main_app = self.main_app_id();
        // Any second explicit root (even with a forged app_id) is unsupported.
        let all_roots: Vec<_> = self
            .entries
            .iter()
            .filter(|(_, e)| {
                e.sample.life != Life::Destroyed && matches!(e.sample.parent, Parent::None {})
            })
            .map(|(id, _)| *id)
            .collect();
        let roots: Vec<_> = all_roots
            .iter()
            .copied()
            .filter(|id| {
                self.entries.get(id).is_some_and(|e| {
                    e.sample.app_id.as_deref() == Some(main_app.as_str())
                        && !self.before.contains(id)
                })
            })
            .collect();
        if all_roots.len() > 1
            || self
                .fixed_main
                .is_some_and(|main| all_roots.iter().any(|id| *id != main))
        {
            self.seal();
            return Err("APP_WINDOW_UNSUPPORTED");
        }
        let candidate = self.fixed_main.or_else(|| roots.first().copied());
        let mut eligible = BTreeMap::new();
        if let Some(id) = candidate {
            eligible.insert(id, Role::Main);
        }
        if let Some(main) = candidate {
            for (id, entry) in &self.entries {
                if *id != main
                    && entry.sample.life != Life::Destroyed
                    && self.chain_to_main(*id, main)
                {
                    eligible.insert(*id, Role::Dialog);
                }
            }
        }
        for (id, role) in &eligible {
            if let Some(entry) = self.entries.get_mut(id) {
                if entry.role.is_some_and(|old| old != *role) {
                    self.seal();
                    return Err("APP_WINDOW_UNSUPPORTED");
                }
                entry.role = Some(*role);
            }
        }
        // B1 may see a candidate before Started, but cannot adopt it until
        // I2 reports Started. Only committed main/dialog enter product panels.
        let mut windows = Vec::new();
        for (id, role) in eligible {
            let e = &self.entries[&id];
            windows.push(NativeWindowFact {
                origin: self.origin.clone(),
                window: id,
                role,
                life: e.sample.life,
                app_id: e.sample.app_id.clone(),
                parent: e.sample.parent,
            });
        }
        // A destroyed dialog's slot is still owned until *both* native and
        // SDK retirement are certified. Preserve slots for unmapped live
        // windows as well. Otherwise a new dialog can steal a retiring
        // panel's slot before its renderer/SDK generation is reaped.
        let held_slots = self
            .entries
            .values()
            .filter(|e| e.retirement_pending || e.sample.life == Life::Unmapped)
            .count();
        let free_slots = 9usize.saturating_sub(held_slots);
        let admitted = if self.started && self.fixed_main.is_some() && self.admission_open {
            windows
                .iter()
                .filter(|w| w.life == Life::Mapped)
                .take(free_slots)
                .map(|w| w.window)
                .collect()
        } else {
            BTreeSet::new()
        };
        Ok(I3WindowCut {
            origin: self.origin.clone(),
            global_window_count: active as u32,
            windows,
            admitted,
            fixed_main: self.fixed_main,
            admission_open: self.admission_open,
        })
    }

    /// Only a newly_adopted_main returned by I4 may permanently fix the root.
    pub fn commit_main(&mut self, id: WindowId) -> Result<(), &'static str> {
        if !self.admission_open || !self.started || self.fixed_main.is_some() {
            return Err("APP_WINDOW_UNSUPPORTED");
        }
        let cut = self.cut()?;
        if !cut
            .windows
            .iter()
            .any(|w| w.window == id && w.role == Role::Main && w.life == Life::Mapped)
        {
            return Err("APP_WINDOW_UNSUPPORTED");
        }
        self.fixed_main = Some(id);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn origin() -> NativeOrigin {
        let f: serde_json::Value = serde_json::from_str(include_str!(
            "../../../scripts/tests/fixtures/linux-desktop-v3-contract.json"
        ))
        .unwrap();
        let raw = f["records"]
            .as_array()
            .unwrap()
            .iter()
            .find(|r| r["id"] == "origin")
            .unwrap()["raw"]
            .as_str()
            .unwrap();
        NativeOrigin::parse(raw.as_bytes()).unwrap()
    }
    fn sample(origin: &NativeOrigin, id: i64, parent: Parent, life: Life) -> WindowSample {
        WindowSample {
            origin: Some(origin.clone()),
            window: WindowId::new(id).unwrap(),
            app_id: Some("mousepad".into()),
            parent,
            life,
        }
    }
    #[test]
    fn started_and_mapped_are_order_independent_but_adoption_is_external() {
        for started_first in [true, false] {
            let o = origin();
            let mut h = WindowHistory::new(o.clone(), []);
            if started_first {
                h.started()
            }
            h.observe(&[sample(&o, 42, Parent::None {}, Life::Mapped)])
                .unwrap();
            if !started_first {
                h.started()
            }
            let cut = h.cut().unwrap();

            assert!(cut.admitted.is_empty());
            h.commit_main(WindowId::new(42).unwrap()).unwrap();
            assert!(
                h.cut()
                    .unwrap()
                    .admitted
                    .contains(&WindowId::new(42).unwrap())
            );
            assert!(h.commit_main(WindowId::new(42).unwrap()).is_err());
        }
    }
    #[test]
    fn second_root_and_unmapped_parent_promotion_fail_closed() {
        let o = origin();
        let mut h = WindowHistory::new(o.clone(), []);
        h.started();
        h.observe(&[sample(&o, 1, Parent::None {}, Life::Mapped)])
            .unwrap();
        h.commit_main(WindowId::new(1).unwrap()).unwrap();
        h.observe(&[
            sample(&o, 1, Parent::None {}, Life::Mapped),
            sample(&o, 2, Parent::None {}, Life::Mapped),
        ])
        .unwrap();
        assert!(h.cut().is_err());
        let mut h = WindowHistory::new(o.clone(), []);
        h.observe(&[sample(&o, 1, Parent::Unresolved {}, Life::Unmapped)])
            .unwrap();
        assert!(
            h.observe(&[sample(&o, 1, Parent::None {}, Life::Mapped)])
                .is_err()
        );
    }
    #[test]
    fn foreign_origin_and_unknown_parent_never_admitted() {
        let o = origin();
        let mut h = WindowHistory::new(o.clone(), []);
        let mut s = sample(&o, 1, Parent::None {}, Life::Mapped);
        s.origin = None;
        assert!(h.observe(&[s]).is_err());
        h.started();
        h.observe(&[sample(&o, 1, Parent::None {}, Life::Mapped)])
            .unwrap();
        h.commit_main(WindowId::new(1).unwrap()).unwrap();
        h.observe(&[
            sample(&o, 1, Parent::None {}, Life::Mapped),
            sample(
                &o,
                2,
                Parent::Resolved {
                    window: WindowId::new(99).unwrap(),
                },
                Life::Mapped,
            ),
        ])
        .unwrap();
        assert!(
            !h.cut()
                .unwrap()
                .admitted
                .contains(&WindowId::new(2).unwrap())
        );
    }
    #[test]
    fn ancestor_unmap_revokes_mapped_descendants_and_remap_keeps_window_ids() {
        let o = origin();
        let mut h = WindowHistory::new(o.clone(), []);
        let main = sample(&o, 1, Parent::None {}, Life::Mapped);
        let child_parent = Parent::Resolved {
            window: WindowId::new(1).unwrap(),
        };
        let grandchild_parent = Parent::Resolved {
            window: WindowId::new(2).unwrap(),
        };
        let child = sample(&o, 2, child_parent, Life::Mapped);
        let grandchild = sample(&o, 3, grandchild_parent, Life::Mapped);
        h.started();
        h.observe(std::slice::from_ref(&main)).unwrap();
        h.commit_main(main.window).unwrap();
        h.observe(&[main.clone(), child.clone(), grandchild.clone()])
            .unwrap();
        assert_eq!(h.cut().unwrap().admitted.len(), 3);

        // A still-mapped grandchild cannot bypass its unmapped intermediate
        // parent and continue receiving input under the old parent chain.
        h.observe(&[
            main.clone(),
            sample(&o, 2, child_parent, Life::Unmapped),
            grandchild.clone(),
        ])
        .unwrap();
        assert_eq!(h.cut().unwrap().admitted, BTreeSet::from([main.window]));
        assert_eq!(h.retained_entries(), 3);

        h.observe(&[main.clone(), child.clone(), grandchild.clone()])
            .unwrap();
        assert_eq!(h.cut().unwrap().admitted.len(), 3);
        assert_eq!(h.retained_entries(), 3);
        // A remap does not mint a new WindowId or erase the dialog role.
        h.observe(&[
            main.clone(),
            sample(&o, 2, Parent::Unresolved {}, Life::Mapped),
            grandchild,
        ])
        .unwrap();
        assert_eq!(h.cut().unwrap().admitted, BTreeSet::from([main.window]));
        assert_eq!(
            h.observe(&[main, sample(&o, 2, Parent::None {}, Life::Mapped)]),
            Err("APP_WINDOW_UNSUPPORTED")
        );
    }

    #[test]
    fn parent_unmap_revokes_even_when_child_remains_mapped() {
        let o = origin();
        let mut h = WindowHistory::new(o.clone(), []);
        let main = sample(&o, 11, Parent::None {}, Life::Mapped);
        let dialog = sample(
            &o,
            12,
            Parent::Resolved {
                window: main.window,
            },
            Life::Mapped,
        );
        h.started();
        h.observe(std::slice::from_ref(&main)).unwrap();
        h.commit_main(main.window).unwrap();
        h.observe(&[main.clone(), dialog.clone()]).unwrap();
        assert_eq!(h.cut().unwrap().admitted.len(), 2);
        h.observe(&[sample(&o, 11, Parent::None {}, Life::Unmapped), dialog])
            .unwrap();
        assert!(h.cut().unwrap().admitted.is_empty());
    }

    #[test]
    fn retired_id_cannot_reappear_while_an_older_main_remains_live() {
        let o = origin();
        let mut h = WindowHistory::new(o.clone(), []);
        let main = sample(&o, 1, Parent::None {}, Life::Mapped);
        let dialog = sample(
            &o,
            2,
            Parent::Resolved {
                window: WindowId::new(1).unwrap(),
            },
            Life::Mapped,
        );
        h.started();
        h.observe(std::slice::from_ref(&main)).unwrap();
        h.commit_main(main.window).unwrap();
        h.observe(&[main.clone(), dialog.clone()]).unwrap();
        h.observe(std::slice::from_ref(&main)).unwrap();
        assert!(!h.confirm_retired(dialog.window, true, false));
        assert_eq!(h.pending_retirements(), 1);
        assert!(h.confirm_retired(dialog.window, true, true));
        for _ in 0..10 {
            h.observe(std::slice::from_ref(&main)).unwrap();
            assert_eq!(h.cut().unwrap().admitted.len(), 1);
        }
        assert_eq!(h.observe(&[main, dialog]), Err("APP_ORIGIN_UNKNOWN"));
    }

    #[test]
    fn retired_dialog_below_main_and_out_of_order_samples_preserve_identity() {
        let o = origin();
        let mut h = WindowHistory::new(o.clone(), []);
        let main = sample(&o, 10, Parent::None {}, Life::Mapped);
        let older_dialog = sample(
            &o,
            3,
            Parent::Resolved {
                window: WindowId::new(10).unwrap(),
            },
            Life::Mapped,
        );
        h.started();
        h.observe(&[older_dialog.clone(), main.clone()]).unwrap();
        h.commit_main(main.window).unwrap();
        h.observe(std::slice::from_ref(&main)).unwrap();
        assert!(h.confirm_retired(older_dialog.window, true, true));
        let newer_dialog = sample(
            &o,
            12,
            Parent::Resolved {
                window: WindowId::new(10).unwrap(),
            },
            Life::Mapped,
        );
        h.observe(&[newer_dialog.clone(), main.clone()]).unwrap();
        h.observe(std::slice::from_ref(&main)).unwrap();
        assert!(h.confirm_retired(newer_dialog.window, true, true));
        h.observe(&[main]).unwrap();
        assert_eq!(h.cut().unwrap().admitted.len(), 1);
        assert_eq!(h.observe(&[older_dialog]), Err("APP_ORIGIN_UNKNOWN"));
    }

    #[test]
    fn missing_retirement_ack_keeps_tombstone_and_blocks_id_reuse() {
        let o = origin();
        let mut h = WindowHistory::new(o.clone(), []);
        let main = sample(&o, 1, Parent::None {}, Life::Mapped);
        let dialog = sample(
            &o,
            2,
            Parent::Resolved {
                window: WindowId::new(1).unwrap(),
            },
            Life::Mapped,
        );
        h.started();
        h.observe(std::slice::from_ref(&main)).unwrap();
        h.commit_main(main.window).unwrap();
        h.observe(&[main.clone(), dialog.clone()]).unwrap();
        h.observe(std::slice::from_ref(&main)).unwrap();
        assert!(!h.confirm_retired(dialog.window, false, true));
        assert!(!h.confirm_retired(dialog.window, true, false));
        assert_eq!(h.pending_retirements(), 1);
        assert_eq!(
            h.observe(&[main.clone(), dialog.clone()]),
            Err("APP_WINDOW_UNSUPPORTED")
        );
        assert!(h.confirm_retired(dialog.window, true, true));
        assert!(!h.confirm_retired(dialog.window, true, true));
        h.observe(&[main]).unwrap();
        assert_eq!(h.retained_entries(), 1);
    }

    #[test]
    fn fresh_history_never_inherits_previous_session_retirements() {
        // WindowId uniqueness is scoped to ProtocolState/Session rather than
        // globally. Reusing numeric IDs in a *fresh* owner is permitted; old
        // origin/SDK evidence still needs independent generation validation.
        let o = origin();
        let mut old = WindowHistory::new(o.clone(), []);
        old.observe(&[sample(&o, 1, Parent::None {}, Life::Mapped)])
            .unwrap();
        old.observe(&[]).unwrap();
        assert!(old.confirm_retired(WindowId::new(1).unwrap(), true, true));
        assert_eq!(
            old.observe(&[sample(&o, 1, Parent::None {}, Life::Mapped)]),
            Err("APP_ORIGIN_UNKNOWN")
        );

        let mut fresh = WindowHistory::new(o.clone(), []);
        fresh
            .observe(&[sample(&o, 1, Parent::None {}, Life::Mapped)])
            .unwrap();
        assert_eq!(fresh.retained_entries(), 1);
    }

    #[test]
    fn missing_acks_fail_closed_at_retirement_ledger_capacity() {
        let o = origin();
        let mut h = WindowHistory::new(o.clone(), []);
        let main = sample(&o, 1, Parent::None {}, Life::Mapped);
        h.started();
        h.observe(std::slice::from_ref(&main)).unwrap();
        h.commit_main(main.window).unwrap();
        for id in 2..=64 {
            let dialog = sample(
                &o,
                id,
                Parent::Resolved {
                    window: main.window,
                },
                Life::Mapped,
            );
            h.observe(&[main.clone(), dialog]).unwrap();
            h.observe(std::slice::from_ref(&main)).unwrap();
        }
        assert_eq!(h.pending_retirements(), 63);
        assert_eq!(h.retained_entries(), MAX_RETAINED_WINDOWS);
        let overflow = sample(
            &o,
            65,
            Parent::Resolved {
                window: main.window,
            },
            Life::Mapped,
        );
        assert_eq!(h.observe(&[main, overflow]), Err("APP_WINDOW_UNSUPPORTED"));
        assert!(!h.is_open());
        assert_eq!(h.retained_entries(), MAX_RETAINED_WINDOWS);
    }

    #[test]
    fn retiring_dialog_reserves_slot_until_both_acks_without_revoking_survivors() {
        let o = origin();
        let mut h = WindowHistory::new(o.clone(), []);
        let main = sample(&o, 1, Parent::None {}, Life::Mapped);
        h.started();
        h.observe(std::slice::from_ref(&main)).unwrap();
        h.commit_main(main.window).unwrap();
        let mut nine = vec![main.clone()];
        for id in 2..=9 {
            nine.push(sample(
                &o,
                id,
                Parent::Resolved {
                    window: main.window,
                },
                Life::Mapped,
            ));
        }
        h.observe(&nine).unwrap();
        assert_eq!(h.cut().unwrap().admitted.len(), 9);

        let retired = nine.pop().unwrap();
        h.observe(&nine).unwrap();
        assert_eq!(h.pending_retirements(), 1);
        let new_dialog = sample(
            &o,
            10,
            Parent::Resolved {
                window: main.window,
            },
            Life::Mapped,
        );
        nine.push(new_dialog.clone());
        h.observe(&nine).unwrap();
        let pending_cut = h.cut().unwrap();
        assert_eq!(pending_cut.global_window_count, 9);
        assert_eq!(pending_cut.admitted.len(), 8);
        assert!(!pending_cut.admitted.contains(&new_dialog.window));
        assert!(pending_cut.admitted.contains(&main.window));
        assert!(!h.confirm_retired(retired.window, true, false));
        assert_eq!(h.cut().unwrap().admitted.len(), 8);
        assert!(h.confirm_retired(retired.window, true, true));
        let released_cut = h.cut().unwrap();
        assert_eq!(released_cut.admitted.len(), 9);
        assert!(released_cut.admitted.contains(&new_dialog.window));
        assert!(!h.confirm_retired(retired.window, true, true));
    }

    #[test]
    fn churn_requires_retirement_proof_and_remains_bounded() {
        let o = origin();
        let mut h = WindowHistory::new(o.clone(), []);
        h.started();
        h.observe(&[sample(&o, 1, Parent::None {}, Life::Mapped)])
            .unwrap();
        h.commit_main(WindowId::new(1).unwrap()).unwrap();
        for id in 2..=1002 {
            let dialog = sample(
                &o,
                id,
                Parent::Resolved {
                    window: WindowId::new(1).unwrap(),
                },
                Life::Mapped,
            );
            h.observe(&[sample(&o, 1, Parent::None {}, Life::Mapped), dialog])
                .unwrap();
            assert_eq!(h.cut().unwrap().admitted.len(), 2);
            h.observe(&[sample(&o, 1, Parent::None {}, Life::Mapped)])
                .unwrap();
            assert_eq!(h.pending_retirements(), 1);
            let id = WindowId::new(id).unwrap();
            assert!(!h.confirm_retired(id, true, false));
            assert!(h.confirm_retired(id, true, true));
            assert_eq!(h.retained_entries(), 1);
        }
    }
}
