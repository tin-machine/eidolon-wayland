"""GTK固定caseの本文を含まないSDK/Android受領の選択と独立判定。"""
import copy
from gtk_interaction_run import app_ids, completed
from sdl_lease_capture import decimal, need
STAGES = {"input": ["captured", "focused", "typed", "clicked", "retired"],
          "dialog": ["captured", "dialog_mapped", "dialog_focused", "dialog_escape", "dialog_retired", "retired"],
          "resize": ["captured", "large", "returned", "retired"]}


def key(value):
    need(type(value) is dict and set(value)=={"session","window","scene","generation"} and all(decimal(v) for v in value.values()),"GTK_RESOURCE_TARGET")
    return dict(value)


def resources(value):
    """本文/traceは選ばず、full targetと実所有counterだけを残す。旧schemaは合格させない。"""
    runtime=value["runtime"]["runtime"];adapter=value["adapter"];handle=value["runtime"]["handle"]
    need(decimal(handle) and decimal(adapter["scene"]) and adapter["scene"]==runtime["scene"],"GTK_RESOURCE_SCENE")
    def counter(value):
        need(type(value) is int and 0<=value<=2**63-1,"GTK_RESOURCE_COUNTER");return value
    def flag(value):
        need(type(value) is bool,"GTK_RESOURCE_FLAG");return value
    targets=[]
    for row in adapter["targets"]:
        need(decimal(row["entity"]),"GTK_RESOURCE_ENTITY")
        controls=row.get("controls");mode=row.get("mode_badge")
        buttons=[] if controls is None else [b["entity"] for b in controls["buttons"]]
        need(all(decimal(v) for v in buttons) and len(set(buttons))==len(buttons),"GTK_RESOURCE_ENTITY")
        visual=row.get("visual_entity");mode_entity=None if mode is None else mode["entity"]
        need(all(v is None or decimal(v) for v in (visual,mode_entity)),"GTK_RESOURCE_ENTITY")
        targets.append(dict(key=key(row["target"]),entity=row["entity"],visual_entity=visual,
            listener_owned=flag(row["listener_owned"]),input_owned=flag(row["input_owned"]),
            retiring=flag(row["retiring"]),sdk_disposed=flag(row["sdk_disposed"]),disposal_failed=flag(row["disposal_failed"]),
            control_entities=sorted(buttons),control_failures=0 if controls is None else counter(controls["failures"]),
            move_owned=row.get("move") is not None,mode_entity=mode_entity,
            mode_ready=False if mode is None else flag(mode["object_ready"]),mode_failures=0 if mode is None else counter(mode["failures"])))
    need(len(targets)==counter(adapter["bindings"]),"GTK_RESOURCE_BINDING_COUNT")
    modes=adapter["window_mode_badges"];controls=adapter["window_controls"]
    owners=dict(panel_entities=counter(adapter["created"])-counter(adapter["destroyed"]),listeners=counter(adapter["listeners"]),
        decorations=counter(adapter["decoration_entities"]),control_entities=counter(controls["entity_owners"]),control_listeners=counter(controls["listener_owners"]))
    need(owners["panel_entities"]>=0,"GTK_RESOURCE_COUNTER")
    owners.update({"mode_"+name:counter(modes[name]) for name in
        ("entity_owners","object_owners","mesh_owners","material_owners","texture_owners","cleanup_pending_owners","pixel_slots_in_use")})
    retired=[]
    for row in adapter["retired"]:
        need(decimal(row["runtime"]) and decimal(row["entity"]),"GTK_RESOURCE_RECEIPT")
        retired.append(dict(runtime=row["runtime"],key=key(row["target"]),entity=row["entity"],
            sdk_disposed=flag(row["sdk_disposed"]),sdk_binding_released=flag(row["sdk_binding_released"]),disposal_failed=flag(row["disposal_failed"])))
    need(len(retired)<=32,"GTK_RESOURCE_RECEIPT")
    panels=[]
    for row in runtime["panels"]:
        need(row["action"] in {"create","destroy"},"GTK_RESOURCE_PANEL")
        panels.append(dict(key=key(row["target"]),action=row["action"],applied=flag(row["applied"]),error=row["error"] is not None))
    board=runtime["window_slots"];entries=[]
    for row in board["entries"]:
        lease=row["lease"];state=row["state"];window=state.get("window")
        need(decimal(str(lease["runtime"])) and decimal(str(lease["session"])) and decimal(str(lease["token"])) and lease["position"] in {"center","right","left","up","down","upper_left","upper_right","lower_left","lower_right"} and (window is None or decimal(str(window))) and
             state["phase"] in {"reserved","bound","retiring"},"GTK_RESOURCE_BOARD")
        entries.append(dict(runtime=str(lease["runtime"]),session=str(lease["session"]),token=str(lease["token"]),position=lease["position"],window=None if window is None else str(window),phase=state["phase"]))
    need(counter(board["occupied"])==len(entries) and all(decimal(str(v)) for v in board["unassigned"]),"GTK_RESOURCE_BOARD")
    return dict(handle=handle,scene=adapter["scene"],targets=targets,retired=retired,owners=owners,panels=panels,
        sdk_bindings=counter(runtime["shutdown"]["sdk_bindings"]),board=dict(entries=entries,cleanup_required=flag(board["cleanup_required"]),unassigned=list(map(str,board["unassigned"]))))


