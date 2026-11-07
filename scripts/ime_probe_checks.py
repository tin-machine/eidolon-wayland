"""IME比較probeのmetadata境界。文字本文と受入れの偽PASSを保存前に拒否する。"""
import json
import re

MAX_SNAPSHOT_BYTES = 1024 * 1024
MAX_PROBE_BYTES = 131072  # 15 focus操作＋9 tap操作の前後観測。全体snapshotは別に1MiB制限。
ACCEPTANCE = dict(actual_ime="not_verified", physical_input="not_verified",
                  hmd="not_verified", pointer_recovery="not_verified")
OPERATIONS = {"created", "openConnection", "reset", "closeConnection", "beginBatchEdit", "endBatchEdit",
              "setComposingText", "commitText", "finishComposingText", "setSelection", "setComposingRegion",
              "deleteSurroundingText", "deleteSurroundingTextInCodePoints", "getTextBeforeCursor",
              "getTextAfterCursor", "getSelectedText", "sendKeyEvent", "performEditorAction",
              "performContextMenuAction", "getExtractedText", "getSurroundingText", "replaceText", "requestCursorUpdates"}
FAILURES = {"batch_limit", "unbalanced_batch", "payload_limit", "committed_cursor_unavailable",
            "selection_unavailable", "invalid_utf16_boundary", "invalid_length", "surrounding_unavailable",
            "invalid_text", "sink_rejected", "stale_connection", "unsupported", "key_route_unavailable",
            "action_route_unavailable", "cursor_updates_unavailable", "key_limit"}
STOCK_CALLBACKS = {"create_connection", "set_composing_text", "set_composing_text_attribute", "commit_text",
                   "commit_text_attribute", "finish_composing_text", "set_composing_region", "set_selection",
                   "begin_batch_edit", "end_batch_edit", "delete_surrounding_utf16", "delete_surrounding_codepoints",
                   "get_text_before", "get_text_after", "get_selected_text", "get_extracted_text",
                   "request_cursor_updates", "request_cursor_updates_filter", "send_key_event", "editor_action",
                   "close_connection"}
CONTROLS = {"focus", "show", "hide", "clear_focus", "reset", "restart"}
EXERCISE_STEPS = [(editor, operation) for editor in "ABC"
                  for operation in ("focus", "show", "hide", "restart", "clear_focus")]
SDK_TAP_STEPS = [(editor, operation) for editor in "ABC" for operation in ("prepare", "tap", "show_if_focused")]
TOUCH_KEYS = {f"panel_{panel}_touch_{action}" for panel in (0, 1) for action in ("down", "up")}
COUNT_KEYS = ({f"{editor}_{callback}" for editor in "AB" for callback in STOCK_CALLBACKS}
              | {f"{editor}_focus_{state}" for editor in "ABC" for state in ("gain", "loss")}
              | {f"panel_{panel}_{event}" for panel in (0, 1)
                 for event in ("touch_down", "touch_up", "hover_enter", "scroll", "button", "attached")}
              | {f"C_metadata_{operation}" for operation in OPERATIONS}
              | {f"operation_{name}" for name in CONTROLS}
              | {"C_transaction", "C_commit", "C_preedit", "C_returned_key", "C_editor_action", "operation_failed"})


def reject(code="INVALID_PROBE_METADATA"):
    # untrusted JSONの値やkeyを例外messageへ転載しない。
    raise ValueError(code)


def integer(value):
    if type(value) is not int or not -1 <= value <= 2**63 - 1:
        reject()
    return value


def nonnegative(value):
    value = integer(value)
    if value < 0:
        reject()
    return value


def signed32(value):
    if type(value) is not int or not -(2**31) <= value < 2**31:
        reject()
    return value


def boolean(value):
    if type(value) is not bool:
        reject()
    return value


def choice(*values):
    def validate(value):
        if type(value) is not str or value not in values:
            reject()
        return value
    return validate


def pattern(regex, limit=128):
    def validate(value):
        if type(value) is not str or len(value) > limit or not re.fullmatch(regex, value):
            reject()
        return value
    return validate


def nullable(validate):
    return lambda value: None if value is None else validate(value)


def fields(value, schema, required=()):
    if type(value) is not dict or set(value) - schema.keys() or not set(required) <= value.keys():
        reject()
    return {key: schema[key](item) for key, item in value.items()}


def counters(value):
    if type(value) is not dict or set(value) - COUNT_KEYS:
        reject()
    return {key: nonnegative(count) for key, count in value.items()}


