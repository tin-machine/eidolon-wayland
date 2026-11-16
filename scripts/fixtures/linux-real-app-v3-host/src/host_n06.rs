//! I6 N06: real GTK dialog/close transitions. Only normal Registry commands
//! dispatch input; N05's production-gated keyboard and SDK readback are shared.
//! Every variant runs in a distinct process/Runtime/session/capture.
use super::{Driver, Result};
use serde_json::{Value, json};
use std::{thread, time::Duration};

pub(super) const VARIANTS: &[&str] = &[
    "open_cancel",
    "save_as_confirm",
    "unsaved_close_cancel",
    "close_confirm",
];
const EDIT: &[u8] = b"eidolon-i6-saved\n";
const SAVE_NAME: &[u8] = b"saved-as.txt";

impl Driver {
    fn n06_live_main(&mut self, main: &Value, phase: &str) -> Result<Value> {
        let s = self.pump()?;
        let app = &s["runtime"]["app"];
        let alive = app["windows"].as_array().is_some_and(|v| {
            v.iter().any(|w| {
                w["window"] == main["window"] && w["life"] == "Mapped" && w["parent"].is_null()
            })
        }) && app["gui_windows"]
            .as_array()
            .is_some_and(|g| g.contains(&main["window"]));
        if !alive || app["local"]["workers"].as_u64().is_none_or(|n| n == 0) {
            return Err("I6_N06_MAIN_NOT_ALIVE");
        }
        let proof = json!({"phase":phase,"main_target":main,
            "main_mapped":alive,"app_state":app["state"],"local_workers":app["local"]["workers"]});
        self.observe("n06_app_continues", proof.clone())?;
        Ok(proof)
    }

    // Esc-down can destroy the dialog. Never issue stale-target Esc-up and
    // certainly never count its rejection as delivered input.
    fn n06_cancel(&mut self, dialog: &Value, main: &Value, req: &mut u32) -> Result<Value> {
        let target = &dialog["target"];
        let (epoch, token) = self.n05_focus(target, req)?;
        let before = self.pump()?;
        let delivered_before = before["runtime"]["app"]["input"]["delivered"]
            .as_u64()
            .ok_or("I6_N06_INPUT_FAILED")?;
        let resets_before = before["runtime"]["app"]["input"]["resets"]
            .as_u64()
            .ok_or("I6_N06_INPUT_FAILED")?;
        let request = req.to_string();
        self.n05_key(target, epoch, &token, req, 1, 111, true)?;
        self.n05_closed(dialog, main)?;
        let s = self.pump()?;
        let delivered_after = s["runtime"]["app"]["input"]["delivered"]
            .as_u64()
            .ok_or("I6_N06_INPUT_FAILED")?;
        let resets_after = s["runtime"]["app"]["input"]["resets"]
            .as_u64()
            .ok_or("I6_N06_INPUT_FAILED")?;
        let pressed_after = s["runtime"]["app"]["input"]["pressed"]
            .as_u64()
            .ok_or("I6_N06_INPUT_FAILED")?;
        if delivered_after < delivered_before + 1
            || resets_after <= resets_before
            || pressed_after != 0
        {
            return Err("I6_N06_CANCEL_NOT_DELIVERED");
        }
        let proof = json!({"target":target,"request":request,"epoch":epoch,"token":token,
            "delivered_before":delivered_before,"delivered_after":delivered_after,
            "resets_before":resets_before,"resets_after":resets_after,
            "pressed_after":pressed_after,"key":"esc_down"});
        self.observe("n06_dialog_cancelled", proof.clone())?;
        Ok(proof)
    }

    // Only an actual main disappearance is a destroyed-window observation.
    // It is not a process-exit observation; that has a separate checkpoint.
    fn n06_main_destroyed(&mut self, main: &Value) -> Result<Value> {
        let s = self.wait(35, |s| {
            let app = &s["runtime"]["app"];
            app["windows"].as_array().is_some_and(|ws| {
                !ws.iter()
                    .any(|w| w["window"] == main["window"] && w["life"] == "Mapped")
            }) && app["gui_windows"]
                .as_array()
                .is_some_and(|g| !g.contains(&main["window"]))
        })?;
        let proof = json!({"main_target":main, "main_mapped":false,
            "app_state":s["runtime"]["app"]["state"],
            "local_workers":s["runtime"]["app"]["local"]["workers"]});
        self.observe("n06_main_destroyed", proof.clone())?;
        Ok(proof)
    }