def baseline_resources(resource, baseline, native_keys):
    targets=resource["targets"];owners=resource["owners"];board=resource["board"]
    if [t["key"] for t in targets]!=native_keys or {t["key"]["window"] for t in targets}!=baseline:return False
    if len(resource["panels"])!=len(baseline) or any(p["key"] not in native_keys or p["action"]!="create" or not p["applied"] or p["error"] for p in resource["panels"]):return False
    if any(e["runtime"]!=resource["handle"] or not any(k["session"]==e["session"] and k["window"]==e["window"] for k in native_keys) for e in board["entries"]):return False
    if resource["sdk_bindings"]!=len(baseline) or board["cleanup_required"] or board["unassigned"]:return False
    if len(board["entries"])!=len(baseline) or {e["window"] for e in board["entries"] if e["phase"]=="bound"}!=baseline:return False
    if any(t["retiring"] or t["sdk_disposed"] or t["disposal_failed"] or t["control_failures"] or t["mode_failures"] or not t["listener_owned"] or not t["input_owned"] or t["mode_entity"] is not None and not t["mode_ready"] for t in targets):return False
    mode_count=sum(t["mode_entity"] is not None for t in targets);buttons=sum(len(t["control_entities"]) for t in targets)
    expected=dict(panel_entities=len(targets),listeners=len(targets),decorations=sum(t["visual_entity"] is not None for t in targets),control_entities=buttons*3,control_listeners=buttons)
    expected.update({"mode_"+n:mode_count for n in ("entity_owners","object_owners","mesh_owners","material_owners","texture_owners","pixel_slots_in_use")})
    expected["mode_cleanup_pending_owners"]=0
    return owners==expected