def synthetic(value):
    proof_keys = ("stock_fixed_text_match", "custom_commit_once", "preedit_observed_and_cleared", "stale_connection_rejected")
    result = fields(value, {
        "result": choice("not_run", "running", "pass", "fail"),
        "input_source": choice("direct_input_connection_fixture"),
        "actual_ime": boolean, "physical_keyboard": boolean,
        "failure_class": pattern(r"[A-Za-z_$][A-Za-z0-9_$]*", 96),
        **{key: boolean for key in proof_keys},
    }, ("result",))
    if any(key in result and result[key] is not False for key in ("actual_ime", "physical_keyboard")):
        reject("SYNTHETIC_SCOPE_OVERCLAIM")
    if result["result"] != "not_run":
        if (result.get("input_source") != "direct_input_connection_fixture"
                or result.get("actual_ime") is not False or result.get("physical_keyboard") is not False):
            reject("SYNTHETIC_SCOPE_OVERCLAIM")
    if result["result"] == "pass" and (any(result.get(key) is not True for key in proof_keys) or "failure_class" in result):
        reject("INCOMPLETE_SYNTHETIC_PROOF")
    return result


def editors(value):
    schema = {
        "editor": choice("A", "B", "C"), "display": integer,
        **{key: boolean for key in ("attached", "window_token", "view_focus", "window_focus", "imm_active_view",
                                   "imm_accepting_text", "connection_active")},
        "ime_insets_visible": nullable(boolean),
        **{key: nonnegative for key in ("utf16_length", "utf8_bytes", "input_type", "ime_options", "generation",
                                       "sequence", "batch_depth", "accepted_transactions", "rejected_operations", "stale_operations")},
        **{key: integer for key in ("selection_start", "selection_end", "composing_start", "composing_end")},
        "last_operation": choice(*OPERATIONS), "last_failure": nullable(choice(*FAILURES)),
    }
    if type(value) is not list or len(value) > 3:
        reject()
    result = [fields(item, schema, ("editor", "display", "attached", "window_focus")) for item in value]
    if len({item["editor"] for item in result}) != len(result):
        reject()
    return result


def panels(value):
    if type(value) is not list or len(value) > 2:
        reject()
    result = [fields(item, {"panel": nonnegative, "window_flags": signed32, "window_focus": boolean},
                     ("panel", "window_flags", "window_focus")) for item in value]
    if len({item["panel"] for item in result}) != len(result) or any(item["panel"] not in (0, 1) for item in result):
        reject()
    return result


def activity(value):
    return fields(value, {"display": integer, "window_focus": boolean,
                         "imm_accepting_text": nullable(boolean), "window_flags": signed32})


def exercise_observation(value, *, step=False):
    schema = {"elapsed_ms": nonnegative, "editors": editors, "panels": panels, "activity": activity,
              "framework_connection_callbacks": lambda item: fields(item, {key: nonnegative for key in "ABC"}, "ABC")}
    if step:
        schema.update(step=nonnegative, editor=choice("A", "B", "C"),
                      operation=choice("focus", "show", "hide", "restart", "clear_focus"),
                      operation_returned=nullable(boolean))
    result = fields(value, schema, schema)
    if ({row["editor"] for row in result["editors"]} != set("ABC")
            or {row["panel"] for row in result["panels"]} != {0, 1}):
        reject("INCOMPLETE_EXERCISE_OBSERVATION")
    required = {"window_token", "view_focus", "window_focus", "imm_active_view", "imm_accepting_text", "ime_insets_visible"}
    if any(not required <= row.keys() for row in result["editors"]):
        reject("INCOMPLETE_EXERCISE_OBSERVATION")
    if set(result["activity"]) != {"display", "window_focus", "imm_accepting_text", "window_flags"}:
        reject("INCOMPLETE_EXERCISE_OBSERVATION")
    return result


