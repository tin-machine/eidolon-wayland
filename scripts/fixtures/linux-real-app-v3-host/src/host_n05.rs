//! I6 N05 — actual normal Registry/Runtime input to GTK Mousepad.
//! No direct process launch, clipboard injection or synthetic file creation.
//! The independent supervisor, not this driver, validates the saved bytes.
use super::{Driver, Result};
use eidolon_model::state::Status;
use serde_json::{Value, json};
use std::{
    thread,
    time::{Duration, Instant},
};

pub(super) const VARIANT: &str = "open_edit_save_as";
const EDIT: &[u8] = b"eidolon-i6-saved\n";
const MAX_PATH: usize = 160;

fn ascii_key(byte: u8) -> Result<(i32, i32, bool)> {
    let letter_scan = [
        30, 48, 46, 32, 18, 33, 34, 35, 23, 36, 37, 38, 50, 49, 24, 25, 16, 19, 31, 20, 22, 47, 17,
        45, 21, 44,
    ];
    match byte {
        b'a'..=b'z' => Ok((
            letter_scan[(byte - b'a') as usize],
            29 + (byte - b'a') as i32,
            false,
        )),
        b'0' => Ok((11, 7, false)),
        b'1'..=b'9' => Ok((
            byte as i32 - b'1' as i32 + 2,
            byte as i32 - b'1' as i32 + 8,
            false,
        )),
        b'/' => Ok((53, 76, false)),
        b'.' => Ok((52, 56, false)),
        b'-' => Ok((12, 69, false)),
        b'_' => Ok((12, 69, true)),
        b'\n' => Ok((28, 66, false)),
        _ => Err("I6_N05_UNSUPPORTED_CHARACTER"),
    }
}

fn project_window(raw: &Value) -> Result<Value> {
    for key in ["window", "parent", "life", "frame", "app_id"] {
        if raw.get(key).is_none() {
            return Err("I6_N05_DIALOG_MISSING");
        }
    }
    Ok(json!({"window":raw["window"],"parent":raw["parent"],
              "life":raw["life"],"frame":raw["frame"],"app_id":raw["app_id"]}))
}

fn path_valid(path: &str) -> bool {
    path.starts_with("/home/eidolon-app/n05-")
        && path.len() <= MAX_PATH
        && path.len() > "/home/eidolon-app/n05-".len()
        && path.bytes().all(|b| ascii_key(b).is_ok())
        && !path.contains("..")
}

impl Driver {
    fn n05_focus(&mut self, target: &Value, req: &mut u32) -> Result<(u64, Value)> {
        let request = req.to_string();
        *req += 1;
        let epoch = self.pump()?["runtime"]["input_resets"]
            .as_u64()
            .ok_or("I6_N05_INPUT_FAILED")?;
        self.n02_command(
            json!({"schema":1,"type":"focus_window",
            "request_id":request,"target":target,"input_epoch":epoch}),
            "focus_window",
        )?;
        self.n02_wait_command(&request, 10)?;
        let s = self.wait(10, |s| {
            s["runtime"]["app"]["input"]["focus_window"] == target["window"]
                && s["runtime"]["app"]["input"]["focus_token"].is_string()
        })?;
        let epoch = s["runtime"]["input_resets"]
            .as_u64()
            .ok_or("I6_N05_INPUT_FAILED")?;
        let token = s["runtime"]["app"]["input"]["focus_token"].clone();
        self.observe(
            "n05_focus_current",
            json!({
            "request_id":request,"target":target,"epoch":epoch,"token":token}),
        )?;
        Ok((epoch, token))
    }

    fn n05_key(
        &mut self,
        target: &Value,
        epoch: u64,
        token: &Value,
        req: &mut u32,
        scan: i32,
        android: i32,
        down: bool,
    ) -> Result<()> {
        let number = *req;
        *req = req.checked_add(1).ok_or("I6_N05_INPUT_FAILED")?;
        let request = number.to_string();
        // The same normal Registry guard as N04. Only per-key producer noise
        // is suppressed; pump still checks every ACCEPTED command completion.
        let command = json!({"schema":1,"type":"key","request_id":request,
            "target":target,"input_epoch":epoch,"focus_token":token,
            "key":{"action":if down {"pressed"} else {"released"},
                "device":1,"scan_code":scan,"key_code":android,
                "repeat":0,"meta":0,"time_ms":number+1000}});
        if self.registry.command(self.handle, &command.to_string()) != Status::Accepted {
            return Err("I6_N05_INPUT_REJECTED");
        }
        self.n02_wait_command(&request, 10)?;
        Ok(())
    }

