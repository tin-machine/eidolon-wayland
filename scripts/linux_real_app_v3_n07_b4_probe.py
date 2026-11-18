"""Independent N07-B4 collection audit; no formal N07 acceptance authority.

The saved native product traces, independently held storage observations and
each process's fingerprint are re-opened by the caller. Producer status strings
are never sufficient. The B3 four-state schema remains unchanged; the restarted
process has its own explicit native schema and two actual Renderer readbacks.
"""
from __future__ import annotations

import copy
import base64
import re
from pathlib import PurePosixPath
from pathlib import Path

import linux_desktop_v3_contract as contract
import linux_real_app_v3_evidence as evidence
import linux_real_app_v3_n01_oracle as n01
import linux_real_app_v3_n02_oracle as n02
import linux_real_app_v3_n04_oracle as n04
import linux_real_app_v3_n06_oracle as n06
import linux_real_app_v3_n07_fingerprint as fingerprint
import linux_real_app_v3_n07_pixel as pixel
import linux_real_app_v3_n07_review as review
import linux_real_app_v3_n07_settings as settings
from linux_real_app_v3_n07_b2_probe import (
    CHECK, _backend_identity, _forwarded, _popup, _state, inspect as inspect_b2,
)

SECOND_SCHEMA = "eidolon-i6-n07-b4-second-native-v1"
SECOND_VARIANT = "same_storage_restart_collection"
NUMBERS = (3, 0, 64, 64, 192)
SECOND_STATES = pixel.STATES[4:]
SECOND_CONTEXT_STATES = ("second_on_context", "second_on_menu_context")
NATIVE_REVISION = "eidolon-i6-n07-b4-host-v1"
SUPERVISOR_REVISION = "eidolon-i6-n07-b4-supervisor-v1"
COLLECTION_REVISION = "eidolon-i6-n07-b4-collection-v1"
MAX_NATIVE_BYTES = 1024 * 1024
MAX_CONTEXT_BYTES = 4 * 1024 * 1024
PHASES = ("first_before_launch", "first_after_toggle", "first_retired",
          "second_before_launch", "second_after_observation", "second_retired")


class B4Invalid(ValueError):
    """Fixed public reason, never arbitrary receipt data or private paths."""


def need(value, code="N07_B4_INVALID"):
    if not value:
        raise B4Invalid(code)


def same(a, b, code="N07_B4_IDENTITY"):
    need(evidence.canonical(a) == evidence.canonical(b), code)


def exact(value, keys, code="N07_B4_SHAPE"):
    need(type(value) is dict and set(value) == set(keys), code)


def zeros(value, keys):
    need(all(type(value[k]) is int and value[k] == 0 for k in keys),
         "N07_B4_CLEANUP_COUNTS")


def census(value):
    exact(value, {"app_process_count", "driver_child_count", "sshd_child_count",
                  "journal_entries", "profile_lock_held", "journal_lock_held"})
    zeros(value, ("app_process_count", "driver_child_count", "sshd_child_count",
                  "journal_entries"))
    need(value["profile_lock_held"] is False and value["journal_lock_held"] is False,
         "N07_B4_LOCK_HELD")


def _common_native(native, source, environment):
    need(type(native) is dict and native.get("host_v3_acceptance_passed") is False
         and native.get("n07_assertions_verified") == [], "N07_B4_PROMOTION")
    need(len(evidence.canonical(native)) <= MAX_NATIVE_BYTES,
         "N07_B4_NATIVE_BOUNDS")
    base = {key: copy.deepcopy(native[key]) for key in n02.NATIVE_KEYS}
    base["schema"], base["variant"] = "eidolon-i6-n02-native-v1", "mousepad_editor"
    target = base["readback"]["target"]
    # Per-target SDK chains are projected only for the unchanged N02 main
    # proof. The original multi-target observations are separately audited.
    base["sdk_observations"] = [row for row in base["sdk_observations"]
                                if row["data"]["target"] == target
                                and row["kind"] in n02.SDK_KINDS]
    for i, event in enumerate(base["sdk_observations"], 1):
        event["sequence"] = str(i)
    n02.strict_runtime(base, source, "mousepad_editor")
    exact(environment, n01.ENVIRONMENT_FIELDS | {"driver_control"})
    env = copy.deepcopy(environment)
    control = env.pop("driver_control")
    _driver_control(control, environment["driver"]["uid"], environment["final"])
    need(env["revision"] == "eidolon-i6-n07-b4-environment-v1",
         "N07_B4_ENVIRONMENT_REVISION")
    env["revision"] = n01.ENVIRONMENT_REVISION
    n01._environment(env, native["run_id"], native["owner_id"], native["source_sha256"])
    census(env["final"])
    events, cap, context, pins, selection = n02.transport(base, env, "mousepad_editor")
    catalog = base["catalog"]["catalog"]
    need(type(base["catalog"]["code"]) is int and base["catalog"]["code"] == 0
         and catalog["session"] == cap["session"]
         and catalog["request_id"] == cap["inspect_request"]
         and catalog["pending"] is False and catalog["error"] is None,
         "N07_B4_CATALOG")
    contract.validate_catalog(evidence.canonical(catalog["desktop_v3"]), context, pins)
    observed = n02.ordinal_events(base["observations"], limit=128)
    selected = n02.only(observed, "n02_same_actor_catalog")[1]["data"]
    same(selected["scope"], context.scope())
    same(selected["app_id"], selection.record()["app_id"])
    target, create = n02.sdk_and_readback(base)
    need(target["session"] == cap["session"], "N07_B4_CAPTURE_TARGET")
    proof = n02.ready_and_input(base, target, create, cap)
    delivered = n02.only(observed, "n02_input_delivered")[1]["data"]
    need(delivered["focus_token"] == proof["focus_token"]
         and delivered["input_epoch"] == proof["epoch"]
         and delivered["target"] == target
         and delivered["delivery_delta"] == proof["delivered_after"] - proof["delivered_before"],
         "N07_B4_ADMISSION_INPUT")
    for command, request in (("linux_desktop_v3_launch", "32"),
                             ("focus_window", "1001"), ("key", "1002"), ("key", "1003")):
        need(any(row["data"]["request_id"] == request
                 and row["data"]["command"] == command
                 and type(row["data"]["status"]) is int and row["data"]["status"] == 0
                 for _, row in observed.get("n02_command_accepted", []))
             and any(row["data"]["request_id"] == request
                     and row["data"]["status"] == "ACCEPTED"
                     for _, row in observed.get("command_complete", [])),
             "N07_B4_ADMISSION_INPUT")
    return base, env, events, observed, cap, context.record()


def _driver_control(value, uid, final):
    exact(value, {"pid", "start_ticks", "uid", "raw_driver_process_count", "excluded_live_control"})
    need(all(type(value[k]) is int and value[k] > 0 for k in ("pid", "start_ticks", "uid"))
         and value["uid"] == uid
         and type(value["raw_driver_process_count"]) is int
         and type(value["excluded_live_control"]) is int
         and value["excluded_live_control"] == 1
         and value["raw_driver_process_count"] - 1 == final["driver_child_count"],
         "N07_B4_CONTROL_ACCOUNTING")


def _close(native, observed):
    # This is the already documented product Close outcome. No reason field
    # is rewritten and no existing N06 assertion/oracle is changed by B4.
    n06.validate_n06_natural_close_control(native["trace"]["events"])
    intent = n02.only(observed, "n07_b4_close_intent")
    destroyed = n02.only(observed, "n07_b4_main_destroyed")
    exited = n02.only(observed, "n07_b4_app_exit")
    origin = n02.only(observed, "origin_retired")
    need(intent[0] < destroyed[0] < exited[0] < origin[0], "N07_B4_CLOSE_ORDER")
    wanted = native["readback"]["target"]
    data = intent[1]["data"]
    exact(data, {"main_target", "action", "request_id", "controls_revision"})
    need(data["main_target"] == wanted and data["action"] == "close_window_target"
         and data["request_id"] == "7400" and type(data["controls_revision"]) is int
         and data["controls_revision"] >= 0
         and destroyed[1]["data"]["main_target"] == wanted
         and destroyed[1]["data"]["main_mapped"] is False
         and exited[1]["data"]["main_target"] == wanted,
         "N07_B4_CLOSE_TARGET")
    accepted = [(i, row) for i, row in observed.get("n02_command_accepted", [])
                if row["data"]["command"] == "close_window_target"
                and row["data"]["request_id"] == data["request_id"]]
    completed = [(i, row) for i, row in observed.get("command_complete", [])
                 if row["data"]["request_id"] == data["request_id"]]
    need(len(accepted) == len(completed) == 1
         and type(accepted[0][1]["data"]["status"]) is int
         and accepted[0][1]["data"]["status"] == 0
         and completed[0][1]["data"]["status"] == "ACCEPTED"
         and intent[0] < accepted[0][0] < completed[0][0] < destroyed[0],
         "N07_B4_CLOSE_COMMAND")
    trace = native["trace"]["events"]
    sends = [(i, e) for i, e in enumerate(trace, 1)
             if e["kind"] == "control_send_attempt" and e["data"]["action"] == "stop"]
    stopped = [(i, e) for i, e in enumerate(trace, 1)
               if e["kind"] == "protocol_event"
               and e["data"]["record"]["event"]["type"] == "stopped"]
    need(len(sends) == len(stopped) == 1 and sends[0][0] < stopped[0][0],
         "N07_B4_CLOSE_TERMINAL")
    payload = stopped[0][1]["data"]["record"]["event"]["payload"]
    need(payload["reason"] == "process_exited"
         and all(payload[k] is True for k in (
             "process_complete", "pipes_eof", "remote_cleaned", "receipt_written"))
         and int(intent[1]["transport_sequence"]) < sends[0][0]
         and int(origin[1]["transport_sequence"]) >= stopped[0][0],
         "N07_B4_CLOSE_TERMINAL")
    actual = native["last_snapshot"]["runtime"]["app"]["error"]
    need(actual in (None, "APP_WINDOW_UNSUPPORTED")
         and exited[1]["data"]["app_error"] == actual
         and exited[1]["data"]["local_waited"] is True
         and type(exited[1]["data"]["local_workers"]) is int
         and exited[1]["data"]["local_workers"] == 0,
         "N07_B4_CLOSE_REASON")
    return actual


