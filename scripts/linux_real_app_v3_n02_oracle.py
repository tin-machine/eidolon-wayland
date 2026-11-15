"""Independent, fail-closed N02 Host R/S/T checkpoint oracle.

This verifies product snapshots/transport and the synthetic SDK owner's
bounded trace. It does NOT verify Meta SDK objects, semantic file content,
environment deletion, Android/Quest or the full I6 87-variant contract.
"""
from __future__ import annotations

import linux_desktop_v3_contract as b1
import linux_desktop_v3_wire as wire
import linux_real_app_v3_evidence as evidence
from linux_real_app_v3_n01_oracle import _counts, _reclamation, _shutdown, _number

CASES = {"gtk3_desktop": b1.DEMO, "mousepad_editor": b1.EDITOR}
ASSERTIONS = (
    "same_actor_catalog", "started_same_origin", "fixed_main",
    "real_content_frame", "b1_ready", "input_gate_current",
    "product_census_complete", "journal_finalized",
    "origin_retirement_before_runtime_stop", "registry_destroy_accepted",
)
NATIVE_KEYS = {
    "schema", "run_id", "variant", "runtime", "owner_id", "source_sha256",
    "build_source", "inventory_sha256", "compiled_source_hashes",
    "trace", "catalog", "observations", "sdk_observations", "sdk_retired",
    "readback", "ready", "input", "start_count", "last_snapshot",
    "destroy_status", "registry_counts", "error", "cleanup_error",
}
SDK_KINDS = (
    "native_create_reserved", "registered", "host_surface_attached",
    "sdk_current", "real_readback", "create_queued", "create_applied",
    "sdk_retiring", "sdk_retired",
)


def require(ok, reason="I6_N02_RECEIPT_INVALID"):
    evidence.require(ok, reason)


def exact(value, keys):
    require(type(value) is dict and set(value) == set(keys))


def same(a, b, reason="I6_N02_RECEIPT_INVALID"):
    require(evidence.canonical(a) == evidence.canonical(b), reason)


def dec(value):
    require(b1.decimal(value))


def ordinal_events(values, *, limit, kind="kind"):
    require(type(values) is list and len(values) <= limit)
    results = {}
    for i, event in enumerate(values, 1):
        require(type(event) is dict and event.get("sequence") == str(i))
        label = event.get(kind)
        require(type(label) is str and len(label) <= 64)
        results.setdefault(label, []).append((i, event))
    return results


def only(mapping, name):
    values = mapping.get(name, [])
    require(len(values) == 1)
    return values[0]


def strict_runtime(native, source, variant):
    exact(native, NATIVE_KEYS)
    require(native["schema"] == "eidolon-i6-n02-native-v1"
            and native["variant"] == variant and native["variant"] in CASES)
    require(evidence.identity(native["run_id"]) and evidence.identity(native["owner_id"]))
    dec(native["runtime"])
    require(native["error"] is None and native["cleanup_error"] is None)
    evidence._source(source)
    evidence._source(native["build_source"])
    require(source["build_features"] == []
            and native["build_source"]["build_features"] == ["host-acceptance"])
    for name in evidence.SOURCE_FIELDS - {"build_features"}:
        same(source[name], native["build_source"][name], "I6_SOURCE_MISMATCH")
    require(native["source_sha256"] == source["tree_sha256"], "I6_SOURCE_MISMATCH")
    hashes = evidence.source_input_hashes()
    same({k: native[k] for k in hashes}, hashes, "I6_SOURCE_MISMATCH")
    require(type(native["start_count"]) is int and native["start_count"] == 1)
    require(native["sdk_retired"] is True)
    return native["run_id"]


