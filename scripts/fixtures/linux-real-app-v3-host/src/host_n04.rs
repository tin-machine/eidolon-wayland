//! N04 native observation only: real dialog twice around 127s idle.
//! No product timeout or lease setting is changed by this driver.
use super::{Driver, Result};
use eidolon_model::state::Status;
use serde_json::{Value, json};
use std::{
    thread,
    time::{Duration, Instant},
};

pub(super) const VARIANT: &str = "idle_125s";
const IDLE: Duration = Duration::from_secs(127);

pub(super) fn clock_ns() -> Result<u64> {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) } != 0
        || ts.tv_sec < 0
        || !(0..1_000_000_000).contains(&ts.tv_nsec)
    {
        return Err("I6_N04_CLOCK_FAILED");
    }
    (ts.tv_sec as u64)
        .checked_mul(1_000_000_000)
        .and_then(|n| n.checked_add(ts.tv_nsec as u64))
        .ok_or("I6_N04_CLOCK_FAILED")
}

impl Driver {
    fn n04_focus(&mut self, target: &Value, request: u32) -> Result<(u64, Value)> {
        let s = self.pump()?;
        let epoch = s["runtime"]["input_resets"]
            .as_u64()
            .ok_or("I6_N04_INPUT_FAILED")?;
        self.n02_command(
            json!({"schema":1,"type":"focus_window",
            "request_id":request.to_string(),"target":target,"input_epoch":epoch}),
            "focus_window",
        )?;
        self.n02_wait_command(&request.to_string(), 10)?;
        let s = self.wait(10, |s| {
            s["runtime"]["app"]["input"]["focus_window"] == target["window"]
                && s["runtime"]["app"]["input"]["focus_token"].is_string()
        })?;
        let epoch = s["runtime"]["input_resets"]
            .as_u64()
            .ok_or("I6_N04_INPUT_FAILED")?;
        let token = s["runtime"]["app"]["input"]["focus_token"].clone();
        self.observe(
            "n04_focus_current",
            json!({
                "request_id":request.to_string(),"target":target,"input_epoch":epoch,
                "focus_token":token,
                "delivered":s["runtime"]["app"]["input"]["delivered"],
            }),
        )?;
        Ok((epoch, token))
    }

    fn n04_key(
        &mut self,
        target: &Value,
        epoch: u64,
        token: &Value,
        request: u32,
        scan: i32,
        android: i32,
        press: bool,
    ) -> Result<()> {
        let id = request.to_string();
        self.n02_command(
            json!({"schema":1,"type":"key","request_id":id,
            "target":target,"input_epoch":epoch,"focus_token":token,
            "key":{"action":if press {"pressed"} else {"released"},
                "device":1,"scan_code":scan,"key_code":android,
                "repeat":0,"meta":0,"time_ms":request+1000}}),
            "key",
        )?;
        self.n02_wait_command(&id, 10)?;
        Ok(())
    }