def _cleanup(native, env, events, observed, app_error):
    s = native["last_snapshot"]
    exact(s, {"schema", "code", "handle", "runtime", "registry"})
    need(type(s["schema"]) is int and s["schema"] == 1
         and type(s["code"]) is int and s["code"] == 0
         and s["handle"] == native["runtime"]
         and n02._counts(s["registry"], 1, 0), "N07_B4_CLEANUP_IDENTITY")
    runtime = s["runtime"]
    need(n02._reclamation(runtime["reclamation"], native["runtime"], True)
         and n02._shutdown(runtime["shutdown"])
         and runtime["state"] == "Stopped" and runtime["panels"] == []
         and runtime["desktop_v3_finalizer_pending"] is False
         and runtime["desktop_v3_cleanup_error"] is None and runtime["error"] is None,
         "N07_B4_RUNTIME_RETIRED")
    zeros(runtime, ("app_sdk_bindings", "commands_pending", "desktop_v3_pending_targets",
                    "helper_workers", "dependency_workers", "compositor_workers"))
    app = runtime["app"]
    need(type(app) is dict and app["error"] == app_error and app["state"] == "stopped"
         and app["ready"] is False and app["directory_cleaned"] is True
         and app["windows"] == [], "N07_B4_APP_RETIRED")
    zeros(app, ("clients", "objects", "surfaces", "pixel_bytes", "pool_bytes", "pool_count",
                "memfd_fds_after", "memfd_maps_after"))
    zeros(app["local"], ("workers", "owned_groups"))
    need(app["local"]["waited"] is True and app["local"]["error"] is None,
         "N07_B4_LOCAL_PROCESS_RETIRED")
    render = runtime["render"]
    zeros(render, ("workers", "contexts", "latest_images", "queued"))
    zeros(render["composition"], ("textures", "texture_bytes", "decoration_texture_bytes", "gl_error"))
    need(render["targets"] == [] and render["error"] is None
         and render["composition"]["error"] is None, "N07_B4_RENDERER_RETIRED")
    transport = runtime["transport"]
    zeros(transport, ("workers", "tokio_threads", "owned_connections"))
    zeros(transport["forward"], ("active",))
    need(transport["state"] == "Stopped" and transport["desktop_pending"] is False
         and transport["pending_cleanup"] == [] and transport["error"] is None,
         "N07_B4_TRANSPORT_RETIRED")
    census(env["final"])
    zeros(native, ("destroy_status",))
    exact(native["registry_counts"], {"runtimes", "workers"})
    zeros(native["registry_counts"], ("runtimes", "workers"))
    need(native["sdk_retired"] is True, "N07_B4_SDK_RETIRED")
    kinds = ("remote_cleanup_observed", "io_retired", "journal_unlinked", "finalizer_published")
    chain = [n02.only(events, kind) for kind in kinds]
    need(all(a[0] < b[0] for a, b in zip(chain, chain[1:]))
         and chain[-2][1]["data"]["directory_fsynced"] is True
         and type(chain[-1][1]["data"]["status"]) is int
         and chain[-1][1]["data"]["status"] == 0, "N07_B4_FINALIZER")
    order = [n02.only(observed, k) for k in (
        "origin_retired", "runtime_stop_requested", "last_live_census", "registry_destroy_return")]
    need(all(a[0] < b[0] for a, b in zip(order, order[1:]))
         and int(order[0][1]["transport_sequence"]) >= chain[-1][0]
         and order[0][1]["data"]["reclamation"]["origin_retired"] is True
         and type(order[1][1]["data"]["status"]) is int
         and order[1][1]["data"]["status"] == 0
         and type(order[-1][1]["data"]["status"]) is int
         and order[-1][1]["data"]["status"] == 0,
         "N07_B4_RETIREMENT_ORDER")
    same(order[-1][1]["data"]["counts"], native["registry_counts"])


def _all_sdk(native):
    sdk = n02.ordinal_events(native["sdk_observations"], limit=128)
    need(set(sdk) == set(n02.SDK_KINDS) | {"n07_renderer_pixel_readback"},
         "N07_B4_SDK_EVENTS")
    keys = {evidence.canonical(row["data"]["target"]) for row in native["sdk_observations"]}
    leases = {}
    for key in keys:
        chain = []
        projected = []
        identity = None
        for kind in n02.SDK_KINDS:
            matching = [(i, row) for i, row in sdk[kind]
                        if evidence.canonical(row["data"]["target"]) == key]
            need(len(matching) == 1, "N07_B4_SDK_LEASE")
            i, row = matching[0]
            need(row["runtime"] == native["runtime"] and contract.decimal(row["adapter"])
                 and contract.decimal(row["data"]["create_seq"]), "N07_B4_SDK_LEASE")
            current = (row["adapter"], row["data"]["create_seq"])
            need(identity is None or identity == current, "N07_B4_SDK_LEASE")
            identity = current
            chain.append(i)
            projected.append(copy.deepcopy(row))
        need(chain == sorted(chain), "N07_B4_SDK_ORDER")
        for sequence, row in enumerate(projected, 1):
            row["sequence"] = str(sequence)
        # Every admitted target, including the Open dialog, must retain the
        # ordinary Create readback contract. Its 4-by-4 ticket is not an
        # N07 candidate ROI and cannot be replaced by a claimed SDK ACK.
        n02.sdk_and_readback({"runtime": native["runtime"],
                             "sdk_observations": projected,
                             "readback": projected[4]["data"]["readback"]})
        leases[key] = (identity, chain[6], chain[7])
    for position, row in sdk["n07_renderer_pixel_readback"]:
        data = row["data"]
        exact(data, {"target", "create_seq", "sdk_adapter", "content_frame", "roi_count"})
        identity, applied, retiring = leases[evidence.canonical(data["target"])]
        need(row["runtime"] == native["runtime"]
             and (row["adapter"], data["create_seq"]) == identity
             and data["sdk_adapter"] == row["adapter"]
             and contract.decimal(data["content_frame"])
             and type(data["roi_count"]) is int and 1 <= data["roi_count"] <= 8
             and applied < position < retiring, "N07_B4_SDK_PIXEL_LEASE")
    return keys


def _pixel_events(native, readbacks):
    sdk = n02.ordinal_events(native["sdk_observations"], limit=128)
    observed = n02.ordinal_events(native["observations"], limit=128)
    actual = sdk.get("n07_renderer_pixel_readback", [])
    sampled = observed.get("n07_roi_readback_observed", [])
    need(actual and len(actual) == len(sampled), "N07_B4_PIXEL_EVENT_COUNT")
    for (_, left), (_, right) in zip(actual, sampled):
        a, b = left["data"], right["data"]
        exact(b, {"phase", "target", "create", "content_frame", "roi_sha256"})
        need(type(b["phase"]) is str and 0 < len(b["phase"]) <= 64
             and a["target"] == b["target"] and a["create_seq"] == b["create"]
             and a["content_frame"] == b["content_frame"]
             and contract.hexadecimal(b["roi_sha256"], 64), "N07_B4_PIXEL_EVENT_IDENTITY")
    for phase, readback in readbacks.items():
        r = readback["receipt"]
        matching = [(a, b) for (_, a), (_, b) in zip(actual, sampled)
                    if b["data"]["phase"] == phase]
        need(matching, "N07_B4_PIXEL_EVENT_MISSING")
        a, b = matching[-1]
        need(a["adapter"] == readback["sdk_adapter"]
             and a["data"]["target"] == r["target"]
             and a["data"]["create_seq"] == r["create"]
             and a["data"]["content_frame"] == r["content_frame"]
             and a["data"]["roi_count"] == len(readback["pixels"])
             and b["data"]["roi_sha256"] == r["roi_sha256"],
             "N07_B4_PIXEL_EVENT_IDENTITY")