def transport(native, environment, variant):
    trace = native["trace"]
    exact(trace, {"overflow", "events"})
    require(trace["overflow"] is False)
    require(len(evidence.canonical(trace)) <= evidence.MAX_TRACE_BYTES, "I6_TRACE_INVALID")
    events = ordinal_events(trace["events"], limit=evidence.MAX_TRACE_EVENTS)
    captures = [event["capture"] for event in trace["events"] if event["capture"] is not None]
    require(captures and all(c == captures[0] for c in captures), "I6_OWNER_MISMATCH")
    cap = captures[0]
    context_event = only(events, "capture_created")[1]
    context = b1.Context.parse(evidence.canonical(context_event["data"]["context"]))
    pins = b1.Pins.parse(evidence.canonical(context_event["data"]["pins"]))
    same(pins.record(), environment["before"]["pins"], "I6_SOURCE_MISMATCH")
    same(context_event["data"]["compiled_source_hashes"], native["compiled_source_hashes"],
         "I6_SOURCE_MISMATCH")
    require(context.record()["session"] == cap["session"]
            and context.record()["nonce"] == cap["nonce"]
            and context.record()["uid"] == environment["app"]["uid"]
            and context.scope() == cap["scope"] == cap["context_digest"],
            "I6_OWNER_MISMATCH")
    capture_hash = b1.digest({
        "context":context.record(), "pins":pins.record(),
        "sources":native["compiled_source_hashes"],
        "inspect_request":cap["inspect_request"], "generation":cap["io_generation"],
    })
    require(cap["capture_id"] == capture_hash, "I6_OWNER_MISMATCH")
    require(native["owner_id"] == evidence.sha(
        (native["run_id"] + ":" + native["runtime"] + ":" + capture_hash).encode("ascii"))[:32],
        "I6_OWNER_MISMATCH")
    require(cap["inspect_request"] == "31")
    starts = [(i, e) for i, e in events.get("control_send_attempt", [])
              if e["data"]["action"] == "start"]
    require(len(starts) == native["start_count"] == 1, "I6_TRACE_INVALID")
    start_events = [(i, e) for i, e in events.get("protocol_event", [])
                    if e["data"]["record"]["event"]["type"] == "started"]
    require(len(start_events) == 1
            and starts[0][0] < start_events[0][0], "I6_TRACE_INVALID")
    # Validate the *real* started wire, including the one-shot catalog
    # Selection and root Waypipe process identity (not an app-leaf PID).
    catalogs = [(i, e) for i, e in events.get("protocol_event", [])
                if e["data"]["record"]["event"]["type"] == "catalog"]
    require(len(catalogs) == 1 and catalogs[0][0] < starts[0][0], "I6_TRACE_INVALID")
    expected = wire.ExpectedEvidence(evidence.canonical(
        catalogs[0][1]["data"]["record"]["event"]["payload"]["evidence"]))
    binding = wire.Binding(context, pins)
    wire.parse_event(evidence.canonical(start_events[0][1]["data"]["record"]), binding, expected)
    selection = b1.Selection.parse(evidence.canonical({
        "scope":context.scope(),
        "revision":b1.catalog_revision(context, pins),
        "app_id":CASES[variant],
    }))
    same(start_events[0][1]["data"]["record"]["event"]["payload"]["selection"],
         selection.record(), "I6_OWNER_MISMATCH")
    # Start ACK, Stop ACK, cleanup and finalizer live on one transport sequence,
    # not on incomparable wall-clock timestamps.
    stopped = [(i, e) for i, e in events.get("protocol_event", [])
               if e["data"]["record"]["event"]["type"] == "stopped"]
    if stopped:
        require(len(stopped) == 1 and starts[0][0] < stopped[0][0],
                "I6_TRACE_INVALID")
    return events, cap, context, pins, selection


