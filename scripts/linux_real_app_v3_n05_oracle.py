"""I6 N05 independent R/S file + GUI + product oracle.

Producer assertions/status never authorize PASS. Saved file facts are from a
separate disposable supervisor, not from the Rust GUI driver. The unchanged
N02 oracle is applied again to the original main-target product evidence.
"""
from __future__ import annotations

import copy
import linux_real_app_v3_evidence as evidence
import linux_real_app_v3_n01_oracle as n01
import linux_real_app_v3_n02_oracle as n02
import linux_real_app_v3_n04_oracle as n04
import linux_real_app_v3_n05_files as files

ASSERTIONS = (
    "input_exact_bytes", "output_exact_bytes", "input_unchanged",
    "file_owner_inode_checked", "dialog_parent_main_match", "real_gui_input",
    "product_census_complete", "journal_finalized",
    "origin_retirement_before_runtime_stop", "registry_destroy_accepted",
)
RECEIPT_REVISION = "eidolon-i6-n05-receipt-v1"
NATIVE_KEYS = n02.NATIVE_KEYS | {"n05"}
ENVIRONMENT_KEYS = n01.ENVIRONMENT_FIELDS | {"file_proof"}
RECEIPT_FIELDS = {"schema","kind","run_id","case_id","variant",
                  "source_sha256","owner","targets","native","environment"}
PHASES = (
    "open_shortcut","open_location","open_path","edit_ctrl_end","edit_append",
    "save_as_shortcut","save_location","save_path",
)


def need(value, message="I6_N05_RECEIPT_INVALID"):
    evidence.require(value, message)


def same(left, right, reason="I6_N05_OWNER_MISMATCH"):
    need(evidence.canonical(left) == evidence.canonical(right), reason)


def owner_targets(native):
    owner, main = n02.case_owner_target(native)
    gui = native["n05"]
    need(type(gui) is dict and type(gui.get("dialogs")) is list
         and len(gui["dialogs"]) == 2, "I6_N05_DIALOG_MISSING")
    targets = [main]
    for dialog in gui["dialogs"]:
        targets.append(n04.derive_target(owner, dialog["readback"],
                                         main["adapter_id"]))
        same(dialog["target"], dialog["readback"]["target"])
    need(len({t["target_id"] for t in targets}) == 3
         and len({t["window"] for t in targets}) == 3,
         "I6_N05_DIALOG_REUSED")
    return owner, targets


def verify_files(env, native, owner):
    proof=env["file_proof"]
    need(type(proof) is dict and set(proof) ==
         {"schema","kind","run_id","capture_id","owner","baseline","input","output"},
         "I6_N05_FILE_MISSING")
    need(proof["schema"] == 1
         and proof["kind"] == "eidolon-i6-n05-file-proof-v1"
         and proof["owner"] == "disposable-supervisor"
         and proof["run_id"] == native["run_id"] == env["run_id"]
         and proof["capture_id"] == native["n05"]["capture_id"]
         == owner["capture_sha256"], "I6_N05_FILE_OWNER_MISMATCH")
    fields={"dev","inode","uid","nlink","size","mtime_ns","ctime_ns","sha256"}
    for kind in ("baseline","input","output"):
        f=proof[kind]
        need(type(f) is dict and set(f)==fields)
        need(all(type(f[k]) is int and f[k] >= 0 for k in fields-{"sha256"}))
        need(f["dev"] > 0 and f["inode"] > 0 and f["nlink"] == 1
             and f["uid"] == env["app"]["uid"] and f["uid"] != env["driver"]["uid"])
        need(n02.b1.hexadecimal(f["sha256"],64))
    before, source, target = (proof[k] for k in ("baseline","input","output"))
    same(source,before, "I6_N05_INPUT_CHANGED")
    need(source["size"]==files.INPUT_LENGTH and source["sha256"]==files.INPUT_SHA256,
         "I6_N05_INPUT_MISMATCH")
    need(target["size"]==files.OUTPUT_LENGTH and target["sha256"]==files.OUTPUT_SHA256,
         "I6_N05_OUTPUT_MISMATCH")
    need((source["dev"],source["inode"]) != (target["dev"],target["inode"])
         and target["ctime_ns"] >= source["ctime_ns"], "I6_N05_FILE_ALIAS")
    gui=native["n05"]
    need(gui["input"] == gui["directory"]+"/"+files.INPUT_NAME
         and gui["output"] == gui["directory"]+"/"+files.OUTPUT_NAME
         and gui["directory"].startswith("/home/eidolon-app/n05-"+native["run_id"]),
         "I6_N05_FILE_OWNER_MISMATCH")
    # A saved hash by itself is not an assertion; the real GUI and cleanup
    # verification below must ALSO pass before claiming any frozen assertion.
    return True


