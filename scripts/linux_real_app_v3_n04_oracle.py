"""I6 N04 temporal proof primitives. Not an acceptance verdict.

Both monotonic clocks and the transport sequence are required. This module
cannot promote a producer status to PASS; a complete GUI/cleanup oracle must
invoke it before claiming any of N04's frozen assertions.
"""
from __future__ import annotations

import linux_real_app_v3_evidence as evidence

ASSERTIONS = (
    "idle_at_least_125s", "multiple_lease_heartbeats",
    "fresh_dialog_before_idle", "fresh_dialog_after_idle",
    "stop_responsive", "no_total_runtime_limit", "product_census_complete",
    "journal_finalized", "origin_retirement_before_runtime_stop",
    "registry_destroy_accepted",
)
MIN_NS = 125_000_000_000
MAX_SAMPLE_GAP_NS = 11_000_000_000  # 5s product cadence; observation allowance only
MAX_ACK_GAP_NS = 15_000_000_000     # original C2 lease; never extend the product
MAX_CLOCK_SKEW_NS = 2_000_000_000


def need(condition, reason="I6_N04_TEMPORAL_INVALID"):
    evidence.require(condition, reason)


def unique(rows, kind):
    selected = [row for row in rows if row.get("kind") == kind]
    need(len(selected) == 1)
    return selected[0]


def natural(value):
    need(type(value) is int and 0 <= value < (1 << 63))
    return value


def verify_temporal(native, external):
    """Reconstruct one capture's real idle heartbeat/ACK progression.

    native: read-only Registry Host acceptance trace and native observation
    watermark; external: separate supervisor timestamps at *stream receipt*.
    A synthetic fixture or an SSH-alive probe cannot satisfy this oracle.
    """
    need(type(native) is dict and type(external) is dict)
    run = native["run_id"]
    need(evidence.identity(run) and external["run_id"] == run)
    observations = native["observations"]
    trace = native["trace"]
    need(type(observations) is list and len(observations) <= 128)
    need(type(trace) is dict and trace["overflow"] is False)
    events = trace["events"]
    need(type(events) is list and 0 < len(events) <= evidence.MAX_TRACE_EVENTS)
    begin = unique(observations, "n04_idle_begin")
    end = unique(observations, "n04_idle_end")
    need(int(begin["sequence"]) < int(end["sequence"]))
    for row in observations:
        need(type(row) is dict and set(row) == {"sequence", "kind", "transport_sequence", "data"})
    a, b = natural(begin["data"]["ns"]), natural(end["data"]["ns"])
    need(b - a >= MIN_NS)
    markers = external["markers"]
    need(type(markers) is list and len(markers) == 2)
    need([m["kind"] for m in markers] == ["begin", "end"])
    need(all(set(m) == {"kind", "run_id", "at_ns"} and m["run_id"] == run
             for m in markers))
    x, y = (natural(m["at_ns"]) for m in markers)
    need(y - x >= MIN_NS and abs(a - x) <= MAX_CLOCK_SKEW_NS
         and abs(b - y) <= MAX_CLOCK_SKEW_NS)
    low, high = int(begin["transport_sequence"]), int(end["transport_sequence"])
    need(0 < low < high <= len(events))
    capture = None
    for n, event in enumerate(events, 1):
        need(event["sequence"] == str(n))
        cap = event["capture"]
        need(type(cap) is dict)
        if capture is None:
            capture = cap
        need(cap == capture and cap.get("capture_id") == native["n04"]["capture_id"],
             "I6_OWNER_MISMATCH")
    # Include the whole capture for pairing to tolerate a heartbeat already
    # in flight at the exact idle boundary. Its earlier send cannot count as
    # a fresh idle renewal. The per-request sequence is source wire data.
    pairs = []
    pending = None
    for pos, event in enumerate(events, 1):
        if event["kind"] == "control_send_attempt" and event["data"]["action"] == "heartbeat":
            need(pending is None, "I6_N04_HEARTBEAT_UNACKED")
            pending = pos
        elif event["kind"] == "protocol_event":
            record = event["data"]["record"]
            kind = record["event"]["type"]
            if kind == "heartbeat":
                need(pending is not None and pending < pos, "I6_N04_HEARTBEAT_UNACKED")
                seq = record["request_sequence"]
                need(type(seq) is str and seq.isascii() and seq.isdecimal()
                     and int(seq) > 0)
                pairs.append((pending, pos, int(seq),
                              record["event"]["payload"]["phase"]))
                pending = None
            elif low < pos <= high and kind in ("stopped", "pending"):
                need(False, "I6_N04_LEASE_LOST")
    need(pending is None, "I6_N04_HEARTBEAT_UNACKED")
    heartbeats = [(ack, seq) for send, ack, seq, phase in pairs
                  if low < send < ack <= high and phase == "started"]
    need(len(heartbeats) >= 20, "I6_N04_HEARTBEAT_UNACKED")
    # A non-started heartbeat inside idle is never a valid lease renewal.
    need(not any(low < send < ack <= high and phase != "started"
                 for send, ack, seq, phase in pairs), "I6_N04_LEASE_LOST")
    seqs = [seq for _, seq in heartbeats]
    need(len(set(seqs)) == len(seqs) and
         all(next_seq == seq + 1 for seq, next_seq in zip(seqs, seqs[1:])),
         "I6_N04_HEARTBEAT_SEQUENCE")
    samples = [row for row in observations if row["kind"] == "n04_lease_sample"]
    need(len(samples) >= 20)
    prior_time, prior_count = a, 0
    for row in samples:
        now = natural(row["data"]["ns"])
        watermark = int(row["transport_sequence"])
        need(prior_time < now <= b and now - prior_time <= MAX_SAMPLE_GAP_NS)
        need(low <= watermark <= high)
        # Every sample must observe an additional, unique wire heartbeat ACK;
        # reusing an earlier successful ACK is not ongoing lease proof.
        count = sum(pos <= watermark for pos, _ in heartbeats)
        need(count > prior_count and count - prior_count <= 2)
        prior_time, prior_count = now, count
    need(b - prior_time <= MAX_SAMPLE_GAP_NS and
         prior_count >= 20)
    return {"capture_id":capture["capture_id"], "duration_ns":b-a,
            "outer_duration_ns":y-x, "heartbeat_acks":len(heartbeats)}

