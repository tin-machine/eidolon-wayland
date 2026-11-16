"""Independent N06 R/S oracle. Producer status/assertions have no authority.

Validates *normal* Registry/SSH/C2/Waypipe, actual GTK input deliveries and
new full-target generations, independent file bytes, and product retirement.
Neither the N01–N05 independent oracles nor the frozen manifest are changed.
"""
from __future__ import annotations

import copy
import linux_real_app_v3_evidence as evidence
import linux_real_app_v3_n01_oracle as n01
import linux_real_app_v3_n02_oracle as n02
import linux_real_app_v3_n04_oracle as n04
import linux_real_app_v3_n05_oracle as n05
import linux_real_app_v3_n05_files as fixed
import linux_real_app_v3_n06_files as files

ASSERTIONS_BY_VARIANT = {
    "open_cancel": (
        "dialog_full_target","dialog_parent_main_match","dialog_cancelled",
        "app_continues","product_census_complete","journal_finalized",
        "origin_retirement_before_runtime_stop","registry_destroy_accepted",
    ),
    "save_as_confirm": (
        "dialog_full_target","dialog_parent_main_match","save_bytes_exact",
        "app_continues","product_census_complete","journal_finalized",
        "origin_retirement_before_runtime_stop","registry_destroy_accepted",
    ),
    "unsaved_close_cancel": (
        "dialog_full_target","dialog_parent_main_match","dialog_cancelled",
        "app_continues","unsaved_bytes_preserved","product_census_complete",
        "journal_finalized","origin_retirement_before_runtime_stop",
        "registry_destroy_accepted",
    ),
    "close_confirm": (
        "dialog_full_target","dialog_parent_main_match","close_intent_recorded",
        "app_exit_observed","event_order_preserved","product_census_complete",
        "journal_finalized","origin_retirement_before_runtime_stop",
        "registry_destroy_accepted",
    ),
}
RECEIPT_REVISION = "eidolon-i6-n06-receipt-v1"
RECEIPT_FIELDS = {"schema","kind","run_id","case_id","variant",
                  "source_sha256","owner","targets","native","environment"}
NATIVE_KEYS = n02.NATIVE_KEYS | {"n05","n06"}
ENVIRONMENT_KEYS = n01.ENVIRONMENT_FIELDS | {"file_proof"}
STEP_NAMES = {
    "open_cancel": ("open_shortcut",),
    "save_as_confirm": n05.PHASES,
    "unsaved_close_cancel": ("open_shortcut","open_location","open_path",
        "edit_ctrl_end","edit_append","close_shortcut",
        "save_as_shortcut","save_location","save_path"),
    "close_confirm": ("open_shortcut","open_location","open_path",
        "edit_ctrl_end","edit_append","close_shortcut"),
}
DIALOG_NAMES = {
    "open_cancel": ("open",),
    "save_as_confirm": ("open","save_as"),
    "unsaved_close_cancel": ("open","unsaved_close","save_as"),
    "close_confirm": ("open","unsaved_close"),
}


def need(condition, why="I6_N06_RECEIPT_INVALID"):
    evidence.require(condition,why)


def same(a,b, why="I6_N06_RECEIPT_INVALID"):
    need(evidence.canonical(a)==evidence.canonical(b),why)


def owner_targets(native):
    owner,main=n02.case_owner_target(native)
    gui=native["n06"]
    need(type(gui) is dict and gui.get("variant") in ASSERTIONS_BY_VARIANT)
    names=DIALOG_NAMES[gui["variant"]]
    need(type(gui.get("dialogs")) is list
         and tuple(d["name"] for d in gui["dialogs"])==names,
         "I6_N06_DIALOG_MISSING")
    targets=[main]
    for d in gui["dialogs"]:
        need(type(d) is dict and set(d)=={"name","window","target","readback"})
        same(d["target"],d["readback"]["target"],"I6_N06_TARGET_MISMATCH")
        targets.append(n04.derive_target(owner,d["readback"],main["adapter_id"]))
    need(len({t["target_id"] for t in targets})==len(targets)
         and len({t["window"] for t in targets})==len(targets)
         and len({(t["window"],t["generation"]) for t in targets})==len(targets),
         "I6_N06_DIALOG_REUSED")
    return owner,targets