def _open(native, observed):
    p = native["n07_second"]
    steps, dialogs = p["steps"], p["dialogs"]
    need(type(steps) is list and [s["phase"] for s in steps] == [
        "open_shortcut", "open_location", "open_path"]
        and type(dialogs) is list and len(dialogs) == 1, "N07_B4_OPEN_MISSING")
    dialog, main = dialogs[0], p["main_target"]
    exact(dialog, {"name", "window", "target", "readback"})
    exact(dialog["window"], {"window", "parent", "life", "frame", "app_id"})
    need(dialog["name"] == "open" and dialog["window"]["parent"] == main["window"]
         and dialog["window"]["life"] == "Mapped"
         and contract.decimal(dialog["window"]["frame"])
         and dialog["window"]["window"] == dialog["target"]["window"]
         and dialog["target"]["window"] != main["window"]
         and dialog["target"]["session"] == main["session"]
         and dialog["target"]["scene"] == main["scene"], "N07_B4_OPEN_DIALOG")
    same(dialog["readback"]["target"], dialog["target"])
    need(dialog["readback"]["runtime"] == native["runtime"]
         and contract.decimal(dialog["readback"]["create"]), "N07_B4_OPEN_DIALOG")
    before = n02.only(observed, "n05_dialog_baseline")
    created = n02.only(observed, "n05_dialog_created")
    closed = n02.only(observed, "n05_dialog_closed")
    confirm = n02.only(observed, "n05_confirm_delivered")
    same(created[1]["data"], {"name": "open", "window": dialog["target"]["window"],
                              "parent": main["window"], "target": dialog["target"]},
         "N07_B4_OPEN_DIALOG")
    same(closed[1]["data"], {"name": "open", "window": dialog["target"]["window"],
                             "target": dialog["target"]}, "N07_B4_OPEN_DIALOG")
    delivered = observed.get("n05_step_delivered", [])
    need(len(delivered) == 3, "N07_B4_OPEN_INPUT")
    baseline = before[1]["data"]
    need(baseline["phase"] == "open" and baseline["main_target"] == main
         and type(baseline["mapped_windows"]) is list
         and 1 <= len(baseline["mapped_windows"]) <= 64
         and len(set(baseline["mapped_windows"])) == len(baseline["mapped_windows"])
         and all(contract.decimal(v) for v in baseline["mapped_windows"])
         and main["window"] in baseline["mapped_windows"]
         and dialog["target"]["window"] not in baseline["mapped_windows"],
         "N07_B4_OPEN_NOT_FRESH")
    need(before[0] < delivered[0][0] < created[0] < delivered[1][0]
         < delivered[2][0] < closed[0] < confirm[0], "N07_B4_OPEN_ORDER")
    confirm_baseline = [(i, row) for i, row in observed.get("n07_roi_readback_observed", [])
                        if row["data"]["phase"] == "before_open_confirm"]
    need(len(confirm_baseline) == 1 and delivered[2][0] < confirm_baseline[0][0] < closed[0],
         "N07_B4_OPEN_BASELINE_ORDER")
    path = p["input"]
    need(type(path) is str and path.endswith("/Documents/n07-eight-lines.txt")
         and len(path) <= 256 and path.startswith("/home/eidolon-app/")
         and ".." not in path and all(c.isascii() and (c.isalnum() or c in "/._-")
                                     for c in path), "N07_B4_DOCUMENT_PATH")
    prior, request = 0, 0
    for index, step in enumerate(steps):
        same(step, delivered[index][1]["data"])
        target = main if index == 0 else dialog["target"]
        same(step["target"], target)
        expected = (4, 4, len(path) * 2 + sum(c == "_" or "A" <= c <= "Z"
                                           for c in path) * 2)[index]
        need(type(step["epoch"]) is int and step["epoch"] >= 0
             and contract.decimal(step["token"])
             and type(step["expected_delivery"]) is int and step["expected_delivery"] == expected
             and type(step["delivered_before"]) is int and step["delivered_before"] >= prior
             and type(step["delivered_after"]) is int
             and step["delivered_after"] >= step["delivered_before"] + expected
             and contract.decimal(step["first_key_request"])
             and contract.decimal(step["next_key_request"])
             and int(step["first_key_request"]) > request
             and int(step["next_key_request"]) - int(step["first_key_request"]) == expected,
             "N07_B4_OPEN_INPUT")
        focus = [(i, row) for i, row in observed.get("n05_focus_current", [])
                 if i < delivered[index][0] and row["data"]["target"] == target
                 and row["data"]["request_id"] == str(int(step["first_key_request"]) - 1)
                 and row["data"]["token"] == step["token"]
                 and row["data"]["epoch"] == step["epoch"]]
        need(len(focus) == 1, "N07_B4_OPEN_FOCUS")
        prior, request = step["delivered_after"], int(step["next_key_request"]) - 1
    fact = confirm[1]["data"]
    need(type(fact["attempts"]) is int and fact["attempts"] in (1, 2, 3)
         and fact["name"] == "open" and fact["target"] == dialog["target"]
         and type(fact["expected_delivery"]) is int
         and fact["expected_delivery"] == {1: 1, 2: 3, 3: 8}[fact["attempts"]]
         and type(fact["delivered_before"]) is int
         and type(fact["delivered_after"]) is int
         and fact["delivered_after"] >= fact["delivered_before"] + fact["expected_delivery"],
         "N07_B4_OPEN_CONFIRM")
    need(any(i < confirm[0] and row["data"]["target"] == fact["target"]
             and row["data"]["token"] == fact["token"]
             and row["data"]["epoch"] == fact["epoch"]
             for i, row in observed.get("n05_focus_current", [])), "N07_B4_OPEN_FOCUS")
    opened = n02.only(observed, "n07_b4_open_confirmed")
    same(opened[1]["data"], {"target": dialog["target"], "document": path},
         "N07_B4_OPEN_CONFIRM")
    need(confirm[0] < opened[0] and contract.decimal(p["final_key_request"])
         and int(p["final_key_request"]) > request, "N07_B4_OPEN_CONFIRM")
    sdk_keys = _all_sdk(native)
    need(sdk_keys == {evidence.canonical(main), evidence.canonical(dialog["target"])},
         "N07_B4_OPEN_SDK_TARGETS")
    actual = [row["data"]["readback"] for row in native["sdk_observations"]
              if row["kind"] == "real_readback" and row["data"]["target"] == dialog["target"]]
    need(len(actual) == 1, "N07_B4_OPEN_DIALOG")
    same(actual[0], dialog["readback"], "N07_B4_OPEN_DIALOG")
    return dialog


def _post_open_settling(proof, before, after):
    exact(proof, {"schema", "source", "parent_surface", "baseline_content_frame",
                  "baseline_main_content_seq", "quiet_ms", "budget_ms", "status",
                  "samples", "content_frame", "main_content_seq"})
    need(len(before["layers"]) == len(after["layers"]) == 1,
         "N07_B4_OPEN_MAIN_LAYER")
    old, new = before["layers"][0], after["layers"][0]
    need(type(proof["schema"]) is int and proof["schema"] == 1
         and proof["source"] == "normal_sdk_renderer_main_content_sequence"
         and proof["parent_surface"] == old["surface"] == new["surface"]
         and proof["baseline_content_frame"] == before["content_frame"]
         and proof["baseline_main_content_seq"] == old["content_seq"]
         and proof["content_frame"] == after["content_frame"]
         and proof["main_content_seq"] == new["content_seq"]
         and proof["status"] == "settled" and type(proof["quiet_ms"]) is int
         and proof["quiet_ms"] == 200 and type(proof["budget_ms"]) is int
         and proof["budget_ms"] == 12000
         and int(new["content_seq"]) > int(old["content_seq"])
         and int(after["content_frame"]) > int(before["content_frame"]),
         "N07_B4_OPEN_MAIN_REPAINT")
    need(all(before[k] == after[k] for k in ("geometry", "logical_size", "content_rect"))
         and all(old[k] == new[k] for k in (
             "surface", "buffer_size", "offset", "clip", "scale", "transform")),
         "N07_B4_OPEN_MAIN_REPLACED")
    samples = proof["samples"]
    need(type(samples) is list and 2 <= len(samples) <= 32, "N07_B4_SETTLING_SAMPLES")
    last = (before["request_seq"], -1, int(before["content_frame"]), int(old["content_seq"]))
    for sample in samples:
        exact(sample, {"content_frame", "main_content_seq", "request_seq", "elapsed_ms"})
        need(contract.decimal(sample["content_frame"])
             and contract.decimal(sample["main_content_seq"])
             and type(sample["request_seq"]) is int and type(sample["elapsed_ms"]) is int
             and last[0] < sample["request_seq"] <= after["request_seq"]
             and 0 <= sample["elapsed_ms"] <= 12000
             and last[1] <= sample["elapsed_ms"]
             and last[2] <= int(sample["content_frame"]) <= int(after["content_frame"])
             and last[3] <= int(sample["main_content_seq"]) <= int(new["content_seq"]),
             "N07_B4_SETTLING_ORDER")
        last = (sample["request_seq"], sample["elapsed_ms"],
                int(sample["content_frame"]), int(sample["main_content_seq"]))
    a, b = samples[-2:]
    need(a["content_frame"] == b["content_frame"] == after["content_frame"]
         and a["main_content_seq"] == b["main_content_seq"] == new["content_seq"]
         and b["request_seq"] == after["request_seq"]
         and b["elapsed_ms"] - a["elapsed_ms"] >= 200, "N07_B4_SETTLING_QUIET")