    fn n05_shortcut(
        &mut self,
        target: &Value,
        epoch: u64,
        token: &Value,
        req: &mut u32,
        scan: i32,
        android: i32,
        shift: bool,
    ) -> Result<u64> {
        self.n05_key(target, epoch, token, req, 29, 113, true)?;
        if shift {
            self.n05_key(target, epoch, token, req, 42, 59, true)?;
        }
        self.n05_key(target, epoch, token, req, scan, android, true)?;
        self.n05_key(target, epoch, token, req, scan, android, false)?;
        if shift {
            self.n05_key(target, epoch, token, req, 42, 59, false)?;
        }
        self.n05_key(target, epoch, token, req, 29, 113, false)?;
        Ok(if shift { 6 } else { 4 })
    }

    fn n05_ascii(
        &mut self,
        target: &Value,
        epoch: u64,
        token: &Value,
        req: &mut u32,
        text: &[u8],
    ) -> Result<u64> {
        let mut delivered = 0;
        for &byte in text {
            let (scan, android, shift) = ascii_key(byte)?;
            if shift {
                self.n05_key(target, epoch, token, req, 42, 59, true)?;
                delivered += 1;
            }
            self.n05_key(target, epoch, token, req, scan, android, true)?;
            self.n05_key(target, epoch, token, req, scan, android, false)?;
            delivered += 2;
            if shift {
                self.n05_key(target, epoch, token, req, 42, 59, false)?;
                delivered += 1;
            }
        }
        Ok(delivered)
    }

    fn n05_step(
        &mut self,
        name: &str,
        target: &Value,
        req: &mut u32,
        input: impl FnOnce(&mut Self, &Value, u64, &Value, &mut u32) -> Result<u64>,
    ) -> Result<Value> {
        let (epoch, token) = self.n05_focus(target, req)?;
        let before = self.pump()?["runtime"]["app"]["input"]["delivered"]
            .as_u64()
            .ok_or("I6_N05_INPUT_FAILED")?;
        let first_request = req.to_string();
        let expected = input(self, target, epoch, &token, req)?;
        let after = self.wait(15, |s| {
            s["runtime"]["app"]["input"]["delivered"]
                .as_u64()
                .is_some_and(|n| n >= before + expected)
        })?;
        let count = after["runtime"]["app"]["input"]["delivered"]
            .as_u64()
            .ok_or("I6_N05_INPUT_FAILED")?;
        if count < before + expected {
            return Err("I6_N05_INPUT_FAILED");
        }
        let proof = json!({"phase":name,"target":target,"epoch":epoch,"token":token,
            "first_key_request":first_request,"next_key_request":req.to_string(),
            "expected_delivery":expected,"delivered_before":before,"delivered_after":count});
        self.observe("n05_step_delivered", proof.clone())?;
        Ok(proof)
    }

    fn n05_fresh_child(app: &Value, main: &Value, previous: &[Value]) -> Option<Value> {
        let admitted = app["gui_windows"].as_array()?;
        let windows = app["windows"].as_array()?;
        windows
            .iter()
            .find(|w| {
                w["parent"] == main["window"]
                    && w["life"] == "Mapped"
                    && w["frame"].is_string()
                    && !previous.contains(&w["window"])
                    && admitted.contains(&w["window"])
            })
            .cloned()
    }

    // MUST be sampled before the key command. A dialog can be created by
    // GTK synchronously during the last key completion, so sampling the
    // "previous" set afterward would silently exclude the actual child.
    fn n05_dialog_baseline(&mut self, main: &Value, phase: &str) -> Result<Vec<Value>> {
        let snapshot = self.pump()?;
        let app = &snapshot["runtime"]["app"];
        let windows = app["windows"].as_array().ok_or("I6_N05_DIALOG_MISSING")?;
        if !windows.iter().any(|w| {
            w["window"] == main["window"] && w["life"] == "Mapped" && w["parent"].is_null()
        }) || !app["gui_windows"]
            .as_array()
            .is_some_and(|g| g.contains(&main["window"]))
        {
            return Err("I6_N05_MAIN_MISSING");
        }
        let previous: Vec<Value> = windows
            .iter()
            .filter(|w| w["life"] == "Mapped")
            .map(|w| w["window"].clone())
            .collect();
        if windows
            .iter()
            .any(|w| w["life"] == "Mapped" && w["parent"] == main["window"])
        {
            return Err("I6_N05_STALE_DIALOG");
        }
        self.observe(
            "n05_dialog_baseline",
            json!({"phase":phase,"main_target":main,"mapped_windows":previous}),
        )?;
        Ok(previous)
    }

