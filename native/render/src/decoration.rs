//! GL ownerだけが使用する装飾texture/shader。CPU compilerやSDK threadへGLを公開しない。
use cgmath::{Deg, Matrix3, SquareMatrix, Vector2};
use eidolon_model::{layout::ContentViewport, window::LogicalSize};
use eidolon_theme::{
    manifest::{Kind, Slot},
    player::Frame as ThemeFrame,
    raster::{CompiledTheme, MAX_RASTER_BYTES},
    style::{Property, Transform, Value},
};
use smithay::backend::{
    allocator::Fourcc,
    renderer::{
        ImportMem, Renderer,
        gles::{
            GlesError, GlesFrame, GlesRenderer, GlesTexProgram, GlesTexture, Uniform, UniformName,
            UniformType,
        },
    },
};
use std::collections::BTreeMap;

/// CPU済みsnapshot。slot/canvasはhostが決め、テーマからappサイズを変更しない。
pub struct Composition<'a> {
    pub theme: &'a CompiledTheme,
    pub frame: &'a ThemeFrame,
    pub canvas: LogicalSize,
    pub content_origin: [u32; 2],
    pub slots: &'a BTreeMap<Slot, Rect>,
}
impl Composition<'_> {
    pub fn viewport(
        &self,
        content: LogicalSize,
        target: [i32; 2],
    ) -> Result<ContentViewport, &'static str> {
        ContentViewport::new(content, self.canvas, self.content_origin, target)
            .map_err(|_| "THEME_LAYOUT_INVALID")
    }
    pub fn draws(
        &self,
        viewport: ContentViewport,
        backdrop: bool,
    ) -> Result<Vec<Draw>, &'static str> {
        let canvas = viewport.canvas_viewport();
        let outer = Matrix3::from_translation(Vector2::new(
            canvas.origin[0] as f32,
            canvas.origin[1] as f32,
        )) * Matrix3::from_scale(canvas.scale as f32);
        let mut parts = draws(self.theme, self.frame, self.slots, backdrop)?;
        for part in &mut parts {
            part.matrix = outer * part.matrix;
        }
        Ok(parts)
    }
}

type AssetKey = ([u8; 32], [u32; 2]);

fn gpu_error(error: GlesError) -> &'static str {
    match smithay::backend::SwapBuffersError::from(error) {
        smithay::backend::SwapBuffersError::ContextLost(_) => "EGL_CONTEXT_LOST",
        _ => "THEME_GPU_FAILED",
    }
}

#[derive(Clone, Copy, Debug)]
pub struct Rect {
    pub origin: [f32; 2],
    pub size: [f32; 2],
}

#[derive(Clone, Debug)]
pub struct Draw {
    pub asset: Option<AssetKey>,
    pub matrix: Matrix3<f32>,
    pub opacity: f32,
    pub tint: Option<[f32; 4]>,
    /// meterはunit quadの右端だけを縮め、part中心のtransformは変えない。
    pub fraction: f32,
}

/// 左上原点のpixel座標。Smithayがprojectionを掛けるのでNDCへ二重変換しない。
pub fn matrix(rect: Rect, transforms: &[Transform]) -> Option<Matrix3<f32>> {
    if rect
        .origin
        .iter()
        .chain(rect.size.iter())
        .any(|v| !v.is_finite())
        || rect.size.iter().any(|v| *v <= 0.)
    {
        return None;
    }
    let mut m = Matrix3::from_translation(Vector2::new(
        rect.origin[0] + rect.size[0] / 2.,
        rect.origin[1] + rect.size[1] / 2.,
    ));
    for op in transforms {
        m = m * match op {
            Transform::Translate(x, y) => {
                Matrix3::from_translation(Vector2::new(*x as f32, *y as f32))
            }
            Transform::Rotate(degrees) => {
                Matrix3::from_angle_z(Deg(degrees.rem_euclid(360.) as f32))
            }
            Transform::Scale(x, y) => Matrix3::from_nonuniform_scale(*x as f32, *y as f32),
        };
    }
    m = m
        * Matrix3::from_nonuniform_scale(rect.size[0], rect.size[1])
        * Matrix3::from_translation(Vector2::new(-0.5, -0.5));
    let elements: &[f32; 9] = m.as_ref();
    elements.iter().all(|v| v.is_finite()).then_some(m)
}

