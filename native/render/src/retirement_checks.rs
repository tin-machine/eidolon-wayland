//! Failure-only tests exercise the normal owner queue and ACK publication. A
//! bookkeeping target can fail disposal; no successful GPU result is injected.
use super::*;
use std::sync::atomic::Ordering;

fn wait(renderer: &Controller, check: impl Fn(&Snapshot) -> bool) {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let observed = renderer.snapshot();
        if check(&observed) {
            return;
        }
        assert!(
            Instant::now() < deadline,
            "owner queue timeout: {observed:?}"
        );
        thread::sleep(Duration::from_millis(1));
    }
}

struct Fixture {
    renderer: Controller,
    failed: TargetKey,
    other: TargetKey,
    failure: Arc<platform::TestControl>,
    other_control: Arc<platform::TestControl>,
}
impl Fixture {
    fn new() -> Self {
        let renderer = Controller::start().unwrap();
        let failed = TargetKey::from_raw(1, 1, 1, 1).unwrap();
        let other = TargetKey::from_raw(1, 2, 1, 1).unwrap();
        let failure = Arc::new(platform::TestControl::default());
        let other_control = Arc::new(platform::TestControl::default());
        for (key, control) in [(failed, &failure), (other, &other_control)] {
            assert_eq!(
                renderer.attach(
                    key,
                    PlatformWindow {
                        control: Some(control.clone())
                    },
                    64,
                    64,
                ),
                Status::Accepted,
            );
        }
        renderer.set_visible(true);
        wait(&renderer, |s| {
            s.targets.len() == 2 && s.targets.iter().all(|t| t.frames > 0)
        });
        renderer.set_visible(false);
        Self {
            renderer,
            failed,
            other,
            failure,
            other_control,
        }
    }

    fn fail_detach(&self) {
        self.failure.retire_failure.store(true, Ordering::Release);
        assert_eq!(self.renderer.detach(self.failed), Status::Accepted);
        self.wait_failed_cleanup();
    }

    fn wait_failed_cleanup(&self) {
        wait(&self.renderer, |s| {
            s.error == Some("EGL_SHUTDOWN_FAILED")
                && !s.targets.iter().any(|t| t.key == self.failed)
        });
        assert!(self.renderer.snapshot().detached.is_empty());
        assert_eq!(self.renderer.snapshot().detaches, 0);
    }

    fn assert_no_ack(&self) {
        let snapshot = self.renderer.snapshot();
        assert!(
            snapshot.detached.is_empty(),
            "cleanup failed but ACK escaped: {snapshot:?}"
        );
        assert_eq!(snapshot.detaches, 0);
    }

    fn finish(&self) {
        self.renderer.request_stop();
        let deadline = Instant::now() + Duration::from_secs(5);
        while !self.renderer.is_finished() {
            assert!(Instant::now() < deadline);
            thread::sleep(Duration::from_millis(1));
        }
        self.assert_no_ack();
        assert!(
            !self.renderer.owner_reclaimed(),
            "failed owner cleanup was promoted by Stop"
        );
    }
}

#[test]
fn repeated_detach_cannot_ack_a_target_after_failed_retirement() {
    let fixture = Fixture::new();
    fixture
        .failure
        .retire_failure
        .store(true, Ordering::Release);
    let fence = fixture.renderer.retire_target(fixture.failed).unwrap();
    fixture.wait_failed_cleanup();
    assert!(!fixture.renderer.retirement_complete(&fence));
    for _ in 0..3 {
        let stale = fixture.renderer.snapshot().stale;
        assert_eq!(fixture.renderer.detach(fixture.failed), Status::Accepted);
        wait(&fixture.renderer, |s| s.stale > stale);
        fixture.assert_no_ack();
        assert!(!fixture.renderer.retirement_complete(&fence));
    }
    fixture.finish();
    assert!(!fixture.renderer.retirement_complete(&fence));
}

#[test]
fn rejected_attach_cannot_replace_failed_detach_evidence() {
    let fixture = Fixture::new();
    fixture.fail_detach();
    let stale = fixture.renderer.snapshot().stale;
    // There is no retire_target floor here. The real FIFO admits this callback,
    // then its generation ledger rejects the old target after failed Detach.
    assert_eq!(
        fixture
            .renderer
            .attach(fixture.failed, PlatformWindow::default(), 64, 64),
        Status::Accepted
    );
    wait(&fixture.renderer, |s| s.stale > stale);
    fixture.assert_no_ack();
    fixture.finish();
}

#[test]
fn later_target_detach_cannot_bypass_the_failed_owner_cleanup() {
    let fixture = Fixture::new();
    fixture.fail_detach();
    assert_eq!(fixture.renderer.detach(fixture.other), Status::Accepted);
    wait(&fixture.renderer, |s| s.targets.is_empty());
    fixture.assert_no_ack();
    fixture.finish();
}

#[test]
fn window_retirement_cannot_publish_ack_after_owner_cleanup_failed() {
    let fixture = Fixture::new();
    fixture.fail_detach();
    let fence = fixture
        .renderer
        .retire_window(fixture.other.session, fixture.other.window)
        .unwrap();
    wait(&fixture.renderer, |s| s.targets.is_empty());
    fixture.assert_no_ack();
    assert!(!fixture.renderer.retirement_complete(&fence));
    fixture.finish();
    assert!(!fixture.renderer.retirement_complete(&fence));
}

#[test]
fn surface_loss_cannot_publish_ack_after_an_earlier_cleanup_failure() {
    let fixture = Fixture::new();
    fixture.fail_detach();
    fixture.other_control.fault.store(1, Ordering::Release);
    fixture.renderer.set_visible(true);
    wait(&fixture.renderer, |s| s.targets.is_empty());
    assert_eq!(fixture.renderer.snapshot().error, Some("SURFACE_LOST"));
    fixture.assert_no_ack();
    fixture.finish();
}