def verify_files(env,native,owner,variant):
    p=env["file_proof"]
    need(type(p) is dict and set(p)=={"schema","kind","run_id","capture_id",
        "owner","baseline","input","output","output_absent"}, "I6_N06_FILE_MISSING")
    need(p["schema"]==1 and p["kind"]==files.RECEIPT_KIND
         and p["owner"]=="disposable-supervisor"
         and p["run_id"]==native["run_id"]==env["run_id"]
         and p["capture_id"]==native["n06"]["capture_id"]==owner["capture_sha256"],
         "I6_N06_FILE_OWNER_MISMATCH")
    gui=native["n06"]
    need(gui["directory"]=="/home/eidolon-app/n06-"+native["run_id"]
         and gui["input"]==gui["directory"]+"/input.txt"
         and gui["output"]==gui["directory"]+"/saved-as.txt",
         "I6_N06_FILE_OWNER_MISMATCH")
    for name in ("baseline","input"):
        f=p[name]
        need(type(f) is dict and set(f)==files.FIELDS)
        need(all(type(f[k]) is int and f[k]>=0 for k in files.FIELDS-{"sha256"})
             and n02.b1.hexadecimal(f["sha256"],64)
             and f["uid"]==env["app"]["uid"]!=env["driver"]["uid"]
             and f["dev"]>0 and f["inode"]>0 and f["nlink"]==1)
    same(p["baseline"],p["input"],"I6_N06_INPUT_CHANGED")
    need(p["input"]["sha256"]==fixed.INPUT_SHA256
         and p["input"]["size"]==fixed.INPUT_LENGTH,
         "I6_N06_INPUT_CHANGED")
    wanted=variant in files.VARIANTS_WITH_OUTPUT
    need(type(p["output_absent"]) is bool and p["output_absent"] is not wanted)
    out=p["output"]
    if wanted:
        need(type(out) is dict and set(out)==files.FIELDS
             and all(type(out[k]) is int and out[k]>=0 for k in files.FIELDS-{"sha256"})
             and n02.b1.hexadecimal(out["sha256"],64)
             and out["uid"]==env["app"]["uid"]
             and out["nlink"]==1 and out["dev"]>0 and out["inode"]>0
             and (out["dev"],out["inode"])!=(p["input"]["dev"],p["input"]["inode"])
             and out["size"]==fixed.OUTPUT_LENGTH
             and out["sha256"]==fixed.OUTPUT_SHA256
             and out["ctime_ns"]>=p["baseline"]["ctime_ns"],
             "I6_N06_OUTPUT_WRONG")
    else:
        need(out is None, "I6_N06_UNEXPECTED_FILE")
    return True


def verify_sdk(native,targets,gui,observed):
    sdk=n02.ordinal_events(native["sdk_observations"],limit=128)
    need(set(sdk)==set(n02.SDK_KINDS))
    main=gui["main_target"]
    for idx,dialog in enumerate(gui["dialogs"],1):
        w=dialog["window"]
        target=dialog["target"]
        readback=dialog["readback"]
        need(type(w) is dict and set(w)=={"window","parent","life","frame","app_id"}
             and w["parent"]==main["window"] and w["life"]=="Mapped"
             and w["window"]==target["window"] and n02.b1.decimal(w["frame"])
             and target["session"]==main["session"] and target["scene"]==main["scene"]
             and readback["runtime"]==native["runtime"]
             and readback["create"]==targets[idx]["create_seq"]
             and n02.b1.decimal(readback["content_frame"])
             and n02.b1.decimal(readback["min_content_frame"])
             and int(readback["content_frame"])>=int(readback["min_content_frame"])
             and readback["backend"]["readback"] is True
             and len(readback["pixel_sha256"])==1
             and n02.b1.hexadecimal(readback["pixel_sha256"][0],64),
             "I6_N06_DIALOG_NOT_CURRENT")
    for target in targets:
        k={name:target[name] for name in ("session","window","scene","generation")}
        events=[]
        for kind in n02.SDK_KINDS:
            matches=[(pos,event) for pos,event in sdk.get(kind,[])
                     if event["data"]["target"]==k]
            need(len(matches)==1,"I6_N06_SDK_RETIREMENT_MISSING")
            pos,event=matches[0]
            need(event["runtime"]==native["runtime"]
                 and event["adapter"]==target["adapter_id"]
                 and event["data"]["create_seq"]==target["create_seq"])
            events.append(pos)
        need(all(a<b for a,b in zip(events,events[1:])),
             "I6_N06_SDK_RETIREMENT_ORDER")
    return True


