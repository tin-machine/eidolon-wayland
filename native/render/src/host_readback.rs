//! Bounded, read-only observation of a real render-thread present. Requests do
//! not supply content, owner identity, CreateSeq, or a successful GPU result.
use super::{Controller, GpuInfo, Message, QUEUE, RenderMode, Shared, State};
use eidolon_model::{
    ids::{ContentSeq, FrameSeq, SessionId, SurfaceId, WindowId},
    state::Status,
    surface::TargetKey,
    window::{
        BufferSizePx, BufferTransform, LogicalPoint, LogicalRect, LogicalSize, WindowSnapshot,
    },
};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{
    num::NonZeroU64,
    sync::{
        Arc, Weak,
        atomic::{AtomicU64, Ordering},
    },
};

pub const MAX_ROIS: usize = 8;
pub const MAX_READBACK_BYTES: usize = 1024 * 1024;
pub const MAX_REQUESTS: usize = 8;
pub const ROI_REVISION: u32 = 1;

fn hex(bytes: impl AsRef<[u8]>) -> String {
    bytes
        .as_ref()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

/// Canonical top-left-origin rectangle in target pixels, independent of GL's
/// bottom-left framebuffer coordinates. IDs are caller correlation, not proof.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub struct HostReadbackRoi {
    pub id: u32,
    pub x: u32,
    pub y: u32,
    pub width: u32,
    pub height: u32,
}

#[derive(Clone, Debug)]
pub struct HostReadbackRequest {
    pub target: TargetKey,
    pub min_content_frame: FrameSeq,
    pub request_seq: NonZeroU64,
    pub rois: Vec<HostReadbackRoi>,
}

/// Raw pixels are deliberately omitted from Debug and Serialize. A fixture may
/// compare this private transient buffer with its known input before dropping it.
#[derive(Clone, Serialize)]
pub struct HostReadbackPixels {
    pub roi: HostReadbackRoi,
    #[serde(skip)]
    pub rgba: Vec<u8>,
}
impl std::fmt::Debug for HostReadbackPixels {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("HostReadbackPixels")
            .field("roi", &self.roi)
            .field("bytes", &self.rgba.len())
            .finish()
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub struct HostReadbackRect {
    pub origin: [i32; 2],
    pub size: [i32; 2],
}

#[derive(Debug, Serialize)]
pub struct HostReadbackLayer {
    pub surface: SurfaceId,
    pub content_seq: ContentSeq,
    pub buffer_size: BufferSizePx,
    pub offset: LogicalPoint,
    pub clip: LogicalRect,
    pub scale: i32,
    pub transform: BufferTransform,
}

/// Renderer T evidence only. Runtime adds its actual handle/native CreateSeq at
/// the guarded boundary; this receipt does not imply SDK currentness or Ready.
#[derive(Debug, Serialize)]
pub struct HostReadbackReceipt {
    pub renderer_owner: u64,
    pub context_epoch: u64,
    pub request_seq: u64,
    pub target: TargetKey,
    pub min_content_frame: FrameSeq,
    pub content_frame: FrameSeq,
    pub present_seq: u64,
    pub snapshot_id: String,
    pub geometry: LogicalRect,
    pub logical_size: LogicalSize,
    pub layers: Vec<HostReadbackLayer>,
    pub content_rect: HostReadbackRect,
    pub roi_revision: u32,
    pub roi_sha256: String,
    pub pixel_sha256: Vec<String>,
    pub backend: GpuInfo,
    pub rois: Vec<HostReadbackPixels>,
}

#[derive(Debug)]
pub enum HostReadbackPoll {
    Pending,
    Complete(Box<HostReadbackReceipt>),
}

/// Non-deserializable, non-cloneable capability for one renderer. Dropping an
/// unpolled ticket cancels it and releases its reservation. Poll is consume-once.
pub struct HostReadbackTicket {
    owner: Weak<(std::sync::Mutex<Shared>, std::sync::Condvar)>,
    request_seq: u64,
}
impl std::fmt::Debug for HostReadbackTicket {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("HostReadbackTicket")
            .field("request_seq", &self.request_seq)
            .finish_non_exhaustive()
    }
}
impl Drop for HostReadbackTicket {
    fn drop(&mut self) {
        if let Some(owner) = self.owner.upgrade() {
            let mut shared = owner.0.lock().unwrap_or_else(|e| e.into_inner());
            shared.readbacks.remove(self.request_seq);
        }
    }
}

