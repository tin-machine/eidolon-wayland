use super::GpuInfo;
use khronos_egl as egl;
pub use ndk::native_window::NativeWindow as PlatformWindow;
use std::{ffi::CStr, time::Instant};

#[link(name = "GLESv3")]
unsafe extern "C" {
    fn glClearColor(r: f32, g: f32, b: f32, a: f32);
    fn glClear(mask: u32);
    fn glEnable(cap: u32);
    fn glScissor(x: i32, y: i32, w: i32, h: i32);
    fn glViewport(x: i32, y: i32, w: i32, h: i32);
    fn glGetString(name: u32) -> *const std::ffi::c_char;
    fn glReadPixels(
        x: i32,
        y: i32,
        w: i32,
        h: i32,
        format: u32,
        typ: u32,
        data: *mut std::ffi::c_void,
    );
    fn glGetError() -> u32;
}
struct Context {
    egl: egl::DynamicInstance<egl::EGL1_4>,
    display: egl::Display,
    context: Option<egl::Context>,
    surface: Option<egl::Surface>,
}
impl Drop for Context {
    fn drop(&mut self) {
        let _ = self.egl.make_current(self.display, None, None, None);
        if let Some(surface) = self.surface {
            let _ = self.egl.destroy_surface(self.display, surface);
        }
        if let Some(context) = self.context {
            let _ = self.egl.destroy_context(self.display, context);
        }
        // SDKも使うEGLDisplayの寿命は所有しない。
        let _ = self.egl.release_thread();
    }
}
pub struct Target {
    // drop順: current解除/EGLSurface/context → ANativeWindow追加参照。
    context: Option<Context>,
    window: PlatformWindow,
    info: GpuInfo,
    rebuilds: u32,
}
impl Target {
    pub fn new(window: PlatformWindow, width: i32, height: i32) -> Result<Self, &'static str> {
        window
            .set_buffers_geometry(width, height, None)
            .map_err(|_| "EGL_INIT_FAILED")?;
        let mut target = Self {
            context: None,
            window,
            info: GpuInfo {
                width,
                height,
                ..Default::default()
            },
            rebuilds: 0,
        };
        target.initialize()?;
        Ok(target)
    }
    fn initialize(&mut self) -> Result<(), &'static str> {
        let egl = unsafe {
            egl::DynamicInstance::<egl::EGL1_4>::load_required_from_filename("libEGL.so")
        }
        .map_err(|_| "EGL_INIT_FAILED")?;
        let display = unsafe { egl.get_display(egl::DEFAULT_DISPLAY) }.ok_or("EGL_INIT_FAILED")?;
        egl.initialize(display).map_err(|_| "EGL_INIT_FAILED")?;
        let mut context = Context {
            egl,
            display,
            context: None,
            surface: None,
        };
        context
            .egl
            .bind_api(egl::OPENGL_ES_API)
            .map_err(|_| "EGL_INIT_FAILED")?;
        let config = context
            .egl
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
        context.context = Some(
            context
                .egl
                .create_context(
                    display,
                    config,
                    None,
                    &[egl::CONTEXT_CLIENT_VERSION, 3, egl::NONE],
                )
                .map_err(|_| "EGL_INIT_FAILED")?,
        );
        context.surface = Some(
            unsafe {
                context.egl.create_window_surface(
                    display,
                    config,
                    self.window.ptr().as_ptr().cast(),
                    None,
                )
            }
            .map_err(|_| "EGL_INIT_FAILED")?,
        );
        context
            .egl
            .make_current(display, context.surface, context.surface, context.context)
            .map_err(map_error)?;
        context.egl.swap_interval(display, 1).map_err(map_error)?;
        let surface = context.surface.unwrap();
        self.info.config_id = context
            .egl
            .get_config_attrib(display, config, egl::CONFIG_ID)
            .map_err(map_error)?;
        for (index, attribute) in [
            egl::RED_SIZE,
            egl::GREEN_SIZE,
            egl::BLUE_SIZE,
            egl::ALPHA_SIZE,
        ]
        .into_iter()
        .enumerate()
        {
            self.info.rgba_bits[index] = context
                .egl
                .get_config_attrib(display, config, attribute)
                .map_err(map_error)?;
        }
        self.info.width = context
            .egl
            .query_surface(display, surface, egl::WIDTH)
            .map_err(map_error)?;
        self.info.height = context
            .egl
            .query_surface(display, surface, egl::HEIGHT)
            .map_err(map_error)?;
        if self.info.width < 32 || self.info.height < 32 {
            return Err("EGL_INIT_FAILED");
        }
        self.info.vendor = gl_string(0x1F00);
        self.info.renderer = gl_string(0x1F01);
        self.info.version = gl_string(0x1F02);
        self.info.readback = false;
        self.context = Some(context);
        Ok(())
    }
    pub fn info(&self) -> GpuInfo {
        self.info.clone()
    }
    pub fn rebuild(&mut self) -> Result<(), &'static str> {
        self.context = None;
        self.rebuilds += 1;
        if self.rebuilds > 3 {
            return Err("SURFACE_LOST");
        }
        self.initialize()
    }
    pub fn frame(&mut self, frame: u64) -> Result<(), &'static str> {
        let ctx = self.context.as_ref().ok_or("SURFACE_LOST")?;
        ctx.egl
            .make_current(ctx.display, ctx.surface, ctx.surface, ctx.context)
            .map_err(map_error)?;
        let (width, height) = (self.info.width, self.info.height);
        unsafe {
            glViewport(0, 0, width, height);
            glEnable(0x0C11);
            // OpenGLのy原点は下。表示の左上赤/右上緑/左下青/右下白。
            for (x, y, w, h, color) in [
                (0, height / 2, width / 2, height - height / 2, [1., 0., 0.]),
                (
                    width / 2,
                    height / 2,
                    width - width / 2,
                    height - height / 2,
                    [0., 1., 0.],
                ),
                (0, 0, width / 2, height / 2, [0., 0., 1.]),
                (width / 2, 0, width - width / 2, height / 2, [1., 1., 1.]),
            ] {
                glScissor(x, y, w, h);
                glClearColor(color[0], color[1], color[2], 1.);
                glClear(0x4000);
            }
            let bar = (width / 10).max(2);
            glScissor(
                ((frame % width as u64) * 8 % (width - bar) as u64) as i32,
                height / 2 - height / 40,
                bar,
                (height / 20).max(2),
            );
            glClearColor(1., 1., 0., 1.);
            glClear(0x4000);
            let mut pixels_match = true;
            if !self.info.readback {
                let expected = [
                    [255, 0, 0, 255],
                    [0, 255, 0, 255],
                    [0, 0, 255, 255],
                    [255, 255, 255, 255],
                ];
                for ((x, y), color) in [
                    (8, height - 8),
                    (width - 8, height - 8),
                    (8, 8),
                    (width - 8, 8),
                ]
                .into_iter()
                .zip(expected)
                {
                    let mut pixel = [0u8; 4];
                    glReadPixels(x, y, 1, 1, 0x1908, 0x1401, pixel.as_mut_ptr().cast());
                    pixels_match &= pixel == color;
                }
            }
            // GL_CONTEXT_LOSTもEGL側のContextLostと同じ再構築経路へ送る。
            super::check_gl_error(glGetError())?;
            if !pixels_match {
                return Err("PATTERN_MISMATCH");
            }
            self.info.readback = true;
        }
        let started = Instant::now();
        ctx.egl
            .swap_buffers(ctx.display, ctx.surface.unwrap())
            .map_err(map_error)?;
        self.info.max_swap_us = self
            .info
            .max_swap_us
            .max(started.elapsed().as_micros().min(u64::MAX as u128) as u64);
        Ok(())
    }
}
fn map_error(error: egl::Error) -> &'static str {
    if error == egl::Error::ContextLost {
        "EGL_CONTEXT_LOST"
    } else {
        "SURFACE_LOST"
    }
}
fn gl_string(name: u32) -> String {
    let value = unsafe { glGetString(name) };
    if value.is_null() {
        return "unknown".into();
    }
    unsafe { CStr::from_ptr(value) }
        .to_string_lossy()
        .chars()
        .take(256)
        .collect()
}
