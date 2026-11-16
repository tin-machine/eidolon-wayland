//! I6 N07-B1: real normal-Registry pointer, popup discovery and content ROIs.
//!
//! This is a *diagnostic Host probe*, not N07/same_storage_restart acceptance:
//! no approved pixel golden, independent keyfile sequence or second Runtime.
//! Menu identity and click coordinates require independent visual review before
//! B2 may use this probe to toggle the actual Line Numbers action.
use super::{Driver, Result};
use eidolon_model::{state::Status, surface::TargetKey};
use eidolon_render::HostReadbackRoi;
use serde_json::{Value, json};
use std::{
    thread,
    time::{Duration, Instant},
};

pub(super) const VARIANT: &str = "pointer_popup_readback_probe";
const SOURCE: &str = "1";
// An explicitly UNREVIEWED discovery coordinate. Only native pointer and
// visual evidence can show which widget GTK actually received this click.
const VIEW_CANDIDATE_UV: [f64; 2] = [0.16, 0.063];

fn delivered_pointer(before: &Value, after: &Value, target: &Value) -> bool {
    let a = &before["runtime"]["app"]["input"]["pointer"];
    let b = &after["runtime"]["app"]["input"]["pointer"];
    let count = a["delivered_buttons"].as_u64();
    let completed = b["delivered_buttons"].as_u64();
    count
        .zip(completed)
        .is_some_and(|(old, new)| new >= old + 2)
        && b["last_delivered_source"] == SOURCE
        && b["last_delivered_surface"].as_str().is_some()
        && b["last_delivered_press_serial"]
            .as_u64()
            .is_some_and(|n| n > 0)
        && b["last_delivered_press_serial"] != a["last_delivered_press_serial"]
        && b["pressed"] == 0
        && b["hover_window"] == target["window"]
        && before["runtime"]["input_resets"] == after["runtime"]["input_resets"]
}

fn popup_grab_matches_click(popup: &Value, pointer: &Value) -> bool {
    // An explicit xdg_popup.grab must cite the initiating client pointer
    // event's serial, not an unrelated input epoch or another capture.
    match popup["grab_serial"].as_u64() {
        Some(serial) => {
            serial > 0
                && (pointer["after"]["last_delivered_press_serial"] == serial
                    || pointer["after"]["last_delivered_serial"] == serial)
        }
        None => popup["grab_serial"].is_null(),
    }
}

fn popup_is_current(popup: &Value, target: &Value) -> bool {
    popup["role"] == "xdg_popup"
        && popup["root_window"] == target["window"]
        && popup["captured_origin"] == true
        && popup["dismissed"] == false
        && popup["ack_serial"]
            .as_u64()
            .is_some_and(|serial| serial > 0)
        && popup["layer"].as_u64().is_some()
        && popup["surface"].as_str().is_some()
        && popup["parent_surface"].as_str().is_some()
        && popup["surface"] != popup["parent_surface"]
}

/// Fixed, bounded discovery regions. They are NOT golden coordinates, and no
/// sample here is compared to observed-as-expected output.
fn discovery_rois(width: u32, height: u32) -> Result<Vec<HostReadbackRoi>> {
    if width < 320 || height < 240 {
        return Err("I6_N07_ROI_UNAVAILABLE");
    }
    Ok(vec![
        HostReadbackRoi {
            id: 1,
            x: 0,
            y: 64,
            width: 48,
            height: 128,
        },
        HostReadbackRoi {
            id: 2,
            x: 64,
            y: 16,
            width: 192,
            height: 128,
        },
    ])
}