def exercise(value):
    def steps(rows):
        if type(rows) is not list or len(rows) > len(EXERCISE_STEPS):
            reject("INVALID_EXERCISE_SEQUENCE")
        parsed = [exercise_observation(row, step=True) for row in rows]
        for index, row in enumerate(parsed):
            if (row["step"] != index + 1 or (row["editor"], row["operation"]) != EXERCISE_STEPS[index]):
                reject("INVALID_EXERCISE_SEQUENCE")
        return parsed

    result = fields(value, {
        "result": choice("not_run", "running", "completed", "blocked", "fail"),
        "sequence": choice("focus-show-hide-restart-clear-v1"), "input_source": choice("framework_focus_requests"),
        "text_injected": boolean, "steps": steps, "cleanup_requested": boolean, "cleanup_failed": boolean,
        "cleanup_observation": exercise_observation,
        "reason": choice("sequence_finished", "exercise_timeout", "exercise_exception", "probe_stopped"),
        "failure_class": pattern(r"[A-Za-z_$][A-Za-z0-9_$]*", 96),
    }, ("result",))
    if result["result"] == "not_run":
        if set(result) != {"result"}:
            reject("INVALID_EXERCISE_SEQUENCE")
        return result
    if (result.get("text_injected") is not False or result.get("input_source") != "framework_focus_requests"
            or result.get("sequence") != "focus-show-hide-restart-clear-v1"
            or not {"steps", "cleanup_requested", "cleanup_failed"} <= result.keys()):
        reject("EXERCISE_SCOPE_OVERCLAIM")
    observations = result["steps"] + ([result["cleanup_observation"]] if "cleanup_observation" in result else [])
    for previous, current in zip(observations, observations[1:]):
        if (current["elapsed_ms"] < previous["elapsed_ms"] or any(
                current["framework_connection_callbacks"][key] < previous["framework_connection_callbacks"][key] for key in "ABC")):
            reject("INVALID_EXERCISE_SEQUENCE")
    if result["result"] == "completed" and (
            len(result["steps"]) != len(EXERCISE_STEPS) or result.get("cleanup_requested") is not True
            or result.get("cleanup_failed") is not False or "cleanup_observation" not in result
            or result.get("reason") != "sequence_finished" or "failure_class" in result):
        reject("INCOMPLETE_EXERCISE_PROOF")
    return result


def sdk_tap_observation(value):
    if type(value) is not dict or "panel_touch_callbacks" not in value:
        reject("INCOMPLETE_SDK_TAP_OBSERVATION")
    observation = exercise_observation({key: item for key, item in value.items() if key != "panel_touch_callbacks"})
    observation["panel_touch_callbacks"] = fields(value["panel_touch_callbacks"],
                                                 {key: nonnegative for key in TOUCH_KEYS}, TOUCH_KEYS)
    return observation


def tap_geometry(value):
    keys = ("panel", "display", "display_width", "display_height", "left", "top", "width", "height", "center_x", "center_y")
    result = fields(value, {key: signed32 for key in keys}, keys)
    if (result["panel"] not in (0, 1) or result["display"] < 0
            or any(result[key] <= 0 for key in ("display_width", "display_height", "width", "height"))
            or result["center_x"] != result["left"] + result["width"] // 2
            or result["center_y"] != result["top"] + result["height"] // 2
            or not 0 <= result["center_x"] < result["display_width"]
            or not 0 <= result["center_y"] < result["display_height"]):
        reject("INVALID_SDK_TAP_GEOMETRY")
    return result


