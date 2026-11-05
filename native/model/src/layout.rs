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
