"""GTK固定caseの本文を含まないSDK/Android受領の選択と独立判定。"""
from gtk_interaction_run import app_ids, completed
from sdl_lease_capture import decimal, need
STAGES = {"input": ["captured", "focused", "typed", "clicked", "retired"],
          "dialog": ["captured", "dialog_mapped", "dialog_focused", "dialog_escape", "dialog_retired", "retired"],
          "resize": ["captured", "large", "returned", "retired"]}


def sample(value, nonce, baseline):
    runtime = value["runtime"]["runtime"];app = runtime["app"]
    expected = app_ids(nonce)
    windows = []
    for row in app.get("windows", []):
        if row.get("life") != "Mapped": continue
        need(decimal(row.get("window")), "GTK_WINDOW_INVALID")
        role = "baseline" if row["window"] in baseline else "main" if row.get("app_id") == expected[0] else "dialog" if row.get("app_id") == expected[1] else "other"
        size = row.get("logical_size");resize = row.get("resize", {})
        if size is not None:
            need(type(size) is dict and set(size) == {"width", "height"} and all(type(v) is int and 0 < v <= 4096 for v in size.values()), "GTK_SIZE_INVALID")
        phase = resize.get("phase")
        need(phase in {None, "idle", "queued", "awaiting_ack", "awaiting_commit", "applied", "adjusted", "timed_out"}, "GTK_RESIZE_SCHEMA")
        frame=row.get("frame");need(frame is None or decimal(str(frame)),"GTK_FRAME_INVALID")
        windows.append(dict(window=row["window"], role=role, logical_size=size, resize_phase=phase, frame=frame))
    targets = []
    for row in runtime.get("render", {}).get("targets", []):
        key = row.get("key", {})
        if key.get("session") != app["session"]: continue
        need(set(key) == {"session", "window", "scene", "generation"} and all(decimal(v) for v in key.values()), "GTK_TARGET_INVALID")
        frames, content = row.get("frames"), row.get("content_frame")
        need(type(frames) is int and 0 <= frames <= 2**63-1 and (content is None or decimal(str(content))), "GTK_FRAME_INVALID")
        targets.append(dict(key=key, frames=frames, content_frame=content))
    input = app["input"]
    need((input.get("focus_window") is None or decimal(input["focus_window"])) and
         all(type(n) is int and 0 <= n <= 2**63-1 for n in
             (runtime["input_resets"], input["pressed"], input["pointer"]["pressed"], input["delivered"], input["pointer"]["buttons"])), "GTK_INPUT_SCHEMA")
    return dict(sequence=value["sequence"], windows=windows, targets=targets, epoch=runtime["input_resets"],
                focused_window=input.get("focus_window"), pressed=input["pressed"], pointer_pressed=input["pointer"]["pressed"],
                delivered=input["delivered"], pointer_buttons=input["pointer"]["buttons"])


def ready(sample, row, case):
    main = [w for w in sample["windows"] if w["role"] == "main"]
    if len(main) != 1 or not row or row["phase"] not in {"ready", "state"}: return False
    target = [t for t in sample["targets"] if t["key"]["window"] == main[0]["window"] and t["frames"] >= 1 and t["content_frame"] is not None]
    size = main[0]["logical_size"]
    if not target or size is None or row["logical_size"] != [size["width"], size["height"]]: return False
    if case == "resize": return True
    x, y = size["width"]/2, 84 if case == "input" else size["height"] - 68
    rect = row["entry_rect"] if case == "input" else row["button_rect"]
    need(rect[0] < x < rect[0]+rect[2] and rect[1] < y < rect[1]+rect[3], "GTK_GEOMETRY_UNSUPPORTED")
    return True


