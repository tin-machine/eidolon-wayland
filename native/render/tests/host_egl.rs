//! Real backend regressions. As integration tests these link the normal library
//! (not its cfg(test) bookkeeping backend). Missing EGL is a failure, never skip.
#![cfg(all(target_os = "linux", feature = "host-acceptance"))]

use eidolon_model::{state::Status, surface::TargetKey};
use eidolon_render::{Controller, PlatformWindow};
use std::{
    collections::BTreeMap,
    sync::Arc,
    time::{Duration, Instant},
};

fn wait(renderer: &Controller, condition: impl Fn(&eidolon_render::Snapshot) -> bool) {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let snapshot = renderer.snapshot();
        if condition(&snapshot) {
            return;
        }
        assert!(
            Instant::now() < deadline,
            "real EGL observation timed out: {snapshot:?}"
        );
        std::thread::sleep(Duration::from_millis(2));
    }
}

fn stop(renderer: &Controller) {
    renderer.request_stop();
    let deadline = Instant::now() + Duration::from_secs(10);
    while !renderer.is_finished() {
        assert!(Instant::now() < deadline, "GL owner did not stop");
        std::thread::sleep(Duration::from_millis(2));
    }
    assert!(
        renderer.owner_reclaimed(),
        "explicit EGL cleanup failed: {:?}",
        renderer.snapshot()
    );
    assert_eq!(renderer.snapshot().contexts, 0);
}

#[test]
fn two_renderers_share_only_the_display_and_retire_their_own_resources() {
    let first = Controller::start().unwrap();
    let second = Controller::start().unwrap();
    let key = TargetKey::from_raw(1, 1, 1, 1).unwrap();
    for renderer in [&first, &second] {
        assert_eq!(
            renderer.attach(key, PlatformWindow::default(), 64, 64),
            Status::Accepted
        );
        renderer.set_visible(true);
        wait(renderer, |s| {
            !s.targets.is_empty() && s.targets[0].frames >= 1
        });
        let snapshot = renderer.snapshot();
        assert!(snapshot.error.is_none(), "{snapshot:?}");
        assert_eq!(snapshot.targets[0].gpu.rgba_bits, [8; 4]);
        assert!(
            snapshot.targets[0]
                .gpu
                .renderer
                .to_ascii_lowercase()
                .contains("llvmpipe")
        );
        assert!(snapshot.targets[0].gpu.version.starts_with("OpenGL ES 3."));
    }
    assert_ne!(first.snapshot().owner_tid, second.snapshot().owner_tid);
    let retired = first.retire_target(key).unwrap();
    let deadline = Instant::now() + Duration::from_secs(10);
    while !first.retirement_complete(&retired) {
        assert!(Instant::now() < deadline, "first pbuffer was not retired");
        std::thread::sleep(Duration::from_millis(2));
    }
    assert!(!second.retirement_complete(&retired));
    assert_eq!(
        first.attach(key, PlatformWindow::default(), 64, 64),
        Status::Stale
    );
    stop(&first);

    // A Device shutdown must not terminate the display still used by another
    // owner; demand further successful presents after the first owner is gone.
    let before = second.snapshot().targets[0].frames;
    wait(&second, |s| {
        !s.targets.is_empty() && s.targets[0].frames > before + 1
    });
    assert!(second.snapshot().error.is_none());
    stop(&second);

    // The last display termination must also permit a clean new owner/context.
    let third = Controller::start().unwrap();
    assert_eq!(
        third.attach(key, PlatformWindow::default(), 64, 64),
        Status::Accepted
    );
    third.set_visible(true);
    wait(&third, |s| {
        !s.targets.is_empty() && s.targets[0].frames >= 1
    });
    stop(&third);
}