def verify_dialog_baselines(observed, main, dialogs, delivered, created):
    """Both dialog freshness baselines precede their real shortcut commands.

    Reject a post-command 'previous' census: GTK may create the transient
    toplevel before the final key-complete result is collected.
    """
    baselines=observed.get("n05_dialog_baseline",[])
    need(len(baselines)==2, "I6_N05_DIALOG_BASELINE_MISSING")
    for i,phase,step in ((0,"open",0),(1,"save_as",5)):
        position,event=baselines[i]
        fact=event["data"]
        need(type(fact) is dict
             and set(fact)=={"phase","main_target","mapped_windows"}
             and fact["phase"]==phase, "I6_N05_DIALOG_BASELINE_INVALID")
        same(fact["main_target"],main)
        windows=fact["mapped_windows"]
        need(type(windows) is list and 1<=len(windows)<=64
             and all(n02.b1.decimal(w) for w in windows)
             and len(windows)==len(set(windows))
             and main["window"] in windows
             and dialogs[i]["window"]["window"] not in windows,
             "I6_N05_DIALOG_NOT_FRESH")
        need(position<delivered[step][0]<created[i][0],
             "I6_N05_DIALOG_BASELINE_ORDER")
    need(delivered[4][0] < baselines[1][0],
         "I6_N05_DIALOG_BASELINE_ORDER")
    return True