    fn n04_dialog(&mut self, phase: &str, base: u32, main: &Value) -> Result<Value> {
        let first = self.pump()?;
        let windows = first["runtime"]["app"]["windows"]
            .as_array()
            .ok_or("I6_N04_DIALOG_MISSING")?;
        if !windows.iter().any(|w| {
            w["window"] == main["window"] && w["life"] == "Mapped" && w["parent"].is_null()
        }) {
            return Err("I6_N04_DIALOG_MISSING");
        }
        let old: Vec<Value> = windows.iter().map(|w| w["window"].clone()).collect();
        let (epoch, token) = self.n04_focus(main, base)?;
        let prior = self.pump()?["runtime"]["app"]["input"]["delivered"]
            .as_u64()
            .ok_or("I6_N04_INPUT_FAILED")?;
        for (i, scan, android, press) in [
            (1, 29, 113, true),
            (2, 24, 43, true),
            (3, 24, 43, false),
            (4, 29, 113, false),
        ] {
            self.n04_key(main, epoch, &token, base + i, scan, android, press)?;
        }
        let found = self.wait(25, |s| {
            let app = &s["runtime"]["app"];
            app["windows"].as_array().is_some_and(|ws| {
                ws.iter().any(|w| {
                    w["life"] == "Mapped"
                        && w["parent"] == main["window"]
                        && w["frame"].is_string()
                        && !old.contains(&w["window"])
                        && app["gui_windows"]
                            .as_array()
                            .is_some_and(|g| g.contains(&w["window"]))
                })
            })
        })?;
        let app = &found["runtime"]["app"];
        let child = app["windows"]
            .as_array()
            .ok_or("I6_N04_DIALOG_MISSING")?
            .iter()
            .find(|w| {
                w["life"] == "Mapped"
                    && w["parent"] == main["window"]
                    && w["frame"].is_string()
                    && !old.contains(&w["window"])
                    && app["gui_windows"]
                        .as_array()
                        .is_some_and(|g| g.contains(&w["window"]))
            })
            .ok_or("I6_N04_DIALOG_MISSING")?
            .clone();
        let child_id = child["window"].clone();
        let ready = Instant::now() + Duration::from_secs(30);
        let readback = loop {
            self.pump()?;
            let sdk = self.n02_sdk.as_mut().ok_or("I6_N04_DIALOG_MISSING")?;
            match sdk.tick_create_fresh(&self.registry, &self.completed) {
                Ok(Some(r)) if r["target"]["window"] == child_id => break r,
                Ok(Some(_)) => return Err("I6_N04_DIALOG_MISSING"),
                Ok(None) | Err(Status::NotReady | Status::Busy) => (),
                Err(_) => return Err("I6_N04_DIALOG_MISSING"),
            }
            if Instant::now() >= ready {
                return Err("I6_OUTER_TIMEOUT");
            }
            thread::sleep(Duration::from_millis(10));
        };
        let child_target = readback["target"].clone();
        let (esc_epoch, esc_token) = self.n04_focus(&child_target, base + 5)?;
        let before_esc = self.pump()?["runtime"]["app"]["input"]["delivered"]
            .as_u64()
            .ok_or("I6_N04_INPUT_FAILED")?;
        self.n04_key(&child_target, esc_epoch, &esc_token, base + 6, 1, 111, true)?;
        self.n04_key(
            &child_target,
            esc_epoch,
            &esc_token,
            base + 7,
            1,
            111,
            false,
        )?;
        let gone = self.wait(25, |s| {
            s["runtime"]["app"]["windows"].as_array().is_some_and(|ws| {
                !ws.iter()
                    .any(|w| w["window"] == child_id && w["life"] == "Mapped")
            }) && s["runtime"]["app"]["gui_windows"]
                .as_array()
                .is_some_and(|g| !g.contains(&child_id))
        })?;
        let after = gone["runtime"]["app"]["input"]["delivered"]
            .as_u64()
            .ok_or("I6_N04_INPUT_FAILED")?;
        let open_count = found["runtime"]["app"]["input"]["delivered"]
            .as_u64()
            .ok_or("I6_N04_INPUT_FAILED")?;
        if open_count < prior + 4 || after < before_esc + 2 {
            return Err("I6_N04_INPUT_FAILED");
        }
        self.observe(
            "n04_dialog_completed",
            json!({"phase":phase,"window":child_id,
            "parent":main["window"],"base_request":base.to_string()}),
        )?;
        Ok(
            json!({"phase":phase,"base_request":base.to_string(),"main_target":main,
            "main_epoch":epoch,"main_focus_token":token,"delivered_before":prior,
            "delivered_open":open_count,"window":child,"dialog_target":child_target,
            "dialog_readback":readback,"open_gui_windows":app["gui_windows"],
            "escape_epoch":esc_epoch,"escape_focus_token":esc_token,
            "delivered_before_escape":before_esc,"delivered_closed":after,
            "closed_gui_windows":gone["runtime"]["app"]["gui_windows"],
            "closed_windows":gone["runtime"]["app"]["windows"]}),
        )
    }

    pub(super) fn exercise_n04(&mut self, run_id: &str) -> Result<()> {
        self.n04_active = true;
        self.exercise_n02("mousepad_editor")?;
        let main = self.n02_readback.as_ref().ok_or("I6_N04_DIALOG_MISSING")?["target"].clone();
        let before = self.n04_dialog("before", 2000, &main)?;
        self.refresh_trace()?;
        let capture = self
            .trace
            .events
            .iter()
            .find_map(|e| e.capture.as_ref())
            .ok_or("I6_N04_TRACE_MISSING")?
            .capture_id
            .clone();
        let begin = clock_ns()?;
        self.observe("n04_idle_begin", json!({"ns":begin}))?;
        eprintln!("I6_N04_IDLE_BEGIN {run_id}");
        let start = Instant::now();
        let mut next = Duration::from_secs(6);
        while start.elapsed() < IDLE {
            let s = self.pump()?;
            if s["runtime"]["state"] != "Ready"
                || s["runtime"]["app"]["state"] != "running"
                || s["runtime"]["transport"]["desktop_v3_transport"]["transport"]["phase"]
                    != "started"
            {
                return Err("I6_N04_IDLE_LOST");
            }
            if start.elapsed() >= next {
                self.observe("n04_lease_sample", json!({"ns":clock_ns()?}))?;
                next += Duration::from_secs(5);
            }
            thread::sleep(Duration::from_millis(100));
        }
        let end = clock_ns()?;
        self.observe("n04_idle_end", json!({"ns":end}))?;
        eprintln!("I6_N04_IDLE_END {run_id}");
        let after = self.n04_dialog("after", 3000, &main)?;
        self.n04 = Some(json!({"schema":1,"run_id":run_id,"capture_id":capture,
            "idle":{"begin_ns":begin,"end_ns":end},"main_target":main,
            "dialogs":[before,after]}));
        Ok(())
    }
    pub(super) fn receipt_n04(
        &self,
        run_id: &str,
        error: Option<&str>,
        cleanup: Option<&str>,
    ) -> Value {
        let mut result = self.receipt_n02(run_id, "mousepad_editor", error, cleanup);
        result["schema"] = json!("eidolon-i6-n04-native-v1");
        result["variant"] = json!(VARIANT);
        result["n04"] = self.n04.clone().unwrap_or(Value::Null);
        result
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn original_product_deadlines_are_not_modified() {
        assert_eq!(VARIANT, "idle_125s");
        assert!(IDLE >= Duration::from_secs(125));
        assert!(clock_ns().unwrap() > 0);
    }
}
