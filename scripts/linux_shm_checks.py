"""固定Linux検査のmetadata境界。接続設定やsnapshot本文を証拠へ持ち出さない。"""
import json
import re

MAX_SNAPSHOT = 1024 * 1024
CASES = {"virtual": "linux-shm-probe", "sdk": "linux-shm-sdk", "recover": "linux-shm-recover"}
BOOLS = {
    "gui_enabled", "gpu_verified", "physical_hmd", "virtual_frame_clock", "credential_bound", "probe_ready",
    "remote_cleanup_confirmed", "journal_cleared", "probe_capability_unchanged", "disconnected", "scope_cleared",
    "local_cleaned", "destroyed", "create_had_no_owner", "termux_configuration_unchanged", "normal_runtime_restored",
    "recovery_queried", "receipt_verified", "recovery_not_required", "normal_gui_enabled", "physical_input",
    "runtime_adopted", "sdk_pose_applied", "sdk_successful_draw_observed", "local_native_sdk_cleaned",
    "product_settings_untouched", "old_runtime_destroyed", "probe_had_no_owner", "normal_sdk_restored", "owner_lease_released",
}
COUNTS = {"apk_uid", "remote_uid", "shm_verified_hashes", "shm_copies", "shm_callbacks", "wayland_connections",
          "first_draw_frames", "last_draw_frames", "source_verified_hashes", "target_driven_callbacks"}
IDS = {"probe_runtime", "origin_session", "sdk_entity", "last_content_frame"}
TOKENS = {"reason", "cleanup_reason", "restore_reason", "last_app_error", "last_linux_error", "failure_class",
          "last_app_state", "last_linux_state", "last_remote_phase"}
STAGES = {"preparing", "connecting", "enqueue_linux_shm_sdk_probe", "pattern", "enqueue_disconnect", "disconnecting",
          "restoring", "restoring_sdk", "finished", "rejected", "cleanup_unconfirmed"}


def need(value, reason):
    if not value:
        raise ValueError(reason)


def number(value):
    need(type(value) is int and 0 <= value <= 2**63 - 1, "METADATA_NUMBER")
    return value


def identifier(value):
    need(type(value) is str and re.fullmatch(r"[1-9][0-9]{0,19}", value) and int(value) <= 2**64 - 1, "METADATA_ID")
    return value


def target(value):
    need(type(value) is dict and set(value) == {"session", "window", "scene", "generation"}, "METADATA_TARGET")
    return {key: identifier(item) for key, item in value.items()}