# Product and GUI admission below are deliberately separate from the producer.
# N01/N02/N03 oracles are imported as-is; their meaning is never relaxed.

import copy
import linux_real_app_v3_n01_oracle as n01
import linux_real_app_v3_n02_oracle as n02

RECEIPT_REVISION = "eidolon-i6-n04-receipt-v1"
NATIVE_KEYS = n02.NATIVE_KEYS | {"n04"}
RECEIPT_FIELDS = {"schema", "kind", "run_id", "case_id", "variant",
                  "source_sha256", "owner", "targets", "native", "environment"}
ENVIRONMENT_KEYS = n01.ENVIRONMENT_FIELDS | {"supervisor"}
STOP_ACK_NS = 8_000_000_000  # original product C2 Stop ACK deadline


def same(a, b, reason="I6_N04_OWNER_MISMATCH"):
    need(evidence.canonical(a) == evidence.canonical(b), reason)


def observations_by_kind(native):
    return n02.ordinal_events(native["observations"], limit=128)


def require_one(events, kind):
    return n02.only(events, kind)


def derive_target(owner, readback, adapter):
    key = readback["target"]
    create = readback["create"]
    need(type(key) is dict and set(key) == {"session", "window", "scene", "generation"})
    need(all(n02.b1.decimal(k) for k in key.values()) and n02.b1.decimal(create))
    target_id = evidence.sha(evidence.canonical({
        "owner_id":owner["owner_id"], "target":key,
        "create_seq":create, "adapter_id":adapter,
    }))[:32]
    target = {
        "target_id":target_id,"owner_id":owner["owner_id"],
        "session":key["session"],"window":key["window"],
        "scene":key["scene"],"generation":key["generation"],
        "create_seq":create,"adapter_id":adapter,
    }
    evidence._target(target, {owner["owner_id"]:owner})
    return target


