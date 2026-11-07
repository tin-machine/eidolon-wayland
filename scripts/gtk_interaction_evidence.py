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
    menus=adapter["window_menus"]
    owners.update({"menu_"+name:counter(menus[name]) for name in
        ("owners","cleanup_pending_owners","entity_owners","listener_owners","object_owners","mesh_owners","material_owners","texture_owners")})
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
    expected.update({"menu_"+n:0 for n in ("owners","cleanup_pending_owners","entity_owners","listener_owners","object_owners","mesh_owners","material_owners","texture_owners")})
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


RESIZE_STEP_FIELDS = {"resize_request", "old_target", "replacement", "configured_serial", "acknowledged_serial", "committed_serial", "width", "height"}
RESIZE_READY_FLAGS = {"receipt_matches", "command_ack", "sizes_applied", "serials_present", "producer_advanced", "input_unchanged",
    "old_owner_released", "focus_unchanged_or_owned_reset", "focus_ready", "physical_applied", "pose_applied", "sdk_and_input_ready"}


def resize_status(row):
    if row is None:return
    need(type(row) is dict and set(row)=={"request_id","pending","readiness","acknowledged","old_retirement_seen","reason"},"GTK_ANDROID_RESIZE_STATUS")
    need(all(type(row[n]) is bool for n in ("pending","acknowledged","old_retirement_seen")) and row["reason"] in {
        "RESIZE_IDLE","RESIZE_WAITING","RESIZE_INTENT_LOST","RESIZE_DEADLINE","RESIZE_COMMAND_REJECTED","RESIZE_REQUEST_CHANGED",
        "RESIZE_OTHER_INPUT","RESIZE_TARGET_CHANGED","RESIZE_GENERATION_REUSED","RESIZE_EPOCH_CHANGED"},"GTK_ANDROID_RESIZE_STATUS")
    need((row["pending"] and decimal(row["request_id"]) and row["reason"]!="RESIZE_IDLE") or
         (not row["pending"] and row["request_id"] is None and row["readiness"] is None and not row["acknowledged"] and
          not row["old_retirement_seen"] and row["reason"]=="RESIZE_IDLE"),"GTK_ANDROID_RESIZE_STATUS")
    ready=row["readiness"]
    need(ready is None or type(ready) is dict and set(ready)==RESIZE_READY_FLAGS|{"epoch","candidate_generation"} and
         all(type(ready[n]) is bool for n in RESIZE_READY_FLAGS) and type(ready["epoch"]) is int and ready["epoch"]>=0 and
         decimal(ready["candidate_generation"]),"GTK_ANDROID_RESIZE_STATUS")


def resize_step(step, previous, request):
    need(previous is not None and step["target"] is not None and previous["target"] is not None and
         decimal(step["resize_request"]) and int(step["resize_request"])>request and type(step["replacement"]) is bool and
         all(type(step[n]) is int and 0<=step[n]<=2**32-1 for n in ("configured_serial","acknowledged_serial","committed_serial")) and
         all(type(step[n]) is int for n in ("width","height")) and
         (step["width"],step["height"])==((800,500) if step["stage"]=="large" else (640,360)),"GTK_ANDROID_RESIZE_PROOF")
    old=step["old_target"]
    need(type(old) is str and old==":".join(previous["target"][n] for n in ("session","window","scene","generation")),"GTK_ANDROID_RESIZE_OLD_TARGET")
    before=previous["target"];after=step["target"]
    need(all(before[n]==after[n] for n in ("session","window","scene")) and step["delivered"]==previous["delivered"],"GTK_ANDROID_RESIZE_PROOF")
    if step["replacement"]:
        need(int(after["generation"])>int(before["generation"]) and 1<=step["input_epoch"]-previous["input_epoch"]<=2,"GTK_ANDROID_RESIZE_EPOCH")
    else:
        need(after==before and step["input_epoch"]==previous["input_epoch"],"GTK_ANDROID_RESIZE_EPOCH")
    return int(step["resize_request"])