def second_readbacks(native):
    p = native["n07_second"]
    need(type(p) is dict and type(p["schema"]) is int and p["schema"] == 1
         and p["run_id"] == native["run_id"]
         and contract.hexadecimal(p["capture_id"], 64)
         and p["menu_coordinate_reviewed"] is False
         and p["menu_identity_verified"] is False
         and not any(k in p for k in ("item_pointer", "toggle", "b2", "reviewed_golden")),
         "N07_B4_SECOND_PROBE")
    target, create, runtime = p["main_target"], native["readback"]["create"], native["runtime"]
    same(target, native["readback"]["target"])
    sdk = p["before_open"]["readback"]["sdk_adapter"]
    names = ("before_open", "before_open_confirm", *SECOND_STATES)
    expected = (NUMBERS, NUMBERS, NUMBERS, CHECK)
    receipts = []
    for name, roi in zip(names, expected):
        _state(p[name], name, target, runtime, create, roi, sdk)
        review._readback(p[name]["readback"])
        receipts.append(p[name]["readback"]["receipt"])
    first = receipts[0]
    for r in receipts:
        need(all(r[k] == first[k] for k in (
            "target", "runtime", "create", "renderer_owner", "context_epoch"))
            and _backend_identity(r["backend"]) == _backend_identity(first["backend"]),
            "N07_B4_SECOND_RENDERER_CHANGED")
    for field in ("request_seq", "present_seq"):
        need(all(a[field] < b[field] for a, b in zip(receipts, receipts[1:])),
             "N07_B4_SECOND_STALE_TICKET")
    need(int(receipts[0]["content_frame"]) <= int(receipts[1]["content_frame"])
         and all(int(a["content_frame"]) < int(b["content_frame"])
                 for a, b in zip(receipts[1:], receipts[2:])), "N07_B4_SECOND_STALE_FRAME")
    need([r["backend"]["max_swap_us"] for r in receipts] ==
         sorted(r["backend"]["max_swap_us"] for r in receipts), "N07_B4_SECOND_METRIC_ORDER")
    need(type(p["pointer"]["input_epoch"]) is int and p["pointer"]["input_epoch"] >= 0
         and type(p["pointer"]["epoch_after"]) is int, "N07_B4_SECOND_POINTER_EPOCH")
    _forwarded(p["pointer"], target, p["pointer"]["input_epoch"], [160, 63])
    _popup(p["popup"], target, p["pointer"], receipts[-1]["layers"])
    need(p["popup"]["parent_surface"] == first["layers"][0]["surface"],
         "N07_B4_SECOND_POPUP_PARENT")
    _post_open_settling(p["post_open_settling"], receipts[1], receipts[2])
    return {name: p[name]["readback"] for name in names}


def second_context(native, context_raw, readbacks):
    need(native.get("error") is None and native.get("cleanup_error") is None
         and native.get("review_context", {}).get("complete") is True,
         "N07_B4_CONTEXT_BINDING")
    return _second_context_values(native, context_raw, readbacks)


def diagnostic_second_context(native, context_raw, readbacks):
    """Same physical ticket joins for a clearly rejected, incomplete sidecar."""
    need(native.get("error") in (None, "I6_N07_B4_SUPERVISOR_REFUSED")
         and native.get("cleanup_error") is None, "N07_B4_CONTEXT_BINDING")
    return _second_context_values(native, context_raw, readbacks)


def _second_context_values(native, context_raw, readbacks):
    binding = native.get("review_context")
    exact(binding, {"schema", "sha256", "bytes", "states", "complete"})
    need(type(binding["schema"]) is int and binding["schema"] == 1
         and type(binding["complete"]) is bool
         and binding["complete"] is (native.get("error") is None and native.get("cleanup_error") is None)
         and binding["states"] == list(SECOND_CONTEXT_STATES)
         and type(context_raw) is bytes and 0 < len(context_raw) <= MAX_CONTEXT_BYTES
         and type(binding["bytes"]) is int and binding["bytes"] == len(context_raw)
         and binding["sha256"] == evidence.sha(context_raw), "N07_B4_CONTEXT_BINDING")
    extra = evidence.parse(context_raw, limit=MAX_CONTEXT_BYTES)
    exact(extra, {"schema", "revision", "source", "run_id", "runtime", "owner_id",
                  "source_sha256", "capture_id", "target", "complete", "states",
                  "error", "cleanup_error"})
    p = native["n07_second"]
    need(type(extra["schema"]) is int and extra["schema"] == 1
         and extra["revision"] == "eidolon-i6-n07-b4-second-review-context-v1"
         and extra["source"] == "host_renderer_review_context"
         and extra["complete"] is binding["complete"]
         and extra["error"] == native.get("error")
         and extra["cleanup_error"] == native.get("cleanup_error")
         and all(extra[k] == native[k] for k in ("run_id", "runtime", "owner_id", "source_sha256"))
         and extra["capture_id"] == p["capture_id"] and extra["target"] == p["main_target"],
         "N07_B4_CONTEXT_ORIGIN")
    exact(extra["states"], SECOND_CONTEXT_STATES)
    result = {}
    for index, name in enumerate(SECOND_CONTEXT_STATES):
        stage = extra["states"][name]
        exact(stage, {"phase", "readback"})
        need(stage["phase"] == name, "N07_B4_CONTEXT_PHASE")
        rb = stage["readback"]
        review._readback(rb)
        r, anchor = rb["receipt"], readbacks[SECOND_STATES[index]]["receipt"]
        need(rb["sdk_adapter"] == readbacks[SECOND_STATES[index]]["sdk_adapter"]
             and all(r[k] == anchor[k] for k in (
                 "target", "runtime", "create", "renderer_owner", "context_epoch",
                 "geometry", "logical_size", "content_rect"))
             and _backend_identity(r["backend"]) == _backend_identity(anchor["backend"])
             and r["request_seq"] > anchor["request_seq"]
             and r["present_seq"] > anchor["present_seq"]
             and int(r["content_frame"]) >= int(anchor["content_frame"])
             and len(r["layers"]) == len(anchor["layers"])
             and r["backend"]["max_swap_us"] >= anchor["backend"]["max_swap_us"],
             "N07_B4_CONTEXT_IDENTITY")
        for layer, old in zip(r["layers"], anchor["layers"]):
            need(all(layer[k] == old[k] for k in (
                "surface", "buffer_size", "offset", "clip", "scale", "transform"))
                and int(layer["content_seq"]) >= int(old["content_seq"])
                and (r["content_frame"] != anchor["content_frame"] or layer == old),
                "N07_B4_CONTEXT_LAYER")
        if index == 0:
            following = readbacks[SECOND_STATES[1]]["receipt"]
            need(r["request_seq"] < following["request_seq"]
                 and r["present_seq"] < following["present_seq"]
                 and int(r["content_frame"]) <= int(following["content_frame"])
                 and r["backend"]["max_swap_us"] <= following["backend"]["max_swap_us"],
                 "N07_B4_CONTEXT_ORDER")
        need(len(rb["pixels"]) == 4, "N07_B4_CONTEXT_TILES")
        for item, (uid, x, y) in zip(rb["pixels"], (
                (101, 0, 0), (102, 256, 0), (103, 0, 128), (104, 256, 128))):
            need(item["id"] == uid and item["rect"] == {
                "x": x, "y": y, "width": 256, "height": 128}, "N07_B4_CONTEXT_TILES")
        result[name] = rb
    return result


def validate_session(native, environment, source, session, context_raw=None):
    """Independent retired-session gate, usable before second Launch is allowed."""
    need(session in ("first", "second"), "N07_B4_SESSION")
    if session == "first":
        inspect_b2(native)
        need(native.get("review_context") is not None, "N07_B4_FIRST_CONTEXT_MISSING")
        _, first_context = review._observed(native, context_raw)
        probe = native["n07_probe"]
        readbacks = {"before_menu": probe["before_menu"]["readback"],
                     "menu_open": probe["menu_open"]["readback"],
                     **{name: probe["b2"][name]["readback"] for name in pixel.STATES[1:4]},
                     **{name: rb for name, rb in first_context.items() if name != "initial_menu_context"}}
        _pixel_events(native, readbacks)
    else:
        need(native.get("schema") == SECOND_SCHEMA and native.get("variant") == SECOND_VARIANT,
             "N07_B4_SECOND_SCHEMA")
    base, env, events, observed, cap, context = _common_native(native, source, environment)
    if session == "first":
        app_error = _close(native, observed)
        probe = native["n07_probe"]
        need(len(_all_sdk(native)) == 1, "N07_B4_FIRST_TARGET_COUNT")
    else:
        n04.validate_control_sequence(native["trace"]["events"])
        _open(native, observed)
        readbacks = second_readbacks(native)
        contexts = second_context(native, context_raw, readbacks)
        _pixel_events(native, {**readbacks, **contexts})
        app_error, probe = None, native["n07_second"]
        stops = [e["data"]["record"]["event"]["payload"] for e in native["trace"]["events"]
                 if e["kind"] == "protocol_event" and e["data"]["record"]["event"]["type"] == "stopped"]
        need(len(stops) == 1 and stops[0]["reason"] == "stop"
             and all(stops[0][k] is True for k in (
                 "process_complete", "pipes_eof", "remote_cleaned", "receipt_written")),
             "N07_B4_SECOND_STOP")
    need(probe["capture_id"] == cap["capture_id"], "N07_B4_CAPTURE_MISMATCH")
    _cleanup(base, env, events, observed, app_error)
    return {"status": "session_collected", "session": session, "capture": cap,
            "context": context, "runtime": native["runtime"], "owner_id": native["owner_id"],
            "normal_cleanup_verified": True, "actual_app_error": app_error,
            "host_v3_acceptance_passed": False, "verified_n07_assertions": []}