pub(crate) struct Entry {
    pub request: HostReadbackRequest,
    pub armed: bool,
    bytes: usize,
    result: Option<Result<HostReadbackReceipt, Status>>,
}
pub(crate) struct Readbacks {
    pub owner_id: u64,
    pub context_epoch: u64,
    last_request: u64,
    entries: Vec<Entry>,
    heads: Vec<TargetKey>,
}
impl Readbacks {
    pub fn new() -> std::io::Result<Self> {
        static OWNERS: AtomicU64 = AtomicU64::new(1);
        let owner_id = OWNERS
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |n| n.checked_add(1))
            .map_err(|_| std::io::Error::other("HOST_READBACK_OWNER_EXHAUSTED"))?;
        Ok(Self {
            owner_id,
            context_epoch: 1,
            last_request: 0,
            entries: vec![],
            heads: vec![],
        })
    }

    pub fn remove(&mut self, request_seq: u64) -> Option<Entry> {
        self.entries
            .iter()
            .position(|entry| entry.request.request_seq.get() == request_seq)
            .map(|index| self.entries.remove(index))
    }

    /// Called under the same short lock as FIFO admission. A request racing a
    /// queued/in-flight newer attach cannot borrow the old published snapshot.
    pub fn admit(&mut self, key: TargetKey) {
        if let Some(head) = self.heads.iter_mut().find(|head| head.same_window(key)) {
            if !key.newer_than(*head) {
                return;
            }
            *head = key;
        } else {
            self.heads.push(key);
        }
        for entry in &mut self.entries {
            if entry.request.target.same_window(key) && entry.request.target != key {
                entry.result = Some(Err(Status::Stale));
            }
        }
    }

    pub fn retain_windows(&mut self, logical: &[(SessionId, WindowId)]) {
        self.heads
            .retain(|key| logical.contains(&(key.session, key.window)));
        for entry in &mut self.entries {
            let key = entry.request.target;
            if !logical.contains(&(key.session, key.window)) {
                entry.result = Some(Err(Status::Stale));
            }
        }
    }

    pub fn invalidate_target(&mut self, key: TargetKey, status: Status) {
        for entry in &mut self.entries {
            if entry.request.target == key {
                entry.result = Some(Err(status));
            }
        }
    }

    pub fn invalidate_window(&mut self, session: SessionId, window: WindowId, status: Status) {
        for entry in &mut self.entries {
            let key = entry.request.target;
            if key.session == session && key.window == window {
                entry.result = Some(Err(status));
            }
        }
    }

    pub fn invalidate_all(&mut self, status: Status) {
        for entry in &mut self.entries {
            entry.result = Some(Err(status));
        }
    }

    pub fn context_lost(&mut self) -> bool {
        self.invalidate_all(Status::NotReady);
        let Some(next) = self.context_epoch.checked_add(1) else {
            return false;
        };
        self.context_epoch = next;
        true
    }

    pub fn arm(&mut self, request_seq: u64) {
        if let Some(entry) = self
            .entries
            .iter_mut()
            .find(|e| e.request.request_seq.get() == request_seq)
        {
            entry.armed = true;
        }
    }

    pub fn ready(&self, key: TargetKey, content: FrameSeq) -> Option<HostReadbackRequest> {
        self.entries
            .iter()
            .find(|e| {
                e.armed
                    && e.result.is_none()
                    && e.request.target == key
                    && content >= e.request.min_content_frame
            })
            .map(|e| e.request.clone())
    }

    pub fn finish(&mut self, request_seq: u64, result: Result<HostReadbackReceipt, Status>) {
        if let Some(entry) = self
            .entries
            .iter_mut()
            .find(|e| e.request.request_seq.get() == request_seq)
            && entry.result.is_none()
        {
            entry.result = Some(result);
        }
    }
}