def input_progress(row):
    if row is None:return
    counts={"floor_keys","floor_buttons","expected_keys","expected_buttons","observed_keys","observed_buttons","keys_pressed","buttons_pressed","expected_epoch","observed_epoch"}
    need(type(row) is dict and set(row)==counts|{"schema","phase","state","reason","elapsed_ms","expected_focus_window","observed_focus_window","focus_token_matches"},"GTK_ANDROID_PROGRESS_SCHEMA")
    need(type(row["schema"]) is int and row["schema"]==1 and all(type(row[n]) is int and 0<=row[n]<=2**63-1 for n in counts) and
         type(row["elapsed_ms"]) is int and 0<=row["elapsed_ms"]<=6000 and type(row["phase"]) is str and row["phase"] in {
             "WAIT_FOCUS","WAIT_KEYS","WAIT_CLICK","WAIT_DIALOG","WAIT_DIALOG_FOCUS","DIALOG_READY","WAIT_DIALOG_CLOSED","OPERATIONS_COMPLETE","CLOSING","WAIT_RETIRED"} and
         all(row[n] is None or decimal(row[n]) for n in ("expected_focus_window","observed_focus_window")) and
         (row["focus_token_matches"] is None or type(row["focus_token_matches"]) is bool),"GTK_ANDROID_PROGRESS_SCHEMA")
    need(all(0<=row["expected_"+n]-row["floor_"+n]<=4 for n in ("keys","buttons")),"GTK_ANDROID_PROGRESS_RANGE")
    reasons={"waiting":{"WAIT_COUNTERS"},"complete":{"COUNTERS_COMPLETE"},"rejected":{"CLOCK_REGRESSED","COUNTER_REGRESSED","COUNTER_EXCESS","UNEXPECTED_HELD","PROGRESS_TIMEOUT"}}
    need(type(row["state"]) is str and row["state"] in reasons and type(row["reason"]) is str and row["reason"] in reasons[row["state"]],"GTK_ANDROID_PROGRESS_STATE")
    if row["state"]!="rejected":
        need(all(row["floor_"+n]<=row["observed_"+n]<=row["expected_"+n] for n in ("keys","buttons")) and
             row["keys_pressed"]<=1 and row["buttons_pressed"]<=1,"GTK_ANDROID_PROGRESS_RANGE")
    if row["state"]=="complete":
        need(all(row["observed_"+n]==row["expected_"+n] for n in ("keys","buttons")) and
             row["keys_pressed"]==row["buttons_pressed"]==0,"GTK_ANDROID_PROGRESS_COMPLETE")


