//! I6 N02 actual product launch driver. No direct app/Waypipe spawn.
//!
//! This is deliberately a product smoke and bounded native observation, not
//! an independent N02 oracle or an I6/Quest acceptance verdict.
use super::{Driver, Result, digest, host_sdk::HostSdkAdapter};
use eidolon_model::linux_desktop_v3::Selection;
use eidolon_model::state::Status;
use serde_json::{Value, json};
use std::{
    thread,
    time::{Duration, Instant},
};

pub(super) const N02_VARIANTS: [&str; 2] = ["gtk3_desktop", "mousepad_editor"];

fn app_id(variant: &str) -> Result<&'static str> {
    match variant {
        "gtk3_desktop" => Ok("gtk3-desktop"),
        "mousepad_editor" => Ok("mousepad-editor-v1"),
        _ => Err("I6_INPUT_INVALID"),
    }
}

impl Driver {
    pub(super) fn n02_command(&mut self, value: Value, name: &'static str) -> Result<()> {
        let request = value["request_id"]
            .as_str()
            .ok_or("I6_N02_COMMAND_FAILED")?
            .to_owned();
        let status = self.registry.command(self.handle, &value.to_string());
        self.observe(
            "n02_command_accepted",
            json!({"command": name, "request_id": request, "status": status as i32}),
        )?;
        if status != Status::Accepted {
            return Err("I6_N02_COMMAND_FAILED");
        }
        Ok(())
    }