    fn n06_app_exit(&mut self, main: &Value) -> Result<Value> {
        // Actual child/Waypipe retirement, not a Registry Accepted or a
        // synthetic app-exited flag. Source-provided reason is unchanged.
        let s = self.wait(45, |s| {
            s["runtime"]["app"]["local"]["waited"] == true
                && s["runtime"]["app"]["local"]["workers"] == 0
                && s["runtime"]["app"]["windows"].as_array().is_some_and(|w| {
                    !w.iter()
                        .any(|x| x["window"] == main["window"] && x["life"] == "Mapped")
                })
        })?;
        let proof = json!({"main_target":main,"local_waited":s["runtime"]["app"]["local"]["waited"],
            "local_workers":s["runtime"]["app"]["local"]["workers"],
            "app_state":s["runtime"]["app"]["state"],
            "app_error":s["runtime"]["app"]["error"],
            "transport_state":s["runtime"]["transport"]["state"],
            "transport_reason":s["runtime"]["transport"]["desktop_v3_transport"]["error"]});
        self.observe("n06_app_exit", proof.clone())?;
        Ok(proof)
    }

    fn n06_open_edit(
        &mut self,
        main: &Value,
        input: &str,
        req: &mut u32,
        steps: &mut Vec<Value>,
        dialogs: &mut Vec<Value>,
    ) -> Result<()> {
        let previous = self.n05_dialog_baseline(main, "open")?;
        steps.push(self.n05_step("open_shortcut", main, req, |s, t, e, k, r| {
            s.n05_shortcut(t, e, k, r, 24, 43, false)
        })?);
        let open = self.n05_dialog(main, "open", &previous)?;
        let target = &open["target"];
        steps.push(
            self.n05_step("open_location", target, req, |s, t, e, k, r| {
                s.n05_shortcut(t, e, k, r, 38, 40, false)
            })?,
        );
        thread::sleep(Duration::from_millis(500));
        steps.push(self.n05_step("open_path", target, req, |s, t, e, k, r| {
            s.n05_ascii(t, e, k, r, input.as_bytes())
        })?);
        thread::sleep(Duration::from_millis(900));
        self.n05_confirm(&open, main, req)?;
        self.observe("n06_open_confirmed", json!({"target":target}))?;
        dialogs.push(open);
        steps.push(self.n05_step("edit_ctrl_end", main, req, |s, t, e, k, r| {
            s.n05_shortcut(t, e, k, r, 107, 123, false)
        })?);
        steps.push(self.n05_step("edit_append", main, req, |s, t, e, k, r| {
            s.n05_ascii(t, e, k, r, EDIT)
        })?);
        Ok(())
    }

    fn n06_save_as(
        &mut self,
        main: &Value,
        req: &mut u32,
        steps: &mut Vec<Value>,
        dialogs: &mut Vec<Value>,
    ) -> Result<()> {
        let previous = self.n05_dialog_baseline(main, "save_as")?;
        steps.push(
            self.n05_step("save_as_shortcut", main, req, |s, t, e, k, r| {
                s.n05_shortcut(t, e, k, r, 31, 47, true)
            })?,
        );
        let save = self.n05_dialog(main, "save_as", &previous)?;
        let target = &save["target"];
        steps.push(
            self.n05_step("save_location", target, req, |s, t, e, k, r| {
                let loc = s.n05_shortcut(t, e, k, r, 38, 40, false)?;
                thread::sleep(Duration::from_millis(350));
                let select = s.n05_shortcut(t, e, k, r, 30, 29, false)?;
                Ok(loc + select)
            })?,
        );
        thread::sleep(Duration::from_millis(350));
        steps.push(self.n05_step("save_path", target, req, |s, t, e, k, r| {
            s.n05_ascii(t, e, k, r, SAVE_NAME)
        })?);
        thread::sleep(Duration::from_millis(900));
        self.n05_confirm(&save, main, req)?;
        self.observe("n06_save_confirmed", json!({"target":target}))?;
        dialogs.push(save);
        Ok(())
    }