def expected_delivery(phase,gui):
    fixed_count={"open_shortcut":4,"open_location":4,"edit_ctrl_end":4,
        "edit_append":len(fixed.APPEND_BYTES)*2,
        "close_shortcut":6,"save_as_shortcut":6,"save_location":8}
    if phase=="open_path":
        return len(gui["input"])*2 + 2*gui["input"].count("_")
    if phase=="save_path":
        return len(fixed.OUTPUT_NAME)*2 + 2*fixed.OUTPUT_NAME.count("_")
    return fixed_count[phase]


def verify_gui(native,owner,targets,variant):
    gui=native["n06"]
    need(type(gui) is dict and set(gui)=={"schema","variant","run_id","capture_id",
        "main_target","directory","input","output","steps","dialogs","cancel",
        "discard","continuation","main_destroyed","app_exit","final_key_request"})
    need(gui["schema"]==1 and gui["variant"]==variant
         and gui["run_id"]==native["run_id"]
         and gui["capture_id"]==owner["capture_sha256"])
    main=gui["main_target"]
    same(main,native["readback"]["target"])
    same(main,native["input"]["target"])
    steps=gui["steps"]
    need(type(steps) is list and tuple(s["phase"] for s in steps)==STEP_NAMES[variant])
    observations=n02.ordinal_events(native["observations"],limit=128)
    delivered=observations.get("n05_step_delivered",[])
    focus=observations.get("n05_focus_current",[])
    created=observations.get("n05_dialog_created",[])
    baselines=observations.get("n05_dialog_baseline",[])
    closed=observations.get("n05_dialog_closed",[])
    confirmed=observations.get("n05_confirm_delivered",[])
    need(len(delivered)==len(steps) and len(created)==len(baselines)==len(gui["dialogs"])
         and len(confirmed)==(2 if variant in ("save_as_confirm","unsaved_close_cancel")
                             else 1 if variant=="close_confirm" else 0),
         "I6_N06_DIALOG_EVIDENCE_MISSING")
    latest=0
    previous_request=0
    for i,step in enumerate(steps):
        same(step,delivered[i][1]["data"],"I6_N06_INPUT_MISMATCH")
        name=step["phase"]
        target=(main if name in ("open_shortcut","edit_ctrl_end","edit_append",
                                 "save_as_shortcut","close_shortcut")
                else gui["dialogs"][0]["target"] if name in ("open_location","open_path")
                else gui["dialogs"][-1]["target"])
        same(step["target"],target,"I6_N06_INPUT_WRONG_TARGET")
        expected=expected_delivery(name,gui)
        need(type(step["expected_delivery"]) is int and step["expected_delivery"]==expected
             and type(step["delivered_before"]) is int
             and type(step["delivered_after"]) is int
             and step["delivered_before"]>=latest
             and step["delivered_after"]>=step["delivered_before"]+expected,
             "I6_N06_INPUT_NOT_DELIVERED")
        start=int(step["first_key_request"])
        end=int(step["next_key_request"])
        need(start>=5000 and start>previous_request
             and end-start==expected and n02.b1.decimal(step["token"])
             and type(step["epoch"]) is int and step["epoch"]>=0,
             "I6_N06_INPUT_REQUEST_INVALID")
        matching=[(j,event) for j,event in focus if j<delivered[i][0]
            and event["data"]["target"]==target
            and event["data"]["request_id"]==str(start-1)
            and event["data"]["token"]==step["token"]
            and event["data"]["epoch"]==step["epoch"]]
        need(len(matching)==1,"I6_N06_STALE_FOCUS")
        latest=step["delivered_after"]
        previous_request=end-1
    need(n02.b1.decimal(gui["final_key_request"])
         and int(gui["final_key_request"])>previous_request)
    # No dialog can be admitted from a post-command GTK snapshot. The only
    # legitimate generation is absent from the pre-dispatch mapped baseline.
    for i,d in enumerate(gui["dialogs"]):
        bp,be=baselines[i]
        cp,ce=created[i]
        fact=be["data"]
        need(type(fact) is dict and set(fact)=={"phase","main_target","mapped_windows"}
             and fact["phase"]==d["name"])
        same(fact["main_target"],main)
        windows=fact["mapped_windows"]
        need(type(windows) is list and 1<=len(windows)<=64
             and len(windows)==len(set(windows)) and main["window"] in windows
             and d["window"]["window"] not in windows
             and all(n02.b1.decimal(x) for x in windows),
             "I6_N06_DIALOG_NOT_FRESH")
        shortcut={"open":"open_shortcut","save_as":"save_as_shortcut",
                  "unsaved_close":"close_shortcut"}[d["name"]]
        dp=[x[0] for x in delivered if x[1]["data"]["phase"]==shortcut]
        need(len(dp)==1 and bp<dp[0]<cp,"I6_N06_DIALOG_BASELINE_ORDER")
        need(ce["data"]["name"]==d["name"]
             and ce["data"]["target"]==d["target"]
             and ce["data"]["parent"]==main["window"]
             and ce["data"]["window"]==d["window"]["window"],
             "I6_N06_DIALOG_PARENT")
        if i>0:
            need(created[i-1][0]<bp)
    verify_sdk(native,targets,gui,observations)
    if variant=="save_as_confirm":
        # N05 independently checks its exact eight-step stream and both
        # confirmed GTK generations without changing the N05 oracle.
        same(native["n05"]["steps"],steps)
        same(native["n05"]["dialogs"],gui["dialogs"])
        n05.verify_gui(native,owner,targets)
    else:
        need(native["n05"] is None,"I6_N06_WRONG_REPLAY")
    if variant in ("open_cancel","unsaved_close_cancel"):
        cancel=gui["cancel"]
        cp,ce=n02.only(observations,"n06_dialog_cancelled")
        d=gui["dialogs"][-1] if variant=="open_cancel" else gui["dialogs"][1]
        need(type(cancel) is dict and cancel["key"]=="esc_down"
             and cancel["target"]==d["target"]
             and n02.b1.decimal(cancel["token"])
             and type(cancel["epoch"]) is int
             and cancel["delivered_after"]>=cancel["delivered_before"]+1
             and cancel["resets_after"]>cancel["resets_before"]
             and cancel["pressed_after"]==0,
             "I6_N06_CANCEL_NOT_DELIVERED")
        same(cancel,ce["data"])
        need(created[0 if variant=="open_cancel" else 1][0]<cp)
        need(any(row["data"]["name"]==d["name"]
                 and row["data"]["target"]==d["target"]
                 and pos<cp for pos,row in closed),
             "I6_N06_DIALOG_NOT_CLOSED")
        matching=[(pos,event) for pos,event in focus if pos<cp
            and event["data"]["target"]==cancel["target"]
            and event["data"]["token"]==cancel["token"]
            and event["data"]["epoch"]==cancel["epoch"]
            and event["data"]["request_id"]==str(int(cancel["request"])-1)]
        need(len(matching)==1)
    else:
        need(gui["cancel"] is None and not observations.get("n06_dialog_cancelled"))
    if variant!="close_confirm":
        continuation=gui["continuation"]
        pos,record=n02.only(observations,"n06_app_continues")
        same(continuation,record["data"])
        need(continuation["main_mapped"] is True and continuation["main_target"]==main
             and type(continuation["local_workers"]) is int
             and continuation["local_workers"]>0
             and pos>created[-1][0],"I6_N06_APP_NOT_CONTINUING")
        need(gui["main_destroyed"] is None and gui["app_exit"] is None
             and gui["discard"] is None)
        if variant=="unsaved_close_cancel":
            # Close Cancel must precede the *next* real Save As, and only that
            # GUI-created output proves the in-memory edit was retained.
            cancelpos=n02.only(observations,"n06_dialog_cancelled")[0]
            savedpos=n02.only(observations,"n06_save_confirmed")[0]
            need(cancelpos<savedpos and savedpos<pos
                 and len(gui["dialogs"])==3)
    else:
        need(gui["continuation"] is None)
        intent_pos,intent=n02.only(observations,"n06_close_intent")
        closepos=[pos for pos,event in delivered if event["data"]["phase"]=="close_shortcut"]
        discardpos,discard=n02.only(observations,"n06_discard_delivered")
        mainpos,main_observation=n02.only(observations,"n06_main_destroyed")
        exitpos,exit_observation=n02.only(observations,"n06_app_exit")
        d=gui["dialogs"][-1]
        need(type(intent["data"]) is dict
             and intent["data"]=={"main_target":main,
                 "action":"file.close-window","accelerator":"Ctrl+Shift+W"}
             and len(closepos)==1
             and intent_pos<closepos[0]<created[-1][0]<discardpos<mainpos<exitpos,
             "I6_N06_CLOSE_ORDER")
        same(gui["discard"],discard["data"])
        same(gui["main_destroyed"],main_observation["data"])
        same(gui["app_exit"],exit_observation["data"])
        need(discard["data"]["action"]=="gtk_dont_save_mnemonic"
             and discard["data"]["target"]==d["target"]
             and discard["data"]["expected_delivery"]==2
             and discard["data"]["delivered_after"]>=discard["data"]["delivered_before"]+2
             and main_observation["data"]["main_mapped"] is False
             and exit_observation["data"]["local_waited"] is True
             and exit_observation["data"]["local_workers"]==0
             and exit_observation["data"]["main_target"]==main,
             "I6_N06_APP_EXIT_NOT_OBSERVED")
        later=n02.only(observations,"origin_retired")
        stop=n02.only(observations,"runtime_stop_requested")
        need(exitpos<later[0]<stop[0],"I6_N06_CLOSE_ORDER")
    return observations