    pub(super) fn n02_wait_command(&mut self, request: &str, seconds: u64) -> Result<Value> {
        let deadline = Instant::now() + Duration::from_secs(seconds);
        loop {
            let snapshot = self.pump()?;
            if self.completed.get(request) == Some(&json!("ACCEPTED")) {
                return Ok(snapshot);
            }
            if Instant::now() >= deadline {
                return Err("I6_OUTER_TIMEOUT");
            }
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn n02_catalog(&mut self, session: &str, variant: &str) -> Result<Value> {
        let catalog = self.registry.catalog_ui(self.handle);
        let entry = app_id(variant)?;
        let descriptor = &catalog["catalog"]["desktop_v3"];
        let entries = descriptor["entries"]
            .as_array()
            .ok_or("I6_CATALOG_MISSING")?;
        if catalog["code"] != 0
            || catalog["catalog"]["session"] != session
            || catalog["catalog"]["pending"] != false
            || catalog["catalog"]["error"] != Value::Null
            || descriptor["namespace"] != "linux-desktop"
            || descriptor["arbitrary_exec"] != false
            || entries.iter().filter(|v| v["id"] == entry).count() != 1
        {
            return Err("I6_CATALOG_MISSING");
        }
        let selection = Selection::parse(
            &serde_json::to_vec(&json!({
                "scope": descriptor["scope"],
                "revision": descriptor["revision"],
                "app_id": entry,
            }))
            .map_err(|_| "I6_CATALOG_MISSING")?,
        )
        .map_err(|_| "I6_CATALOG_MISSING")?;
        self.catalog = catalog.clone();
        self.observe(
            "n02_same_actor_catalog",
            json!({
                "session":session,
                "inspect_request":"31",
                "scope": descriptor["scope"],
                "revision": descriptor["revision"],
                "app_id":entry,
            }),
        )?;
        Ok(selection.record())
    }

    /// Run the ordinary v3 Scene->Connect->Inspect->Launch->native plan path.
    /// Use the actual inspected actor's Selection; no local app spawn, no SDK
    /// success from a fixture boolean, and no helper bypass.
    pub(super) fn exercise_n02(&mut self, variant: &str) -> Result<()> {
        self.exercise_n02_inner(variant, false)
    }

    pub(super) fn exercise_n02_inner(&mut self, variant: &str, n03_double: bool) -> Result<()> {
        app_id(variant)?;
        self.wait(15, |s| s["runtime"]["state"] == "Ready")?;
        self.observe("runtime_ready", json!({}))?;

        self.n02_command(
            json!({"schema":1,"type":"scene_ready","request_id":"20","scene":"1"}),
            "scene_ready",
        )?;
        self.wait(10, |s| s["runtime"]["scene"] == "1")?;
        self.n02_command(
            json!({"schema":1,"type":"set_activity_state","request_id":"21",
                   "expected_scene":"1","visible":true}),
            "set_activity_state",
        )?;
        self.n02_command(
            json!({"schema":1,"type":"set_xr_state","request_id":"22",
                   "expected_scene":"1","state":"FOCUSED"}),
            "set_xr_state",
        )?;
        self.wait(10, |s| {
            s["runtime"]["scene"] == "1"
                && s["runtime"]["activity_visible"] == true
                && s["runtime"]["xr_state"] == "FOCUSED"
        })?;
        self.observe("n02_scene_visible_focused", json!({"scene":"1"}))?;

        let session = self.connect(30, false)?;
        self.command("linux_desktop_v3_inspect", 31, Some(&session))?;
        self.wait(45, |s| s["runtime"]["transport"]["state"] == "DesktopReady")?;
        let selection = self.n02_catalog(&session, variant)?;
        self.n02_command(
            json!({"schema":1,"type":"linux_desktop_v3_launch","request_id":"32",
                   "session":session,"selection":selection.clone()}),
            "linux_desktop_v3_launch",
        )?;
        self.n02_sdk = Some(HostSdkAdapter::new(self.handle).map_err(|_| "I6_N02_SDK_FAILED")?);
        if n03_double {
            self.n03_second_while_first_live(&session, selection)?;
        }
        // Inspect actual launch transitions rather than timing out on a
        // misleading queue Accepted. This is bounded read-only R evidence,
        // not a setter or a replacement for Started.
        let deadline = Instant::now() + Duration::from_secs(45);
        let mut prior = String::new();
        loop {
            let s = self.pump()?;
            let remote = &s["runtime"]["transport"]["desktop_v3_transport"];
            let app = &s["runtime"]["app"];
            let progress = json!({
                "transport_phase": remote["transport"]["phase"],
                "journal_stage": remote["journal_stage"],
                "launch_request": remote["launch_request"],
                "start_attempted": remote["start_attempted"],
                "started_observed": remote["started_observed"],
                "transport_error": remote["error"],
                "transport_faults": remote["faults"],
                "app_state": app["state"], "app_ready": app["ready"],
                "app_error": app["error"], "forward_sent": app["forward_sent"],
                "local_ready": app["local"]["ready"],
                "local_workers": app["local"]["workers"],
                "local_error": app["local"]["error"],
                "accepted": app["accepted"],
            });
            let current = progress.to_string();
            if current != prior {
                self.append_observation("n02_launch_progress", progress)?;
                prior = current;
            }
            if remote["session"] == session
                && remote["launch_request"] == "32"
                && remote["started_observed"] == true
                && remote["transport"]["phase"] == "started"
            {
                break;
            }
            if s["runtime"]["state"] != "Ready"
                || remote["error"].is_string()
                || app["error"].is_string()
            {
                return Err("I6_N02_LAUNCH_FAILED");
            }
            if Instant::now() >= deadline {
                return Err("I6_OUTER_TIMEOUT");
            }
            thread::sleep(Duration::from_millis(10));
        }
        self.observe(
            "n02_transport_started",
            json!({"request_id":"32","session":session}),
        )?;

        let deadline = Instant::now() + Duration::from_secs(30);
        let readback = loop {
            let snapshot = self.pump()?;
            if snapshot["runtime"]["state"] != "Ready" {
                return Err("I6_N02_LAUNCH_FAILED");
            }
            let sdk = self.n02_sdk.as_mut().ok_or("I6_N02_SDK_FAILED")?;
            match sdk.tick_create(&self.registry, &self.completed) {
                Ok(Some(readback)) => break readback,
                Ok(None) => (),
                Err(Status::NotReady | Status::Busy) => (),
                Err(_) => return Err("I6_N02_SDK_FAILED"),
            }
            if Instant::now() >= deadline {
                return Err("I6_OUTER_TIMEOUT");
            }
            thread::sleep(Duration::from_millis(10));
        };
        self.n02_readback = Some(readback.clone());

        let target = readback["target"].clone();
        let create = readback["create"].clone();
        let snapshot = self.wait(10, |s| {
            s["runtime"]["desktop_v3_presentation"]["state"] == "ready"
                && s["runtime"]["desktop_v3_presentation"]["startup_completed"] == true
                && s["runtime"]["desktop_v3_presentation"]["main"] == target["window"]
                && s["runtime"]["desktop_v3_sdk_target"]["target"] == target
                && s["runtime"]["desktop_v3_sdk_target"]["acknowledged"] == true
                && s["runtime"]["desktop_v3_sdk_target"]["binding_present"] == true
                && s["runtime"]["desktop_v3_sdk_target"]["renderer_frame_seen"] == true
                && s["runtime"]["desktop_v3_sdk_target"]["create_command"] == create
        })?;
        self.n02_ready = Some(json!({
            "presentation": snapshot["runtime"]["desktop_v3_presentation"],
            "sdk_target": snapshot["runtime"]["desktop_v3_sdk_target"],
            "transport": snapshot["runtime"]["transport"]["desktop_v3_transport"],
        }));
        self.observe("n02_b1_ready", json!({"target":target,"create":create}))?;

        // This is the normal parser/command gate. The focus token must come
        // from ProtocolState's current input report AFTER FocusWindow completes,
        // never from our caller-generated request id.
        let epoch = snapshot["runtime"]["input_resets"]
            .as_u64()
            .ok_or("I6_N02_INPUT_FAILED")?;
        let before = snapshot["runtime"]["app"]["input"]["delivered"]
            .as_u64()
            .ok_or("I6_N02_INPUT_FAILED")?;
        self.n02_command(
            json!({"schema":1,"type":"focus_window","request_id":"1001",
                   "target":target,"input_epoch":epoch}),
            "focus_window",
        )?;
        self.n02_wait_command("1001", 10)?;
        let snapshot = self.wait(10, |s| {
            s["runtime"]["app"]["input"]["focus_window"] == target["window"]
                && s["runtime"]["app"]["input"]["focus_token"].is_string()
        })?;
        let token = snapshot["runtime"]["app"]["input"]["focus_token"].clone();
        let current_epoch = snapshot["runtime"]["input_resets"]
            .as_u64()
            .ok_or("I6_N02_INPUT_FAILED")?;
        // A harmless US Left Shift press/release proves actual key delivery.
        for (request, action, time_ms) in
            [("1002", "pressed", 100u32), ("1003", "released", 101u32)]
        {
            self.n02_command(
                json!({
                    "schema":1,"type":"key","request_id":request,"target":target,
                    "input_epoch":current_epoch,"focus_token":token,
                    "key":{"action":action,"device":1,"scan_code":42,"key_code":59,
                           "repeat":0,"meta":0,"time_ms":time_ms},
                }),
                "key",
            )?;
            self.n02_wait_command(request, 10)?;
        }
        let after = self.wait(5, |s| {
            s["runtime"]["app"]["input"]["delivered"]
                .as_u64()
                .is_some_and(|n| n >= before + 2)
        })?;
        let delivered = after["runtime"]["app"]["input"]["delivered"]
            .as_u64()
            .ok_or("I6_N02_INPUT_FAILED")?;
        self.n02_input = Some(json!({
            "target":target,"epoch":current_epoch,"focus_token":token,
            "focus_request":"1001","key_requests":["1002","1003"],
            "delivered_before":before,"delivered_after":delivered,
            "focus_window":after["runtime"]["app"]["input"]["focus_window"],
        }));
        self.observe(
            "n02_input_delivered",
            json!({
                "target":target,"input_epoch":current_epoch,
                "focus_token":token,"delivery_delta":delivered-before,
            }),
        )?;
        Ok(())
    }

    /// Close through the current ordinary controller, then advance synthetic
    /// SDK retirement and the product finalizer on the same live Runtime.
    /// Do not call Runtime stop until origin_retired AND finalizer both hold.
    pub(super) fn retire_n02(&mut self) -> Result<()> {
        let snapshot = self.pump()?;
        if let Some(session) = snapshot["runtime"]["transport"]["session"].as_str()
            && snapshot["runtime"]["reclamation"]["recovery_connect_allowed"] != true
            && snapshot["runtime"]["reclamation"]["origin_retired"] != true
        {
            let session = session.to_owned();
            // Even an earlier product failure must attempt normal Disconnect.
            self.command("disconnect", 60, Some(&session))?;
        }
        let deadline = Instant::now() + Duration::from_secs(60);
        let mut recovered = false;
        loop {
            let snapshot = self.pump()?;
            let done = if let Some(sdk) = self.n02_sdk.as_mut() {
                match sdk.tick_retire(&self.registry) {
                    Ok(done) => done,
                    Err(Status::NotReady | Status::Busy) => false,
                    Err(_) => return Err("I6_N02_RETIRE_FAILED"),
                }
            } else {
                false
            };
            if done && snapshot["runtime"]["reclamation"]["origin_retired"] == true {
                self.refresh_trace()?;
                if !self
                    .trace
                    .events
                    .iter()
                    .any(|e| e.kind == "finalizer_published")
                {
                    return Err("I6_FINALIZER_MISSING");
                }
                self.observe(
                    "origin_retired",
                    json!({"reclamation":snapshot["runtime"]["reclamation"]}),
                )?;
                return Ok(());
            }
            if done
                && !recovered
                && snapshot["runtime"]["reclamation"]["recovery_connect_allowed"] == true
            {
                let session = self.connect(61, true)?;
                self.command("linux_desktop_v3_recover", 62, Some(&session))?;
                recovered = true;
            }
            if Instant::now() >= deadline {
                return Err("I6_OUTER_TIMEOUT");
            }
            thread::sleep(Duration::from_millis(10));
        }
    }

    pub(super) fn receipt_n02(
        &self,
        run_id: &str,
        variant: &str,
        error: Option<&str>,
        cleanup_error: Option<&str>,
    ) -> Value {
        let source: Value =
            serde_json::from_str(env!("EIDOLON_I6_BUILD_SOURCE")).expect("compiled source");
        let capture = self.trace.events.iter().find_map(|e| e.capture.as_ref());
        let owner_id = capture.map(|capture| {
            digest(format!("{run_id}:{}:{}", self.handle, capture.capture_id))[..32].to_owned()
        });
        json!({
            "schema":"eidolon-i6-n02-native-v1",
            "run_id":run_id,"variant":variant,"runtime":self.handle.to_string(),
            "owner_id":owner_id,"source_sha256":source["tree_sha256"],
            "build_source":source,
            "inventory_sha256":digest(
                eidolon_transport::linux_desktop::v3::sources::inventory_bytes()),
            "compiled_source_hashes":
                eidolon_transport::linux_desktop::v3::sources::current_hashes(),
            "trace":self.trace,"catalog":self.catalog,"observations":self.observations,
            "sdk_observations":self.n02_sdk.as_ref().map(|sdk| sdk.observations()),
            "sdk_retired":self.n02_sdk.as_ref().is_some_and(|sdk| sdk.has_retired_all()),
            "readback":self.n02_readback,"ready":self.n02_ready,"input":self.n02_input,
            "start_count":self.trace.events.iter()
                .filter(|e| e.kind == "control_send_attempt" && e.data["action"] == "start")
                .count(),
            "last_snapshot":self.last_snapshot,"destroy_status":self.destroy_status,
            "registry_counts":self.registry.counts(),"error":error,"cleanup_error":cleanup_error,
        })
    }
}

// Isolated test of the mapping; the variant name is not a catalog AppId.
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn variants_are_not_catalog_app_ids() {
        assert_eq!(app_id("gtk3_desktop"), Ok("gtk3-desktop"));
        assert_eq!(app_id("mousepad_editor"), Ok("mousepad-editor-v1"));
        assert_eq!(app_id("gtk3-desktop"), Err("I6_INPUT_INVALID"));
        assert_eq!(app_id("mousepad-editor-v1"), Err("I6_INPUT_INVALID"));
    }
}
