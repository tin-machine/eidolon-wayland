//! 実Wayland clientとcheckpointを同期し、owned snapshot/serial/可視callbackを検証する。
use super::{
    checks::{fixture_resources, object_counts},
    protocol::ProtocolState,
};
use eidolon_model::{
    ids::{SessionId, WindowId},
    window::{BufferTransform, LogicalSize, WindowLife, WindowSnapshot},
};
use eidolon_shm_fixture::state::{self as fixture, Gate, Scenario, Stage};
use serde::Serialize;
use sha2::{Digest, Sha256};
use smithay::reexports::wayland_server::Display;
use std::{
    os::unix::net::UnixStream,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    thread,
    time::{Duration, Instant},
};

#[derive(Debug, Default, Serialize)]
pub struct Proof {
    pub client: Option<fixture::Report>,
    pub checkpoints: u32,
    pub last_stage: Option<String>,
    pub immutable_checks: u32,
    pub transforms: u32,
    pub scales: u32,
    pub mapped: bool,
    pub unmapped: bool,
    pub remapped: bool,
    pub resize_commit_only: bool,
    pub consecutive_configures: bool,
    pub recreated: bool,
    pub rejected_resize: bool,
    pub tree_flags: u32,
    pub tree_immutable_checks: u32,
    pub clip_checks: u32,
    pub content_reused: bool,
    pub withheld: u32,
    pub window_ids: Vec<WindowId>,
    pub cadence_checks: u32,
}
#[derive(Debug, Default, Serialize)]
pub struct Report {
    pub state: &'static str,
    pub fixture_revision: &'static str,
    pub callback_clock: &'static str,
    pub scenario: String,
    pub uid: u32,
    pub proof: Proof,
    pub recovery: Option<Proof>,
    pub surfaces_after: usize,
    pub clients_after: usize,
    pub objects_after: usize,
    pub windows_after: usize,
    pub pixel_bytes_after: usize,
    pub pool_bytes_after: usize,
    pub pool_count_after: usize,
    pub fixture_fds_after: usize,
    pub fixture_maps_after: usize,
    pub elapsed_ms: u64,
    pub error: Option<&'static str>,
}

fn hash(snapshot: &WindowSnapshot) -> String {
    let mut hash = Sha256::new();
    for layer in &snapshot.layers {
        hash.update(layer.pixels.pixels());
    }
    hash.finalize().iter().map(|b| format!("{b:02x}")).collect()
}
fn current(state: &ProtocolState) -> Result<Arc<WindowSnapshot>, &'static str> {
    let windows = state
        .windows()
        .into_iter()
        .filter(|w| w.life == WindowLife::Mapped)
        .collect::<Vec<_>>();
    if windows.len() != 1 {
        return Err("MAPPED_WINDOW_COUNT");
    }
    state
        .latest_snapshot(windows[0].window)
        .ok_or("MISSING_SNAPSHOT")
}
fn size_and_color(
    snapshot: &WindowSnapshot,
    width: u32,
    height: u32,
    color: [u8; 4],
) -> Result<(), &'static str> {
    if snapshot.logical_size != LogicalSize::new(width, height).unwrap()
        || snapshot.layers.len() != 1
        || !snapshot.layers[0]
            .pixels
            .pixels()
            .chunks_exact(4)
            .all(|p| p == color)
    {
        return Err("SNAPSHOT_PIXELS");
    }
    Ok(())
}

// 固定fixture用の検証器。製品rendererではなく、最大64x64/16KiBだけを一時確保する。
fn raster(snapshot: &WindowSnapshot) -> Result<Vec<u8>, &'static str> {
    let width = snapshot.logical_size.width;
    let height = snapshot.logical_size.height;
    if width > 64 || height > 64 {
        return Err("FIXTURE_RASTER_SIZE");
    }
    let mut result = vec![0u8; (width * height * 4) as usize];
    for layer in &snapshot.layers {
        for y in 0..height {
            for x in 0..width {
                let Some(uv) = layer.buffer_uv_at(f64::from(x) + 0.5, f64::from(y) + 0.5) else {
                    continue;
                };
                let bx = (uv[0] * f64::from(layer.size_px.width)).floor() as u32;
                let by = (uv[1] * f64::from(layer.size_px.height)).floor() as u32;
                let source =
                    &layer.pixels.pixels()[((by * layer.size_px.width + bx) * 4) as usize..][..4];
                let destination = &mut result[((y * width + x) * 4) as usize..][..4];
                for i in 0..4 {
                    destination[i] = (u32::from(source[i])
                        + (u32::from(destination[i]) * (255 - u32::from(source[3])) + 127) / 255)
                        .min(255) as u8;
                }
            }
        }
    }
    Ok(result)
}
fn raster_hash(snapshot: &WindowSnapshot) -> Result<String, &'static str> {
    Ok(Sha256::digest(raster(snapshot)?)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect())
}
fn fill(pixels: &mut [u8], x: usize, y: usize, width: usize, height: usize, color: [u8; 4]) {
    for row in y..y + height {
        for col in x..x + width {
            pixels[(row * 28 + col) * 4..(row * 28 + col) * 4 + 4].copy_from_slice(&color);
        }
    }
}