def sdk_and_readback(native):
    sdk = ordinal_events(native["sdk_observations"], limit=128)
    require(set(sdk) == set(SDK_KINDS))
    chain = [only(sdk, stage) for stage in SDK_KINDS]
    require(all(a[0] < b[0] for a, b in zip(chain, chain[1:])))
    adapter_values = {e["adapter"] for e in native["sdk_observations"]}
    runtimes = {e["runtime"] for e in native["sdk_observations"]}
    require(len(adapter_values) == 1 and len(runtimes) == 1
            and runtimes == {native["runtime"]})
    adapter = next(iter(adapter_values))
    dec(adapter)
    first = chain[0][1]["data"]
    target, create = first["target"], first["create_seq"]
    require(type(target) is dict and set(target) == {"session", "window", "scene", "generation"})
    require(all(b1.decimal(n) for n in target.values()) and b1.decimal(create))
    for _, event in chain:
        same(event["data"]["target"], target, "I6_OWNER_MISMATCH")
        same(event["data"]["create_seq"], create, "I6_OWNER_MISMATCH")
    same(chain[4][1]["data"]["readback"], native["readback"], "I6_OWNER_MISMATCH")
    readback = native["readback"]
    require(type(readback) is dict and readback["runtime"] == native["runtime"]
            and readback["target"] == target and readback["create"] == create,
            "I6_OWNER_MISMATCH")
    require(b1.decimal(readback["content_frame"]) and b1.decimal(readback["min_content_frame"])
            and int(readback["content_frame"]) >= int(readback["min_content_frame"])
            and type(readback["layers"]) is list and len(readback["layers"]) >= 1)
    require(type(readback["present_seq"]) is int and readback["present_seq"] > 0)
    require(readback["roi_revision"] == 1 and len(readback["rois"]) == 1
            and len(readback["pixel_sha256"]) == 1)
    require(readback["rois"][0]["roi"] == {
        "id":1, "x":readback["rois"][0]["roi"]["x"],
        "y":readback["rois"][0]["roi"]["y"], "width":4, "height":4,
    })
    require(b1.hexadecimal(readback["pixel_sha256"][0], 64)
            and b1.hexadecimal(readback["roi_sha256"], 64)
            and readback["backend"]["readback"] is True
            and readback["backend"]["renderer"] != ""
            and readback["backend"]["vendor"] != "")
    return target, create


def ready_and_input(native, target, create, cap):
    ready = native["ready"]
    require(type(ready) is dict)
    presentation, sdk, remote = ready["presentation"], ready["sdk_target"], ready["transport"]
    require(presentation["state"] == "ready" and presentation["startup_completed"] is True
            and presentation["main"] == target["window"])
    require(sdk["runtime"] == native["runtime"] and sdk["target"] == target
            and sdk["create_command"] == create
            and sdk["binding_present"] is True and sdk["acknowledged"] is True
            and sdk["renderer_frame_seen"] is True)
    require(remote["session"] == cap["session"] and remote["launch_request"] == "32"
            and remote["started_observed"] is True and remote["transport"]["phase"] == "started")
    input_proof = native["input"]
    require(input_proof["target"] == target and input_proof["focus_window"] == target["window"]
            and b1.decimal(input_proof["focus_token"])
            and input_proof["focus_request"] == "1001"
            and input_proof["key_requests"] == ["1002", "1003"]
            and type(input_proof["epoch"]) is int and input_proof["epoch"] >= 0
            and type(input_proof["delivered_before"]) is int
            and type(input_proof["delivered_after"]) is int
            and input_proof["delivered_after"] >= input_proof["delivered_before"] + 2)
    return input_proof