def android_receipt(value, nonce, case):
    row = value.get("check")
    if type(row) is not dict or row.get("case") != "gtk-"+case or row.get("nonce") != nonce: return None
    need(set(row) == {"case", "nonce", "result", "phase", "reason", "scope", "physical_input_verified", "ime_verified",
                      "host_physical_filter_verified", "runtime", "session", "scene", "main_window", "target", "input_epoch", "foot_windows", "steps"}, "GTK_ANDROID_SCHEMA")
    need(row["scope"] == "target_bound_synthetic_native" and all(row[k] is False for k in
         ("physical_input_verified", "ime_verified", "host_physical_filter_verified")) and row["result"] in
         {"running", "completed", "failed", "unsupported", "cancelled"}, "GTK_ANDROID_SCOPE")
    need(row["phase"] in {"START", "WAIT_FOCUS", "WAIT_KEYS", "WAIT_CLICK", "WAIT_DIALOG", "WAIT_DIALOG_FOCUS", "DIALOG_READY", "WAIT_DIALOG_CLOSED",
                          "WAIT_LARGE", "WAIT_RETURN", "OPERATIONS_COMPLETE", "CLOSING", "COMPLETE", "UNSUPPORTED", "CANCELLED"}, "GTK_ANDROID_PHASE")
    need(row["reason"] is None or type(row["reason"]) is str and __import__("re").fullmatch(r"(?:GTK_[A-Z_]+|DIALOG_TARGET_UNAVAILABLE)", row["reason"]), "GTK_ANDROID_REASON")
    need(all(row[k] is None or decimal(row[k]) for k in ("runtime", "session", "scene", "main_window")) and
         type(row["input_epoch"]) is int and row["input_epoch"] >= 0 and type(row["steps"]) is list and len(row["steps"]) <= 24 and
         all(type(step) is dict for step in row["steps"]) and
         type(row["foot_windows"]) is list and all(decimal(v) for v in row["foot_windows"]), "GTK_ANDROID_BINDING")
    for target in [row["target"], *[step.get("target") for step in row["steps"]]]:
        need(target is None or type(target) is dict and set(target) == {"session", "window", "scene", "generation"} and all(decimal(v) for v in target.values()), "GTK_ANDROID_TARGET")
    for step in row["steps"]:
        need(type(step) is dict and set(step) == {"stage", "target", "input_epoch", "delivered"} and
             step["stage"] in {"captured", "focused", "typed", "clicked", "dialog_mapped", "dialog_focused", "dialog_escape", "dialog_retired", "large", "returned", "retired"} and
             type(step["input_epoch"]) is int and step["input_epoch"] >= 0 and type(step["delivered"]) is int and step["delivered"] >= 0, "GTK_ANDROID_STEP")
    stages=[step["stage"] for step in row["steps"]]
    need(stages==STAGES[case][:len(stages)] and (row["result"]!="completed" or row["phase"]=="COMPLETE" and stages==STAGES[case]),"GTK_ANDROID_SEQUENCE")
    return row


def current_frame(sample, role):
    windows=[w for w in sample["windows"] if w["role"]==role]
    if len(windows)!=1:return None
    window=windows[0]
    for target in sample["targets"]:
        content=target["content_frame"]
        if target["key"]["window"]==window["window"] and content is not None and target["frames"]>=1 and window.get("frame") is not None and int(content)>=int(window["frame"]):
            return int(content)
    return None


def receipt_binding(row, binding, scene, baseline, observed_main, observed_dialog):
    if row["runtime"] is None:
        need(row["result"] in {"failed", "cancelled"} and row["target"] is None and not row["steps"] and
             (row["main_window"] is None or row["main_window"] in observed_main), "GTK_ANDROID_OWNER_CHANGED")
        return  # owner取得前の失敗理由を残す。合格判定には使わない。
    need(row["runtime"] == binding["handle"] and row["session"] == binding["session"] and row["scene"] == scene and
         set(row["foot_windows"]) == baseline and row["main_window"] in observed_main, "GTK_ANDROID_OWNER_CHANGED")
    for target in [row["target"], *[step["target"] for step in row["steps"]]]:
        if target is not None:
            need(target["session"] == binding["session"] and target["scene"] == scene and
                 target["window"] in observed_main | observed_dialog, "GTK_ANDROID_TARGET_CHANGED")


def summarize(samples, baseline, case, fixture, android):
    main, dialog, first = set(), set(), set()
    preserved, sizes = True, []
    for sample in samples:
        mapped = {w["window"] for w in sample["windows"]}
        rendered = {t["key"]["window"] for t in sample["targets"] if t["frames"]>=1 and t["content_frame"] is not None}
        preserved &= baseline <= mapped and baseline <= rendered and not any(w["role"] == "other" for w in sample["windows"])
        main.update(w["window"] for w in sample["windows"] if w["role"] == "main")
        dialog.update(w["window"] for w in sample["windows"] if w["role"] == "dialog")
        for w in sample["windows"]:
            if w["role"] == "main" and w["resize_phase"] == "applied": sizes.append(w["logical_size"])
        for target in sample["targets"]:
            if target["frames"] >= 1 and target["content_frame"] is not None: first.add(target["key"]["window"])
    last = samples[-1] if samples else dict(windows=[], targets=[], pressed=-1, pointer_pressed=-1)
    live = {w["window"] for w in last["windows"]} | {t["key"]["window"] for t in last["targets"]}
    retired = bool(main) and not (main | dialog) & live
    last_report = fixture[-1] if fixture else None
    c_gate = bool(last_report and last_report["phase"] == "final" and last_report["reason"] == "completed" and completed(last_report, case))
    resize = case != "resize" or any(s == dict(width=800, height=500) for s in sizes) and any(s == dict(width=640, height=360) for s in sizes)
    mapped = len(main) == 1 and main <= first and (case != "dialog" or len(dialog) == 1 and dialog <= first)
    passed = bool(mapped and preserved and retired and c_gate and resize and android and android["result"] == "completed" and
                  last["pressed"] == last["pointer_pressed"] == 0)
    return dict(status="observed" if passed else "not_verified", main_windows=sorted(main), dialog_windows=sorted(dialog),
                baseline_preserved=preserved, all_retired=retired, client_counter_gate=c_gate, sdk_resize_gate=resize,
                sdk_first_frames=sorted(first & (main | dialog)), physical_input="not_run", ime="not_run", hmd="not_run")
