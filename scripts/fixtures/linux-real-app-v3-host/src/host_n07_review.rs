//! B3-only review aids from the same live Registry/SDK renderer. These pixels
//! never replace the B2 observations or become an approved expected image.
use super::{
    Driver, Result, digest,
    host_n07_b2::{popup, snapshot_content_frame},
    write_receipt_bytes,
};
use eidolon_render::HostReadbackRoi;
use serde_json::{Value, json};
use std::{
    path::Path,
    thread,
    time::{Duration, Instant},
};

const MAX_CONTEXT_BYTES: usize = 4 * 1024 * 1024;
const SETTLE_BUDGET: Duration = Duration::from_secs(12);
const SETTLE_QUIET: Duration = Duration::from_millis(200);
const MAX_SETTLE_SAMPLES: usize = 32;
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
pub(super) fn review_rois() -> Vec<HostReadbackRoi> {
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

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct MainContent {
    frame: u64,
    sequence: u64,
    only_main: bool,
}

fn decimal(value: &Value) -> Result<u64> {
    let raw = value
        .as_str()
        .ok_or("I6_N07_REVIEW_MAIN_SEQUENCE_INVALID")?;
    if raw.is_empty() || raw.starts_with('0') || !raw.bytes().all(|b| b.is_ascii_digit()) {
        return Err("I6_N07_REVIEW_MAIN_SEQUENCE_INVALID");
    }
    raw.parse()
        .map_err(|_| "I6_N07_REVIEW_MAIN_SEQUENCE_INVALID")
}

fn main_layer<'a>(stage: &'a Value, parent: &Value) -> Result<&'a Value> {
    decimal(parent)?;
    let layers = stage["readback"]["receipt"]["layers"]
        .as_array()
        .ok_or("I6_N07_REVIEW_MAIN_LAYER_INVALID")?;
    let mut found = layers.iter().filter(|layer| layer["surface"] == *parent);
    let layer = found.next().ok_or("I6_N07_REVIEW_MAIN_LAYER_INVALID")?;
    if found.next().is_some() {
        return Err("I6_N07_REVIEW_MAIN_LAYER_INVALID");
    }
    Ok(layer)
}

fn main_content(stage: &Value, parent: &Value) -> Result<MainContent> {
    let receipt = &stage["readback"]["receipt"];
    Ok(MainContent {
        frame: decimal(&receipt["content_frame"])?,
        sequence: decimal(&main_layer(stage, parent)?["content_seq"])?,
        // This pinned Mousepad fixture has one main layer; waiting for it
        // also excludes stale popup layers from an in-flight composition.
        only_main: receipt["layers"]
            .as_array()
            .is_some_and(|layers| layers.len() == 1),
    })
}

fn same_main_capture(baseline: &Value, current: &Value, parent: &Value) -> Result<()> {
    let a = &baseline["readback"];
    let b = &current["readback"];
    for key in ["source", "target", "create_seq", "sdk_adapter"] {
        if a[key].is_null() || a[key] != b[key] {
            return Err("I6_N07_REVIEW_MAIN_IDENTITY_CHANGED");
        }
    }
    for key in [
        "target",
        "runtime",
        "create",
        "renderer_owner",
        "context_epoch",
        "geometry",
        "logical_size",
        "content_rect",
    ] {
        if a["receipt"][key].is_null() || a["receipt"][key] != b["receipt"][key] {
            return Err("I6_N07_REVIEW_MAIN_IDENTITY_CHANGED");
        }
    }
    // max_swap_us is a dynamic measurement. It cannot be compared as part
    // of the fixed EGL identity or used as a content-settling condition.
    for key in [
        "vendor",
        "renderer",
        "version",
        "config_id",
        "width",
        "height",
        "rgba_bits",
        "readback",
    ] {
        if a["receipt"]["backend"][key].is_null()
            || a["receipt"]["backend"][key] != b["receipt"]["backend"][key]
        {
            return Err("I6_N07_REVIEW_MAIN_IDENTITY_CHANGED");
        }
    }
    let old_layer = main_layer(baseline, parent)?;
    let new_layer = main_layer(current, parent)?;
    for key in [
        "surface",
        "buffer_size",
        "offset",
        "clip",
        "scale",
        "transform",
    ] {
        if old_layer[key].is_null() || old_layer[key] != new_layer[key] {
            return Err("I6_N07_REVIEW_MAIN_IDENTITY_CHANGED");
        }
    }
    let old = main_content(baseline, parent)?;
    let new = main_content(current, parent)?;
    if new.frame < old.frame || new.sequence < old.sequence {
        return Err("I6_N07_REVIEW_MAIN_CONTENT_REGRESSION");
    }
    Ok(())
}

/// Quiet time only stages a diagnostic readback; main-buffer progress is the
/// required causal evidence. Neither property certifies a Line Numbers glyph.
#[derive(Default)]
struct QuietMainContent {
    last: Option<MainContent>,
    since: Duration,
}
impl QuietMainContent {
    fn observe(&mut self, old: MainContent, now: MainContent, elapsed: Duration) -> bool {
        if !now.only_main || now.frame <= old.frame || now.sequence <= old.sequence {
            self.last = None;
            return false;
        }
        if self.last != Some(now) {
            self.last = Some(now);
            self.since = elapsed;
            return false;
        }
        elapsed.saturating_sub(self.since) >= SETTLE_QUIET
    }
}

