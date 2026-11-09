"""Relational receipt validation for the synthetic A0 restart/instance cases.

This validates measured evidence, not just the summary booleans. A per-run
Close result cannot certify a restart, Stop cannot stand in for an app crash,
and three copies of a precursor snapshot cannot certify responsiveness.
"""
from __future__ import annotations

import re

from linux_real_app_build import require

INVALID = "APP_WAYLAND_RECEIPT_INVALID"
PENDING = ("gui_setting_persistence", "protected_settings_gui_readonly")
PRECURSOR_HOLD_MS = 125_000
RUN_FIELDS = frozenset({
    "state", "status", "error", "run_id", "owner_scope_id", "profile_id", "runtime_id",
    "termination_kind", "termination", "initial_main", "profile_before", "profile_after", "documents_before_termination",
    "app_spawned", "cleanup", "recovery_guard", "mousepad_sha256", "waypipe_sha256", "waypipe_version", "final_graph",
})
PROFILE_FIELDS = frozenset({
    "documents_sha256", "settings_sha256", "settings_present", "preferences_sha256",
    "preferences_count", "tree_sha256",
})
INITIAL_FIELDS = frozenset({
    "window", "frame", "app_id", "client_count", "prelaunch_client_count", "observed_mapped_count", "input_delivered", "pressed",
})
OBSERVATION_FIELDS = frozenset({
    "run_id", "owner_scope_id", "profile_id", "runtime_id", "main", "app_id", "client_count",
    "topology_sha256", "profile", "response",
})
RESPONSE_FIELDS = frozenset({
    "responsive", "main", "frame", "input_delivered_before", "input_delivered_after",
    "window_count", "client_count", "pressed", "topology_preserved", "dialog_window",
})


def _object(value, fields):
    require(type(value) is dict and set(value) == fields, INVALID)


def _hex(value, size):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{" + str(size) + r"}", value) is not None


def _count(value, *, minimum=0):
    return type(value) is int and minimum <= value <= 18446744073709551615


def _window(value):
    return (type(value) is str and re.fullmatch(r"[1-9][0-9]{0,18}", value) is not None
            and int(value) <= 9223372036854775807)


def validate_cleanup(value):
    require(type(value) is dict and type(value.get("schema")) is int and value["schema"] == 1
            and value.get("complete") is True and value.get("pending") is False
            and value.get("reason") == "COMPLETED" and type(value.get("open_pidfds")) is int
            and value["open_pidfds"] == 0 and value.get("root_pidfds_closed") is True
            and value.get("output_pipes_eof") is True
            and all(_count(value.get(key)) for key in ("term_signals", "kill_signals", "reaped")),
            "APP_WAYLAND_CLEANUP_PENDING")


def _profile(value):
    _object(value, PROFILE_FIELDS)
    require(all(_hex(value[key], 64) for key in ("documents_sha256", "tree_sha256"))
            and type(value["settings_present"]) is bool
            and (value["settings_sha256"] is None if not value["settings_present"]
                 else _hex(value["settings_sha256"], 64))
            and _count(value["preferences_count"])
            and (value["preferences_sha256"] is None if value["preferences_count"] == 0
                 else value["settings_present"] and _hex(value["preferences_sha256"], 64)), INVALID)


def _initial(value):
    _object(value, INITIAL_FIELDS)
    require(_window(value["window"]) and _window(value["frame"]) and value["app_id"] == "mousepad"
            and type(value["client_count"]) is int and value["client_count"] == 1
            and type(value["prelaunch_client_count"]) is int and value["prelaunch_client_count"] == 0
            and type(value["observed_mapped_count"]) is int and value["observed_mapped_count"] == 1
            and _count(value["input_delivered"]) and type(value["pressed"]) is int
            and value["pressed"] == 0, INVALID)


