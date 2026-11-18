//! B3-only review aids from the same live Registry/SDK renderer. These pixels
//! never replace the B2 observations or become an approved expected image.
use super::{Driver, Result, digest, write_receipt_bytes};
use eidolon_render::HostReadbackRoi;
use serde_json::{Value, json};
use std::path::Path;

const MAX_CONTEXT_BYTES: usize = 4 * 1024 * 1024;
const PHASES: [&str; 4] = [
    "first_off_context",
    "first_off_menu_context",
    "first_on_context",
    "first_on_menu_context",
];

/// Four disjoint, same-ticket tiles cover only the fixture's upper-left
/// 512x256 pixels. Each tile is bounded by the existing 32768-pixel review
/// limit and all four together stay below the renderer's 1-MiB request cap.
/// The full label and nearby text are diagnostic context, not Golden masks.
fn review_rois() -> Vec<HostReadbackRoi> {
    [(0, 0), (256, 0), (0, 128), (256, 128)]
        .into_iter()
        .enumerate()
        .map(|(index, (x, y))| HostReadbackRoi {
            id: 101 + index as u32,
            x,
            y,
            width: 256,
            height: 128,
        })
        .collect()
}

fn context_receipt(native: &Value, states: &Value) -> Value {
    let complete = native["error"].is_null()
        && native["cleanup_error"].is_null()
        && states.as_object().is_some_and(|v| {
            v.len() == PHASES.len() && PHASES.iter().all(|key| v.contains_key(*key))
        });
    json!({
        "schema":1,"revision":"eidolon-i6-n07-b3-review-context-v1",
        "source":"host_renderer_review_context",
        "run_id":native["run_id"],"runtime":native["runtime"],
        "owner_id":native["owner_id"],"source_sha256":native["source_sha256"],
        "capture_id":native["n07_probe"]["capture_id"],
        "target":native["n07_probe"]["main_target"],
        "complete":complete,"states":states,
        "error":native["error"],"cleanup_error":native["cleanup_error"],
    })
}

fn encoded_context(native: &Value, states: &Value) -> Result<(Vec<u8>, Value)> {
    let value = context_receipt(native, states);
    let bytes = serde_json::to_vec(&value).map_err(|_| "I6_N07_REVIEW_CONTEXT_INVALID")?;
    if bytes.len() > MAX_CONTEXT_BYTES {
        return Err("I6_N07_REVIEW_CONTEXT_OVERFLOW");
    }
    let acquired: Vec<_> = PHASES
        .iter()
        .filter(|key| states.get(**key).is_some())
        .copied()
        .collect();
    let binding = json!({
        "schema":1,"sha256":digest(&bytes),"bytes":bytes.len(),
        "states":acquired,"complete":value["complete"],
    });
    Ok((bytes, binding))
}

impl Driver {
    pub(super) fn n07_capture_review_context(&mut self, target: &Value, phase: &str) -> Result<()> {
        if self.n07_review_context.is_none() {
            return Ok(());
        }
        if !PHASES.contains(&phase)
            || self
                .n07_review_context
                .as_ref()
                .unwrap()
                .get(phase)
                .is_some()
        {
            return Err("I6_N07_REVIEW_CONTEXT_INVALID");
        }
        // Normal input, lifecycle and GPU ownership remain unchanged. The
        // ordinary SDK API selects its current Create lease and frame fence.
        let stage = self.n07_capture_with_rois(target, phase, Some(review_rois()))?;
        self.n07_review_context.as_mut().unwrap()[phase] = stage;
        Ok(())
    }

    pub(super) fn write_n07_review_context(&self, path: &Path, native: &Value) -> Result<Value> {
        let states = self
            .n07_review_context
            .as_ref()
            .ok_or("I6_N07_REVIEW_CONTEXT_INVALID")?;
        let (bytes, binding) = encoded_context(native, states)?;
        // Digest binds these exact serialized bytes, not a later image/JSON
        // transform. Partial failures are retained with complete=false.
        write_receipt_bytes(path, &bytes)?;
        Ok(binding)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn native() -> Value {
        json!({
            "run_id":"fixture-run","runtime":"4","owner_id":"fixture-owner",
            "source_sha256":"fixture-source","error":null,"cleanup_error":null,
            "n07_probe":{"capture_id":"fixture-capture","main_target":{
                "session":"1","window":"2","scene":"3","generation":"4"
            }}
        })
    }

    #[test]
    fn review_tiles_are_bounded_nonoverlapping_and_include_menu_context() {
        let rois = review_rois();
        assert_eq!(rois.len(), 4);
        assert_eq!(
            rois.iter().map(|r| r.width * r.height * 4).sum::<u32>(),
            524288
        );
        for (i, a) in rois.iter().enumerate() {
            assert_eq!(a.id, 101 + i as u32);
            assert!(a.width * a.height <= 32768);
            assert!(a.x + a.width <= 512 && a.y + a.height <= 256);
            for b in &rois[i + 1..] {
                assert!(
                    a.x + a.width <= b.x
                        || b.x + b.width <= a.x
                        || a.y + a.height <= b.y
                        || b.y + b.height <= a.y
                );
            }
        }
    }

    #[test]
    fn sidecar_digest_binds_pixels_identity_and_partial_failure() {
        let native = native();
        let mut states = json!({});
        for phase in PHASES {
            states[phase] = json!({"phase":phase,"readback":{"pixels":"fixture-only"}});
        }
        let (bytes, binding) = encoded_context(&native, &states).unwrap();
        assert_eq!(binding["sha256"], digest(&bytes));
        assert_eq!(binding["bytes"], bytes.len());
        assert_eq!(binding["complete"], true);
        assert!(!String::from_utf8(bytes).unwrap().contains("second_on"));
        let mut changed = native.clone();
        changed["n07_probe"]["capture_id"] = json!("foreign");
        assert_ne!(
            encoded_context(&changed, &states).unwrap().1["sha256"],
            binding["sha256"]
        );
        changed = native.clone();
        changed["error"] = json!("I6_N07_FAILED");
        assert_eq!(
            encoded_context(&changed, &states).unwrap().1["complete"],
            false
        );
        changed = native;
        changed["cleanup_error"] = json!("I6_CLEANUP_FAILED");
        assert_eq!(
            encoded_context(&changed, &states).unwrap().1["complete"],
            false
        );
        states.as_object_mut().unwrap().remove(PHASES[3]);
        assert_eq!(
            encoded_context(&changed, &states).unwrap().1["states"]
                .as_array()
                .unwrap()
                .len(),
            3
        );
    }

    #[test]
    fn partial_and_oversized_context_cannot_be_complete_success() {
        let native = native();
        assert_eq!(
            encoded_context(&native, &json!({})).unwrap().1["complete"],
            false
        );
        let large = json!({"first_off_context":"x".repeat(MAX_CONTEXT_BYTES)});
        assert!(matches!(
            encoded_context(&native, &large),
            Err("I6_N07_REVIEW_CONTEXT_OVERFLOW")
        ));
    }
}
