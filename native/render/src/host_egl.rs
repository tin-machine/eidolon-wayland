//! Linux-only acceptance adapter. Every handle is created and destroyed by the
//! normal GL owner. This backend never falls back to the host bookkeeping stub.
use crate::{
    GpuInfo,
    compositor::{Compositor, Probe, Report},
    decoration::Composition,
    host_readback::{HostReadbackPixels, HostReadbackRoi},
};
use eidolon_model::{ids::*, limits, window::WindowSnapshot};
use khronos_egl as egl;
use smithay::backend::egl::{
    EGLContext, EGLError, EGLSurface, display::EGLDisplayHandle, ffi, native::EGLNativeSurface,
    wrap_egl_call_ptr,
};
use std::{
    collections::HashMap,
    ffi::c_void,
    marker::PhantomData,
    rc::Rc,
    sync::{
        Arc, Mutex, OnceLock,
        atomic::{AtomicU64, AtomicUsize, Ordering},
    },
};

const EGL_PLATFORM_SURFACELESS_MESA: egl::Enum = 0x31dd;

// EGL may return the same display for several renderer owners in this process.
// eglInitialize/eglTerminate are not reference counted by the driver. Keep a
// process lease, and terminate only after the final owned context was destroyed.
fn display_users() -> &'static Mutex<HashMap<usize, usize>> {
    static USERS: OnceLock<Mutex<HashMap<usize, usize>>> = OnceLock::new();
    USERS.get_or_init(|| Mutex::new(HashMap::new()))
}

struct RawContext {
    egl: egl::DynamicInstance<egl::EGL1_5>,
    display: egl::Display,
    config: Option<egl::Config>,
    context: Option<egl::Context>,
    display_lease: bool,
    thread_released: bool,
    // Raw EGL handles must never cross the GL owner thread.
    _thread: PhantomData<Rc<()>>,
}
impl Drop for RawContext {
    fn drop(&mut self) {
        // Failure is already retained by the explicit shutdown caller. Drop is
        // only a best-effort cleanup path, never a successful receipt.
        let _ = self.shutdown();
    }
}
impl RawContext {
    fn shutdown(&mut self) -> Result<(), &'static str> {
        if self.display_lease || self.context.is_some() {
            self.egl
                .make_current(self.display, None, None, None)
                .map_err(|_| "EGL_SHUTDOWN_FAILED")?;
        }
        if let Some(context) = self.context {
            self.egl
                .destroy_context(self.display, context)
                .map_err(|_| "EGL_SHUTDOWN_FAILED")?;
            self.context = None;
        }
        let mut users = display_users().lock().unwrap_or_else(|e| e.into_inner());
        let key = self.display.as_ptr() as usize;
        if self.display_lease {
            let count = users.get_mut(&key).ok_or("EGL_SHUTDOWN_FAILED")?;
            if *count == 1 {
                self.egl
                    .terminate(self.display)
                    .map_err(|_| "EGL_SHUTDOWN_FAILED")?;
                users.remove(&key);
            } else {
                *count -= 1;
            }
            self.display_lease = false;
        }
        if !self.thread_released {
            self.egl
                .release_thread()
                .map_err(|_| "EGL_SHUTDOWN_FAILED")?;
            self.thread_released = true;
        }
        Ok(())
    }
}

struct PbufferAllocation {
    used: Arc<AtomicUsize>,
    bytes: usize,
}
impl Drop for PbufferAllocation {
    fn drop(&mut self) {
        self.used.fetch_sub(self.bytes, Ordering::Relaxed);
    }
}
struct Pbuffer {
    width: i32,
    height: i32,
    _allocation: PbufferAllocation,
}
// SAFETY: The dimensions are validated before construction. EGLSurface owns the
// created pbuffer and this allocation lease; creation/drop stay on the GL owner.
unsafe impl EGLNativeSurface for Pbuffer {
    unsafe fn create(
        &self,
        display: &Arc<EGLDisplayHandle>,
        config: ffi::egl::types::EGLConfig,
    ) -> Result<*const c_void, EGLError> {
        wrap_egl_call_ptr(|| unsafe {
            ffi::egl::CreatePbufferSurface(
                ***display,
                config,
                [egl::WIDTH, self.width, egl::HEIGHT, self.height, egl::NONE].as_ptr(),
            )
        })
    }
}

