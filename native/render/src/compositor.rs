//! GL owner専用の画像合成。入力はimmutableなowned pixelと検査済みgeometryだけ。
use crate::decoration::{Composition, Decorations, Draw, PreparedDecorations};
use eidolon_model::{
    ids::{ContentSeq, SessionId, SurfaceId, WindowId},
    limits,
    window::{LogicalRect, WindowSnapshot},
};
use serde::Serialize;
use smithay::{
    backend::{
        allocator::Fourcc,
        egl::{EGLContext, EGLSurface},
        renderer::{
            Bind, Frame, ImportMem, Renderer, TextureFilter,
            gles::{GlesRenderer, GlesTexture},
        },
    },
    utils::{Physical, Rectangle, Transform},
};
use std::collections::HashMap;
use std::time::Instant;

type Error = &'static str;
#[cfg(feature = "host-acceptance")]
type PaintResult = Vec<crate::host_readback::HostReadbackPixels>;
#[cfg(not(feature = "host-acceptance"))]
type PaintResult = ();

/// GPU-side renderer.wait is insufficient for a CPU retirement receipt. The
/// acceptance backend waits for an actual GLES3 fence, with a finite timeout.
#[cfg(feature = "host-acceptance")]
fn bounded_gpu_finish(gl: &smithay::backend::renderer::gles::ffi::Gles2) -> Result<(), Error> {
    use smithay::backend::renderer::gles::ffi;
    if !gl.FenceSync.is_loaded() || !gl.ClientWaitSync.is_loaded() || !gl.DeleteSync.is_loaded() {
        return Err("HOST_EGL_FENCE_UNAVAILABLE");
    }
    // SAFETY: Called only while this renderer's GLES3 context is current on its
    // owner thread. The sync is deleted exactly once on every non-null path.
    unsafe {
        super::check_gl_error(gl.GetError())?;
        let sync = gl.FenceSync(ffi::SYNC_GPU_COMMANDS_COMPLETE, 0);
        if sync.is_null() {
            super::check_gl_error(gl.GetError())?;
            return Err("GPU_FENCE_FAILED");
        }
        let result = gl.ClientWaitSync(sync, ffi::SYNC_FLUSH_COMMANDS_BIT, 100_000_000);
        let error = gl.GetError();
        gl.DeleteSync(sync);
        super::check_gl_error(error)?;
        super::check_gl_error(gl.GetError())?;
        match result {
            ffi::ALREADY_SIGNALED | ffi::CONDITION_SATISFIED => Ok(()),
            ffi::TIMEOUT_EXPIRED => Err("GPU_FENCE_TIMEOUT"),
            _ => Err("GPU_FENCE_FAILED"),
        }
    }
}
fn gpu_error<E: Into<smithay::backend::SwapBuffersError>>(error: E) -> Error {
    match error.into() {
        smithay::backend::SwapBuffersError::ContextLost(_) => "EGL_CONTEXT_LOST",
        _ => "SURFACE_LOST",
    }
}
fn record_gpu<E: std::fmt::Debug + Into<smithay::backend::SwapBuffersError>>(
    report: &mut Report,
    error: E,
) -> Error {
    report.error = Some(format!("{error:?}").chars().take(256).collect());
    gpu_error(error)
}
type ImageKey = (SessionId, SurfaceId, ContentSeq);
type WindowKey = (SessionId, WindowId);

pub use eidolon_model::layout::Viewport;

fn viewport_rect(viewport: &Viewport, rect: LogicalRect) -> Rectangle<i32, Physical> {
    let rect = viewport.pixel_rect(rect);
    Rectangle::new(
        (rect.origin[0], rect.origin[1]).into(),
        (rect.size[0], rect.size[1]).into(),
    )
}

/// readback用fixtureのCPU基準。terminal本文を診断へ渡すAPIではない。
pub fn reference_pixel(snapshot: &WindowSnapshot, viewport: &Viewport, x: f64, y: f64) -> [u8; 4] {
    let mut color = [0, 0, 0, 255];
    let Some([x, y]) = viewport.logical_at(x, y) else {
        return color;
    };
    for layer in &snapshot.layers {
        let Some([u, v]) = layer.buffer_uv_at(x, y) else {
            continue;
        };
        let px = ((u * f64::from(layer.size_px.width)).floor() as u32).min(layer.size_px.width - 1);
        let py =
            ((v * f64::from(layer.size_px.height)).floor() as u32).min(layer.size_px.height - 1);
        let index = ((py * layer.size_px.width + px) * 4) as usize;
        let source = &layer.pixels.pixels()[index..index + 4];
        for channel in 0..3 {
            color[channel] = (u32::from(source[channel])
                + (u32::from(color[channel]) * (255 - u32::from(source[3])) + 127) / 255)
                .min(255) as u8;
        }
    }
    color
}