impl Driver {
    /// Three normal Registry commands and their actual command completions.
    /// Only the compositor's live surface-delivery counter can certify a
    /// forwarded button; a Registry Accepted or button-reset delta cannot.
    fn n07_click(&mut self, target: &Value, epoch: u64, req: &mut u32) -> Result<Value> {
        let before = self.pump()?;
        for (event, time_ms) in [
            (json!({"type":"move"}), 700u32),
            (json!({"type":"buttons","changed":1,"state":1}), 701),
            (json!({"type":"buttons","changed":1,"state":0}), 702),
        ] {
            let number = *req;
            *req = req.checked_add(1).ok_or("I6_N07_INPUT_FAILED")?;
            let command = json!({
                "schema":1, "type":"pointer", "request_id":number.to_string(),
                "target":target,"input_epoch":epoch,
                "pointer":{"source":SOURCE,"uv":VIEW_CANDIDATE_UV,
                           "event":event,"time_ms":time_ms},
            });
            self.n02_command(command, "pointer")?;
            self.n02_wait_command(&number.to_string(), 10)?;
        }
        let after = self.wait(10, |s| delivered_pointer(&before, s, target))?;
        if !delivered_pointer(&before, &after, target) {
            return Err("I6_N07_POINTER_NOT_DELIVERED");
        }
        let proof = json!({
            "target":target,"source":SOURCE,"input_epoch":epoch,
            // Receipt contract prohibits JSON floats. Transport still uses
            // the real [f64;2] UV command; evidence stores exact milli-units.
            "uv_milli":[160,63],"coordinate_reviewed":false,
            "first_request":(*req - 3).to_string(),
            "next_request":req.to_string(),
            "before":before["runtime"]["app"]["input"]["pointer"],
            "after":after["runtime"]["app"]["input"]["pointer"],
            "epoch_after":after["runtime"]["input_resets"],
            "provenance":"normal_registry_command_and_compositor_forward",
        });
        self.observe("n07_pointer_forwarded", proof.clone())?;
        Ok(proof)
    }

    fn n07_capture(&mut self, target: &Value, phase: &str) -> Result<Value> {
        let snapshot = self.pump()?;
        let renderer_target = snapshot["runtime"]["render"]["targets"]
            .as_array()
            .and_then(|targets| targets.iter().find(|r| r["key"] == *target))
            .ok_or("I6_N07_RENDERER_NOT_READY")?;
        let width = renderer_target["width"]
            .as_u64()
            .and_then(|n| u32::try_from(n).ok())
            .ok_or("I6_N07_RENDERER_NOT_READY")?;
        let height = renderer_target["height"]
            .as_u64()
            .and_then(|n| u32::try_from(n).ok())
            .ok_or("I6_N07_RENDERER_NOT_READY")?;
        let native_target: TargetKey =
            serde_json::from_value(target.clone()).map_err(|_| "I6_N07_TARGET_INVALID")?;
        let rois = discovery_rois(width, height)?;
        let evidence = self
            .n02_sdk
            .as_mut()
            .ok_or("I6_N07_SDK_MISSING")?
            .capture_live_rois(&self.registry, native_target, &rois)
            .map_err(|_| "I6_N07_RENDERER_READBACK_FAILED")?;
        if evidence["target"] != *target
            || evidence["source"] != "host_renderer_readback"
            || evidence["receipt"]["target"] != *target
            || evidence["receipt"]["runtime"] != self.handle.to_string()
            || evidence["pixels"].as_array().is_none_or(|v| v.len() != 2)
        {
            return Err("I6_N07_RENDERER_ORIGIN_INVALID");
        }
        self.observe(
            "n07_roi_readback_observed",
            json!({
                "phase":phase, "target":target, "create":evidence["create_seq"],
                "content_frame":evidence["receipt"]["content_frame"],
                "roi_sha256":evidence["receipt"]["roi_sha256"],
            }),
        )?;
        Ok(json!({"phase":phase,"readback":evidence}))
    }