def case_owner_targets(native):
    """One runtime owner, three distinct observed SDK target identities.

    IDs are *derived* from readback and single actual SDK adapter, not supplied
    by a caller. Formal census includes both transient dialog target leases.
    """
    owner, main = n02.case_owner_target(native)
    n04 = native["n04"]
    need(type(n04) is dict and type(n04["dialogs"]) is list
         and len(n04["dialogs"]) == 2, "I6_N04_DIALOG_MISSING")
    adapter = main["adapter_id"]
    targets = [main]
    for dialog in n04["dialogs"]:
        target = derive_target(owner, dialog["dialog_readback"], adapter)
        same(dialog["dialog_target"], dialog["dialog_readback"]["target"])
        targets.append(target)
    need(len({t["target_id"] for t in targets}) == 3
         and len({t["window"] for t in targets}) == 3,
         "I6_N04_DIALOG_REUSED")
    return owner, targets


def verify_dialogs(native, owner, targets):
    info = native["n04"]
    need(set(info) == {"schema","run_id","capture_id","idle","main_target","dialogs"}
         and info["schema"] == 1 and info["run_id"] == native["run_id"])
    same(info["main_target"], native["readback"]["target"])
    same(info["main_target"], native["input"]["target"])
    need(info["capture_id"] == owner["capture_sha256"],
         "I6_OWNER_MISMATCH")
    ordered = observations_by_kind(native)
    first_done = require_one(ordered, "n04_idle_begin")[0]
    last_idle = require_one(ordered, "n04_idle_end")[0]
    dialog_done = ordered.get("n04_dialog_completed", [])
    need(len(dialog_done) == 2
         and dialog_done[0][0] < first_done < last_idle < dialog_done[1][0])
    sdk = n02.ordinal_events(native["sdk_observations"],limit=128)
    focus = ordered.get("n04_focus_current", [])
    need(len(focus)==4,"I6_N04_FOCUS_MISSING")
    main = info["main_target"]
    seen_requests = set()
    for ix, dialog in enumerate(info["dialogs"]):
        need(set(dialog) == {
            "phase","base_request","main_target","main_epoch","main_focus_token",
            "delivered_before","delivered_open","window","dialog_target",
            "dialog_readback","open_gui_windows","escape_epoch","escape_focus_token",
            "delivered_before_escape","delivered_closed",
            "closed_gui_windows","closed_windows"})
        phase, base = (("before", 2000),("after",3000))[ix]
        need(dialog["phase"] == phase and dialog["base_request"] == str(base))
        same(dialog["main_target"], main)
        child=dialog["window"]
        key=dialog["dialog_target"]
        readback=dialog["dialog_readback"]
        need(type(child) is dict and child["window"] == key["window"]
             and child["parent"] == main["window"] and child["life"] == "Mapped"
             and n02.b1.decimal(child["frame"])
             and child["window"] in dialog["open_gui_windows"]
             and child["window"] not in dialog["closed_gui_windows"]
             and main["window"] in dialog["closed_gui_windows"],
             "I6_N04_DIALOG_MISSING")
        need(child["window"] != main["window"]
             and key["session"] == main["session"]
             and key["scene"] == main["scene"]
             and key["generation"] != "0",
             "I6_OWNER_MISMATCH")
        need(not any(w["window"] == child["window"] and w["life"] == "Mapped"
                     for w in dialog["closed_windows"]),
             "I6_N04_DIALOG_NOT_DESTROYED")
        need(type(readback) is dict and readback["runtime"] == native["runtime"]
             and readback["target"] == key and readback["create"] == targets[ix+1]["create_seq"]
             and n02.b1.decimal(readback["content_frame"])
             and n02.b1.decimal(readback["min_content_frame"])
             and int(readback["content_frame"]) >= int(readback["min_content_frame"])
             and type(readback["layers"]) is list and bool(readback["layers"])
             and type(readback["pixel_sha256"]) is list
             and len(readback["pixel_sha256"]) == 1
             and n02.b1.hexadecimal(readback["pixel_sha256"][0],64)
             and readback["backend"]["readback"] is True,
             "I6_N04_DIALOG_NO_FRAME")
        # SDK evidence must stem from each independently admitted native
        # CreateSeq, attach, current content frame, and later same-target retire.
        sdk_seq=[]
        for kind in n02.SDK_KINDS:
            matches=[(pos,e) for pos,e in sdk.get(kind, [])
                     if e["data"]["target"] == key]
            need(len(matches) == 1, "I6_N04_SDK_MISSING")
            pos,e=matches[0]
            same(e["data"]["create_seq"], readback["create"])
            need(e["runtime"] == native["runtime"]
                 and e["adapter"] == targets[0]["adapter_id"],
                 "I6_OWNER_MISMATCH")
            sdk_seq.append(pos)
            if kind == "real_readback":
                same(e["data"]["readback"],readback)
        need(all(a < b for a,b in zip(sdk_seq,sdk_seq[1:])))
        # A positive dispatch demands real delivery counters PLUS one exact
        # completion per accepted request; no queued-only substitution.
        before, opened = dialog["delivered_before"],dialog["delivered_open"]
        before_esc, closed = dialog["delivered_before_escape"],dialog["delivered_closed"]
        need(all(type(n) is int and n>=0 for n in (before,opened,before_esc,closed))
             and opened >= before+4 and before_esc >= opened and closed >= before_esc+2)
        need(type(dialog["main_epoch"]) is int and dialog["main_epoch"]>=0
             and type(dialog["escape_epoch"]) is int and dialog["escape_epoch"]>=0
             and n02.b1.decimal(dialog["main_focus_token"])
             and n02.b1.decimal(dialog["escape_focus_token"]))
        for recorded, request, expected_target, expected_epoch, expected_token in (
            (focus[ix*2],str(base),main,dialog["main_epoch"],dialog["main_focus_token"]),
            (focus[ix*2+1],str(base+5),key,dialog["escape_epoch"],dialog["escape_focus_token"]),
        ):
            data=recorded[1]["data"]
            need(data["request_id"]==request and data["input_epoch"]==expected_epoch
                 and data["focus_token"]==expected_token)
            same(data["target"],expected_target)
        for offset, command in [(0,"focus_window"),(1,"key"),(2,"key"),
                                (3,"key"),(4,"key"),(5,"focus_window"),
                                (6,"key"),(7,"key")]:
            request=str(base+offset)
            need(request not in seen_requests)
            seen_requests.add(request)
            accepted=[e for _,e in ordered.get("n02_command_accepted",[])
                      if e["data"]["request_id"] == request
                      and e["data"]["command"] == command
                      and e["data"]["status"] == 0]
            completed=[e for _,e in ordered.get("command_complete",[])
                       if e["data"]["request_id"] == request
                       and e["data"]["status"] == "ACCEPTED"]
            need(len(accepted) == len(completed) == 1, "I6_N04_INPUT_NOT_DELIVERED")
        event=dialog_done[ix][1]["data"]
        need(event["phase"]==phase and event["window"]==child["window"]
             and event["parent"] == main["window"]
             and event["base_request"] == str(base))
    # The idle boundary cannot include launch/readiness, either dialog setup,
    # or cleanup. One application owner and target survive both fresh dialogs.
    need(info["dialogs"][0]["window"]["window"] !=
         info["dialogs"][1]["window"]["window"],
         "I6_N04_DIALOG_REUSED")
    return ordered


