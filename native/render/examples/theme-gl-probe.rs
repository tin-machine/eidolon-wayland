//! Linuxの実EGL/GLES readback。Quest/SDK alphaやHMDの成立を代替しない。
use eidolon_model::pixels::PixelBudget;
use eidolon_render::decoration::{Decorations, Draw, Rect, matrix};
use eidolon_theme::{
    raster::{Bundle, RasterBudget, compile},
    style::Transform as CssTransform,
};
use khronos_egl as egl;
use smithay::{
    backend::{
        egl::{
            EGLContext, EGLError, EGLSurface, display::EGLDisplayHandle, ffi,
            native::EGLNativeSurface, wrap_egl_call_ptr,
        },
        renderer::{Bind, Frame, Renderer, TextureFilter, gles::GlesRenderer},
    },
    utils::{Rectangle, Transform},
};
use std::{collections::BTreeMap, ffi::c_void, sync::Arc};

struct Raw {
    egl: egl::DynamicInstance<egl::EGL1_5>,
    display: egl::Display,
    context: egl::Context,
}
impl Drop for Raw {
    fn drop(&mut self) {
        let _ = self.egl.make_current(self.display, None, None, None);
        let _ = self.egl.destroy_context(self.display, self.context);
        // この短命probe processで作成したdisplayのみ。他process/SDKのdisplayに触れない。
        let _ = self.egl.terminate(self.display);
        let _ = self.egl.release_thread();
    }
}
struct Pbuffer;
// SAFETY: 128x64のEGL Pbufferを同じdisplay/configに作り、EGLSurfaceが破棄する。
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
                [egl::WIDTH, 128, egl::HEIGHT, 64, egl::NONE].as_ptr(),
            )
        })
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // EGL_PLATFORM_SURFACELESS_MESA、EGL/eglext.hの値。hostのwindow systemは使わない。
    let egl =
        unsafe { egl::DynamicInstance::<egl::EGL1_5>::load_required_from_filename("libEGL.so.1") }?;
    let display =
        unsafe { egl.get_platform_display(0x31DD, egl::DEFAULT_DISPLAY, &[egl::ATTRIB_NONE]) }?;
    egl.initialize(display)?;
    egl.bind_api(egl::OPENGL_ES_API)?;
    let config = egl
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
                egl::NONE,
            ],
        )?
        .ok_or("missing RGBA8 EGL config")?;
    let context = egl.create_context(
        display,
        config,
        None,
        &[egl::CONTEXT_CLIENT_VERSION, 3, egl::NONE],
    )?;
    let raw = Raw {
        egl,
        display,
        context,
    };
    let context = unsafe {
        EGLContext::from_raw(raw.display.as_ptr(), config.as_ptr(), raw.context.as_ptr())
    }?;
    let mut surface = unsafe {
        EGLSurface::new(
            context.display(),
            context.pixel_format().ok_or("missing EGL pixel format")?,
            context.config_id(),
            Pbuffer,
        )
    }?;
    let mut renderer = unsafe { GlesRenderer::new(context) }?;
    let driver = renderer.with_context(|gl| {
        [0x1F00, 0x1F01, 0x1F02].map(|name| {
            let value = unsafe { gl.GetString(name) };
            if value.is_null() {
                return "unknown".to_owned();
            }
            unsafe { std::ffi::CStr::from_ptr(value.cast()) }
                .to_string_lossy()
                .chars()
                .take(256)
                .collect::<String>()
        })
    })?;
    let mut decorations = Decorations::new(&mut renderer)?;
    let source = Bundle::new([
        ("theme.toml".into(), b"format_version=1\nid='probe'\nname='Probe'\nstyle='style.css'\n[parts.a]\nkind='svg'\nslot='window-backdrop'\nsrc='a.svg'\nsize=[8.0,8.0]".to_vec()),
        ("style.css".into(),b"#a {opacity:1;}".to_vec()),
        ("a.svg".into(),br##"<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="#ff0000" fill-opacity="0.5"/></svg>"##.to_vec()),
    ]).map_err(|e| e.0)?;
    let cpu = PixelBudget::default();
    let quota = RasterBudget::default();
    let theme = compile(&source, &BTreeMap::new(), &cpu, &quota).map_err(|e| e.0)?;
    decorations.prepare(&mut renderer, &theme)?;
    let key = theme.parts["a"].raster.as_ref().unwrap().key;
    // 別assetをGPUへstage後に取り消しても、旧textureは描画可能であることを実readbackする。
    let other_source = Bundle::new([
        ("theme.toml".into(), b"format_version=1\nid='other'\nname='Other'\nstyle='style.css'\n[parts.a]\nkind='svg'\nslot='window-backdrop'\nsrc='a.svg'\nsize=[8.0,8.0]".to_vec()),
        ("style.css".into(),b"#a {opacity:1;}".to_vec()),
        ("a.svg".into(),br##"<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="#0000ff"/></svg>"##.to_vec()),
    ]).map_err(|e| e.0)?;
    let other = compile(&other_source, &BTreeMap::new(), &cpu, &quota).map_err(|e| e.0)?;
    let prior_bytes = decorations.texture_bytes();
    assert!(
        decorations
            .stage(&mut renderer, &other, prior_bytes)
            .is_err()
    );
    assert_eq!(decorations.texture_bytes(), prior_bytes);
    let candidate = decorations.stage(&mut renderer, &other, 32 * 1024 * 1024)?;
    assert!(decorations.staged_bytes(&candidate) > prior_bytes);
    drop(candidate);
    drop(other);
    renderer.cleanup_texture_cache()?;
    assert_eq!(decorations.texture_bytes(), prior_bytes);
    let uploads = decorations.uploads();
    let draws = [
        Draw {
            asset: Some(key),
            matrix: matrix(
                Rect {
                    origin: [16., 16.],
                    size: [32., 16.],
                },
                &[CssTransform::Rotate(45.)],
            )
            .unwrap(),
            opacity: 0.5,
            tint: None,
            fraction: 1.,
        },
        Draw {
            asset: Some(key),
            matrix: matrix(
                Rect {
                    origin: [72., 16.],
                    size: [32., 16.],
                },
                &[],
            )
            .unwrap(),
            opacity: 1.,
            tint: Some([0., 1., 0., 0.5]),
            fraction: 1.,
        },
    ];
    let mut samples = 0;
    for _ in 0..3 {
        decorations.prepare(&mut renderer, &theme)?;
        assert_eq!(
            decorations.uploads(),
            uploads,
            "warm cache must not reupload"
        );
        renderer.upscale_filter(TextureFilter::Linear)?;
        renderer.downscale_filter(TextureFilter::Linear)?;
        let mut target = renderer.bind(&mut surface)?;
        let mut frame = renderer.render(&mut target, (128, 64).into(), Transform::Flipped180)?;
        frame.clear(
            [0., 0., 0., 0.].into(),
            &[Rectangle::from_size((128, 64).into())],
        )?;
        decorations.draw(&mut frame, &draws)?;
        let actual = frame.with_context(|gl| {
            let mut values = vec![];
            for (x, y, expected) in [
                (0, 0, [0, 0, 0, 0]),
                (32, 24, [64, 0, 0, 64]),
                (38, 32, [64, 0, 0, 64]),
                (44, 24, [0, 0, 0, 0]),
                (88, 24, [0, 64, 0, 64]),
            ] {
                let mut rgba = [0u8; 4];
                unsafe {
                    gl.ReadPixels(x, 63 - y, 1, 1, 0x1908, 0x1401, rgba.as_mut_ptr().cast());
                }
                values.push((x, y, rgba, expected));
            }
            (values, unsafe { gl.GetError() })
        })?;
        if actual.1 != 0 {
            return Err(format!("GL error {}", actual.1).into());
        }
        for (x, y, rgba, expected) in actual.0 {
            if !rgba.iter().zip(expected).all(|(a, b)| a.abs_diff(b) <= 1) {
                return Err(format!("readback {x},{y}: {rgba:?} != {expected:?}").into());
            }
            samples += 1;
        }
        let sync = frame.finish()?;
        drop(target);
        renderer.wait(&sync)?;
        renderer.upscale_filter(TextureFilter::Nearest)?;
        renderer.downscale_filter(TextureFilter::Nearest)?;
    }
    let gpu_bytes = decorations.texture_bytes();
    drop(decorations);
    renderer.cleanup_texture_cache()?;
    drop(theme);
    drop(renderer);
    let context = unsafe {
        EGLContext::from_raw(raw.display.as_ptr(), config.as_ptr(), raw.context.as_ptr())
    }?;
    let mut compositor = unsafe { eidolon_render::compositor::Compositor::new(context) }?;
    let composite_samples = composite_probe(&mut compositor, &mut surface, &cpu, &quota)?;
    compositor.clear()?;
    drop(compositor);
    assert_eq!(cpu.used_bytes(), 0);
    assert_eq!(quota.used_bytes(), 0);
    println!(
        "{}",
        serde_json::json!({"status":"pass", "backend":"host-egl", "driver":driver, "readback_samples":samples, "uploads":uploads,
        "texture_bytes":gpu_bytes, "staged_cancel_and_budget_failure_preserved_old":true, "composite_readback_samples":composite_samples, "cpu_bytes_after_drop":0, "quest_verified":false, "hmd_verified":false})
    );
    Ok(())
}

fn composite_probe(
    compositor: &mut eidolon_render::compositor::Compositor,
    surface: &mut EGLSurface,
    cpu: &PixelBudget,
    quota: &RasterBudget,
) -> Result<u64, Box<dyn std::error::Error>> {
    use eidolon_model::{ids::*, window::*};
    use eidolon_render::{compositor::Probe, decoration::Composition};
    use eidolon_theme::{manifest::Slot, player::Player};
    use std::{collections::BTreeSet, time::Duration};
    let source = Bundle::new([
        ("theme.toml".into(), b"format_version=1\nid='composite'\nname='Composite'\nstyle='style.css'\n[parts.back]\nkind='svg'\nslot='window-backdrop'\nsrc='a.svg'\nsize=[56.0,24.0]\n[parts.front]\nkind='meter'\nslot='titlebar-end'\nsize=[4.0,4.0]".to_vec()),
        ("style.css".into(), b"#back {opacity:0.5;} #front {-eidolon-value:1; -eidolon-tint:#00ff00;}".to_vec()),
        ("a.svg".into(), br##"<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="#ff0000" fill-opacity="0.5"/></svg>"##.to_vec()),
    ]).map_err(|e| e.0)?;
    let theme = compile(&source, &BTreeMap::new(), cpu, quota).map_err(|e| e.0)?;
    let mut player = Player::new(Duration::ZERO);
    let frame = player
        .evaluate(&theme, Duration::ZERO, true, false, &BTreeSet::new(), None)
        .map_err(|e| e.0)?;
    let slots = BTreeMap::from([
        (
            Slot::WindowBackdrop,
            Rect {
                origin: [0., 0.],
                size: [64., 32.],
            },
        ),
        (
            Slot::TitlebarEnd,
            Rect {
                origin: [2., 2.],
                size: [4., 4.],
            },
        ),
    ]);
    let composition = Composition {
        theme: &theme,
        frame: &frame,
        canvas: LogicalSize::new(64, 32).unwrap(),
        content_origin: [16, 8],
        slots: &slots,
    };
    let mut pixels = cpu.allocate(32, 16).unwrap();
    for y in 0..16 {
        for x in 0..32 {
            pixels.pixels_mut()[(y * 32 + x) * 4..(y * 32 + x + 1) * 4].copy_from_slice(
                if x < 16 {
                    &[0, 0, 255, 255]
                } else {
                    &[255, 255, 0, 255]
                },
            );
        }
    }
    let mut popup = cpu.allocate(8, 8).unwrap();
    for p in popup.pixels_mut().chunks_exact_mut(4) {
        p.copy_from_slice(&[128, 0, 0, 128]);
    }
    let snapshot = WindowSnapshot {
        session: SessionId::new(1).unwrap(),
        window: WindowId::new(1).unwrap(),
        frame_seq: FrameSeq::new(1).unwrap(),
        geometry: LogicalRect::new(0, 0, 32, 16).unwrap(),
        logical_size: LogicalSize::new(32, 16).unwrap(),
        layers: vec![
            SurfaceLayer {
                surface_id: SurfaceId::new(1).unwrap(),
                content_seq: ContentSeq::new(1).unwrap(),
                offset_logical: LogicalPoint::default(),
                clip_logical: LogicalRect::new(0, 0, 32, 16).unwrap(),
                transform: BufferTransform::Normal,
                scale: 1,
                size_px: BufferSizePx::new(32, 16).unwrap(),
                pixels: Arc::new(pixels),
            },
            SurfaceLayer {
                surface_id: SurfaceId::new(2).unwrap(),
                content_seq: ContentSeq::new(1).unwrap(),
                offset_logical: LogicalPoint { x: 12, y: 4 },
                clip_logical: LogicalRect::new(12, 4, 4, 8).unwrap(),
                transform: BufferTransform::Normal,
                scale: 1,
                size_px: BufferSizePx::new(8, 8).unwrap(),
                pixels: Arc::new(popup),
            },
        ],
    };
    let probes = [
        Probe {
            x: 0,
            y: 63,
            rgba: [0, 0, 0, 0],
        }, // transparent outer canvas
        Probe {
            x: 20,
            y: 12,
            rgba: [64, 0, 0, 64],
        }, // half-alpha SVG, opacity .5
        Probe {
            x: 6,
            y: 6,
            rgba: [0, 255, 0, 255],
        }, // front chrome
        Probe {
            x: 63,
            y: 18,
            rgba: [0, 0, 255, 255],
        }, // Nearest at boundary
        Probe {
            x: 64,
            y: 18,
            rgba: [255, 255, 0, 255],
        },
        Probe {
            x: 60,
            y: 30,
            rgba: [128, 0, 127, 255],
        }, // clipped translucent popup
        Probe {
            x: 66,
            y: 30,
            rgba: [255, 255, 0, 255],
        },
    ];
    for _ in 0..3 {
        compositor.paint_decorated(surface, &snapshot, &probes, Some(&composition))?;
    }
    let report = compositor.report();
    assert_eq!(report.uploads, 2);
    assert_eq!(report.updates, 0);
    assert_eq!(report.decoration_uploads, 2);
    assert_eq!(report.decoration_presents, 3);
    // 無効化後の同じapp imageは再uploadせず、通常letterboxと不透明背景へ戻る。
    compositor.paint(
        surface,
        &snapshot,
        &[Probe {
            x: 0,
            y: 63,
            rgba: [0, 0, 255, 255],
        }],
    )?;
    assert_eq!(compositor.report().decoration_texture_bytes, 0);
    assert_eq!(compositor.report().uploads, 2);
    Ok(report.readback_samples)
}