def _run(value, *, ending, source, refused=False):
    _object(value, RUN_FIELDS)
    require(all(_hex(value[key], 32) for key in ("run_id", "owner_scope_id", "profile_id", "runtime_id"))
            and value["termination_kind"] == ending, INVALID)
    validate_cleanup(value["cleanup"])
    _profile(value["profile_before"])
    _profile(value["profile_after"])
    guard = value["recovery_guard"]
    _object(guard, {"clear", "reason", "session_empty", "recovery_data_empty", "profile_lock_held"})
    require(guard["profile_lock_held"] is True and type(guard["session_empty"]) is bool
            and type(guard["recovery_data_empty"]) is bool, INVALID)
    termination = value["termination"]
    _object(termination, {"kind", "observed", "app_pidfd_exit", "server_returncode", "forced_scope_cleanup"})
    require(termination["kind"] == ending, INVALID)
    if refused:
        require(value["state"] == "fail" and value["status"] == "failed"
                and value["error"] == "APP_STATE_RECOVERY_REQUIRED" and value["app_spawned"] is False
                and value["initial_main"] is None and guard["clear"] is False
                and guard["reason"] == "APP_STATE_RECOVERY_REQUIRED"
                and value["profile_before"] == value["profile_after"]
                and value["documents_before_termination"] is None
                and termination["observed"] is False and termination["app_pidfd_exit"] is None
                and termination["server_returncode"] is None and termination["forced_scope_cleanup"] is False
                and value["final_graph"] is None,
                INVALID)
    else:
        require(value["state"] == "partial" and value["status"] == "incomplete" and value["error"] is None
                and value["app_spawned"] is True and guard["clear"] is True
                and guard["reason"] == "NONE" and guard["session_empty"] is True
                and guard["recovery_data_empty"] is True and termination["observed"] is True, INVALID)
        _initial(value["initial_main"])
        require(_hex(value["documents_before_termination"], 64)
                and value["documents_before_termination"] == value["profile_after"]["documents_sha256"], INVALID)
        if ending == "close":
            require(type(termination["server_returncode"]) is int and termination["server_returncode"] == 0
                    and termination["forced_scope_cleanup"] is False
                    and termination["app_pidfd_exit"] is None, INVALID)
        elif ending == "stop":
            require(termination["forced_scope_cleanup"] is True
                    and termination["app_pidfd_exit"] is None and termination["server_returncode"] is None
                    and value["final_graph"] is None, INVALID)
        else:
            require(ending == "crash" and termination["app_pidfd_exit"] is True
                    and type(termination["server_returncode"]) is int
                    and termination["server_returncode"] != 0
                    and termination["forced_scope_cleanup"] is False, INVALID)
        if ending != "stop":
            graph = value["final_graph"]
            _object(graph, {"window_count", "client_count", "pressed", "focus_window"})
            require(all(type(graph[key]) is int and graph[key] == 0
                        for key in ("window_count", "client_count", "pressed"))
                    and graph["focus_window"] is None, INVALID)
    # Refused launches may have validated hashes before their pre-spawn guard,
    # but must not invent absent values. Every executing run must match the
    # static inspection that brackets the entire experiment.
    for key in ("mousepad_sha256", "waypipe_sha256"):
        require((refused and value[key] is None) or
                (_hex(value[key], 64) and value[key] == source.get(key)), INVALID)
    require(value["waypipe_version"] == "0.11.3" or refused and value["waypipe_version"] is None, INVALID)


def _observation(value):
    _object(value, OBSERVATION_FIELDS)
    require(all(_hex(value[key], 32) for key in ("run_id", "owner_scope_id", "profile_id", "runtime_id"))
            and _window(value["main"]) and value["app_id"] == "mousepad"
            and type(value["client_count"]) is int and value["client_count"] == 1
            and _hex(value["topology_sha256"], 64), INVALID)
    _profile(value["profile"])
    # The pinned unmodified Mousepad saves four ordinary geometry keys after
    # its configure timer. An empty startup baseline cannot certify isolation.
    require(value["profile"]["settings_present"] is True
            and value["profile"]["preferences_count"] >= 4, INVALID)
    response = value["response"]
    _object(response, RESPONSE_FIELDS)
    require(response["responsive"] is True and response["topology_preserved"] is True
            and response["main"] == value["main"] and _window(response["frame"])
            and _window(response["dialog_window"]) and response["dialog_window"] != value["main"]
            and type(response["window_count"]) is int and response["window_count"] == 1
            and type(response["client_count"]) is int and response["client_count"] == 1
            and type(response["pressed"]) is int and response["pressed"] == 0
            and _count(response["input_delivered_before"]) and _count(response["input_delivered_after"])
            and response["input_delivered_after"] > response["input_delivered_before"], INVALID)


