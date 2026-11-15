//! T evidence only: a real fixed wl_shm client enters production ProtocolState,
//! immutable snapshots enter the ordinary Controller, and Mesa renders them.
//! This is not Registry Launch, a synthetic SDK, B1 Ready, or N02 acceptance.
use eidolon_core::compositor::protocol::ProtocolState;
use eidolon_model::{
    ids::{FrameSeq, SessionId},
    pixels::PixelBudget,
    state::Status,
    surface::TargetKey,
    window::{WindowLife, WindowSnapshot},
};
use eidolon_render::{
    Controller, HostReadbackPoll, HostReadbackReceipt, HostReadbackRequest, HostReadbackRoi,
    HostReadbackTicket, PlatformWindow, theme_state::SemanticState,
};
use eidolon_theme::raster::{Bundle, RasterBudget, compile};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use smithay::reexports::wayland_server::Display;
use std::{
    collections::BTreeMap,
    num::NonZeroU64,
    os::unix::net::UnixStream,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

type Result<T> = std::result::Result<T, &'static str>;
const SIDE: i32 = 1280;
const ORIGIN: u32 = 624;
const LIMIT: Duration = Duration::from_secs(18);
const THEME_MANIFEST: &[u8] = b"format_version=2\nid='i6-fixed'\nname='I6 fixed'\nstyle='style.css'\n[parts.backdrop]\nkind='svg-mask'\nslot='window-backdrop'\nsrc='white.svg'\nsize=[1280.0,1280.0]\n";
const THEME_STYLE: &[u8] =
    b"#backdrop {-eidolon-tint:#102040;opacity:1;}\n#backdrop.focused {-eidolon-tint:#506070;}\n";
const THEME_SVG: &[u8] = br##"<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="#ffffff"/></svg>"##;

fn require(value: bool, reason: &'static str) -> Result<()> {
    if value { Ok(()) } else { Err(reason) }
}
fn accepted(value: Status) -> Result<()> {
    require(value == Status::Accepted, "FIXTURE_COMMAND_REJECTED")
}
fn digest(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}
fn rois() -> Vec<HostReadbackRoi> {
    vec![
        HostReadbackRoi {
            id: 1,
            x: ORIGIN,
            y: ORIGIN,
            width: 32,
            height: 32,
        },
        HostReadbackRoi {
            id: 2,
            x: 8,
            y: 8,
            width: 2,
            height: 2,
        },
    ]
}
fn request(key: TargetKey, frame: FrameSeq, sequence: u64) -> HostReadbackRequest {
    HostReadbackRequest {
        target: key,
        min_content_frame: frame,
        request_seq: NonZeroU64::new(sequence).unwrap(),
        rois: rois(),
    }
}
/// Deliberately independent of compositor/reference_pixel and fixture::pattern.
/// wl_shm ARGB8888 is little-endian BGRA. The premultiplied app is painted
/// over the product's opaque black content rectangle; decoration is outside it.
fn expected(frame: u32, roi: u32, focused: bool) -> Vec<u8> {
    if roi == 2 {
        return (0..4)
            .flat_map(|_| {
                if focused {
                    [80, 96, 112, 255]
                } else {
                    [16, 32, 64, 255]
                }
            })
            .collect();
    }
    let mut output = Vec::with_capacity(4096);
    for y in 0..32u32 {
        for x in 0..32u32 {
            output.extend_from_slice(&[
                (frame % 128) as u8,
                ((y + frame) % 128) as u8,
                ((x + frame) % 128) as u8,
                255,
            ]);
        }
    }
    output
}
fn observed(receipt: HostReadbackReceipt, label: &str, frame: u32, focused: bool) -> Result<Value> {
    require(
        receipt
            .backend
            .renderer
            .to_ascii_lowercase()
            .contains("llvmpipe"),
        "FIXTURE_BACKEND_NOT_LLVMPIPE",
    )?;
    require(
        receipt.backend.rgba_bits == [8, 8, 8, 8]
            && receipt.backend.width == SIDE
            && receipt.backend.height == SIDE
            && receipt.backend.version.contains("OpenGL ES 3"),
        "FIXTURE_BACKEND_FORMAT",
    )?;
    require(receipt.rois.len() == 2, "FIXTURE_ROI_COUNT")?;
    let mut comparisons = Vec::new();
    for pixels in &receipt.rois {
        let wanted = expected(frame, pixels.roi.id, focused);
        require(pixels.rgba == wanted, "FIXTURE_PIXEL_MISMATCH")?;
        comparisons.push(json!({"roi_id":pixels.roi.id,"bytes":pixels.rgba.len(),
            "actual_sha256":digest(&pixels.rgba),"expected_sha256":digest(&wanted)}));
    }
    Ok(
        json!({"label":label,"fixture_frame":frame,"focused":focused,
        "receipt":receipt,"comparisons":comparisons}),
    )
}

struct Fixture {
    renderer: Controller,
    display: Display<ProtocolState>,
    protocol: ProtocolState,
    stop: Arc<AtomicBool>,
    client: Option<JoinHandle<Result<eidolon_shm_fixture::Report>>>,
    started: Instant,
}
impl Fixture {
    fn dispatch(&mut self) -> Result<()> {
        require(self.started.elapsed() < LIMIT, "FIXTURE_TIMEOUT")?;
        self.display
            .dispatch_clients(&mut self.protocol)
            .map_err(|_| "FIXTURE_DISPATCH")?;
        self.protocol.finish_dispatch(&self.display.handle());
        self.display.flush_clients().map_err(|_| "FIXTURE_FLUSH")?;
        if let Some(error) = self.renderer.snapshot().error {
            return Err(error);
        }
        Ok(())
    }
    fn until(&mut self, mut ready: impl FnMut(&mut Self) -> Result<bool>) -> Result<()> {
        loop {
            self.dispatch()?;
            if ready(self)? {
                return Ok(());
            }
            thread::sleep(Duration::from_millis(2));
        }
    }
    fn image(&mut self, previous: Option<FrameSeq>) -> Result<Arc<WindowSnapshot>> {
        let mut output = None;
        self.until(|this| {
            output = this
                .protocol
                .windows()
                .iter()
                .filter(|w| w.life == WindowLife::Mapped)
                .find_map(|w| this.protocol.latest_snapshot(w.window))
                .filter(|image| previous.is_none_or(|p| image.frame_seq > p));
            Ok(output.is_some())
        })?;
        output.ok_or("FIXTURE_SNAPSHOT_MISSING")
    }
    fn read(&mut self, ticket: &HostReadbackTicket) -> Result<HostReadbackReceipt> {
        let mut result = None;
        self.until(|this| match this.renderer.poll_host_readback(ticket) {
            Ok(HostReadbackPoll::Pending) => Ok(false),
            Ok(HostReadbackPoll::Complete(receipt)) => {
                result = Some(*receipt);
                Ok(true)
            }
            Err(_) => Err("FIXTURE_READBACK_REJECTED"),
        })?;
        result.ok_or("FIXTURE_READBACK_MISSING")
    }
    fn sample(
        &mut self,
        key: TargetKey,
        frame: FrameSeq,
        sequence: u64,
    ) -> Result<HostReadbackReceipt> {
        let ticket = self
            .renderer
            .request_host_readback(request(key, frame, sequence))
            .map_err(|_| "FIXTURE_READBACK_REQUEST")?;
        self.read(&ticket)
    }
    fn callback(&mut self, window: eidolon_model::ids::WindowId) -> Result<()> {
        let mut sent = false;
        self.until(|this| {
            sent |= this
                .protocol
                .frame_callbacks(window, this.started.elapsed(), true, true, true)
                > 0;
            this.display.flush_clients().map_err(|_| "FIXTURE_FLUSH")?;
            Ok(sent)
        })
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        self.renderer.request_stop();
        if let Some(client) = self.client.take() {
            let _ = client.join();
        }
    }
}

fn run() -> Result<Value> {
    let pixels = PixelBudget::default();
    let renderer =
        Controller::start_with_budget(pixels.clone()).map_err(|_| "FIXTURE_RENDER_THREAD")?;
    let theme = compile(
        &Bundle::new([
            ("theme.toml".into(), THEME_MANIFEST.to_vec()),
            ("style.css".into(), THEME_STYLE.to_vec()),
            ("white.svg".into(), THEME_SVG.to_vec()),
        ])
        .map_err(|_| "FIXTURE_THEME")?,
        &BTreeMap::new(),
        &pixels,
        &RasterBudget::default(),
    )
    .map_err(|_| "FIXTURE_THEME")?;
    accepted(renderer.set_theme(Some(Arc::new(theme)), true))?;
    // Give the normal theme staging path work without ever creating a target.
    thread::sleep(Duration::from_millis(60));
    let no_target = renderer.snapshot();
    require(
        no_target.contexts == 0
            && no_target.targets.is_empty()
            && no_target.attaches == 0
            && no_target.latest_images == 0
            && no_target.error.is_none(),
        "FIXTURE_EAGER_EGL_ALLOCATION",
    )?;
    renderer.use_windows();
    let display = Display::<ProtocolState>::new().map_err(|_| "FIXTURE_DISPLAY")?;
    let session = SessionId::new(1).unwrap();
    let mut protocol = ProtocolState::new_with_budget(&display.handle(), session, pixels.clone());
    let (server, client) = UnixStream::pair().map_err(|_| "FIXTURE_SOCKETPAIR")?;
    protocol.accept(&display.handle(), server)?;
    let stop = Arc::new(AtomicBool::new(false));
    let child_stop = stop.clone();
    let worker = thread::spawn(move || {
        eidolon_shm_fixture::run(client, eidolon_shm_fixture::Mode::Pattern, child_stop)
    });
    let mut fixture = Fixture {
        renderer,
        display,
        protocol,
        stop,
        client: Some(worker),
        started: Instant::now(),
    };
    let first = fixture.image(None)?;
    require(
        first.logical_size.width == 32
            && first.logical_size.height == 32
            && first.layers.len() == 1,
        "FIXTURE_SHM_GEOMETRY",
    )?;
    let first_frame = first.frame_seq;
    let window = first.window;
    // Scene/generation are explicit renderer T inputs. Window and FrameSeq are
    // allocated by ProtocolState; there is no Runtime/CreateSeq success setter.
    let key = TargetKey::from_raw(first.session.get(), first.window.get(), 1, 1).unwrap();
    accepted(fixture.renderer.windows(session, &[window]))?;
    accepted(fixture.renderer.submit(first.clone(), vec![]))?;
    accepted(
        fixture
            .renderer
            .attach_window(key, PlatformWindow::default(), SIDE, SIDE),
    )?;
    fixture.renderer.set_visible(true);
    fixture.until(|this| {
        Ok(this
            .renderer
            .snapshot()
            .targets
            .iter()
            .any(|target| target.key == key && target.content_frame == Some(first_frame)))
    })?;
    let mut readbacks = vec![observed(
        fixture.sample(key, first_frame, 1)?,
        "initial",
        0,
        false,
    )?];
    let mut negatives = Vec::new();

    let foreign = Controller::start().map_err(|_| "FIXTURE_RENDER_THREAD")?;
    let ticket = fixture
        .renderer
        .request_host_readback(request(key, first_frame, 2))
        .map_err(|_| "FIXTURE_READBACK_REQUEST")?;
    require(
        matches!(foreign.poll_host_readback(&ticket), Err(Status::Stale)),
        "FIXTURE_FOREIGN_TICKET_ACCEPTED",
    )?;
    negatives.push(json!({"case":"foreign_owner","status":"Stale"}));
    readbacks.push(observed(fixture.read(&ticket)?, "idle", 0, false)?);
    require(
        matches!(
            fixture.renderer.poll_host_readback(&ticket),
            Err(Status::Stale)
        ),
        "FIXTURE_TICKET_REPLAY_ACCEPTED",
    )?;
    negatives.push(json!({"case":"ticket_replay","status":"Stale"}));
    for (label, target) in [
        (
            "wrong_session",
            TargetKey::from_raw(2, window.get(), 1, 1).unwrap(),
        ),
        (
            "wrong_window",
            TargetKey::from_raw(session.get(), window.get() + 1, 1, 1).unwrap(),
        ),
        (
            "uncreated_generation",
            TargetKey::from_raw(session.get(), window.get(), 1, 2).unwrap(),
        ),
    ] {
        let rejected = fixture
            .renderer
            .request_host_readback(request(target, first_frame, 3));
        require(
            matches!(rejected, Err(Status::Stale | Status::NotReady)),
            "FIXTURE_WRONG_TARGET_ACCEPTED",
        )?;
        negatives.push(json!({"case":label,"status":"StaleOrNotReady"}));
    }
    for (label, invalid_rois) in [
        (
            "roi_count",
            (0..9)
                .map(|id| HostReadbackRoi {
                    id,
                    x: 0,
                    y: 0,
                    width: 1,
                    height: 1,
                })
                .collect(),
        ),
        (
            "roi_bytes",
            vec![HostReadbackRoi {
                id: 1,
                x: 0,
                y: 0,
                width: 1024,
                height: 1024,
            }],
        ),
        (
            "roi_bounds",
            vec![HostReadbackRoi {
                id: 1,
                x: 1279,
                y: 0,
                width: 2,
                height: 1,
            }],
        ),
    ] {
        let mut invalid = request(key, first_frame, 3);
        invalid.rois = invalid_rois;
        require(
            matches!(
                fixture.renderer.request_host_readback(invalid),
                Err(Status::InvalidArgument)
            ),
            "FIXTURE_ROI_LIMIT_ACCEPTED",
        )?;
        negatives.push(json!({"case":label,"status":"InvalidArgument"}));
    }
    accepted(fixture.renderer.set_semantic_state(
        session,
        window,
        SemanticState {
            focused: true,
            ..Default::default()
        },
    ))?;
    readbacks.push(observed(
        fixture.sample(key, first_frame, 4)?,
        "decoration",
        0,
        true,
    )?);
    let next_frame = first_frame
        .checked_next()
        .ok_or("FIXTURE_FRAME_EXHAUSTED")?;
    let future = fixture
        .renderer
        .request_host_readback(request(key, next_frame, 5))
        .map_err(|_| "FIXTURE_READBACK_REQUEST")?;
    for _ in 0..8 {
        fixture.dispatch()?;
        require(
            matches!(
                fixture.renderer.poll_host_readback(&future),
                Ok(HostReadbackPoll::Pending)
            ),
            "FIXTURE_MIN_FRAME_FALSE_SUCCESS",
        )?;
        thread::sleep(Duration::from_millis(3));
    }
    negatives.push(json!({"case":"min_content_frame","status":"Pending"}));
    fixture.callback(window)?;
    let updated = fixture.image(Some(first_frame))?;
    let second_frame = updated.frame_seq;
    accepted(fixture.renderer.submit(updated.clone(), vec![]))?;
    readbacks.push(observed(fixture.read(&future)?, "content_update", 1, true)?);
    require(
        second_frame > first_frame
            && first.layers[0].pixels.pixels()[0] == 0
            && updated.layers[0].pixels.pixels()[0] == 1,
        "FIXTURE_IMMUTABLE_SNAPSHOT",
    )?;
    drop(first);

    // No callback produces frame 2 yet, so all requests must remain bounded and
    // Pending. The controller must refuse capacity rather than silently evict.
    let mut pending = Vec::new();
    for sequence in 10..75 {
        match fixture.renderer.request_host_readback(request(
            key,
            second_frame.checked_next().unwrap(),
            sequence,
        )) {
            Ok(ticket) => pending.push(ticket),
            Err(Status::Busy) => break,
            Err(_) => return Err("FIXTURE_CAPACITY_WRONG_STATUS"),
        }
    }
    require(
        !pending.is_empty() && pending.len() < 65,
        "FIXTURE_UNBOUNDED_READBACK",
    )?;
    negatives.push(json!({"case":"queue_capacity","status":"Busy","accepted":pending.len()}));
    for ticket in &pending {
        require(
            matches!(
                fixture.renderer.poll_host_readback(ticket),
                Ok(HostReadbackPoll::Pending)
            ),
            "FIXTURE_MIN_FRAME_FALSE_SUCCESS",
        )?;
    }
    fixture.callback(window)?;
    let third = fixture.image(Some(second_frame))?;
    let third_frame = third.frame_seq;
    accepted(fixture.renderer.submit(third, vec![]))?;
    for ticket in &pending {
        let _ = observed(fixture.read(ticket)?, "capacity_drain", 2, true)?;
    }

    let old_fence = fixture
        .renderer
        .retire_target(key)
        .map_err(|_| "FIXTURE_RETIRE")?;
    require(
        !foreign.retirement_complete(&old_fence),
        "FIXTURE_FOREIGN_FENCE_ACCEPTED",
    )?;
    require(
        fixture
            .renderer
            .attach_window(key, PlatformWindow::default(), SIDE, SIDE)
            == Status::Stale,
        "FIXTURE_LATE_ATTACH_ACCEPTED",
    )?;
    negatives.push(json!({"case":"foreign_fence","status":"Rejected"}));
    negatives.push(json!({"case":"late_attach","status":"Stale"}));
    fixture.until(|this| Ok(this.renderer.retirement_complete(&old_fence)))?;
    require(
        !fixture
            .renderer
            .snapshot()
            .targets
            .iter()
            .any(|t| t.key == key),
        "FIXTURE_EARLY_TARGET_RETIREMENT",
    )?;
    require(
        matches!(
            fixture
                .renderer
                .request_host_readback(request(key, third_frame, 90)),
            Err(Status::Stale | Status::NotReady)
        ),
        "FIXTURE_RETIRED_READBACK_ACCEPTED",
    )?;
    negatives.push(json!({"case":"retired_generation","status":"StaleOrNotReady"}));
    let newer = TargetKey::from_raw(session.get(), window.get(), 1, 2).unwrap();
    accepted(
        fixture
            .renderer
            .attach_window(newer, PlatformWindow::default(), SIDE, SIDE),
    )?;
    fixture.until(|this| {
        Ok(this
            .renderer
            .snapshot()
            .targets
            .iter()
            .any(|t| t.key == newer && t.content_frame == Some(third_frame)))
    })?;
    let late_fence = fixture
        .renderer
        .retire_target(key)
        .map_err(|_| "FIXTURE_RETIRE")?;
    fixture.until(|this| Ok(this.renderer.retirement_complete(&late_fence)))?;
    require(
        fixture
            .renderer
            .snapshot()
            .targets
            .iter()
            .any(|t| t.key == newer),
        "FIXTURE_OLD_FENCE_RETIRED_NEW",
    )?;
    readbacks.push(observed(
        fixture.sample(newer, third_frame, 91)?,
        "new_generation",
        2,
        true,
    )?);
    negatives.push(json!({"case":"old_target_new_generation","status":"Preserved"}));

    // Let the actual client finish all remaining fixed SHM frames and destroy
    // its Wayland objects. This does not mutate product success/Ready state.
    fixture.until(|this| {
        this.protocol
            .frame_callbacks(window, this.started.elapsed(), true, true, true);
        this.display.flush_clients().map_err(|_| "FIXTURE_FLUSH")?;
        Ok(this
            .client
            .as_ref()
            .is_some_and(|client| client.is_finished()))
    })?;
    let client = fixture
        .client
        .take()
        .ok_or("FIXTURE_CLIENT_MISSING")?
        .join()
        .map_err(|_| "FIXTURE_CLIENT_PANIC")??;
    require(
        client.frames == eidolon_shm_fixture::FRAMES && !client.protocol_error,
        "FIXTURE_CLIENT_INCOMPLETE",
    )?;
    fixture.until(|this| {
        Ok(this.protocol.windows().is_empty()
            && this.protocol.surface_count() == 0
            && this.protocol.pool_usage().bytes == 0)
    })?;
    drop(updated);
    let window_fence = fixture
        .renderer
        .retire_window(session, window)
        .map_err(|_| "FIXTURE_RETIRE")?;
    fixture.until(|this| Ok(this.renderer.retirement_complete(&window_fence)))?;
    let retired = fixture.renderer.snapshot();
    require(
        retired.targets.is_empty()
            && retired.latest_images == 0
            && retired.composition.textures == 0
            && retired.composition.texture_bytes == 0,
        "FIXTURE_EARLY_WINDOW_RETIREMENT",
    )?;
    require(
        fixture
            .renderer
            .attach_window(newer, PlatformWindow::default(), SIDE, SIDE)
            == Status::Stale,
        "FIXTURE_WINDOW_REATTACH_ACCEPTED",
    )?;
    negatives.push(json!({"case":"retired_window_attach","status":"Stale"}));
    fixture.renderer.request_stop();
    require(
        matches!(
            fixture
                .renderer
                .request_host_readback(request(newer, third_frame, 92)),
            Err(Status::NotReady | Status::Stale)
        ),
        "FIXTURE_STOPPED_READBACK_ACCEPTED",
    )?;
    negatives.push(json!({"case":"after_stop","status":"NotReadyOrStale"}));
    let stopped = Instant::now();
    while !fixture.renderer.owner_reclaimed() {
        require(
            stopped.elapsed() < Duration::from_secs(3),
            "FIXTURE_RENDERER_NOT_RECLAIMED",
        )?;
        thread::sleep(Duration::from_millis(2));
    }
    let final_render = fixture.renderer.snapshot();
    require(
        final_render.workers == 0
            && final_render.contexts == 0
            && final_render.targets.is_empty()
            && final_render.latest_images == 0,
        "FIXTURE_FINAL_CENSUS",
    )?;
    foreign.request_stop();
    Ok(
        json!({"schema":1,"evidence_class":"T","revision":"eidolon-i6-renderer-v1",
        "status":"pass","identity":serde_json::from_str::<Value>(env!("EIDOLON_I6_RENDERER_BUILD_IDENTITY")).map_err(|_| "FIXTURE_BUILD_IDENTITY")?,
        "fixture":{"shm":"eidolon-shm-fixture-pattern-v1","theme_manifest_sha256":digest(THEME_MANIFEST),
            "theme_style_sha256":digest(THEME_STYLE),"theme_svg_sha256":digest(THEME_SVG)},
        "before_target":no_target,"readbacks":readbacks,"negative_cases":negatives,
        "client":client,"retired":retired,"final_renderer":final_render,
        "owner_reclaimed":fixture.renderer.owner_reclaimed(),
        "coverage":{"normal_launch":"not_run","sdk":"not_run","b1_ready":"not_run",
            "current_input_gate":"not_run","n02_gtk3_desktop":"not_run","n02_mousepad_editor":"not_run",
            "android_storage":"not_run","quest":"not_run"},
        "host_v3_acceptance_passed":false}),
    )
}

fn main() {
    let (result, status) = match run() {
        Ok(result) => (result, 0),
        Err(reason) => (
            json!({"schema":1,"revision":"eidolon-i6-renderer-v1","evidence_class":"T",
            "status":"fail","reason":reason,"host_v3_acceptance_passed":false}),
            1,
        ),
    };
    println!("{}", result);
    std::process::exit(status);
}