    fn n05_dialog(&mut self, main: &Value, name: &str, previous: &[Value]) -> Result<Value> {
        // The snapshot is deliberately sampled AFTER the shortcut; the
        // baseline argument is from BEFORE it and is never overwritten.
        let mapped = self.wait(25, |s| {
            Self::n05_fresh_child(&s["runtime"]["app"], main, previous).is_some()
        })?;
        let window = Self::n05_fresh_child(&mapped["runtime"]["app"], main, previous)
            .ok_or("I6_N05_DIALOG_MISSING")?;
        let win_id = window["window"].clone();
        let deadline = Instant::now() + Duration::from_secs(30);
        let readback = loop {
            self.pump()?;
            let sdk = self.n02_sdk.as_mut().ok_or("I6_N05_DIALOG_MISSING")?;
            match sdk.tick_create_fresh(&self.registry, &self.completed) {
                Ok(Some(r)) if r["target"]["window"] == win_id => break r,
                Ok(Some(_)) => return Err("I6_N05_DIALOG_MISSING"),
                Ok(None) | Err(Status::NotReady | Status::Busy) => (),
                Err(_) => return Err("I6_N05_DIALOG_MISSING"),
            }
            if Instant::now() >= deadline {
                return Err("I6_OUTER_TIMEOUT");
            }
            thread::sleep(Duration::from_millis(10));
        };
        let proof = json!({"name":name,"window":project_window(&window)?,
                         "target":readback["target"],"readback":readback});
        self.observe(
            "n05_dialog_created",
            json!({"name":name,"window":win_id,
            "parent":main["window"],"target":proof["target"]}),
        )?;
        Ok(proof)
    }

    fn n05_closed(&mut self, dialog: &Value, main: &Value) -> Result<()> {
        let win = &dialog["window"]["window"];
        self.wait(30, |s| {
            let app = &s["runtime"]["app"];
            app["windows"].as_array().is_some_and(|windows| {
                !windows
                    .iter()
                    .any(|w| w["window"] == *win && w["life"] == "Mapped")
            }) && app["gui_windows"]
                .as_array()
                .is_some_and(|g| !g.contains(win) && g.contains(&main["window"]))
        })?;
        self.observe(
            "n05_dialog_closed",
            json!({
            "name":dialog["name"],"window":win,"target":dialog["target"]}),
        )?;
        Ok(())
    }