#[derive(Clone, Debug)]
pub struct Probe {
    pub x: i32,
    pub y: i32,
    pub rgba: [u8; 4],
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct Report {
    pub uploads: u64,
    pub updates: u64,
    pub decoration_uploads: u64,
    pub decoration_texture_bytes: usize,
    pub decoration_presents: u64,
    pub textures: usize,
    pub texture_bytes: usize,
    pub readback_samples: u64,
    pub pattern_readback_samples: u64,
    pub max_swap_us: u64,
    pub stage: &'static str,
    pub error: Option<String>,
    pub gl_error: u32,
}
struct Cached {
    texture: GlesTexture,
    size: (u32, u32),
    bytes: usize,
    window: WindowKey,
}
pub struct Compositor {
    // texture Dropの通知をrendererより先に送る。
    cache: HashMap<ImageKey, Cached>,
    staged_theme: Option<Option<PreparedDecorations>>,
    decorations: Option<Decorations>,
    theme_active: bool,
    renderer: GlesRenderer,
    report: Report,
}
impl Compositor {
    /// # Safety
    /// contextはこのGL owner以外でcurrentにせず、外部handleの寿命は本体より長く保つ。
    pub unsafe fn new(context: EGLContext) -> Result<Self, Error> {
        let mut renderer = unsafe { GlesRenderer::new(context) }.map_err(|_| "EGL_INIT_FAILED")?;
        if !renderer.mem_formats().any(|f| f == Fourcc::Abgr8888) {
            return Err("RGBA_UNSUPPORTED");
        }
        renderer
            .upscale_filter(TextureFilter::Nearest)
            .map_err(|_| "EGL_INIT_FAILED")?;
        renderer
            .downscale_filter(TextureFilter::Nearest)
            .map_err(|_| "EGL_INIT_FAILED")?;
        Ok(Self {
            cache: HashMap::new(),
            staged_theme: None,
            decorations: None,
            theme_active: false,
            renderer,
            report: Report::default(),
        })
    }
    pub fn context(&self) -> &EGLContext {
        self.renderer.egl_context()
    }
    pub fn report(&self) -> Report {
        self.report.clone()
    }
    #[cfg(feature = "host-acceptance")]
    pub fn finish_gpu_work(&mut self) -> Result<(), Error> {
        // Dropped GlesTexture references enqueue their GL deletions. Drain them
        // before inserting the fence, including work left by a prior clear.
        self.renderer.cleanup_texture_cache().map_err(gpu_error)?;
        // with_context makes the context current without a surface. Besides
        // waiting for deletion/draw commands, this releases any current pbuffer
        // so a following eglDestroySurface is not merely deferred.
        self.renderer
            .with_context(bounded_gpu_finish)
            .map_err(gpu_error)?
    }
    pub fn forget(&mut self, session: SessionId, window: WindowId) -> Result<(), Error> {
        self.cache
            .retain(|_, image| image.window != (session, window));
        self.refresh();
        self.renderer
            .cleanup_texture_cache()
            .map_err(|error| record_gpu(&mut self.report, error))?;
        Ok(())
    }
    pub fn clear(&mut self) -> Result<(), Error> {
        self.cache.clear();
        self.staged_theme = None;
        self.decorations = None;
        self.theme_active = false;
        self.report.decoration_texture_bytes = 0;
        self.refresh();
        self.renderer
            .cleanup_texture_cache()
            .map_err(|error| record_gpu(&mut self.report, error))
    }
    fn refresh(&mut self) {
        self.report.textures = self.cache.len();
        self.report.texture_bytes = self.cache.values().map(|v| v.bytes).sum();
    }
    pub fn prepare_theme(
        &mut self,
        theme: Option<&eidolon_theme::raster::CompiledTheme>,
    ) -> Result<(), Error> {
        self.stage_theme(theme)?;
        self.finish_theme(true)
    }
    pub fn stage_theme(
        &mut self,
        theme: Option<&eidolon_theme::raster::CompiledTheme>,
    ) -> Result<(), Error> {
        self.finish_theme(false)?;
        if let Some(theme) = theme {
            if self.decorations.is_none() {
                self.decorations = Some(Decorations::new(&mut self.renderer).map_err(gpu_error)?);
                self.report.decoration_uploads += 1;
            }
            let decorations = self.decorations.as_mut().unwrap();
            let before = decorations.uploads();
            let result = decorations.stage(
                &mut self.renderer,
                theme,
                limits::PIXEL_BYTES.saturating_sub(self.report.texture_bytes),
            );
            self.report.decoration_uploads += decorations.uploads() - before;
            match result {
                Ok(prepared) => {
                    self.report.decoration_texture_bytes = decorations.staged_bytes(&prepared);
                    self.staged_theme = Some(Some(prepared));
                    Ok(())
                }
                Err(error) => {
                    let cleanup = self.finish_theme(false);
                    if cleanup == Err("EGL_CONTEXT_LOST") {
                        cleanup
                    } else {
                        Err(error)
                    }
                }
            }
        } else {
            self.staged_theme = Some(None);
            Ok(())
        }
    }
    pub fn finish_theme(&mut self, adopt: bool) -> Result<(), Error> {
        if let Some(prepared) = self.staged_theme.take()
            && adopt
        {
            if let Some(prepared) = prepared {
                self.decorations
                    .as_mut()
                    .ok_or("THEME_TEXTURE_MISSING")?
                    .adopt(prepared);
                self.theme_active = true;
            } else {
                self.theme_active = false;
            }
        }
        if !self.theme_active {
            self.decorations = None;
        }
        self.report.decoration_texture_bytes = self
            .decorations
            .as_ref()
            .map_or(0, Decorations::texture_bytes);
        self.renderer.cleanup_texture_cache().map_err(gpu_error)
    }
    fn decoration_pass(
        &mut self,
        surface: &mut EGLSurface,
        draws: &[Draw],
        clear: bool,
        probes: &[Probe],
    ) -> Result<(), Error> {
        self.renderer
            .upscale_filter(TextureFilter::Linear)
            .map_err(gpu_error)?;
        self.renderer
            .downscale_filter(TextureFilter::Linear)
            .map_err(gpu_error)?;
        let result = (|| {
            let size = surface.get_size().ok_or("SURFACE_LOST")?;
            let mut target = self.renderer.bind(surface).map_err(gpu_error)?;
            let mut frame = self
                .renderer
                .render(&mut target, size, Transform::Flipped180)
                .map_err(gpu_error)?;
            if clear {
                frame
                    .clear([0., 0., 0., 0.].into(), &[Rectangle::from_size(size)])
                    .map_err(gpu_error)?;
            }
            self.decorations
                .as_ref()
                .ok_or("THEME_TEXTURE_MISSING")?
                .draw(&mut frame, draws)?;
            let (matched, error) = frame
                .with_context(|gl| {
                    let matched = probes.iter().all(|probe| {
                        if probe.x < 0 || probe.y < 0 || probe.x >= size.w || probe.y >= size.h {
                            return false;
                        }
                        let mut actual = [0u8; 4];
                        unsafe {
                            gl.ReadPixels(
                                probe.x,
                                size.h - 1 - probe.y,
                                1,
                                1,
                                0x1908,
                                0x1401,
                                actual.as_mut_ptr().cast(),
                            );
                        }
                        actual
                            .iter()
                            .zip(probe.rgba)
                            .all(|(a, b)| a.abs_diff(b) <= 1)
                    });
                    (matched, unsafe { gl.GetError() })
                })
                .map_err(gpu_error)?;
            let sync = frame.finish().map_err(gpu_error)?;
            drop(target);
            self.renderer.wait(&sync).map_err(gpu_error)?;
            super::check_gl_error(error)?;
            if !matched {
                return Err("COMPOSITE_MISMATCH");
            }
            self.report.readback_samples += probes.len() as u64;
            Ok(())
        })();
        // エラー経路でも次のapp passにLinearを漏らさない。
        let up = self
            .renderer
            .upscale_filter(TextureFilter::Nearest)
            .map_err(gpu_error);
        let down = self
            .renderer
            .downscale_filter(TextureFilter::Nearest)
            .map_err(gpu_error);
        if up == Err("EGL_CONTEXT_LOST") || down == Err("EGL_CONTEXT_LOST") {
            return Err("EGL_CONTEXT_LOST");
        }
        result.and(up).and(down)
    }
    pub fn paint(
        &mut self,
        surface: &mut EGLSurface,
        snapshot: &WindowSnapshot,
        probes: &[Probe],
    ) -> Result<(), Error> {
        self.paint_decorated(surface, snapshot, probes, None)
    }