struct Observer {
    proof: Proof,
    saved: Option<Arc<WindowSnapshot>>,
    saved_hash: String,
    saved_tree: Option<Arc<WindowSnapshot>>,
    saved_tree_hash: String,
    first: Option<WindowId>,
    last_frame: i64,
    visible: bool,
    target: bool,
    budget: bool,
    suspended_callbacks: u64,
}
impl Default for Observer {
    fn default() -> Self {
        Self {
            proof: Proof::default(),
            saved: None,
            saved_hash: String::new(),
            saved_tree: None,
            saved_tree_hash: String::new(),
            first: None,
            last_frame: 0,
            visible: true,
            target: true,
            budget: true,
            suspended_callbacks: 0,
        }
    }
}
impl Observer {
    fn remember(&mut self, snapshot: &Arc<WindowSnapshot>) {
        if self.first.is_none() {
            self.first = Some(snapshot.window);
            self.proof.window_ids.push(snapshot.window);
        }
        if self.saved.is_none() {
            self.saved_hash = hash(snapshot);
            self.saved = Some(snapshot.clone());
        }
    }
    fn updated(&mut self, snapshot: &WindowSnapshot) -> Result<(), &'static str> {
        if snapshot.frame_seq.get() <= self.last_frame {
            return Err("FRAME_SEQUENCE");
        }
        self.last_frame = snapshot.frame_seq.get();
        if let Some(saved) = &self.saved {
            if hash(saved) != self.saved_hash {
                return Err("MUTATED_OLD_SNAPSHOT");
            }
            if snapshot.frame_seq != saved.frame_seq {
                self.proof.immutable_checks += 1;
            }
        }
        Ok(())
    }
    fn checkpoint(&mut self, state: &mut ProtocolState, stage: Stage) -> Result<(), &'static str> {
        self.proof.last_stage = Some(format!("{stage:?}"));
        if let Some(saved) = &self.saved_tree {
            if raster_hash(saved)? != self.saved_tree_hash {
                return Err("MUTATED_OLD_TREE");
            }
            self.proof.tree_immutable_checks += 1;
        }
        match stage {
            Stage::FirstMapped => {
                let snapshot = current(state)?;
                self.remember(&snapshot);
                self.updated(&snapshot)?;
                self.proof.mapped = true;
            }
            Stage::RejectResize => {
                let window = self.first.ok_or("NO_INITIAL_WINDOW")?;
                if state.configure_window(
                    SessionId::new(1).unwrap(),
                    window,
                    LogicalSize {
                        width: 2049,
                        height: 32,
                    },
                ) != Err("INVALID_ARGUMENT")
                {
                    return Err("OVERSIZED_RESIZE_ACCEPTED");
                }
                self.proof.rejected_resize = true;
            }
            Stage::TreePending => {
                let snapshot = current(state)?;
                if snapshot.layers.len() != 1 || snapshot.frame_seq.get() != self.last_frame {
                    return Err("PARTIAL_SYNC_TREE");
                }
                self.proof.tree_flags |= 1;
            }
            Stage::TreeSynced
            | Stage::TreeRestacked
            | Stage::TreeRestored
            | Stage::TreeDesynced
            | Stage::TreePopups => {
                let snapshot = current(state)?;
                self.updated(&snapshot)?;
                if snapshot.logical_size != LogicalSize::new(28, 30).unwrap()
                    || snapshot.geometry.origin.x != 2
                    || snapshot.geometry.origin.y != 1
                {
                    return Err("WINDOW_GEOMETRY");
                }
                let mut expected = vec![0u8; 28 * 30 * 4];
                fill(&mut expected, 0, 0, 28, 30, [255, 0, 0, 255]);
                if stage != Stage::TreeRestacked {
                    let color = if matches!(stage, Stage::TreeDesynced | Stage::TreePopups) {
                        [0, 255, 255, 255]
                    } else {
                        [0, 0, 255, 255]
                    };
                    fill(&mut expected, 2, 4, 12, 10, color);
                    fill(&mut expected, 5, 6, 6, 4, [0, 255, 0, 255]);
                }
                if stage == Stage::TreePopups {
                    fill(&mut expected, 10, 10, 8, 8, [255, 255, 0, 255]);
                    fill(&mut expected, 16, 16, 2, 2, [255, 0, 255, 255]);
                    fill(&mut expected, 24, 26, 4, 4, [255, 128, 0, 255]);
                    if snapshot.layers.len() != 6 {
                        return Err("POPUP_LAYER_COUNT");
                    }
                    let nested = &snapshot.layers[4];
                    let near = &snapshot.layers[5];
                    if nested.clip_logical.size != LogicalSize::new(2, 2).unwrap()
                        || near.clip_logical.size != LogicalSize::new(4, 4).unwrap()
                    {
                        return Err("POPUP_PARENT_CLIP");
                    }
                    self.proof.clip_checks = 2;
                } else if snapshot.layers.len() != 3 {
                    return Err("TREE_LAYER_COUNT");
                }
                if raster(&snapshot)? != expected {
                    return Err("TREE_PIXELS");
                }
                if stage == Stage::TreeSynced {
                    self.saved_tree_hash = raster_hash(&snapshot)?;
                    self.saved_tree = Some(snapshot.clone());
                }
                if stage == Stage::TreeRestacked {
                    let first = self.saved.as_ref().ok_or("MISSING_INITIAL_SNAPSHOT")?;
                    let root = snapshot
                        .layers
                        .iter()
                        .find(|l| l.surface_id == first.layers[0].surface_id)
                        .ok_or("MISSING_ROOT_LAYER")?;
                    if root.content_seq != first.layers[0].content_seq {
                        return Err("UNCHANGED_BUFFER_RECOPIED");
                    }
                    self.proof.content_reused = true;
                }
                self.proof.tree_flags |= match stage {
                    Stage::TreeSynced => 2,
                    Stage::TreeRestacked => 4,
                    Stage::TreeRestored => 8,
                    Stage::TreeDesynced => 16,
                    _ => 32,
                };
            }
            Stage::TreeUnmapped => {
                let window = self.first.ok_or("NO_INITIAL_WINDOW")?;
                if state.latest_snapshot(window).is_some()
                    || !state
                        .windows()
                        .iter()
                        .any(|w| w.window == window && w.life == WindowLife::Unmapped)
                {
                    return Err("TREE_UNMAP_STATE");
                }
                self.proof.tree_flags |= 64;
            }
            Stage::ConfigureResize | Stage::ConfigureTwice => {
                let window = self.first.ok_or("NO_INITIAL_WINDOW")?;
                if stage == Stage::ConfigureResize {
                    state.configure_window(
                        SessionId::new(1).unwrap(),
                        window,
                        LogicalSize::new(48, 32).unwrap(),
                    )?;
                } else {
                    state.configure_window(
                        SessionId::new(1).unwrap(),
                        window,
                        LogicalSize::new(80, 32).unwrap(),
                    )?;
                    state.configure_window(
                        SessionId::new(1).unwrap(),
                        window,
                        LogicalSize::new(96, 32).unwrap(),
                    )?;
                }
            }
            Stage::ResizeAcked | Stage::TwiceAcked => {
                let snapshot = current(state)?;
                let (width, color) = if stage == Stage::ResizeAcked {
                    (32, [128, 0, 0, 128])
                } else {
                    (64, [0, 128, 0, 128])
                };
                size_and_color(&snapshot, width, 32, color)?;
                if snapshot.frame_seq.get() != self.last_frame {
                    return Err("ACK_CHANGED_SNAPSHOT");
                }
                if stage == Stage::ResizeAcked {
                    self.proof.resize_commit_only = true;
                } else {
                    self.proof.consecutive_configures = true;
                }
            }
            Stage::Resized | Stage::Remapped | Stage::TwiceCommitted => {
                let snapshot = current(state)?;
                if Some(snapshot.window) != self.first {
                    return Err("REMAP_CHANGED_WINDOW_ID");
                }
                let (width, color) = match stage {
                    Stage::Resized => (48, [0, 0, 128, 128]),
                    Stage::Remapped => (64, [0, 128, 0, 128]),
                    _ => (96, [64, 64, 64, 128]),
                };
                size_and_color(&snapshot, width, 32, color)?;
                self.updated(&snapshot)?;
                if stage == Stage::Remapped {
                    self.proof.remapped = true;
                }
            }
            Stage::Unmapped | Stage::RemapAcked => {
                let window = self.first.ok_or("NO_INITIAL_WINDOW")?;
                let report = state
                    .windows()
                    .into_iter()
                    .find(|w| w.window == window)
                    .ok_or("WINDOW_LOST_ON_UNMAP")?;
                let expected = if stage == Stage::Unmapped {
                    WindowLife::Unmapped
                } else {
                    WindowLife::Configured
                };
                if report.life != expected || state.latest_snapshot(window).is_some() {
                    return Err("UNMAP_STATE");
                }
                if stage == Stage::Unmapped {
                    self.proof.unmapped = true;
                }
            }
            Stage::Recreated => {
                let snapshot = current(state)?;
                if snapshot.window <= self.first.ok_or("NO_INITIAL_WINDOW")? {
                    return Err("WINDOW_ID_REUSED");
                }
                size_and_color(&snapshot, 32, 32, [10, 20, 30, 255])?;
                if let Some(saved) = &self.saved
                    && (hash(saved) != self.saved_hash
                        || snapshot.layers[0].surface_id == saved.layers[0].surface_id)
                {
                    return Err("SURFACE_ID_REUSED");
                }
                self.proof.recreated = true;
                self.proof.window_ids.push(snapshot.window);
            }
            Stage::Transform { index, scale } => {
                const TABLES: [&str; 8] = [
                    "ABCDEF", "ECAFDB", "FEDCBA", "BDFACE", "BADCFE", "ACEBDF", "EFCDAB", "FDBECA",
                ];
                let wanted = [
                    BufferTransform::Normal,
                    BufferTransform::Rotate90,
                    BufferTransform::Rotate180,
                    BufferTransform::Rotate270,
                    BufferTransform::Flipped,
                    BufferTransform::Flipped90,
                    BufferTransform::Flipped180,
                    BufferTransform::Flipped270,
                ];
                let snapshot = current(state)?;
                self.remember(&snapshot);
                self.updated(&snapshot)?;
                if snapshot.layers.len() != 1 || index >= 8 || ![1, 2].contains(&scale) {
                    return Err("TRANSFORM_ARGUMENT");
                }
                let layer = &snapshot.layers[0];
                if layer.transform != wanted[index as usize] || layer.scale != scale as i32 {
                    return Err("TRANSFORM_METADATA");
                }
                let mut actual = String::new();
                for y in 0..snapshot.logical_size.height {
                    for x in 0..snapshot.logical_size.width {
                        let uv = layer
                            .buffer_uv_at(f64::from(x) + 0.5, f64::from(y) + 0.5)
                            .ok_or("TRANSFORM_CLIP")?;
                        let bx = (uv[0] * f64::from(layer.size_px.width)).floor() as u32;
                        let by = (uv[1] * f64::from(layer.size_px.height)).floor() as u32;
                        actual.push(
                            layer.pixels.pixels()[((by * layer.size_px.width + bx) * 4) as usize]
                                as char,
                        );
                    }
                }
                if actual != TABLES[index as usize] {
                    return Err("TRANSFORM_PIXELS");
                }
                self.proof.transforms |= 1 << index;
                self.proof.scales |= 1 << (scale - 1);
            }
            Stage::Suspend(gate) => {
                match gate {
                    Gate::Visible => self.visible = false,
                    Gate::Target => self.target = false,
                    Gate::Budget => self.budget = false,
                }
                self.suspended_callbacks = state.counters.callbacks;
            }
            Stage::Withheld(_) => {
                if state.counters.callbacks != self.suspended_callbacks {
                    return Err("INELIGIBLE_CALLBACK");
                }
                self.proof.withheld += 1;
            }
            Stage::Resume(gate) => match gate {
                Gate::Visible => self.visible = true,
                Gate::Target => self.target = true,
                Gate::Budget => self.budget = true,
            },
            Stage::Resumed(_) => {
                if state.counters.callbacks != self.suspended_callbacks + 1 {
                    return Err("CALLBACK_NOT_RESUMED");
                }
                let snapshot = current(state)?;
                self.updated(&snapshot)?;
            }
        }
        self.proof.checkpoints += 1;
        Ok(())
    }
}

