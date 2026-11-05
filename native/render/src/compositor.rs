//! GL owner専用の画像合成。入力はimmutableなowned pixelと検査済みgeometryだけ。
use eidolon_model::{
    ids::{ContentSeq, SessionId, SurfaceId, WindowId},
    limits,
    window::{LogicalRect, LogicalSize, WindowSnapshot},
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

/// 左上原点。window全体の縦横比を保ち、余白をhit可能な画像へ含めない。
#[derive(Clone, Copy, Debug)]
pub struct Viewport {
    pub target: LogicalSize,
    pub logical: LogicalSize,
    pub origin: [f64; 2],
    pub scale: f64,
}
impl Viewport {
    pub fn new(logical: LogicalSize, width: i32, height: i32) -> Result<Self, Error> {
        super::validate_dimensions(width, height).map_err(|_| "INVALID_ARGUMENT")?;
        let logical =
            LogicalSize::new(logical.width, logical.height).map_err(|_| "INVALID_ARGUMENT")?;
        let scale = (f64::from(width) / f64::from(logical.width))
            .min(f64::from(height) / f64::from(logical.height));
        Ok(Self {
            target: LogicalSize {
                width: width as u32,
                height: height as u32,
            },
            logical,
            scale,
            origin: [
                (f64::from(width) - f64::from(logical.width) * scale) / 2.,
                (f64::from(height) - f64::from(logical.height) * scale) / 2.,
            ],
        })
    }
    pub fn logical_at(&self, x: f64, y: f64) -> Option<[f64; 2]> {
        let x = (x - self.origin[0]) / self.scale;
        let y = (y - self.origin[1]) / self.scale;
        (x.is_finite()
            && y.is_finite()
            && x >= 0.
            && y >= 0.
            && x < f64::from(self.logical.width)
            && y < f64::from(self.logical.height))
        .then_some([x, y])
    }
    fn rect(&self, rect: LogicalRect) -> Rectangle<i32, Physical> {
        let x = (self.origin[0] + f64::from(rect.origin.x) * self.scale).round() as i32;
        let y = (self.origin[1] + f64::from(rect.origin.y) * self.scale).round() as i32;
        let right = (self.origin[0]
            + (f64::from(rect.origin.x) + f64::from(rect.size.width)) * self.scale)
            .round() as i32;
        let bottom = (self.origin[1]
            + (f64::from(rect.origin.y) + f64::from(rect.size.height)) * self.scale)
            .round() as i32;
        Rectangle::new((x, y).into(), (right - x, bottom - y).into())
    }
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
        self.refresh();
        self.renderer
            .cleanup_texture_cache()
            .map_err(|error| record_gpu(&mut self.report, error))
    }
    fn refresh(&mut self) {
        self.report.textures = self.cache.len();
        self.report.texture_bytes = self.cache.values().map(|v| v.bytes).sum();
    }
    pub fn paint(
        &mut self,
        surface: &mut EGLSurface,
        snapshot: &WindowSnapshot,
        probes: &[Probe],
    ) -> Result<(), Error> {
        let size = surface.get_size().ok_or("SURFACE_LOST")?;
        let viewport = Viewport::new(snapshot.logical_size, size.w, size.h)?;
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
            let dst = viewport.rect(layer.rect().map_err(|_| "INVALID_ARGUMENT")?);
            let Some(clip) = viewport.rect(layer.clip_logical).intersection(dst) else {
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
        Ok(())
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