    pub fn paint_decorated(
        &mut self,
        surface: &mut EGLSurface,
        snapshot: &WindowSnapshot,
        probes: &[Probe],
        decoration: Option<&Composition<'_>>,
    ) -> Result<(), Error> {
        self.paint_decorated_inner(
            surface,
            snapshot,
            probes,
            decoration,
            #[cfg(feature = "host-acceptance")]
            &[],
        )
        .map(|_| ())
    }

    #[cfg(all(feature = "host-acceptance", not(test)))]
    pub(crate) fn paint_decorated_readback(
        &mut self,
        surface: &mut EGLSurface,
        snapshot: &WindowSnapshot,
        probes: &[Probe],
        decoration: Option<&Composition<'_>>,
        rois: &[crate::host_readback::HostReadbackRoi],
    ) -> Result<PaintResult, Error> {
        if rois.is_empty() {
            return Err("INVALID_ARGUMENT");
        }
        self.paint_decorated_inner(surface, snapshot, probes, decoration, rois)
    }

    fn paint_decorated_inner(
        &mut self,
        surface: &mut EGLSurface,
        snapshot: &WindowSnapshot,
        probes: &[Probe],
        decoration: Option<&Composition<'_>>,
        #[cfg(feature = "host-acceptance")] rois: &[crate::host_readback::HostReadbackRoi],
    ) -> Result<PaintResult, Error> {
        let size = surface.get_size().ok_or("SURFACE_LOST")?;
        let layout = decoration
            .map(|d| d.viewport(snapshot.logical_size, [size.w, size.h]))
            .transpose()?;
        let viewport = match layout {
            Some(layout) => layout.content_viewport(),
            None => Viewport::new(snapshot.logical_size, size.w, size.h)
                .map_err(|_| "INVALID_ARGUMENT")?,
        };
        let (back, front) = if let Some(decoration) = decoration {
            self.prepare_theme(Some(decoration.theme))?;
            (
                decoration.draws(layout.unwrap(), true)?,
                decoration.draws(layout.unwrap(), false)?,
            )
        } else {
            self.prepare_theme(None)?;
            (vec![], vec![])
        };
        if snapshot.layers.len() > limits::SURFACES {
            return Err("INVALID_ARGUMENT");
        }
        let window = (snapshot.session, snapshot.window);
        self.cache.retain(|key, image| {
            image.window != window
                || snapshot
                    .layers
                    .iter()
                    .any(|layer| key.0 == snapshot.session && key.1 == layer.surface_id)
        });
        let mut images = vec![];
        for layer in &snapshot.layers {
            if (layer.size_px.width, layer.size_px.height)
                != (layer.pixels.width_px(), layer.pixels.height_px())
            {
                return Err("INVALID_ARGUMENT");
            }
            let dst = viewport_rect(&viewport, layer.rect().map_err(|_| "INVALID_ARGUMENT")?);
            let Some(clip) = viewport_rect(&viewport, layer.clip_logical).intersection(dst) else {
                continue;
            };
            if dst.size.is_empty() || clip.size.is_empty() {
                continue;
            }
            let key = (snapshot.session, layer.surface_id, layer.content_seq);
            if !self.cache.contains_key(&key) {
                let prior = self
                    .cache
                    .keys()
                    .copied()
                    .find(|old| old.0 == key.0 && old.1 == key.1)
                    .and_then(|old| self.cache.remove(&old));
                if let Some(mut prior) =
                    prior.filter(|old| old.size == (layer.size_px.width, layer.size_px.height))
                {
                    self.renderer
                        .update_memory(
                            &prior.texture,
                            layer.pixels.pixels(),
                            Rectangle::from_size(
                                (layer.size_px.width as i32, layer.size_px.height as i32).into(),
                            ),
                        )
                        .map_err(|_| "RGBA_IMPORT_FAILED")?;
                    prior.window = window;
                    self.cache.insert(key, prior);
                    self.report.updates += 1;
                } else {
                    self.renderer
                        .cleanup_texture_cache()
                        .map_err(|error| record_gpu(&mut self.report, error))?;
                    self.refresh();
                    if self
                        .report
                        .texture_bytes
                        .checked_add(layer.pixels.pixels().len())
                        .and_then(|n| n.checked_add(self.report.decoration_texture_bytes))
                        .is_none_or(|n| n > limits::PIXEL_BYTES)
                    {
                        return Err("RENDER_BUDGET");
                    }
                    let texture = self
                        .renderer
                        .import_memory(
                            layer.pixels.pixels(),
                            Fourcc::Abgr8888,
                            (layer.size_px.width as i32, layer.size_px.height as i32).into(),
                            false,
                        )
                        .map_err(|_| "RGBA_IMPORT_FAILED")?;
                    self.cache.insert(
                        key,
                        Cached {
                            texture,
                            bytes: layer.pixels.pixels().len(),
                            size: (layer.size_px.width, layer.size_px.height),
                            window,
                        },
                    );
                    self.report.uploads += 1;
                }
            }
            let cached = &self.cache[&key];
            if cached.size != (layer.size_px.width, layer.size_px.height) {
                return Err("INVALID_ARGUMENT");
            }
            images.push((
                cached.texture.clone(),
                dst,
                Rectangle::new(clip.loc - dst.loc, clip.size),
                (layer.size_px.width as i32, layer.size_px.height as i32),
                layer.transform,
            ));
        }
        self.refresh();
        if decoration.is_some() {
            self.decoration_pass(surface, &back, true, &[])?;
        }
        self.renderer
            .upscale_filter(TextureFilter::Nearest)
            .map_err(gpu_error)?;
        self.renderer
            .downscale_filter(TextureFilter::Nearest)
            .map_err(gpu_error)?;
        self.report.stage = "bind";
        let mut target = self
            .renderer
            .bind(surface)
            .map_err(|error| record_gpu(&mut self.report, error))?;
        // Smithay examples/minimal.rsと同じwindow framebufferの出力変換。
        // canonical左上原点をEGLの下原点へ合わせる。wl_buffer transformとは別。
        self.report.stage = "frame_begin";
        let mut frame = self
            .renderer
            .render(&mut target, size, Transform::Flipped180)
            .map_err(|error| record_gpu(&mut self.report, error))?;
        self.report.stage = "clear";
        if decoration.is_none() {
            frame
                .clear([0., 0., 0., 1.].into(), &[Rectangle::from_size(size)])
                .map_err(|error| record_gpu(&mut self.report, error))?;
        } else {
            // 透明なapp pixelの下にも既存の黒背景を維持し、外周だけを透過する。
            let rect = viewport_rect(
                &viewport,
                LogicalRect::new(
                    0,
                    0,
                    snapshot.logical_size.width,
                    snapshot.logical_size.height,
                )
                .map_err(|_| "INVALID_ARGUMENT")?,
            );
            frame
                .draw_solid(
                    rect,
                    &[Rectangle::from_size(rect.size)],
                    [0., 0., 0., 1.].into(),
                )
                .map_err(|error| record_gpu(&mut self.report, error))?;
        }
        self.report.stage = "draw";
        for (texture, dst, damage, size, transform) in images {
            use eidolon_model::window::BufferTransform as B;
            let transform = match transform {
                B::Normal => Transform::Normal,
                B::Rotate90 => Transform::_90,
                B::Rotate180 => Transform::_180,
                B::Rotate270 => Transform::_270,
                B::Flipped => Transform::Flipped,
                B::Flipped90 => Transform::Flipped90,
                B::Flipped180 => Transform::Flipped180,
                B::Flipped270 => Transform::Flipped270,
            };
            Frame::render_texture_from_to(
                &mut frame,
                &texture,
                Rectangle::from_size(size.into()).to_f64(),
                dst,
                &[damage],
                &[],
                transform,
                1.,
            )
            .map_err(|error| record_gpu(&mut self.report, error))?;
        }
        let final_probes = probes;
        let probes = if decoration.is_some() {
            &[][..]
        } else {
            probes
        };
        // 読取りだけをGL frame内で行い、Smithayのstateを変更しない。
        self.report.stage = "readback";
        let (matched, error) = frame
            .with_context(|gl| {
                let matched = probes.iter().all(|probe| {
                    if probe.x < 0 || probe.y < 0 || probe.x >= size.w || probe.y >= size.h {
                        return false;
                    }
                    let mut actual = [0u8; 4];
                    unsafe {
                        gl.ReadPixels(
                            probe.x,
                            size.h - 1 - probe.y,
                            1,
                            1,
                            0x1908,
                            0x1401,
                            actual.as_mut_ptr().cast(),
                        );
                    }
                    actual
                        .iter()
                        .zip(probe.rgba)
                        .all(|(a, b)| a.abs_diff(b) <= 1)
                });
                (matched, unsafe { gl.GetError() })
            })
            .map_err(|error| record_gpu(&mut self.report, error))?;
        self.report.gl_error = error;
        self.report.stage = "finish";
        let sync = frame
            .finish()
            .map_err(|error| record_gpu(&mut self.report, error))?;
        drop(target);
        self.report.stage = "fence";
        self.renderer
            .wait(&sync)
            .map_err(|error| record_gpu(&mut self.report, error))?;
        if decoration.is_some() {
            self.decoration_pass(surface, &front, false, final_probes)?;
            self.report.decoration_presents += 1;
        }
        // Capture after BOTH passes. No byte escapes until the real fence and
        // swap below succeed. The snapshot used here is the one actually drawn.
        #[cfg(feature = "host-acceptance")]
        let readback = if rois.is_empty() {
            Vec::new()
        } else {
            self.readback_frame(surface, rois)?
        };
        self.report.stage = "bind_for_swap";
        // SAFETY: surface/contextとも同じGL ownerで保持し、別threadではcurrentにしない。
        unsafe {
            self.renderer
                .egl_context()
                .make_current_with_surface(surface)
        }
        .map_err(|error| record_gpu(&mut self.report, error))?;
        self.report.stage = "check_gl_error";
        super::check_gl_error(error)?;
        if !matched {
            return Err("COMPOSITE_MISMATCH");
        }
        self.report.readback_samples += probes.len() as u64;
        self.report.stage = "swap";
        let started = Instant::now();
        surface
            .swap_buffers(None)
            .map_err(|error| record_gpu(&mut self.report, error))?;
        self.report.max_swap_us = self
            .report
            .max_swap_us
            .max(started.elapsed().as_micros().min(u64::MAX as u128) as u64);
        self.report.stage = "presented";
        #[cfg(feature = "host-acceptance")]
        {
            if !rois.is_empty() {
                let error = self
                    .renderer
                    .with_context(|gl| unsafe { gl.GetError() })
                    .map_err(gpu_error)?;
                super::check_gl_error(error)?;
            }
            Ok(readback)
        }
        #[cfg(not(feature = "host-acceptance"))]
        Ok(())
    }

