"""Versioned, read-only N07 fixture settings oracle; never Golden/Acceptance.

v1's exact whole-keyfile oracle is unchanged. This opt-in v2 profile starts
with an absent keyfile in newly provisioned C1 storage. It admits only the
target boolean and the exact Mousepad/GTK automatic UI-state schemas below.
Unknown preferences never enter the exported bytes. See the design record
for pinned upstream schemas, lifecycle causes and the geometry limitation.
"""
from __future__ import annotations

import base64
import binascii
import json
import re

import linux_real_app_v3_n07_settings as legacy

REVISION = "eidolon-i6-n07-settings-lifecycle-v2"
SNAPSHOT_REVISION = "eidolon-i6-n07-keyfile-snapshot-v2"
WINDOW = "org/xfce/mousepad/state/window"
CHOOSER = "org/gtk/settings/file-chooser"
WINDOW_KEYS = frozenset(("width", "height", "maximized", "fullscreen"))
CHOOSER_LITERALS = {
    "date-format": "'regular'", "location-mode": "'filename-entry'",
    "show-hidden": "false", "show-size-column": "true", "show-type-column": "true",
    "sort-column": "'name'", "sort-directories-first": "false",
    "sort-order": "'ascending'", "type-format": "'category'",
}
CHOOSER_KEYS = frozenset((*CHOOSER_LITERALS, "sidebar-width", "window-position", "window-size"))
PHASES = ("first_before_launch", "first_after_toggle", "first_retired", "second_before_launch",
          "second_before_open", "second_after_observation", "second_retired")
MAX_BYTES = 4096
SOURCE = "same_snapshot_allowlisted_keyfile_bytes"
need = legacy.need


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _integer(value, low, high):
    need(type(value) is str and re.fullmatch(r"0|-?[1-9][0-9]{0,9}", value) is not None,
         "N07_SETTINGS_VALUE_TYPE")
    number = int(value)
    need(low <= number <= high, "N07_SETTINGS_VALUE_RANGE")
    return number


def _pair(value, positive=False):
    need(type(value) is str and re.fullmatch(r"\((0|-?[1-9][0-9]{0,9}), (0|-?[1-9][0-9]{0,9})\)", value)
         is not None, "N07_SETTINGS_VALUE_TYPE")
    return tuple(_integer(v, 1 if positive else -(1 << 31), (1 << 31) - 1)
                 for v in value[1:-1].split(", "))


def parse_safe(raw):
    """Reject arbitrary text before it can be exported; return lexical values.

    GLib's canonical section/key grammar excludes comments, empty sections,
    escapes, duplicate keys/sections, continuation lines and whitespace-only
    rewrites. Ordering is preserved separately, not sorted away as evidence.
    """
    need(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, "N07_SETTINGS_BYTES_BOUNDS")
    try:
        text = raw.decode("ascii")
    except UnicodeError as error:
        raise legacy.KeyfileEvidenceError("N07_SETTINGS_SYNTAX") from error
    need(text.endswith("\n") and not text.endswith("\n\n"), "N07_SETTINGS_SYNTAX")
    entries, order = {}, []
    for block in text[:-1].split("\n\n"):
        lines = block.split("\n")
        need(len(lines) >= 2 and lines[0].startswith("[") and lines[0].endswith("]"),
             "N07_SETTINGS_SYNTAX")
        section = lines[0][1:-1]
        need(section in (legacy.SECTION, WINDOW, CHOOSER), "N07_SETTINGS_UNKNOWN_SECTION")
        need(section not in entries, "N07_SETTINGS_DUPLICATE")
        values = entries[section] = {}
        allowed = {legacy.SECTION: {legacy.KEY}, WINDOW: WINDOW_KEYS, CHOOSER: CHOOSER_KEYS}[section]
        for line in lines[1:]:
            need(line.count("=") == 1, "N07_SETTINGS_SYNTAX")
            key, value = line.split("=", 1)
            need(key in allowed, "N07_SETTINGS_UNKNOWN_KEY")
            need(key not in values, "N07_SETTINGS_DUPLICATE")
            if section == legacy.SECTION:
                need(value in ("true", "false"), "N07_KEYFILE_TYPE")
            elif section == WINDOW:
                if key in ("maximized", "fullscreen"):
                    # An expectation of this non-maximizing fixture, not an
                    # observation of GTK's otherwise unexposed window flags.
                    need(value == "false", "N07_SETTINGS_WINDOW_POLICY")
                else:
                    _integer(value, 1, (1 << 32) - 1)
            elif key in CHOOSER_LITERALS:
                # Pinned GTK defaults, except location-mode which the actual
                # admitted Ctrl+L changes to filename-entry in this profile.
                need(value == CHOOSER_LITERALS[key], "N07_SETTINGS_CHOOSER_POLICY")
            elif key == "sidebar-width":
                _integer(value, 1, (1 << 31) - 1)
            else:
                _pair(value, positive=key == "window-size")
            values[key] = value
            order.append((section, key, value))
    return entries, order