def live_census_n02(snapshot, runtime):
    """N02 has an actual stopped Session. N01's app=None shortcut is invalid."""
    exact(snapshot, {"schema", "code", "handle", "runtime", "registry"})
    require(snapshot["schema"] == 1 and snapshot["code"] == 0 and snapshot["handle"] == runtime,
            "I6_OWNER_MISMATCH")
    s = snapshot["runtime"]
    require(s["state"] == "Stopped" and _counts(snapshot["registry"], 1, 0)
            and _reclamation(s["reclamation"], runtime, True) and _shutdown(s["shutdown"]))
    require(s["app_sdk_bindings"] == 0 and s["commands_pending"] == 0
            and s["desktop_v3_pending_targets"] == 0
            and s["desktop_v3_finalizer_pending"] is False
            and s["desktop_v3_cleanup_error"] is None and s["panels"] == []
            and s["error"] is None)
    for key in ("helper_workers", "dependency_workers", "compositor_workers"):
        _number(s[key])
        require(s[key] == 0)
    app = s["app"]
    # A retained diagnostic Report must NOT be mistaken for a live app
    # nor erased to mimic the N01 NoLocalAllocation case.
    require(type(app) is dict and app["state"] == "stopped"
            and app["ready"] is False and app["directory_cleaned"] is True
            and app["windows"] == [] and app["error"] is None)
    for key in ("clients", "objects", "surfaces", "pixel_bytes", "pool_bytes", "pool_count"):
        _number(app[key])
        require(app[key] == 0)
    for key in ("memfd_fds_after", "memfd_maps_after"):
        _number(app[key])
        require(app[key] == 0)
    local = app["local"]
    require(local["workers"] == 0 and local["owned_groups"] == 0
            and local["waited"] is True and local["error"] is None)
    render = s["render"]
    for key in ("workers", "contexts", "latest_images", "queued"):
        _number(render[key])
        require(render[key] == 0)
    require(render["targets"] == [] and render["error"] is None)
    for key in ("textures", "texture_bytes", "decoration_texture_bytes", "gl_error"):
        _number(render["composition"][key])
        require(render["composition"][key] == 0)
    require(render["composition"]["error"] is None)
    transport = s["transport"]
    for key in ("workers", "tokio_threads", "owned_connections"):
        _number(transport[key])
        require(transport[key] == 0)
    require(transport["forward"]["active"] == 0 and transport["desktop_pending"] is False
            and transport["pending_cleanup"] == [] and transport["error"] is None)
    return True


def cleanup(native, env, events):
    require(native["sdk_retired"] is True)
    require(live_census_n02(native["last_snapshot"], native["runtime"]))
    require(native["destroy_status"] == 0
            and native["registry_counts"] == {"runtimes":0, "workers":0})
    final = env["final"]
    require(all(final[k] == 0 for k in
                ("app_process_count", "driver_child_count", "sshd_child_count", "journal_entries"))
            and final["profile_lock_held"] is False
            and final["journal_lock_held"] is False)
    unlink = only(events, "journal_unlinked")
    published = only(events, "finalizer_published")
    require(unlink[0] < published[0] and unlink[1]["data"]["directory_fsynced"] is True
            and published[1]["data"]["status"] == 0)
    observed = ordinal_events(native["observations"], limit=128)
    origin = only(observed, "origin_retired")
    stop = only(observed, "runtime_stop_requested")
    census = only(observed, "last_live_census")
    destroy = only(observed, "registry_destroy_return")
    require(origin[0] < stop[0] < census[0] < destroy[0])
    require(stop[1]["data"]["status"] == 0 and destroy[1]["data"]["status"] == 0)
    require(origin[1]["data"]["reclamation"]["origin_retired"] is True)
    require(destroy[1]["data"]["counts"] == native["registry_counts"])
    require(int(origin[1]["transport_sequence"]) >= published[0], "I6_TRACE_INVALID")
    return observed


