//! I4's B1 projection point. I2's verified owner cut and I3's window-history
//! cut must be assembled by the core owner before calling tick(). This module
//! never guesses an origin, SDK binding, ACK or current generation.
use eidolon_model::{
    ids::WindowId,
    linux_desktop_v3::{
        Error, Result,
        presentation::{NativeOrigin, Observation, PresentationState, Startup},
        report::{PresentationSummary, SdkAction, SdkTarget},
    },
};

pub struct V3PresentationObserver {
    startup: Startup,
}

pub struct ObservedPresentation {
    pub presentation: PresentationSummary,
    pub sdk_target: Option<SdkTarget>,
    /// I3 commits this candidate before issuing any Create. Never expose the
    /// candidate through the public input/render-admitted set beforehand.
    pub newly_adopted_main: Option<WindowId>,
    /// Only exact current evidence from the same Observation permits input.
    pub main_input_eligible: bool,
}

impl V3PresentationObserver {
    pub fn begin(origin: NativeOrigin, accepted_at_ms: u64, before: Vec<WindowId>) -> Result<Self> {
        Ok(Self {
            startup: Startup::begin(origin, accepted_at_ms, before)?,
        })
    }

    pub fn tick(&mut self, observation: &Observation) -> Result<ObservedPresentation> {
        let previous = PresentationSummary::from_startup(&self.startup)?;
        self.startup = match self.startup.advance(observation) {
            Ok(next) => next,
            Err(error) => {
                // An old Ready must not remain visible after a model invariant
                // failed. Keep first_failure and the B1 adoption/deadlines.
                self.startup = self.startup.fail_observation(&error, true);
                return Err(error);
            }
        };
        // B1's immutable role history is a second retained ledger. Until
        // certified native+SDK retirement is wired to B1's GC port, fail
        // closed on exhaustion instead of growing indefinitely or silently
        // dropping role identity. This does not change the B1 golden model.
        if self.startup.role_history_len() > crate::linux_desktop_v3::MAX_RETAINED_WINDOWS {
            let error = Error("APP_WINDOW_UNSUPPORTED");
            self.startup = self.startup.fail_observation(&error, true);
            return Err(error);
        }
        let presentation = PresentationSummary::from_startup(&self.startup)?;
        let current = observation.current_evidence();
        let sdk_target = current.as_ref().map(|e| SdkTarget {
            runtime: e.runtime,
            target: e.target,
            create_command: e.create_command,
            action: SdkAction::Create,
            acknowledged: e.acknowledged,
            binding_present: e.binding_present,
            renderer_frame_seen: e.renderer_frame_seen,
        });
        let newly_adopted_main = if previous.main.is_none() {
            presentation.main
        } else {
            None
        };
        let main_input_eligible = presentation.state == PresentationState::Ready
            && presentation.startup_completed
            && current
                .as_ref()
                .is_some_and(|e| e.acknowledged && e.binding_present && e.renderer_frame_seen);
        Ok(ObservedPresentation {
            presentation,
            sdk_target,
            newly_adopted_main,
            main_input_eligible,
        })
    }

    pub(crate) fn release_startup_main(
        &mut self,
        origin: &NativeOrigin,
        main: WindowId,
        now_ms: u64,
    ) -> Result<()> {
        self.startup.release_startup_main(origin, main, now_ms)
    }

    pub fn snapshot(&self) -> Result<PresentationSummary> {
        PresentationSummary::from_startup(&self.startup)
    }

    pub fn startup(&self) -> &Startup {
        &self.startup
    }

    /// The same native/SDK proof releases I3 history and B1's role cache.
    /// Keep the current main identity while it is still in the presentation.
    pub(crate) fn retire_confirmed_window(
        &mut self,
        proof: &crate::v3_retirement::WindowRetirementProof,
    ) -> Result<()> {
        if self.current() != proof.origin() {
            return Err(Error("APP_ORIGIN_UNKNOWN"));
        }
        if self.snapshot()?.main != Some(proof.window()) {
            self.startup.retire_confirmed_dialog(proof.window())?;
        }
        Ok(())
    }

    /// Only the native owner may use this output for Report::validate_current.
    pub fn current(&self) -> &NativeOrigin {
        self.startup.origin()
    }

