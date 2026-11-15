"""Independent N01 protocol/ownership oracle; never product authority.

The native runner records facts at normal Registry/Runtime/Controller boundaries.
The disposable environment supervisor records filesystem/process observations.
This module cross-checks both and reconstructs the B1 catalog independently. A
receipt is evidence about one Inspect-only case, not an I6, EGL or SDK verdict.
"""
from __future__ import annotations

import linux_desktop_v3_contract as contract
import linux_desktop_v3_wire as wire
import linux_real_app_v3_evidence as evidence

REVISION = "eidolon-i6-n01-receipt-v1"
ENVIRONMENT_REVISION = "eidolon-i6-n01-environment-v1"
NATIVE_REVISION = "eidolon-i6-n01-native-v1"
CASE = ("N01", "inspect_only")
ASSERTIONS = frozenset({
    "real_catalog", "start_count_zero", "no_local_allocation_sealed",
    "helper_ssh_reaped", "product_census_complete", "journal_finalized",
    "no_local_allocation_sealed_before_stop", "registry_destroy_accepted",
})
ENVIRONMENT_FIELDS = {"schema", "revision", "run_id", "owner_id", "source_sha256",
                      "driver", "app", "ssh", "before", "after", "runner", "isolation", "final"}
INPUT_FIELDS = {"inventory_sha256", "compiled_source_hashes", "bundle_build_id", "bundle_sha256", "pins"}
UNALLOCATED_RESOURCES = frozenset({
    "ssh_forwards", "local_waypipe", "local_pipes", "wayland_clients", "wayland_objects",
    "wayland_surfaces", "wayland_shm", "native_windows", "renderer_target", "sdk_lease", "origin_retirement",
})


def _require(condition, reason="I6_N01_RECEIPT_INVALID"):
    evidence.require(condition, reason)


def _exact(value, fields):
    _require(type(value) is dict and set(value) == set(fields))


def _same(left, right, reason="I6_N01_RECEIPT_INVALID"):
    # Python equality equates True and 1; serialized contract equality does not.
    _require(evidence.canonical(left) == evidence.canonical(right), reason)


def _hash(value):
    _require(contract.hexadecimal(value, 64))


def _number(value, low=0, high=contract.MAX_I64):
    _require(contract.integer(value, low, high))


def _environment(value, run_id, owner_id, source_sha256):
    """Check independent supervisor observations, never a generic passed flag."""
    _exact(value, ENVIRONMENT_FIELDS)
    _require(type(value["schema"]) is int and value["schema"] == 1
             and value["revision"] == ENVIRONMENT_REVISION)
    _require(value["run_id"] == run_id and value["owner_id"] == owner_id,
             "I6_OWNER_MISMATCH")
    _require(value["source_sha256"] == source_sha256, "I6_SOURCE_MISMATCH")
    for role in ("driver", "app"):
        account = value[role]
        _exact(account, {"uid", "passwd_home", "home_uid", "home_mode", "ancestors", "fresh_home"})
        _number(account["uid"], 1, contract.MAX_UID)
        _number(account["home_uid"], 1, contract.MAX_UID)
        _number(account["home_mode"], 0, 0o7777)
        _require(account["passwd_home"] == "/home/eidolon-" + role
                 and account["home_uid"] == account["uid"]
                 and account["home_mode"] == 0o700 and account["fresh_home"] is True,
                 "I6_N01_ENVIRONMENT_INVALID")
        _require(type(account["ancestors"]) is list and len(account["ancestors"]) == 2)
        for ancestor in account["ancestors"]:
            _exact(ancestor, {"uid", "mode"})
            _number(ancestor["uid"], 0, contract.MAX_UID)
            _number(ancestor["mode"], 0, 0o7777)
            _require(ancestor["uid"] == 0 and ancestor["mode"] & 0o022 == 0
                     and ancestor["mode"] & 0o111 == 0o111, "I6_N01_ENVIRONMENT_INVALID")
    _require(value["driver"]["uid"] != value["app"]["uid"], "I6_N01_ENVIRONMENT_INVALID")
    isolation = value["isolation"]
    _exact(isolation, {"kind", "pid_namespace", "mount_namespace", "docker_marker", "docker_socket_present"})
    _require(isolation["kind"] == "docker" and contract.decimal(isolation["pid_namespace"])
             and contract.decimal(isolation["mount_namespace"])
             and isolation["docker_marker"] is True and isolation["docker_socket_present"] is False,
             "I6_N01_ENVIRONMENT_INVALID")
    ssh = value["ssh"]
    _exact(ssh, {"listen_host", "port", "host_key_sha256", "pinned_host_key_sha256", "dedicated"})
    _number(ssh["port"], 1024, 65535)
    _hash(ssh["host_key_sha256"])
    _hash(ssh["pinned_host_key_sha256"])
    _require(ssh["listen_host"] == "127.0.0.1" and ssh["dedicated"] is True
             and ssh["host_key_sha256"] == ssh["pinned_host_key_sha256"], "I6_N01_ENVIRONMENT_INVALID")
    source_inputs = evidence.source_input_hashes()
    for stage in ("before", "after"):
        inputs = value[stage]
        _exact(inputs, INPUT_FIELDS)
        for field in INPUT_FIELDS - {"compiled_source_hashes", "pins"}:
            _hash(inputs[field])
        _same(inputs["compiled_source_hashes"], source_inputs["compiled_source_hashes"], "I6_SOURCE_MISMATCH")
        _require(inputs["inventory_sha256"] == source_inputs["inventory_sha256"], "I6_SOURCE_MISMATCH")
        pins = contract.Pins.parse(evidence.canonical(inputs["pins"]))
        _require(inputs["bundle_build_id"] == pins.build_id()
                 and inputs["bundle_sha256"] == pins.record()["desktop_bundle_sha256"], "I6_SOURCE_MISMATCH")
    _same(value["before"], value["after"], "I6_SOURCE_MISMATCH")
    runner = value["runner"]
    _exact(runner, {"source_sha256", "binary_sha256"})
    _require(runner["source_sha256"] == source_sha256, "I6_SOURCE_MISMATCH")
    _hash(runner["binary_sha256"])
    final = value["final"]
    _exact(final, {"app_process_count", "driver_child_count", "sshd_child_count", "journal_entries", "profile_lock_held", "journal_lock_held"})
    for count in ("app_process_count", "driver_child_count", "sshd_child_count", "journal_entries"):
        _number(final[count])
    _require(type(final["profile_lock_held"]) is bool and type(final["journal_lock_held"]) is bool)
    return pins