    // GtkFileChooser may resolve the location entry on its first Enter and
    // only activate the Open/Save response on the next key. Every retry is
    // another real Registry dispatch; only actual dialog destruction counts.
    fn n05_confirm(&mut self, dialog: &Value, main: &Value, req: &mut u32) -> Result<()> {
        let target = &dialog["target"];
        let (epoch, token) = self.n05_focus(target, req)?;
        let before = self.pump()?["runtime"]["app"]["input"]["delivered"]
            .as_u64()
            .ok_or("I6_N05_INPUT_FAILED")?;
        let mut dispatched = 0u64;
        for attempt in 1..=3 {
            if attempt < 3 {
                self.n05_key(target, epoch, &token, req, 28, 66, true)?;
                dispatched += 1;
            } else {
                // An explicit dialog mnemonic is the last bounded fallback.
                // It cannot bypass focus/input authorization or cause a file
                // to be written by the supervisor.
                self.n05_key(target, epoch, &token, req, 56, 57, true)?;
                let (scan, android) = if dialog["name"] == "open" {
                    (24, 43)
                } else {
                    (31, 47)
                };
                self.n05_key(target, epoch, &token, req, scan, android, true)?;
                self.n05_key(target, epoch, &token, req, scan, android, false)?;
                self.n05_key(target, epoch, &token, req, 56, 57, false)?;
                dispatched += 4;
            }
            let deadline = Instant::now() + Duration::from_secs(5);
            loop {
                let current = self.pump()?;
                let app = &current["runtime"]["app"];
                let gone = app["windows"].as_array().is_some_and(|windows| {
                    !windows
                        .iter()
                        .any(|w| w["window"] == dialog["window"]["window"] && w["life"] == "Mapped")
                }) && app["gui_windows"]
                    .as_array()
                    .is_some_and(|windows| !windows.contains(&dialog["window"]["window"]));
                if gone {
                    let after = app["input"]["delivered"]
                        .as_u64()
                        .ok_or("I6_N05_INPUT_FAILED")?;
                    if after < before + dispatched {
                        return Err("I6_N05_INPUT_FAILED");
                    }
                    self.n05_closed(dialog, main)?;
                    self.observe(
                        "n05_confirm_delivered",
                        json!({"name":dialog["name"],"target":target,
                            "epoch":epoch,"token":token,"attempts":attempt,
                            "delivered_before":before,"delivered_after":after,
                            "expected_delivery":dispatched}),
                    )?;
                    return Ok(());
                }
                if Instant::now() >= deadline {
                    break;
                }
                thread::sleep(Duration::from_millis(50));
            }
            let still_mapped = self.last_snapshot["runtime"]["app"]["windows"]
                .as_array()
                .is_some_and(|rows| {
                    rows.iter()
                        .any(|w| w["window"] == dialog["window"]["window"] && w["life"] == "Mapped")
                });
            let probe = json!({
                "name":dialog["name"], "attempt":attempt, "target":target,
                "dialog_mapped":still_mapped,
                "delivered":self.last_snapshot["runtime"]["app"]["input"]["delivered"],
            });
            self.observe("n05_confirm_attempt", probe)?;
            if attempt < 3 {
                self.n05_key(target, epoch, &token, req, 28, 66, false)?;
                dispatched += 1;
            }
        }
        Err("I6_N05_DIALOG_CONFIRM_TIMEOUT")
    }

    pub(super) fn exercise_n05(&mut self, run_id: &str, directory: &str) -> Result<()> {
        if !path_valid(directory) {
            return Err("I6_N05_PATH_INVALID");
        }
        self.exercise_n02("mousepad_editor")?;
        self.n05_active = true;
        let main = self.n02_readback.as_ref().ok_or("I6_N05_MAIN_MISSING")?["target"].clone();
        let input = format!("{directory}/input.txt");
        let output = format!("{directory}/saved-as.txt");
        if input.len() > MAX_PATH || output.len() > MAX_PATH {
            return Err("I6_N05_PATH_INVALID");
        }
        let mut req = 5000u32;
        let mut steps = Vec::new();
        let open_previous = self.n05_dialog_baseline(&main, "open")?;
        // N04's proven live Ctrl+O path, but keep the dialog open.
        steps.push(
            self.n05_step("open_shortcut", &main, &mut req, |s, t, e, k, r| {
                s.n05_shortcut(t, e, k, r, 24, 43, false)
            })?,
        );
        let open = self.n05_dialog(&main, "open", &open_previous)?;
        let child = &open["target"];
        steps.push(
            self.n05_step("open_location", child, &mut req, |s, t, e, k, r| {
                s.n05_shortcut(t, e, k, r, 38, 40, false)
            })?,
        );
        // GTK's location entry is created asynchronously after Ctrl+L.
        // Delivery by the compositor alone is not proof that the entry has
        // received focus. Allow the real GTK main loop to expose it first.
        thread::sleep(Duration::from_millis(500));
        steps.push(
            self.n05_step("open_path", child, &mut req, |s, t, e, k, r| {
                s.n05_ascii(t, e, k, r, input.as_bytes())
            })?,
        );
        // The location chooser resolves file paths through GTK/GIO after its
        // input queue drains. This bound does not change any product lease.
        thread::sleep(Duration::from_millis(900));
        // Enter-down can synchronously destroy the file chooser; do not
        // send a stale-target key-up (same rule as N04 Esc-down).
        self.n05_confirm(&open, &main, &mut req)?;
        self.observe("n05_open_confirmed", json!({"target":child}))?;
        steps.push(
            self.n05_step("edit_ctrl_end", &main, &mut req, |s, t, e, k, r| {
                s.n05_shortcut(t, e, k, r, 107, 123, false)
            })?,
        );
        steps.push(
            self.n05_step("edit_append", &main, &mut req, |s, t, e, k, r| {
                s.n05_ascii(t, e, k, r, EDIT)
            })?,
        );
        let save_previous = self.n05_dialog_baseline(&main, "save_as")?;
        steps.push(
            self.n05_step("save_as_shortcut", &main, &mut req, |s, t, e, k, r| {
                s.n05_shortcut(t, e, k, r, 31, 47, true)
            })?,
        );
        let save = self.n05_dialog(&main, "save_as", &save_previous)?;
        let child = &save["target"];
        steps.push(
            self.n05_step("save_location", child, &mut req, |s, t, e, k, r| {
                s.n05_shortcut(t, e, k, r, 38, 40, false)
            })?,
        );
        // GTK's location entry is created asynchronously after Ctrl+L.
        // Delivery by the compositor alone is not proof that the entry has
        // received focus. Allow the real GTK main loop to expose it first.
        thread::sleep(Duration::from_millis(500));
        steps.push(
            self.n05_step("save_path", child, &mut req, |s, t, e, k, r| {
                s.n05_ascii(t, e, k, r, output.as_bytes())
            })?,
        );
        // The location chooser resolves file paths through GTK/GIO after its
        // input queue drains. This bound does not change any product lease.
        thread::sleep(Duration::from_millis(900));
        self.n05_confirm(&save, &main, &mut req)?;
        self.observe("n05_save_confirmed", json!({"target":child}))?;
        self.refresh_trace()?;
        let capture = self
            .trace
            .events
            .iter()
            .find_map(|e| e.capture.as_ref())
            .ok_or("I6_N05_CAPTURE_MISSING")?
            .capture_id
            .clone();
        self.n05 = Some(json!({"schema":1,"run_id":run_id,"capture_id":capture,
            "main_target":main,"directory":directory,
            "input":input,"output":output,"steps":steps,"dialogs":[open,save],
            "final_key_request":req.to_string()}));
        Ok(())
    }

