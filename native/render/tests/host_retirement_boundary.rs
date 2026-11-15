//! Public owner-boundary regressions. These use input snapshots, not successful
//! product-state setters; no EGL allocation is needed to expose the reference race.
#![cfg(target_os = "linux")]

use eidolon_model::{ids::*, state::Status, surface::TargetKey, window::*};
use eidolon_render::Controller;
use std::{
    sync::Arc,
    time::{Duration, Instant},
};

fn snapshot(renderer: &Controller, key: TargetKey, frame: i64) -> Arc<WindowSnapshot> {
    Arc::new(WindowSnapshot {
        session: key.session,
        window: key.window,
        frame_seq: FrameSeq::new(frame).unwrap(),
        geometry: LogicalRect::new(0, 0, 64, 64).unwrap(),
        logical_size: LogicalSize::new(64, 64).unwrap(),
        layers: vec![SurfaceLayer {
            surface_id: SurfaceId::new(1).unwrap(),
            content_seq: ContentSeq::new(frame).unwrap(),
            offset_logical: LogicalPoint::default(),
            clip_logical: LogicalRect::new(0, 0, 64, 64).unwrap(),
            transform: BufferTransform::Normal,
            scale: 1,
            size_px: BufferSizePx::new(64, 64).unwrap(),
            pixels: Arc::new(renderer.pixel_budget().allocate(64, 64).unwrap()),
        }],
    })
}

#[test]
fn late_submission_cannot_retain_pixels_behind_a_window_retirement_fence() {
    let renderer = Controller::start().unwrap();
    let key = TargetKey::from_raw(1, 1, 1, 1).unwrap();
    renderer.use_windows();
    assert_eq!(
        renderer.windows(key.session, &[key.window]),
        Status::Accepted
    );
    assert_eq!(
        renderer.submit(snapshot(&renderer, key, 1), vec![]),
        Status::Accepted
    );
    let fence = renderer.retire_window(key.session, key.window).unwrap();

    // A frame producer may already have copied a snapshot when retirement seals
    // admission. Its late delivery must release that input, not re-own the image.
    let late = snapshot(&renderer, key, 2);
    let weak = Arc::downgrade(&late);
    let submission = renderer.submit(late, vec![]);
    let deadline = Instant::now() + Duration::from_secs(5);
    while !renderer.retirement_complete(&fence) {
        assert!(
            Instant::now() < deadline,
            "window retirement did not finish"
        );
        std::thread::sleep(Duration::from_millis(2));
    }
    let final_state = renderer.snapshot();
    assert_eq!(
        (
            submission,
            final_state.latest_images,
            weak.upgrade().is_some(),
            renderer.pixel_budget().used_bytes()
        ),
        (Status::Stale, 0, false, 0),
        "a completed window fence must own no late image reference"
    );
    assert_eq!(final_state.contexts, 0);
    renderer.request_stop();
    while !renderer.is_finished() {
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(2));
    }
    assert!(renderer.owner_reclaimed());
}