def verify_stop(native, ordered):
    trace=native["trace"]["events"]
    sends=[(i,e) for i,e in enumerate(trace,1)
           if e["kind"] == "control_send_attempt" and e["data"]["action"] == "stop"]
    acks=[(i,e) for i,e in enumerate(trace,1)
          if e["kind"] == "protocol_event"
          and e["data"]["record"]["event"]["type"] == "stopped"]
    need(len(sends)==len(acks)==1 and sends[0][0]<acks[0][0],
         "I6_N04_STOP_MISSING")
    payload=acks[0][1]["data"]["record"]["event"]["payload"]
    need(payload["reason"]=="stop" and
         all(payload[k] is True for k in
             ("process_complete","pipes_eof","remote_cleaned","receipt_written")),
         "I6_N04_STOP_INCOMPLETE")
    start=require_one(ordered,"n04_stop_send_seen")
    end=require_one(ordered,"n04_stop_ack_seen")
    need(start[0]<end[0] and
         int(start[1]["data"]["transport_sequence"])==sends[0][0] and
         int(end[1]["data"]["transport_sequence"])==acks[0][0])
    start_ns=natural(start[1]["data"]["ns"])
    end_ns=natural(end[1]["data"]["ns"])
    need(0 <= end_ns-start_ns <= STOP_ACK_NS,
         "I6_N04_STOP_LATE")
    # Both Stop ACK and its resource teardown precede Runtime shutdown.
    stop_requested=require_one(ordered,"runtime_stop_requested")
    origin=require_one(ordered,"origin_retired")
    need(end[0] < origin[0] < stop_requested[0],
         "I6_N04_RETIREMENT_ORDER")
    return True