#[test]
fn backend_unavailable_fails_but_theme_without_a_target_allocates_no_egl() {
    const CHILD: &str = "EIDOLON_HOST_EGL_UNAVAILABLE_CHILD";
    if std::env::var_os(CHILD).is_none() {
        let output = std::process::Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "backend_unavailable_fails_but_theme_without_a_target_allocates_no_egl",
                "--nocapture",
            ])
            .env(CHILD, "1")
            .env("GALLIUM_DRIVER", "eidolon_invalid_driver")
            .env("LIBGL_ALWAYS_SOFTWARE", "1")
            .env("LIBGL_DRIVERS_PATH", "/nonexistent/eidolon-i6-drivers")
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "fresh-process negative failed:\n{}\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        return;
    }
    let renderer = Controller::start().unwrap();
    let pixels = renderer.pixel_budget();
    let source = eidolon_theme::raster::Bundle::new([
        ("theme.toml".into(), b"format_version=1\nid='no-target'\nname='No target'\nstyle='style.css'\n[parts.front]\nkind='meter'\nslot='titlebar-end'\nsize=[4.0,4.0]".to_vec()),
        ("style.css".into(), b"#front {-eidolon-value:1; -eidolon-tint:#00ff00;}".to_vec()),
    ]).unwrap();
    let theme = Arc::new(
        eidolon_theme::raster::compile(
            &source,
            &BTreeMap::new(),
            &pixels,
            &eidolon_theme::raster::RasterBudget::default(),
        )
        .unwrap(),
    );
    let revision = renderer.request_theme(Some(theme), false).unwrap();
    wait(&renderer, |s| s.theme_attempted_revision == revision);
    let snapshot = renderer.snapshot();
    assert_eq!(snapshot.contexts, 0);
    assert_eq!(snapshot.attaches, 0);
    assert!(snapshot.targets.is_empty());
    assert!(snapshot.error.is_none());
    assert!(snapshot.theme_error.is_none());
    assert_eq!(pixels.used_bytes(), 0);

    // The same invalid environment must really fail once a target demands EGL.
    let key = TargetKey::from_raw(1, 1, 1, 1).unwrap();
    assert_eq!(
        renderer.attach(key, PlatformWindow::default(), 64, 64),
        Status::Accepted
    );
    wait(&renderer, |s| s.error.is_some());
    let snapshot = renderer.snapshot();
    assert!(matches!(
        snapshot.error,
        Some("EGL_INIT_FAILED" | "HOST_EGL_BACKEND_MISMATCH")
    ));
    assert_eq!(snapshot.attaches, 0);
    assert_eq!(snapshot.contexts, 0);
    assert!(snapshot.targets.is_empty());
    stop(&renderer);
}