def verify(native, env, source, variant):
    """Never accept a producer-supplied status. Recompute ten fixed assertions."""
    require(variant in CASES)
    strict_runtime(native, source, variant)
    require(type(env) is dict and env["run_id"] == native["run_id"]
            and env["owner_id"] == native["owner_id"]
            and env["source_sha256"] == native["source_sha256"]
            and env["revision"] == "eidolon-i6-n02-environment-v1")
    require(env["before"] == env["after"] and env["runner"]["source_sha256"] == native["source_sha256"])
    require(env["app"]["uid"] != env["driver"]["uid"]
            and env["ssh"]["pinned_host_key_sha256"] == env["ssh"]["host_key_sha256"]
            and env["isolation"]["docker_socket_present"] is False)
    events, cap, context, pins, selection = transport(native, env, variant)
    catalog = native["catalog"]["catalog"]
    require(native["catalog"]["code"] == 0 and catalog["session"] == cap["session"]
            and catalog["request_id"] == cap["inspect_request"]
            and catalog["pending"] is False and catalog["error"] is None)
    b1.validate_catalog(evidence.canonical(catalog["desktop_v3"]), context, pins)
    observed = ordinal_events(native["observations"], limit=128)
    same(only(observed, "n02_same_actor_catalog")[1]["data"]["scope"],
         context.scope(), "I6_OWNER_MISMATCH")
    same(only(observed, "n02_same_actor_catalog")[1]["data"]["app_id"],
         selection.record()["app_id"], "I6_OWNER_MISMATCH")
    target, create = sdk_and_readback(native)
    require(target["session"] == cap["session"])
    input_proof = ready_and_input(native, target, create, cap)
    delivered_observation = only(observed, "n02_input_delivered")[1]["data"]
    require(delivered_observation["focus_token"] == input_proof["focus_token"]
            and delivered_observation["input_epoch"] == input_proof["epoch"]
            and delivered_observation["target"] == target
            and delivered_observation["delivery_delta"]
                == input_proof["delivered_after"] - input_proof["delivered_before"])
    for command, request in (("linux_desktop_v3_launch", "32"),
                             ("focus_window", "1001"), ("key", "1002"), ("key", "1003")):
        if command == "key":
            require(any(e["data"]["request_id"] == request and e["data"]["command"] == command
                        and e["data"]["status"] == 0
                        for _, e in observed.get("n02_command_accepted", [])))
        else:
            require(any(e["data"]["request_id"] == request and e["data"]["command"] == command
                        and e["data"]["status"] == 0
                        for _, e in observed.get("n02_command_accepted", [])))
        require(any(e["data"]["request_id"] == request and e["data"]["status"] == "ACCEPTED"
                    for _, e in observed.get("command_complete", [])))
    cleanup(native, env, events)
    return {"status":"pass","reason":"I6_OK","verified_assertions":list(ASSERTIONS),
            "evidence_classes":["R","S","T"],"host_v3_acceptance_passed":False,
            "environment_disposal":"not_yet"}



# A native Host acceptance case has exactly one admitted main target. These
# identifiers only label observations: they cannot mint a B1 NativeOrigin or
# substitute SDK/renderer/remote lifecycle evidence.
RECEIPT_REVISION = "eidolon-i6-n02-receipt-v1"
RECEIPT_FIELDS = {
    "schema", "kind", "run_id", "case_id", "variant", "source_sha256",
    "owner", "target", "native", "environment",
}