    #[cfg(feature = "host-acceptance")]
    fn readback_frame(
        &mut self,
        surface: &mut EGLSurface,
        rois: &[crate::host_readback::HostReadbackRoi],
    ) -> Result<Vec<crate::host_readback::HostReadbackPixels>, Error> {
        use crate::host_readback::HostReadbackPixels;
        use smithay::backend::renderer::gles::ffi;
        let size = surface.get_size().ok_or("SURFACE_LOST")?;
        if rois.is_empty() || rois.len() > 8 {
            return Err("INVALID_ARGUMENT");
        }
        let mut total = 0usize;
        for (index, roi) in rois.iter().enumerate() {
            if roi.width == 0
                || roi.height == 0
                || roi
                    .x
                    .checked_add(roi.width)
                    .is_none_or(|n| n > size.w as u32)
                || roi
                    .y
                    .checked_add(roi.height)
                    .is_none_or(|n| n > size.h as u32)
                || rois[..index].iter().any(|old| old.id == roi.id)
            {
                return Err("INVALID_ARGUMENT");
            }
            total = (roi.width as usize)
                .checked_mul(roi.height as usize)
                .and_then(|n| n.checked_mul(4))
                .and_then(|n| n.checked_add(total))
                .filter(|n| *n <= 1024 * 1024)
                .ok_or("READBACK_BUDGET")?;
        }
        let mut target = self.renderer.bind(surface).map_err(gpu_error)?;
        // This pass has no draws or clears; it binds the actual finished pbuffer
        // without renderer.with_context switching to a surfaceless framebuffer.
        let mut frame = self
            .renderer
            .render(&mut target, size, Transform::Flipped180)
            .map_err(gpu_error)?;
        let pixels = frame
            .with_context(|gl| -> Result<Vec<HostReadbackPixels>, Error> {
                // SAFETY: all rectangles and byte counts are checked above. Preserve
                // GL pack state so this read-only observation cannot affect later GL.
                unsafe {
                    super::check_gl_error(gl.GetError())?;
                    let parameters = [
                        ffi::PACK_ALIGNMENT,
                        ffi::PACK_ROW_LENGTH,
                        ffi::PACK_SKIP_PIXELS,
                        ffi::PACK_SKIP_ROWS,
                    ];
                    let mut prior = [0i32; 4];
                    for (attribute, value) in parameters.into_iter().zip(prior.iter_mut()) {
                        gl.GetIntegerv(attribute, value);
                    }
                    let mut pack_buffer = 0i32;
                    gl.GetIntegerv(ffi::PIXEL_PACK_BUFFER_BINDING, &mut pack_buffer);
                    super::check_gl_error(gl.GetError())?;
                    gl.BindBuffer(ffi::PIXEL_PACK_BUFFER, 0);
                    for (attribute, value) in parameters.into_iter().zip([4, 0, 0, 0]) {
                        gl.PixelStorei(attribute, value);
                    }
                    let result = (|| {
                        super::check_gl_error(gl.GetError())?;
                        let mut pixels = Vec::with_capacity(rois.len());
                        for roi in rois {
                            let stride = roi.width as usize * 4;
                            let mut rgba = vec![0u8; stride * roi.height as usize];
                            gl.ReadPixels(
                                roi.x as i32,
                                size.h - (roi.y + roi.height) as i32,
                                roi.width as i32,
                                roi.height as i32,
                                ffi::RGBA,
                                ffi::UNSIGNED_BYTE,
                                rgba.as_mut_ptr().cast(),
                            );
                            super::check_gl_error(gl.GetError())?;
                            // Public ROI rows use the same top-left origin as geometry.
                            for y in 0..roi.height as usize / 2 {
                                let opposite = roi.height as usize - 1 - y;
                                let (upper, lower) = rgba.split_at_mut(opposite * stride);
                                upper[y * stride..(y + 1) * stride]
                                    .swap_with_slice(&mut lower[..stride]);
                            }
                            pixels.push(HostReadbackPixels { roi: *roi, rgba });
                        }
                        Ok(pixels)
                    })();
                    for (attribute, value) in parameters.into_iter().zip(prior) {
                        gl.PixelStorei(attribute, value);
                    }
                    gl.BindBuffer(ffi::PIXEL_PACK_BUFFER, pack_buffer as u32);
                    super::check_gl_error(gl.GetError())?;
                    result
                }
            })
            .map_err(gpu_error)??;
        let _sync = frame.finish().map_err(gpu_error)?;
        drop(target);
        self.finish_gpu_work()?;
        Ok(pixels)
    }
    pub fn gpu_strings(&mut self) -> Result<[String; 3], Error> {
        self.renderer
            .with_context(|gl| {
                [0x1F00, 0x1F01, 0x1F02].map(|name| {
                    let value = unsafe { gl.GetString(name) };
                    if value.is_null() {
                        return "unknown".to_owned();
                    }
                    unsafe { std::ffi::CStr::from_ptr(value.cast()) }
                        .to_string_lossy()
                        .chars()
                        .take(256)
                        .collect()
                })
            })
            .map_err(|_| "EGL_INIT_FAILED")
    }
    /// 診断patternも同じSmithay stateを使い、raw GL描画を混在させない。
    pub fn pattern(
        &mut self,
        surface: &mut EGLSurface,
        frame_seq: u64,
        verify: bool,
    ) -> Result<(), Error> {
        let size = surface.get_size().ok_or("SURFACE_LOST")?;
        let (w, h) = (size.w, size.h);
        self.report.stage = "bind";
        let mut target = self
            .renderer
            .bind(surface)
            .map_err(|error| record_gpu(&mut self.report, error))?;
        // Smithay examples/minimal.rsと同じwindow framebufferの出力変換。
        // canonical左上原点をEGLの下原点へ合わせる。wl_buffer transformとは別。
        self.report.stage = "frame_begin";
        let mut frame = self
            .renderer
            .render(&mut target, size, Transform::Flipped180)
            .map_err(|error| record_gpu(&mut self.report, error))?;
        self.report.stage = "clear";
        frame
            .clear([0., 0., 0., 1.].into(), &[Rectangle::from_size(size)])
            .map_err(|error| record_gpu(&mut self.report, error))?;
        for (x, y, width, height, color) in [
            (0, 0, w / 2, h / 2, [1., 0., 0., 1.]),
            (w / 2, 0, w - w / 2, h / 2, [0., 1., 0., 1.]),
            (0, h / 2, w / 2, h - h / 2, [0., 0., 1., 1.]),
            (w / 2, h / 2, w - w / 2, h - h / 2, [1., 1., 1., 1.]),
            (
                ((frame_seq % w as u64) * 8 % (w - w / 10) as u64) as i32,
                h / 2 - h / 40,
                w / 10,
                (h / 20).max(2),
                [1., 1., 0., 1.],
            ),
        ] {
            let dst = Rectangle::new((x, y).into(), (width, height).into());
            frame
                .draw_solid(dst, &[Rectangle::from_size(dst.size)], color.into())
                .map_err(|error| record_gpu(&mut self.report, error))?;
        }
        self.report.stage = "readback";
        let (matched, error) = frame
            .with_context(|gl| {
                let mut matched = true;
                if verify {
                    for (x, y, expected) in [
                        (8, 8, [255, 0, 0, 255]),
                        (w - 8, 8, [0, 255, 0, 255]),
                        (8, h - 8, [0, 0, 255, 255]),
                        (w - 8, h - 8, [255, 255, 255, 255]),
                    ] {
                        let mut actual = [0u8; 4];
                        unsafe {
                            gl.ReadPixels(
                                x,
                                h - 1 - y,
                                1,
                                1,
                                0x1908,
                                0x1401,
                                actual.as_mut_ptr().cast(),
                            );
                        }
                        matched &= actual == expected;
                    }
                }
                (matched, unsafe { gl.GetError() })
            })
            .map_err(|error| record_gpu(&mut self.report, error))?;
        self.report.gl_error = error;
        self.report.stage = "finish";
        let sync = frame
            .finish()
            .map_err(|error| record_gpu(&mut self.report, error))?;
        drop(target);
        self.report.stage = "fence";
        self.renderer
            .wait(&sync)
            .map_err(|error| record_gpu(&mut self.report, error))?;
        self.report.stage = "bind_for_swap";
        // SAFETY: surface/contextとも同じGL ownerで保持し、別threadではcurrentにしない。
        unsafe {
            self.renderer
                .egl_context()
                .make_current_with_surface(surface)
        }
        .map_err(|error| record_gpu(&mut self.report, error))?;
        self.report.stage = "check_gl_error";
        super::check_gl_error(error)?;
        if !matched {
            return Err("PATTERN_MISMATCH");
        }
        if verify {
            self.report.pattern_readback_samples += 4;
        }
        self.report.stage = "swap";
        let started = Instant::now();
        surface
            .swap_buffers(None)
            .map_err(|error| record_gpu(&mut self.report, error))?;
        self.report.max_swap_us = self
            .report
            .max_swap_us
            .max(started.elapsed().as_micros().min(u64::MAX as u128) as u64);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use eidolon_model::window::LogicalSize;
    #[test]
    fn letterbox_rejects_bars_nonfinite_and_right_bottom_edges() {
        let viewport = Viewport::new(LogicalSize::new(64, 32).unwrap(), 128, 128).unwrap();
        assert_eq!(viewport.origin, [0., 32.]);
        assert_eq!(viewport.logical_at(64., 64.), Some([32., 16.]));
        for (x, y) in [
            (0., 31.),
            (0., 96.),
            (128., 64.),
            (f64::NAN, 0.),
            (0., f64::INFINITY),
        ] {
            assert_eq!(viewport.logical_at(x, y), None);
        }
    }
}