pub struct Device {
    // Rust drops fields in declaration order: textures/Smithay first, raw EGL last.
    compositor: Compositor,
    raw: RawContext,
    pbuffer_bytes: Arc<AtomicUsize>,
    epoch: u64,
}
impl Device {
    pub fn new() -> Result<Self, &'static str> {
        let egl = unsafe {
            egl::DynamicInstance::<egl::EGL1_5>::load_required_from_filename("libEGL.so.1")
        }
        .map_err(|_| "EGL_INIT_FAILED")?;
        // Explicit platform selection: no desktop, X11, Wayland or default-display
        // fallback is permitted, irrespective of the caller's environment.
        let display = unsafe {
            egl.get_platform_display(
                EGL_PLATFORM_SURFACELESS_MESA,
                egl::DEFAULT_DISPLAY,
                &[egl::ATTRIB_NONE],
            )
        }
        .map_err(|_| "EGL_INIT_FAILED")?;
        {
            let mut users = display_users().lock().map_err(|_| "EGL_INIT_FAILED")?;
            let count = users.entry(display.as_ptr() as usize).or_default();
            if *count == 0 {
                egl.initialize(display).map_err(|_| "EGL_INIT_FAILED")?;
            }
            *count = count.checked_add(1).ok_or("EGL_INIT_FAILED")?;
        }
        let mut raw = RawContext {
            egl,
            display,
            config: None,
            context: None,
            display_lease: true,
            thread_released: false,
            _thread: PhantomData,
        };
        let initialized = (|| {
            raw.egl
                .bind_api(egl::OPENGL_ES_API)
                .map_err(|_| "EGL_INIT_FAILED")?;
            let config = raw
                .egl
                .choose_first_config(
                    display,
                    &[
                        egl::SURFACE_TYPE,
                        egl::PBUFFER_BIT,
                        egl::RENDERABLE_TYPE,
                        egl::OPENGL_ES3_BIT,
                        egl::RED_SIZE,
                        8,
                        egl::GREEN_SIZE,
                        8,
                        egl::BLUE_SIZE,
                        8,
                        egl::ALPHA_SIZE,
                        8,
                        egl::SAMPLES,
                        0,
                        egl::SAMPLE_BUFFERS,
                        0,
                        egl::DEPTH_SIZE,
                        0,
                        egl::STENCIL_SIZE,
                        0,
                        egl::NONE,
                    ],
                )
                .map_err(|_| "EGL_INIT_FAILED")?
                .ok_or("EGL_INIT_FAILED")?;
            for attribute in [
                egl::RED_SIZE,
                egl::GREEN_SIZE,
                egl::BLUE_SIZE,
                egl::ALPHA_SIZE,
            ] {
                if raw
                    .egl
                    .get_config_attrib(display, config, attribute)
                    .map_err(|_| "EGL_INIT_FAILED")?
                    != 8
                {
                    return Err("RGBA_UNSUPPORTED");
                }
            }
            for attribute in [
                egl::SAMPLES,
                egl::SAMPLE_BUFFERS,
                egl::DEPTH_SIZE,
                egl::STENCIL_SIZE,
            ] {
                if raw
                    .egl
                    .get_config_attrib(display, config, attribute)
                    .map_err(|_| "EGL_INIT_FAILED")?
                    != 0
                {
                    return Err("RGBA_UNSUPPORTED");
                }
            }
            raw.config = Some(config);
            let context = raw
                .egl
                .create_context(
                    display,
                    config,
                    None,
                    &[egl::CONTEXT_CLIENT_VERSION, 3, egl::NONE],
                )
                .map_err(|_| "EGL_INIT_FAILED")?;
            raw.context = Some(context);
            // SAFETY: The external context belongs to this thread-bound Device and
            // outlives the compositor. No Android/SDK display is obtained or touched.
            let context = unsafe {
                EGLContext::from_raw(display.as_ptr(), config.as_ptr(), context.as_ptr())
            }
            .map_err(|_| "EGL_INIT_FAILED")?;
            let mut compositor = unsafe { Compositor::new(context) }?;
            let [vendor, renderer, version] = compositor.gpu_strings()?;
            if !vendor.to_ascii_lowercase().contains("mesa")
                || !renderer.to_ascii_lowercase().contains("llvmpipe")
                || !version.starts_with("OpenGL ES 3.")
            {
                return Err("HOST_EGL_BACKEND_MISMATCH");
            }
            Ok(compositor)
        })();
        let compositor = match initialized {
            Ok(compositor) => compositor,
            Err(error) => {
                return Err(match raw.shutdown() {
                    Ok(()) => error,
                    Err(_) => "EGL_SHUTDOWN_FAILED",
                });
            }
        };
        static EPOCH: AtomicU64 = AtomicU64::new(1);
        let epoch =
            match EPOCH.fetch_update(Ordering::Relaxed, Ordering::Relaxed, |n| n.checked_add(1)) {
                Ok(epoch) => epoch,
                Err(_) => {
                    drop(compositor);
                    return Err(match raw.shutdown() {
                        Ok(()) => "EGL_INIT_FAILED",
                        Err(_) => "EGL_SHUTDOWN_FAILED",
                    });
                }
            };
        Ok(Self {
            compositor,
            raw,
            pbuffer_bytes: Arc::new(AtomicUsize::new(0)),
            epoch,
        })
    }

    fn surface(&mut self, width: i32, height: i32) -> Result<EGLSurface, &'static str> {
        crate::validate_dimensions(width, height).map_err(|_| "INVALID_ARGUMENT")?;
        let bytes = (width as usize)
            .checked_mul(height as usize)
            .and_then(|n| n.checked_mul(4))
            .ok_or("RENDER_BUDGET")?;
        let allocated = self
            .pbuffer_bytes
            .load(Ordering::Relaxed)
            .checked_add(bytes)
            .filter(|n| *n <= limits::PIXEL_BYTES)
            .ok_or("RENDER_BUDGET")?;
        self.pbuffer_bytes.store(allocated, Ordering::Relaxed);
        let native = Pbuffer {
            width,
            height,
            _allocation: PbufferAllocation {
                used: self.pbuffer_bytes.clone(),
                bytes,
            },
        };
        let context = self.compositor.context();
        // SAFETY: The surface uses the same live display/config as its owner.
        unsafe {
            EGLSurface::new(
                context.display(),
                context.pixel_format().ok_or("EGL_INIT_FAILED")?,
                context.config_id(),
                native,
            )
        }
        .map_err(|_| "SURFACE_LOST")
    }

    fn info(&mut self, surface: &EGLSurface) -> Result<GpuInfo, &'static str> {
        let [vendor, renderer, version] = self.compositor.gpu_strings()?;
        let size = surface.get_size().ok_or("SURFACE_LOST")?;
        let config = self.raw.config.ok_or("EGL_INIT_FAILED")?;
        Ok(GpuInfo {
            width: size.w,
            height: size.h,
            vendor,
            renderer,
            version,
            config_id: self
                .raw
                .egl
                .get_config_attrib(self.raw.display, config, egl::CONFIG_ID)
                .map_err(|_| "EGL_INIT_FAILED")?,
            rgba_bits: [8; 4],
            ..Default::default()
        })
    }
    pub fn finish_gpu_work(&mut self) -> Result<(), &'static str> {
        self.check_egl_error()?;
        self.compositor.finish_gpu_work()
    }
    pub fn shutdown(self) -> Result<(), &'static str> {
        let prior = self.check_egl_error();
        let Device {
            mut compositor,
            mut raw,
            pbuffer_bytes,
            ..
        } = self;
        let surfaces = if pbuffer_bytes.load(Ordering::Relaxed) == 0 {
            Ok(())
        } else {
            Err("EGL_SURFACES_PENDING")
        };
        let clear = compositor.clear();
        let finish = compositor.finish_gpu_work();
        drop(compositor);
        let release = raw.shutdown();
        prior.and(surfaces).and(clear).and(finish).and(release)
    }
    fn check_egl_error(&self) -> Result<(), &'static str> {
        match self.raw.egl.get_error() {
            None => Ok(()),
            Some(egl::Error::ContextLost) => Err("EGL_CONTEXT_LOST"),
            Some(_) => Err("SURFACE_LOST"),
        }
    }
    pub fn forget(&mut self, session: SessionId, window: WindowId) -> Result<(), &'static str> {
        self.compositor.forget(session, window)
    }
    pub fn stage_theme(
        &mut self,
        theme: Option<&eidolon_theme::raster::CompiledTheme>,
    ) -> Result<(), &'static str> {
        self.compositor.stage_theme(theme)
    }
    pub fn finish_theme(&mut self, adopt: bool) -> Result<(), &'static str> {
        self.compositor.finish_theme(adopt)
    }
    pub fn report(&self) -> Report {
        self.compositor.report()
    }
    pub fn clear(&mut self) -> Result<(), &'static str> {
        self.compositor.clear()
    }
}