/// hostが決めたslot矩形を使う。canvas/targetへの外側変換は同じmatrixへ左から掛ける。
pub fn draws(
    theme: &CompiledTheme,
    frame: &ThemeFrame,
    slots: &BTreeMap<Slot, Rect>,
    backdrop: bool,
) -> Result<Vec<Draw>, &'static str> {
    let mut result = vec![];
    for (id, definition) in theme.manifest.ordered_parts() {
        if (definition.slot == Slot::WindowBackdrop) != backdrop {
            continue;
        }
        let Some(slot) = slots.get(&definition.slot) else {
            continue;
        }; // chrome帯なしなら非表示。
        let values = frame.parts.get(id).ok_or("THEME_FRAME_PART_MISSING")?;
        let opacity = match values.get(&Property::Opacity) {
            Some(Value::Number(v)) => *v as f32,
            None => 1.,
            _ => return Err("THEME_VALUE_TYPE"),
        };
        let tint = match values.get(&Property::Tint) {
            Some(Value::Color(c)) => c.map(|v| v as f32),
            None => [1.; 4],
            _ => return Err("THEME_VALUE_TYPE"),
        };
        let transforms = match values.get(&Property::Transform) {
            Some(Value::Transform(v)) => v.as_slice(),
            None => &[],
            _ => return Err("THEME_VALUE_TYPE"),
        };
        let fraction = if definition.kind == Kind::Meter {
            match values.get(&Property::Value) {
                Some(Value::Number(v)) => *v as f32,
                None => continue,
                _ => return Err("THEME_VALUE_TYPE"),
            }
        } else {
            1.
        };
        if !opacity.is_finite()
            || !(0. ..=1.).contains(&opacity)
            || !fraction.is_finite()
            || !(0. ..=1.).contains(&fraction)
            || tint
                .iter()
                .any(|v| !v.is_finite() || !(0. ..=1.).contains(v))
        {
            return Err("THEME_VALUE_RANGE");
        }
        if opacity == 0. || fraction == 0. {
            continue;
        }
        let size = definition
            .size
            .map(|v| v.map(|v| v as f32))
            .unwrap_or(slot.size);
        let rect = Rect {
            origin: std::array::from_fn(|i| slot.origin[i] + (slot.size[i] - size[i]) / 2.),
            size,
        };
        let matrix = matrix(rect, transforms).ok_or("THEME_MATRIX_RANGE")?;
        result.push(Draw {
            asset: theme.parts[id].raster.as_ref().map(|r| r.key),
            matrix,
            opacity,
            tint: (definition.kind != Kind::Svg).then_some(tint),
            fraction,
        });
    }
    Ok(result)
}

#[derive(Clone)]
struct Cached {
    texture: GlesTexture,
    bytes: usize,
}

pub struct Decorations {
    // shader/textureはrendererより先にdropする。
    cache: BTreeMap<AssetKey, Cached>,
    white: GlesTexture,
    mask_shader: GlesTexProgram,
    uploads: u64,
}

impl Decorations {
    pub fn new(renderer: &mut GlesRenderer) -> Result<Self, GlesError> {
        let mask_shader = renderer.compile_custom_texture_shader(
            include_str!("decoration-mask.frag"),
            &[UniformName::new("part_color", UniformType::_4f)],
        )?;
        let white = renderer.import_memory(&[255; 4], Fourcc::Abgr8888, (1, 1).into(), false)?;
        Ok(Self {
            cache: BTreeMap::new(),
            white,
            mask_shader,
            uploads: 1,
        })
    }
    pub fn uploads(&self) -> u64 {
        self.uploads
    }
    pub fn texture_bytes(&self) -> usize {
        4 + self.cache.values().map(|v| v.bytes).sum::<usize>()
    }