def _result(status, *, missing=(), failures=()):
    return {"status": status,
            "reason": {"pass": "I6_OK", "fail": "I6_ASSERTION_FAILED",
                       "incomplete": "I6_EVIDENCE_MISSING"}[status],
            "missing": sorted(set(missing)), "failures": sorted(set(failures)),
            "verified_assertions": sorted(ASSERTIONS) if status == "pass" else [],
            "census": {r: "not_created" if r in UNALLOCATED_RESOURCES else "complete"
                       for r in evidence.RESOURCES} if status == "pass" else {}}


CAPTURE_FIELDS = {"session", "inspect_request", "io_generation", "nonce", "scope", "context_digest", "capture_id"}
TRACE_FIELDS = {
    "capture_created": {"context", "pins", "compiled_source_hashes"},
    "journal_reserved": {"stage"}, "journal_prepared": {"stage"},
    "catalog_published": {"selections"}, "control_send_attempt": {"action"},
    "protocol_event": {"record"}, "no_local_allocation_sealed": {"before_cancel"},
    "remote_cleanup_observed": {"remote", "channel_complete", "forward_retired", "router_drained", "start_attempted"},
    "journal_complete": {"stage"}, "io_retired": {"generation", "tokio_threads"},
    "journal_unlinked": {"generation", "directory_fsynced"},
    "finalizer_published": {"generation", "status"}, "controller_stop_requested": set(),
    "recovery_bound": {"generation"}, "recovery_durable_complete": {"generation", "cleanup"},
    "recovery_receipt": {"record", "generation", "expected_boot_id", "exit_status", "eof", "exit_signal",
                         "channel_closed", "router_active"},
}
REMOTE_FIELDS = {"phase", "started", "stdout_bytes", "stderr_bytes", "process_complete", "pipes_eof",
                 "remote_cleaned", "receipt_written", "terminal_event", "stop_reason", "eof",
                 "exit_status", "exit_signal", "channel_closed", "protocol_valid", "reason"}


def _capture(value):
    _exact(value, CAPTURE_FIELDS)
    _require(all(contract.decimal(value[k]) for k in ("session", "inspect_request", "io_generation")))
    _require(contract.hexadecimal(value["nonce"], 16))
    for field in ("scope", "context_digest", "capture_id"):
        _hash(value[field])