def android_receipt(value, nonce, case):
    row = value.get("check")
    if type(row) is not dict or row.get("case") != "gtk-"+case or row.get("nonce") != nonce: return None
    fields={"case", "nonce", "result", "phase", "reason", "scope", "physical_input_verified", "ime_verified",
            "host_physical_filter_verified", "runtime", "session", "scene", "main_window", "target", "input_epoch", "foot_windows", "steps", "input_progress"}
    if case=="resize":fields.update({"resize_replacement","resize_observation"})
    need(set(row)==fields,"GTK_ANDROID_SCHEMA")
    input_progress(row["input_progress"])
    if case=="resize":need(row["input_progress"] is None,"GTK_ANDROID_PROGRESS_SCHEMA")
    elif row["result"]=="completed":need(row["input_progress"] is not None and row["input_progress"]["state"]=="complete","GTK_ANDROID_PROGRESS_COMPLETE")
    if case=="resize":
        resize_status(row["resize_replacement"])
        observation=row["resize_observation"]
        need(type(observation) is dict and set(observation)=={"state","return_requests"} and
             observation["state"] in {"WAITING_LARGE","LARGE_READY","WAIT_RETURN","COMPLETE","CANCELLED"} and
             type(observation["return_requests"]) is int and observation["return_requests"] in {0,1},"GTK_ANDROID_RESIZE_OBSERVATION")
        if row["result"]=="completed":need(observation==dict(state="COMPLETE",return_requests=1),"GTK_ANDROID_RESIZE_OBSERVATION")
        if row["phase"]=="LARGE_READY":need(observation["state"]=="LARGE_READY","GTK_ANDROID_RESIZE_OBSERVATION")
        if row["phase"]=="WAIT_RETURN":need(observation==dict(state="WAIT_RETURN",return_requests=1),"GTK_ANDROID_RESIZE_OBSERVATION")
    need(row["scope"] == "target_bound_synthetic_native" and all(row[k] is False for k in
         ("physical_input_verified", "ime_verified", "host_physical_filter_verified")) and row["result"] in
         {"running", "completed", "failed", "unsupported", "cancelled"}, "GTK_ANDROID_SCOPE")
    need(row["phase"] in {"START", "WAIT_FOCUS", "WAIT_KEYS", "WAIT_CLICK", "WAIT_DIALOG", "WAIT_DIALOG_FOCUS", "DIALOG_READY", "WAIT_DIALOG_CLOSED",
                          "WAIT_LARGE", "LARGE_READY", "WAIT_RETURN", "OPERATIONS_COMPLETE", "CLOSING", "COMPLETE", "UNSUPPORTED", "CANCELLED"}, "GTK_ANDROID_PHASE")
    need(row["reason"] is None or type(row["reason"]) is str and __import__("re").fullmatch(r"(?:GTK_[A-Z_]+|DIALOG_TARGET_UNAVAILABLE)", row["reason"]), "GTK_ANDROID_REASON")
    need(all(row[k] is None or decimal(row[k]) for k in ("runtime", "session", "scene", "main_window")) and
         type(row["input_epoch"]) is int and row["input_epoch"] >= 0 and type(row["steps"]) is list and len(row["steps"]) <= 24 and
         all(type(step) is dict for step in row["steps"]) and
         type(row["foot_windows"]) is list and all(decimal(v) for v in row["foot_windows"]), "GTK_ANDROID_BINDING")
    for target in [row["target"], *[step.get("target") for step in row["steps"]]]:
        need(target is None or type(target) is dict and set(target) == {"session", "window", "scene", "generation"} and all(decimal(v) for v in target.values()), "GTK_ANDROID_TARGET")
    previous=None;request=0
    for step in row["steps"]:
        resize=case=="resize" and step.get("stage") in {"large","returned"}
        fields={"stage", "target", "input_epoch", "delivered"} | (RESIZE_STEP_FIELDS if resize else set())
        need(type(step) is dict and set(step) == fields and
             step["stage"] in {"captured", "focused", "typed", "clicked", "dialog_mapped", "dialog_focused", "dialog_escape", "dialog_retired", "large", "returned", "retired"} and
             type(step["input_epoch"]) is int and step["input_epoch"] >= 0 and type(step["delivered"]) is int and step["delivered"] >= 0, "GTK_ANDROID_STEP")
        if resize:request=resize_step(step,previous,request)
        previous=step
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


def resize_large_observed(value, sdk_sample, fixture, android):
    """現在の同full targetの800x500をPCが保存してから、一回のreturnだけを許可する。"""
    if not android or android.get("phase")!="LARGE_READY" or android.get("resize_observation")!=dict(state="LARGE_READY",return_requests=0):return None
    if not fixture or fixture.get("case")!="resize" or fixture.get("run_id")!=android.get("nonce") or fixture.get("phase") not in {"ready","state"} or fixture.get("resize_large_seen") is not True or fixture.get("resize_return_seen") is not False or fixture.get("logical_size")!=[800,500]:return None
    try:
        steps=android["steps"]
        if [step["stage"] for step in steps]!=["captured","large"]:return None
        step=steps[-1];target=key(step["target"]);expected=dict(width=800,height=500)
        if target!=android["target"] or (step["width"],step["height"])!=(800,500):return None
        if current_frame(sdk_sample,"main") is None or sdk_sample["epoch"]!=step["input_epoch"] or sdk_sample["delivered"]!=step["delivered"] or sdk_sample["pressed"]!=0 or sdk_sample["pointer_pressed"]!=0:return None
        runtime=value["runtime"]["runtime"];adapter=value["adapter"]
        if value["sequence"]!=sdk_sample["sequence"] or runtime.get("state")!="Ready" or runtime.get("activity_visible") is not True or runtime.get("xr_state")!="FOCUSED":return None
        if not any(t["key"]==target for t in sdk_sample["targets"]):return None
        if value["runtime"]["handle"]!=android["runtime"] or runtime["scene"]!=target["scene"] or runtime["app"]["session"]!=target["session"]:return None
        windows=[w for w in runtime["app"]["windows"] if w["window"]==target["window"] and w["life"]=="Mapped"]
        if len(windows)!=1:return None
        window=windows[0];resize=window["resize"];request=resize["request"]
        old=":".join(request["target"][n] for n in ("session","window","scene","generation"))
        # native ResizeRequestはrequest_idとtargetだけ。dimensionsは直下の4size receiptで照合する。
        if set(request)!={"request_id","target"} or request["request_id"]!=step["resize_request"] or old!=step["old_target"]:return None
        if resize["phase"]!="applied" or resize["error"] is not None or any(size!=expected for size in (window["logical_size"],resize["requested_logical_size"],resize["configured_logical_size"],resize["committed_size"])):return None
        serials={n:resize[n] for n in ("configured_serial","acknowledged_serial","committed_serial")}
        if any(type(v) is not int or not 0<=v<=2**32-1 or v!=step[n] for n,v in serials.items()):return None
        native=[t for t in runtime["render"]["targets"] if t["key"]==target]
        if len(native)!=1 or native[0]["frames"]<1 or native[0].get("presentation_paused") is not False or native[0]["content_size"]!=expected or not __import__("re").fullmatch(r"[1-9][0-9]*",str(native[0]["content_frame"])) or int(native[0]["content_frame"])<int(window["frame"]):return None
        plans=[p for p in runtime["panels"] if p["target"]==target and p["action"]=="create" and p["applied"] is True and p["error"] is None]
        bound=[t for t in adapter["targets"] if t["target"]==target]
        if len(plans)!=1 or len(bound)!=1 or bound[0]["runtime"]!=android["runtime"] or any(bound[0][n] is not False for n in ("retiring","sdk_disposed","disposal_failed")) or any(bound[0][n] is not True for n in ("listener_owned","input_owned")):return None
        return dict(sequence=sdk_sample["sequence"],target=target,resize_request=step["resize_request"],logical_size=expected,
                    content_frame=str(native[0]["content_frame"]),**serials)
    except (KeyError,TypeError,ValueError,OverflowError):return None


