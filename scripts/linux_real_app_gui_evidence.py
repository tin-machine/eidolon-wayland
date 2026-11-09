"""Relational validation of synthetic Mousepad GUI observations.

The observer only reads GTK widget properties. Actions must have separate
Wayland seat evidence and a live positive control. Persistent settings are
read from the private keyfile; neither an API probe nor window geometry can
stand in for the line-number GUI experiment. No text or pixels enter here.
"""
from __future__ import annotations

import hashlib

import linux_real_app_evidence as lifecycle
from linux_real_app_build import require

INVALID = lifecycle.INVALID
PROTECTED_VALUE_DIGESTS = {
    "opening-mode": hashlib.sha256(b"tab").hexdigest(),
    "session-restore": hashlib.sha256(b"never").hexdigest(),
}
BASE_FIELDS = frozenset({
    "run_id", "main", "prefs_window", "parent", "frame", "observer_sequence",
    "input_delivered", "pointer_buttons",
})
VIEW_FIELDS = BASE_FIELDS | {"line_numbers", "gui_checked", "saved_present", "saved_value"}
COMBO_FIELDS = BASE_FIELDS | {"present", "mapped", "sensitive", "expected_value", "value_sha256"}
TOGGLE_FIELDS = BASE_FIELDS | {"active", "sensitive", "mapped"}
INPUT_FIELDS = frozenset({
    "window", "key_before", "key_after", "pointer_before", "pointer_after",
    "keys_pressed", "pointer_pressed", "target_observed",
})
READY_FIELDS = frozenset({
    "window", "frame", "app_id", "client_count", "observed_mapped_count", "input_delivered", "pressed",
})


def _base(value, fields, run):
    lifecycle._object(value, fields)
    require(value["run_id"] == run["run_id"] and value["main"] == run["initial_main"]["window"]
            and lifecycle._window(value["prefs_window"]) and value["prefs_window"] != value["main"]
            and value["parent"] == value["main"] and lifecycle._window(value["frame"])
            and lifecycle._count(value["observer_sequence"], minimum=1)
            and lifecycle._count(value["input_delivered"]) and lifecycle._count(value["pointer_buttons"]), INVALID)


def _view(value, run, *, enabled, saved):
    _base(value, VIEW_FIELDS, run)
    require(value["line_numbers"] is enabled and value["gui_checked"] is enabled
            and value["saved_present"] is saved
            and (value["saved_value"] is enabled if saved else value["saved_value"] is None), INVALID)


def _progress(before, after, *, allow_equal=False):
    """A new observer request is required, with no input or frame rollback."""
    require(all(before[key] == after[key] for key in ("run_id", "main", "prefs_window", "parent"))
            and after["observer_sequence"] >= before["observer_sequence"] + (0 if allow_equal else 1)
            and int(after["frame"]) >= int(before["frame"])
            and after["input_delivered"] >= before["input_delivered"]
            and after["pointer_buttons"] >= before["pointer_buttons"], INVALID)
    if after["observer_sequence"] == before["observer_sequence"]:
        require(all(before[key] == after[key]
                    for key in ("frame", "input_delivered", "pointer_buttons")), INVALID)


def _input(value, before, after):
    lifecycle._object(value, INPUT_FIELDS)
    require(value["window"] == before["prefs_window"] == after["prefs_window"]
            and value["target_observed"] is True
            and all(lifecycle._count(value[key]) for key in
                    ("key_before", "key_after", "pointer_before", "pointer_after", "keys_pressed", "pointer_pressed"))
            and value["keys_pressed"] == 0 and value["pointer_pressed"] == 0
            and value["key_before"] == before["input_delivered"]
            and value["key_after"] == after["input_delivered"]
            and value["key_after"] >= value["key_before"]
            and value["pointer_before"] == before["pointer_buttons"]
            and value["pointer_after"] == after["pointer_buttons"]
            and value["pointer_after"] >= value["pointer_before"] + 2, INVALID)


def _combo(value, run, name):
    _base(value, COMBO_FIELDS, run)
    require(value["present"] is True and value["mapped"] is True
            and value["sensitive"] is False and value["expected_value"] is True
            and value["value_sha256"] == PROTECTED_VALUE_DIGESTS[name], INVALID)


def _toggle(value, run):
    _base(value, TOGGLE_FIELDS, run)
    require(type(value["active"]) is bool and value["sensitive"] is True and value["mapped"] is True, INVALID)


def _trial(value, run, name):
    lifecycle._object(value, {"before", "after", "input", "positive_before", "positive_changed",
                              "positive_restored", "positive_input", "restore_input"})
    before, changed, restored = (value[key] for key in ("positive_before", "positive_changed", "positive_restored"))
    for point in (before, changed, restored):
        _toggle(point, run)
    _progress(before, changed)
    _progress(changed, restored)
    require(changed["active"] is not before["active"] and restored["active"] is before["active"], INVALID)
    _input(value["positive_input"], before, changed)
    _input(value["restore_input"], changed, restored)
    negative_before, negative_after = value["before"], value["after"]
    _combo(negative_before, run, name)
    _combo(negative_after, run, name)
    _progress(restored, negative_before, allow_equal=True)
    _progress(negative_before, negative_after)
    require(negative_before["value_sha256"] == negative_after["value_sha256"], INVALID)
    _input(value["input"], negative_before, negative_after)