    pub fn fail(&mut self, error: &Error) {
        self.startup = self.startup.fail_observation(error, true);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use eidolon_model::{
        ids::{SceneEpoch, WindowId},
        linux_desktop_v3::presentation::{Life, NativeWindowFact, Parent, Role},
    };

    fn origin() -> NativeOrigin {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../../scripts/tests/fixtures/linux-desktop-v3-contract.json"
        ))
        .unwrap();
        let item = fixture["records"]
            .as_array()
            .unwrap()
            .iter()
            .find(|v| v["id"] == "origin")
            .unwrap();
        NativeOrigin::parse(item["raw"].as_str().unwrap().as_bytes()).unwrap()
    }

    #[test]
    fn start_then_map_adopts_once_and_does_not_issue_create_on_adoption_tick() {
        let owner = origin();
        let main = WindowId::new(77).unwrap();
        let scene = SceneEpoch::new(3).unwrap();
        let mut observer = V3PresentationObserver::begin(owner.clone(), 1000, vec![]).unwrap();
        let first = Observation::native_builder(owner.clone(), 1500)
            .lifecycle(true, Some(scene), true)
            .transport(Some(owner.clone()), None, false, false)
            .finish()
            .unwrap();
        let result = observer.tick(&first).unwrap();
        assert_eq!(result.presentation.state, PresentationState::AwaitingMain);
        assert_eq!(result.newly_adopted_main, None);
        let mapped = Observation::native_builder(owner.clone(), 1600)
            .lifecycle(true, Some(scene), true)
            .transport(Some(owner.clone()), None, false, false)
            .windows(
                1,
                vec![NativeWindowFact {
                    origin: owner.clone(),
                    window: main,
                    role: Role::Main,
                    life: Life::Mapped,
                    app_id: Some("mousepad".into()),
                    parent: Parent::None {},
                }],
            )
            .finish()
            .unwrap();
        let result = observer.tick(&mapped).unwrap();
        assert_eq!(result.newly_adopted_main, Some(main));
        assert_eq!(result.presentation.state, PresentationState::AwaitingSdk);
        assert!(
            result.sdk_target.is_none(),
            "main adoption is not SDK proof"
        );
        assert!(!result.main_input_eligible);
        let repeated = observer.tick(&mapped);
        assert!(repeated.is_ok(), "same tick time is monotonic");
        assert_eq!(repeated.unwrap().newly_adopted_main, None);
    }

    #[test]
    fn desktop_handoff_requires_fresh_sdk_evidence_and_cannot_extend_wait() {
        use eidolon_model::{
            ids::CommandSeq,
            linux_desktop_v3::presentation::{
                MAIN_WAIT_MS, NativeAckFact, NativeCreateIdentity, NativeFrameFact,
                NativePanelFact, NativeSdkFact,
            },
            surface::TargetKey,
        };
        let mut record = origin().record();
        record["app_id"] = format!("desktop-entry:{}", "a".repeat(64)).into();
        let owner = NativeOrigin::parse(&serde_json::to_vec(&record).unwrap()).unwrap();
        let scene = SceneEpoch::new(1).unwrap();
        let splash = WindowId::new(1).unwrap();
        let editor = WindowId::new(2).unwrap();
        let observe = |now, root: Option<WindowId>, evidence: Option<WindowId>| {
            let windows: Vec<_> = root
                .into_iter()
                .map(|window| NativeWindowFact {
                    origin: owner.clone(),
                    window,
                    life: Life::Mapped,
                    role: Role::Main,
                    app_id: None,
                    parent: Parent::None {},
                })
                .collect();
            let mut obs = Observation::native_builder(owner.clone(), now)
                .lifecycle(true, Some(scene), true)
                .transport(Some(owner.clone()), None, false, false)
                .windows(windows.len() as u32, windows);
            if let Some(window) = evidence {
                let runtime = owner.runtime();
                let target =
                    TargetKey::from_raw(owner.session().get(), window.get(), scene.get(), 1)
                        .unwrap();
                let create = NativeCreateIdentity {
                    runtime,
                    target,
                    command_seq: CommandSeq::new(window.get()).unwrap(),
                };
                obs = obs
                    .current_create(Some(create.clone()))
                    .panel(Some(NativePanelFact {
                        create: create.clone(),
                        applied: true,
                        error: false,
                    }))
                    .ack(Some(NativeAckFact {
                        create,
                        success: true,
                    }))
                    .sdk(Some(NativeSdkFact {
                        runtime,
                        target,
                        bound: true,
                        diagnostic: false,
                    }))
                    .frame(Some(NativeFrameFact {
                        runtime,
                        target,
                        frames: 1,
                        paused: false,
                        presentation_paused: false,
                        windows_mode: true,
                        content_frame_seen: true,
                    }));
            }
            obs.finish().unwrap()
        };
        let mut observer = V3PresentationObserver::begin(owner.clone(), 0, vec![]).unwrap();
        observer.tick(&observe(100, Some(splash), None)).unwrap();
        assert!(
            observer
                .tick(&observe(120, Some(splash), Some(splash)))
                .unwrap()
                .main_input_eligible
        );
        observer.release_startup_main(&owner, splash, 130).unwrap();
        let gap = observer.tick(&observe(130, None, None)).unwrap();
        assert_eq!(gap.presentation.state, PresentationState::AwaitingMain);
        assert!(!gap.main_input_eligible);
        observer.release_startup_main(&owner, splash, 150).unwrap();
        let cancelled = Observation::native_builder(owner.clone(), 150)
            .lifecycle(true, Some(scene), true)
            .transport(None, None, true, false)
            .finish()
            .unwrap();
        let stopped = observer.startup().advance(&cancelled).unwrap().summary();
        assert_eq!(stopped["state"], "stopping");
        assert!(stopped["first_failure"].is_null());
        let waiting = observer.startup().clone();
        assert_eq!(
            waiting
                .advance(&observe(130 + MAIN_WAIT_MS, None, None))
                .unwrap()
                .summary()["first_failure"],
            "APP_LAUNCH_TIMEOUT"
        );
        let adopted = observer.tick(&observe(160, Some(editor), None)).unwrap();
        assert_eq!(adopted.newly_adopted_main, Some(editor));
        assert!(!adopted.main_input_eligible);
        assert_eq!(
            observer
                .startup()
                .advance(&observe(170, Some(editor), Some(splash)))
                .err()
                .unwrap()
                .0,
            "PRESENTATION_STALE"
        );
        assert!(
            observer
                .tick(&observe(170, Some(editor), Some(editor)))
                .unwrap()
                .main_input_eligible
        );
        assert!(observer.release_startup_main(&owner, editor, 180).is_err());
        assert_eq!(observer.snapshot().unwrap().first_failure, None);
    }

    #[test]
    fn uncertified_dialog_churn_fails_closed_before_b1_role_history_grows_unbounded() {
        let owner = origin();
        let scene = SceneEpoch::new(1).unwrap();
        let main_window = WindowId::new(1).unwrap();
        let main = || NativeWindowFact {
            origin: owner.clone(),
            window: main_window,
            role: Role::Main,
            life: Life::Mapped,
            app_id: Some("mousepad".into()),
            parent: Parent::None {},
        };
        let mut observer = V3PresentationObserver::begin(owner.clone(), 1, vec![]).unwrap();
        let started = Observation::native_builder(owner.clone(), 2)
            .lifecycle(true, Some(scene), true)
            .transport(Some(owner.clone()), None, false, false)
            .windows(1, vec![main()])
            .finish()
            .unwrap();
        observer.tick(&started).unwrap();
        for id in 2..=65 {
            let dialog = NativeWindowFact {
                origin: owner.clone(),
                window: WindowId::new(id).unwrap(),
                role: Role::Dialog,
                life: Life::Mapped,
                app_id: Some("mousepad".into()),
                parent: Parent::Resolved {
                    window: main_window,
                },
            };
            let observation = Observation::native_builder(owner.clone(), id as u64 + 2)
                .lifecycle(true, Some(scene), true)
                .transport(Some(owner.clone()), None, false, false)
                .windows(2, vec![main(), dialog])
                .finish()
                .unwrap();
            let result = observer.tick(&observation);
            if id <= 64 {
                result.unwrap();
            } else {
                assert_eq!(result.err().unwrap().0, "APP_WINDOW_UNSUPPORTED");
            }
        }
        assert_eq!(
            observer.snapshot().unwrap().state,
            PresentationState::Pending
        );
        assert!(
            observer.startup().role_history_len()
                <= crate::linux_desktop_v3::MAX_RETAINED_WINDOWS + 1
        );
    }

    #[test]
    fn contract_error_closes_old_owner_and_does_not_resurrect_on_next_tick() {
        let owner = origin();
        let mut observer = V3PresentationObserver::begin(owner.clone(), 1000, vec![]).unwrap();
        let scene = SceneEpoch::new(1).unwrap();
        let good = Observation::native_builder(owner.clone(), 1001)
            .lifecycle(true, Some(scene), true)
            .finish()
            .unwrap();
        observer.tick(&good).unwrap();
        let clock_reversal = Observation::native_builder(owner.clone(), 999)
            .lifecycle(true, Some(scene), true)
            .finish()
            .unwrap();
        assert_eq!(
            observer.tick(&clock_reversal).err().unwrap().0,
            "CLOCK_STALE"
        );
        assert_eq!(
            observer.snapshot().unwrap().state,
            PresentationState::Pending
        );
        assert_eq!(
            observer.snapshot().unwrap().first_failure,
            Some(eidolon_model::linux_desktop_v3::Reason::AppPresentationFailed)
        );
        observer.tick(&good).unwrap();
        assert!(matches!(
            observer.snapshot().unwrap().state,
            PresentationState::Stopping | PresentationState::Pending
        ));
        assert_eq!(
            observer.snapshot().unwrap().first_failure,
            Some(eidolon_model::linux_desktop_v3::Reason::AppPresentationFailed)
        );
    }
}