def validate_n06_natural_close_control(events):
    """R: validate real natural Close C2 order, without an invented Stop ACK.

    A process-exited terminal can be emitted asynchronously while Stop is
    in flight. Its `request_sequence` remains that of the last completed
    action. This is distinct from a regular N04 Stop ACK (which advances the
    sequence); the N04 oracle and all other N06 variants remain strict.
    """
    response={"inspect":"catalog","heartbeat":"heartbeat",
              "start":"started","stop":"stopped"}
    pending=None
    request_number=0
    wire_number=0
    actions=[]
    spontaneous_terminal=False
    for item in events:
        kind=item["kind"]
        if kind=="control_send_attempt":
            action=item["data"]["action"]
            need(action in response and pending is None
                 and not spontaneous_terminal,
                 "I6_N06_CLOSE_CONTROL_SEQUENCE")
            pending=action
        elif kind=="protocol_event":
            record=item["data"]["record"]
            wire_number+=1
            need(record["sequence"]==str(wire_number),
                 "I6_N06_CLOSE_CONTROL_SEQUENCE")
            received=record["event"]["type"]
            if received=="prepared":
                need(wire_number==1 and request_number==0
                     and pending is None and record["request_sequence"]=="0",
                     "I6_N06_CLOSE_CONTROL_SEQUENCE")
                continue
            need(pending is not None and received==response[pending],
                 "I6_N06_CLOSE_CONTROL_SEQUENCE")
            if received=="stopped":
                payload=record["event"]["payload"]
                need(pending=="stop" and not spontaneous_terminal
                     and record["request_sequence"]==str(request_number)
                     and payload["reason"]=="process_exited"
                     and all(payload[k] is True for k in (
                         "process_complete","pipes_eof",
                         "remote_cleaned","receipt_written")),
                     "I6_N06_CLOSE_CONTROL_SEQUENCE")
                spontaneous_terminal=True
            else:
                need(record["request_sequence"]==str(request_number+1),
                     "I6_N06_CLOSE_CONTROL_SEQUENCE")
                request_number+=1
            actions.append(pending)
            pending=None
    need(pending is None and spontaneous_terminal
         and wire_number==request_number+2
         and actions and actions[0]=="inspect" and actions[-1]=="stop"
         and actions.count("start")==actions.count("stop")==1,
         "I6_N06_CLOSE_CONTROL_SEQUENCE")
    return True


