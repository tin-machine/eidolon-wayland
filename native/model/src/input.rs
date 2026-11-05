//! SDKのUVとsurface候補。入力regionの評価とWayland event送信はcore ownerが行う。
use crate::{ids::SurfaceId, layout::Viewport, window::WindowSnapshot};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum UvOrigin {
    TopLeft,
    BottomLeft,
}

/// SDK UVを正規化する。右/下は半開区間で、範囲外hitを端へclampしない。
pub fn canonical_uv(u: f64, v: f64, origin: UvOrigin) -> Option<[f64; 2]> {
    if !u.is_finite() || !v.is_finite() || !(0. ..=1.).contains(&u) || !(0. ..=1.).contains(&v) {
        return None;
    }
    let y = match origin {
        UvOrigin::TopLeft => v,
        UvOrigin::BottomLeft => 1. - v,
    };
    (u < 1. && y < 1.).then_some([u, y])
}

pub fn window_at(viewport: &Viewport, u: f64, v: f64, origin: UvOrigin) -> Option<[f64; 2]> {
    let [u, v] = canonical_uv(u, v, origin)?;
    viewport.logical_at(
        u * f64::from(viewport.target.width),
        v * f64::from(viewport.target.height),
    )
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
pub struct SurfaceHit {
    pub surface: SurfaceId,
    pub position: [f64; 2],
}

/// 手前からの候補。snapshotのoffsetはgeometry/popupを処理済みなので一度だけ差し引く。
pub fn surface_hits(
    snapshot: &WindowSnapshot,
    x: f64,
    y: f64,
) -> impl Iterator<Item = SurfaceHit> + '_ {
    let in_canvas = x.is_finite()
        && y.is_finite()
        && x >= 0.
        && y >= 0.
        && x < f64::from(snapshot.logical_size.width)
        && y < f64::from(snapshot.logical_size.height);
    snapshot.layers.iter().rev().filter_map(move |layer| {
        if !in_canvas || !layer.clip_logical.contains(x, y) || !layer.rect().ok()?.contains(x, y) {
            return None;
        }
        Some(SurfaceHit {
            surface: layer.surface_id,
            position: [
                x - f64::from(layer.offset_logical.x),
                y - f64::from(layer.offset_logical.y),
            ],
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        ids::{ContentSeq, FrameSeq, SessionId, WindowId},
        pixels::PixelBudget,
        window::{
            BufferSizePx, BufferTransform, LogicalPoint, LogicalRect, LogicalSize, SurfaceLayer,
        },
    };
    use std::sync::Arc;
    fn layer(
        id: i64,
        offset: [i32; 2],
        clip: LogicalRect,
        size: [u32; 2],
        scale: i32,
        transform: BufferTransform,
    ) -> SurfaceLayer {
        SurfaceLayer {
            surface_id: SurfaceId::new(id).unwrap(),
            content_seq: ContentSeq::new(id).unwrap(),
            offset_logical: LogicalPoint {
                x: offset[0],
                y: offset[1],
            },
            clip_logical: clip,
            transform,
            scale,
            size_px: BufferSizePx::new(size[0], size[1]).unwrap(),
            pixels: Arc::new(PixelBudget::default().allocate(size[0], size[1]).unwrap()),
        }
    }
    fn snapshot(size: LogicalSize, layers: Vec<SurfaceLayer>) -> WindowSnapshot {
        WindowSnapshot {
            session: SessionId::new(1).unwrap(),
            window: WindowId::new(1).unwrap(),
            frame_seq: FrameSeq::new(1).unwrap(),
            geometry: LogicalRect::new(2, 3, size.width, size.height).unwrap(),
            logical_size: size,
            layers,
        }
    }
    #[test]
    fn uv_origin_half_open_edges_invalid_values_and_letterbox_are_not_clamped() {
        let view = Viewport::new(LogicalSize::new(64, 32).unwrap(), 128, 64).unwrap();
        assert_eq!(
            window_at(&view, 0.5, 1., UvOrigin::BottomLeft),
            Some([32., 0.])
        );
        assert_eq!(
            window_at(&view, 0.5, 0., UvOrigin::TopLeft),
            Some([32., 0.])
        );
        for (u, v, origin) in [
            (1., 0.5, UvOrigin::TopLeft),
            (-f64::EPSILON, 0.5, UvOrigin::TopLeft),
            (0.5, 1., UvOrigin::TopLeft),
            (0.5, 0., UvOrigin::BottomLeft),
            (f64::NAN, 0.5, UvOrigin::TopLeft),
            (0.5, f64::INFINITY, UvOrigin::BottomLeft),
            (0.5, -0.1, UvOrigin::BottomLeft),
        ] {
            assert_eq!(window_at(&view, u, v, origin), None);
        }
        let bars = Viewport::new(LogicalSize::new(64, 32).unwrap(), 128, 128).unwrap();
        assert_eq!(window_at(&bars, 0.5, 0.1, UvOrigin::TopLeft), None);
    }
    #[test]
    fn geometry_popup_clip_and_stacking_return_surface_local_coordinates_once() {
        let clip = LogicalRect::new(0, 0, 8, 6).unwrap();
        let state = snapshot(
            clip.size,
            vec![
                layer(1, [-2, -3], clip, [12, 10], 1, BufferTransform::Normal),
                layer(
                    2,
                    [4, 1],
                    LogicalRect::new(4, 1, 4, 3).unwrap(),
                    [6, 6],
                    1,
                    BufferTransform::Normal,
                ),
            ],
        );
        let hits: Vec<_> = surface_hits(&state, 5., 2.).collect();
        assert_eq!(
            hits,
            vec![
                SurfaceHit {
                    surface: SurfaceId::new(2).unwrap(),
                    position: [1., 1.]
                },
                SurfaceHit {
                    surface: SurfaceId::new(1).unwrap(),
                    position: [7., 5.]
                }
            ]
        );
        assert_eq!(
            surface_hits(&state, 7.5, 4.).next().unwrap().position,
            [9.5, 7.]
        );
        assert_eq!(surface_hits(&state, 8., 3.).count(), 0);
        assert_eq!(surface_hits(&state, f64::NAN, 3.).count(), 0);
    }
    #[test]
    fn eight_buffer_transforms_and_scale_do_not_transform_surface_input_twice() {
        use BufferTransform::*;
        let cases = [
            (Normal, [0.2, 0.6]),
            (Rotate90, [0.6, 0.8]),
            (Rotate180, [0.8, 0.4]),
            (Rotate270, [0.4, 0.2]),
            (Flipped, [0.8, 0.6]),
            (Flipped90, [0.6, 0.2]),
            (Flipped180, [0.2, 0.4]),
            (Flipped270, [0.4, 0.8]),
        ];
        for scale in [1, 2] {
            for (transform, expected_uv) in cases {
                let size = [6 * scale as u32, 4 * scale as u32];
                let logical = transform
                    .logical_size(BufferSizePx::new(size[0], size[1]).unwrap(), scale)
                    .unwrap();
                let state = snapshot(
                    logical,
                    vec![layer(
                        1,
                        [0, 0],
                        LogicalRect::new(0, 0, logical.width, logical.height).unwrap(),
                        size,
                        scale,
                        transform,
                    )],
                );
                let view = Viewport::new(logical, 64, 64).unwrap();
                let expected = [
                    f64::from(logical.width) * 0.2,
                    f64::from(logical.height) * 0.6,
                ];
                let uv = [
                    (view.origin[0] + expected[0] * view.scale) / 64.,
                    (view.origin[1] + expected[1] * view.scale) / 64.,
                ];
                let point = window_at(&view, uv[0], uv[1], UvOrigin::TopLeft).unwrap();
                let hit = surface_hits(&state, point[0], point[1]).next().unwrap();
                let buffer = state.layers[0].buffer_uv_at(point[0], point[1]).unwrap();
                for axis in 0..2 {
                    assert!((hit.position[axis] - expected[axis]).abs() < 1e-10);
                    assert!((buffer[axis] - expected_uv[axis]).abs() < 1e-10);
                }
            }
        }
    }
}
