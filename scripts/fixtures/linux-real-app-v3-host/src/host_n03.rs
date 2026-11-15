//! I6 N03: real Registry/Runtime/SSH Launch Admission negative probes.
//! Rejected requests never call the C2 helper or allocate a native app.
use super::{Driver, Result};
use eidolon_model::linux_desktop_v3::Selection;
use eidolon_model::state::Status;
use serde_json::{Value, json};
use std::{thread, time::{Duration, Instant}};

pub(super) const N03_VARIANTS: [&str; 5] = [
    "unknown_app_id", "scope_changed", "revision_changed",
    "old_catalog", "double_launch",
];

fn other_hash(hash: &str) -> Result<String> {
    if hash.len() != 64 || !hash.bytes().all(|c| c.is_ascii_hexdigit()) {
        return Err("I6_N03_CATALOG_INVALID");
    }
    let mut result = hash.to_owned();
    result.replace_range(..1, if hash.starts_with('a') { "b" } else { "a" });
    Ok(result)
}

impl Driver {
    fn n03_prepare_scene(&mut self) -> Result<()> {
        self.wait(15, |s| s["runtime"]["state"] == "Ready")?;
        self.observe("runtime_ready", json!({}))?;
        for (name, value) in [
            ("scene_ready", json!({"schema":1,"type":"scene_ready",
                                  "request_id":"20","scene":"1"})),
            ("set_activity_state", json!({"schema":1,"type":"set_activity_state",
                                  "request_id":"21","expected_scene":"1","visible":true})),
            ("set_xr_state", json!({"schema":1,"type":"set_xr_state",
                                  "request_id":"22","expected_scene":"1","state":"FOCUSED"})),
        ] {
            let request = value["request_id"].as_str().ok_or("I6_N03_COMMAND_FAILED")?.to_owned();
            let status = self.registry.command(self.handle, &value.to_string());
            self.observe("n03_setup_command", json!({"command":name,"request_id":request,
                                                       "status":status as i32}))?;
            if status != Status::Accepted {
                return Err("I6_N03_COMMAND_FAILED");
            }
        }
        self.wait(10, |s| s["runtime"]["scene"] == "1"
            && s["runtime"]["activity_visible"] == true
            && s["runtime"]["xr_state"] == "FOCUSED")?;
        Ok(())
    }

    fn n03_inspect(&mut self, connect_request: i64, inspect_request: i64) -> Result<(String, Value)> {
        let session = self.connect(connect_request, false)?;
        self.command("linux_desktop_v3_inspect", inspect_request, Some(&session))?;
        self.wait(45, |s| s["runtime"]["transport"]["state"] == "DesktopReady")?;
        let catalog = self.registry.catalog_ui(self.handle);
        let descriptor = &catalog["catalog"]["desktop_v3"];
        let selection = json!({
            "scope":descriptor["scope"], "revision":descriptor["revision"],
            "app_id":"gtk3-desktop",
        });
        Selection::parse(&serde_json::to_vec(&selection)
            .map_err(|_| "I6_N03_CATALOG_INVALID")?)
            .map_err(|_| "I6_N03_CATALOG_INVALID")?;
        if catalog["code"] != 0 || catalog["catalog"]["session"] != session
            || catalog["catalog"]["request_id"] != inspect_request.to_string()
            || catalog["catalog"]["pending"] != false || catalog["catalog"]["error"] != Value::Null
            || descriptor["namespace"] != "linux-desktop"
            || descriptor["arbitrary_exec"] != false
            || descriptor["entries"].as_array().is_none_or(|entries|
                entries.iter().filter(|e| e["id"] == "gtk3-desktop").count() != 1)
        {
            return Err("I6_N03_CATALOG_INVALID");
        }
        self.catalog = catalog;
        self.refresh_trace()?;
        let capture = self.trace.events.iter().rev().find_map(|e| e.capture.as_ref())
            .ok_or("I6_N03_CATALOG_INVALID")?.capture_id.clone();
        self.observe("n03_catalog", json!({
            "session":session,"inspect_request":inspect_request.to_string(),
            "selection":selection,"capture_id":capture,"registry":self.catalog,
        }))?;
        Ok((session, selection))
    }