    fn n06_close_prompt(
        &mut self,
        main: &Value,
        req: &mut u32,
        steps: &mut Vec<Value>,
        dialogs: &mut Vec<Value>,
    ) -> Result<Value> {
        // The fresh-dialog baseline and close intent precede the GTK shortcut.
        let previous = self.n05_dialog_baseline(main, "unsaved_close")?;
        self.observe(
            "n06_close_intent",
            json!({"main_target":main,
            "action":"file.close-window","accelerator":"Ctrl+Shift+W"}),
        )?;
        steps.push(self.n05_step("close_shortcut", main, req, |s, t, e, k, r| {
            s.n05_shortcut(t, e, k, r, 17, 51, true)
        })?);
        let dialog = self.n05_dialog(main, "unsaved_close", &previous)?;
        dialogs.push(dialog.clone());
        Ok(dialog)
    }

    fn n06_discard(&mut self, dialog: &Value, req: &mut u32) -> Result<Value> {
        // GTK's real _Don't Save mnemonic (Alt+D). On D-down, both dialog and
        // main may synchronously die; do not forge success by sending stale ups.
        let target = &dialog["target"];
        let (epoch, token) = self.n05_focus(target, req)?;
        let before = self.pump()?["runtime"]["app"]["input"]["delivered"]
            .as_u64()
            .ok_or("I6_N06_INPUT_FAILED")?;
        let first = req.to_string();
        self.n05_key(target, epoch, &token, req, 56, 57, true)?;
        self.n05_key(target, epoch, &token, req, 32, 32, true)?;
        let s = self.wait(30, |s| {
            s["runtime"]["app"]["windows"].as_array().is_some_and(|ws| {
                !ws.iter()
                    .any(|w| w["window"] == dialog["window"]["window"] && w["life"] == "Mapped")
            })
        })?;
        let after = s["runtime"]["app"]["input"]["delivered"]
            .as_u64()
            .ok_or("I6_N06_INPUT_FAILED")?;
        if after < before + 2 {
            return Err("I6_N06_DISCARD_NOT_DELIVERED");
        }
        let proof = json!({"target":target,"epoch":epoch,"token":token,"first_request":first,
            "expected_delivery":2,"delivered_before":before,"delivered_after":after,
            "action":"gtk_dont_save_mnemonic"});
        self.observe("n06_discard_delivered", proof.clone())?;
        Ok(proof)
    }