def _retirement_binding(native, environment, source, session):
    """Source/owner admission for cleanup, without rewriting exercise errors."""
    need(session in ("first", "second") and type(native) is dict
         and n02.NATIVE_KEYS <= set(native), "N07_B4_RETIREMENT_IDENTITY")
    need(native["schema"] == ("eidolon-i6-n07-b2-native-probe-v1" if session == "first" else SECOND_SCHEMA)
         and native["host_v3_acceptance_passed"] is False
         and native["n07_assertions_verified"] == []
         and evidence.identity(native["run_id"]) and evidence.identity(native["owner_id"])
         and contract.decimal(native["runtime"])
         and type(native["start_count"]) is int and native["start_count"] == 1
         and native["sdk_retired"] is True and native["cleanup_error"] is None,
         "N07_B4_RETIREMENT_IDENTITY")
    # A supervisor refusal is an observation outcome. It remains in the raw
    # native receipt and cannot make this cleanup result an exercise success.
    need(native["error"] in (None, "I6_N07_B4_SUPERVISOR_REFUSED"),
         "N07_B4_RETIREMENT_EXERCISE_ERROR")
    evidence._source(source)
    evidence._source(native["build_source"])
    need(source["dirty"] is False and source["build_features"] == []
         and native["source_sha256"] == source["tree_sha256"], "N07_B4_SOURCE")
    same(native["build_source"], {**source, "build_features": ["host-acceptance"]}, "N07_B4_SOURCE")
    hashes = evidence.source_input_hashes()
    same({key: native[key] for key in hashes}, hashes, "N07_B4_SOURCE")
    exact(environment, n01.ENVIRONMENT_FIELDS | {"driver_control"})
    need(environment["revision"] == "eidolon-i6-n07-b4-environment-v1",
         "N07_B4_ENVIRONMENT_REVISION")
    env = copy.deepcopy(environment)
    control = env.pop("driver_control")
    _driver_control(control, env["driver"]["uid"], env["final"])
    env["revision"] = n01.ENVIRONMENT_REVISION
    n01._environment(env, native["run_id"], native["owner_id"], native["source_sha256"])
    events, capture, context, _, _ = n02.transport(native, env, "mousepad_editor")
    expected = evidence.sha((native["run_id"] + ":" + native["runtime"] + ":" +
                             capture["capture_id"]).encode("ascii"))[:32]
    need(native["owner_id"] == expected, "N07_B4_RETIREMENT_OWNER")
    return env, events, n02.ordinal_events(native["observations"], limit=128), capture, context.record()


def validate_retirement(native, environment, source, session):
    """Prove started-session product cleanup separately from GUI success.

    The error field, Stop/Close reason and all original receipt bytes retain
    their original meaning. This result cannot authorize a second session:
    the supervisor must still require the existing full session validation.
    """
    env, events, observed, capture, context = _retirement_binding(native, environment, source, session)
    keys = _all_sdk(native)
    main = native["readback"]["target"]
    need(evidence.canonical(main) in keys and main["session"] == capture["session"],
         "N07_B4_RETIREMENT_OWNER")
    for row in native["sdk_observations"]:
        need(row["data"]["target"]["session"] == capture["session"]
             and row["data"]["target"]["scene"] == main["scene"], "N07_B4_RETIREMENT_OWNER")
    stopped = [row["data"]["record"]["event"]["payload"] for row in native["trace"]["events"]
               if row["kind"] == "protocol_event" and row["data"]["record"]["event"]["type"] == "stopped"]
    need(len(stopped) == 1 and all(stopped[0][key] is True for key in (
        "process_complete", "pipes_eof", "remote_cleaned", "receipt_written")),
         "N07_B4_RETIREMENT_TERMINAL")
    reason = stopped[0]["reason"]
    if reason == "process_exited":
        app_error = _close(native, observed)
    else:
        need(reason == "stop", "N07_B4_RETIREMENT_TERMINAL")
        n04.validate_control_sequence(native["trace"]["events"])
        app_error = None
    _cleanup(native, env, events, observed, app_error)
    return {"status": "product_retirement_verified", "session": session,
            "runtime": native["runtime"], "owner_id": native["owner_id"],
            "capture": capture, "context": context, "native_error": native["error"],
            "terminal_reason": reason, "actual_app_error": app_error,
            "product_cleanup_verified": True, "exercise_success_verified": False,
            "host_v3_acceptance_passed": False, "verified_n07_assertions": []}


def _keyfile_diagnostics(supervisor):
    """Optional same-read key inventory is diagnostic, never an oracle substitute."""
    values = supervisor.get("keyfile_diagnostics", {})
    need(type(values) is dict and set(values) <= set(PHASES), "N07_B4_KEYFILE_DIAGNOSTIC")
    bindings = {"first_before_launch": supervisor["keyfile"].get("before_first_launch"),
                "first_after_toggle": supervisor["keyfile"].get("after_first_toggle"),
                "second_before_launch": supervisor["supplemental_keyfile"].get("second_before_launch"),
                "second_after_observation": supervisor["keyfile"].get("second_session")}
    for phase, value in values.items():
        proof = bindings.get(phase)
        need(type(proof) is dict, "N07_B4_KEYFILE_DIAGNOSTIC")
        exact(value, {"schema", "revision", "source", "proof_sha256", "file_sha256", "file_size",
                      "value_hash", "status", "reason", "section_count", "entry_count", "sections"})
        file = proof["file"]
        need(type(value["schema"]) is int and value["schema"] == 1
             and value["revision"] == "eidolon-i6-n07-keyfile-diagnostic-v1"
             and value["source"] == "same_snapshot_keyfile_bytes"
             and value["value_hash"] == "sha256_utf8_parsed_value"
             and value["proof_sha256"] == evidence.sha(evidence.canonical(proof))
             and value["file_sha256"] == (file["sha256"] if file else None)
             and value["file_size"] == (file["size"] if file else None), "N07_B4_KEYFILE_DIAGNOSTIC")
        need(type(value["section_count"]) is int and 0 <= value["section_count"] <= settings.MAX_BYTES
             and type(value["entry_count"]) is int and 0 <= value["entry_count"] <= settings.MAX_BYTES,
             "N07_B4_KEYFILE_DIAGNOSTIC")
        if file is None:
            need(value["section_count"] == value["entry_count"] == 0 and value["sections"] == []
                 and value["status"] == "observed", "N07_B4_KEYFILE_DIAGNOSTIC")
        if value["status"] == "unavailable":
            need(value["reason"] in ("N07_KEYFILE_DIAGNOSTIC_BOUNDS", "N07_KEYFILE_DIAGNOSTIC_NAMES")
                 and value["sections"] == [], "N07_B4_KEYFILE_DIAGNOSTIC")
            continue
        need(value["status"] == "observed" and value["reason"] is None
             and type(value["sections"]) is list and len(value["sections"]) == value["section_count"] <= 64,
             "N07_B4_KEYFILE_DIAGNOSTIC")
        names, entries, total, target_values = [], 0, 0, []
        for section in value["sections"]:
            exact(section, {"section", "keys"})
            names.append(section["section"])
            need(type(section["keys"]) is list, "N07_B4_KEYFILE_DIAGNOSTIC")
            keys = []
            for item in section["keys"]:
                exact(item, {"key", "value_sha256"})
                keys.append(item["key"])
                need(contract.hexadecimal(item["value_sha256"], 64), "N07_B4_KEYFILE_DIAGNOSTIC")
                if section["section"] == settings.SECTION and item["key"] == settings.KEY:
                    target_values.append(item["value_sha256"])
            for name in [section["section"], *keys]:
                need(type(name) is str and re.fullmatch(r"[A-Za-z0-9_./-]{1,256}", name) is not None,
                     "N07_B4_KEYFILE_DIAGNOSTIC")
                total += len(name)
            need(keys == sorted(set(keys)), "N07_B4_KEYFILE_DIAGNOSTIC")
            entries += len(keys)
        need(names == sorted(set(names)) and entries == value["entry_count"] <= 256 and total <= 8192,
             "N07_B4_KEYFILE_DIAGNOSTIC")
        need(target_values == ([evidence.sha(("true" if proof["value"] else "false").encode("ascii"))]
                               if proof["key_present"] else []), "N07_B4_KEYFILE_DIAGNOSTIC")