    /// Uses N02 normal Inspect/Selection/Launch/SDK Create. Never opens a
    /// Mousepad process directly. One candidate menu click is diagnostic only:
    /// popup identity is derived from actual compositor xdg role callbacks.
    pub(super) fn exercise_n07_probe(&mut self, run_id: &str) -> Result<()> {
        self.exercise_n02("mousepad_editor")?;
        let main = self.n02_readback.as_ref().ok_or("I6_N07_MAIN_MISSING")?["target"].clone();
        let off = self.n07_capture(&main, "before_menu")?;
        let mut req = 7000u32;
        let (epoch, _token) = self.n05_focus(&main, &mut req)?;
        let pointer = self.n07_click(&main, epoch, &mut req)?;
        self.refresh_trace()?;
        let capture_id = self
            .trace
            .events
            .iter()
            .find_map(|event| event.capture.as_ref())
            .ok_or("I6_N07_CAPTURE_MISSING")?
            .capture_id
            .clone();
        self.n07_probe = Some(json!({
            "schema":1,"run_id":run_id,"variant":VARIANT,
            "capture_id":capture_id,
            "main_target":main,"pointer":pointer,"before_menu":off,
            "menu_coordinate_reviewed":false,"menu_identity_verified":false,
        }));
        // A real post-click image is captured even on discovery failure.
        // It lets reviewers distinguish no visible change from GTK drawing
        // an inline menu, without inventing an xdg_popup or a pixel golden.
        let post = self.n07_capture(&main, "after_pointer")?;
        self.n07_probe.as_mut().unwrap()["after_pointer"] = post;
        // Do not mint a popup from Registry Accepted: retain only actual
        // bounded protocol-owner snapshots. Capture every distinct popup
        // state, including unconfigured/undrawn/dismissed roles, for review.
        let deadline = Instant::now() + Duration::from_secs(8);
        let mut popup_samples = Vec::new();
        let mut previous = Value::Null;
        let current = loop {
            let snapshot = self.pump()?;
            let raw = snapshot["runtime"]["app"]["popups"].clone();
            if raw != previous && popup_samples.len() < 8 {
                popup_samples.push(json!({
                    "popups":raw,
                    "input_resets":snapshot["runtime"]["input_resets"],
                    "render_target":snapshot["runtime"]["render"]["targets"]
                        .as_array().and_then(|targets| targets.iter()
                            .find(|item| item["key"] == main))
                        .map(|item| json!({
                            "content_frame":item["content_frame"],
                            "frames":item["frames"],
                        })),
                }));
                previous = raw.clone();
            }
            if raw.as_array().is_some_and(|popups| {
                popups.iter().any(|popup| popup_is_current(popup, &main))
            }) {
                break snapshot;
            }
            if Instant::now() >= deadline {
                self.n07_probe.as_mut().unwrap()["popup_samples"] = json!(popup_samples);
                return Err("I6_N07_POPUP_UNOBSERVED");
            }
            thread::sleep(Duration::from_millis(50));
        };
        self.n07_probe.as_mut().unwrap()["popup_samples"] = json!(popup_samples);
        let popup = current["runtime"]["app"]["popups"]
            .as_array()
            .and_then(|v| v.iter().find(|p| popup_is_current(p, &main)))
            .ok_or("I6_N07_POPUP_UNOBSERVED")?
            .clone();
        if !popup_grab_matches_click(&popup, &pointer) {
            return Err("I6_N07_POPUP_SERIAL_MISMATCH");
        }
        let menu = self.n07_capture(&main, "menu_open")?;
        if self.n07_probe.as_ref().is_none() {
            return Err("I6_N07_PROBE_MISSING");
        }
        self.n07_probe.as_mut().unwrap()["popup"] = popup.clone();
        self.n07_probe.as_mut().unwrap()["menu_open"] = menu;
        self.observe(
            "n07_popup_observed",
            json!({
                "role":popup["role"],"parent_surface":popup["parent_surface"],
                "root_window":popup["root_window"],"ack_serial":popup["ack_serial"],
                "grab_serial":popup["grab_serial"],"layer":popup["layer"],
            }),
        )?;
        Ok(())
    }

