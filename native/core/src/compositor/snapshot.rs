//! protocol owner内でtreeを一度に集め、Waylandを含まないowned snapshotを公開する。
use super::ProtocolState;
use eidolon_model::{
    ids::{ContentSeq, FrameSeq, SurfaceId, WindowId},
    limits,
    linux_desktop_v3::presentation::{Life as V3Life, Parent as V3Parent},
    pixels::OwnedRgba,
    window::{
        BufferSizePx, BufferTransform, FrameClock, GeometryError, LogicalPoint, LogicalRect,
        LogicalSize, SurfaceLayer, WindowLife, WindowSnapshot,
    },
};
use smithay::{
    reexports::wayland_server::{
        Resource,
        backend::ObjectId,
        protocol::{
            wl_output,
            wl_surface::{self, WlSurface},
        },
    },
    wayland::{
        compositor::{
            BufferAssignment, SubsurfaceCachedState, SurfaceAttributes, TraversalAction,
            get_parent, with_states, with_surface_tree_upward,
        },
        shell::xdg::{
            SurfaceCachedState, ToplevelState, ToplevelSurface, XdgPopupSurfaceData,
            XdgToplevelSurfaceData, XdgToplevelSurfaceRoleAttributes,
        },
    },
};
use std::{
    cell::Cell,
    collections::{HashMap, HashSet},
    sync::Arc,
    time::Duration,
};

pub(super) struct SurfaceMeta {
    pub id: SurfaceId,
    pub content: Option<ContentSeq>,
    pub scale: i32,
    pub transform: BufferTransform,
    pub window: Option<WindowId>,
}
pub(super) struct WindowRecord {
    pub surface: ToplevelSurface,
    pub id: WindowId,
    /// Immutable provenance copied from ClientState during xdg role creation.
    pub origin: Option<eidolon_model::linux_desktop_v3::presentation::NativeOrigin>,
    /// Saved before Smithay resets role parent or reparents a child on unmap.
    pub pre_unmap_parent: Option<ObjectId>,
    pub life: WindowLife,
    initial: ToplevelState,
    frame: Option<FrameSeq>,
    snapshot: Option<Arc<WindowSnapshot>>,
    clock: FrameClock,
    pub(super) resize: eidolon_model::resize::ResizeState,
    pub(super) sizing: eidolon_model::resize::Sizing,
}

#[derive(Clone, Debug, serde::Serialize)]
pub struct WindowReport {
    pub window: WindowId,
    pub app_id: Option<String>,
    /// xdg_toplevel.set_parent の同じ Display 内の実親。title / app_id から推測しない。
    pub parent: Option<WindowId>,
    pub life: WindowLife,
    pub frame: Option<FrameSeq>,
    pub logical_size: Option<LogicalSize>,
    pub layers: usize,
    pub resize: eidolon_model::resize::Report,
    pub sizing: eidolon_model::resize::Sizing,
}

struct Layer {
    object: ObjectId,
    surface: SurfaceId,
    content: ContentSeq,
    offset: LogicalPoint,
    size: LogicalSize,
    scale: i32,
    transform: BufferTransform,
    pixels: Arc<OwnedRgba>,
}

fn transform(value: wl_output::Transform) -> BufferTransform {
    match value {
        wl_output::Transform::Normal => BufferTransform::Normal,
        wl_output::Transform::_90 => BufferTransform::Rotate90,
        wl_output::Transform::_180 => BufferTransform::Rotate180,
        wl_output::Transform::_270 => BufferTransform::Rotate270,
        wl_output::Transform::Flipped => BufferTransform::Flipped,
        wl_output::Transform::Flipped90 => BufferTransform::Flipped90,
        wl_output::Transform::Flipped180 => BufferTransform::Flipped180,
        wl_output::Transform::Flipped270 => BufferTransform::Flipped270,
        _ => unreachable!("wl_surface handler validates transform"),
    }
}

fn offset(
    states: &smithay::wayland::compositor::SurfaceData,
    parent: LogicalPoint,
) -> Result<LogicalPoint, GeometryError> {
    if states.role == Some("subsurface") {
        let position = states
            .cached_state
            .get::<SubsurfaceCachedState>()
            .current()
            .location;
        parent.checked_add(LogicalPoint {
            x: position.x,
            y: position.y,
        })
    } else {
        Ok(parent)
    }
}

