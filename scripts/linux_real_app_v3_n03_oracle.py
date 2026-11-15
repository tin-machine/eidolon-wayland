"""Independent, fail-closed I6 N03 Launch Admission R/T oracle.

Reconstructs genuine captures from Host transport trace and checks exact
request/command-completion pairs. Parser return is not a Runtime completion.
The double-launch case also re-runs the N02 positive product oracle; a second
request's BUSY never substitutes for successful first-owner retirement.
"""
from __future__ import annotations

import copy

import linux_desktop_v3_contract as b1
import linux_real_app_v3_evidence as evidence
import linux_real_app_v3_n01_oracle as n01
import linux_real_app_v3_n02_oracle as n02

EXPECTED = {
    "unknown_app_id": ("32", "INVALID_ARGUMENT", 3),
    "scope_changed": ("32", "NOT_READY", 0),
    "revision_changed": ("32", "NOT_READY", 0),
    "old_catalog": ("42", "NOT_READY", 0),
    "double_launch": ("33", "BUSY", 0),
}
ASSERTIONS = {
    (row["variant"]): tuple(row["assertions"])
    for row in evidence.load_manifest()["cases"] if row["case_id"] == "N03"
}
RECEIPT_REVISION = "eidolon-i6-n03-receipt-v1"
NATIVE_REVISION = "eidolon-i6-n03-native-v1"
ENVIRONMENT_REVISION = "eidolon-i6-n03-environment-v1"
RECEIPT_FIELDS = {
    "schema", "kind", "run_id", "case_id", "variant",
    "source_sha256", "owner", "target", "native", "environment",
}


def require(condition, reason="I6_N03_RECEIPT_INVALID"):
    evidence.require(condition, reason)


def same(left, right, reason="I6_N03_RECEIPT_INVALID"):
    require(evidence.canonical(left) == evidence.canonical(right), reason)


def exact(value, fields):
    require(type(value) is dict and set(value) == set(fields))


def only(events, kind):
    rows = events.get(kind, [])
    require(len(rows) == 1, "I6_TRACE_INVALID")
    return rows[0]


def observations(native):
    rows = native["observations"]
    require(type(rows) is list and len(rows) <= 128)
    result, watermark = {}, 0
    for i, row in enumerate(rows, 1):
        exact(row, {"sequence", "kind", "transport_sequence", "data"})
        require(row["sequence"] == str(i) and type(row["kind"]) is str
                and 0 < len(row["kind"]) <= 64 and type(row["data"]) is dict)
        require(b1.decimal(row["transport_sequence"]))
        tick = int(row["transport_sequence"])
        require(watermark <= tick <= len(native["trace"]["events"]), "I6_TRACE_INVALID")
        watermark = tick
        result.setdefault(row["kind"], []).append((i, row))
    return result


def capture_epochs(native, env):
    """Reconstruct each real Inspect capture; no old/new owner substitution."""
    trace = native["trace"]
    exact(trace, {"overflow", "events"})
    require(trace["overflow"] is False and type(trace["events"]) is list
            and 0 < len(trace["events"]) <= evidence.MAX_TRACE_EVENTS
            and len(evidence.canonical(trace)) <= evidence.MAX_TRACE_BYTES, "I6_TRACE_INVALID")
    epochs, order = {}, []
    for i, event in enumerate(trace["events"], 1):
        exact(event, {"sequence", "kind", "capture", "data"})
        require(event["sequence"] == str(i) and event["kind"] in n01.TRACE_FIELDS
                and len(evidence.canonical(event)) <= evidence.MAX_EVENT_BYTES)
        n01._capture(event["capture"])
        capture = event["capture"]
        require(type(capture) is dict)
        identifier = capture["capture_id"]
        if identifier not in epochs:
            require(event["kind"] == "capture_created")
            epochs[identifier] = {"capture": capture, "events": []}
            order.append(identifier)
        same(epochs[identifier]["capture"], capture, "I6_OWNER_MISMATCH")
        # The epochs are contiguous and never return to a retired capture.
        require(order[-1] == identifier, "I6_OWNER_MISMATCH")
        epochs[identifier]["events"].append(event)
    require(len(order) == (2 if native["variant"] == "old_catalog" else 1),
            "I6_OWNER_MISMATCH")
    for identifier in order:
        phase = epochs[identifier]
        capture = phase["capture"]
        created = phase["events"][0]
        require(created["kind"] == "capture_created")
        context = b1.Context.parse(evidence.canonical(created["data"]["context"]))
        pins = b1.Pins.parse(evidence.canonical(created["data"]["pins"]))
        require(context.record()["session"] == capture["session"]
                and context.record()["nonce"] == capture["nonce"]
                and context.record()["uid"] == env["app"]["uid"]
                and context.scope() == capture["scope"] == capture["context_digest"],
                "I6_OWNER_MISMATCH")
        same(pins.record(), env["before"]["pins"], "I6_SOURCE_MISMATCH")
        same(created["data"]["compiled_source_hashes"],
             native["compiled_source_hashes"], "I6_SOURCE_MISMATCH")
        require(b1.digest({
            "context": context.record(), "pins": pins.record(),
            "sources": native["compiled_source_hashes"],
            "inspect_request": capture["inspect_request"],
            "generation": capture["io_generation"],
        }) == identifier, "I6_OWNER_MISMATCH")
        phase["selection"] = {
            "scope": context.scope(),
            "revision": b1.catalog_revision(context, pins),
            "app_id": b1.DEMO,
        }
        phase["context"] = context
        phase["pins"] = pins
    return [epochs[x] for x in order]