pub(crate) fn validate_rois(rois: &[HostReadbackRoi], size: [i32; 2]) -> Result<usize, Status> {
    if rois.is_empty() || rois.len() > MAX_ROIS || size.iter().any(|n| *n <= 0) {
        return Err(Status::InvalidArgument);
    }
    let mut total = 0usize;
    for (i, roi) in rois.iter().enumerate() {
        if roi.width == 0
            || roi.height == 0
            || rois[..i].iter().any(|prior| prior.id == roi.id)
            || roi
                .x
                .checked_add(roi.width)
                .is_none_or(|n| n > size[0] as u32)
            || roi
                .y
                .checked_add(roi.height)
                .is_none_or(|n| n > size[1] as u32)
        {
            return Err(Status::InvalidArgument);
        }
        total = (roi.width as usize)
            .checked_mul(roi.height as usize)
            .and_then(|n| n.checked_mul(4))
            .and_then(|n| total.checked_add(n))
            .filter(|n| *n <= MAX_READBACK_BYTES)
            .ok_or(Status::InvalidArgument)?;
    }
    Ok(total)
}

fn current(shared: &Shared, key: TargetKey) -> Result<(), Status> {
    if shared.stop {
        return Err(Status::NotReady);
    }
    if shared.snapshot.workers == 0 || shared.snapshot.error.is_some() {
        return Err(Status::Failed);
    }
    if shared.mode != RenderMode::Windows || !shared.visible || shared.hidden.contains(&key) {
        return Err(Status::NotReady);
    }
    if !shared.logical.contains(&(key.session, key.window))
        || !shared.readbacks.heads.contains(&key)
        || shared
            .retirement_floors
            .iter()
            .any(|floor| floor.same_window(key) && !key.newer_than(*floor))
    {
        return Err(Status::Stale);
    }
    if !shared
        .snapshot
        .targets
        .iter()
        .any(|t| t.key == key && t.role == RenderMode::Windows && !t.presentation_paused)
    {
        return Err(Status::Stale);
    }
    Ok(())
}

impl Controller {
    pub fn request_host_readback(
        &self,
        request: HostReadbackRequest,
    ) -> Result<HostReadbackTicket, Status> {
        let mut shared = self.state.0.lock().unwrap_or_else(|e| e.into_inner());
        current(&shared, request.target)?;
        let target = shared
            .snapshot
            .targets
            .iter()
            .find(|t| t.key == request.target)
            .ok_or(Status::Stale)?;
        let bytes = validate_rois(&request.rois, [target.gpu.width, target.gpu.height])?;
        if request.request_seq.get() <= shared.readbacks.last_request {
            return Err(Status::Stale);
        }
        if shared.queue.len() >= QUEUE
            || shared.readbacks.entries.len() >= MAX_REQUESTS
            || shared
                .readbacks
                .entries
                .iter()
                .map(|e| e.bytes)
                .sum::<usize>()
                + bytes
                > MAX_READBACK_BYTES
        {
            return Err(Status::Busy);
        }
        let request_seq = request.request_seq.get();
        shared.readbacks.last_request = request_seq;
        shared.readbacks.entries.push(Entry {
            request,
            armed: false,
            bytes,
            result: None,
        });
        shared.queue.push_back(Message::Readback(request_seq));
        drop(shared);
        self.state.1.notify_one();
        Ok(HostReadbackTicket {
            owner: Arc::downgrade(&self.state),
            request_seq,
        })
    }

    pub fn poll_host_readback(
        &self,
        ticket: &HostReadbackTicket,
    ) -> Result<HostReadbackPoll, Status> {
        let owner = ticket.owner.upgrade().ok_or(Status::Stale)?;
        if !Arc::ptr_eq(&owner, &self.state) {
            return Err(Status::Stale);
        }
        let mut shared = self.state.0.lock().unwrap_or_else(|e| e.into_inner());
        let entry = shared
            .readbacks
            .entries
            .iter()
            .find(|e| e.request.request_seq.get() == ticket.request_seq)
            .ok_or(Status::Stale)?;
        // A failed or retired ticket cannot become successful after a context
        // rebuild, unpause, later draw, or another renderer's empty snapshot.
        if let Some(Err(status)) = entry.result.as_ref() {
            return Err(*status);
        }
        current(&shared, entry.request.target)?;
        if entry.result.is_none() {
            return Ok(HostReadbackPoll::Pending);
        }
        let entry = shared
            .readbacks
            .remove(ticket.request_seq)
            .ok_or(Status::Stale)?;
        entry
            .result
            .ok_or(Status::Failed)?
            .map(|receipt| HostReadbackPoll::Complete(Box::new(receipt)))
    }
}