def _storage(value, sessions, environments):
    exact(value, {"record", "storage_digest", "uid", "passwd_home", "root_path",
                  "root_identity", "owner", "owner_sha256", "directories",
                  "control_files", "control_files_final"})
    record = contract.StorageRecord.parse(contract.canonical_json(value["record"]))
    owner = contract.StorageOwner.parse(contract.canonical_json(value["owner"]))
    need(value["storage_digest"] == record.sha256()
         and type(value["uid"]) is int and value["uid"] > 0
         and value["passwd_home"] == "/home/eidolon-app"
         and value["owner_sha256"] == owner.sha256(), "N07_B4_STORAGE_IDENTITY")
    exact(value["control_files"], {"owner.json", "profile.lock"})
    same(value["control_files"], value["control_files_final"], "N07_B4_CONTROL_FILE_REPLACED")
    for name, stamp in value["control_files"].items():
        exact(stamp, {"dev", "ino", "uid", "mode", "nlink", "size", "mtime_ns", "ctime_ns"})
        need(all(type(stamp[k]) is int and 0 < stamp[k] < (1 << 63)
                 for k in ("dev", "ino", "uid", "nlink", "mtime_ns", "ctime_ns"))
             and stamp["uid"] == value["uid"] and stamp["nlink"] == 1
             and type(stamp["mode"]) is int and stamp["mode"] == 0o600
             and type(stamp["size"]) is int
             and stamp["size"] == (len(owner.canonical()) if name == "owner.json" else 0),
             "N07_B4_CONTROL_FILE_INVALID")
    r = record.record()
    path = "/home/eidolon-app/.local/share/eidolon/apps/" + r["installation_id"] + "/" + \
           r["profile_storage_id"] + "/" + contract.EDITOR
    need(value["root_path"] == path, "N07_B4_UNTRUSTED_ROOT")
    for label in ("first", "second"):
        context = contract.Context.parse(contract.canonical_json(sessions[label]["context"]))
        record.matches_context(context)
        owner.matches_context(context)
        need(context.record()["uid"] == value["uid"] == environments[label]["app"]["uid"]
             and environments[label]["app"]["passwd_home"] == value["passwd_home"],
             "N07_B4_STORAGE_UID")
    root = value["root_identity"]
    exact(root, {"dev", "ino", "uid", "mode"})
    need(all(type(root[k]) is int and 0 < root[k] < (1 << 63) for k in ("dev", "ino", "uid"))
         and root["uid"] == value["uid"] and type(root["mode"]) is int
         and root["mode"] == 0o700, "N07_B4_ROOT_IDENTITY")
    directories = value["directories"]
    expected = list(PurePosixPath(path).parts)
    expected[-1] = path
    need(type(directories) is list and len(directories) == len(expected),
         "N07_B4_ANCESTOR_CHAIN")
    for index, (directory, relative) in enumerate(zip(directories, expected)):
        exact(directory, {"relative", "dev", "ino", "uid", "mode"})
        need(directory["relative"] == relative
             and all(type(directory[k]) is int and 0 < directory[k] < (1 << 63)
                     for k in ("dev", "ino"))
             and type(directory["uid"]) is int
             and directory["uid"] == (0 if index < 2 else value["uid"])
             and type(directory["mode"]) is int and 0 <= directory["mode"] <= 0o7777
             and directory["mode"] & 0o022 == 0, "N07_B4_ANCESTOR_CHAIN")
    same({k: directories[-1][k] for k in root}, root, "N07_B4_ROOT_IDENTITY")
    return r


def _document(value, storage, before, second):
    from linux_real_app_v3_n07_b4_storage import DOCUMENT_BYTES
    exact(value, {"relative_path", "bytes_b64", "size", "sha256", "line_count",
                  "created_at_ns", "identity", "final"})
    need(value["relative_path"] == "Documents/n07-eight-lines.txt"
         and value["bytes_b64"] == base64.b64encode(DOCUMENT_BYTES).decode("ascii")
         and type(value["size"]) is int and value["size"] == len(DOCUMENT_BYTES)
         and value["sha256"] == evidence.sha(DOCUMENT_BYTES)
         and type(value["line_count"]) is int and value["line_count"] == 8
         and type(value["created_at_ns"]) is int
         and before["received_at_ns"] <= value["created_at_ns"]
         <= before["observation"]["observed_at_ns"], "N07_B4_DOCUMENT_BASELINE")
    identity = value["identity"]
    exact(identity, {"dev", "ino", "uid", "mode", "nlink", "size", "mtime_ns", "ctime_ns"})
    need(all(type(identity[k]) is int and 0 < identity[k] < (1 << 63)
             for k in ("dev", "ino", "uid", "nlink", "size", "mtime_ns", "ctime_ns"))
         and identity["uid"] == storage["uid"] and identity["nlink"] == 1
         and type(identity["mode"]) is int and identity["mode"] == 0o600
         and identity["size"] == len(DOCUMENT_BYTES), "N07_B4_DOCUMENT_IDENTITY")
    same(value["final"], {**identity, "sha256": value["sha256"]}, "N07_B4_DOCUMENT_CHANGED")
    need(second["n07_second"]["input"] == storage["root_path"] + "/" + value["relative_path"]
         and second["n07_second"]["document"] == second["n07_second"]["input"],
         "N07_B4_DOCUMENT_FOREIGN")


def _fingerprints(records, natives, environments, source):
    exact(records, {"first", "second"}, "N07_B4_FINGERPRINT_MISSING")
    results = {}
    for label in ("first", "second"):
        record = fingerprint.validate_fingerprint(records[label], natives[label],
                                                  environments[label], source)
        need(record["missing"] in ([], ["theme"])
             and record["evidence"]["observation_errors"] == [],
             "N07_B4_FINGERPRINT_INCOMPLETE")
        results[label] = record
    same(results["first"]["values"], results["second"]["values"], "N07_B4_FINGERPRINT_MISMATCH")
    same(results["first"]["missing"], results["second"]["missing"], "N07_B4_FINGERPRINT_MISMATCH")
    processes = [records[label]["evidence"]["process"] for label in ("first", "second")]
    need(all(type(p) is dict for p in processes)
         and len({(p["pid"], p["start_ticks"]) for p in processes}) == 2,
         "N07_B4_PROCESS_REUSED")
    for part in ("policy", "source_pins", "fonts", "libraries", "renderer"):
        same(records["first"]["evidence"][part], records["second"]["evidence"][part],
             "N07_B4_FINGERPRINT_MISMATCH")
    return results