    /// 全upload成功後だけcacheを置換する。旧/新の重複GPU容量も上限に含める。
    pub fn prepare(
        &mut self,
        renderer: &mut GlesRenderer,
        theme: &CompiledTheme,
    ) -> Result<(), &'static str> {
        self.prepare_with_limit(renderer, theme, MAX_RASTER_BYTES)
    }

    pub fn prepare_with_limit(
        &mut self,
        renderer: &mut GlesRenderer,
        theme: &CompiledTheme,
        limit: usize,
    ) -> Result<(), &'static str> {
        let result = self.prepare_inner(renderer, theme, limit.min(MAX_RASTER_BYTES));
        // texture Dropは削除通知。候補失敗時も次の準備前にowner上で実allocationを回収する。
        let cleanup = renderer.cleanup_texture_cache().map_err(gpu_error);
        if cleanup == Err("EGL_CONTEXT_LOST") {
            return cleanup;
        }
        result.and(cleanup)
    }

    fn prepare_inner(
        &mut self,
        renderer: &mut GlesRenderer,
        theme: &CompiledTheme,
        limit: usize,
    ) -> Result<(), &'static str> {
        let mut next = BTreeMap::new();
        let mut bytes = self.texture_bytes();
        if bytes > limit {
            return Err("THEME_GPU_BUDGET");
        }
        for part in theme.parts.values() {
            let Some(raster) = &part.raster else {
                continue;
            };
            if next.contains_key(&raster.key) {
                continue;
            }
            let cached = if let Some(cached) = self.cache.get(&raster.key) {
                cached.clone()
            } else {
                let pixels = raster.pixels.image();
                let size = pixels.pixels().len();
                bytes = bytes
                    .checked_add(size)
                    .filter(|b| *b <= limit)
                    .ok_or("THEME_GPU_BUDGET")?;
                let texture = renderer
                    .import_memory(
                        pixels.pixels(),
                        Fourcc::Abgr8888,
                        (pixels.width_px() as i32, pixels.height_px() as i32).into(),
                        false,
                    )
                    .map_err(gpu_error)?;
                self.uploads += 1;
                Cached {
                    texture,
                    bytes: size,
                }
            };
            next.insert(raster.key, cached);
        }
        self.cache = next;
        Ok(())
    }

    /// pass開始前にcallerがSmithayのfilter APIでLinearを設定し、app pass前にNearestへ戻す。
    /// raw GL stateは変更しない。context loss時はDecorations全体をowner上で作り直す。
    pub fn draw(&self, frame: &mut GlesFrame<'_, '_>, draws: &[Draw]) -> Result<(), &'static str> {
        for draw in draws {
            let texture = match draw.asset {
                Some(key) => &self.cache.get(&key).ok_or("THEME_TEXTURE_MISSING")?.texture,
                None => &self.white,
            };
            let uniforms = draw
                .tint
                .map(|c| [Uniform::new("part_color", (c[0], c[1], c[2], c[3]))]);
            frame
                .render_texture(
                    texture,
                    Matrix3::identity(),
                    draw.matrix,
                    Some([0., 0., draw.fraction, 1.]),
                    draw.opacity,
                    draw.tint.map(|_| &self.mask_shader),
                    uniforms.as_ref().map(|v| v.as_slice()).unwrap_or(&[]),
                )
                .map_err(gpu_error)?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use cgmath::Vector3;
    #[test]
    fn arbitrary_rotation_preserves_centre_and_positive_angles_turn_clockwise_in_pixels() {
        let rect = Rect {
            origin: [100., 200.],
            size: [40., 20.],
        };
        let matrix = matrix(rect, &[Transform::Rotate(45.)]).unwrap();
        let centre = matrix * Vector3::new(0.5, 0.5, 1.);
        assert!((centre.x - 120.).abs() < 0.001 && (centre.y - 210.).abs() < 0.001);
        let right = matrix * Vector3::new(1., 0.5, 1.);
        assert!((right.x - (120. + 20. / 2f32.sqrt())).abs() < 0.001);
        assert!((right.y - (210. + 20. / 2f32.sqrt())).abs() < 0.001);
    }
    #[test]
    fn translation_and_scale_keep_css_function_order() {
        let rect = Rect {
            origin: [0., 0.],
            size: [10., 10.],
        };
        let m = matrix(
            rect,
            &[Transform::Translate(2., 3.), Transform::Scale(2., 1.)],
        )
        .unwrap();
        let centre = m * Vector3::new(0.5, 0.5, 1.);
        assert_eq!(centre, Vector3::new(7., 8., 1.));
        assert!(matrix(rect, &[Transform::Rotate(f64::NAN)]).is_none());
    }
}