impl Driver {
    pub(super) fn n07_capture_settled_post_popup(
        &mut self,
        target: &Value,
        epoch: u64,
        baseline: &Value,
        parent: &Value,
        rois: Vec<HostReadbackRoi>,
    ) -> Result<Value> {
        self.n07_capture_settled_main(target, epoch, baseline, parent, rois, false)
    }

    pub(super) fn n07_capture_settled_post_open(
        &mut self,
        target: &Value,
        epoch: u64,
        baseline: &Value,
        parent: &Value,
        rois: Vec<HostReadbackRoi>,
    ) -> Result<Value> {
        self.n07_capture_settled_main(target, epoch, baseline, parent, rois, true)
    }

    fn n07_settling_record(&mut self, second: bool) -> Result<&mut Value> {
        if second {
            self.n07_second
                .as_mut()
                .ok_or("I6_N07_REVIEW_CONTEXT_INVALID")
        } else {
            self.n07_probe
                .as_mut()
                .map(|probe| &mut probe["b2"])
                .ok_or("I6_N07_REVIEW_CONTEXT_INVALID")
        }
    }

    fn n07_capture_settled_main(
        &mut self,
        target: &Value,
        epoch: u64,
        baseline: &Value,
        parent: &Value,
        rois: Vec<HostReadbackRoi>,
        second: bool,
    ) -> Result<Value> {
        let phase = if second {
            "second_on_numbers_1_8"
        } else {
            "first_on_gutter"
        };
        let proof_name = if second {
            "post_open_settling"
        } else {
            "post_popup_settling"
        };
        let old = main_content(baseline, parent)?;
        let start = Instant::now();
        let mut quiet = QuietMainContent::default();
        let mut sampled = None;
        let mut last = None;
        let mut samples = Vec::new();
        self.n07_settling_record(second)?[proof_name] = json!({
            "schema":1,"source":"normal_sdk_renderer_main_content_sequence",
            "parent_surface":parent,"baseline_content_frame":old.frame.to_string(),
            "baseline_main_content_seq":old.sequence.to_string(),
            "quiet_ms":SETTLE_QUIET.as_millis() as u64,
            "budget_ms":SETTLE_BUDGET.as_millis() as u64,
            "status":"pending","samples":[],
        });
        loop {
            let snapshot = self.pump()?;
            if snapshot["runtime"]["input_resets"] != epoch {
                return Err("I6_N07_REVIEW_INPUT_EPOCH_CHANGED");
            }
            if popup(&snapshot, target).is_some() {
                return Err("I6_N07_REVIEW_POPUP_REAPPEARED");
            }
            if start.elapsed() >= SETTLE_BUDGET {
                return Err("I6_N07_REVIEW_MAIN_SETTLING_TIMEOUT");
            }
            let frame = snapshot_content_frame(&snapshot, target)
                .ok_or("I6_N07_REVIEW_MAIN_SEQUENCE_INVALID")?;
            // Repeat readback only for new actual content, or to confirm the
            // quiet interval. A static stale buffer is not repeatedly polled
            // into a synthetic newer frame or a successful receipt.
            let confirm = last.is_some_and(|mark| quiet.observe(old, mark, start.elapsed()));
            if sampled != Some(frame) || confirm {
                if samples.len() >= MAX_SETTLE_SAMPLES {
                    return Err("I6_N07_REVIEW_MAIN_SETTLING_SAMPLE_LIMIT");
                }
                let stage = self.n07_capture_with_rois(target, phase, Some(rois.clone()))?;
                // A timeout/identity failure retains the most recent actual
                // pixels beside pending settling metadata and normal cleanup.
                self.n07_settling_record(second)?[phase] = stage.clone();
                same_main_capture(baseline, &stage, parent)?;
                let mark = main_content(&stage, parent)?;
                if last.is_some_and(|previous: MainContent| {
                    mark.frame < previous.frame || mark.sequence < previous.sequence
                }) {
                    return Err("I6_N07_REVIEW_MAIN_CONTENT_REGRESSION");
                }
                let elapsed = start.elapsed();
                samples.push(json!({
                    "content_frame":mark.frame.to_string(),
                    "main_content_seq":mark.sequence.to_string(),
                    "request_seq":stage["readback"]["receipt"]["request_seq"],
                    "elapsed_ms":elapsed.as_millis() as u64,
                }));
                self.n07_settling_record(second)?[proof_name]["samples"] = json!(samples);
                sampled = Some(mark.frame);
                last = Some(mark);
                if elapsed >= SETTLE_BUDGET {
                    return Err("I6_N07_REVIEW_MAIN_SETTLING_TIMEOUT");
                }
                if quiet.observe(old, mark, elapsed) {
                    let proof = &mut self.n07_settling_record(second)?[proof_name];
                    proof["status"] = json!("settled");
                    proof["content_frame"] = json!(mark.frame.to_string());
                    proof["main_content_seq"] = json!(mark.sequence.to_string());
                    return Ok(stage);
                }
            }
            thread::sleep(Duration::from_millis(50));
        }
    }

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