    fn n03_rejected_launch(&mut self, session: &str, selection: Value,
                           request: &str, variant: &str) -> Result<()> {
        let expected = match variant {
            "unknown_app_id" => "INVALID_ARGUMENT",
            "scope_changed" | "revision_changed" | "old_catalog" => "NOT_READY",
            "double_launch" => "BUSY",
            _ => return Err("I6_N03_COMMAND_FAILED"),
        };
        let value = json!({"schema":1,"type":"linux_desktop_v3_launch",
                          "request_id":request,"session":session,"selection":selection});
        self.observe("n03_launch_submitted", json!({
            "variant":variant,"request_id":request,"session":session,"selection":selection,
        }))?;
        // Only this exact request ID can have a non-Accepted completion.
        // Other rejected completions remain fatal in Driver::pump().
        if expected != "INVALID_ARGUMENT" {
            self.n03_expected.insert(request.to_owned(), expected.to_owned());
        }
        let status = self.registry.command(self.handle, &value.to_string());
        self.observe("n03_registry_return", json!({
            "request_id":request,"status":status as i32,
        }))?;
        let immediate_expected = if expected == "INVALID_ARGUMENT" {
            Status::InvalidArgument
        } else {
            Status::Accepted
        };
        if status != immediate_expected {
            return Err("I6_N03_COMMAND_FAILED");
        }
        if expected != "INVALID_ARGUMENT" {
            let deadline = Instant::now() + Duration::from_secs(15);
            loop {
                self.pump()?;
                if self.completed.get(request) == Some(&json!(expected)) {
                    break;
                }
                if Instant::now() >= deadline {
                    return Err("I6_OUTER_TIMEOUT");
                }
                thread::sleep(Duration::from_millis(10));
            }
        } else if self.completed.contains_key(request) {
            return Err("I6_N03_COMMAND_FAILED");
        }
        self.observe("n03_rejection_observed", json!({
            "request_id":request,"status":expected,
            "completion":self.completed.get(request).cloned(),
        }))?;
        let snapshot = self.pump()?;
        let runtime = &snapshot["runtime"];
        self.observe("n03_rejection_boundary", json!({
            "request_id":request,
            "app":runtime["app"],
            "pending_targets":runtime["desktop_v3_pending_targets"],
            "sdk_bindings":runtime["app_sdk_bindings"],
            "render_targets":runtime["render"]["targets"],
            "origin_retired":runtime["reclamation"]["origin_retired"],
            "launch_request":runtime["transport"]["desktop_v3_transport"]["launch_request"],
            "start_attempted":runtime["transport"]["desktop_v3_transport"]["start_attempted"],
            "presentation":runtime["desktop_v3_presentation"]["state"],
        }))?;
        Ok(())
    }

    /// The positive first Launch is still live. N02 continues to establish
    /// real Started/SDK/readback/input and owns retirement after this probe.
    pub(super) fn n03_second_while_first_live(&mut self, session: &str,
                                                selection: Value) -> Result<()> {
        self.n02_wait_command("32", 15)?;
        let snapshot = self.pump()?;
        let runtime = &snapshot["runtime"];
        if runtime["reclamation"]["origin_retired"] == true
            || runtime["transport"]["desktop_v3_transport"]["launch_request"] != "32"
            || !runtime["app"].is_object()
        {
            return Err("I6_N03_FIRST_NOT_LIVE");
        }
        self.observe("n03_first_live", json!({
            "request_id":"32","completion":self.completed.get("32"),
            "launch_request":runtime["transport"]["desktop_v3_transport"]["launch_request"],
            "origin_retired":runtime["reclamation"]["origin_retired"],
            "app_state":runtime["app"]["state"],
        }))?;
        self.n03_rejected_launch(session, selection, "33", "double_launch")
    }

    pub(super) fn exercise_n03(&mut self, variant: &str) -> Result<()> {
        if !N03_VARIANTS.contains(&variant) {
            return Err("I6_N03_COMMAND_FAILED");
        }
        if variant == "double_launch" {
            // N02's proven positive product path, with one precisely placed
            // admission probe before its readback/Ready/input phase.
            self.exercise_n02_inner("gtk3_desktop", true)?;
            return self.retire_n02();
        }
        self.n03_prepare_scene()?;
        let (session, current) = self.n03_inspect(30, 31)?;
        let mut selected = current.clone();
        match variant {
            "unknown_app_id" => {
                selected["app_id"] = json!("not-a-registered-app");
            }
            "scope_changed" => {
                selected["scope"] = json!(other_hash(current["scope"].as_str()
                    .ok_or("I6_N03_CATALOG_INVALID")?)?);
            }
            "revision_changed" => {
                selected["revision"] = json!(other_hash(current["revision"].as_str()
                    .ok_or("I6_N03_CATALOG_INVALID")?)?);
            }
            "old_catalog" => {
                // Complete the old capture (including its *own* finalizer)
                // before binding a genuinely new session and inspecting again.
                self.retire_after_failure()?;
                self.observe("n03_old_capture_retired", json!({
                    "old_session":session,
                    "old_selection":current,
                }))?;
                let (new_session, new_selection) = self.n03_inspect(40, 41)?;
                if new_session == session || new_selection == current {
                    return Err("I6_N03_CAPTURE_NOT_FRESH");
                }
                self.n03_rejected_launch(&new_session, current, "42", variant)?;
                return self.retire_after_failure();
            }
            _ => return Err("I6_N03_COMMAND_FAILED"),
        }
        self.n03_rejected_launch(&session, selected, "32", variant)?;
        self.retire_after_failure()
    }

    pub(super) fn receipt_n03(&self, run_id: &str, variant: &str,
                              error: Option<&str>, cleanup_error: Option<&str>) -> Value {
        // Same bounded native observation fields as N02, but a distinct
        // schema; the N03 oracle never confuses this with an N02 pass.
        let mut receipt = self.receipt_n02(run_id, "gtk3_desktop", error, cleanup_error);
        receipt["schema"] = json!("eidolon-i6-n03-native-v1");
        receipt["variant"] = json!(variant);
        // For two sequential captures the last capture is the actual Launch
        // admission owner. Keep both immutable trace epochs for the oracle.
        if let Some(capture) = self.trace.events.iter().rev().find_map(|e| e.capture.as_ref()) {
            receipt["owner_id"] = json!(super::digest(
                format!("{run_id}:{}:{}", self.handle, capture.capture_id))[..32].to_owned());
        }
        receipt
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fixed_variants_and_mutated_hashes() {
        assert_eq!(N03_VARIANTS.len(), 5);
        assert_eq!(other_hash(&"a".repeat(64)), Ok(format!("b{}", "a".repeat(63))));
        assert_eq!(other_hash(&"b".repeat(64)), Ok(format!("a{}", "b".repeat(63))));
        assert!(other_hash("invalid").is_err());
    }
}