fn drive(
    display: &mut Display<ProtocolState>,
    state: &mut ProtocolState,
    scenario: Scenario,
    stop: &AtomicBool,
    proof: &mut Proof,
) -> Result<(), &'static str> {
    let (server, peer) = UnixStream::pair().map_err(|_| "SOCKET_PAIR")?;
    let interrupt = peer.try_clone().map_err(|_| "SOCKET_CLONE")?;
    state.accept(&display.handle(), server)?;
    let (steps_tx, steps_rx) = mpsc::sync_channel(1);
    let (reply_tx, reply_rx) = mpsc::sync_channel(1);
    let (done_tx, done_rx) = mpsc::sync_channel(1);
    let cancel = Arc::new(AtomicBool::new(false));
    let child_cancel = cancel.clone();
    let worker = thread::Builder::new()
        .name("state-fixture".into())
        .spawn(move || {
            let _ = done_tx.send(fixture::run(
                peer,
                scenario,
                child_cancel,
                fixture::Control {
                    steps: steps_tx,
                    replies: reply_rx,
                },
            ));
        })
        .map_err(|_| "FIXTURE_THREAD")?;
    let mut observer = Observer::default();
    let mut tick = 0u64;
    let mut last_callback = HashMap::<WindowId, Duration>::new();
    let deadline = Instant::now() + Duration::from_secs(25);
    let outcome = 'dispatch: loop {
        if stop.load(Ordering::Acquire) {
            break Err("CANCELLED");
        }
        if Instant::now() >= deadline {
            break Err("SERVER_TIMEOUT");
        }
        if display.dispatch_clients(state).is_err() {
            break Err("SERVER_DISPATCH");
        }
        state.finish_dispatch(&display.handle());
        let now = Duration::from_nanos(tick * 16_666_667);
        tick += 1;
        for window in state.windows() {
            if state.frame_callbacks(
                window.window,
                now,
                observer.visible,
                observer.target,
                observer.budget,
            ) > 0
            {
                if last_callback
                    .insert(window.window, now)
                    .is_some_and(|previous| now - previous < Duration::from_nanos(16_666_667))
                {
                    break 'dispatch Err("CALLBACK_RATE");
                }
                observer.proof.cadence_checks += 1;
            }
        }
        if display.flush_clients().is_err() {
            break Err("SERVER_FLUSH");
        }
        if let Ok(stage) = steps_rx.try_recv() {
            if let Err(error) = observer.checkpoint(state, stage) {
                break Err(error);
            }
            if reply_tx.try_send(()).is_err() {
                break Err("CHECKPOINT_REPLY");
            }
        }
        match done_rx.try_recv() {
            Ok(result) => break result,
            Err(mpsc::TryRecvError::Disconnected) => break Err("FIXTURE_PANIC"),
            Err(mpsc::TryRecvError::Empty) => (),
        }
        thread::sleep(Duration::from_millis(1));
    };
    cancel.store(true, Ordering::Release);
    let _ = interrupt.shutdown(std::net::Shutdown::Both);
    worker.join().map_err(|_| "FIXTURE_PANIC")?;
    drop(interrupt);
    observer.saved.take();
    observer.saved_tree.take();
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        display
            .dispatch_clients(state)
            .map_err(|_| "SERVER_DISPATCH")?;
        state.finish_dispatch(&display.handle());
        let _ = display.flush_clients();
        if object_counts(display) == (0, 0) && state.pool_usage().pools == 0 {
            break;
        }
        if Instant::now() >= deadline {
            return Err("CLIENT_CLEANUP");
        }
        thread::sleep(Duration::from_millis(1));
    }
    *proof = observer.proof;
    proof.client = Some(outcome?);
    Ok(())
}