    pub(super) fn receipt_n05(
        &self,
        run_id: &str,
        error: Option<&str>,
        cleanup: Option<&str>,
    ) -> Value {
        let mut result = self.receipt_n02(run_id, "mousepad_editor", error, cleanup);
        result["schema"] = json!("eidolon-i6-n05-native-v1");
        result["variant"] = json!(VARIANT);
        result["n05"] = self.n05.clone().unwrap_or(Value::Null);
        result
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn early_created_dialog_is_not_excluded_by_late_detection() {
        let main = json!({"session":"1","window":"7","scene":"1","generation":"1"});
        let child = json!({"window":"8","parent":"7","life":"Mapped",
                           "frame":"20","app_id":"org.xfce.mousepad"});
        let app = json!({"windows":[
            {"window":"7","parent":null,"life":"Mapped","frame":"19"},
            child.clone()
        ],"gui_windows":["7","8"]});
        // The child already exists at the first post-command snapshot.
        let result = Driver::n05_fresh_child(&app, &main, &[json!("7")]);
        assert_eq!(result, Some(child.clone()));
        // A dialog existing before the shortcut cannot be minted as new.
        assert!(Driver::n05_fresh_child(&app, &main, &[json!("7"), json!("8")]).is_none());
        // Parent, admission and mapped lifetime are all mandatory.
        let mut wrong = app.clone();
        wrong["windows"][1]["parent"] = json!("99");
        assert!(Driver::n05_fresh_child(&wrong, &main, &[json!("7")]).is_none());
        wrong = app.clone();
        wrong["gui_windows"] = json!(["7"]);
        assert!(Driver::n05_fresh_child(&wrong, &main, &[json!("7")]).is_none());
        wrong = app;
        wrong["windows"][1]["life"] = json!("Destroyed");
        assert!(Driver::n05_fresh_child(&wrong, &main, &[json!("7")]).is_none());
    }

    #[test]
    fn fixed_ascii_physicals_not_arbitrary_unicode_or_ime() {
        assert_eq!(EDIT, b"eidolon-i6-saved\n");
        for b in b"/home/eidolon-app/n05-cafe0123/input.txt" {
            assert!(ascii_key(*b).is_ok());
        }
        assert_eq!(ascii_key(b'_').unwrap(), (12, 69, true));
        assert_eq!(ascii_key(b'0').unwrap(), (11, 7, false));
        assert_eq!(ascii_key(b'9').unwrap(), (10, 16, false));
        assert!(ascii_key(0xff).is_err());
        assert!(!path_valid("/tmp/eidolon"));
        assert!(!path_valid("/home/eidolon-app/n05-../other"));
    }
}