def case_owner_target(native):
    """Project owner/target identities without granting a product verdict.

    A caller may record these projections, but only verify_receipt() is allowed
    to promote them to independently verified acceptance evidence.
    """
    require(type(native) is dict and type(native.get("trace")) is dict)
    trace = native["trace"]["events"]
    require(type(trace) is list and len(trace) <= evidence.MAX_TRACE_EVENTS)
    captures = [e["capture"] for e in trace if e["capture"] is not None]
    require(captures and all(c == captures[0] for c in captures), "I6_OWNER_MISMATCH")
    capture = captures[0]
    require(type(capture) is dict)
    observations = native["observations"]
    require(type(observations) is list and len(observations) <= 128)
    connects = [o["data"]["request_id"] for o in observations
                if o["kind"] == "command_accepted"
                and o["data"]["command"] == "connect"]
    require(len(connects) == 1 and b1.decimal(connects[0]), "I6_OWNER_MISMATCH")
    sdk = native["sdk_observations"]
    require(type(sdk) is list and sdk and type(sdk[0]) is dict)
    adapter = sdk[0]["adapter"]
    readback = native["readback"]
    require(type(readback) is dict)
    key, create = readback["target"], readback["create"]
    require(type(key) is dict and set(key) == {"session", "window", "scene", "generation"}
            and all(b1.decimal(n) for n in key.values())
            and b1.decimal(create) and b1.decimal(adapter), "I6_OWNER_MISMATCH")
    owner_id = native["owner_id"]
    require(evidence.identity(owner_id) and evidence.identity(native["run_id"])
            and b1.decimal(native["runtime"]), "I6_OWNER_MISMATCH")
    owner = {
        "owner_id": owner_id,
        "runtime": native["runtime"],
        "admission_epoch": "1",  # Host fixture ordinal, not an Android epoch
        "connect_request": connects[0],
        "launch_request": "32",
        "session": capture["session"],
        "capture_sha256": capture["capture_id"],
        "context_sha256": capture["context_digest"],
        "io_generation": capture["io_generation"],
        "nonce": capture["nonce"],
        "scope": capture["scope"],
        "native_origin": "32",  # NativeOrigin.request() issued by Registry
    }
    target_id = evidence.sha(evidence.canonical({
        "owner_id": owner_id, "target": key, "create_seq": create,
        "adapter_id": adapter,
    }))[:32]
    target = {
        "target_id": target_id, "owner_id": owner_id,
        "session": key["session"], "window": key["window"],
        "scene": key["scene"], "generation": key["generation"],
        "create_seq": create, "adapter_id": adapter,
    }
    evidence._owner(owner)
    evidence._target(target, {owner_id: owner})
    return owner, target


def verify_receipt(receipt, *, expected_run_id, expected_source_sha256,
                   expected_owner, expected_target, expected_source):
    """One immutable typed receipt; recompute the N02 oracle from raw R/S/T.

    No producer status, assertion list or census string can grant acceptance.
    Same-run owner/target IDs are independently derived from native facts.
    """
    exact(receipt, RECEIPT_FIELDS)
    require(type(receipt["schema"]) is int and receipt["schema"] == 1
            and receipt["kind"] == RECEIPT_REVISION
            and receipt["case_id"] == "N02" and receipt["variant"] in CASES)
    require(receipt["run_id"] == expected_run_id
            and evidence.identity(expected_run_id), "I6_OWNER_MISMATCH")
    require(receipt["source_sha256"] == expected_source_sha256, "I6_SOURCE_MISMATCH")
    require(expected_source is not None, "I6_SOURCE_MISMATCH")
    evidence._source(expected_source)
    require(expected_source["tree_sha256"] == expected_source_sha256
            and expected_source["build_features"] == [], "I6_SOURCE_MISMATCH")
    native, environment = receipt["native"], receipt["environment"]
    require(native["run_id"] == expected_run_id
            and environment["run_id"] == expected_run_id, "I6_OWNER_MISMATCH")
    owner, target = case_owner_target(native)
    same(receipt["owner"], owner, "I6_OWNER_MISMATCH")
    same(receipt["target"], target, "I6_OWNER_MISMATCH")
    same(expected_owner, owner, "I6_OWNER_MISMATCH")
    same(expected_target, target, "I6_OWNER_MISMATCH")
    outcome = verify(native, environment, expected_source, receipt["variant"])
    require(outcome["status"] == "pass"
            and outcome["verified_assertions"] == list(ASSERTIONS)
            and outcome["host_v3_acceptance_passed"] is False
            and outcome["environment_disposal"] == "not_yet")
    # The N02 oracle checks remote process/pipe EOF, locked profile, complete
    # local/Wayland/SHM and GPU retirements, SDK same-lease fence, finalizer,
    # live Registry stop and external process census before marking PASS.
    # Every resource is therefore complete (none are falsely "not_created").
    return {"status": "pass", "verified_assertions": list(ASSERTIONS),
            "census": {resource: "complete" for resource in evidence.RESOURCES},
            "target_id": target["target_id"]}


def invalid():
    return {"status":"invalid","reason":"I6_N02_RECEIPT_INVALID","verified_assertions":[],
            "evidence_classes":[],"host_v3_acceptance_passed":False,
            "environment_disposal":"not_yet"}