def verify_natural_close_product(base,env,source,observed):
    """N06-specific natural Close oracle: exact N02 success preconditions,
    with the sole documented *actual* APP_WINDOW_UNSUPPORTED outcome.

    This never patches the product reason or modifies the N02 oracle. All
    source-bound launch, controller, SDK, file, resource and ownership checks
    are repeated using the original raw evidence.
    """
    n02.strict_runtime(base,source,"mousepad_editor")
    need(type(env) is dict and env["run_id"]==base["run_id"]
         and env["owner_id"]==base["owner_id"]
         and env["source_sha256"]==base["source_sha256"]
         and env["revision"]=="eidolon-i6-n02-environment-v1")
    same(env["before"],env["after"],"I6_SOURCE_MISMATCH")
    need(env["runner"]["source_sha256"]==base["source_sha256"]
         and env["app"]["uid"]!=env["driver"]["uid"]
         and env["ssh"]["pinned_host_key_sha256"]==env["ssh"]["host_key_sha256"]
         and env["isolation"]["docker_socket_present"] is False,
         "I6_N06_ENVIRONMENT_INVALID")
    events,cap,context,pins,selection=n02.transport(base,env,"mousepad_editor")
    catalog=base["catalog"]["catalog"]
    need(base["catalog"]["code"]==0 and catalog["session"]==cap["session"]
         and catalog["request_id"]==cap["inspect_request"]
         and catalog["pending"] is False and catalog["error"] is None,
         "I6_N06_INSPECT_INVALID")
    n02.b1.validate_catalog(evidence.canonical(catalog["desktop_v3"]),context,pins)
    same(n02.only(observed,"n02_same_actor_catalog")[1]["data"]["scope"],
         context.scope(),"I6_N06_SELECTION_INVALID")
    same(n02.only(observed,"n02_same_actor_catalog")[1]["data"]["app_id"],
         selection.record()["app_id"],"I6_N06_SELECTION_INVALID")
    target,create=n02.sdk_and_readback(base)
    need(target["session"]==cap["session"])
    proof=n02.ready_and_input(base,target,create,cap)
    delivery=n02.only(observed,"n02_input_delivered")[1]["data"]
    need(delivery["focus_token"]==proof["focus_token"]
         and delivery["input_epoch"]==proof["epoch"]
         and delivery["target"]==target
         and delivery["delivery_delta"]==
             proof["delivered_after"]-proof["delivered_before"],
         "I6_N06_LAUNCH_INPUT_INVALID")
    for command,request in (("linux_desktop_v3_launch","32"),
                            ("focus_window","1001"),("key","1002"),("key","1003")):
        need(any(e["data"]["request_id"]==request
                 and e["data"]["command"]==command
                 and e["data"]["status"]==0
                 for _,e in observed.get("n02_command_accepted",[])))
        need(any(e["data"]["request_id"]==request
                 and e["data"]["status"]=="ACCEPTED"
                 for _,e in observed.get("command_complete",[])))

    snapshot=base["last_snapshot"]
    n02.exact(snapshot,{"schema","code","handle","runtime","registry"})
    need(snapshot["schema"]==1 and snapshot["code"]==0
         and snapshot["handle"]==base["runtime"]
         and n02._counts(snapshot["registry"],1,0)
         and n02._reclamation(snapshot["runtime"]["reclamation"],base["runtime"],True)
         and n02._shutdown(snapshot["runtime"]["shutdown"]),
         "I6_N06_CENSUS_INVALID")
    runtime=snapshot["runtime"]
    need(runtime["state"]=="Stopped" and runtime["app_sdk_bindings"]==0
         and runtime["commands_pending"]==0
         and runtime["desktop_v3_pending_targets"]==0
         and runtime["desktop_v3_finalizer_pending"] is False
         and runtime["desktop_v3_cleanup_error"] is None
         and runtime["panels"]==[] and runtime["error"] is None,
         "I6_N06_CENSUS_INVALID")
    for key in ("helper_workers","dependency_workers","compositor_workers"):
        n02._number(runtime[key])
        need(runtime[key]==0,"I6_N06_CENSUS_INVALID")
    app=runtime["app"]
    # The product reason is EXPECTED only on the observed close path. It is
    # never silently erased before running a pre-existing strict oracle.
    need(app["error"]=="APP_WINDOW_UNSUPPORTED"
         and app["state"]=="stopped" and app["ready"] is False
         and app["directory_cleaned"] is True and app["windows"]==[],
         "I6_N06_NATURAL_CLOSE_REASON_MISMATCH")
    exit_data=n02.only(observed,"n06_app_exit")[1]["data"]
    need(exit_data["app_error"]==app["error"]
         and exit_data["local_waited"] is True
         and exit_data["local_workers"]==0
         and exit_data["transport_state"] in ("CleanupPending","Stopped"),
         "I6_N06_NATURAL_CLOSE_REASON_MISMATCH")
    for key in ("clients","objects","surfaces","pixel_bytes",
                "pool_bytes","pool_count","memfd_fds_after","memfd_maps_after"):
        n02._number(app[key])
        need(app[key]==0,"I6_N06_CENSUS_INVALID")
    local=app["local"]
    need(local["workers"]==0 and local["owned_groups"]==0
         and local["waited"] is True and local["error"] is None,
         "I6_N06_CENSUS_INVALID")
    render=runtime["render"]
    for key in ("workers","contexts","latest_images","queued"):
        n02._number(render[key])
        need(render[key]==0,"I6_N06_CENSUS_INVALID")
    need(render["targets"]==[] and render["error"] is None)
    for key in ("textures","texture_bytes","decoration_texture_bytes","gl_error"):
        n02._number(render["composition"][key])
        need(render["composition"][key]==0,"I6_N06_CENSUS_INVALID")
    need(render["composition"]["error"] is None)
    transport=runtime["transport"]
    for key in ("workers","tokio_threads","owned_connections"):
        n02._number(transport[key])
        need(transport[key]==0,"I6_N06_CENSUS_INVALID")
    need(transport["forward"]["active"]==0
         and transport["desktop_pending"] is False
         and transport["pending_cleanup"]==[]
         and transport["error"] is None
         and transport["state"]=="Stopped",
         "I6_N06_CENSUS_INVALID")
    final=env["final"]
    need(all(final[k]==0 for k in (
        "app_process_count","driver_child_count","sshd_child_count","journal_entries"))
        and final["profile_lock_held"] is False
        and final["journal_lock_held"] is False,
        "I6_N06_CENSUS_INVALID")
    unlink=n02.only(events,"journal_unlinked")
    published=n02.only(events,"finalizer_published")
    need(unlink[0]<published[0]
         and unlink[1]["data"]["directory_fsynced"] is True
         and published[1]["data"]["status"]==0,
         "I6_N06_FINALIZER_MISSING")
    origin=n02.only(observed,"origin_retired")
    stop=n02.only(observed,"runtime_stop_requested")
    census=n02.only(observed,"last_live_census")
    destroy=n02.only(observed,"registry_destroy_return")
    need(origin[0]<stop[0]<census[0]<destroy[0]
         and stop[1]["data"]["status"]==0
         and destroy[1]["data"]["status"]==0
         and origin[1]["data"]["reclamation"]["origin_retired"] is True
         and destroy[1]["data"]["counts"]==base["registry_counts"]
         and base["sdk_retired"] is True
         and base["destroy_status"]==0
         and base["registry_counts"]=={"runtimes":0,"workers":0}
         and int(origin[1]["transport_sequence"])>=published[0],
         "I6_N06_RETIREMENT_INVALID")
    return True