def _trace(native, owner, environment):
    trace = native["trace"]
    _exact(trace, {"overflow", "events"})
    _require(type(trace["overflow"]) is bool and type(trace["events"]) is list
             and len(trace["events"]) <= evidence.MAX_TRACE_EVENTS
             and len(evidence.canonical(trace)) <= evidence.MAX_TRACE_BYTES, "I6_TRACE_INVALID")
    by_kind, capture = {}, None
    for ordinal, event in enumerate(trace["events"], 1):
        _exact(event, {"sequence", "kind", "capture", "data"})
        _require(event["sequence"] == str(ordinal) and event["kind"] in TRACE_FIELDS
                 and len(evidence.canonical(event)) <= evidence.MAX_EVENT_BYTES, "I6_TRACE_INVALID")
        _exact(event["data"], TRACE_FIELDS[event["kind"]])
        if event["kind"] == "protocol_event":
            _exact(event["data"]["record"], wire.EVENT_FIELDS)
            _exact(event["data"]["record"]["event"], {"type", "payload"})
        _capture(event["capture"])
        if capture is None:
            capture = event["capture"]
        else:
            _same(event["capture"], capture, "I6_OWNER_MISMATCH")
        by_kind.setdefault(event["kind"], []).append(event)
    missing = ["trace_overflow"] if trace["overflow"] else []
    recovering = any(k in by_kind for k in ("recovery_bound", "recovery_receipt", "recovery_durable_complete"))
    singleton = set(TRACE_FIELDS) - {"protocol_event", "control_send_attempt", "io_retired"}
    optional = {"recovery_bound", "recovery_receipt", "recovery_durable_complete"}
    if recovering and "recovery_receipt" in by_kind:
        optional.add("journal_complete")
    for kind in singleton:
        count = len(by_kind.get(kind, []))
        _require(count <= 1, "I6_TRACE_INVALID")
        if count == 0 and kind not in optional:
            missing.append("receipt/" + kind)
    if capture is None:
        return missing, [], None, by_kind
    for owner_key, capture_key in (("session", "session"), ("io_generation", "io_generation"),
                                    ("nonce", "nonce"), ("scope", "scope"),
                                    ("context_sha256", "context_digest"), ("capture_sha256", "capture_id")):
        _require(owner[owner_key] == capture[capture_key], "I6_OWNER_MISMATCH")
    _require(owner["launch_request"] is None and owner["native_origin"] is None,
             "I6_OWNER_MISMATCH")

    def item(kind):
        return by_kind[kind][0]

    def ordered(before, after):
        if before in by_kind and after in by_kind:
            _require(int(item(before)["sequence"]) < int(item(after)["sequence"]), "I6_TRACE_INVALID")

    chain = ["capture_created", "journal_reserved", "journal_prepared", "catalog_published",
             "no_local_allocation_sealed", "remote_cleanup_observed", "journal_complete",
             "journal_unlinked", "finalizer_published", "controller_stop_requested"]
    for first, second in zip(chain, chain[1:]):
        ordered(first, second)
    failures = []
    for kind, stage in (("journal_reserved", "reserved"), ("journal_prepared", "prepared"),
                         ("journal_complete", "complete")):
        if kind in by_kind:
            _require(item(kind)["data"]["stage"] == stage)
    final_generation = capture["io_generation"]
    if recovering:
        if "recovery_bound" not in by_kind:
            missing.append("receipt/recovery_bound")
        else:
            final_generation = item("recovery_bound")["data"]["generation"]
            _require(contract.decimal(final_generation) and int(final_generation) > int(capture["io_generation"]),
                     "I6_OWNER_MISMATCH")
        _require(not ("recovery_receipt" in by_kind and "recovery_durable_complete" in by_kind), "I6_TRACE_INVALID")
        if not any(k in by_kind for k in ("recovery_receipt", "recovery_durable_complete")):
            missing.append("receipt/recovery_evidence")
        for kind in ("recovery_receipt", "recovery_durable_complete"):
            if kind in by_kind:
                _require(item(kind)["data"]["generation"] == final_generation, "I6_OWNER_MISMATCH")
                ordered("remote_cleanup_observed", kind)
                ordered(kind, "recovery_bound")
                ordered("recovery_bound", "journal_unlinked")
    for kind in ("journal_unlinked", "finalizer_published"):
        if kind in by_kind:
            _require(item(kind)["data"]["generation"] == final_generation, "I6_OWNER_MISMATCH")
    retired_generations = {}
    for retired in by_kind.get("io_retired", []):
        generation = retired["data"]["generation"]
        _require(generation in {capture["io_generation"], final_generation} and generation not in retired_generations,
                 "I6_OWNER_MISMATCH")
        retired_generations[generation] = retired
        _number(retired["data"]["tokio_threads"])
        if retired["data"]["tokio_threads"] != 0:
            failures.append("helper_ssh_reaped")
    for generation in {capture["io_generation"], final_generation}:
        if generation not in retired_generations:
            missing.append("receipt/io_retired")
    if capture["io_generation"] in retired_generations:
        for kind in ("remote_cleanup_observed", "journal_complete"):
            if kind in by_kind:
                _require(int(item(kind)["sequence"]) < int(retired_generations[capture["io_generation"]]["sequence"]),
                         "I6_TRACE_INVALID")
    if final_generation in retired_generations and "journal_unlinked" in by_kind:
        _require(int(retired_generations[final_generation]["sequence"]) < int(item("journal_unlinked")["sequence"]),
                 "I6_TRACE_INVALID")
    if recovering and capture["io_generation"] in retired_generations:
        for kind in ("recovery_receipt", "recovery_durable_complete"):
            if kind in by_kind:
                _require(int(retired_generations[capture["io_generation"]]["sequence"]) < int(item(kind)["sequence"]),
                         "I6_TRACE_INVALID")
        if final_generation in retired_generations and "recovery_bound" in by_kind:
            _require(int(item("recovery_bound")["sequence"]) < int(retired_generations[final_generation]["sequence"]),
                     "I6_TRACE_INVALID")
    if "no_local_allocation_sealed" in by_kind:
        _require(item("no_local_allocation_sealed")["data"]["before_cancel"] is True)
    if "journal_unlinked" in by_kind:
        _require(item("journal_unlinked")["data"]["directory_fsynced"] is True)
    if "finalizer_published" in by_kind:
        _number(item("finalizer_published")["data"]["status"], 0, 5)
        if item("finalizer_published")["data"]["status"] != 0:
            failures.append("journal_finalized")
    if "remote_cleanup_observed" in by_kind:
        data = item("remote_cleanup_observed")["data"]
        remote = data["remote"]
        _exact(remote, REMOTE_FIELDS)
        for field in ("stdout_bytes", "stderr_bytes"):
            _number(remote[field])
        for field in REMOTE_FIELDS - {"phase", "stdout_bytes", "stderr_bytes", "stop_reason", "exit_status", "reason"}:
            _require(type(remote[field]) is bool)
        for field in ("channel_complete", "forward_retired", "router_drained", "start_attempted"):
            _require(type(data[field]) is bool)
        _require(remote["exit_status"] is None or contract.integer(remote["exit_status"], 0, (1 << 32) - 1))
        if (remote["phase"] != "stopped" or remote["reason"] is not None
                or remote["stop_reason"] != "stop" or remote["exit_status"] != 0
                or remote["started"] or remote["exit_signal"] or data["start_attempted"]
                or not all(remote[k] for k in ("process_complete", "pipes_eof", "remote_cleaned", "receipt_written",
                                               "terminal_event", "eof", "channel_closed", "protocol_valid"))
                or not all(data[k] for k in ("channel_complete", "forward_retired", "router_drained"))):
            if not recovering or "recovery_durable_complete" in by_kind:
                failures.append("helper_ssh_reaped")
    if "capture_created" not in by_kind:
        return missing, failures, capture, by_kind
    initial = item("capture_created")["data"]
    context = contract.Context.parse(evidence.canonical(initial["context"]))
    pins = contract.Pins.parse(evidence.canonical(initial["pins"]))
    _same(pins.record(), environment["before"]["pins"], "I6_SOURCE_MISMATCH")
    _same(initial["compiled_source_hashes"], native["compiled_source_hashes"], "I6_SOURCE_MISMATCH")
    _require(context.record()["uid"] == environment["app"]["uid"] and
             context.record()["session"] == capture["session"] and context.record()["nonce"] == capture["nonce"]
             and context.sha256() == capture["context_digest"] == capture["scope"], "I6_OWNER_MISMATCH")
    capture_hash = contract.digest({"context": context.record(), "pins": pins.record(),
                                   "sources": initial["compiled_source_hashes"],
                                   "inspect_request": capture["inspect_request"], "generation": capture["io_generation"]})
    _require(capture_hash == capture["capture_id"], "I6_OWNER_MISMATCH")
    protocol_events = by_kind.get("protocol_event", [])
    # Failure cleanup can recover the same capture before a catalog exists.
    # Inspect every present recovery record independently of catalog admission.
    if "recovery_receipt" in by_kind:
        recovery = item("recovery_receipt")["data"]
        for key in ("eof", "exit_signal", "channel_closed"):
            _require(type(recovery[key]) is bool)
        _number(recovery["router_active"])
        _number(recovery["exit_status"], 0, (1 << 32) - 1)
        prepared = [e for e in protocol_events if e["data"]["record"]["event"]["type"] == "prepared"]
        _require(len(prepared) <= 1, "I6_TRACE_INVALID")
        if prepared:
            frame = wire.parse_event(evidence.canonical(prepared[0]["data"]["record"]),
                                     wire.Binding(context, pins), None)
            boot = frame["event"]["payload"]["boot_id"]
            _require(recovery["expected_boot_id"] == boot, "I6_OWNER_MISMATCH")
            _require(int(prepared[0]["sequence"]) < int(item("recovery_receipt")["sequence"]), "I6_TRACE_INVALID")
        else:
            # Still validate the receipt's schema, capture and sources. The
            # recorded recovery expectation alone cannot prove the original boot.
            missing.append("receipt/prepared_protocol")
            boot = recovery["expected_boot_id"]
        recovered = wire.parse_receipt(evidence.canonical(recovery["record"]) + b"\n",
            wire.Binding(context, pins), native["compiled_source_hashes"], boot, recovery["exit_status"])
        if (recovered["status"] != "complete" or recovered["process_evidence"] != "receipt"
                or recovered["origin_boot_id"] != boot or recovered["boot_id"] != boot
                or not recovered["pipes_eof"] or recovery["exit_status"] != 0
                or not recovery["eof"] or recovery["exit_signal"] or not recovery["channel_closed"]
                or recovery["router_active"] != 0):
            failures.append("helper_ssh_reaped")
    if "recovery_durable_complete" in by_kind:
        cleanup = item("recovery_durable_complete")["data"]["cleanup"]
        _exact(cleanup, {"process_complete", "pipes_eof", "remote_cleaned", "receipt_written", "channel_complete",
                         "forward_retired", "router_drained", "boot_changed"})
        _require(all(type(value) is bool for value in cleanup.values()))
        if not all(cleanup[k] for k in cleanup.keys() - {"boot_changed"}) or cleanup["boot_changed"]:
            failures.append("helper_ssh_reaped")
        if "journal_complete" not in by_kind:
            missing.append("receipt/journal_complete")
        ordered("journal_complete", "recovery_durable_complete")
    catalogs = [e for e in protocol_events if e["data"]["record"].get("event", {}).get("type") == "catalog"]
    if not catalogs:
        missing.append("receipt/catalog_protocol")
    else:
        _require(len(catalogs) == 1, "I6_TRACE_INVALID")
        catalog_evidence = wire.ExpectedEvidence(evidence.canonical(catalogs[0]["data"]["record"]["event"]["payload"]["evidence"]))
        _same(catalog_evidence.record()["source_hashes"], native["compiled_source_hashes"], "I6_SOURCE_MISMATCH")
        protocol = wire.Protocol(wire.Binding(context, pins), catalog_evidence)
        start_count, inspect_count, stop_count = 0, 0, 0
        for event in trace["events"]:
            if event["kind"] == "control_send_attempt":
                action = event["data"]["action"]
                _require(action in {"inspect", "start", "heartbeat", "stop"})
                if action == "start":
                    start_count += 1
                    failures.append("start_count_zero")
                    continue
                inspect_count += action == "inspect"
                stop_count += action == "stop"
                if action == "stop" and "no_local_allocation_sealed" in by_kind:
                    _require(int(item("no_local_allocation_sealed")["sequence"]) < int(event["sequence"]), "I6_TRACE_INVALID")
                if not start_count:
                    protocol.command(action)
            elif event["kind"] == "protocol_event" and not start_count:
                protocol.accept(evidence.canonical(event["data"]["record"]))
        _require(native["start_count"] == start_count, "I6_TRACE_INVALID")
        if inspect_count != 1 or stop_count != 1 or not recovering and protocol.phase != "stopped":
            missing.append("receipt/protocol_complete")
        if "catalog_published" in by_kind:
            selections = [contract.Selection.parse(evidence.canonical({"scope": context.scope(),
                          "revision": contract.catalog_revision(context, pins), "app_id": app})).record()
                          for app in (contract.DEMO, contract.EDITOR)]
            _same(item("catalog_published")["data"]["selections"], selections)
            _require(int(catalogs[0]["sequence"]) < int(item("catalog_published")["sequence"]), "I6_TRACE_INVALID")
        catalog = native["catalog"]
        if catalog is None:
            missing.append("receipt/registry_catalog")
        else:
            _exact(catalog, {"schema", "code", "catalog"})
            _require(type(catalog["schema"]) is int and catalog["schema"] == 1
                     and type(catalog["code"]) is int and catalog["code"] == 0)
            ui = catalog["catalog"]
            _exact(ui, {"session", "request_id", "pending", "reply", "error", "desktop", "desktop_v3"})
            _require(ui["session"] == capture["session"] and ui["request_id"] == capture["inspect_request"]
                     and ui["pending"] is False and ui["reply"] is None and ui["error"] is None
                     and ui["desktop"] is None, "I6_OWNER_MISMATCH")
            contract.validate_catalog(evidence.canonical(ui["desktop_v3"]), context, pins)
    return missing, failures, capture, by_kind


