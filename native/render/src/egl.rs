//! Android EGL adapter。Displayを終了せず、一つのGL ownerがcontextと複数Surfaceを保持する。
use crate::{
    GpuInfo,
    compositor::{Compositor, Probe, Report},
};
use eidolon_model::window::WindowSnapshot;
use khronos_egl as egl;
use ndk::native_window::NativeWindow;
use smithay::backend::egl::{
    EGLContext, EGLError, EGLSurface, display::EGLDisplayHandle, ffi, native::EGLNativeSurface,
    wrap_egl_call_ptr,
};
use std::{ffi::c_void, sync::Arc};

struct RawContext {
    egl: egl::DynamicInstance<egl::EGL1_4>,
    display: egl::Display,
    config: egl::Config,
    context: Option<egl::Context>,
}
impl Drop for RawContext {
    fn drop(&mut self) {
        let _ = self.egl.make_current(self.display, None, None, None);
        if let Some(context) = self.context.take() {
            let _ = self.egl.destroy_context(self.display, context);
        }
        // SDKと共有するDisplayにはeglTerminateしない。
        let _ = self.egl.release_thread();
    }
}
struct AndroidWindow(NativeWindow);
// SAFETY: NativeWindowの追加参照をEGLSurface全寿命に保持する。create/dropはGL ownerだけ。
unsafe impl EGLNativeSurface for AndroidWindow {
    unsafe fn create(
        &self,
        display: &Arc<EGLDisplayHandle>,
        config: ffi::egl::types::EGLConfig,
    ) -> Result<*const c_void, EGLError> {
        wrap_egl_call_ptr(|| unsafe {
            ffi::egl::CreateWindowSurface(
                ***display,
                config,
                self.0.ptr().as_ptr().cast(),
                std::ptr::null(),
            )
        })
    }
}

pub struct Device {
    // 外部context handleを破棄する前にtexture/Smithay rendererを回収する。
    compositor: Compositor,
    raw: RawContext,
}
impl Device {
    pub fn new() -> Result<Self, &'static str> {
        let egl = unsafe {
            egl::DynamicInstance::<egl::EGL1_4>::load_required_from_filename("libEGL.so")
        }
        .map_err(|_| "EGL_INIT_FAILED")?;
        let display = unsafe { egl.get_display(egl::DEFAULT_DISPLAY) }.ok_or("EGL_INIT_FAILED")?;
        egl.initialize(display).map_err(|_| "EGL_INIT_FAILED")?;
        egl.bind_api(egl::OPENGL_ES_API)
            .map_err(|_| "EGL_INIT_FAILED")?;
        let config = egl
            .choose_first_config(
                display,
                &[
                    egl::SURFACE_TYPE,
                    egl::WINDOW_BIT,
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
                    egl::NONE,
                ],
            )
            .map_err(|_| "EGL_INIT_FAILED")?
            .ok_or("EGL_INIT_FAILED")?;
        let mut raw = RawContext {
            egl,
            display,
            config,
            context: None,
        };
        raw.context = Some(
            raw.egl
                .create_context(
                    display,
                    config,
                    None,
                    &[egl::CONTEXT_CLIENT_VERSION, 3, egl::NONE],
                )
                .map_err(|_| "EGL_INIT_FAILED")?,
        );
        // SAFETY: Android system libEGLで作成した有効handle。本DeviceはGL ownerから移動せず、
        // compositorより後にrawをdropし、Displayのshared寿命は変えない。
        let context = unsafe {
            EGLContext::from_raw(
                display.as_ptr(),
                config.as_ptr(),
                raw.context.unwrap().as_ptr(),
            )
        }
        .map_err(|_| "EGL_INIT_FAILED")?;
        let compositor = unsafe { Compositor::new(context) }?;
        Ok(Self { compositor, raw })
    }
    pub fn surface(
        &mut self,
        window: NativeWindow,
        width: i32,
        height: i32,
    ) -> Result<EGLSurface, &'static str> {
        crate::validate_dimensions(width, height).map_err(|_| "INVALID_ARGUMENT")?;
        window
            .set_buffers_geometry(width, height, None)
            .map_err(|_| "SURFACE_LOST")?;
        let context = self.compositor.context();
        // SAFETY: pixel format/config/displayはこのDeviceのcontextに由来し、NativeWindowをsurfaceが所有する。
        unsafe {
            EGLSurface::new(
                context.display(),
                context.pixel_format().ok_or("EGL_INIT_FAILED")?,
                context.config_id(),
                AndroidWindow(window),
            )
        }
        .map_err(|_| "SURFACE_LOST")
    }
    pub fn info(&mut self, surface: &EGLSurface) -> Result<GpuInfo, &'static str> {
        let [vendor, renderer, version] = self.compositor.gpu_strings()?;
        let size = surface.get_size().ok_or("SURFACE_LOST")?;
        let mut info = GpuInfo {
            width: size.w,
            height: size.h,
            vendor,
            renderer,
            version,
            ..Default::default()
        };
        info.config_id = self
            .raw
            .egl
            .get_config_attrib(self.raw.display, self.raw.config, egl::CONFIG_ID)
            .map_err(|_| "EGL_INIT_FAILED")?;
        for (index, attribute) in [
            egl::RED_SIZE,
            egl::GREEN_SIZE,
            egl::BLUE_SIZE,
            egl::ALPHA_SIZE,
        ]
        .into_iter()
        .enumerate()
        {
            info.rgba_bits[index] = self
                .raw
                .egl
                .get_config_attrib(self.raw.display, self.raw.config, attribute)
                .map_err(|_| "EGL_INIT_FAILED")?;
        }
        Ok(info)
    }
    pub fn paint(
        &mut self,
        surface: &mut EGLSurface,
        snapshot: &WindowSnapshot,
        probes: &[Probe],
        decoration: Option<&crate::decoration::Composition<'_>>,
    ) -> Result<u64, &'static str> {
        self.compositor
            .paint_decorated(surface, snapshot, probes, decoration)?;
        Ok(self.compositor.report().max_swap_us)
    }
    pub fn prepare_theme(
        &mut self,
        theme: Option<&eidolon_theme::raster::CompiledTheme>,
    ) -> Result<(), &'static str> {
        self.compositor.prepare_theme(theme)
    }
    pub fn report(&self) -> Report {
        self.compositor.report()
    }
    pub fn clear(&mut self) -> Result<(), &'static str> {
        self.compositor.clear()
    }
    pub fn pattern(
        &mut self,
        surface: &mut EGLSurface,
        frame: u64,
        verify: bool,
    ) -> Result<u64, &'static str> {
        self.compositor.pattern(surface, frame, verify)?;
        Ok(self.compositor.report().max_swap_us)
    }
    pub fn forget(
        &mut self,
        session: eidolon_model::ids::SessionId,
        window: eidolon_model::ids::WindowId,
    ) -> Result<(), &'static str> {
        self.compositor.forget(session, window)
    }
}