def verify_natural_close_terminal(native,observed):
    """Independent C2 natural process exit, never Registry Accepted alone."""
    records=native["trace"]["events"]
    stop_sent=[(i,e) for i,e in enumerate(records,1)
               if e["kind"]=="control_send_attempt"
               and e["data"]["action"]=="stop"]
    terminal=[(i,e) for i,e in enumerate(records,1)
              if e["kind"]=="protocol_event"
              and e["data"]["record"]["event"]["type"]=="stopped"]
    need(len(stop_sent)==len(terminal)==1
         and stop_sent[0][0]<terminal[0][0],
         "I6_N06_TERMINAL_NOT_OBSERVED")
    payload=terminal[0][1]["data"]["record"]["event"]["payload"]
    need(payload["reason"]=="process_exited"
         and all(payload[k] is True for k in (
             "process_complete","pipes_eof","remote_cleaned","receipt_written")),
         "I6_N06_TERMINAL_NOT_OBSERVED")
    intent=n02.only(observed,"n06_close_intent")
    destroyed=n02.only(observed,"n06_main_destroyed")
    exited=n02.only(observed,"n06_app_exit")
    origin=n02.only(observed,"origin_retired")
    # In natural Close, the product may send C2 Stop as soon as the GTK
    # main is destroyed; by the time the Host *observes* process wait,
    # the terminal event can already have arrived. Do not impose the
    # unsupported "Host app-exit observation before C2 Stop" ordering.
    need(intent[0]<destroyed[0]<exited[0]<origin[0]
         and int(intent[1]["transport_sequence"])<stop_sent[0][0]
         and int(destroyed[1]["transport_sequence"])
             <=int(exited[1]["transport_sequence"])
         and int(origin[1]["transport_sequence"])>=terminal[0][0],
         "I6_N06_TERMINAL_ORDER")
    return True