def resize_observation_matches(observation, android):
    if not android or type(observation) is not dict:return False
    fields={"sequence","target","resize_request","logical_size","content_frame","configured_serial","acknowledged_serial","committed_serial"}
    if set(observation)!=fields or not decimal(str(observation["sequence"])) or not decimal(observation["content_frame"]):return False
    large=[step for step in android.get("steps",[]) if step["stage"]=="large"]
    return len(large)==1 and observation["target"]==large[0]["target"] and observation["resize_request"]==large[0]["resize_request"] and observation["logical_size"]==dict(width=800,height=500) and all(type(observation[n]) is int and observation[n]==large[0][n] for n in ("configured_serial","acknowledged_serial","committed_serial")) and android.get("resize_observation")==dict(state="COMPLETE",return_requests=1)


def resize_observation_recorded(samples, observation, android):
    if not resize_observation_matches(observation,android):return False
    large=next(step for step in android["steps"] if step["stage"]=="large")
    for snapshot in samples:
        if snapshot["sequence"]!=observation["sequence"] or snapshot["epoch"]!=large["input_epoch"] or snapshot["delivered"]!=large["delivered"] or snapshot["pressed"]!=0 or snapshot["pointer_pressed"]!=0:continue
        windows=[w for w in snapshot["windows"] if w["role"]=="main" and w["window"]==observation["target"]["window"]]
        targets=[t for t in snapshot["targets"] if t["key"]==observation["target"]]
        if len(windows)==len(targets)==1 and windows[0]["resize_phase"]=="applied" and windows[0]["logical_size"]==observation["logical_size"] and targets[0]["frames"]>=1 and targets[0]["content_frame"] is not None and int(targets[0]["content_frame"])>=int(observation["content_frame"]) and current_frame(snapshot,"main") is not None:return True
    return False


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


def summarize(samples, baseline, case, fixture, android, resize_observation=None):
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
    observed = case!="resize" or resize_observation_recorded(samples,resize_observation,android)
    mapped = len(main) == 1 and main <= first and (case != "dialog" or len(dialog) == 1 and dialog <= first)
    passed = bool(mapped and preserved and retired and c_gate and resize and observed and android and android["result"] == "completed" and
                  last["pressed"] == last["pointer_pressed"] == 0)
    return dict(status="observed" if passed else "not_verified", main_windows=sorted(main), dialog_windows=sorted(dialog),
                baseline_preserved=preserved, all_retired=retired, client_counter_gate=c_gate, sdk_resize_gate=resize, host_resize_observation_gate=observed,
                sdk_first_frames=sorted(first & (main | dialog)), physical_input="not_run", ime="not_run", hmd="not_run")