#[derive(Default)]
pub struct PlatformWindow {
    _private: (),
}

pub struct Target {
    surface: Option<EGLSurface>,
    info: GpuInfo,
    epoch: u64,
    rebuilds: u32,
    _thread: PhantomData<Rc<()>>,
}
impl Target {
    pub fn new(
        device: &mut Device,
        _: PlatformWindow,
        width: i32,
        height: i32,
    ) -> Result<Self, &'static str> {
        let surface = device.surface(width, height)?;
        let info = match device.info(&surface) {
            Ok(info) => info,
            Err(error) => {
                drop(surface);
                return Err(if device.check_egl_error().is_ok() {
                    error
                } else {
                    "EGL_SHUTDOWN_FAILED"
                });
            }
        };
        if [info.width, info.height] != [width, height] {
            drop(surface);
            return Err(if device.check_egl_error().is_ok() {
                "SURFACE_LOST"
            } else {
                "EGL_SHUTDOWN_FAILED"
            });
        }
        Ok(Self {
            surface: Some(surface),
            info,
            epoch: device.epoch,
            rebuilds: 0,
            _thread: PhantomData,
        })
    }
    pub fn info(&self) -> GpuInfo {
        self.info.clone()
    }
    pub fn retire(&mut self, device: &mut Device) -> Result<(), &'static str> {
        if self.epoch != device.epoch {
            return Err("EGL_CONTEXT_LOST");
        }
        // This also unbinds the pbuffer. EGL otherwise defers destruction of a
        // surface that remains current, even after eglDestroySurface succeeds.
        device.finish_gpu_work()?;
        drop(self.surface.take());
        device.check_egl_error()
    }
    pub fn invalidate(&mut self) {
        self.surface = None;
    }
    pub fn rebuild(&mut self, device: &mut Device) -> Result<(), &'static str> {
        self.rebuilds += 1;
        if self.rebuilds > 3 {
            return Err("SURFACE_LOST");
        }
        let surface = device.surface(self.info.width, self.info.height)?;
        self.info = device.info(&surface)?;
        self.surface = Some(surface);
        self.epoch = device.epoch;
        Ok(())
    }
    pub fn frame(
        &mut self,
        device: &mut Device,
        frame: u64,
        snapshot: Option<&WindowSnapshot>,
        probes: &[Probe],
        decoration: Option<&Composition<'_>>,
    ) -> Result<(), &'static str> {
        if self.epoch != device.epoch {
            return Err("EGL_CONTEXT_LOST");
        }
        let surface = self.surface.as_mut().ok_or("SURFACE_LOST")?;
        if let Some(snapshot) = snapshot {
            device
                .compositor
                .paint_decorated(surface, snapshot, probes, decoration)?;
        } else {
            device
                .compositor
                .pattern(surface, frame, !self.info.readback)?;
        }
        self.info.readback |= snapshot.is_none() || !probes.is_empty();
        self.info.max_swap_us = device.compositor.report().max_swap_us;
        Ok(())
    }
    pub fn frame_readback(
        &mut self,
        device: &mut Device,
        _frame: u64,
        snapshot: &WindowSnapshot,
        probes: &[Probe],
        decoration: Option<&Composition<'_>>,
        rois: &[HostReadbackRoi],
    ) -> Result<Vec<HostReadbackPixels>, &'static str> {
        if self.epoch != device.epoch {
            return Err("EGL_CONTEXT_LOST");
        }
        let surface = self.surface.as_mut().ok_or("SURFACE_LOST")?;
        let pixels = device
            .compositor
            .paint_decorated_readback(surface, snapshot, probes, decoration, rois)?;
        self.info.readback = true;
        self.info.max_swap_us = device.compositor.report().max_swap_us;
        Ok(pixels)
    }
}