def verify_gui(native, owner, targets):
    gui=native["n05"]
    need(type(gui) is dict and set(gui) == {
        "schema","run_id","capture_id","main_target","directory","input",
        "output","steps","dialogs","final_key_request"})
    need(gui["schema"]==1 and gui["run_id"]==native["run_id"]
         and gui["capture_id"]==owner["capture_sha256"],
         "I6_N05_OWNER_MISMATCH")
    main=gui["main_target"]
    same(main,native["readback"]["target"])
    same(main,native["input"]["target"])
    need(type(gui["steps"]) is list and len(gui["steps"])==8
         and tuple(x["phase"] for x in gui["steps"])==PHASES)
    observed=n02.ordinal_events(native["observations"],limit=128)
    focus=observed.get("n05_focus_current",[])
    delivered=observed.get("n05_step_delivered",[])
    created=observed.get("n05_dialog_created",[])
    closed=observed.get("n05_dialog_closed",[])
    confirmed=observed.get("n05_confirm_delivered",[])
    need(len(focus)==10 and len(delivered)==8
         and len(created)==len(closed)==len(confirmed)==2
         and len(observed.get("n05_open_confirmed",[]))==1
         and len(observed.get("n05_save_confirmed",[]))==1,
         "I6_N05_INPUT_MISSING")
    verify_dialog_baselines(observed,main,gui["dialogs"],delivered,created)
    sdk=n02.ordinal_events(native["sdk_observations"],limit=128)
    preceding=0
    first_key=0
    for i,step in enumerate(gui["steps"]):
        same(step,delivered[i][1]["data"],"I6_N05_INPUT_MISMATCH")
        target = main if i in (0,3,4,5) else gui["dialogs"][0 if i in (1,2) else 1]["target"]
        same(step["target"],target)
        need(type(step["epoch"]) is int and step["epoch"]>=0
             and n02.b1.decimal(step["token"]))
        focus_fact=focus[i+(1 if i>=3 else 0)][1]["data"]
        same(focus_fact["token"],step["token"], "I6_N05_STALE_FOCUS")
        same(focus_fact["target"],target, "I6_N05_STALE_FOCUS")
        need(focus_fact["epoch"]==step["epoch"]
             and focus_fact["request_id"]==str(int(step["first_key_request"])-1),
             "I6_N05_STALE_FOCUS")
        count=step["expected_delivery"]
        expected = (4,4,len(gui["input"])*2,4,len(files.APPEND_BYTES)*2,6,4,
                    len(gui["output"])*2)[i]
        # Underscore needs a genuine Shift down/up in the physical key stream.
        expected += (gui["input"].count("_") if i==2 else
                     gui["output"].count("_") if i==7 else 0)*2
        need(type(count) is int and count==expected and count>0)
        before=step["delivered_before"]
        after=step["delivered_after"]
        need(type(before) is int and type(after) is int
             and before>=preceding and after>=before+count,
             "I6_N05_INPUT_NOT_DELIVERED")
        start=int(step["first_key_request"])
        end=int(step["next_key_request"])
        need(start>=5000 and end-start==count and start>first_key
             and n02.b1.decimal(step["first_key_request"])
             and n02.b1.decimal(step["next_key_request"]), "I6_N05_REQUEST_INVALID")
        preceding=after
        first_key=end-1
        need(focus[i+(1 if i>=3 else 0)][0]
             < delivered[i][0], "I6_N05_STALE_FOCUS")
    need(n02.b1.decimal(gui["final_key_request"])
         and int(gui["final_key_request"]) > first_key)
    # Open must close before edit; Save As must arise AFTER the editor changes.
    need(delivered[0][0]<created[0][0]<delivered[1][0]<delivered[2][0]
         <closed[0][0]<delivered[3][0]<delivered[4][0]<delivered[5][0]
         <created[1][0]<delivered[6][0]<delivered[7][0]<closed[1][0],
         "I6_N05_GUI_ORDER")
    for i,dialog in enumerate(gui["dialogs"]):
        need(type(dialog) is dict and set(dialog) ==
             {"name","window","target","readback"})
        need(dialog["name"]==("open","save_as")[i])
        win=dialog["window"]
        need(type(win) is dict and set(win)=={"window","parent","life","frame","app_id"})
        need(win["life"]=="Mapped" and win["parent"]==main["window"]
             and win["window"]==dialog["target"]["window"]
             and n02.b1.decimal(win["frame"])
             and dialog["target"]["session"]==main["session"]
             and dialog["target"]["scene"]==main["scene"],
             "I6_N05_DIALOG_PARENT")
        readback=dialog["readback"]
        need(readback["runtime"]==native["runtime"]
             and readback["target"]==dialog["target"]
             and readback["create"]==targets[i+1]["create_seq"]
             and n02.b1.decimal(readback["content_frame"])
             and n02.b1.decimal(readback["min_content_frame"])
             and int(readback["content_frame"])>=int(readback["min_content_frame"])
             and readback["backend"]["readback"] is True
             and len(readback["pixel_sha256"])==1
             and n02.b1.hexadecimal(readback["pixel_sha256"][0],64),
             "I6_N05_DIALOG_NO_FRAME")
        need(created[i][1]["data"]["name"]==dialog["name"]
             and created[i][1]["data"]["window"]==win["window"]
             and created[i][1]["data"]["parent"]==main["window"]
             and closed[i][1]["data"]["name"]==dialog["name"]
             and closed[i][1]["data"]["window"]==win["window"],
             "I6_N05_DIALOG_MISSING")
        confirm=confirmed[i][1]["data"]
        need(confirm["name"]==dialog["name"]
             and confirm["target"]==dialog["target"]
             and confirm["attempts"] in (1,2,3)
             and type(confirm["delivered_before"]) is int
             and type(confirm["delivered_after"]) is int
             and confirm["expected_delivery"]=={1:1,2:3,3:8}[confirm["attempts"]]
             and confirm["delivered_after"]>=confirm["delivered_before"]
                       +confirm["expected_delivery"],
             "I6_N05_CONFIRM_NOT_DELIVERED")
        actual_focus=focus[3 if i==0 else 9][1]["data"]
        same(confirm["token"],actual_focus["token"],"I6_N05_STALE_FOCUS")
        same(confirm["target"],actual_focus["target"],"I6_N05_STALE_FOCUS")
        need(confirm["epoch"]==actual_focus["epoch"]
             and closed[i][0]<confirmed[i][0],
             "I6_N05_CONFIRM_ORDER")
    for target in targets:
        key = {k:target[k] for k in ("session","window","scene","generation")}
        sequence=[]
        for kind in n02.SDK_KINDS:
            matches=[(i,e) for i,e in sdk.get(kind,[])
                     if e["data"]["target"]==key]
            need(len(matches)==1,"I6_N05_SDK_NOT_RETIRED")
            pos,event=matches[0]
            need(event["runtime"]==native["runtime"]
                 and event["adapter"]==target["adapter_id"]
                 and event["data"]["create_seq"]==target["create_seq"])
            sequence.append(pos)
        need(all(a<b for a,b in zip(sequence,sequence[1:])),
             "I6_N05_SDK_NOT_RETIRED")
    return observed