RECLAMATION_COUNTS = {"connections", "workers", "native_targets", "panels", "sdk_bindings",
                      "board_windows", "pending_operations"}
RECLAMATION_FLAGS = {"origin_retired", "recovery_connect_allowed", "recovery_command_allowed",
                     "remote_cleanup_pending", "local_cleanup_pending", "shutdown_complete"}
OBSERVATION_FIELDS = {
    "registry_created": {"status", "handle"}, "runtime_ready": set(),
    "command_accepted": {"command", "request_id", "status"},
    "command_complete": {"request_id", "status"},
    "credential_provided": {"session", "request_id", "status"},
    "catalog_observed": set(), "origin_retired": {"reclamation"},
    "runtime_stop_requested": {"status"}, "runtime_shutdown_complete": {"shutdown", "reclamation"},
    "last_live_census": {"snapshot"}, "registry_destroy_return": {"status", "counts"},
}


def _counts(value, runtimes, workers):
    _exact(value, {"runtimes", "workers"})
    _number(value["runtimes"])
    _number(value["workers"])
    return value["runtimes"] == runtimes and value["workers"] == workers


def _reclamation(value, runtime, stopped):
    _exact(value, {"runtime", *RECLAMATION_COUNTS, *RECLAMATION_FLAGS})
    _require(value["runtime"] == runtime, "I6_OWNER_MISMATCH")
    for key in RECLAMATION_COUNTS:
        _number(value[key])
    for key in RECLAMATION_FLAGS:
        _require(type(value[key]) is bool)
    zero = RECLAMATION_COUNTS if stopped else RECLAMATION_COUNTS - {"workers"}
    return (all(value[k] == 0 for k in zero) and value["origin_retired"] is True
            and value["remote_cleanup_pending"] is False and value["local_cleanup_pending"] is False
            and value["recovery_connect_allowed"] is False and value["recovery_command_allowed"] is False
            and value["shutdown_complete"] is stopped)


