//! N07-B2: one real Mousepad GUI candidate toggle, four actual renderer states.
//! No reviewed golden, independent keyfile proof or second C2 session exists yet.
use super::{
    Driver, Result,
    host_n07::{popup_grab_matches_click, popup_is_current},
};
use eidolon_render::HostReadbackRoi;
use serde_json::{Value, json};
use std::{
    thread,
    time::{Duration, Instant},
};

pub(super) const VARIANT: &str = "line_numbers_four_rois_probe";
// Candidate positions in the fixed 1024x768 Host fixture. The menu item is
// third in the pinned upstream View menu after Select Font / Color Scheme.
// An actual CI renderer crop located Line Numbers around y=126; this
// coordinate still needs independent B3 golden review.
const VIEW_UV: [f64; 2] = [0.16, 0.063];
const LINE_NUMBERS_UV: [f64; 2] = [0.195, 0.164];

fn gutter_roi() -> HostReadbackRoi {
    HostReadbackRoi {
        id: 1,
        x: 0,
        y: 64,
        width: 48,
        height: 128,
    }
}
fn checkmark_roi() -> HostReadbackRoi {
    // Covers the proposed Line Numbers row and checkmark, but not a golden.
    HostReadbackRoi {
        id: 2,
        x: 112,
        y: 96,
        width: 112,
        height: 128,
    }
}
fn frame(stage: &Value) -> Result<u64> {
    stage["readback"]["receipt"]["content_frame"]
        .as_str()
        .and_then(|v| v.parse().ok())
        .ok_or("I6_N07_B2_FRAME_INVALID")
}
fn hash(stage: &Value) -> Result<&str> {
    stage["readback"]["pixels"][0]["sha256"]
        .as_str()
        .ok_or("I6_N07_B2_ROI_INVALID")
}

/// Renderer TargetReport serializes content_frame as a positive decimal string,
/// just like the SDK readback receipt. Using Value::as_u64() here would always
/// return None and make real GUI frame progress impossible to observe.
pub(super) fn snapshot_content_frame(snapshot: &Value, target: &Value) -> Option<u64> {
    let raw = snapshot["runtime"]["render"]["targets"]
        .as_array()?
        .iter()
        .find(|r| r["key"] == *target)?["content_frame"]
        .as_str()?;
    if raw.is_empty() || raw.starts_with('0') || !raw.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    raw.parse::<u64>().ok()
}
pub(super) fn popup(snapshot: &Value, target: &Value) -> Option<Value> {
    snapshot["runtime"]["app"]["popups"]
        .as_array()?
        .iter()
        .find(|p| popup_is_current(p, target))
        .cloned()
}