fn collect(
    state: &ProtocolState,
    root: &WlSurface,
    base: LogicalPoint,
) -> Result<Vec<Layer>, GeometryError> {
    let error = Cell::new(None);
    let mut layers = vec![];
    with_surface_tree_upward(
        root,
        base,
        |surface, states, parent| {
            if !state.pixels.contains_key(&surface.id()) {
                return TraversalAction::SkipChildren;
            }
            match offset(states, *parent) {
                Ok(point) => TraversalAction::DoChildren(point),
                Err(err) => {
                    error.set(Some(err));
                    TraversalAction::Break
                }
            }
        },
        |surface, states, parent| {
            let Some(pixels) = state.pixels.get(&surface.id()) else {
                return;
            };
            let Some(meta) = state.surface_meta.get(&surface.id()) else {
                return;
            };
            let result = (|| {
                let size = meta.transform.logical_size(
                    BufferSizePx::new(pixels.width_px(), pixels.height_px())?,
                    meta.scale,
                )?;
                let offset = offset(states, *parent)?;
                LogicalRect::new(offset.x, offset.y, size.width, size.height)?;
                Ok::<_, GeometryError>(Layer {
                    object: surface.id(),
                    surface: meta.id,
                    content: meta.content.expect("copied pixel sequence"),
                    offset,
                    size,
                    scale: meta.scale,
                    transform: meta.transform,
                    pixels: pixels.clone(),
                })
            })();
            match result {
                Ok(layer) => layers.push(layer),
                Err(err) => error.set(Some(err)),
            }
        },
        |_, _, _| true,
    );
    if let Some(error) = error.get() {
        Err(error)
    } else {
        Ok(layers)
    }
}

/// xdg geometryは実surface treeのboundsでclampする。加算はi64、公開rectは検査済みi32。
fn surface_geometry(surface: &WlSurface, layers: &[Layer]) -> Result<LogicalRect, GeometryError> {
    let first = layers.first().ok_or(GeometryError::InvalidSize)?;
    let mut left = i64::from(first.offset.x);
    let mut top = i64::from(first.offset.y);
    let mut right = left + i64::from(first.size.width);
    let mut bottom = top + i64::from(first.size.height);
    for layer in layers.iter().skip(1) {
        left = left.min(i64::from(layer.offset.x));
        top = top.min(i64::from(layer.offset.y));
        right = right.max(i64::from(layer.offset.x) + i64::from(layer.size.width));
        bottom = bottom.max(i64::from(layer.offset.y) + i64::from(layer.size.height));
    }
    if let Some(requested) = with_states(surface, |states| {
        states
            .cached_state
            .get::<SurfaceCachedState>()
            .current()
            .geometry
    }) {
        left = left.max(i64::from(requested.loc.x));
        top = top.max(i64::from(requested.loc.y));
        right = right.min(i64::from(requested.loc.x) + i64::from(requested.size.w));
        bottom = bottom.min(i64::from(requested.loc.y) + i64::from(requested.size.h));
    }
    LogicalRect::new(
        i32::try_from(left).map_err(|_| GeometryError::Overflow)?,
        i32::try_from(top).map_err(|_| GeometryError::Overflow)?,
        u32::try_from(right - left).map_err(|_| GeometryError::InvalidSize)?,
        u32::try_from(bottom - top).map_err(|_| GeometryError::InvalidSize)?,
    )
}

impl ProtocolState {
    pub(super) fn register_toplevel(&mut self, surface: ToplevelSurface) {
        let Some(id) = self.next_window else {
            surface
                .wl_surface()
                .post_error(wl_surface::Error::InvalidSize, "window ID exhausted");
            return;
        };
        self.next_window = id.checked_next();
        let initial = surface.current_state();
        surface.with_pending_state(|state| state.size = Some((1024, 768).into()));
        let origin = surface.wl_surface().client().and_then(|client| {
            client
                .get_data::<super::ClientState>()
                .and_then(|data| data.origin.clone())
        });
        self.toplevels.insert(
            surface.wl_surface().id(),
            WindowRecord {
                surface,
                id,
                origin,
                pre_unmap_parent: None,
                life: WindowLife::AwaitingInitialCommit,
                initial,
                frame: None,
                snapshot: None,
                clock: FrameClock::default(),
                resize: Default::default(),
                sizing: Default::default(),
            },
        );
    }