pub(crate) struct RenderedFrame<'a> {
    pub owner: u64,
    pub epoch: u64,
    pub snapshot: &'a WindowSnapshot,
    pub present_seq: u64,
    pub backend: GpuInfo,
    pub decoration: Option<&'a crate::decoration::Composition<'a>>,
}

pub(crate) fn receipt(
    request: &HostReadbackRequest,
    frame: RenderedFrame<'_>,
    rois: Vec<HostReadbackPixels>,
) -> Result<HostReadbackReceipt, Status> {
    let RenderedFrame {
        owner,
        epoch,
        snapshot,
        present_seq,
        backend,
        decoration,
    } = frame;
    if snapshot.frame_seq < request.min_content_frame
        || snapshot.session != request.target.session
        || snapshot.window != request.target.window
        || !backend.readback
        || present_seq == 0
        || rois.len() != request.rois.len()
    {
        return Err(Status::Failed);
    }
    validate_rois(&request.rois, [backend.width, backend.height])?;
    let mut hash = Sha256::new();
    hash.update(ROI_REVISION.to_le_bytes());
    let mut pixel_sha256 = Vec::with_capacity(rois.len());
    for (pixels, expected) in rois.iter().zip(&request.rois) {
        if pixels.roi != *expected
            || pixels.rgba.len() != expected.width as usize * expected.height as usize * 4
        {
            return Err(Status::Failed);
        }
        for value in [
            expected.id,
            expected.x,
            expected.y,
            expected.width,
            expected.height,
        ] {
            hash.update(value.to_le_bytes());
        }
        pixel_sha256.push(hex(Sha256::digest(&pixels.rgba)));
    }
    let viewport = match decoration {
        Some(d) => d
            .viewport(snapshot.logical_size, [backend.width, backend.height])
            .map_err(|_| Status::Failed)?
            .content_viewport(),
        None => eidolon_model::layout::Viewport::new(
            snapshot.logical_size,
            backend.width,
            backend.height,
        )
        .map_err(|_| Status::Failed)?,
    };
    let rect = viewport.pixel_rect(
        LogicalRect::new(
            0,
            0,
            snapshot.logical_size.width,
            snapshot.logical_size.height,
        )
        .map_err(|_| Status::Failed)?,
    );
    Ok(HostReadbackReceipt {
        renderer_owner: owner,
        context_epoch: epoch,
        request_seq: request.request_seq.get(),
        target: request.target,
        min_content_frame: request.min_content_frame,
        content_frame: snapshot.frame_seq,
        present_seq,
        snapshot_id: format!(
            "{owner}:{}:{}:{}",
            snapshot.session.get(),
            snapshot.window.get(),
            snapshot.frame_seq.get()
        ),
        geometry: snapshot.geometry,
        logical_size: snapshot.logical_size,
        layers: snapshot
            .layers
            .iter()
            .map(|layer| HostReadbackLayer {
                surface: layer.surface_id,
                content_seq: layer.content_seq,
                buffer_size: layer.size_px,
                offset: layer.offset_logical,
                clip: layer.clip_logical,
                scale: layer.scale,
                transform: layer.transform,
            })
            .collect(),
        content_rect: HostReadbackRect {
            origin: rect.origin,
            size: rect.size,
        },
        roi_revision: ROI_REVISION,
        roi_sha256: hex(hash.finalize()),
        pixel_sha256,
        backend,
        rois,
    })
}