def verify_product(native, env, source, observed):
    # Full C2 Inspect→Heartbeat→Start→Stop request/ACK association.
    n04.validate_control_sequence(native["trace"]["events"])
    trace=native["trace"]["events"]
    sends=[(i,e) for i,e in enumerate(trace,1)
           if e["kind"]=="control_send_attempt" and e["data"]["action"]=="stop"]
    stops=[(i,e) for i,e in enumerate(trace,1)
           if e["kind"]=="protocol_event"
           and e["data"]["record"]["event"]["type"]=="stopped"]
    need(len(sends)==len(stops)==1 and sends[0][0]<stops[0][0],
         "I6_N05_STOP_MISSING")
    payload=stops[0][1]["data"]["record"]["event"]["payload"]
    need(payload["reason"]=="stop" and all(payload[k] is True
         for k in ("process_complete","pipes_eof","remote_cleaned","receipt_written")))
    # Product shutdown, not fixture rescue: reused N02 oracle establishes
    # complete ledger, finalizer and Registry destroy on this same capture.
    base=copy.deepcopy(native)
    base.pop("n05")
    base["schema"]="eidolon-i6-n02-native-v1"
    base["variant"]="mousepad_editor"
    key=base["readback"]["target"]
    base["sdk_observations"]=[e for e in base["sdk_observations"]
                              if e["data"]["target"]==key]
    for i,event in enumerate(base["sdk_observations"],1):
        event["sequence"]=str(i)
    standard={k:copy.deepcopy(v) for k,v in env.items() if k!="file_proof"}
    standard["revision"]="eidolon-i6-n02-environment-v1"
    n01_env=copy.deepcopy(standard)
    n01_env["revision"]="eidolon-i6-n01-environment-v1"
    n01._environment(n01_env,native["run_id"],native["owner_id"],
                     native["source_sha256"])
    result=n02.verify(base,standard,source,"mousepad_editor")
    need(result["status"]=="pass"
         and result["verified_assertions"]==list(n02.ASSERTIONS),
         "I6_N05_PRODUCT_INVALID")
    retired=n02.only(observed,"origin_retired")[0]
    stopping=n02.only(observed,"runtime_stop_requested")[0]
    need(retired<stopping and
         int(n02.only(observed,"origin_retired")[1]["transport_sequence"])>=stops[0][0],
         "I6_N05_RETIREMENT_ORDER")
    return True


def verify(native, env, source, variant="open_edit_save_as"):
    need(variant=="open_edit_save_as")
    n02.exact(native,NATIVE_KEYS)
    n02.exact(env,ENVIRONMENT_KEYS)
    need(native["schema"]=="eidolon-i6-n05-native-v1"
         and native["variant"]==variant
         and env["revision"]=="eidolon-i6-n05-environment-v1"
         and native["error"] is None and native["cleanup_error"] is None)
    owner, targets=owner_targets(native)
    verify_files(env,native,owner)
    observations=verify_gui(native,owner,targets)
    verify_product(native,env,source,observations)
    return {"status":"pass","reason":"I6_OK",
            "verified_assertions":list(ASSERTIONS),
            "evidence_classes":["R","S"],"host_v3_acceptance_passed":False,
            "environment_disposal":"not_yet"}


def verify_receipt(receipt, *, expected_run_id, expected_source_sha256,
                   expected_owner, expected_targets, expected_source):
    n02.exact(receipt,RECEIPT_FIELDS)
    need(receipt["schema"]==1 and receipt["kind"]==RECEIPT_REVISION
         and receipt["case_id"]=="N05" and receipt["variant"]=="open_edit_save_as")
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
    result=verify(native,env,expected_source)
    need(result["status"]=="pass"
         and result["verified_assertions"]==list(ASSERTIONS)
         and result["host_v3_acceptance_passed"] is False)
    return {"status":"pass","verified_assertions":list(ASSERTIONS),
            "census":{k:"complete" for k in evidence.RESOURCES},
            "target_ids":[t["target_id"] for t in targets]}


def invalid():
    return {"status":"invalid","reason":"I6_N05_RECEIPT_INVALID",
            "verified_assertions":[],"evidence_classes":[],
            "host_v3_acceptance_passed":False,
            "environment_disposal":"not_yet"}