def decode(raw):
    need(type(raw) is str and len(raw.encode("utf-8")) <= MAX_SNAPSHOT, "METADATA_LIMIT")
    def unique(pairs):
        out = {}
        for key, value in pairs:
            need(key not in out, "METADATA_DUPLICATE_KEY")
            out[key] = value
        return out
    def invalid(_):
        raise ValueError("METADATA_NONFINITE")
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def metadata(check, case, nonce, uid):
    if type(check) is not dict or check.get("case") != CASES[case] or check.get("nonce") != nonce:
        return None
    need(check.get("result") in {"running", "restoring", "pass", "not_required", "fail", "cancelled", "cleanup_unconfirmed"}, "METADATA_RESULT")
    need(type(check.get("apk_uid")) is int and check["apk_uid"] == uid, "APK_UID_CHANGED")
    out = {key: check[key] for key in ("case", "nonce", "result")}
    for key in BOOLS & check.keys():
        need(type(check[key]) is bool, "METADATA_BOOLEAN")
        out[key] = check[key]
    for key in COUNTS & check.keys():
        out[key] = number(check[key])
    for key in IDS & check.keys():
        out[key] = None if check[key] is None else identifier(check[key])
    for key in TOKENS & check.keys():
        item = check[key]
        need(item is None or type(item) is str and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", item), "METADATA_TOKEN")
        out[key] = item
    if "journal_pending" in check:
        need(check["journal_pending"] is None or type(check["journal_pending"]) is bool, "METADATA_BOOLEAN")
        out["journal_pending"] = check["journal_pending"]
    if "stage" in check:
        need(check["stage"] in STAGES, "METADATA_STAGE")
        out["stage"] = check["stage"]
    if "sdk_target" in check:
        out["sdk_target"] = target(check["sdk_target"])
    # Only fixed error/counter fields from SDK failure metadata. Never save arbitrary nested JSON.
    if "failure_metadata" in check:
        value = check["failure_metadata"]
        need(type(value) is dict, "METADATA_FAILURE")
        out["failure_metadata"] = {}
        for key in ("app_error", "transport_error", "linux_error", "render_error"):
            item = value.get(key)
            need(item is None or type(item) is str and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", item), "METADATA_TOKEN")
            out["failure_metadata"][key] = item
    return out


def inspect(raw, *, case, nonce, uid, pid, version, digest):
    value = decode(raw)
    need(type(value) is dict and type(value.get("schema")) is int and value["schema"] == 1, "SNAPSHOT_SCHEMA")
    need(type(value.get("process_id")) is int and value["process_id"] == pid, "APK_PROCESS_CHANGED")
    build = value.get("build_info", {})
    need(type(build) is dict and build.get("profile") == "debug" and build.get("target") == "aarch64-linux-android"
         and type(build.get("apk_version_code")) is int and build["apk_version_code"] == version
         and build.get("source_digest") == digest, "APK_SOURCE_VERSION_CHANGED")
    sequence = identifier(value.get("sequence"))
    check = metadata(value.get("check"), case, nonce, uid)
    envelope = value.get("runtime", {})
    runtime = envelope.get("runtime", {}) if type(envelope) is dict else {}
    transport = runtime.get("transport", {})
    counts = envelope.get("registry", {})
    normal = (type(envelope.get("code")) is int and envelope["code"] == 0 and runtime.get("state") == "Ready" and runtime.get("error") is None
              and transport.get("state") == "Idle" and type(transport.get("owned_connections")) is int and transport["owned_connections"] == 0
              and transport.get("pending_cleanup") == [] and type(counts) is dict
              and all(type(counts.get(key)) is int and counts[key] == 1 for key in ("runtimes", "workers")))
    handle = identifier(envelope["handle"]) if "handle" in envelope else None
    rows = runtime.get("render", {}).get("targets", [])
    sdk = value.get("adapter", {}).get("targets", [])
    sdk_ready = False
    if (normal and runtime.get("activity_visible") is True and runtime.get("xr_state") == "FOCUSED"
            and len(rows) == 1 and len(sdk) == 1 and rows[0].get("role") == sdk[0].get("role") == "diagnostic"
            and type(rows[0].get("frames")) is int and rows[0]["frames"] >= 3
            and type(runtime.get("shutdown", {}).get("sdk_bindings")) is int and runtime["shutdown"]["sdk_bindings"] == 1):
        key = target(rows[0].get("key"))
        sdk_ready = (key == target(sdk[0].get("target")) and key["scene"] == runtime.get("scene")
                     and sdk[0].get("retiring") is False and sdk[0].get("sdk_disposed") is False
                     and sdk[0].get("disposal_failed") is False)
    return dict(sequence=sequence, handle=handle, check=check, normal_native_ready=normal, normal_sdk_ready=sdk_ready)


def cleaned(observed, case, previous):
    check = observed.get("check") or {}
    if check.get("result") not in {"pass", "not_required", "fail", "cancelled"}:
        return False
    normal = (observed["normal_native_ready"] and observed["handle"] is not None
              and int(observed["handle"]) > int(previous) and check.get("normal_runtime_restored") is True)
    if case == "sdk":
        return (normal and observed["normal_sdk_ready"] and check.get("stage") == "finished"
                and all(check.get(key) is True for key in ("normal_sdk_restored", "owner_lease_released"))
                and (check.get("old_runtime_destroyed") is True or check.get("probe_had_no_owner") is True))
    return (normal and check.get("destroyed") is True
            and check.get("probe_runtime") != observed["handle"])


def outcome(observed, case, previous):
    check = observed["check"]
    if check is None or check["result"] in {"running", "restoring"}:
        return None
    if check["result"] in {"fail", "cancelled", "cleanup_unconfirmed"}:
        return check["result"]
    # A case's preliminary pass cannot stand in for completion of native/SDK restoration.
    if not cleaned(observed, case, previous):
        return None
    need(check.get("physical_hmd") is False, "ACCEPTANCE_SCOPE_CHANGED")
    if case == "sdk":
        need(check.get("result") == "pass" and check.get("normal_gui_enabled") is False
             and check.get("physical_input") is False and check.get("virtual_frame_clock") is False, "ACCEPTANCE_SCOPE_CHANGED")
        need(all(check.get(key) is True for key in ("product_settings_untouched", "runtime_adopted", "probe_ready", "sdk_pose_applied", "sdk_successful_draw_observed",
             "remote_cleanup_confirmed", "journal_cleared", "disconnected", "scope_cleared", "local_native_sdk_cleaned")), "SDK_PROOF_INCOMPLETE")
        need(check.get("source_verified_hashes") == 100 and check.get("target_driven_callbacks") == 100
             and 0 < check.get("first_draw_frames", 0) < check.get("last_draw_frames", 0)
             and check.get("sdk_target") is not None and check.get("sdk_entity") is not None
             and int(check.get("last_content_frame") or "0") > 0, "SDK_DRAW_INCOMPLETE")
        return "pass"
    need(check.get("gui_enabled") is False and check.get("gpu_verified") is False
         and check.get("virtual_frame_clock") is (case == "virtual"), "ACCEPTANCE_SCOPE_CHANGED")
    need(all(check.get(key) is True for key in ("termux_configuration_unchanged", "credential_bound", "probe_ready", "disconnected", "scope_cleared", "local_cleaned", "journal_cleared")), "NATIVE_PROOF_INCOMPLETE")
    if case == "recover":
        absent = (check.get("recovery_not_required") is True and check.get("recovery_queried") is False
                  and check.get("receipt_verified") is False and check.get("remote_cleanup_confirmed") is False)
        if check["result"] == "not_required":
            need(absent, "RECOVERY_PROOF_INCONSISTENT")
            return "not_required"
        need(check.get("recovery_not_required") is False and all(check.get(key) is True for key in
             ("recovery_queried", "receipt_verified", "remote_cleanup_confirmed")), "RECOVERY_PROOF_INCONSISTENT")
    else:
        need(check["result"] == "pass" and all(check.get(key) is True for key in ("remote_cleanup_confirmed", "probe_capability_unchanged"))
             and check.get("journal_pending") is False and all(check.get(key) == 100 for key in ("shm_verified_hashes", "shm_copies", "shm_callbacks"))
             and check.get("wayland_connections", 0) >= 2, "SHM_PROOF_INCOMPLETE")
    return "pass"