def sdk_tap(value):
    def steps(rows):
        if type(rows) is not list or len(rows) > len(SDK_TAP_STEPS):
            reject("INVALID_SDK_TAP_SEQUENCE")
        parsed = []
        for index, row in enumerate(rows):
            item = fields(row, {
                "step": nonnegative, "editor": choice("A", "B", "C"),
                "operation": choice("prepare", "tap", "show_if_focused"),
                "before": sdk_tap_observation, "after": sdk_tap_observation,
                "route": choice("sdk_panel_dispatch_event", "framework"),
                "down_submitted": boolean, "up_submitted": boolean, "show_returned": nullable(boolean),
                "outcome": choice("cleanup_requested", "dispatch_submitted", "show_requested", "show_skipped_no_focus",
                                  "bounds_unavailable", "operation_exception"),
                "geometry": tap_geometry, "failure_class": pattern(r"[A-Za-z_$][A-Za-z0-9_$]*", 96),
            }, ("step", "editor", "operation", "before", "after", "route", "down_submitted", "up_submitted", "show_returned", "outcome"))
            editor, operation = SDK_TAP_STEPS[index]
            if (item["step"] != index + 1 or (item["editor"], item["operation"]) != (editor, operation)
                    or item["route"] != ("sdk_panel_dispatch_event" if operation == "tap" else "framework")):
                reject("INVALID_SDK_TAP_SEQUENCE")
            if operation != "tap" and (item["down_submitted"] or item["up_submitted"] or "geometry" in item):
                reject("INVALID_SDK_TAP_SEQUENCE")
            if operation != "show_if_focused" and item["show_returned"] is not None:
                reject("INVALID_SDK_TAP_SEQUENCE")
            geometry = item.get("geometry")
            if geometry:
                before_editor = next(row for row in item["before"]["editors"] if row["editor"] == editor)
                if geometry["panel"] != (1 if editor == "C" else 0) or geometry["display"] != before_editor["display"]:
                    reject("INVALID_SDK_TAP_GEOMETRY")
            outcome = item["outcome"]
            if outcome == "operation_exception":
                if "failure_class" not in item:
                    reject("INVALID_SDK_TAP_SEQUENCE")
            elif "failure_class" in item:
                reject("INVALID_SDK_TAP_SEQUENCE")
            elif operation == "prepare":
                if outcome != "cleanup_requested": reject("INVALID_SDK_TAP_SEQUENCE")
            elif operation == "tap":
                if outcome == "dispatch_submitted":
                    if not item["down_submitted"] or not item["up_submitted"] or not geometry:
                        reject("INCOMPLETE_SDK_TAP_DISPATCH")
                elif outcome != "bounds_unavailable" or item["down_submitted"] or item["up_submitted"] or geometry:
                    reject("INVALID_SDK_TAP_SEQUENCE")
            else:
                before_editor = next(row for row in item["before"]["editors"] if row["editor"] == editor)
                focused = before_editor["window_focus"] and before_editor["view_focus"]
                if (outcome != ("show_requested" if focused else "show_skipped_no_focus")
                        or (type(item["show_returned"]) is not bool if focused else item["show_returned"] is not None)):
                    reject("INVALID_SDK_TAP_SEQUENCE")
            parsed.append(item)
        return parsed

    result = fields(value, {
        "result": choice("not_run", "running", "completed", "blocked", "fail"),
        "sequence": choice("prepare-sdk-tap-focused-show-v1"), "input_source": choice("sdk_panel_dispatch_event"),
        "text_injected": boolean, "steps": steps, "cleanup_requested": boolean, "cleanup_failed": boolean,
        "cleanup_observation": sdk_tap_observation,
        "reason": choice("sequence_finished", "sdk_tap_timeout", "sdk_tap_step_failed", "sdk_tap_exception", "probe_stopped"),
        "failure_class": pattern(r"[A-Za-z_$][A-Za-z0-9_$]*", 96),
    }, ("result",))
    if result["result"] == "not_run":
        if set(result) != {"result"}: reject("INVALID_SDK_TAP_SEQUENCE")
        return result
    if (result.get("text_injected") is not False or result.get("input_source") != "sdk_panel_dispatch_event"
            or result.get("sequence") != "prepare-sdk-tap-focused-show-v1"
            or not {"steps", "cleanup_requested", "cleanup_failed"} <= result.keys()):
        reject("SDK_TAP_SCOPE_OVERCLAIM")
    observations = [step[phase] for step in result["steps"] for phase in ("before", "after")]
    if "cleanup_observation" in result: observations.append(result["cleanup_observation"])
    for previous, current in zip(observations, observations[1:]):
        if (current["elapsed_ms"] < previous["elapsed_ms"] or any(
                current[field][key] < previous[field][key]
                for field in ("framework_connection_callbacks", "panel_touch_callbacks") for key in current[field])):
            reject("INVALID_SDK_TAP_SEQUENCE")
    if result["result"] == "completed" and (
            len(result["steps"]) != len(SDK_TAP_STEPS) or result.get("cleanup_requested") is not True
            or result.get("cleanup_failed") is not False or "cleanup_observation" not in result
            or result.get("reason") != "sequence_finished" or "failure_class" in result
            or any(step["outcome"] in {"operation_exception", "bounds_unavailable"} for step in result["steps"])):
        reject("INCOMPLETE_SDK_TAP_PROOF")
    return result