    pub(super) fn register_surface(&mut self, surface: &WlSurface) -> bool {
        let Some(id) = self.next_surface else {
            surface.post_error(wl_surface::Error::InvalidSize, "surface ID exhausted");
            return false;
        };
        self.next_surface = id.checked_next();
        self.surface_meta.insert(
            surface.id(),
            SurfaceMeta {
                id,
                content: None,
                scale: 1,
                transform: BufferTransform::Normal,
                window: None,
            },
        );
        true
    }

    pub(super) fn update_surface_metadata(
        &mut self,
        surface: &WlSurface,
        new_pixels: bool,
    ) -> bool {
        let (scale, transform) = with_states(surface, |states| {
            let mut attrs = states.cached_state.get::<SurfaceAttributes>();
            (
                attrs.current().buffer_scale,
                transform(attrs.current().buffer_transform),
            )
        });
        let window = self
            .root_for_surface(surface)
            .and_then(|root| self.toplevels.get(&root).map(|window| window.id));
        let Some(meta) = self.surface_meta.get_mut(&surface.id()) else {
            return false;
        };
        meta.scale = scale;
        meta.transform = transform;
        meta.window = window;
        if new_pixels {
            let Some(content) = self.next_content else {
                surface.post_error(wl_surface::Error::InvalidSize, "content sequence exhausted");
                return false;
            };
            self.next_content = content.checked_next();
            meta.content = Some(content);
        }
        if let Some(pixels) = self.pixels.get(&surface.id())
            && BufferSizePx::new(pixels.width_px(), pixels.height_px())
                .and_then(|size| transform.logical_size(size, scale))
                .is_err()
        {
            surface.post_error(
                wl_surface::Error::InvalidSize,
                "buffer size does not match scale",
            );
            return false;
        }
        true
    }

    /// 最初のattach前と、unmap後の再attach前に最新の有効なconfigure ACKを要求する。
    pub(super) fn prepare_role_commit(&mut self, surface: &WlSurface) -> bool {
        if self.dismissed_popups.contains(&surface.id()) {
            // popup_doneと交差したin-flight commitは再表示せず破棄する。
            return false;
        }
        let new_buffer = with_states(surface, |states| {
            matches!(
                states
                    .cached_state
                    .get::<SurfaceAttributes>()
                    .current()
                    .buffer,
                Some(BufferAssignment::NewBuffer(_))
            )
        });
        let role = smithay::wayland::compositor::get_role(surface);
        if new_buffer
            && ((role == Some("xdg_toplevel") && !self.toplevels.contains_key(&surface.id()))
                || (role == Some("xdg_popup")
                    && !self
                        .popups
                        .iter()
                        .any(|popup| popup.wl_surface() == surface)))
        {
            surface.post_error(wl_surface::Error::InvalidSize, "xdg role object required");
            return false;
        }
        if let Some(window) = self.toplevels.get(&surface.id()) {
            if new_buffer {
                return window.surface.ensure_configured();
            }
            if !window.surface.is_initial_configure_sent() {
                window.surface.send_configure();
                self.counters.configures += 1;
            }
        }
        if let Some(popup) = self
            .popups
            .iter()
            .find(|popup| popup.wl_surface() == surface)
        {
            if new_buffer {
                return popup.ensure_configured();
            }
            if !popup.is_initial_configure_sent() {
                let _ = popup.send_configure();
                self.counters.configures += 1;
            }
        }
        true
    }

    pub(super) fn root_for_surface(&self, surface: &WlSurface) -> Option<ObjectId> {
        let mut current = surface.clone();
        let mut seen = HashSet::new();
        for _ in 0..=limits::SURFACES {
            if !seen.insert(current.id()) {
                return None;
            }
            if self.toplevels.contains_key(&current.id()) {
                return Some(current.id());
            }
            if let Some(parent) = get_parent(&current) {
                current = parent;
                continue;
            }
            current = self
                .popups
                .iter()
                .find(|p| p.wl_surface() == &current)?
                .get_parent_surface()?;
        }
        None
    }