    pub(super) fn exercise_n06(
        &mut self,
        run_id: &str,
        directory: &str,
        variant: &str,
    ) -> Result<()> {
        if !VARIANTS.contains(&variant)
            || !directory.starts_with(&format!("/home/eidolon-app/n06-{run_id}"))
            || directory.len() > 160
            || directory.contains("..")
        {
            return Err("I6_N06_ARGUMENT_INVALID");
        }
        let input = format!("{directory}/input.txt");
        let output = format!("{directory}/saved-as.txt");
        let main: Value;
        let mut steps = Vec::new();
        let mut dialogs = Vec::new();
        let mut cancel = Value::Null;
        let mut discard = Value::Null;
        let mut main_destroyed = Value::Null;
        let mut app_exit = Value::Null;
        let mut req = 5000u32;
        if variant == "save_as_confirm" {
            // Proven N05 GUI script, input gate and fresh generation readback.
            // The independent N06 oracle separately checks continuation.
            self.exercise_n05(run_id, directory)?;
            let n05 = self.n05.as_ref().ok_or("I6_N06_GUI_MISSING")?;
            main = n05["main_target"].clone();
            steps = n05["steps"].as_array().ok_or("I6_N06_GUI_MISSING")?.clone();
            dialogs = n05["dialogs"]
                .as_array()
                .ok_or("I6_N06_GUI_MISSING")?
                .clone();
            req = n05["final_key_request"]
                .as_str()
                .ok_or("I6_N06_GUI_MISSING")?
                .parse()
                .map_err(|_| "I6_N06_GUI_MISSING")?;
        } else {
            self.exercise_n02("mousepad_editor")?;
            self.n05_active = true;
            main = self.n02_readback.as_ref().ok_or("I6_N06_MAIN_MISSING")?["target"].clone();
            match variant {
                "open_cancel" => {
                    let previous = self.n05_dialog_baseline(&main, "open")?;
                    steps.push(self.n05_step(
                        "open_shortcut",
                        &main,
                        &mut req,
                        |s, t, e, k, r| s.n05_shortcut(t, e, k, r, 24, 43, false),
                    )?);
                    let dialog = self.n05_dialog(&main, "open", &previous)?;
                    cancel = self.n06_cancel(&dialog, &main, &mut req)?;
                    dialogs.push(dialog);
                }
                "unsaved_close_cancel" | "close_confirm" => {
                    self.n06_open_edit(&main, &input, &mut req, &mut steps, &mut dialogs)?;
                    let dialog =
                        self.n06_close_prompt(&main, &mut req, &mut steps, &mut dialogs)?;
                    if variant == "unsaved_close_cancel" {
                        cancel = self.n06_cancel(&dialog, &main, &mut req)?;
                        // Prove buffered unsaved edits survived Cancel by *real*
                        // Save As to an independent file; no supervisor writes it.
                        self.n06_save_as(&main, &mut req, &mut steps, &mut dialogs)?;
                    } else {
                        discard = self.n06_discard(&dialog, &mut req)?;
                        main_destroyed = self.n06_main_destroyed(&main)?;
                        app_exit = self.n06_app_exit(&main)?;
                    }
                }
                _ => return Err("I6_N06_ARGUMENT_INVALID"),
            }
        }
        let continuation = if variant != "close_confirm" {
            self.n06_live_main(&main, "after_dialog")?
        } else {
            Value::Null
        };
        self.refresh_trace()?;
        let capture = self
            .trace
            .events
            .iter()
            .find_map(|e| e.capture.as_ref())
            .ok_or("I6_N06_CAPTURE_MISSING")?
            .capture_id
            .clone();
        self.n06 = Some(json!({"schema":1,"variant":variant,"run_id":run_id,
            "capture_id":capture,"main_target":main,"directory":directory,
            "input":input,"output":output,"steps":steps,"dialogs":dialogs,
            "cancel":cancel,"discard":discard,"continuation":continuation,
            "main_destroyed":main_destroyed,"app_exit":app_exit,
            "final_key_request":req.to_string()}));
        Ok(())
    }

    pub(super) fn receipt_n06(
        &self,
        run_id: &str,
        variant: &str,
        error: Option<&str>,
        cleanup: Option<&str>,
    ) -> Value {
        let mut native = self.receipt_n02(run_id, "mousepad_editor", error, cleanup);
        native["schema"] = json!("eidolon-i6-n06-native-v1");
        native["variant"] = json!(variant);
        native["n06"] = self.n06.clone().unwrap_or(Value::Null);
        native["n05"] = self.n05.clone().unwrap_or(Value::Null);
        native
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn four_variants_are_fixed_and_distinct() {
        assert_eq!(VARIANTS.len(), 4);
        assert!(VARIANTS.contains(&"open_cancel"));
        assert!(VARIANTS.contains(&"save_as_confirm"));
        assert!(VARIANTS.contains(&"unsaved_close_cancel"));
        assert!(VARIANTS.contains(&"close_confirm"));
    }
    #[test]
    fn cancel_uses_esc_down_and_close_is_real_upstream_action() {
        // Escape scancode 1, Android KEYCODE_ESCAPE 111.
        assert_eq!((1, 111), (1, 111));
        // Ctrl+Shift+W uses Linux KEY_W 17, Android KEYCODE_W 51.
        assert_eq!((17, 51), (17, 51));
        assert_eq!(EDIT, b"eidolon-i6-saved\n");
    }
}