def owner_target(native):
    epochs = capture_epochs_without_context(native)
    cap = epochs[-1]
    owner_id = evidence.sha((
        native["run_id"] + ":" + native["runtime"] + ":" + cap["capture_id"]
    ).encode("ascii"))[:32]
    connect = "40" if native["variant"] == "old_catalog" else "30"
    if native["variant"] == "double_launch":
        normalized = normalized_n02(native)
        return n02.case_owner_target(normalized)
    owner = {
        "owner_id": owner_id, "runtime": native["runtime"],
        "admission_epoch": "2" if native["variant"] == "old_catalog" else "1",
        "connect_request": connect, "launch_request": None,
        "session": cap["session"], "capture_sha256": cap["capture_id"],
        "context_sha256": cap["context_digest"], "io_generation": cap["io_generation"],
        "nonce": cap["nonce"], "scope": cap["scope"], "native_origin": None,
    }
    evidence._owner(owner)
    return owner, None


def capture_epochs_without_context(native):
    seen = []
    for row in native["trace"]["events"]:
        capture = row["capture"]
        if capture is not None and (
            not seen or capture["capture_id"] != seen[-1]["capture_id"]
        ):
            seen.append(capture)
    require(len(seen) == (2 if native["variant"] == "old_catalog" else 1))
    return seen


def normalized_n02(native):
    converted = copy.deepcopy(native)
    converted["schema"] = n02.RECEIPT_REVISION.replace("receipt", "native")
    # The native schema is not the typed receipt revision.
    converted["schema"] = "eidolon-i6-n02-native-v1"
    converted["variant"] = "gtk3_desktop"
    return converted


def phase_no_launch(native, env, owner, phase, catalog):
    """Use N01's independent strict protocol and physical cleanup checker."""
    stage = copy.deepcopy(native)
    stage["trace"] = {
        "overflow": False,
        "events": [dict(e, sequence=str(i)) for i, e in enumerate(phase["events"], 1)],
    }
    stage["start_count"] = 0
    stage["catalog"] = catalog
    phase_owner = copy.deepcopy(owner)
    capture = phase["capture"]
    for source, destination in (
        ("session", "session"), ("io_generation", "io_generation"),
        ("nonce", "nonce"), ("scope", "scope"),
        ("capture_id", "capture_sha256"), ("context_digest", "context_sha256"),
    ):
        phase_owner[destination] = capture[source]
    phase_owner["launch_request"] = None
    phase_owner["native_origin"] = None
    missing, failures, seen, kinds = n01._trace(stage, phase_owner, env)
    require(not missing and not failures and seen is not None,
            "I6_N03_CLEANUP_UNPROVEN")
    require("no_local_allocation_sealed" in kinds
            and "remote_cleanup_observed" in kinds
            and "finalizer_published" in kinds
            and "controller_stop_requested" in kinds)
    remote = only({k: [(int(v["sequence"]), v) for v in rows]
                   for k, rows in kinds.items()}, "remote_cleanup_observed")[1]["data"]
    require(remote["start_attempted"] is False
            and remote["remote"]["started"] is False)
    require(not any(e["kind"] == "control_send_attempt"
                    and e["data"]["action"] == "start" for e in phase["events"]))
    require(not any(e["kind"] == "protocol_event"
                    and e["data"]["record"]["event"]["type"] == "started"
                    for e in phase["events"]))
    return kinds