def _shutdown(value):
    _exact(value, {"requested", "complete", "elapsed_ms", "deadline_ms", "timed_out", "pending_owners", "sdk_bindings"})
    for key in ("requested", "complete", "timed_out"):
        _require(type(value[key]) is bool)
    for key in ("elapsed_ms", "deadline_ms", "sdk_bindings"):
        _number(value[key])
    _require(type(value["pending_owners"]) is list
             and all(k in {"core", "transport", "render", "helper", "dependencies", "compositor", "app", "theme", "sdk"}
                     for k in value["pending_owners"]))
    return (value["requested"] and value["complete"] and not value["timed_out"]
            and value["deadline_ms"] == 10000 and value["elapsed_ms"] <= 10000
            and value["pending_owners"] == [] and value["sdk_bindings"] == 0)


def _live_census(snapshot, runtime):
    """Check the actual final Registry snapshot, before destroy removes its owner.

    Unrelated UI/theme diagnostics are opaque and cannot satisfy an assertion.
    The exact outer and reclamation/shutdown contracts establish this census.
    """
    _exact(snapshot, {"schema", "code", "handle", "runtime", "registry"})
    _require(type(snapshot["schema"]) is int and snapshot["schema"] == 1
             and type(snapshot["code"]) is int and snapshot["code"] == 0
             and snapshot["handle"] == runtime, "I6_OWNER_MISMATCH")
    value = snapshot["runtime"]
    _require(type(value) is dict)
    needed = {"state", "shutdown", "reclamation", "commands_pending", "app_sdk_bindings", "helper_workers",
              "dependency_workers", "compositor_workers", "app", "desktop_v3_pending_targets",
              "desktop_v3_finalizer_pending", "desktop_v3_cleanup_error", "panels", "render", "transport", "error"}
    _require(needed <= value.keys())
    okay = _counts(snapshot["registry"], 1, 0)
    okay &= _reclamation(value["reclamation"], runtime, True) and _shutdown(value["shutdown"])
    for field in ("commands_pending", "app_sdk_bindings", "helper_workers", "dependency_workers",
                  "compositor_workers", "desktop_v3_pending_targets"):
        _number(value[field])
        okay &= value[field] == 0
    _require(type(value["desktop_v3_finalizer_pending"]) is bool and type(value["panels"]) is list)
    okay &= (value["state"] == "Stopped" and value["app"] is None and value["error"] is None
             and value["desktop_v3_cleanup_error"] is None
             and not value["desktop_v3_finalizer_pending"] and value["panels"] == [])
    render = value["render"]
    _require(type(render) is dict and {"workers", "targets", "contexts", "latest_images", "queued", "error"} <= render.keys())
    for field in ("workers", "contexts", "latest_images", "queued"):
        _number(render[field])
        okay &= render[field] == 0
    _require(type(render["targets"]) is list)
    okay &= render["targets"] == [] and render["error"] is None
    transport = value["transport"]
    _require(type(transport) is dict
             and {"workers", "tokio_threads", "owned_connections", "desktop_pending", "pending_cleanup", "forward", "error"}
             <= transport.keys())
    for field in ("workers", "tokio_threads", "owned_connections"):
        _number(transport[field])
        okay &= transport[field] == 0
    _require(type(transport["desktop_pending"]) is bool and type(transport["pending_cleanup"]) is list
             and type(transport["forward"]) is dict and "active" in transport["forward"])
    _number(transport["forward"]["active"])
    okay &= (not transport["desktop_pending"] and transport["pending_cleanup"] == []
             and transport["forward"]["active"] == 0 and transport["error"] is None)
    return okay