use std::collections::HashMap;
pub fn run(scenario: Scenario, stop: Arc<AtomicBool>) -> Report {
    let started = Instant::now();
    let mut report = Report {
        state: "running",
        fixture_revision: "eidolon-wayland-state-v1",
        callback_clock: "virtual",
        scenario: format!("{scenario:?}"),
        ..Default::default()
    };
    // SAFETY: getuidには引数もpointerもない。
    report.uid = unsafe { libc::getuid() };
    let outcome = (|| {
        if fixture_resources()? != (0, 0) {
            return Err("FIXTURE_ALREADY_RUNNING");
        }
        let mut display = Display::<ProtocolState>::new().map_err(|_| "DISPLAY_CREATE")?;
        let mut state = ProtocolState::new(&display.handle(), SessionId::new(1).unwrap());
        let scenario_result = (|| {
            drive(&mut display, &mut state, scenario, &stop, &mut report.proof)?;
            if !matches!(
                scenario,
                Scenario::Lifecycle | Scenario::Transforms | Scenario::FrameGates | Scenario::Tree
            ) {
                if !report
                    .proof
                    .client
                    .as_ref()
                    .is_some_and(|client| client.protocol_error)
                {
                    return Err("BAD_CLIENT_NOT_REJECTED");
                }
                let mut recovery = Proof::default();
                let outcome = drive(
                    &mut display,
                    &mut state,
                    Scenario::Transforms,
                    &stop,
                    &mut recovery,
                );
                report.recovery = Some(recovery);
                outcome?;
            }
            Ok(())
        })();
        report.surfaces_after = state.surface_count();
        report.windows_after = state.windows().len();
        (report.clients_after, report.objects_after) = object_counts(&display);
        report.pixel_bytes_after = state.pixel_bytes();
        report.pool_bytes_after = state.pool_usage().bytes;
        report.pool_count_after = state.pool_usage().pools;
        (report.fixture_fds_after, report.fixture_maps_after) = fixture_resources()?;
        scenario_result?;
        let proof = report.recovery.as_ref().unwrap_or(&report.proof);
        if proof
            .client
            .as_ref()
            .is_none_or(|client| client.protocol_error || client.checkpoints != proof.checkpoints)
        {
            return Err("CHECKPOINT_EVIDENCE");
        }
        match scenario {
            Scenario::Lifecycle
                if !(proof.mapped
                    && proof.unmapped
                    && proof.remapped
                    && proof.resize_commit_only
                    && proof.consecutive_configures
                    && proof.recreated
                    && proof.rejected_resize
                    && proof.immutable_checks >= 3) =>
            {
                return Err("LIFECYCLE_EVIDENCE");
            }
            Scenario::FrameGates
                if proof.withheld != 3
                    || proof.immutable_checks < 3
                    || proof.client.as_ref().unwrap().withheld != 3 =>
            {
                return Err("FRAME_GATE_EVIDENCE");
            }
            Scenario::Tree
                if proof.tree_flags != 127
                    || proof.tree_immutable_checks < 5
                    || proof.clip_checks != 2
                    || !proof.content_reused
                    || proof.client.as_ref().unwrap().popup_done != 3
                    || proof.client.as_ref().unwrap().popup_positions
                        != vec![(10, 10, 8, 8), (6, 6, 6, 6), (24, 26, 8, 8)] =>
            {
                return Err("TREE_EVIDENCE");
            }
            Scenario::Lifecycle | Scenario::FrameGates | Scenario::Tree => (),
            _ if proof.transforms != 255
                || proof.scales != 3
                || proof.checkpoints != 16
                || proof.immutable_checks != 15 =>
            {
                return Err("TRANSFORM_EVIDENCE");
            }
            _ => (),
        }
        if report.surfaces_after != 0
            || report.windows_after != 0
            || report.clients_after != 0
            || report.objects_after != 0
            || report.pixel_bytes_after != 0
            || report.pool_bytes_after != 0
            || report.pool_count_after != 0
            || report.fixture_fds_after != 0
            || report.fixture_maps_after != 0
        {
            return Err("RESOURCE_CLEANUP");
        }
        Ok(())
    })();
    report.error = outcome.err();
    report.state = if report.error.is_some() {
        "fail"
    } else {
        "pass"
    };
    report.elapsed_ms = started.elapsed().as_millis() as u64;
    report
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn actual_client_lifecycle_transforms_visibility_and_bad_requests() {
        let _guard = crate::compositor::FIXTURE_LOCK.lock().unwrap();
        for scenario in [
            Scenario::Lifecycle,
            Scenario::Transforms,
            Scenario::FrameGates,
            Scenario::Tree,
            Scenario::BadSerial,
            Scenario::StaleConfigure,
            Scenario::PrematureBuffer,
            Scenario::UnconfiguredRemap,
            Scenario::BadScale,
            Scenario::PositionerOverflow,
        ] {
            let report = run(scenario, Arc::new(AtomicBool::new(false)));
            assert_eq!(report.state, "pass", "{report:#?}");
        }
    }
}