def observe_snapshot(proof, raw):
    """Called only with the exact bytes from the already pinned FD read."""
    legacy.validate_snapshot(proof)
    if proof["file"] is None:
        need(raw is None, "N07_SETTINGS_SNAPSHOT_BINDING")
        encoded = None
    else:
        parse_safe(raw)  # Never publish unrecognized/private setting values.
        need(len(raw) == proof["file"]["size"] and legacy.sha(raw) == proof["file"]["sha256"],
             "N07_SETTINGS_SNAPSHOT_BINDING")
        encoded = base64.b64encode(raw).decode("ascii")
    return {"schema": 1, "revision": SNAPSHOT_REVISION, "source": SOURCE,
            "proof_sha256": legacy.sha(canonical(proof)), "bytes_b64": encoded}


def validate_snapshot(proof, observation):
    legacy.validate_snapshot(proof)
    need(type(observation) is dict and set(observation) == {
        "schema", "revision", "source", "proof_sha256", "bytes_b64"}, "N07_SETTINGS_SNAPSHOT_SHAPE")
    need(type(observation["schema"]) is int and observation["schema"] == 1
         and observation["revision"] == SNAPSHOT_REVISION and observation["source"] == SOURCE
         and observation["proof_sha256"] == legacy.sha(canonical(proof)), "N07_SETTINGS_SNAPSHOT_BINDING")
    encoded = observation["bytes_b64"]
    if proof["file"] is None:
        need(encoded is None and proof["others_sha256"] == legacy.sha(b"{}"),
             "N07_SETTINGS_SNAPSHOT_BINDING")
        return {}, [], None
    need(type(encoded) is str and 0 < len(encoded) <= 4 * ((MAX_BYTES + 2) // 3),
         "N07_SETTINGS_BYTES_BOUNDS")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise legacy.KeyfileEvidenceError("N07_SETTINGS_BYTES_ENCODING") from error
    need(base64.b64encode(raw).decode("ascii") == encoded
         and len(raw) == proof["file"]["size"] and legacy.sha(raw) == proof["file"]["sha256"],
         "N07_SETTINGS_SNAPSHOT_BINDING")
    entries, order = parse_safe(raw)
    present, value, others = legacy._parse(raw)
    need(present == proof["key_present"] and value is proof["value"]
         and others == proof["others_sha256"], "N07_SETTINGS_SNAPSHOT_BINDING")
    return entries, order, raw


def _geometry(entries, limits):
    """Bounds only: GTK excludes CSD/titlebar allocation from saved sizes.

    The source-bound native validator supplies real logical geometry. No
    historical dimensions or guessed decoration offset are used here.
    """
    for section, label in ((WINDOW, "main"), (CHOOSER, "chooser")):
        if section not in entries:
            continue
        size = limits[label]
        need(type(size) is dict and set(size) == {"width", "height"}
             and all(type(size[k]) is int and 0 < size[k] <= 65536 for k in size),
             "N07_SETTINGS_GEOMETRY")
        values = entries[section]
        if section == WINDOW:
            for key in ("width", "height"):
                if key in values:
                    need(int(values[key]) <= size[key], "N07_SETTINGS_GEOMETRY")
        else:
            width, height = _pair(values["window-size"], positive=True)
            need(width <= size["width"] and height <= size["height"]
                 and int(values["sidebar-width"]) <= width, "N07_SETTINGS_GEOMETRY")


def validate_sequence(proofs, observations, *, complete=True, geometries=None):
    """Recompute a contiguous live prefix or the complete seven-point sequence.

    Acquisition barriers and ordinary Pointer/Open/cleanup causality are
    checked by the independent B4 product validator, not by this file oracle.
    """
    need(type(complete) is bool and type(proofs) is dict and type(observations) is dict,
         "N07_SETTINGS_SEQUENCE_SHAPE")
    phases = PHASES if complete else PHASES[:len(proofs)]
    need(phases and set(proofs) == set(observations) == set(phases), "N07_SETTINGS_PHASE_MISSING")
    if geometries is not None:
        need(type(geometries) is dict and set(geometries) == {"first", "second"}
             and all(type(v) is dict and set(v) == {"main", "chooser"} for v in geometries.values()),
             "N07_SETTINGS_GEOMETRY")
    previous, previous_order, previous_raw = {}, [], None
    baseline, owner, snapshots, transitions = None, None, {}, []
    for index, phase in enumerate(phases):
        proof = proofs[phase]
        entries, order, raw = validate_snapshot(proof, observations[phase])
        identity = (proof["storage_digest"], proof["root_dev"], proof["root_ino"])
        if baseline is None:
            baseline = identity
        need(identity == baseline, "N07_DIFFERENT_STORAGE_ROOT")
        if index == 0:
            need(proof["key_present"] is False and proof["value"] is None,
                 "N07_INITIAL_KEY_PRESENT")
            need(entries == {} and raw is None, "N07_SETTINGS_INITIAL_NOT_EMPTY")
        else:
            need(proof["key_present"] is True and proof["value"] is True,
                 "N07_KEY_NOT_PERSISTED")
            uid = proof["file"]["uid"]
            if owner is None:
                owner = uid
            need(uid == owner, "N07_SETTINGS_OWNER_CHANGED")
            if phase in ("first_after_toggle", "first_retired", "second_before_launch", "second_before_open"):
                need(CHOOSER not in entries, "N07_SETTINGS_CHOOSER_BEFORE_OPEN")
            if phase != "first_after_toggle":
                need(set(entries.get(WINDOW, {})) == WINDOW_KEYS, "N07_SETTINGS_WINDOW_INCOMPLETE")
            if phase in ("second_after_observation", "second_retired"):
                need(set(entries.get(CHOOSER, {})) == CHOOSER_KEYS, "N07_SETTINGS_CHOOSER_INCOMPLETE")
            old = {(s, k): v for s, values in previous.items() for k, v in values.items()}
            new = {(s, k): v for s, values in entries.items() for k, v in values.items()}
            need(all(key in new and new[key] == value for key, value in old.items()),
                 "N07_UNEXPECTED_SETTING_CHANGE")
            need([item for item in order if item[:2] in old] == previous_order,
                 "N07_SETTINGS_SERIALIZATION_CHANGED")
            added = sorted(set(new) - set(old))
            if phase in ("second_before_launch", "second_before_open", "second_retired") or not added:
                need(raw == previous_raw, "N07_RESTART_KEYFILE_CHANGED")
            transitions.append({"phase": phase, "added": [[s, k] for s, k in added],
                                "bytes_unchanged": raw == previous_raw})
            if geometries is not None:
                _geometry(entries, geometries["first" if phase.startswith("first_") else "second"])
        snapshots[phase] = {"proof_sha256": legacy.sha(canonical(proof)),
                            "observation_sha256": legacy.sha(canonical(observations[phase])),
                            "entries_sha256": legacy.sha(canonical(entries)),
                            "file_sha256": None if raw is None else legacy.sha(raw),
                            "file_size": None if raw is None else len(raw)}
        previous, previous_order, previous_raw = entries, order, raw
    return {"revision": REVISION, "complete": complete, "snapshots": snapshots,
            "transitions": transitions, "geometry_bounds_verified": geometries is not None,
            "window_flags_source": "controlled_fixture_expectation",
            "geometry_relation": "gtk_allocation_bounded_by_real_logical_size",
            "host_v3_acceptance_passed": False, "verified_n07_assertions": []}
