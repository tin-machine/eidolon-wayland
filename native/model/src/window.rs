//! Wayland resource/GLを含まないimmutable window snapshotと座標規約。
use crate::{
    ids::{ContentSeq, FrameSeq, SessionId, SurfaceId, WindowId},
    limits,
    pixels::{OwnedRgba, byte_len},
};
use serde::Serialize;
use std::{sync::Arc, time::Duration};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum GeometryError {
    InvalidSize,
    InvalidScale,
    Overflow,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub struct LogicalSize {
    pub width: u32,
    pub height: u32,
}
impl LogicalSize {
    pub fn new(width: u32, height: u32) -> Result<Self, GeometryError> {
        if width == 0
            || height == 0
            || width > limits::BUFFER_DIMENSION
            || height > limits::BUFFER_DIMENSION
        {
            return Err(GeometryError::InvalidSize);
        }
        Ok(Self { width, height })
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub struct BufferSizePx {
    pub width: u32,
    pub height: u32,
}
impl BufferSizePx {
    pub fn new(width: u32, height: u32) -> Result<Self, GeometryError> {
        byte_len(width, height).map_err(|_| GeometryError::InvalidSize)?;
        Ok(Self { width, height })
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize)]
pub struct LogicalPoint {
    pub x: i32,
    pub y: i32,
}
impl LogicalPoint {
    pub fn checked_add(self, other: Self) -> Result<Self, GeometryError> {
        Ok(Self {
            x: self.x.checked_add(other.x).ok_or(GeometryError::Overflow)?,
            y: self.y.checked_add(other.y).ok_or(GeometryError::Overflow)?,
        })
    }
    pub fn checked_sub(self, other: Self) -> Result<Self, GeometryError> {
        Ok(Self {
            x: self.x.checked_sub(other.x).ok_or(GeometryError::Overflow)?,
            y: self.y.checked_sub(other.y).ok_or(GeometryError::Overflow)?,
        })
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub struct LogicalRect {
    pub origin: LogicalPoint,
    pub size: LogicalSize,
}
impl LogicalRect {
    pub fn new(x: i32, y: i32, width: u32, height: u32) -> Result<Self, GeometryError> {
        let size = LogicalSize::new(width, height)?;
        x.checked_add(width as i32).ok_or(GeometryError::Overflow)?;
        y.checked_add(height as i32)
            .ok_or(GeometryError::Overflow)?;
        Ok(Self {
            origin: LogicalPoint { x, y },
            size,
        })
    }
    pub fn intersection(self, other: Self) -> Option<Self> {
        let x = self.origin.x.max(other.origin.x);
        let y = self.origin.y.max(other.origin.y);
        let right = (i64::from(self.origin.x) + i64::from(self.size.width))
            .min(i64::from(other.origin.x) + i64::from(other.size.width));
        let bottom = (i64::from(self.origin.y) + i64::from(self.size.height))
            .min(i64::from(other.origin.y) + i64::from(other.size.height));
        if right <= i64::from(x) || bottom <= i64::from(y) {
            return None;
        }
        Self::new(
            x,
            y,
            u32::try_from(right - i64::from(x)).ok()?,
            u32::try_from(bottom - i64::from(y)).ok()?,
        )
        .ok()
    }
    pub fn contains(self, x: f64, y: f64) -> bool {
        x.is_finite()
            && y.is_finite()
            && x >= f64::from(self.origin.x)
            && y >= f64::from(self.origin.y)
            && x < f64::from(self.origin.x) + f64::from(self.size.width)
            && y < f64::from(self.origin.y) + f64::from(self.size.height)
    }
}

/// wl_surface.set_buffer_transformの値。描画では適用済みの変換を打ち消す。
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize)]
pub enum BufferTransform {
    #[default]
    Normal,
    Rotate90,
    Rotate180,
    Rotate270,
    Flipped,
    Flipped90,
    Flipped180,
    Flipped270,
}
impl BufferTransform {
    pub fn logical_size(
        self,
        size: BufferSizePx,
        scale: i32,
    ) -> Result<LogicalSize, GeometryError> {
        let scale = u32::try_from(scale)
            .ok()
            .filter(|s| *s > 0)
            .ok_or(GeometryError::InvalidScale)?;
        if !size.width.is_multiple_of(scale) || !size.height.is_multiple_of(scale) {
            return Err(GeometryError::InvalidScale);
        }
        let (width, height) = if matches!(
            self,
            Self::Rotate90 | Self::Rotate270 | Self::Flipped90 | Self::Flipped270
        ) {
            (size.height, size.width)
        } else {
            (size.width, size.height)
        };
        LogicalSize::new(width / scale, height / scale)
    }

    /// 左上原点のsurface UVから所有bufferのUVへ。GL描画と検査で同じ規約を使う。
    pub fn buffer_uv(self, u: f64, v: f64) -> [f64; 2] {
        match self {
            Self::Normal => [u, v],
            Self::Rotate90 => [v, 1.0 - u],
            Self::Rotate180 => [1.0 - u, 1.0 - v],
            Self::Rotate270 => [1.0 - v, u],
            Self::Flipped => [1.0 - u, v],
            Self::Flipped90 => [v, u],
            Self::Flipped180 => [u, 1.0 - v],
            Self::Flipped270 => [1.0 - v, 1.0 - u],
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub enum WindowLife {
    Created,
    AwaitingInitialCommit,
    Configured,
    Mapped,
    Unmapped,
    Destroyed,
}

#[derive(Clone, Debug)]
pub struct SurfaceLayer {
    pub surface_id: SurfaceId,
    pub content_seq: ContentSeq,
    pub offset_logical: LogicalPoint,
    /// window canvas内のclip。popupも親canvasを越えて描かない。
    pub clip_logical: LogicalRect,
    pub transform: BufferTransform,
    pub scale: i32,
    pub size_px: BufferSizePx,
    pub pixels: Arc<OwnedRgba>,
}
impl SurfaceLayer {
    pub fn rect(&self) -> Result<LogicalRect, GeometryError> {
        let size = self.transform.logical_size(self.size_px, self.scale)?;
        LogicalRect::new(
            self.offset_logical.x,
            self.offset_logical.y,
            size.width,
            size.height,
        )
    }

    /// window上の座標からsurface内のbuffer UVへ。clip/offsetは描画とhit testで共用する。
    pub fn buffer_uv_at(&self, x: f64, y: f64) -> Option<[f64; 2]> {
        let rect = self.rect().ok()?;
        if !self.clip_logical.contains(x, y) || !rect.contains(x, y) {
            return None;
        }
        Some(self.transform.buffer_uv(
            (x - f64::from(rect.origin.x)) / f64::from(rect.size.width),
            (y - f64::from(rect.origin.y)) / f64::from(rect.size.height),
        ))
    }
}

/// layerは奥から手前の順。resource/共有pointer/可変pixelを含まない。
#[derive(Clone, Debug)]
pub struct WindowSnapshot {
    pub session: SessionId,
    pub window: WindowId,
    pub frame_seq: FrameSeq,
    pub geometry: LogicalRect,
    pub logical_size: LogicalSize,
    pub layers: Vec<SurfaceLayer>,
}

/// visible targetとrender予算を持つwindowだけ最大60Hzで進める。clockはownerが渡す。
#[derive(Default, Debug)]
pub struct FrameClock {
    next: Option<Duration>,
}
impl FrameClock {
    pub const INTERVAL: Duration = Duration::from_nanos(16_666_667);
    pub fn take(
        &mut self,
        now: Duration,
        mapped: bool,
        visible: bool,
        target_ready: bool,
        budget: bool,
    ) -> bool {
        if !mapped
            || !visible
            || !target_ready
            || !budget
            || self.next.is_some_and(|next| now < next)
        {
            return false;
        }
        let Some(next) = now.checked_add(Self::INTERVAL) else {
            return false;
        };
        self.next = Some(next);
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pixels::PixelBudget;

    #[test]
    fn eight_inverse_buffer_transforms_match_independent_asymmetric_pixel_tables() {
        // bufferはAB/CD/EF。Waylandは適用済みtransformの逆を表示する。
        let cases = [
            (BufferTransform::Normal, "ABCDEF"),
            (BufferTransform::Rotate90, "ECAFDB"),
            (BufferTransform::Rotate180, "FEDCBA"),
            (BufferTransform::Rotate270, "BDFACE"),
            (BufferTransform::Flipped, "BADCFE"),
            (BufferTransform::Flipped90, "ACEBDF"),
            (BufferTransform::Flipped180, "EFCDAB"),
            (BufferTransform::Flipped270, "FDBECA"),
        ];
        for (transform, expected) in cases {
            for scale in [1, 2] {
                let budget = PixelBudget::default();
                let mut pixels = budget.allocate(2 * scale, 3 * scale).unwrap();
                for y in 0..3 * scale {
                    for x in 0..2 * scale {
                        let p = ((y * 2 * scale + x) * 4) as usize;
                        pixels.pixels_mut()[p..p + 4].copy_from_slice(&[
                            b'A' + ((y / scale) * 2 + x / scale) as u8,
                            0,
                            0,
                            255,
                        ]);
                    }
                }
                let size_px = BufferSizePx::new(2 * scale, 3 * scale).unwrap();
                let size = transform.logical_size(size_px, scale as i32).unwrap();
                let layer = SurfaceLayer {
                    surface_id: SurfaceId::new(1).unwrap(),
                    content_seq: ContentSeq::new(1).unwrap(),
                    offset_logical: LogicalPoint::default(),
                    clip_logical: LogicalRect::new(0, 0, size.width, size.height).unwrap(),
                    transform,
                    scale: scale as i32,
                    size_px,
                    pixels: Arc::new(pixels),
                };
                let mut actual = String::new();
                for y in 0..size.height {
                    for x in 0..size.width {
                        let uv = layer
                            .buffer_uv_at(f64::from(x) + 0.5, f64::from(y) + 0.5)
                            .unwrap();
                        let bx = (uv[0] * f64::from(size_px.width)).floor() as u32;
                        let by = (uv[1] * f64::from(size_px.height)).floor() as u32;
                        actual.push(
                            layer.pixels.pixels()[((by * size_px.width + bx) * 4) as usize] as char,
                        );
                    }
                }
                assert_eq!(actual, expected, "{transform:?}/scale={scale}");
            }
        }
    }

    #[test]
    fn invalid_scale_overflow_and_clip_cannot_produce_sampling_coordinates() {
        let size = BufferSizePx::new(3, 2).unwrap();
        for scale in [-1, 0, 2, 4] {
            assert!(BufferTransform::Normal.logical_size(size, scale).is_err());
        }
        assert!(LogicalRect::new(i32::MAX, 0, 2, 2).is_err());
        let rect = LogicalRect::new(-3, -2, 10, 10).unwrap();
        assert_eq!(
            rect.intersection(LogicalRect::new(0, 0, 4, 3).unwrap())
                .unwrap(),
            LogicalRect::new(0, 0, 4, 3).unwrap()
        );
        assert!(!rect.contains(f64::NAN, 0.0));
        assert!(!rect.contains(7.0, 0.0));
    }

    #[test]
    fn scheduler_withholds_callbacks_until_all_visibility_conditions_and_caps_rate() {
        let mut clock = FrameClock::default();
        for (mapped, visible, target, budget) in [
            (false, true, true, true),
            (true, false, true, true),
            (true, true, false, true),
            (true, true, true, false),
        ] {
            assert!(!clock.take(Duration::ZERO, mapped, visible, target, budget));
        }
        assert!(clock.take(Duration::ZERO, true, true, true, true));
        assert!(!clock.take(
            FrameClock::INTERVAL - Duration::from_nanos(1),
            true,
            true,
            true,
            true
        ));
        assert!(clock.take(FrameClock::INTERVAL, true, true, true, true));
        assert!(!clock.take(FrameClock::INTERVAL, true, true, true, true));
        assert!(clock.take(Duration::from_secs(60), true, true, true, true));
        assert!(!clock.take(Duration::from_secs(60), true, true, true, true));
        assert!(!clock.take(Duration::MAX, true, true, true, true));
        assert!(!clock.take(Duration::MAX, true, true, true, true));
    }
}