    pub(super) fn receipt_n07_probe(
        &self,
        run_id: &str,
        error: Option<&str>,
        cleanup: Option<&str>,
    ) -> Value {
        let mut result = self.receipt_n02(run_id, "mousepad_editor", error, cleanup);
        result["schema"] = json!("eidolon-i6-n07-b1-native-probe-v1");
        result["variant"] = json!(VARIANT);
        result["n07_probe"] = self.n07_probe.clone().unwrap_or(Value::Null);
        result["n07_assertions_verified"] = json!([]);
        result["host_v3_acceptance_passed"] = json!(false);
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ack_without_actual_live_surface_delivery_is_not_a_pointer_proof() {
        let target = json!({"window":"7"});
        let old = json!({"runtime":{"input_resets":1,"app":{"input":{"pointer":{
            "delivered_buttons":5,"hover_window":"7","pressed":0,
        }}}}});
        let mut after = old.clone();
        assert!(!delivered_pointer(&old, &after, &target));
        after["runtime"]["app"]["input"]["pointer"]["delivered_buttons"] = json!(7);
        after["runtime"]["app"]["input"]["pointer"]["last_delivered_source"] = json!("1");
        after["runtime"]["app"]["input"]["pointer"]["last_delivered_press_serial"] = json!(38);
        after["runtime"]["app"]["input"]["pointer"]["last_delivered_serial"] = json!(39);
        after["runtime"]["app"]["input"]["pointer"]["last_delivered_surface"] = json!("11");
        assert!(delivered_pointer(&old, &after, &target));
        after["runtime"]["input_resets"] = json!(2);
        assert!(!delivered_pointer(&old, &after, &target));
        after["runtime"]["input_resets"] = json!(1);
        after["runtime"]["app"]["input"]["pointer"]["last_delivered_source"] = json!("2");
        assert!(!delivered_pointer(&old, &after, &target));
    }
    #[test]
    fn popup_must_be_owned_mapped_acked_and_in_main_layer() {
        let target = json!({"window":"7"});
        let original = json!({"role":"xdg_popup","root_window":"7",
            "captured_origin":true,"dismissed":false,"ack_serial":34,
            "layer":3,"surface":"11","parent_surface":"1"});
        assert!(popup_is_current(&original, &target));
        for (field, bad) in [
            ("role", json!("xdg_toplevel")),
            ("root_window", json!("8")),
            ("captured_origin", json!(false)),
            ("dismissed", json!(true)),
            ("ack_serial", Value::Null),
            ("layer", Value::Null),
            ("parent_surface", json!("11")),
        ] {
            let mut value = original.clone();
            value[field] = bad;
            assert!(!popup_is_current(&value, &target), "{field}");
        }
    }
    #[test]
    fn real_popup_grab_serial_must_match_delivered_pointer_press_or_release() {
        let pointer = json!({"after":{
            "last_delivered_press_serial":38,
            "last_delivered_serial":39,
        }});
        assert!(popup_grab_matches_click(
            &json!({"grab_serial":38}),
            &pointer
        ));
        assert!(popup_grab_matches_click(
            &json!({"grab_serial":39}),
            &pointer
        ));
        assert!(popup_grab_matches_click(
            &json!({"grab_serial":null}),
            &pointer
        ));
        assert!(!popup_grab_matches_click(
            &json!({"grab_serial":40}),
            &pointer
        ));
        assert!(!popup_grab_matches_click(
            &json!({"grab_serial":0}),
            &pointer
        ));
    }
    #[test]
    fn calibration_rois_are_bounded_but_not_reviewed_goldens() {
        let regions = discovery_rois(1024, 768).unwrap();
        assert_eq!(regions.len(), 2);
        assert_eq!(regions[0].x, 0);
        assert!(regions.iter().all(|r| r.width * r.height <= 32768));
        assert!(discovery_rois(319, 768).is_err());
        assert!(discovery_rois(1024, 239).is_err());
        assert_eq!(VIEW_CANDIDATE_UV, [0.16, 0.063]);
    }
}