def request_and_status(native, seen, phases, variant):
    request, expected, immediate = EXPECTED[variant]
    submit = only(seen, "n03_launch_submitted")
    registry = only(seen, "n03_registry_return")
    rejected = only(seen, "n03_rejection_observed")
    boundary = only(seen, "n03_rejection_boundary")
    require(submit[0] < registry[0] < rejected[0] < boundary[0],
            "I6_TRACE_INVALID")
    cmd = submit[1]["data"]
    exact(cmd, {"variant", "request_id", "session", "selection"})
    require(cmd["variant"] == variant and cmd["request_id"] == request)
    require(registry[1]["data"] == {"request_id": request, "status": immediate})
    require(rejected[1]["data"] == {
        "request_id": request, "status": expected,
        "completion": None if expected == "INVALID_ARGUMENT" else expected,
    })
    completes = [(i, o["data"]) for i, o in seen.get("command_complete", [])
                 if o["data"]["request_id"] == request]
    if expected == "INVALID_ARGUMENT":
        require(not completes)
    else:
        require(len(completes) == 1 and completes[0][0] < rejected[0]
                and completes[0][1]["status"] == expected, "I6_TRACE_INVALID")
    # No arbitrary unrelated command's rejection can satisfy this request.
    require(sorted((o["data"]["request_id"], o["data"]["status"])
                   for _, o in seen.get("command_complete", [])
                   if o["data"]["status"] != "ACCEPTED")
            == ([] if expected == "INVALID_ARGUMENT" else [(request, expected)]),
            "I6_TRACE_INVALID")
    require(all(o["data"]["request_id"] == request
                for _, o in seen.get("n03_launch_submitted", [])))
    require(cmd["session"] == phases[-1]["capture"]["session"])
    current = phases[-1]["selection"]
    selected = cmd["selection"]
    exact(selected, {"scope", "revision", "app_id"})
    if variant == "unknown_app_id":
        require(selected["scope"] == current["scope"]
                and selected["revision"] == current["revision"]
                and selected["app_id"] not in {b1.DEMO, b1.EDITOR}
                and type(selected["app_id"]) is str)
    elif variant == "scope_changed":
        require(b1.hexadecimal(selected["scope"], 64)
                and selected["scope"] != current["scope"]
                and selected["revision"] == current["revision"]
                and selected["app_id"] == current["app_id"])
        b1.Selection.parse(evidence.canonical(selected))
    elif variant == "revision_changed":
        require(b1.hexadecimal(selected["revision"], 64)
                and selected["revision"] != current["revision"]
                and selected["scope"] == current["scope"]
                and selected["app_id"] == current["app_id"])
        b1.Selection.parse(evidence.canonical(selected))
    elif variant == "old_catalog":
        require(len(phases) == 2 and selected == phases[0]["selection"]
                and selected != current, "I6_OWNER_MISMATCH")
        b1.Selection.parse(evidence.canonical(selected))
    else:
        same(selected, current, "I6_OWNER_MISMATCH")
        b1.Selection.parse(evidence.canonical(selected))
    data = boundary[1]["data"]
    exact(data, {"request_id", "app", "pending_targets", "sdk_bindings",
                 "render_targets", "origin_retired", "launch_request",
                 "start_attempted", "presentation"})
    require(data["request_id"] == request)
    if variant != "double_launch":
        require(data["app"] is None and data["pending_targets"] == 0
                and data["sdk_bindings"] == 0 and data["render_targets"] == []
                and data["start_attempted"] is False
                and data["launch_request"] is None)
    else:
        require(type(data["app"]) is dict and data["origin_retired"] is False
                and data["launch_request"] == "32")
    return submit, rejected, boundary