impl Driver {
    pub(super) fn exercise_n07_b2_probe(&mut self, run_id: &str) -> Result<()> {
        // B1's actual normal Inspect/Launch/SDK Create, View pointer dispatch,
        // popup-grab serialization and initial live renderer readback.
        self.exercise_n07_probe(run_id)?;
        let base = self.n07_probe.as_ref().ok_or("I6_N07_B2_BASE_MISSING")?;
        let target = base["main_target"].clone();
        let initial_popup = base["popup"].clone();
        let initial_pointer = base["pointer"].clone();
        let off = base["before_menu"].clone();
        let epoch = initial_pointer["input_epoch"]
            .as_u64()
            .ok_or("I6_N07_B2_STALE_EPOCH")?;
        if !popup_grab_matches_click(&initial_popup, &initial_pointer) {
            return Err("I6_N07_B2_POPUP_SERIAL");
        }
        // Keep a partial report on every subsequent failure; no error is
        // converted to a successful pixel receipt.
        self.n07_probe.as_mut().unwrap()["b2"] = json!({
            "schema": 1,
            "source": "normal_registry_pointer_and_sdk_renderer",
            "coordinate_reviewed": false,
            "menu_identity_verified": false,
            "initial_popup": initial_popup,
            "first_off_gutter": {"source_stage": "before_menu", "roi_id": 1},
            "candidate_item_uv_milli": [195, 164],
            "item_pointer": null,
            "first_off_checkmark": null,
            "first_on_gutter": null,
            "reopen_pointer": null,
            "reopened_popup": null,
            "first_on_checkmark": null
        });
        let off_check = self.n07_capture_with_rois(
            &target,
            "first_off_checkmark",
            Some(vec![checkmark_roi()]),
        )?;
        self.n07_probe.as_mut().unwrap()["b2"]["first_off_checkmark"] = off_check.clone();
        self.n07_capture_review_context(&target, "first_off_menu_context")?;
        let mut req = 7100u32;
        // The item must be selected on its real popup surface, not a Registry
        // Accepted status on the main toplevel. Preserve the B1 press barrier.
        let item =
            self.n07_click_at(&target, epoch, &mut req, LINE_NUMBERS_UV, [195, 164], false)?;
        self.n07_probe.as_mut().unwrap()["b2"]["item_pointer"] = item.clone();
        if item["after"]["last_delivered_surface"]
            != self.n07_probe.as_ref().unwrap()["b2"]["initial_popup"]["surface"]
        {
            return Err("I6_N07_B2_ITEM_WRONG_SURFACE");
        }
        let closed = self.wait(12, |s| {
            s["runtime"]["input_resets"] == epoch && popup(s, &target).is_none()
        })?;
        if popup(&closed, &target).is_some() {
            return Err("I6_N07_B2_MENU_STILL_OPEN");
        }
        let deadline = Instant::now() + Duration::from_secs(12);
        let previous_content_frame = frame(&off_check)?;
        // Closing a popup does not necessarily update main content; we need a
        // newer *actual* renderer content frame, not a timer or synthetic ACK.
        loop {
            let current = self.pump()?;
            let newer = snapshot_content_frame(&current, &target)
                .is_some_and(|n| n > previous_content_frame);
            if newer {
                break;
            }
            if Instant::now() >= deadline {
                return Err("I6_N07_B2_NO_NEW_CONTENT_FRAME");
            }
            thread::sleep(Duration::from_millis(50));
        }
        let on = if self.n07_review_context.is_some() {
            // B3's larger review images exposed a popup-removal frame with
            // the old main buffer. The optional review path additionally
            // waits for a real newer main-layer content sequence to settle.
            // Plain B2 keeps its original composition-frame diagnostic.
            self.n07_capture_settled_post_popup(
                &target,
                epoch,
                &off_check,
                &initial_popup["parent_surface"],
                vec![gutter_roi()],
            )?
        } else {
            self.n07_capture_with_rois(&target, "first_on_gutter", Some(vec![gutter_roi()]))?
        };
        self.n07_probe.as_mut().unwrap()["b2"]["first_on_gutter"] = on.clone();
        self.n07_capture_review_context(&target, "first_on_context")?;
        // B1 first_off_gutter is the first ROI of the real before_menu readback.
        if frame(&on)? <= frame(&off)? || hash(&on)? == hash(&off)? {
            return Err("I6_N07_B2_GUTTER_UNCHANGED");
        }
        // Reopen View through the same ordinary Registry pointer commands.
        let reopen = self.n07_click_at(&target, epoch, &mut req, VIEW_UV, [160, 63], true)?;
        self.n07_probe.as_mut().unwrap()["b2"]["reopen_pointer"] = reopen.clone();
        let current = self.wait(12, |s| {
            popup(s, &target).is_some_and(|p| {
                p["surface"] != initial_popup["surface"]
                    || p["ack_serial"] != initial_popup["ack_serial"]
            })
        })?;
        let fresh = popup(&current, &target).ok_or("I6_N07_B2_REOPEN_POPUP_MISSING")?;
        self.n07_probe.as_mut().unwrap()["b2"]["reopened_popup"] = fresh.clone();
        if !popup_grab_matches_click(&fresh, &reopen) {
            return Err("I6_N07_B2_REOPEN_POPUP_SERIAL");
        }
        let on_check =
            self.n07_capture_with_rois(&target, "first_on_checkmark", Some(vec![checkmark_roi()]))?;
        self.n07_probe.as_mut().unwrap()["b2"]["first_on_checkmark"] = on_check.clone();
        self.n07_capture_review_context(&target, "first_on_menu_context")?;
        if frame(&on_check)? <= frame(&off_check)? || hash(&on_check)? == hash(&off_check)? {
            return Err("I6_N07_B2_CHECKMARK_UNCHANGED");
        }
        self.observe(
            "n07_b2_candidate_gui_transition",
            json!({
                "target": target, "popup_surface": fresh["surface"],
                "old_content_frame": frame(&off)?, "new_content_frame": frame(&on)?,
                "golden_approved": false, "verified_n07_assertions": [],
            }),
        )?;
        Ok(())
    }
    pub(super) fn receipt_n07_b2_probe(
        &self,
        run_id: &str,
        error: Option<&str>,
        cleanup: Option<&str>,
    ) -> Value {
        let mut receipt = self.receipt_n07_probe(run_id, error, cleanup);
        receipt["schema"] = json!("eidolon-i6-n07-b2-native-probe-v1");
        receipt["variant"] = json!(VARIANT);
        if receipt["n07_probe"].is_object() {
            receipt["n07_probe"]["variant"] = json!(VARIANT);
        }
        receipt
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn candidate_roi_bounds_and_menu_positions_are_not_goldens() {
        let a = gutter_roi();
        let b = checkmark_roi();
        assert_eq!((a.x, a.y, a.width, a.height), (0, 64, 48, 128));
        assert_eq!((b.x, b.y, b.width, b.height), (112, 96, 112, 128));
        assert!(a.width * a.height <= 32768);
        assert!(b.width * b.height <= 32768);
        assert_eq!(LINE_NUMBERS_UV, [0.195, 0.164]);
        assert_eq!(VIEW_UV, [0.16, 0.063]);
    }

    #[test]
    fn snapshot_frame_is_decimal_string_not_json_number() {
        let target = json!({"session":"1","window":"1","scene":"1","generation":"1"});
        let mut snapshot = json!({"runtime":{"render":{"targets":[{
            "key":target,"content_frame":"8"
        }]}}});
        assert_eq!(snapshot_content_frame(&snapshot, &target), Some(8));
        assert!(snapshot_content_frame(&snapshot, &target).is_some_and(|n| n > 7));
        assert!(!snapshot_content_frame(&snapshot, &target).is_some_and(|n| n > 8));
        snapshot["runtime"]["render"]["targets"][0]["content_frame"] = json!(8);
        assert_eq!(snapshot_content_frame(&snapshot, &target), None);
        for invalid in ["", "0", "008", "8.0", "-8", "+8", "18446744073709551616"] {
            snapshot["runtime"]["render"]["targets"][0]["content_frame"] = json!(invalid);
            assert_eq!(
                snapshot_content_frame(&snapshot, &target),
                None,
                "{invalid}"
            );
        }
        snapshot["runtime"]["render"]["targets"][0]["content_frame"] = json!("9");
        assert_eq!(
            snapshot_content_frame(
                &snapshot,
                &json!({"session":"1","window":"1",
                "scene":"1","generation":"2"})
            ),
            None
        );
    }

    #[test]
    fn candidate_requires_current_owned_popup_and_real_content_frame() {
        let target = json!({"window":"7"});
        let valid = json!({"runtime":{"app":{"popups":[{
            "role":"xdg_popup","root_window":"7","captured_origin":true,
            "dismissed":false,"ack_serial":24,"layer":1,
            "surface":"11","parent_surface":"1"
        }]}}});
        assert!(popup(&valid, &target).is_some());
        assert!(popup(&valid, &json!({"window":"8"})).is_none());
        let mut stale = valid.clone();
        stale["runtime"]["app"]["popups"][0]["dismissed"] = json!(true);
        assert!(popup(&stale, &target).is_none());
        assert!(frame(&json!({"readback":{"receipt":{"content_frame":"20"}}})).is_ok());
        assert!(frame(&json!({"readback":{"receipt":{"content_frame":null}}})).is_err());
    }
}