#[test]
fn real_roi_rows_content_frames_and_owner_are_bound_to_successful_presents() {
    use eidolon_model::{ids::*, window::*};
    use eidolon_render::{HostReadbackPoll, HostReadbackRequest, HostReadbackRoi};
    use std::num::NonZeroU64;
    let renderer = Controller::start().unwrap();
    let foreign = Controller::start().unwrap();
    let key = TargetKey::from_raw(1, 1, 1, 1).unwrap();
    renderer.use_windows();
    assert_eq!(
        renderer.windows(key.session, &[key.window]),
        Status::Accepted
    );
    // This test supplies pixels at the renderer API boundary. The independent
    // real wl_shm fixture covers the upstream protocol import path separately.
    let submit = |sequence: i64, blue: u8| {
        let mut pixels = renderer.pixel_budget().allocate(64, 64).unwrap();
        for y in 0..64usize {
            for x in 0..64usize {
                pixels.pixels_mut()[(y * 64 + x) * 4..(y * 64 + x + 1) * 4].copy_from_slice(&[
                    (x * 3) as u8,
                    (y * 3) as u8,
                    blue,
                    255,
                ]);
            }
        }
        assert_eq!(
            renderer.submit(
                Arc::new(WindowSnapshot {
                    session: key.session,
                    window: key.window,
                    frame_seq: FrameSeq::new(sequence).unwrap(),
                    geometry: LogicalRect::new(0, 0, 64, 64).unwrap(),
                    logical_size: LogicalSize::new(64, 64).unwrap(),
                    layers: vec![SurfaceLayer {
                        surface_id: SurfaceId::new(1).unwrap(),
                        content_seq: ContentSeq::new(sequence).unwrap(),
                        offset_logical: LogicalPoint::default(),
                        clip_logical: LogicalRect::new(0, 0, 64, 64).unwrap(),
                        transform: BufferTransform::Normal,
                        scale: 1,
                        size_px: BufferSizePx::new(64, 64).unwrap(),
                        pixels: Arc::new(pixels),
                    }],
                }),
                vec![]
            ),
            Status::Accepted
        );
    };
    submit(1, 17);
    assert_eq!(
        renderer.attach_window(key, PlatformWindow::default(), 64, 64),
        Status::Accepted
    );
    renderer.set_visible(true);
    wait(&renderer, |s| {
        s.targets
            .first()
            .is_some_and(|t| t.content_frame == Some(FrameSeq::new(1).unwrap()))
    });
    let request = |sequence, minimum| HostReadbackRequest {
        target: key,
        min_content_frame: FrameSeq::new(minimum).unwrap(),
        request_seq: NonZeroU64::new(sequence).unwrap(),
        rois: vec![
            HostReadbackRoi {
                id: 1,
                x: 3,
                y: 5,
                width: 7,
                height: 11,
            },
            HostReadbackRoi {
                id: 2,
                x: 41,
                y: 37,
                width: 13,
                height: 17,
            },
        ],
    };
    let read = |ticket: &eidolon_render::HostReadbackTicket| {
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            match renderer.poll_host_readback(ticket).unwrap() {
                HostReadbackPoll::Complete(receipt) => break receipt,
                HostReadbackPoll::Pending => {
                    assert!(
                        Instant::now() < deadline,
                        "readback timed out: {:?}",
                        renderer.snapshot()
                    );
                    std::thread::sleep(Duration::from_millis(2));
                }
            }
        }
    };
    let compare = |receipt: &eidolon_render::HostReadbackReceipt, blue: u8| {
        assert_eq!(receipt.target, key);
        assert_eq!(receipt.geometry, LogicalRect::new(0, 0, 64, 64).unwrap());
        assert!(
            receipt
                .backend
                .renderer
                .to_ascii_lowercase()
                .contains("llvmpipe")
        );
        for pixels in &receipt.rois {
            let roi = pixels.roi;
            assert_eq!(pixels.rgba.len(), (roi.width * roi.height * 4) as usize);
            for y in 0..roi.height {
                for x in 0..roi.width {
                    let at = ((y * roi.width + x) * 4) as usize;
                    assert_eq!(
                        pixels.rgba[at..at + 4],
                        [((roi.x + x) * 3) as u8, ((roi.y + y) * 3) as u8, blue, 255]
                    );
                }
            }
        }
    };
    let first_ticket = renderer.request_host_readback(request(1, 1)).unwrap();
    assert!(matches!(
        foreign.poll_host_readback(&first_ticket),
        Err(Status::Stale)
    ));
    let first = read(&first_ticket);
    compare(&first, 17);
    assert_eq!(first.content_frame, FrameSeq::new(1).unwrap());
    let idle = read(&renderer.request_host_readback(request(2, 1)).unwrap());
    compare(&idle, 17);
    assert_eq!(idle.content_frame, first.content_frame);
    assert!(idle.present_seq > first.present_seq);

    let waiting = renderer.request_host_readback(request(3, 2)).unwrap();
    for _ in 0..4 {
        assert!(matches!(
            renderer.poll_host_readback(&waiting),
            Ok(HostReadbackPoll::Pending)
        ));
        std::thread::sleep(Duration::from_millis(2));
    }
    submit(2, 203);
    let updated = read(&waiting);
    compare(&updated, 203);
    assert_eq!(updated.content_frame, FrameSeq::new(2).unwrap());
    assert_ne!(updated.snapshot_id, first.snapshot_id);
    assert_ne!(updated.pixel_sha256, first.pixel_sha256);

    let late = renderer.request_host_readback(request(4, 3)).unwrap();
    let retired = renderer.retire_target(key).unwrap();
    assert!(matches!(
        renderer.poll_host_readback(&late),
        Err(Status::Stale)
    ));
    assert!(matches!(
        renderer.request_host_readback(request(5, 1)),
        Err(Status::Stale)
    ));
    let deadline = Instant::now() + Duration::from_secs(10);
    while !renderer.retirement_complete(&retired) {
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(2));
    }
    stop(&renderer);
    assert!(matches!(
        renderer.request_host_readback(request(6, 1)),
        Err(Status::NotReady)
    ));
    stop(&foreign);
}