    pub(super) fn reset_unmapped_toplevel(&mut self, surface: &WlSurface) {
        let Some(window) = self.toplevels.get_mut(&surface.id()) else {
            return;
        };
        if window.life != WindowLife::Mapped {
            return;
        }
        window.life = WindowLife::Unmapped;
        window.snapshot = None;
        window.resize.unmap();
        let old_parent = window.surface.parent();
        window.pre_unmap_parent = old_parent.as_ref().map(|parent| parent.id());
        with_states(surface, |states| {
            let mut attrs = states
                .data_map
                .get::<XdgToplevelSurfaceData>()
                .unwrap()
                .lock()
                .unwrap();
            // private pending_configuresも破棄し、unmap以前のserialを再利用させない。
            *attrs = XdgToplevelSurfaceRoleAttributes::default();
            attrs.current = window.initial.clone();
            let mut cached = states.cached_state.get::<SurfaceCachedState>();
            *cached.current() = SurfaceCachedState::default();
            *cached.pending() = SurfaceCachedState::default();
        });
        window
            .surface
            .with_pending_state(|state| state.size = Some((1024, 768).into()));
        self.counters.unmaps += 1;
        for child in self.toplevels.values_mut() {
            if child
                .surface
                .parent()
                .is_some_and(|parent| parent.id() == surface.id())
            {
                child.pre_unmap_parent = Some(surface.id());
            }
            with_states(child.surface.wl_surface(), |states| {
                let mut attrs = states
                    .data_map
                    .get::<XdgToplevelSurfaceData>()
                    .unwrap()
                    .lock()
                    .unwrap();
                if attrs.parent.as_ref() == Some(surface) {
                    attrs.parent = old_parent.clone();
                }
            });
        }
        self.dismiss_popup_tree(surface);
    }

    pub(super) fn dismiss_popup_tree(&mut self, surface: &WlSurface) {
        let dismissed = self
            .popups
            .iter()
            .filter(|popup| {
                let mut parent = popup.get_parent_surface();
                for _ in 0..=limits::SURFACES {
                    let Some(current) = parent else {
                        return false;
                    };
                    if current == *surface {
                        return true;
                    }
                    parent = self
                        .popups
                        .iter()
                        .find(|popup| popup.wl_surface() == &current)
                        .and_then(|popup| popup.get_parent_surface());
                }
                false
            })
            .map(|popup| popup.wl_surface().id())
            .collect::<HashSet<_>>();
        for popup in self
            .popups
            .iter()
            .rev()
            .filter(|p| dismissed.contains(&p.wl_surface().id()))
        {
            if self.dismissed_popups.insert(popup.wl_surface().id()) {
                popup.send_popup_done();
            }
            let mut clear = vec![];
            with_surface_tree_upward(
                popup.wl_surface(),
                (),
                |_, _, _| TraversalAction::DoChildren(()),
                |surface, _, _| clear.push(surface.clone()),
                |_, _, _| true,
            );
            for surface in clear {
                self.pixels.remove(&surface.id());
                self.output.leave(&surface);
            }
        }
    }

    pub(super) fn forget_toplevel(&mut self, surface: &WlSurface) {
        self.dismiss_popup_tree(surface);
        let Some(mut window) = self.toplevels.remove(&surface.id()) else {
            return;
        };
        window.life = WindowLife::Destroyed;
        let remove = self
            .surface_meta
            .iter()
            .filter(|(_, meta)| meta.window == Some(window.id))
            .map(|(object, _)| object.clone())
            .collect::<Vec<_>>();
        for object in remove {
            self.pixels.remove(&object);
            if let Some(meta) = self.surface_meta.get_mut(&object) {
                meta.window = None;
            }
            if let Some(surface) = self.surfaces.iter().find(|surface| surface.id() == object) {
                self.output.leave(surface);
            }
        }
    }

    pub(super) fn forget_popup(&mut self, surface: &WlSurface) {
        let root = self.root_for_surface(surface);
        self.dismiss_popup_tree(surface);
        let mut clear = vec![];
        with_surface_tree_upward(
            surface,
            (),
            |_, _, _| TraversalAction::DoChildren(()),
            |surface, _, _| clear.push(surface.clone()),
            |_, _, _| true,
        );
        for surface in clear {
            self.pixels.remove(&surface.id());
            self.output.leave(&surface);
        }
        self.popups.retain(|popup| popup.wl_surface() != surface);
        self.dismissed_popups.remove(&surface.id());
        if let Some(root) = root {
            self.refresh_window(&root);
        }
    }