def verify_product(native,env,source,observed,variant):
    # Source-bound controller/process/Wayland/renderer/SDK cleanup: same
    # trusted N02 product oracle, but each N06 child SDK generation gets its
    # own separate strict S verification above.
    base=copy.deepcopy(native)
    base.pop("n06")
    base.pop("n05")
    base["schema"]="eidolon-i6-n02-native-v1"
    base["variant"]="mousepad_editor"
    main=base["readback"]["target"]
    base["sdk_observations"]=[row for row in base["sdk_observations"]
                              if row["data"]["target"]==main]
    for i,event in enumerate(base["sdk_observations"],1):
        event["sequence"]=str(i)
    standard={k:copy.deepcopy(v) for k,v in env.items() if k!="file_proof"}
    standard["revision"]="eidolon-i6-n02-environment-v1"
    n01_env=copy.deepcopy(standard)
    n01_env["revision"]="eidolon-i6-n01-environment-v1"
    n01._environment(n01_env,native["run_id"],native["owner_id"],
                     native["source_sha256"])
    if variant=="close_confirm":
        validate_n06_natural_close_control(native["trace"]["events"])
    else:
        n04.validate_control_sequence(native["trace"]["events"])
    if variant=="close_confirm":
        # Preserve and bind the actual reason throughout observation and
        # final product census. A rewritten reason is never accepted.
        observed_reason=native["n06"]["app_exit"]["app_error"]
        final_reason=base["last_snapshot"]["runtime"]["app"]["error"]
        need(observed_reason==final_reason
             and final_reason in (None,"APP_WINDOW_UNSUPPORTED"),
             "I6_N06_NATURAL_CLOSE_REASON_MISMATCH")
        verify_natural_close_terminal(native,observed)
    if variant=="close_confirm" and (
            base["last_snapshot"]["runtime"]["app"]["error"]==
            "APP_WINDOW_UNSUPPORTED"):
        # Real natural-close result is an expected documented product outcome,
        # *never* normalized or rewritten as an N02 stopped-without-error.
        verify_natural_close_product(base,standard,source,observed)
    else:
        outcome=n02.verify(base,standard,source,"mousepad_editor")
        need(outcome["status"]=="pass"
             and outcome["verified_assertions"]==list(n02.ASSERTIONS),
             "I6_N06_PRODUCT_INCOMPLETE")
    origin=n02.only(observed,"origin_retired")
    stopping=n02.only(observed,"runtime_stop_requested")
    need(origin[0]<stopping[0],"I6_N06_PRODUCT_ORDER")
    trace=native["trace"]["events"]
    finalizers=[event for event in trace if event["kind"]=="finalizer_published"]
    need(len(finalizers)==1
         and int(origin[1]["transport_sequence"])>=int(finalizers[0]["sequence"]),
         "I6_N06_FINALIZER_MISSING")
    return True