pub(crate) fn publish(
    state: &State,
    request: &HostReadbackRequest,
    result: Result<HostReadbackReceipt, Status>,
) {
    let mut shared = state.0.lock().unwrap_or_else(|e| e.into_inner());
    let result = current(&shared, request.target).and(result);
    shared.readbacks.finish(request.request_seq.get(), result);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{PlatformWindow, fixtures, platform};
    use std::{
        thread,
        time::{Duration, Instant},
    };

    fn wait(mut check: impl FnMut() -> bool) {
        let deadline = Instant::now() + Duration::from_secs(5);
        while !check() {
            assert!(Instant::now() < deadline, "renderer boundary timeout");
            thread::sleep(Duration::from_millis(1));
        }
    }

    fn renderer(control: Option<Arc<platform::TestControl>>) -> (Controller, TargetKey) {
        let renderer = Controller::start().unwrap();
        renderer.use_windows();
        renderer.set_visible(true);
        let key = TargetKey::from_raw(1, 1, 1, 1).unwrap();
        assert_eq!(
            renderer.windows(key.session, &[key.window]),
            Status::Accepted
        );
        let (image, probes) = fixtures::make(0, 1, key, 64, 64).unwrap();
        assert_eq!(renderer.submit(image, probes), Status::Accepted);
        assert_eq!(
            renderer.attach_window(key, PlatformWindow { control }, 1024, 1024),
            Status::Accepted
        );
        wait(|| {
            renderer
                .snapshot()
                .targets
                .first()
                .is_some_and(|t| t.content_frame.is_some())
        });
        (renderer, key)
    }

    fn request(key: TargetKey, seq: u64, min: i64) -> HostReadbackRequest {
        HostReadbackRequest {
            target: key,
            request_seq: NonZeroU64::new(seq).unwrap(),
            min_content_frame: FrameSeq::new(min).unwrap(),
            rois: vec![HostReadbackRoi {
                id: 1,
                x: 0,
                y: 0,
                width: 1,
                height: 1,
            }],
        }
    }

    fn stop(renderer: &Controller) {
        renderer.request_stop();
        wait(|| renderer.owner_reclaimed());
    }

    #[test]
    fn bounded_pending_requests_reject_bad_identity_roi_and_release_capacity_on_ticket_drop() {
        let (renderer, key) = renderer(None);
        let foreign = Controller::start().unwrap();
        let ticket = renderer.request_host_readback(request(key, 1, 99)).unwrap();
        assert!(matches!(
            renderer.poll_host_readback(&ticket),
            Ok(HostReadbackPoll::Pending)
        ));
        assert!(matches!(
            foreign.poll_host_readback(&ticket),
            Err(Status::Stale)
        ));
        assert_eq!(
            renderer
                .request_host_readback(request(key, 1, 99))
                .unwrap_err(),
            Status::Stale
        );
        for wrong in [
            TargetKey::from_raw(2, 1, 1, 1).unwrap(),
            TargetKey::from_raw(1, 2, 1, 1).unwrap(),
            TargetKey::from_raw(1, 1, 2, 1).unwrap(),
            TargetKey::from_raw(1, 1, 1, 2).unwrap(),
        ] {
            assert_eq!(
                renderer
                    .request_host_readback(request(wrong, 2, 99))
                    .unwrap_err(),
                Status::Stale
            );
        }
        for bad in [
            vec![],
            (0..9)
                .map(|id| HostReadbackRoi {
                    id,
                    x: 0,
                    y: 0,
                    width: 1,
                    height: 1,
                })
                .collect(),
            vec![HostReadbackRoi {
                id: 1,
                x: u32::MAX,
                y: 0,
                width: 1,
                height: 1,
            }],
            vec![HostReadbackRoi {
                id: 1,
                x: 0,
                y: 0,
                width: 1024,
                height: 1024,
            }],
        ] {
            let mut req = request(key, 2, 99);
            req.rois = bad;
            assert_eq!(
                renderer.request_host_readback(req).unwrap_err(),
                Status::InvalidArgument
            );
        }
        let mut held = vec![ticket];
        for seq in 2..=MAX_REQUESTS as u64 {
            held.push(
                renderer
                    .request_host_readback(request(key, seq, 99))
                    .unwrap(),
            );
        }
        assert_eq!(
            renderer
                .request_host_readback(request(key, 9, 99))
                .unwrap_err(),
            Status::Busy
        );
        held.clear();
        let mut full = request(key, 10, 99);
        full.rois[0].width = 512;
        full.rois[0].height = 512;
        let full = renderer.request_host_readback(full).unwrap();
        assert_eq!(
            renderer
                .request_host_readback(request(key, 11, 99))
                .unwrap_err(),
            Status::Busy
        );
        drop(full);
        let next = renderer
            .request_host_readback(request(key, 12, 99))
            .unwrap();
        stop(&renderer);
        assert!(matches!(
            renderer.poll_host_readback(&next),
            Err(Status::NotReady)
        ));
        assert_eq!(
            renderer
                .request_host_readback(request(key, 13, 99))
                .unwrap_err(),
            Status::NotReady
        );
        stop(&foreign);
    }

    #[test]
    fn generation_admission_and_retirement_seal_reject_old_tickets_before_gpu_fifo_completes() {
        let (renderer, key) = renderer(None);
        let ticket = renderer.request_host_readback(request(key, 1, 99)).unwrap();
        let next = TargetKey::from_raw(1, 1, 1, 2).unwrap();
        assert_eq!(
            renderer.attach_window(next, PlatformWindow::default(), 1024, 1024),
            Status::Accepted
        );
        assert!(matches!(
            renderer.poll_host_readback(&ticket),
            Err(Status::Stale)
        ));
        wait(|| renderer.snapshot().targets.iter().any(|t| t.key == next));
        let pending = renderer
            .request_host_readback(request(next, 2, 99))
            .unwrap();
        let fence = renderer.retire_target(next).unwrap();
        assert!(matches!(
            renderer.poll_host_readback(&pending),
            Err(Status::Stale)
        ));
        assert_eq!(
            renderer.attach_window(next, PlatformWindow::default(), 1024, 1024),
            Status::Stale
        );
        wait(|| renderer.retirement_complete(&fence));
        stop(&renderer);
    }

    #[test]
    fn context_loss_invalidates_pending_minimum_even_after_successful_rebuild_and_redraw() {
        let hook = Arc::new(platform::TestControl::default());
        let (renderer, key) = renderer(Some(hook.clone()));
        let ticket = renderer.request_host_readback(request(key, 1, 99)).unwrap();
        hook.fault.store(2, Ordering::Release);
        let (image, probes) = fixtures::make(1, 2, key, 64, 64).unwrap();
        assert_eq!(renderer.submit(image, probes), Status::Accepted);
        wait(|| renderer.snapshot().context_rebuilds > 0);
        wait(|| {
            renderer
                .snapshot()
                .targets
                .first()
                .is_some_and(|t| t.content_frame == FrameSeq::new(2).ok())
        });
        assert!(matches!(
            renderer.poll_host_readback(&ticket),
            Err(Status::NotReady)
        ));
        stop(&renderer);
    }

    #[test]
    fn bookkeeping_stub_can_never_issue_a_successful_readback_receipt() {
        let (renderer, key) = renderer(None);
        let ticket = renderer.request_host_readback(request(key, 1, 1)).unwrap();
        wait(|| {
            !matches!(
                renderer.poll_host_readback(&ticket),
                Ok(HostReadbackPoll::Pending)
            )
        });
        assert!(matches!(
            renderer.poll_host_readback(&ticket),
            Err(Status::Failed)
        ));
        assert_eq!(
            renderer.snapshot().error,
            Some("HOST_READBACK_REQUIRES_REAL_EGL")
        );
        stop(&renderer);
    }

    #[test]
    fn private_pixels_are_absent_from_debug_and_serialized_evidence() {
        let pixels = HostReadbackPixels {
            roi: HostReadbackRoi {
                id: 1,
                x: 0,
                y: 0,
                width: 1,
                height: 1,
            },
            rgba: vec![203, 197, 181, 255],
        };
        let encoded = serde_json::to_string(&pixels).unwrap();
        assert!(!encoded.contains("rgba") && !encoded.contains("203"));
        assert!(!format!("{pixels:?}").contains("203"));
    }
}