def _observations(native, owner, capture, trace_kinds):
    observations = native["observations"]
    _require(type(observations) is list and len(observations) <= 128)
    indexed, previous_watermark = {}, 0
    for ordinal, observation in enumerate(observations, 1):
        _exact(observation, {"sequence", "kind", "transport_sequence", "data"})
        _require(observation["sequence"] == str(ordinal) and observation["kind"] in OBSERVATION_FIELDS
                 and (observation["transport_sequence"] == "0" or contract.decimal(observation["transport_sequence"])),
                 "I6_TRACE_INVALID")
        _exact(observation["data"], OBSERVATION_FIELDS[observation["kind"]])
        watermark = int(observation["transport_sequence"])
        _require(previous_watermark <= watermark <= len(native["trace"]["events"]), "I6_TRACE_INVALID")
        previous_watermark = watermark
        indexed.setdefault(observation["kind"], []).append(observation)
    missing, failures = [], []
    singles = set(OBSERVATION_FIELDS) - {"command_accepted", "command_complete", "credential_provided"}
    for kind in singles:
        count = len(indexed.get(kind, []))
        _require(count <= 1, "I6_TRACE_INVALID")
        if count == 0:
            missing.append("receipt/" + kind)
    chain = ["registry_created", "runtime_ready", "catalog_observed", "origin_retired", "runtime_stop_requested",
             "runtime_shutdown_complete", "last_live_census", "registry_destroy_return"]
    for first, second in zip(chain, chain[1:]):
        if first in indexed and second in indexed:
            _require(int(indexed[first][0]["sequence"]) < int(indexed[second][0]["sequence"]), "I6_TRACE_INVALID")
    for kind, trace_kind in (("catalog_observed", "catalog_published"), ("origin_retired", "finalizer_published"),
                              ("runtime_stop_requested", "controller_stop_requested"),
                              ("runtime_shutdown_complete", "controller_stop_requested")):
        if kind in indexed and trace_kind in trace_kinds:
            _require(int(indexed[kind][0]["transport_sequence"]) >= int(trace_kinds[trace_kind][0]["sequence"]), "I6_TRACE_INVALID")
    accepted = {}
    for observation in indexed.get("command_accepted", []):
        data = observation["data"]
        _require(data["command"] in {"connect", "linux_desktop_v3_inspect", "disconnect", "linux_desktop_v3_recover"}
                 and contract.decimal(data["request_id"]) and data["request_id"] not in accepted)
        _number(data["status"], 0, 5)
        accepted[data["request_id"]] = observation
        if data["status"] != 0:
            failures.append("real_catalog")
    completed = set()
    for observation in indexed.get("command_complete", []):
        data = observation["data"]
        _require(contract.decimal(data["request_id"]) and data["request_id"] in accepted
                 and data["request_id"] not in completed
                 and data["status"] in {"ACCEPTED", "STALE", "BUSY", "INVALID_ARGUMENT", "NOT_READY", "FAILED"})
        _require(int(accepted[data["request_id"]]["sequence"]) < int(observation["sequence"]), "I6_TRACE_INVALID")
        completed.add(data["request_id"])
        if data["status"] != "ACCEPTED":
            failures.append("real_catalog")
    for request, observation in accepted.items():
        if request not in completed:
            missing.append("receipt/command_completion")
    for command in ("connect", "linux_desktop_v3_inspect", "disconnect"):
        found = [o for o in accepted.values() if o["data"]["command"] == command]
        if not found:
            missing.append("receipt/" + command)
        if command != "connect":
            _require(len(found) <= 1, "I6_TRACE_INVALID")
        if found and command == "linux_desktop_v3_inspect" and capture is not None:
            _require(found[0]["data"]["request_id"] == capture["inspect_request"], "I6_OWNER_MISMATCH")
        if found and command == "connect":
            _require(found[0]["data"]["request_id"] == owner["connect_request"], "I6_OWNER_MISMATCH")
            _require(len(found) <= (2 if "recovery_bound" in trace_kinds else 1), "I6_TRACE_INVALID")
    recovery_commands = [o for o in accepted.values() if o["data"]["command"] == "linux_desktop_v3_recover"]
    if "recovery_bound" in trace_kinds:
        connects = [o for o in accepted.values() if o["data"]["command"] == "connect"]
        if len(connects) != 2 or len(recovery_commands) != 1:
            missing.append("receipt/live_recover_commands")
        elif int(connects[1]["sequence"]) >= int(recovery_commands[0]["sequence"]):
            raise evidence.EvidenceError("I6_TRACE_INVALID")
    else:
        _require(not recovery_commands, "I6_TRACE_INVALID")
    credentials = indexed.get("credential_provided", [])
    if not credentials:
        missing.append("receipt/credential_provided")
    credential_scopes = set()
    for observation in credentials:
        data = observation["data"]
        _require(contract.decimal(data["session"]) and contract.decimal(data["request_id"]))
        _number(data["status"], 0, 5)
        scope = (data["session"], data["request_id"])
        _require(scope not in credential_scopes, "I6_OWNER_MISMATCH")
        credential_scopes.add(scope)
        _require(data["request_id"] in accepted and accepted[data["request_id"]]["data"]["command"] == "connect",
                 "I6_OWNER_MISMATCH")
        if data["status"] != 0:
            failures.append("real_catalog")
    if capture is not None and credentials:
        _require((capture["session"], owner["connect_request"]) in credential_scopes, "I6_OWNER_MISMATCH")
        if "recovery_bound" in trace_kinds:
            generation = trace_kinds["recovery_bound"][0]["data"]["generation"]
            connects = [o for o in accepted.values() if o["data"]["command"] == "connect"]
            if len(connects) == 2:
                _require((generation, connects[1]["data"]["request_id"]) in credential_scopes, "I6_OWNER_MISMATCH")
    if "registry_created" in indexed:
        data = indexed["registry_created"][0]["data"]
        _require(data["handle"] == native["runtime"], "I6_OWNER_MISMATCH")
        _number(data["status"], 0, 5)
        if data["status"] != 0:
            failures.append("real_catalog")
    if "origin_retired" in indexed and not _reclamation(indexed["origin_retired"][0]["data"]["reclamation"], native["runtime"], False):
        failures.append("journal_finalized")
    if "runtime_stop_requested" in indexed:
        status = indexed["runtime_stop_requested"][0]["data"]["status"]
        _number(status, 0, 5)
        if status != 0:
            failures.append("product_census_complete")
    if "runtime_shutdown_complete" in indexed:
        data = indexed["runtime_shutdown_complete"][0]["data"]
        if not _shutdown(data["shutdown"]) or not _reclamation(data["reclamation"], native["runtime"], True):
            failures.append("product_census_complete")
    if "last_live_census" in indexed:
        snapshot = indexed["last_live_census"][0]["data"]["snapshot"]
        _same(snapshot, native["last_snapshot"])
        if not _live_census(snapshot, native["runtime"]):
            failures.append("product_census_complete")
    if "registry_destroy_return" in indexed:
        data = indexed["registry_destroy_return"][0]["data"]
        _number(data["status"], 0, 5)
        _require(data["status"] == native["destroy_status"])
        _same(data["counts"], native["registry_counts"])
        if data["status"] != 0 or not _counts(data["counts"], 0, 0):
            failures.append("registry_destroy_accepted")
    return missing, failures