def _barriers(native, supervisor, sessions, natives, environments):
    host, external = native["barriers"], supervisor["barriers"]
    need(type(host) is list and type(external) is list
         and len(host) == len(external) == len(PHASES), "N07_B4_BARRIER_MISSING")
    last_time = 0
    result = {}
    for phase, left, right in zip(PHASES, host, external):
        exact(left, {"request", "request_sha256", "response", "response_monotonic_ns", "error"})
        exact(right, {"request", "request_sha256", "received_at_ns", "observation",
                      "observation_sha256", "response", "sent_at_ns"})
        request, observation, response = right["request"], right["observation"], right["response"]
        exact(request, {"schema", "run_id", "session", "phase", "runtime", "capture_id",
                        "capture", "context", "transport_watermark", "monotonic_ns"})
        label = "first" if phase.startswith("first_") else "second"
        s, n, env = sessions[label], natives[label], environments[label]
        need(type(request["schema"]) is int and request["schema"] == 1
             and request["run_id"] == native["run_id"]
             and request["session"] == label and request["phase"] == phase
             and request["runtime"] == n["runtime"]
             and request["capture_id"] == s["capture"]["capture_id"]
             and type(request["transport_watermark"]) is int
             and 0 < request["transport_watermark"] <= len(n["trace"]["events"]),
             "N07_B4_BARRIER_IDENTITY")
        same(request["capture"], s["capture"])
        same(request["context"], s["context"])
        same(left["request"], request)
        # Requests contain only ASCII contract identities, so Rust's raw
        # sorted JSON and this exact contract serialization coincide.
        need(left["request_sha256"] == right["request_sha256"]
             == evidence.sha(evidence.canonical(request))
             and left["error"] is None, "N07_B4_BARRIER_DIGEST")
        exact(observation, {"phase", "session", "observed_at_ns", "keyfile", "census",
                            "driver_control", "product_cleanup_verified", "document_sha256"})
        need(observation["phase"] == phase and observation["session"] == label
             and right["observation_sha256"] == evidence.sha(evidence.canonical(observation)),
             "N07_B4_OBSERVATION_DIGEST")
        exact(response, {"schema", "phase", "request_sha256", "allow", "observation_sha256"})
        need(type(response["schema"]) is int and response["schema"] == 1
             and response["phase"] == phase and response["allow"] is True
             and response["request_sha256"] == right["request_sha256"]
             and response["observation_sha256"] == right["observation_sha256"],
             "N07_B4_BARRIER_REFUSED")
        same(left["response"], response)
        times = [request["monotonic_ns"], right["received_at_ns"], observation["observed_at_ns"],
                 right["sent_at_ns"], left["response_monotonic_ns"]]
        need(all(type(t) is int and 0 < t < (1 << 63) for t in times)
             and times == sorted(times) and last_time < times[0], "N07_B4_BARRIER_TIME")
        last_time = times[-1]
        need(observation["document_sha256"] == supervisor["document"]["sha256"],
             "N07_B4_DOCUMENT_CHANGED")
        events = n02.ordinal_events(n["observations"], limit=128)
        if phase.endswith("_retired"):
            census(observation["census"])
            same(observation["census"], env["final"])
            same(observation["driver_control"], env["driver_control"])
            need(observation["product_cleanup_verified"] is True
                 and observation["keyfile"] is None
                 and request["transport_watermark"] == len(n["trace"]["events"]),
                 "N07_B4_RETIREMENT_GATE")
            n02.only(events, "registry_destroy_return")
        else:
            need(observation["product_cleanup_verified"] is False
                 and observation["census"] is None and observation["driver_control"] is None,
                 "N07_B4_FALSE_RETIREMENT")
            recorded = [(i, e) for i, e in events.get("n07_b4_barrier", [])
                        if e["data"]["request"]["phase"] == phase]
            need(len(recorded) == 1, "N07_B4_BARRIER_NATIVE_MISSING")
            same(recorded[0][1]["data"], left)
            if phase.endswith("_before_launch"):
                starts = [int(e["sequence"]) for e in n["trace"]["events"]
                          if e["kind"] == "control_send_attempt" and e["data"]["action"] == "start"]
                need(len(starts) == 1 and request["transport_watermark"] < starts[0],
                     "N07_B4_PRELAUNCH_TOO_LATE")
                launches = [i for i, e in events.get("n02_command_accepted", [])
                            if e["data"]["command"] == "linux_desktop_v3_launch"]
                need(len(launches) == 1 and recorded[0][0] < launches[0],
                     "N07_B4_PRELAUNCH_TOO_LATE")
            elif phase == "first_after_toggle":
                need(n02.only(events, "n07_b2_candidate_gui_transition")[0] < recorded[0][0]
                     < n02.only(events, "n07_b4_close_intent")[0], "N07_B4_TOGGLE_OBSERVATION_ORDER")
            else:
                captures = [(i, e) for i, e in events.get("n07_roi_readback_observed", [])
                            if e["data"]["phase"] == "second_on_menu_context"]
                need(len(captures) == 1 and captures[0][0] < recorded[0][0]
                     < n02.only(events, "origin_retired")[0], "N07_B4_SECOND_OBSERVATION_ORDER")
        result[phase] = right
    # The entire first retired ACK precedes even second Inspect's prelaunch
    # barrier. One Registry owner and the new Runtime are bound below; no
    # global frame monotonicity is inferred from these lifecycle timestamps.
    need(host[2]["response_monotonic_ns"] < host[3]["request"]["monotonic_ns"],
         "N07_B4_SECOND_BEFORE_CLEANUP")
    for index, label in ((0, "first"), (3, "second")):
        observed = n02.ordinal_events(natives[label]["observations"], limit=128)
        creation = n02.only(observed, "n07_b4_runtime_created")[1]["data"]
        exact(creation, {"session", "runtime", "started_at_ns", "completed_at_ns"})
        need(creation["session"] == label and creation["runtime"] == natives[label]["runtime"]
             and type(creation["started_at_ns"]) is int and type(creation["completed_at_ns"]) is int
             and 0 < creation["started_at_ns"] <= creation["completed_at_ns"]
             < host[index]["request"]["monotonic_ns"], "N07_B4_RUNTIME_CREATE_TIME")
        if label == "second":
            need(creation["started_at_ns"] > host[2]["response_monotonic_ns"],
                 "N07_B4_SECOND_BEFORE_CLEANUP")
    return result


def validate_collection(native, supervisor, first, second, environments, fingerprints,
                        contexts, source, *, native_raws=None):
    """Revalidate both actual sessions and the independent supervisor records.

    Exact original native bytes are mandatory for the Host's file digests.
    Dictionary reserialization cannot replace those acquired artifacts.
    """
    exact(native, {"schema", "revision", "run_id", "source_sha256", "build_source",
                   "sessions", "barriers", "error", "reviewed_golden",
                   "menu_identity_verified", "verified_n07_assertions", "host_v3_acceptance_passed"})
    exact(supervisor, {"schema", "revision", "run_id", "source", "trusted_storage",
                       "document", "keyfile", "supplemental_keyfile", "barriers",
                       "fingerprint_observers", "failures", "external_rescue", "final_census"}
          | ({"keyfile_diagnostics"} if "keyfile_diagnostics" in supervisor else set()))
    need(type(native["schema"]) is int and native["schema"] == 1
         and native["revision"] == NATIVE_REVISION
         and type(supervisor["schema"]) is int and supervisor["schema"] == 1
         and supervisor["revision"] == SUPERVISOR_REVISION
         and evidence.identity(native["run_id"]) and native["run_id"] == supervisor["run_id"]
         and native["error"] is None and supervisor["failures"] == []
         and supervisor["external_rescue"] is False
         and native["reviewed_golden"] is False and native["menu_identity_verified"] is False
         and native["verified_n07_assertions"] == []
         and native["host_v3_acceptance_passed"] is False, "N07_B4_COLLECTION_INCOMPLETE")
    evidence._source(source)
    need(source["dirty"] is False and source["build_features"] == [], "N07_B4_SOURCE")
    same(supervisor["source"], source, "N07_B4_SOURCE")
    need(native["source_sha256"] == source["tree_sha256"], "N07_B4_SOURCE")
    same(native["build_source"], {**source, "build_features": ["host-acceptance"]}, "N07_B4_SOURCE")
    for value in (environments, fingerprints, contexts, native_raws):
        exact(value, {"first", "second"}, "N07_B4_SESSION_MISSING")
    natives = {"first": first, "second": second}
    sessions = {}
    need(type(native["sessions"]) is list and len(native["sessions"]) == 2,
         "N07_B4_SESSION_MISSING")
    for label, summary in zip(("first", "second"), native["sessions"]):
        n, raw = natives[label], native_raws[label]
        need(type(raw) is bytes and 0 < len(raw) <= MAX_NATIVE_BYTES, "N07_B4_NATIVE_RAW_MISSING")
        same(evidence.parse(raw, limit=MAX_NATIVE_BYTES), n, "N07_B4_NATIVE_RAW_CHANGED")
        exact(summary, {"session", "runtime", "owner_id", "native_file", "native_sha256", "native_bytes",
                        "context_file", "review_context", "error", "cleanup_error", "product_cleanup_complete"})
        need(summary["session"] == label and summary["runtime"] == n["runtime"]
             and summary["owner_id"] == n["owner_id"] and n["run_id"] == native["run_id"]
             and summary["native_file"] == label + "-native.json"
             and summary["context_file"] == label + "-context.json"
             and summary["native_sha256"] == evidence.sha(raw)
             and type(summary["native_bytes"]) is int and summary["native_bytes"] == len(raw)
             and summary["error"] is None and summary["cleanup_error"] is None
             and summary["product_cleanup_complete"] is True, "N07_B4_SESSION_BINDING")
        same(summary["review_context"], n["review_context"])
        sessions[label] = validate_session(n, environments[label], source, label, contexts[label])
    a, b = sessions["first"], sessions["second"]
    need(a["runtime"] != b["runtime"] and a["owner_id"] != b["owner_id"]
         and all(a["capture"][key] != b["capture"][key] for key in ("nonce", "capture_id")),
         "N07_B4_SESSION_REUSED")
    same(environments["first"]["driver_control"], environments["second"]["driver_control"],
         "N07_B4_CONTROL_CHANGED")
    storage = supervisor["trusted_storage"]
    _storage(storage, sessions, environments)
    exact(supervisor["keyfile"], {"before_first_launch", "after_first_toggle", "second_session"})
    keyfiles = supervisor["keyfile"]
    settings.validate_file_sequence(keyfiles["before_first_launch"], keyfiles["after_first_toggle"],
                                    keyfiles["second_session"])
    _keyfile_diagnostics(supervisor)
    for proof in keyfiles.values():
        need(proof["storage_digest"] == storage["storage_digest"]
             and proof["root_dev"] == storage["root_identity"]["dev"]
             and proof["root_ino"] == storage["root_identity"]["ino"], "N07_B4_KEYFILE_ROOT")
        if proof["file"] is not None:
            need(proof["file"]["uid"] == storage["uid"], "N07_B4_KEYFILE_OWNER")
    phases = _barriers(native, supervisor, sessions, natives, environments)
    for phase, name in (("first_before_launch", "before_first_launch"),
                        ("first_after_toggle", "after_first_toggle"),
                        ("second_after_observation", "second_session")):
        same(phases[phase]["observation"]["keyfile"], keyfiles[name], "N07_B4_SNAPSHOT_MOMENT")
    exact(supervisor["supplemental_keyfile"], {"second_before_launch"})
    supplemental = supervisor["supplemental_keyfile"]["second_before_launch"]
    same(phases["second_before_launch"]["observation"]["keyfile"], supplemental)
    settings.validate_file_sequence(keyfiles["before_first_launch"], keyfiles["after_first_toggle"], supplemental)
    _document(supervisor["document"], storage, phases["first_before_launch"], second)
    _fingerprints(fingerprints, natives, environments, source)
    observers = supervisor["fingerprint_observers"]
    exact(observers, {"first", "second"})
    for label, after in (("first", "first_after_toggle"), ("second", "second_after_observation")):
        timing = observers[label]
        exact(timing, {"started_at_ns", "stopped_at_ns"})
        begin = phases[label + "_before_launch"]
        end = phases[after]
        need(all(type(timing[k]) is int for k in timing)
             and begin["received_at_ns"] <= timing["started_at_ns"] <= begin["sent_at_ns"]
             and end["received_at_ns"] <= timing["stopped_at_ns"] <= end["sent_at_ns"]
             and timing["started_at_ns"] < timing["stopped_at_ns"], "N07_B4_OBSERVER_INTERVAL")
    need(observers["first"]["stopped_at_ns"] < observers["second"]["started_at_ns"],
         "N07_B4_OBSERVER_REUSED")
    census(supervisor["final_census"])
    return {"status": "collected", "revision": COLLECTION_REVISION,
            "sessions": sessions, "same_storage_verified": True,
            "keyfile_sequence_verified": True, "normal_cleanup_verified": True,
            "renderer_states": list(pixel.STATES), "fingerprint_missing": fingerprints["first"]["missing"],
            "menu_identity_verified": False, "reviewed_golden": False,
            "host_v3_acceptance_passed": False, "verified_n07_assertions": []}