def verify(native, env, source, variant):
    """Return fixed assertions only after independently checking every fact."""
    require(variant in EXPECTED and set(ASSERTIONS) == set(EXPECTED))
    exact(native, n02.NATIVE_KEYS)
    require(native["schema"] == NATIVE_REVISION and native["variant"] == variant
            and evidence.identity(native["run_id"])
            and evidence.identity(native["owner_id"]) and b1.decimal(native["runtime"])
            and native["error"] is None and native["cleanup_error"] is None)
    evidence._source(source)
    evidence._source(native["build_source"])
    require(source["build_features"] == []
            and native["build_source"]["build_features"] == ["host-acceptance"])
    for field in evidence.SOURCE_FIELDS - {"build_features"}:
        same(source[field], native["build_source"][field], "I6_SOURCE_MISMATCH")
    require(native["source_sha256"] == source["tree_sha256"], "I6_SOURCE_MISMATCH")
    for field, value in evidence.source_input_hashes().items():
        same(native[field], value, "I6_SOURCE_MISMATCH")
    require(type(env) is dict and env["revision"] == ENVIRONMENT_REVISION)
    n01_env = copy.deepcopy(env)
    n01_env["revision"] = n01.ENVIRONMENT_REVISION
    n01._environment(n01_env, native["run_id"],
                     native["owner_id"], native["source_sha256"])
    require(env["runner"]["source_sha256"] == source["tree_sha256"])
    phases = capture_epochs(native, env)
    current = phases[-1]["capture"]
    require(native["owner_id"] == evidence.sha((
        native["run_id"] + ":" + native["runtime"] + ":" + current["capture_id"]
    ).encode("ascii"))[:32], "I6_OWNER_MISMATCH")
    seen = observations(native)
    # All actual inspected catalog evidence must remain separately attributable
    # to its session/capture. A changed string is not an old capture.
    if variant != "double_launch":
        catalogs = seen.get("n03_catalog", [])
        require(len(catalogs) == len(phases))
        for (ordinal, row), phase in zip(catalogs, phases):
            data = row["data"]
            exact(data, {"session", "inspect_request", "selection", "capture_id", "registry"})
            cap = phase["capture"]
            require(data["session"] == cap["session"]
                    and data["inspect_request"] == cap["inspect_request"]
                    and data["capture_id"] == cap["capture_id"])
            same(data["selection"], phase["selection"], "I6_OWNER_MISMATCH")
            same(data["registry"]["catalog"]["desktop_v3"],
                 b1.catalog_descriptor(phase["context"], phase["pins"]),
                 "I6_OWNER_MISMATCH")
            if phase is phases[-1]:
                same(native["catalog"], data["registry"], "I6_OWNER_MISMATCH")
        # Product cleanup/NoLocalAllocation is checked for BOTH old and current
        # captures. One new Runtime with a zero ledger cannot prove an old one.
        owner, _ = owner_target(native)
        for phase, (_, row) in zip(phases, catalogs):
            phase_no_launch(native, env, owner, phase, row["data"]["registry"])
        require(native["start_count"] == 0
                and native["sdk_observations"] is None
                and native["readback"] is None and native["ready"] is None
                and native["input"] is None and native["sdk_retired"] is False)
        require(n01._live_census(native["last_snapshot"], native["runtime"]),
                "I6_N03_CLEANUP_UNPROVEN")
        if variant == "old_catalog":
            old = only(seen, "n03_old_capture_retired")
            require(old[1]["data"] == {"old_session": phases[0]["capture"]["session"],
                                     "old_selection": phases[0]["selection"]})
            require(catalogs[0][0] < old[0] < catalogs[1][0])
            old_end = next(e for e in phases[0]["events"] if e["kind"] == "finalizer_published")
            new_start = phases[1]["events"][0]
            require(int(old_end["sequence"]) < int(new_start["sequence"]),
                    "I6_OWNER_MISMATCH")
    else:
        require(not seen.get("n03_catalog") and not seen.get("n03_old_capture_retired"))
        normalized = normalized_n02(native)
        normal_env = copy.deepcopy(env)
        normal_env["revision"] = "eidolon-i6-n02-environment-v1"
        result = n02.verify(normalized, normal_env, source, "gtk3_desktop")
        require(result["status"] == "pass")
        first = only(seen, "n03_first_live")
        require(first[1]["data"]["request_id"] == "32"
                and first[1]["data"]["completion"] == "ACCEPTED"
                and first[1]["data"]["launch_request"] == "32"
                and first[1]["data"]["origin_retired"] is False
                and type(first[1]["data"]["app_state"]) is str)
        require(any(o["data"]["request_id"] == "32"
                    and o["data"]["status"] == "ACCEPTED"
                    and i < first[0]
                    for i, o in seen.get("command_complete", [])))
        require(native["start_count"] == 1)
    submitted, rejected, boundary = request_and_status(native, seen, phases, variant)
    if variant == "double_launch":
        require(only(seen, "n03_first_live")[0] < submitted[0])
    # The actual same Runtime must finalize before Stop and destruction.
    origin_rows = seen.get("origin_retired", [])
    require(len(origin_rows) == (2 if variant == "old_catalog" else 1))
    stop = only(seen, "runtime_stop_requested")
    shut = only(seen, "runtime_shutdown_complete")
    census = only(seen, "last_live_census")
    destroyed = only(seen, "registry_destroy_return")
    require(boundary[0] < origin_rows[-1][0] < stop[0] < shut[0]
            < census[0] < destroyed[0], "I6_TRACE_INVALID")
    require(stop[1]["data"]["status"] == 0
            and destroyed[1]["data"]["status"] == 0
            and native["destroy_status"] == 0
            and destroyed[1]["data"]["counts"] == {"runtimes":0,"workers":0}
            and native["registry_counts"] == {"runtimes":0,"workers":0})
    same(native["last_snapshot"], census[1]["data"]["snapshot"])
    require(all(env["final"][k] == 0 for k in
                ("app_process_count", "driver_child_count", "sshd_child_count", "journal_entries"))
            and env["final"]["profile_lock_held"] is False
            and env["final"]["journal_lock_held"] is False)
    require(all(e["data"]["status"] == 0 for p in phases
                for e in p["events"] if e["kind"] == "finalizer_published"))
    require(sum(e["kind"] == "finalizer_published"
                for p in phases for e in p["events"]) == len(phases))
    return {
        "status":"pass", "reason":"I6_OK",
        "verified_assertions":list(ASSERTIONS[variant]),
        "evidence_classes":["R","T"],
        "host_v3_acceptance_passed":False,
        "environment_disposal":"not_yet",
    }