def verify_receipt(receipt, *, expected_run_id, expected_source_sha256, expected_owner, expected_source=None):
    """Check one receipt's facts; absence stays incomplete and mixing is invalid.

    Returns only fixed identifiers and counts; raw private context and SSH
    payloads never enter a diagnostic or the validator's public result.
    """
    _exact(receipt, {"schema", "kind", "run_id", "case_id", "variant", "source_sha256", "owner", "native", "environment"})
    _require(type(receipt["schema"]) is int and receipt["schema"] == 1 and receipt["kind"] == REVISION
             and (receipt["case_id"], receipt["variant"]) == CASE)
    _require(receipt["run_id"] == expected_run_id and evidence.identity(expected_run_id), "I6_OWNER_MISMATCH")
    _require(receipt["source_sha256"] == expected_source_sha256, "I6_SOURCE_MISMATCH")
    evidence._owner(receipt["owner"])
    _same(receipt["owner"], expected_owner, "I6_OWNER_MISMATCH")
    owner = receipt["owner"]
    environment = receipt["environment"]
    _environment(environment, expected_run_id, owner["owner_id"], expected_source_sha256)
    native = receipt["native"]
    _exact(native, {"schema", "run_id", "runtime", "owner_id", "source_sha256", "build_source",
                    "inventory_sha256", "compiled_source_hashes", "trace", "catalog", "observations", "start_count",
                    "last_snapshot", "destroy_status", "registry_counts", "error", "cleanup_error"})
    _require(native["schema"] == NATIVE_REVISION and native["run_id"] == expected_run_id
             and native["runtime"] == owner["runtime"] and native["owner_id"] == owner["owner_id"], "I6_OWNER_MISMATCH")
    _require(native["source_sha256"] == expected_source_sha256, "I6_SOURCE_MISMATCH")
    evidence._source(native["build_source"])
    _require(native["build_source"]["tree_sha256"] == expected_source_sha256
             and native["build_source"]["build_features"] == ["host-acceptance"], "I6_SOURCE_MISMATCH")
    if expected_source is not None:
        # The validator observes a checkout, while the runner embeds its fixed
        # build profile. Every physical-source field must still match exactly.
        _require(expected_source["build_features"] == [], "I6_SOURCE_MISMATCH")
        physical = evidence.SOURCE_FIELDS - {"build_features"}
        _same({k: native["build_source"][k] for k in physical},
              {k: expected_source[k] for k in physical}, "I6_SOURCE_MISMATCH")
    source_inputs = evidence.source_input_hashes()
    for field in source_inputs:
        _same(native[field], source_inputs[field], "I6_SOURCE_MISMATCH")
    _number(native["start_count"])
    _require(native["destroy_status"] is None or contract.integer(native["destroy_status"], 0, 5))
    errors = {
        "I6_INPUT_INVALID", "I6_REGISTRY_CREATE_FAILED", "I6_RUNTIME_STALE", "I6_TRACE_OVERFLOW",
        "I6_CREDENTIAL_FAILED", "I6_OUTER_TIMEOUT", "I6_COMMAND_REJECTED", "I6_CATALOG_MISSING",
        "I6_FINALIZER_MISSING", "I6_DESTROY_REJECTED", "I6_RECEIPT_INVALID",
    }
    _require(all(native[field] is None or native[field] in errors for field in ("error", "cleanup_error")))
    missing, failures, capture, kinds = _trace(native, owner, environment)
    if capture is not None:
        derived_owner = evidence.sha((expected_run_id + ":" + owner["runtime"] + ":" + capture["capture_id"]).encode("ascii"))[:32]
        _require(owner["owner_id"] == derived_owner and owner["admission_epoch"] == "1", "I6_OWNER_MISMATCH")
    more_missing, more_failures = _observations(native, owner, capture, kinds)
    missing += more_missing
    failures += more_failures
    if native["build_source"]["dirty"]:
        missing.append("clean_source")
    if native["error"] is not None:
        if native["error"] in {"I6_TRACE_OVERFLOW", "I6_OUTER_TIMEOUT"}:
            missing.append("receipt/incomplete_run")
        else:
            failures.append("product_census_complete")
    if native["cleanup_error"] is not None:
        failures.append("product_census_complete")
    if native["start_count"] != 0:
        failures.append("start_count_zero")
    final = environment["final"]
    if (any(final[field] != 0 for field in ("app_process_count", "driver_child_count", "sshd_child_count", "journal_entries"))
            or final["profile_lock_held"] or final["journal_lock_held"]):
        failures.append("product_census_complete")
    return _result("fail" if failures else "incomplete" if missing else "pass", missing=missing, failures=failures)

