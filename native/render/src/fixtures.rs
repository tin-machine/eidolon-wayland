//! 固定snapshot/probeだけ。期待値は独立表から作り、renderer自身を正解に使わない。
use crate::compositor::{Probe, Viewport};
use eidolon_model::{
    ids::{ContentSeq, FrameSeq, SurfaceId},
    pixels::PixelBudget,
    surface::TargetKey,
    window::{
        BufferSizePx, BufferTransform as B, LogicalPoint, LogicalRect, LogicalSize, SurfaceLayer,
        WindowSnapshot,
    },
};
use std::sync::Arc;

pub const STAGES: u32 = 18;
fn color(symbol: u8) -> [u8; 4] {
    let value = (symbol - b'A' + 1) * 30;
    [value, 200 - value, value / 2, 255]
}
pub fn make(
    stage: u32,
    sequence: i64,
    key: TargetKey,
    width: i32,
    height: i32,
) -> Result<(Arc<WindowSnapshot>, Vec<Probe>), &'static str> {
    std::hint::black_box("eidolon-render-fixture-code-v1");
    if stage >= STAGES {
        return Err("INVALID_ARGUMENT");
    }
    let budget = PixelBudget::default();
    let content = ContentSeq::new(sequence).map_err(|_| "INVALID_ARGUMENT")?;
    let frame = FrameSeq::new(sequence).map_err(|_| "INVALID_ARGUMENT")?;
    let mut layers = vec![];
    let mut expected = vec![];
    let logical;
    if stage < 16 {
        let (transform, table) = [
            (B::Normal, "ABCDEF"),
            (B::Rotate90, "ECAFDB"),
            (B::Rotate180, "FEDCBA"),
            (B::Rotate270, "BDFACE"),
            (B::Flipped, "BADCFE"),
            (B::Flipped90, "ACEBDF"),
            (B::Flipped180, "EFCDAB"),
            (B::Flipped270, "FDBECA"),
        ][(stage / 2) as usize];
        let scale = stage % 2 + 1;
        let mut pixels = budget
            .allocate(2 * scale, 3 * scale)
            .map_err(|_| "RESOURCE_LIMIT")?;
        for y in 0..3 * scale {
            for x in 0..2 * scale {
                let symbol = b'A' + ((y / scale) * 2 + x / scale) as u8;
                let index = ((y * 2 * scale + x) * 4) as usize;
                pixels.pixels_mut()[index..index + 4].copy_from_slice(&color(symbol));
            }
        }
        let size_px = BufferSizePx::new(2 * scale, 3 * scale).unwrap();
        logical = transform.logical_size(size_px, scale as i32).unwrap();
        layers.push(SurfaceLayer {
            surface_id: SurfaceId::new(1).unwrap(),
            content_seq: content,
            offset_logical: LogicalPoint::default(),
            clip_logical: LogicalRect::new(0, 0, logical.width, logical.height).unwrap(),
            transform,
            scale: scale as i32,
            size_px,
            pixels: Arc::new(pixels),
        });
        expected.extend(table.bytes().map(color));
    } else {
        logical = LogicalSize::new(4, 4).unwrap();
        for (id, size, offset, clip, rgba) in [
            (
                1,
                4,
                (0, 0),
                (0, 0, 4, 4),
                if stage == 16 {
                    [0, 0, 255, 255]
                } else {
                    [0, 255, 255, 255]
                },
            ),
            (2, 2, (1, 1), (1, 1, 1, 1), [128, 0, 0, 128]),
            (3, 2, (0, 0), (0, 0, 1, 1), [0, 64, 0, 64]),
        ] {
            let mut pixels = budget.allocate(size, size).map_err(|_| "RESOURCE_LIMIT")?;
            for pixel in pixels.pixels_mut().chunks_exact_mut(4) {
                pixel.copy_from_slice(&rgba);
            }
            layers.push(SurfaceLayer {
                surface_id: SurfaceId::new(id).unwrap(),
                content_seq: content,
                offset_logical: LogicalPoint {
                    x: offset.0,
                    y: offset.1,
                },
                clip_logical: LogicalRect::new(clip.0, clip.1, clip.2, clip.3).unwrap(),
                transform: B::Normal,
                scale: 1,
                size_px: BufferSizePx::new(size, size).unwrap(),
                pixels: Arc::new(pixels),
            });
        }
        for y in 0..4 {
            for x in 0..4 {
                expected.push(if x == 0 && y == 0 {
                    if stage == 16 {
                        [0, 64, 191, 255]
                    } else {
                        [0, 255, 191, 255]
                    }
                } else if x == 1 && y == 1 {
                    if stage == 16 {
                        [128, 0, 127, 255]
                    } else {
                        [128, 127, 127, 255]
                    }
                } else if stage == 16 {
                    [0, 0, 255, 255]
                } else {
                    [0, 255, 255, 255]
                });
            }
        }
    }
    let viewport = Viewport::new(logical, width, height).map_err(|_| "INVALID_ARGUMENT")?;
    let mut probes = vec![];
    for (index, rgba) in expected.into_iter().enumerate() {
        let x = (index % logical.width as usize) as f64 + 0.5;
        let y = (index / logical.width as usize) as f64 + 0.5;
        probes.push(Probe {
            x: (viewport.origin[0] + x * viewport.scale).floor() as i32,
            y: (viewport.origin[1] + y * viewport.scale).floor() as i32,
            rgba,
        });
    }
    if logical.width != logical.height && width == height {
        probes.push(Probe {
            x: 0,
            y: 0,
            rgba: [0, 0, 0, 255],
        });
        probes.push(Probe {
            x: width - 1,
            y: height - 1,
            rgba: [0, 0, 0, 255],
        });
    }
    Ok((
        Arc::new(WindowSnapshot {
            session: key.session,
            window: key.window,
            frame_seq: frame,
            geometry: LogicalRect::new(0, 0, logical.width, logical.height).unwrap(),
            logical_size: logical,
            layers,
        }),
        probes,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn independent_tables_cover_16_transforms_scale_alpha_stack_clip_and_letterbox() {
        for stage in 0..STAGES {
            let (snapshot, probes) = make(
                stage,
                i64::from(stage + 1),
                TargetKey::from_raw(1, 1, 1, 1).unwrap(),
                64,
                64,
            )
            .unwrap();
            let viewport = Viewport::new(snapshot.logical_size, 64, 64).unwrap();
            for probe in probes {
                assert_eq!(
                    crate::compositor::reference_pixel(
                        &snapshot,
                        &viewport,
                        f64::from(probe.x) + 0.5,
                        f64::from(probe.y) + 0.5
                    ),
                    probe.rgba,
                    "stage={stage} x={} y={}",
                    probe.x,
                    probe.y
                );
            }
        }
    }
}