def verify(native,env,source,variant):
    need(variant in ASSERTIONS_BY_VARIANT)
    n02.exact(native,NATIVE_KEYS)
    n02.exact(env,ENVIRONMENT_KEYS)
    need(native["schema"]=="eidolon-i6-n06-native-v1"
         and native["variant"]==variant
         and env["revision"]=="eidolon-i6-n06-environment-v1"
         and native["error"] is None and native["cleanup_error"] is None)
    gui=native["n06"]
    need(type(gui) is dict and gui["variant"]==variant)
    owner,targets=owner_targets(native)
    verify_files(env,native,owner,variant)
    observed=verify_gui(native,owner,targets,variant)
    verify_product(native,env,source,observed,variant)
    return {"status":"pass","reason":"I6_OK",
            "verified_assertions":list(ASSERTIONS_BY_VARIANT[variant]),
            "evidence_classes":["R","S"],"host_v3_acceptance_passed":False,
            "environment_disposal":"not_yet"}


def verify_receipt(receipt,*,expected_run_id,expected_source_sha256,
                   expected_owner,expected_targets,expected_source):
    n02.exact(receipt,RECEIPT_FIELDS)
    need(receipt["schema"]==1 and receipt["kind"]==RECEIPT_REVISION
         and receipt["case_id"]=="N06"
         and receipt["variant"] in ASSERTIONS_BY_VARIANT)
    need(receipt["run_id"]==expected_run_id
         and receipt["source_sha256"]==expected_source_sha256,
         "I6_SOURCE_MISMATCH")
    evidence._source(expected_source)
    need(expected_source["tree_sha256"]==expected_source_sha256)
    native,env=receipt["native"],receipt["environment"]
    need(native["run_id"]==env["run_id"]==expected_run_id)
    owner,targets=owner_targets(native)
    same(owner,expected_owner)
    same(owner,receipt["owner"])
    same(targets,expected_targets)
    same(targets,receipt["targets"])
    result=verify(native,env,expected_source,receipt["variant"])
    need(result["status"]=="pass")
    return {"status":"pass","verified_assertions":list(ASSERTIONS_BY_VARIANT[receipt["variant"]]),
            "census":{k:"complete" for k in evidence.RESOURCES},
            "target_ids":[t["target_id"] for t in targets]}


def invalid():
    return {"status":"invalid","reason":"I6_N06_RECEIPT_INVALID",
            "verified_assertions":[],"evidence_classes":[],
            "host_v3_acceptance_passed":False,
            "environment_disposal":"not_yet"}