def validate_lifecycle(value, source, *, require_precursor_hold=False):
    """Require all three measured endings and three independent refusal seeds."""
    _object(value, {"schema", "cases", "recovery", "independence"})
    require(type(value["schema"]) is int and value["schema"] == 1, INVALID)
    cases = value["cases"]
    _object(cases, {"close_restart", "stop_restart", "crash_restart"})
    runs = []
    profiles = []
    for ending in ("close", "stop", "crash"):
        case = cases[ending + "_restart"]
        _object(case, {"first", "restart", "outcome", "documents_preserved", "settings_preserved"})
        require(case["outcome"] in (("restarted",) if ending == "close" else ("restarted", "recovery_required"))
                and case["documents_preserved"] is True, INVALID)
        first, restart = case["first"], case["restart"]
        _run(first, ending=ending, source=source)
        _run(restart, ending="close", source=source, refused=case["outcome"] == "recovery_required")
        require(first["profile_id"] == restart["profile_id"]
                and first["profile_after"] == restart["profile_before"]
                and first["profile_after"]["documents_sha256"] == restart["profile_after"]["documents_sha256"], INVALID)
        if ending == "close":
            previous = first["profile_after"]
            require(case["settings_preserved"] is True and previous["preferences_count"] > 0
                    and previous["preferences_count"] == restart["profile_after"]["preferences_count"]
                    and previous["preferences_sha256"] == restart["profile_after"]["preferences_sha256"], INVALID)
        else:
            # Forced termination never certifies a normal settings flush.
            require(case["settings_preserved"] is None, INVALID)
        runs.extend((first, restart))
        profiles.append(first["profile_id"])
    recovery = value["recovery"]
    _object(recovery, {"session", "autosave", "ambiguous"})
    for kind, case in recovery.items():
        _object(case, {"run", "profile_preserved"})
        require(case["profile_preserved"] is True, INVALID)
        run = case["run"]
        _run(run, ending="close", source=source, refused=True)
        if kind in ("session", "ambiguous"):
            require(run["recovery_guard"]["session_empty"] is False, INVALID)
        else:
            require(run["recovery_guard"]["recovery_data_empty"] is False, INVALID)
        runs.append(run)
        profiles.append(run["profile_id"])
    independent = value["independence"]
    _object(independent, {"precursor_worker_id", "target_worker_id", "precursor_initial",
                          "after_close", "after_stop", "precursor_final", "hold_probe"})
    require(_hex(independent["precursor_worker_id"], 32) and _hex(independent["target_worker_id"], 32)
            and independent["precursor_worker_id"] != independent["target_worker_id"], INVALID)
    require(type(require_precursor_hold) is bool, INVALID)
    hold = independent["hold_probe"]
    require(not require_precursor_hold or hold is not None, INVALID)
    observations = [independent["precursor_initial"]]
    if hold is not None:
        _object(hold, {"elapsed_ms", "observation"})
        require(_count(hold["elapsed_ms"], minimum=PRECURSOR_HOLD_MS)
                and hold["elapsed_ms"] < 600_000, INVALID)
        observations.append(hold["observation"])
    observations.extend(independent[key] for key in ("after_close", "after_stop"))
    for observation in observations:
        _observation(observation)
    original = observations[0]
    stable = ("run_id", "owner_scope_id", "profile_id", "runtime_id", "main", "app_id", "client_count", "topology_sha256")
    for earlier, later in zip(observations, observations[1:]):
        require(all(earlier[key] == later[key] for key in stable)
                and all(earlier["profile"][key] == later["profile"][key]
                        for key in ("documents_sha256", "settings_sha256", "preferences_sha256", "preferences_count"))
                and later["response"]["input_delivered_before"] >= earlier["response"]["input_delivered_after"]
                and later["response"]["dialog_window"] != earlier["response"]["dialog_window"], INVALID)
    final = independent["precursor_final"]
    _run(final, ending="close", source=source)
    require(all(final[key] == original[key] for key in ("run_id", "owner_scope_id", "profile_id", "runtime_id"))
            and final["initial_main"]["window"] == original["main"]
            and final["profile_after"]["documents_sha256"] == original["profile"]["documents_sha256"], INVALID)
    runs.append(final)
    profiles.append(final["profile_id"])
    # WindowIds may be numerically equal on distinct compositors. Use fresh
    # run/owner/runtime identities, never WindowId inequality, for new runs.
    for key in ("run_id", "owner_scope_id", "runtime_id"):
        require(len({run[key] for run in runs}) == len(runs), INVALID)
    require(len(set(profiles)) == len(profiles), INVALID)
    return value