    fn stage(frame: u64, sequence: u64, popup: bool) -> Value {
        let target = json!({"session":"1","window":"1","scene":"1","generation":"1"});
        let mut layers = vec![json!({
            "surface":"3","content_seq":sequence.to_string(),
            "buffer_size":{"width":1076,"height":820},
            "offset":{"x":-26,"y":-23},
            "clip":{"origin":{"x":0,"y":0},"size":{"width":1024,"height":768}},
            "scale":1,"transform":"Normal",
        })];
        if popup {
            layers.push(json!({"surface":"4","content_seq":"8"}));
        }
        json!({"phase":"fixture_only","readback":{
            "source":"host_renderer_readback","target":target,
            "create_seq":"1","sdk_adapter":"1",
            "receipt":{
                "target":target,"runtime":"1","create":"1",
                "renderer_owner":1,"context_epoch":1,
                "content_frame":frame.to_string(),"layers":layers,
                "geometry":{"origin":{"x":0,"y":0},"size":{"width":1024,"height":768}},
                "logical_size":{"width":1024,"height":768},
                "content_rect":{"origin":[0,0],"size":[1024,768]},
                "backend":{"vendor":"fixture","renderer":"fixture","version":"fixture",
                    "config_id":1,"width":1024,"height":768,"rgba_bits":[8,8,8,8],
                    "readback":true,"max_swap_us":1},
            },
        }})
    }

    #[test]
    fn popup_removal_frame_without_new_main_buffer_cannot_settle() {
        let parent = json!("3");
        let old = main_content(&stage(8, 9, true), &parent).unwrap();
        let removed = main_content(&stage(11, 9, false), &parent).unwrap();
        let fresh = main_content(&stage(12, 13, false), &parent).unwrap();
        let mut quiet = QuietMainContent::default();
        assert!(!quiet.observe(old, removed, Duration::ZERO));
        assert!(!quiet.observe(old, removed, Duration::from_secs(11)));
        assert!(!quiet.observe(old, fresh, Duration::from_millis(11000)));
        assert!(!quiet.observe(old, fresh, Duration::from_millis(11199)));
        assert!(quiet.observe(old, fresh, Duration::from_millis(11200)));
    }

    #[test]
    fn new_content_or_popup_resets_the_observed_quiet_interval() {
        let old = MainContent {
            frame: 3,
            sequence: 4,
            only_main: false,
        };
        let first = MainContent {
            frame: 4,
            sequence: 5,
            only_main: true,
        };
        let second = MainContent {
            frame: 5,
            sequence: 6,
            only_main: true,
        };
        let mut quiet = QuietMainContent::default();
        assert!(!quiet.observe(old, first, Duration::ZERO));
        assert!(!quiet.observe(old, second, SETTLE_QUIET));
        assert!(quiet.observe(old, second, SETTLE_QUIET * 2));
        let popup = MainContent {
            only_main: false,
            ..second
        };
        assert!(!quiet.observe(old, popup, SETTLE_QUIET * 3));
        assert!(!quiet.observe(old, second, SETTLE_QUIET * 4));
        assert!(quiet.observe(old, second, SETTLE_QUIET * 5));
    }

    #[test]
    fn settling_preserves_main_capture_identity_but_not_dynamic_swap_timing() {
        let parent = json!("3");
        let baseline = stage(8, 9, true);
        let current = stage(12, 13, false);
        assert!(same_main_capture(&baseline, &current, &parent).is_ok());
        let mut dynamic = current.clone();
        dynamic["readback"]["receipt"]["backend"]["max_swap_us"] = json!(500);
        assert!(same_main_capture(&baseline, &dynamic, &parent).is_ok());
        for path in [
            "/readback/sdk_adapter",
            "/readback/create_seq",
            "/readback/receipt/renderer_owner",
            "/readback/receipt/context_epoch",
            "/readback/receipt/target/generation",
            "/readback/receipt/layers/0/surface",
            "/readback/receipt/backend/renderer",
            "/readback/receipt/layers/0/offset/x",
        ] {
            let mut changed = current.clone();
            *changed.pointer_mut(path).unwrap() = json!("foreign");
            assert!(
                same_main_capture(&baseline, &changed, &parent).is_err(),
                "{path}"
            );
        }
        assert!(same_main_capture(&baseline, &stage(12, 8, false), &parent).is_err());
        assert!(same_main_capture(&baseline, &stage(7, 13, false), &parent).is_err());
        for invalid in [json!(9), json!(null), json!("0"), json!("09"), json!("+9")] {
            let mut changed = current.clone();
            changed["readback"]["receipt"]["layers"][0]["content_seq"] = invalid.clone();
            assert!(main_content(&changed, &parent).is_err());
            changed = current.clone();
            changed["readback"]["receipt"]["content_frame"] = invalid;
            assert!(main_content(&changed, &parent).is_err());
        }
    }

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