    pub fn configure_window(
        &mut self,
        session: eidolon_model::ids::SessionId,
        window: WindowId,
        size: LogicalSize,
    ) -> Result<u32, &'static str> {
        if session != self.session {
            return Err("STALE");
        }
        LogicalSize::new(size.width, size.height).map_err(|_| "INVALID_ARGUMENT")?;
        let record = self
            .toplevels
            .values()
            .find(|record| record.id == window)
            .ok_or("STALE")?;
        record.surface.with_pending_state(|state| {
            state.size = Some((size.width as i32, size.height as i32).into())
        });
        let serial = record.surface.send_configure();
        self.counters.configures += 1;
        Ok(serial.into())
    }

    pub fn request_resize(
        &mut self,
        window: WindowId,
        size: LogicalSize,
    ) -> Result<(), &'static str> {
        let record = self
            .toplevels
            .values_mut()
            .find(|r| r.id == window && r.life == WindowLife::Mapped)
            .ok_or("STALE")?;
        record.resize.request(size)
    }
    pub fn request_resize_owned(
        &mut self,
        target: eidolon_model::surface::TargetKey,
        request: eidolon_model::ids::CommandSeq,
        size: LogicalSize,
    ) -> Result<(), &'static str> {
        self.toplevels
            .values_mut()
            .find(|r| r.id == target.window && r.life == WindowLife::Mapped)
            .ok_or("STALE")?
            .resize
            .request_owned(request, target, size)
    }
    pub fn restore_sizes(
        &mut self,
        window: WindowId,
        physical: Option<eidolon_model::resize::PhysicalSize>,
        logical: Option<LogicalSize>,
    ) -> Result<(), &'static str> {
        let record = self
            .toplevels
            .values_mut()
            .find(|r| r.id == window && r.life == WindowLife::Mapped)
            .ok_or("STALE")?;
        record.sizing.applied_physical_size_m = None;
        if let Some(size) = physical {
            if !size.valid() {
                return Err("INVALID_ARGUMENT");
            }
            record.sizing.physical_size_m = size;
            record.sizing.size_command = None;
        }
        if let Some(size) = logical {
            record.resize.request(size)?;
        }
        Ok(())
    }
    pub fn set_physical_size(
        &mut self,
        window: WindowId,
        request: eidolon_model::ids::CommandSeq,
        size: eidolon_model::resize::PhysicalSize,
    ) -> Result<(), &'static str> {
        self.toplevels
            .values_mut()
            .find(|r| r.id == window && r.life == WindowLife::Mapped)
            .ok_or("STALE")?
            .sizing
            .request(request, size)
    }
    pub fn step_physical_size(
        &mut self,
        window: WindowId,
        request: eidolon_model::ids::CommandSeq,
        expected_command: Option<eidolon_model::ids::CommandSeq>,
        expected: eidolon_model::resize::PhysicalSize,
        step: eidolon_model::resize::PhysicalStep,
    ) -> Result<(), &'static str> {
        self.toplevels
            .values_mut()
            .find(|r| r.id == window && r.life == WindowLife::Mapped)
            .ok_or("STALE")?
            .sizing
            .step(request, expected_command, expected, step)
    }
    pub fn physical_size_applied(
        &mut self,
        window: WindowId,
        command: Option<eidolon_model::ids::CommandSeq>,
        size: eidolon_model::resize::PhysicalSize,
    ) -> Result<(), &'static str> {
        self.toplevels
            .values_mut()
            .find(|r| r.id == window && r.life == WindowLife::Mapped)
            .ok_or("STALE")?
            .sizing
            .applied(command, size)
    }
    pub fn observe_producers(&mut self, producers: &[(WindowId, BufferSizePx)]) {
        for record in self.toplevels.values_mut() {
            let size = producers
                .iter()
                .find(|(id, _)| *id == record.id)
                .map(|(_, size)| *size);
            if record.sizing.producer_size_px.is_some() && size.is_none() {
                record.sizing.applied_physical_size_m = None;
            }
            record.sizing.producer_size_px = size;
        }
    }
    /// protocol ownerのclock。command batchで最後に残った希望値だけをconfigureする。
    pub fn resize_tick(&mut self, now: Duration) {
        for record in self
            .toplevels
            .values_mut()
            .filter(|r| r.life == WindowLife::Mapped)
        {
            record.resize.tick(now);
            if let Some(size) = record.resize.next_size() {
                let content = self
                    .surface_meta
                    .get(&record.surface.wl_surface().id())
                    .and_then(|m| m.content);
                record.surface.with_pending_state(|s| {
                    s.size = Some((size.width as i32, size.height as i32).into())
                });
                let serial = record.surface.send_configure();
                if let Err(error) = record.resize.sent(serial.into(), content, now) {
                    record
                        .surface
                        .wl_surface()
                        .post_error(wl_surface::Error::InvalidSize, error);
                }
                self.counters.configures += 1;
            }
        }
    }

    pub(super) fn refresh_window(&mut self, root: &ObjectId) {
        let Some(window) = self.toplevels.get(root) else {
            return;
        };
        let surface = window.surface.wl_surface().clone();
        if !self.pixels.contains_key(root) {
            return;
        }
        let next = match window.frame {
            Some(frame) => frame.checked_next(),
            None => FrameSeq::new(1).ok(),
        };
        let Some(frame) = next else {
            surface.post_error(wl_surface::Error::InvalidSize, "frame sequence exhausted");
            return;
        };
        let id = window.id;
        match self.build_snapshot(&surface, id, frame) {
            Ok(snapshot) => {
                let window = self.toplevels.get_mut(root).unwrap();
                let serial = with_states(&surface, |states| {
                    states
                        .data_map
                        .get::<XdgToplevelSurfaceData>()
                        .unwrap()
                        .lock()
                        .unwrap()
                        .current_serial
                        .map(u32::from)
                });
                let content = self.surface_meta.get(root).and_then(|m| m.content);
                if !window.resize.commit(serial, content, snapshot.logical_size) {
                    return;
                }
                if window.life != WindowLife::Mapped {
                    self.counters.maps += 1;
                }
                window.life = WindowLife::Mapped;
                window.frame = Some(frame);
                window.snapshot = Some(Arc::new(snapshot));
            }
            Err(_) => surface.post_error(
                wl_surface::Error::InvalidSize,
                "invalid surface tree geometry",
            ),
        }
    }

    fn build_snapshot(
        &mut self,
        root: &WlSurface,
        window: WindowId,
        frame_seq: FrameSeq,
    ) -> Result<WindowSnapshot, GeometryError> {
        let base = collect(self, root, LogicalPoint::default())?;
        let geometry = surface_geometry(root, &base)?;
        let canvas = LogicalRect::new(0, 0, geometry.size.width, geometry.size.height)?;
        let mut origins = HashMap::from([(root.id(), LogicalPoint::default())]);
        let mut geometries = HashMap::from([(root.id(), geometry)]);
        let mut clips = HashMap::from([(root.id(), canvas)]);
        let mut layers = vec![];
        let mut append = |source: Vec<Layer>,
                          origin: LogicalPoint,
                          clip: LogicalRect,
                          state: &mut Self|
         -> Result<(), GeometryError> {
            for layer in source {
                if let Some(meta) = state.surface_meta.get_mut(&layer.object) {
                    meta.window = Some(window);
                }
                let offset = layer
                    .offset
                    .checked_add(origin)?
                    .checked_sub(geometry.origin)?;
                let rect =
                    LogicalRect::new(offset.x, offset.y, layer.size.width, layer.size.height)?;
                if let Some(clip) = rect.intersection(clip) {
                    layers.push(SurfaceLayer {
                        surface_id: layer.surface,
                        content_seq: layer.content,
                        offset_logical: offset,
                        clip_logical: clip,
                        transform: layer.transform,
                        scale: layer.scale,
                        size_px: BufferSizePx::new(
                            layer.pixels.width_px(),
                            layer.pixels.height_px(),
                        )?,
                        pixels: layer.pixels,
                    });
                }
            }
            Ok(())
        };
        append(base, LogicalPoint::default(), canvas, self)?;
        // popupは生成順に描く。親は必ず先に存在し、後のpopup/子が手前になる。
        for popup in self.popups.clone() {
            if self.dismissed_popups.contains(&popup.wl_surface().id())
                || !self.pixels.contains_key(&popup.wl_surface().id())
            {
                continue;
            }
            let Some(parent) = popup.get_parent_surface() else {
                continue;
            };
            let (Some(parent_origin), Some(parent_geometry), Some(parent_clip)) = (
                origins.get(&parent.id()),
                geometries.get(&parent.id()),
                clips.get(&parent.id()),
            ) else {
                continue;
            };
            let tree = collect(self, popup.wl_surface(), LogicalPoint::default())?;
            let popup_geometry = surface_geometry(popup.wl_surface(), &tree)?;
            let configured = with_states(popup.wl_surface(), |states| {
                states
                    .data_map
                    .get::<XdgPopupSurfaceData>()
                    .unwrap()
                    .lock()
                    .unwrap()
                    .current
                    .geometry
            });
            if configured.size.w != popup_geometry.size.width as i32
                || configured.size.h != popup_geometry.size.height as i32
            {
                return Err(GeometryError::InvalidSize);
            }
            let origin = parent_origin
                .checked_add(parent_geometry.origin)?
                .checked_add(LogicalPoint {
                    x: configured.loc.x,
                    y: configured.loc.y,
                })?
                .checked_sub(popup_geometry.origin)?;
            let parent_rect = LogicalRect::new(
                parent_origin.x,
                parent_origin.y,
                parent_geometry.size.width,
                parent_geometry.size.height,
            )?;
            let parent_rect = LogicalRect::new(
                parent_rect
                    .origin
                    .x
                    .checked_add(parent_geometry.origin.x)
                    .ok_or(GeometryError::Overflow)?
                    .checked_sub(geometry.origin.x)
                    .ok_or(GeometryError::Overflow)?,
                parent_rect
                    .origin
                    .y
                    .checked_add(parent_geometry.origin.y)
                    .ok_or(GeometryError::Overflow)?
                    .checked_sub(geometry.origin.y)
                    .ok_or(GeometryError::Overflow)?,
                parent_rect.size.width,
                parent_rect.size.height,
            )?;
            let Some(clip) = parent_clip.intersection(parent_rect) else {
                continue;
            };
            append(tree, origin, clip, self)?;
            origins.insert(popup.wl_surface().id(), origin);
            geometries.insert(popup.wl_surface().id(), popup_geometry);
            clips.insert(popup.wl_surface().id(), clip);
        }
        Ok(WindowSnapshot {
            session: self.session,
            window,
            frame_seq,
            geometry,
            logical_size: geometry.size,
            layers,
        })
    }

    pub fn windows(&self) -> Vec<WindowReport> {
        let mut windows =
            self.toplevels
                .values()
                .map(|window| WindowReport {
                    window: window.id,
                    parent: window.surface.parent().and_then(|parent| {
                        self.toplevels.get(&parent.id()).map(|record| record.id)
                    }),
                    app_id: with_states(window.surface.wl_surface(), |states| {
                        states
                            .data_map
                            .get::<XdgToplevelSurfaceData>()
                            .unwrap()
                            .lock()
                            .unwrap()
                            .app_id
                            .clone()
                    }),
                    life: window.life,
                    frame: window.frame,
                    logical_size: window.snapshot.as_ref().map(|s| s.logical_size),
                    layers: window.snapshot.as_ref().map_or(0, |s| s.layers.len()),
                    resize: window.resize.report(),
                    sizing: window.sizing.clone(),
                })
                .collect::<Vec<_>>();
        windows.sort_by_key(|window| window.window);
        windows
    }
    /// v3-only typed facts. Never reinterpret legacy Option<WindowId>::None
    /// as an explicit root when the xdg role had an unresolved former parent.
    pub(crate) fn v3_owned_samples(
        &self,
        origin: &eidolon_model::linux_desktop_v3::presentation::NativeOrigin,
    ) -> Result<Vec<crate::linux_desktop_v3::WindowSample>, &'static str> {
        if self.verified_origin.as_ref() != Some(origin) {
            return Err("APP_ORIGIN_UNKNOWN");
        }
        Ok(self.v3_samples())
    }

    /// Retires only this logical window's protocol-owned references. The
    /// renderer has a separate fence; shared wl_surface/SHM resources remain
    /// obligations of the origin until the Session's final local close.
    pub(crate) fn v3_window_retired(
        &self,
        origin: &eidolon_model::linux_desktop_v3::presentation::NativeOrigin,
        window: WindowId,
    ) -> bool {
        self.verified_origin.as_ref() == Some(origin)
            && !self.toplevels.values().any(|record| record.id == window)
            && !self
                .surface_meta
                .values()
                .any(|meta| meta.window == Some(window))
    }

    pub fn v3_samples(&self) -> Vec<crate::linux_desktop_v3::WindowSample> {
        self.toplevels
            .iter()
            .map(|(object, record)| {
                let actual_parent = record.surface.parent().map(|parent| parent.id());
                // Smithay can implicitly reparent a child when its parent is
                // unmapped. That is not a client-authorized set_parent event:
                // the new snapshot parent must not silently replace the
                // previously captured xdg parent in I3's identity proof.
                // Until the protocol owner can certify a new explicit parent,
                // keep the role unresolved rather than promote it to a root
                // or to a different dialog chain.
                let parent = if record.pre_unmap_parent.is_some()
                    && record.pre_unmap_parent != actual_parent
                {
                    V3Parent::Unresolved {}
                } else {
                    let raw_parent = actual_parent.or_else(|| record.pre_unmap_parent.clone());
                    match raw_parent {
                        Some(parent) if parent == *object => V3Parent::Invalid {},
                        Some(parent) => match self.toplevels.get(&parent) {
                            Some(ancestor)
                                if ancestor.origin == record.origin
                                    && ancestor.origin.is_some() =>
                            {
                                V3Parent::Resolved {
                                    window: ancestor.id,
                                }
                            }
                            Some(_) => V3Parent::Invalid {},
                            None => V3Parent::Unresolved {},
                        },
                        None => V3Parent::None {},
                    }
                };
                let app_id = with_states(record.surface.wl_surface(), |states| {
                    states
                        .data_map
                        .get::<XdgToplevelSurfaceData>()
                        .unwrap()
                        .lock()
                        .unwrap()
                        .app_id
                        .clone()
                });
                crate::linux_desktop_v3::WindowSample {
                    origin: record.origin.clone(),
                    window: record.id,
                    app_id,
                    parent,
                    life: if record.life == WindowLife::Mapped {
                        V3Life::Mapped
                    } else {
                        V3Life::Unmapped
                    },
                }
            })
            .collect()
    }

    pub fn close_window(&mut self, window: WindowId) -> Result<(), &'static str> {
        let record = self
            .toplevels
            .values()
            .find(|record| record.id == window)
            .ok_or("STALE")?;
        record.surface.send_close();
        Ok(())
    }
    pub fn latest_snapshot(&self, window: WindowId) -> Option<Arc<WindowSnapshot>> {
        self.toplevels
            .values()
            .find(|w| w.id == window)?
            .snapshot
            .clone()
    }

    pub fn frame_callbacks(
        &mut self,
        window: WindowId,
        now: Duration,
        visible: bool,
        target_ready: bool,
        budget: bool,
    ) -> u64 {
        let Some(record) = self
            .toplevels
            .values_mut()
            .find(|record| record.id == window)
        else {
            return 0;
        };
        if !record.clock.take(
            now,
            record.life == WindowLife::Mapped,
            visible,
            target_ready,
            budget,
        ) {
            return 0;
        }
        let Some(snapshot) = &record.snapshot else {
            return 0;
        };
        let visible = snapshot
            .layers
            .iter()
            .map(|l| l.surface_id)
            .collect::<HashSet<_>>();
        let before = self.counters.callbacks;
        for surface in self.surfaces.clone() {
            if self
                .surface_meta
                .get(&surface.id())
                .is_some_and(|meta| visible.contains(&meta.id))
            {
                self.complete_frame(&surface, now.as_millis() as u32);
            }
        }
        self.counters.callbacks - before
    }

    pub(super) fn role_acked(&mut self, surface: &WlSurface) {
        if let Some(window) = self.toplevels.get_mut(&surface.id())
            && window.life != WindowLife::Mapped
        {
            window.life = WindowLife::Configured;
        }
    }
}
