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
        Ok(Self { startup: Startup::begin(origin, accepted_at_ms, before)? })
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
        let newly_adopted_main =
            if previous.main.is_none() { presentation.main } else { None };
        let main_input_eligible =
            presentation.state == PresentationState::Ready
                && presentation.startup_completed
                && current.as_ref().is_some_and(|e| {
                    e.acknowledged && e.binding_present && e.renderer_frame_seen
                });
        Ok(ObservedPresentation {
            presentation, sdk_target, newly_adopted_main, main_input_eligible,
        })
    }

    pub fn snapshot(&self) -> Result<PresentationSummary> {
        PresentationSummary::from_startup(&self.startup)
    }

    pub fn startup(&self) -> &Startup {
        &self.startup
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
        )).unwrap();
        let item = fixture["records"].as_array().unwrap().iter()
            .find(|v| v["id"] == "origin").unwrap();
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
            .finish().unwrap();
        let result = observer.tick(&first).unwrap();
        assert_eq!(result.presentation.state, PresentationState::AwaitingMain);
        assert_eq!(result.newly_adopted_main, None);
        let mapped = Observation::native_builder(owner.clone(), 1600)
            .lifecycle(true, Some(scene), true)
            .transport(Some(owner.clone()), None, false, false)
            .windows(1, vec![NativeWindowFact {
                origin: owner.clone(), window: main, role: Role::Main, life: Life::Mapped,
                app_id: Some("mousepad".into()), parent: Parent::None {},
            }]).finish().unwrap();
        let result = observer.tick(&mapped).unwrap();
        assert_eq!(result.newly_adopted_main, Some(main));
        assert_eq!(result.presentation.state, PresentationState::AwaitingSdk);
        assert!(result.sdk_target.is_none(), "main adoption is not SDK proof");
        assert!(!result.main_input_eligible);
        let repeated = observer.tick(&mapped);
        assert!(repeated.is_ok(), "same tick time is monotonic");
        assert_eq!(repeated.unwrap().newly_adopted_main, None);
    }

    #[test]
    fn contract_error_closes_old_owner_and_does_not_resurrect_on_next_tick() {
        let owner = origin();
        let mut observer = V3PresentationObserver::begin(owner.clone(), 1000, vec![]).unwrap();
        let scene = SceneEpoch::new(1).unwrap();
        let good = Observation::native_builder(owner.clone(), 1001)
            .lifecycle(true, Some(scene), true).finish().unwrap();
        observer.tick(&good).unwrap();
        let clock_reversal = Observation::native_builder(owner.clone(), 999)
            .lifecycle(true, Some(scene), true).finish().unwrap();
        assert_eq!(observer.tick(&clock_reversal).err().unwrap().0, "CLOCK_STALE");
        assert_eq!(observer.snapshot().unwrap().state, PresentationState::Pending);
        assert_eq!(observer.snapshot().unwrap().first_failure,
            Some(eidolon_model::linux_desktop_v3::Reason::AppPresentationFailed));
        observer.tick(&good).unwrap();
        assert!(matches!(observer.snapshot().unwrap().state,
            PresentationState::Stopping | PresentationState::Pending));
        assert_eq!(observer.snapshot().unwrap().first_failure,
            Some(eidolon_model::linux_desktop_v3::Reason::AppPresentationFailed));
    }
}