def probe_metadata(value, nonce):
    if type(value) is not dict or value.get("case") != "ime-editor-probe" or value.get("nonce") != nonce:
        return None
    if len(json.dumps(value, ensure_ascii=True).encode("utf-8")) > MAX_PROBE_BYTES:
        reject("PROBE_LIMIT")
    if value.get("acceptance") != ACCEPTANCE or value.get("linux_delivery_connected") is not False:
        reject("PROBE_ACCEPTANCE_OVERCLAIM")
    result = fields(value, {
        "case": choice("ime-editor-probe"), "nonce": pattern(r"[0-9a-f]{32}", 32),
        "result": choice("running", "completed", "blocked", "fail"), "scope": choice("android_editor_fixture"),
        "linux_delivery_connected": boolean,
        "acceptance": lambda item: fields(item, {key: choice("not_verified") for key in ACCEPTANCE}, ACCEPTANCE),
        "synthetic": synthetic, "exercise": exercise, "sdk_tap": sdk_tap,
        "stage": choice("panels_pending", "awaiting_manual"), "elapsed_ms": nonnegative,
        "editors": editors, "counts": counters, "last_lengths_utf16": counters,
        "physical_key_events_observed": nonnegative, "operation": choice("start", "button", *CONTROLS),
        "operation_returned": nullable(boolean),
        "activity": activity,
        "panels": panels, "selected_ime": pattern(r"unknown|[A-Za-z0-9_.$]+/[A-Za-z0-9_.$]+", 512),
        "ime_subtype": nullable(lambda item: fields(item, {
            "hash_code": signed32,
            "language_tag": pattern(r"[A-Za-z0-9-]*", 64), "mode": pattern(r"[A-Za-z0-9_-]*", 32)})),
        "failure_class": pattern(r"[A-Za-z_$][A-Za-z0-9_$]*", 96),
        "reason": choice("scene_closed", "operator_stop", "panel_stop", "probe_timeout", "panel_attach_timeout", "probe_exception"),
        **{key: boolean for key in ("cleanup_failed", "panels_destroy_requested", "views_detached_observed", "final_snapshot_unavailable")},
    }, ("case", "nonce", "result", "scope", "linux_delivery_connected", "acceptance", "synthetic"))
    if result["result"] == "completed" and (result.get("cleanup_failed") is not False
                                            or result.get("panels_destroy_requested") is not True):
        reject("INCOMPLETE_STOP_PROOF")
    if result.get("exercise", {}).get("result") == "running" and result.get("sdk_tap", {}).get("result") == "running":
        reject("CONCURRENT_PROBE_SEQUENCE")
    return result


def parse_receipt(raw, nonce):
    if type(raw) is not str or len(raw.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        reject("RECEIPT_LIMIT")

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                reject("DUPLICATE_JSON_KEY")
            value[key] = item
        return value

    snapshot = json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: reject("INVALID_JSON_NUMBER"))
    if type(snapshot) is not dict:
        reject("INVALID_SNAPSHOT")
    return probe_metadata(snapshot.get("check"), nonce)


def action_outcome(action, check):
    """Noneは待機継続。返却値は操作/取得の範囲であり、日本語入力の合格ではない。"""
    if action == "status":
        return "metadata_observed"
    if action == "start":
        if check["result"] != "running":
            reject("PROBE_NOT_RUNNING")
        if check["synthetic"]["result"] == "fail":
            reject("SYNTHETIC_FIXTURE_FAILED")
        if check.get("stage") != "awaiting_manual" or check["synthetic"]["result"] != "pass":
            return None
        if {row["editor"] for row in check.get("editors", []) if row.get("attached")} != {"A", "B", "C"}:
            return None
        if {row["panel"] for row in check.get("panels", [])} != {0, 1}:
            return None
        return "panel_fixture_ready"
    if action == "stop":
        if check["result"] == "running":
            return None
        if check["result"] != "completed" or check.get("reason") not in {"operator_stop", "panel_stop"}:
            reject("STOP_NOT_ACKNOWLEDGED")
        if check.get("cleanup_failed") is not False or check.get("panels_destroy_requested") is not True:
            reject("INCOMPLETE_STOP_PROOF")
        return "stop_acknowledged"  # detach観測は独立field。SDK資源解放完了とは扱わない。
    if action == "exercise":
        proof = check.get("exercise", {"result": "not_run"})
        if check["result"] != "running" or proof["result"] in {"blocked", "fail"}:
            reject("EXERCISE_NOT_COMPLETED")
        if proof["result"] != "completed":
            return None
        return "exercise_sequence_observed"
    if action == "sdk-tap":
        proof = check.get("sdk_tap", {"result": "not_run"})
        if check["result"] != "running" or proof["result"] in {"blocked", "fail"}:
            reject("SDK_TAP_NOT_COMPLETED")
        if proof["result"] != "completed":
            return None
        return "sdk_tap_sequence_observed"
    reject("UNKNOWN_ACTION")