def _ready(value, run):
    lifecycle._object(value, READY_FIELDS)
    require(value["window"] == run["initial_main"]["window"] and value["app_id"] == "mousepad"
            and lifecycle._window(value["frame"]) and type(value["client_count"]) is int
            and value["client_count"] == 1 and lifecycle._count(value["observed_mapped_count"], minimum=1)
            and lifecycle._count(value["input_delivered"])
            and type(value["pressed"]) is int and value["pressed"] == 0, INVALID)


def _open_tab(value, run, last_trial):
    lifecycle._object(value, {"run_id", "main", "before_tabs", "after_tabs", "before", "after",
                              "observer_before", "observer_after", "open_dialog", "parent"})
    require(value["run_id"] == run["run_id"] and value["main"] == run["initial_main"]["window"]
            and lifecycle._count(value["before_tabs"], minimum=1) and value["before_tabs"] < 32
            and type(value["after_tabs"]) is int and value["after_tabs"] == value["before_tabs"] + 1
            and lifecycle._count(value["observer_before"], minimum=1)
            and lifecycle._count(value["observer_after"], minimum=1)
            and value["observer_before"] > last_trial["observer_sequence"]
            and value["observer_after"] > value["observer_before"]
            and lifecycle._window(value["open_dialog"])
            and value["open_dialog"] not in (value["main"], last_trial["prefs_window"])
            and value["parent"] == value["main"], INVALID)
    before, after = value["before"], value["after"]
    _ready(before, run)
    _ready(after, run)
    require(before["input_delivered"] >= last_trial["input_delivered"]
            and after["input_delivered"] > before["input_delivered"]
            and after["observed_mapped_count"] > before["observed_mapped_count"]
            and int(after["frame"]) > int(before["frame"]), INVALID)


def _prior_runs(source):
    value = source.get("lifecycle")
    require(type(value) is dict and type(value.get("cases")) is dict
            and type(value.get("recovery")) is dict and type(value.get("independence")) is dict, INVALID)
    result = []
    for case in value["cases"].values():
        require(type(case) is dict and type(case.get("first")) is dict and type(case.get("restart")) is dict, INVALID)
        result.extend((case["first"], case["restart"]))
    for case in value["recovery"].values():
        require(type(case) is dict and type(case.get("run")) is dict, INVALID)
        result.append(case["run"])
    final = value["independence"].get("precursor_final")
    require(type(final) is dict, INVALID)
    result.append(final)
    return result


def validate_gui(value, source, lifecycle_value=None):
    """Require GUI change/restart/isolation and both real Preferences trials."""
    lifecycle._object(value, {"schema", "observer", "persistence", "protected"})
    require(type(value["schema"]) is int and value["schema"] == 1, INVALID)
    observer = value["observer"]
    lifecycle._object(observer, {"kind", "module_sha256", "source_sha256"})
    require(observer["kind"] == "gtk3-readonly-v1"
            and lifecycle._hex(observer["module_sha256"], 64)
            and lifecycle._hex(observer["source_sha256"], 64), INVALID)

    persistence = value["persistence"]
    lifecycle._object(persistence, {"first", "restart", "isolation", "initial", "changed", "restarted",
                                    "isolated", "change_input"})
    first, restart, isolation = (persistence[key] for key in ("first", "restart", "isolation"))
    for run in (first, restart, isolation):
        lifecycle._run(run, ending="close", source=source)
    require(first["profile_id"] == restart["profile_id"]
            and first["profile_id"] != isolation["profile_id"]
            and first["profile_after"] == restart["profile_before"]
            and first["profile_after"]["documents_sha256"] == restart["profile_after"]["documents_sha256"]
            and first["profile_before"]["settings_present"] is False
            and first["profile_before"]["preferences_count"] == 0
            and isolation["profile_before"]["settings_present"] is False
            and isolation["profile_before"]["preferences_count"] == 0, INVALID)
    prior = _prior_runs(source if lifecycle_value is None else {"lifecycle": lifecycle_value})
    for key in ("run_id", "owner_scope_id", "runtime_id"):
        identities = [run[key] for run in (*prior, first, restart, isolation)]
        require(len(set(identities)) == len(identities), INVALID)
    require(all(first["profile_id"] != run["profile_id"] and isolation["profile_id"] != run["profile_id"]
                for run in prior), INVALID)

    initial, changed, restarted, isolated = (persistence[key] for key in ("initial", "changed", "restarted", "isolated"))
    _view(initial, first, enabled=False, saved=False)
    _view(changed, first, enabled=True, saved=True)
    _view(restarted, restart, enabled=True, saved=True)
    _view(isolated, isolation, enabled=False, saved=False)
    _progress(initial, changed)
    _input(persistence["change_input"], initial, changed)

    protected = value["protected"]
    lifecycle._object(protected, {"run_id", "main", "prefs_window", "parent", "positive_before",
                                  "positive_after", "positive_input", "widgets", "open_tab"})
    require(all(protected[key] == initial[key] for key in ("run_id", "main", "prefs_window", "parent"))
            and protected["positive_before"] == initial and protected["positive_after"] == changed
            and protected["positive_input"] == persistence["change_input"], INVALID)
    widgets = protected["widgets"]
    lifecycle._object(widgets, {"opening-mode", "session-restore"})
    previous = changed
    for name in ("opening-mode", "session-restore"):
        trial = widgets[name]
        _trial(trial, first, name)
        _progress(previous, trial["positive_before"])
        previous = trial["after"]
    _open_tab(protected["open_tab"], first, previous)
    return value
