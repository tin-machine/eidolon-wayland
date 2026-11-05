//! 描画と入力で共有する、左上原点・縦横比保持のwindow canvas変換。
use crate::{
    surface::validate_dimensions,
    window::{GeometryError, LogicalRect, LogicalSize},
};

#[derive(Clone, Copy, Debug)]
pub struct Viewport {
    pub target: LogicalSize,
    pub logical: LogicalSize,
    pub origin: [f64; 2],
    pub scale: f64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PixelRect {
    pub origin: [i32; 2],
    pub size: [i32; 2],
}

/// appの論理領域と装飾canvasを分離する。app resize/configureのサイズは変更しない。
/// 描画と入力は同じ値を持つこの型を使い、SDKのUVだけを別に拡大しない。
#[derive(Clone, Copy, Debug)]
pub struct ContentViewport {
    canvas: Viewport,
    content: LogicalSize,
    offset: [u32; 2],
}

impl ContentViewport {
    pub fn new(
        content: LogicalSize,
        canvas: LogicalSize,
        offset: [u32; 2],
        target: [i32; 2],
    ) -> Result<Self, GeometryError> {
        let content = LogicalSize::new(content.width, content.height)?;
        if offset[0]
            .checked_add(content.width)
            .is_none_or(|v| v > canvas.width)
            || offset[1]
                .checked_add(content.height)
                .is_none_or(|v| v > canvas.height)
        {
            return Err(GeometryError::InvalidSize);
        }
        Ok(Self {
            canvas: Viewport::new(canvas, target[0], target[1])?,
            content,
            offset,
        })
    }

    /// 透明余白をNoneにする。SDKが背後panelへrayを配送する保証とは別の契約。
    pub fn logical_at(&self, pixel: [f64; 2]) -> Option<[f64; 2]> {
        let [x, y] = self.canvas.logical_at(pixel[0], pixel[1])?;
        let x = x - f64::from(self.offset[0]);
        let y = y - f64::from(self.offset[1]);
        (x >= 0.
            && y >= 0.
            && x < f64::from(self.content.width)
            && y < f64::from(self.content.height))
        .then_some([x, y])
    }

    /// app/popupの連続座標をtargetへ変換する。clipは呼出側のsurface treeで適用する。
    pub fn pixel_at(&self, logical: [f64; 2]) -> Option<[f64; 2]> {
        if !logical.iter().all(|v| v.is_finite()) {
            return None;
        }
        let pixel = std::array::from_fn(|i| {
            self.canvas.origin[i] + (logical[i] + f64::from(self.offset[i])) * self.canvas.scale
        });
        pixel.iter().all(|v| v.is_finite()).then_some(pixel)
    }

    /// 既存appのm/pxを保ったままcanvasを広げる。themeから物理サイズを上書きしない。
    pub fn physical_size(&self, content_metres: [f64; 2]) -> Option<[f64; 2]> {
        let canvas = [self.canvas.logical.width, self.canvas.logical.height];
        let content = [self.content.width, self.content.height];
        let size = std::array::from_fn(|i| {
            content_metres[i] * f64::from(canvas[i]) / f64::from(content[i])
        });
        (content_metres.iter().all(|v| v.is_finite() && *v > 0.)
            && size.iter().all(|v| v.is_finite() && *v > 0.))
        .then_some(size)
    }
}

impl Viewport {
    pub fn new(logical: LogicalSize, width: i32, height: i32) -> Result<Self, GeometryError> {
        validate_dimensions(width, height).map_err(|_| GeometryError::InvalidSize)?;
        let logical = LogicalSize::new(logical.width, logical.height)?;
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
    pub fn pixel_rect(&self, rect: LogicalRect) -> PixelRect {
        let x = (self.origin[0] + f64::from(rect.origin.x) * self.scale).round() as i32;
        let y = (self.origin[1] + f64::from(rect.origin.y) * self.scale).round() as i32;
        let right = (self.origin[0]
            + (f64::from(rect.origin.x) + f64::from(rect.size.width)) * self.scale)
            .round() as i32;
        let bottom = (self.origin[1]
            + (f64::from(rect.origin.y) + f64::from(rect.size.height)) * self.scale)
            .round() as i32;
        PixelRect {
            origin: [x, y],
            size: [right - x, bottom - y],
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn decoration_canvas_preserves_content_scale_and_rejects_margins() {
        let content = LogicalSize::new(1024, 768).unwrap();
        let canvas = LogicalSize::new(1280, 1280).unwrap();
        let view = ContentViewport::new(content, canvas, [128, 256], [1280, 1280]).unwrap();
        assert_eq!(view.pixel_at([0., 0.]), Some([128., 256.]));
        assert_eq!(view.logical_at([640., 640.]), Some([512., 384.]));
        assert_eq!(view.physical_size([1.2, 0.9]), Some([1.5, 1.5]));
        for p in [
            [127., 640.],
            [1152., 640.],
            [640., 255.],
            [640., 1024.],
            [f64::NAN, 500.],
        ] {
            assert_eq!(view.logical_at(p), None);
        }
        // 2倍targetと外側letterboxでも同じapp座標へ戻る。
        let scaled = ContentViewport::new(content, canvas, [128, 256], [2048, 1536]).unwrap();
        for p in [[0., 0.], [1023., 767.], [300.25, 201.75]] {
            let restored = scaled.logical_at(scaled.pixel_at(p).unwrap()).unwrap();
            assert!((restored[0] - p[0]).abs() < 1e-9 && (restored[1] - p[1]).abs() < 1e-9);
        }
        assert!(ContentViewport::new(content, canvas, [u32::MAX, 0], [1280, 1280]).is_err());
        assert!(ContentViewport::new(content, canvas, [257, 0], [1280, 1280]).is_err());
        assert_eq!(view.physical_size([0., 1.]), None);
    }

    #[test]
    fn disabled_decoration_matches_existing_letterbox() {
        let content = LogicalSize::new(1280, 720).unwrap();
        let old = Viewport::new(content, 1024, 768).unwrap();
        let new = ContentViewport::new(content, content, [0, 0], [1024, 768]).unwrap();
        for p in [[0., 0.], [512., 384.], [1023., 767.], [100., 200.]] {
            assert_eq!(old.logical_at(p[0], p[1]), new.logical_at(p));
        }
    }
    #[test]
    fn letterbox_is_shared_by_render_rectangles_and_continuous_input() {
        let view = Viewport::new(LogicalSize::new(64, 32).unwrap(), 128, 128).unwrap();
        assert_eq!(view.origin, [0., 32.]);
        assert_eq!(view.logical_at(64., 64.), Some([32., 16.]));
        assert_eq!(
            view.pixel_rect(LogicalRect::new(-5, 3, 20, 9).unwrap()),
            PixelRect {
                origin: [-10, 38],
                size: [40, 18]
            }
        );
        for point in [
            [0., 31.],
            [0., 96.],
            [128., 64.],
            [-1., 64.],
            [f64::NAN, 0.],
            [0., f64::INFINITY],
        ] {
            assert_eq!(view.logical_at(point[0], point[1]), None);
        }
    }
}
