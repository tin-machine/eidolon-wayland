//! Polling views of one authoritative runtime cut. Never persist these as diagnostics.
//! Both readers keep the actual target keys, scene, command and retirement evidence.
use eidolon_core::diagnostics::Snapshot;
use serde_json::{Value, json};

pub(crate) fn project(state: &Snapshot, panel: bool) -> Value {
    let tx = &state.transport;
    let render = &state.render;
    let targets: Vec<_> = render
        .targets
        .iter()
        .map(|target| {
            json!({
                "key": target.key, "role": target.role, "frames": target.frames,
                "content_frame": target.content_frame, "content_updates": target.content_updates,
                "presentation_paused": target.presentation_paused,
            })
        })
        .collect();
    let app = state.app.as_ref().map(|app| {
        json!({
            "session": app.session, "state": app.state, "mode": app.mode, "nonce": app.nonce,
            "ready": app.ready, "windows": app.windows, "input": app.input, "error": app.error,
            "termux_ime": app.termux_ime,
            "gui_windows": app.gui_windows, "remote_gui_verified": app.remote_gui_verified,
        })
    });
    let mut transport = json!({
        "state": tx.state, "profile_id": tx.profile_id, "session": tx.session,
        "desktop_pending": tx.desktop_pending, "desktop_v3_transport": tx.desktop_v3_transport,
    });
    let mut drawing = json!({
        "mode": render.mode, "paused": render.paused, "targets": targets,
        "theme_revision": render.theme_revision,
    });
    if panel {
        drawing["detached"] = json!(render.detached);
        drawing["workers"] = json!(render.workers);
        drawing["contexts"] = json!(render.contexts);
        if !state.panels.is_empty() {
            drawing["window_controls"] = json!(render.window_controls);
        }
    } else {
        drawing["context_rebuilds"] = json!(render.context_rebuilds);
        drawing["error"] = json!(render.error);
        transport["owned_connections"] = json!(tx.owned_connections);
        transport["termux_shelf_readable"] = json!(tx.termux_shelf_readable());
        transport["error"] = json!(tx.error);
        transport["desktop_reboot_needed"] = json!(tx.desktop_reboot_needed);
        transport["desktop_v3_recovery"] = json!(tx.desktop_v3_recovery);
        transport["pending_cleanup"] = json!(tx.pending_cleanup);
        transport["desktop"] = json!(tx.desktop);
        transport["app_forward"] = json!(tx.app_forward);
        transport["systemd_chrome"] = json!(tx.systemd_chrome);
        transport["systemd_last_stop"] = json!(tx.systemd_last_stop);
    }
    let mut runtime = json!({
        "state": state.state, "scene": state.scene, "xr_state": state.xr_state,
        "activity_visible": state.activity_visible, "input_resets": state.input_resets,
        "anchor_preparing_target": state.anchor_preparing_target,
        "anchor_placement_target": state.anchor_placement_target,
        "panels": state.panels, "workspace": state.workspace, "window_groups": state.window_groups,
        "desktop_v3_sdk_target": state.desktop_v3_sdk_target,
        "desktop_v3_presentation": state.desktop_v3_presentation,
        "desktop_v3_finalizer_pending": state.desktop_v3_finalizer_pending,
        "desktop_v3_cleanup_error": state.desktop_v3_cleanup_error,
        "reclamation": state.reclamation,
    });
    runtime["app"] = app.unwrap_or(Value::Null);
    runtime["render"] = drawing;
    runtime["transport"] = transport;
    if panel {
        runtime["shutdown"] = json!({
            "requested": state.shutdown.requested, "pending_owners": state.shutdown.pending_owners,
        });
    } else {
        runtime["app_sdk_bindings"] = json!(state.app_sdk_bindings);
        runtime["window_launch"] = json!(state.window_launch);
        runtime["window_slots"] = json!(state.window_slots);
        runtime["theme"] = json!(state.theme);
    }
    runtime
}

#[cfg(test)]
mod tests {
    use super::*;
    use eidolon_model::{ids::FrameSeq, surface::TargetKey};
    use eidolon_render::{GpuInfo, RenderMode, TargetReport};

    #[test]
    fn both_readers_get_the_current_generation_and_only_the_panel_gets_detach_evidence() {
        let mut cut = Snapshot::default();
        let key = TargetKey::from_raw(4, 3, 2, 7).unwrap();
        cut.render.targets.push(TargetReport {
            key,
            frames: 18,
            presentation_paused: true,
            player_clock_ms: None,
            content_frame: Some(FrameSeq::new(8).unwrap()),
            content_updates: 3,
            role: RenderMode::Windows,
            canvas: None,
            content_size: None,
            decoration_frames: 0,
            io_draw: None,
            frame_timing: Default::default(),
            gpu: GpuInfo::default(),
        });
        cut.render.detached.push_back(key);
        cut.shutdown.requested = true;
        cut.shutdown.pending_owners.push("render");
        let full = json!(cut);
        for panel in [true, false] {
            let ui = project(&cut, panel);
            for field in [
                "key",
                "role",
                "frames",
                "content_frame",
                "content_updates",
                "presentation_paused",
            ] {
                assert_eq!(
                    ui["render"]["targets"][0][field],
                    full["render"]["targets"][0][field]
                );
            }
            assert!(ui["render"]["targets"][0].get("frame_timing").is_none());
            assert_eq!(ui.get("theme").is_some(), !panel);
            if panel {
                assert_eq!(ui["render"]["detached"], full["render"]["detached"]);
                assert_eq!(
                    ui["shutdown"]["pending_owners"],
                    full["shutdown"]["pending_owners"]
                );
                assert_eq!(ui["shutdown"]["requested"], true);
            }
        }
    }

    #[test]
    fn launcher_reader_keeps_current_systemd_admission_and_retirement() {
        let mut cut = Snapshot::default();
        cut.transport.systemd_chrome =
            Some(eidolon_transport::linux_desktop::systemd::Report::available());
        cut.transport.systemd_last_stop = Some("SYSTEMD_STOP_REQUESTED");
        let product = project(&cut, false);
        assert_eq!(product["transport"]["systemd_chrome"]["phase"], "available");
        assert_eq!(
            product["transport"]["systemd_last_stop"],
            "SYSTEMD_STOP_REQUESTED"
        );
        assert!(
            project(&cut, true)["transport"]
                .get("systemd_chrome")
                .is_none()
        );
    }
}