def load_collection(root: Path):
    """Read only the fixed B4 evidence set, preserving exact native bytes."""
    raw = {label: review._read(root / (label + "-native.json")) for label in ("first", "second")}
    native = evidence.parse(review._read(root / "n07-b4-native.json"))
    supervisor = evidence.parse(review._read(root / "n07-b4-supervisor.json"))
    report = evidence.parse(review._read(root / "n07-b4-validation.json"))
    need(type(report) is dict and report.get("revision") == COLLECTION_REVISION
         and report.get("reviewed_golden") is False
         and report.get("menu_identity_verified") is False
         and report.get("verified_n07_assertions") == []
         and report.get("host_v3_acceptance_passed") is False, "N07_B4_REPORT_PROMOTION")
    source = report["source"]
    return {"native": native, "supervisor": supervisor,
            "first": evidence.parse(raw["first"], limit=MAX_NATIVE_BYTES),
            "second": evidence.parse(raw["second"], limit=MAX_NATIVE_BYTES),
            "environments": {label: evidence.parse(review._read(root / (label + "-environment.json")))
                             for label in ("first", "second")},
            "fingerprints": {label: evidence.parse(review._read(root / (label + "-fingerprint.json")))
                             for label in ("first", "second")},
            "contexts": {label: review._read(root / (label + "-context.json"))
                         for label in ("first", "second")},
            "source": source, "native_raws": raw}


def validate_saved(root: Path):
    return validate_collection(**load_collection(root))


def adversarial_checks(**inputs):
    """Corrupt saved evidence and demand rejection beyond producer status.

    Native summaries are deliberately rebound to each mutated raw native
    object. Rejection therefore exercises semantic joins, rather than merely
    noticing that a wrapper file digest changed.
    """
    validate_collection(**inputs)

    def reuse_nonce(data):
        nonce = next(row["capture"]["nonce"] for row in data["first"]["trace"]["events"]
                     if row["capture"] is not None)
        for row in data["second"]["trace"]["events"]:
            if row["capture"] is not None:
                row["capture"]["nonce"] = nonce
            if row["kind"] == "capture_created":
                row["data"]["context"]["nonce"] = nonce

    def early_second_runtime(data):
        early = data["native"]["barriers"][2]["response_monotonic_ns"] - 1
        creation = next(row for row in data["second"]["observations"]
                        if row["kind"] == "n07_b4_runtime_created")
        creation["data"]["started_at_ns"] = early

    mutations = (
        ("foreign_storage", lambda d: d["supervisor"]["trusted_storage"]["record"]
         .update(profile_storage_id="f" * 32)),
        ("foreign_root", lambda d: d["supervisor"]["trusted_storage"]["root_identity"].update(ino=999999)),
        ("initial_true", lambda d: d["supervisor"]["keyfile"]["before_first_launch"]
         .update(key_present=True, value=True)),
        ("initial_false", lambda d: d["supervisor"]["keyfile"]["before_first_launch"]
         .update(key_present=True, value=False)),
        ("other_setting_changed", lambda d: d["supervisor"]["keyfile"]["second_session"]
         .update(others_sha256="f" * 64)),
        ("restart_bytes_changed", lambda d: d["supervisor"]["keyfile"]["second_session"]
         ["file"].update(sha256="f" * 64)),
        ("runtime_reuse", lambda d: d["second"].update(runtime=d["first"]["runtime"])),
        ("owner_reuse", lambda d: d["second"].update(owner_id=d["first"]["owner_id"])),
        ("capture_reuse", lambda d: d["second"]["n07_second"]
         .update(capture_id=d["first"]["n07_probe"]["capture_id"])),
        ("nonce_reuse", reuse_nonce),
        ("second_missing", lambda d: d["second"]["n07_second"].pop("second_on_checkmark")),
        ("first_relabel", lambda d: d["second"]["n07_second"].update(
            second_on_checkmark=copy.deepcopy(d["first"]["n07_probe"]["b2"]["first_on_checkmark"]))),
        ("foreign_create", lambda d: d["second"]["n07_second"]["second_on_checkmark"]
         ["readback"]["receipt"].update(create="999999")),
        ("foreign_dialog_create", lambda d: d["second"]["n07_second"]["dialogs"][0]
         ["readback"].update(create="999999")),
        ("foreign_dialog_closed", lambda d: next(row["data"] for row in d["second"]["observations"]
         if row["kind"] == "n05_dialog_closed").update(window="999999")),
        ("foreign_renderer", lambda d: d["second"]["n07_second"]["second_on_checkmark"]
         ["readback"]["receipt"].update(renderer_owner=999999)),
        ("integer_frame", lambda d: d["second"]["n07_second"]["second_on_checkmark"]
         ["readback"]["receipt"].update(content_frame=8)),
        ("old_frame", lambda d: d["second"]["n07_second"]["second_on_numbers_1_8"]
         ["readback"]["receipt"].update(content_frame="1")),
        ("old_main_buffer", lambda d: d["second"]["n07_second"]["post_open_settling"]
         .update(main_content_seq=d["second"]["n07_second"]["post_open_settling"]["baseline_main_content_seq"])),
        ("negative_settling_time", lambda d: d["second"]["n07_second"]["post_open_settling"]
         ["samples"][0].update(elapsed_ms=-1)),
        ("fingerprint_changed", lambda d: d["fingerprints"]["second"]["values"].update(gtk_version="0.0.0")),
        ("configured_theme_promoted", lambda d: d["fingerprints"]["second"]["values"].update(theme="Adwaita")),
        ("cleanup_gate_removed", lambda d: d["supervisor"]["barriers"][2]
         ["observation"].update(product_cleanup_verified=False)),
        ("first_not_retired", lambda d: d["first"].update(sdk_retired=False)),
        ("runtime_created_before_cleanup", early_second_runtime),
        ("external_rescue", lambda d: d["supervisor"].update(external_rescue=True)),
        ("document_changed", lambda d: d["supervisor"]["document"]["final"].update(sha256="f" * 64)),
        ("fake_approval", lambda d: d["native"].update(reviewed_golden=True)),
        ("false_acceptance", lambda d: d["native"].update(verified_n07_assertions=["pointer_toggled_setting"])),
    )
    rejected = []
    for name, mutate in mutations:
        changed = copy.deepcopy(inputs)
        mutate(changed)
        for label, summary in zip(("first", "second"), changed["native"]["sessions"]):
            raw = evidence.canonical(changed[label])
            changed["native_raws"][label] = raw
            summary["native_sha256"], summary["native_bytes"] = evidence.sha(raw), len(raw)
        try:
            validate_collection(**changed)
        except (ValueError, KeyError, TypeError, OverflowError):
            rejected.append(name)
            continue
        raise B4Invalid("N07_B4_MUTATION_ACCEPTED")
    try:
        pixel.load_reviewed()
    except pixel.PixelEvidenceError as error:
        need(str(error) == "N07_GOLDEN_UNREVIEWED", "N07_B4_GOLDEN_GATE_CHANGED")
    else:
        raise B4Invalid("N07_B4_GOLDEN_GATE_CHANGED")
    return {"status": "rejected_all", "mutations": rejected,
            "production_golden_unreviewed": True, "verified_n07_assertions": []}