def resource_retirement(samples, baseline, owned):
    if not samples or not owned:return False
    before=samples[0].get("resources");after=samples[-1].get("resources")
    if not before or not after:return False
    # 最初の採取はspawn前。footのfull key/Entity/listener/control/mode ownerも同一で残ること。
    base_targets=before["targets"]
    native_keys=[t["key"] for t in samples[0]["targets"] if t["key"]["window"] in baseline]
    if not baseline_resources(before,baseline,native_keys):return False
    if after["targets"]!=base_targets or after["owners"]!=before["owners"] or after["sdk_bindings"]!=len(baseline):return False
    if after["panels"]!=before["panels"] or after["board"]!=before["board"]:return False
    if after["board"]["cleanup_required"] or after["board"]["unassigned"] or {e["window"] for e in after["board"]["entries"] if e["phase"]=="bound"}!=baseline:return False
    if any(t["retiring"] or t["sdk_disposed"] or t["disposal_failed"] or t["control_failures"] or t["mode_failures"] or not t["listener_owned"] for t in base_targets):return False
    return all(any(r["key"]==k and r["sdk_disposed"] and r["sdk_binding_released"] and not r["disposal_failed"]
                   and r["runtime"]==after["handle"] for r in after["retired"]) for k in owned)


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
    return dict(sequence=value["sequence"], windows=windows, targets=targets, resources=resources(value),epoch=runtime["input_resets"],
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


def receipt_scope(row, binding, scene, baseline, observed_main):
    if row["runtime"] is None:
        need(row["result"] in {"failed", "cancelled"} and row["target"] is None and not row["steps"] and
             (row["main_window"] is None or row["main_window"] in observed_main), "GTK_ANDROID_OWNER_CHANGED")
        return False  # owner取得前の失敗理由を残す。合格判定には使わない。
    need(row["runtime"] == binding["handle"] and row["session"] == binding["session"] and row["scene"] == scene and
         set(row["foot_windows"]) == baseline and row["main_window"] in observed_main, "GTK_ANDROID_OWNER_CHANGED")
    for target in [row["target"], *[step["target"] for step in row["steps"]]]:
        if target is not None:
            need(target["session"] == binding["session"] and target["scene"] == scene and
                 target["window"] not in baseline, "GTK_ANDROID_TARGET_CHANGED")
    return True


def receipt_binding(row, binding, scene, baseline, observed_main, observed_dialog):
    if not receipt_scope(row, binding, scene, baseline, observed_main):return
    for target in [row["target"], *[step["target"] for step in row["steps"]]]:
        if target is not None:
            need(target["window"] in observed_main | observed_dialog, "GTK_ANDROID_TARGET_CHANGED")


class ReceiptLedger:
    """checkはruntimeより先に保存され得る。未観測targetを採用せず有限時間だけ保留する。"""
    def __init__(self, binding, scene, baseline):
        self.binding=dict(binding);self.scene=scene;self.baseline=set(baseline)
        self.main=set();self.dialog=set();self.targets=set();self.pending=[];self.last=None

    @staticmethod
    def identity(value):
        value=key(value)
        return tuple(value[name] for name in ("session","window","scene","generation"))

    def observe(self, sample, receipt, now):
        self.main.update(w["window"] for w in sample["windows"] if w["role"]=="main")
        self.dialog.update(w["window"] for w in sample["windows"] if w["role"]=="dialog")
        for row in sample["targets"]:
            if row["key"]["window"] in self.main | self.dialog and row["frames"]>=1 and row["content_frame"] is not None:
                self.targets.add(self.identity(row["key"]))
        need(len(self.targets)<=16,"GTK_ANDROID_TARGET_LIMIT")
        if receipt is not None:
            receipt_scope(receipt,self.binding,self.scene,self.baseline,self.main)
            if receipt!=self.last and not any(row==receipt for row,_ in self.pending):
                need(len(self.pending)<32,"GTK_ANDROID_RECEIPT_LIMIT")
                self.pending.append((copy.deepcopy(receipt),now))
        accepted=[];pending=[]
        for row,started in self.pending:
            need(now-started<2,"GTK_ANDROID_TARGET_UNOBSERVED")
            targets=[t for t in [row["target"],*[step["target"] for step in row["steps"]]] if t is not None]
            if all(self.identity(t) in self.targets for t in targets):
                receipt_binding(row,self.binding,self.scene,self.baseline,self.main,self.dialog)
                accepted.append(row)
            else:
                pending.append((row,started))
        self.pending=pending
        if accepted:self.last=accepted[-1]
        return accepted


def summarize(samples, baseline, case, fixture, android):
    main, dialog, first = set(), set(), set()
    owned=[]
    base_resources=samples[0].get("resources") if samples else None
    preserved, sizes = True, []
    for sample in samples:
        mapped = {w["window"] for w in sample["windows"]}
        rendered = {t["key"]["window"] for t in sample["targets"] if t["frames"]>=1 and t["content_frame"] is not None}
        preserved &= baseline <= mapped and baseline <= rendered and not any(w["role"] == "other" for w in sample["windows"])
        if base_resources:
            resources=sample.get("resources",{})
            baseline_targets=[t for t in resources.get("targets",[]) if t["key"]["window"] in baseline]
            preserved &= baseline_targets==base_resources["targets"]
        main.update(w["window"] for w in sample["windows"] if w["role"] == "main")
        dialog.update(w["window"] for w in sample["windows"] if w["role"] == "dialog")
        for w in sample["windows"]:
            if w["role"] == "main" and w["resize_phase"] == "applied": sizes.append(w["logical_size"])
        for target in sample["targets"]:
            if target["frames"] >= 1 and target["content_frame"] is not None: first.add(target["key"]["window"])
    last = samples[-1] if samples else dict(windows=[], targets=[], pressed=-1, pointer_pressed=-1)
    live = {w["window"] for w in last["windows"]} | {t["key"]["window"] for t in last["targets"]}
    for sample in samples:
        for row in [*sample.get("resources",{}).get("targets",[]),*sample["targets"]]:
            if row["key"]["window"] in main|dialog and row["key"] not in owned:owned.append(row["key"])
    retired = bool(main) and not (main | dialog) & live and resource_retirement(samples,baseline,owned)
    last_report = fixture[-1] if fixture else None
    c_gate = bool(last_report and last_report["phase"] == "final" and last_report["reason"] == "completed" and completed(last_report, case))
    resize = case != "resize" or any(s == dict(width=800, height=500) for s in sizes) and any(s == dict(width=640, height=360) for s in sizes)
    mapped = len(main) == 1 and main <= first and (case != "dialog" or len(dialog) == 1 and dialog <= first)
    passed = bool(mapped and preserved and retired and c_gate and resize and android and android["result"] == "completed" and
                  last["pressed"] == last["pointer_pressed"] == 0)
    return dict(status="observed" if passed else "not_verified", main_windows=sorted(main), dialog_windows=sorted(dialog),
                baseline_preserved=preserved, all_retired=retired, client_counter_gate=c_gate, sdk_resize_gate=resize,
                sdk_first_frames=sorted(first & (main | dialog)), physical_input="not_run", ime="not_run", hmd="not_run")