def verify(native, environment, source, variant="idle_125s"):
    """No producer PASS; independently re-run Host product, GUI and R/S/T oracle."""
    need(variant=="idle_125s")
    n02.exact(native,NATIVE_KEYS)
    n02.exact(environment,ENVIRONMENT_KEYS)
    need(native["schema"]=="eidolon-i6-n04-native-v1"
         and native["variant"]==variant and
         environment["revision"]=="eidolon-i6-n04-environment-v1")
    owner, targets=case_owner_targets(native)
    same(native["n04"]["idle"],{
        "begin_ns":require_one(observations_by_kind(native),"n04_idle_begin")[1]["data"]["ns"],
        "end_ns":require_one(observations_by_kind(native),"n04_idle_end")[1]["data"]["ns"]})
    temporal=verify_temporal(native,environment["supervisor"])
    need(temporal["capture_id"]==owner["capture_sha256"])
    observed=verify_dialogs(native,owner,targets)
    verify_stop(native,observed)
    # Re-exercise the already-reviewed N02 real product oracle with the
    # *unchanged* main-target receipt schema. Only main-target SDK observations
    # are projected; extra dialog targets are checked independently above.
    base=copy.deepcopy(native)
    base.pop("n04")
    base["schema"]="eidolon-i6-n02-native-v1"
    base["variant"]="mousepad_editor"
    sdk=base["sdk_observations"]
    key=base["readback"]["target"]
    base["sdk_observations"]=[e for e in sdk if e["data"]["target"]==key]
    for i,event in enumerate(base["sdk_observations"],1):
        event["sequence"]=str(i)
    environment_n02={k:copy.deepcopy(v) for k,v in environment.items()
                     if k!="supervisor"}
    environment_n02["revision"]="eidolon-i6-n02-environment-v1"
    n01_environment=copy.deepcopy(environment_n02)
    n01_environment["revision"]="eidolon-i6-n01-environment-v1"
    n01._environment(n01_environment,native["run_id"],native["owner_id"],
                     native["source_sha256"])
    n02_result=n02.verify(base,environment_n02,source,"mousepad_editor")
    need(n02_result["status"]=="pass"
         and n02_result["verified_assertions"]==list(n02.ASSERTIONS),
         "I6_N04_PRODUCT_INVALID")
    return {"status":"pass","reason":"I6_OK",
            "verified_assertions":list(ASSERTIONS),"evidence_classes":["R","S"],
            "host_v3_acceptance_passed":False,"environment_disposal":"not_yet"}


def verify_receipt(receipt, *, expected_run_id, expected_source_sha256,
                   expected_owner, expected_targets, expected_source):
    n02.exact(receipt,RECEIPT_FIELDS)
    need(receipt["schema"] == 1 and receipt["kind"] == RECEIPT_REVISION
         and receipt["case_id"] == "N04" and receipt["variant"] == "idle_125s")
    need(receipt["run_id"] == expected_run_id
         and receipt["source_sha256"] == expected_source_sha256,
         "I6_SOURCE_MISMATCH")
    evidence._source(expected_source)
    need(expected_source["tree_sha256"]==expected_source_sha256)
    native, env=receipt["native"],receipt["environment"]
    need(native["run_id"]==expected_run_id and env["run_id"]==expected_run_id)
    owner, targets=case_owner_targets(native)
    same(expected_owner,owner)
    same(receipt["owner"],owner)
    same(expected_targets,targets)
    same(receipt["targets"],targets)
    result=verify(native,env,expected_source,"idle_125s")
    need(result["status"]=="pass" and
         result["verified_assertions"]==list(ASSERTIONS) and
         result["host_v3_acceptance_passed"] is False)
    return {"status":"pass","verified_assertions":list(ASSERTIONS),
            "census":{k:"complete" for k in evidence.RESOURCES},
            "target_ids":[t["target_id"] for t in targets]}


def invalid():
    return {"status":"invalid","reason":"I6_N04_RECEIPT_INVALID",
            "verified_assertions":[],"evidence_classes":[],
            "host_v3_acceptance_passed":False,
            "environment_disposal":"not_yet"}