def verify_receipt(receipt, *, expected_run_id, expected_source_sha256,
                   expected_owner, expected_target, expected_source):
    exact(receipt, RECEIPT_FIELDS)
    require(type(receipt["schema"]) is int and receipt["schema"] == 1
            and receipt["kind"] == RECEIPT_REVISION
            and receipt["case_id"] == "N03"
            and receipt["variant"] in EXPECTED)
    require(receipt["run_id"] == expected_run_id
            and receipt["source_sha256"] == expected_source_sha256,
            "I6_SOURCE_MISMATCH")
    native, env = receipt["native"], receipt["environment"]
    owner, target = owner_target(native)
    same(receipt["owner"], owner, "I6_OWNER_MISMATCH")
    same(expected_owner, owner, "I6_OWNER_MISMATCH")
    same(receipt["target"], target, "I6_OWNER_MISMATCH")
    same(expected_target, target, "I6_OWNER_MISMATCH")
    require(native["run_id"] == expected_run_id
            and env["run_id"] == expected_run_id
            and env["owner_id"] == owner["owner_id"])
    outcome = verify(native, env, expected_source, receipt["variant"])
    require(outcome["status"] == "pass"
            and outcome["verified_assertions"] == list(ASSERTIONS[receipt["variant"]]))
    return {
        "status":"pass",
        "verified_assertions":list(ASSERTIONS[receipt["variant"]]),
        "census": {
            resource: ("complete" if target is not None
                       or resource not in n01.UNALLOCATED_RESOURCES else "not_created")
            for resource in evidence.RESOURCES
        },
        "target_id": target["target_id"] if target is not None else None,
    }


def invalid():
    return {"status":"invalid","reason":"I6_N03_RECEIPT_INVALID",
            "verified_assertions":[],"evidence_classes":[],
            "host_v3_acceptance_passed":False,
            "environment_disposal":"not_yet"}
