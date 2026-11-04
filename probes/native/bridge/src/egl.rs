use super::log;
use khronos_egl as egl;
use ndk::native_window::NativeWindow;
use serde_json::{Value, json};
use std::{
    ffi::CStr,
    sync::{
        Mutex,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

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
        if let Some(s) = self.surface {
            let _ = self.egl.destroy_surface(self.display, s);
        }
        if let Some(c) = self.context {
            let _ = self.egl.destroy_context(self.display, c);
        }
        // SDKと共有し得るprocess-wide EGLDisplayへeglTerminateしない。
        let _ = self.egl.release_thread();
    }
}

pub fn render(
    window: NativeWindow,
    stop: &AtomicBool,
    paused: &AtomicBool,
    stats: &Mutex<Value>,
) -> Result<(), String> {
    let egl =
        unsafe { egl::DynamicInstance::<egl::EGL1_4>::load_required_from_filename("libEGL.so") }
            .map_err(|e| e.to_string())?;
    let display = unsafe { egl.get_display(egl::DEFAULT_DISPLAY) }.ok_or("no EGL display")?;
    egl.initialize(display).map_err(|e| e.to_string())?;
    let mut ctx = Context {
        egl,
        display,
        context: None,
        surface: None,
    };
    ctx.egl
        .bind_api(egl::OPENGL_ES_API)
        .map_err(|e| e.to_string())?;
    let config = ctx
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
        .map_err(|e| e.to_string())?
        .ok_or("no GLES3 config")?;
    let context = ctx
        .egl
        .create_context(
            display,
            config,
            None,
            &[egl::CONTEXT_CLIENT_VERSION, 3, egl::NONE],
        )
        .map_err(|e| e.to_string())?;
    ctx.context = Some(context);
    window
        .set_buffers_geometry(1024, 768, None)
        .map_err(|e| e.to_string())?;
    let surface = unsafe {
        ctx.egl
            .create_window_surface(display, config, window.ptr().as_ptr().cast(), None)
    }
    .map_err(|e| e.to_string())?;
    ctx.surface = Some(surface);
    ctx.egl
        .make_current(display, Some(surface), Some(surface), Some(context))
        .map_err(|e| e.to_string())?;
    ctx.egl
        .swap_interval(display, 1)
        .map_err(|e| e.to_string())?;
    let width = ctx
        .egl
        .query_surface(display, surface, egl::WIDTH)
        .map_err(|e| e.to_string())?;
    let height = ctx
        .egl
        .query_surface(display, surface, egl::HEIGHT)
        .map_err(|e| e.to_string())?;
    let renderer = unsafe {
        let s = glGetString(0x1F01);
        if s.is_null() {
            "unknown".into()
        } else {
            CStr::from_ptr(s).to_string_lossy().into_owned()
        }
    };
    {
        let mut st = stats.lock().unwrap();
        st["width"] = json!(width);
        st["height"] = json!(height);
        st["renderer"] = json!(renderer);
    }
    log(&format!(
        "egl_ready width={width} height={height} renderer={renderer}"
    ));
    let mut frames = 0u64;
    let mut max_swap_us = 0u128;
    while !stop.load(Ordering::Relaxed) {
        if paused.load(Ordering::Relaxed) {
            thread::sleep(Duration::from_millis(10));
            continue;
        }
        unsafe {
            glViewport(0, 0, width, height);
            glEnable(0x0C11);
            let colors = [
                (0, height / 2, [1., 0., 0.]),
                (width / 2, height / 2, [0., 1., 0.]),
                (0, 0, [0., 0., 1.]),
                (width / 2, 0, [1., 1., 1.]),
            ];
            for (x, y, c) in colors {
                glScissor(x, y, width / 2, height / 2);
                glClearColor(c[0], c[1], c[2], 1.);
                glClear(0x4000);
            }
            glScissor((frames as i32 * 8) % (width - 80), height / 2 - 20, 80, 40);
            glClearColor(1., 1., 0., 1.);
            glClear(0x4000);
            if frames == 0 {
                let mut pixels = Vec::new();
                for (x, y) in [
                    (8, height - 8),
                    (width - 8, height - 8),
                    (8, 8),
                    (width - 8, 8),
                ] {
                    let mut pixel = [0u8; 4];
                    glReadPixels(x, y, 1, 1, 0x1908, 0x1401, pixel.as_mut_ptr().cast());
                    pixels.push(pixel);
                }
                let expected = [
                    [255, 0, 0, 255],
                    [0, 255, 0, 255],
                    [0, 0, 255, 255],
                    [255, 255, 255, 255],
                ];
                let good = pixels == expected && glGetError() == 0;
                let mut st = stats.lock().unwrap();
                st["readback"] = json!(good);
                st["corners"] = json!(pixels);
                if !good {
                    return Err("pixel readback mismatch".into());
                }
            }
        }
        let started = Instant::now();
        ctx.egl
            .swap_buffers(display, surface)
            .map_err(|e| format!("swap: {e}"))?;
        max_swap_us = max_swap_us.max(started.elapsed().as_micros());
        frames += 1;
        {
            let mut st = stats.lock().unwrap();
            st["frames"] = json!(frames);
            st["max_swap_us"] = json!(max_swap_us);
        }
        if frames == 1 || frames % 120 == 0 {
            log(&format!(
                "render_progress frames={frames} max_swap_us={max_swap_us}"
            ));
        }
        thread::sleep(Duration::from_millis(16));
    }
    Ok(())
}
