"""N07-B3 reproducible, unapproved review packets from saved Host receipts.

This is a diagnostic export, never a production golden generator or approval
API. The packet digest identifies the exact proposal a reviewer saw. Possessing
that digest is NOT evidence of independent review. The production six-state
loader and its unset approval pin are deliberately untouched.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import html
import json
import os
from pathlib import Path
import re
import stat
import struct
import zlib

import linux_real_app_v3_evidence as evidence
import linux_real_app_v3_n07_pixel as pixel
from linux_real_app_v3_n07_b2_probe import inspect as inspect_b2, _backend_identity

REVISION = "eidolon-i6-n07-review-candidate-v1"
STATES = pixel.STATES[:4]
MISSING_STATES = pixel.STATES[4:]
MAX_INPUT_BYTES = 4 * 1024 * 1024
REPOSITORY = "tin-machine/eidolon-wayland"
INPUT_NAMES = {
    "native": "native.json", "environment": "environment.json",
    "validation": "validation.json", "provenance": "provenance.json",
    "fingerprint": "fingerprint.json", "proposal": "proposal.json",
}
CONTEXT_STATES = ("first_off_context", "first_off_menu_context",
                  "first_on_context", "first_on_menu_context")
CHECKLIST = (
    "Compare the complete View menu label with the pinned Mousepad menus.ui; "
    "Line N_umbers must map to app.preferences.view.show-line-numbers.",
    "Identify the exact unchecked/checked Line Numbers row; exclude adjacent "
    "Menubar, Toolbar and Statusbar checkmarks.",
    "Locate actual numeral glyphs in the gutter. A blank crop, background "
    "change or border movement does not prove line numbers.",
    "Exclude hover/selection highlights, caret, shadows and background-only "
    "changes from the proposed mask and negative contrast.",
    "Match popup parent, surface, ACK/grab serial, composition layer, offset, "
    "scale and content viewport; ROI coordinates use the target top-left.",
    "Verify real font files/configuration, linked libraries, theme, locale, "
    "scale, renderer revision and Mesa backend against the collected fingerprint.",
    "Review exact ROI, binary mask, per-channel tolerance and negative "
    "thresholds. Full-ROI/exact-match defaults are provisional, not optimized.",
    "Collect both second-session states in an independent reference acquisition; "
    "never rename first-session images or use an acceptance run as its reference.",
    "Record actual reviewer, review commit and exact six-state fixture SHA in "
    "a separate approval change. This packet supplies no approval identity.",
)


class ReviewError(ValueError):
    """Bounded fixed reason; never echo an input path or arbitrary payload."""


def need(condition: bool, code: str) -> None:
    if not condition:
        raise ReviewError(code)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: object) -> bytes:
    return evidence.canonical(value)


def _hex(value: object, length: int) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{%d}" % length, value) is not None


def _uint(value: object, *, positive: bool = False) -> bool:
    return type(value) is int and (1 if positive else 0) <= value <= (1 << 63) - 1


def _decimal(value: object) -> bool:
    return (type(value) is str and re.fullmatch(r"[1-9][0-9]{0,18}", value) is not None
            and int(value) <= (1 << 63) - 1)


def _exact(value: object, fields: set[str], reason: str) -> None:
    need(type(value) is dict and set(value) == fields, reason)


def _parse(raw: bytes) -> dict:
    value = evidence.parse(raw, limit=MAX_INPUT_BYTES)
    need(type(value) is dict, "N07_REVIEW_JSON_OBJECT")
    return value


def _read(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
             and 0 < before.st_size <= MAX_INPUT_BYTES, "N07_REVIEW_INPUT_BOUNDS")
        chunks, remaining = [], MAX_INPUT_BYTES + 1
        while remaining:
            block = os.read(fd, min(65536, remaining))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        raw = b"".join(chunks)
        after, named = os.fstat(fd), path.stat(follow_symlinks=False)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        need(identity(before) == identity(after) == identity(named)
             and len(raw) == before.st_size, "N07_REVIEW_INPUT_RACE")
        return raw
    finally:
        os.close(fd)


def _write(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
    path.chmod(0o600)


def _point(value: object) -> None:
    _exact(value, {"x", "y"}, "N07_REVIEW_GEOMETRY")
    need(all(type(v) is int and -(1 << 31) <= v < (1 << 31)
             for v in value.values()), "N07_REVIEW_GEOMETRY")


def _size(value: object) -> None:
    _exact(value, {"width", "height"}, "N07_REVIEW_GEOMETRY")
    need(all(type(v) is int and 0 < v <= 16384 for v in value.values()),
         "N07_REVIEW_GEOMETRY")


def _geometry(value: object) -> None:
    _exact(value, {"origin", "size"}, "N07_REVIEW_GEOMETRY")
    _point(value["origin"])
    _size(value["size"])


def _readback(value: dict) -> None:
    """Recheck real ticket geometry as well as the B2 pixel/provenance gate."""
    _exact(value, {"source", "target", "create_seq", "sdk_adapter", "receipt", "pixels"},
           "N07_REVIEW_READBACK_SHAPE")
    r = value["receipt"]
    _exact(r, {"renderer_owner", "context_epoch", "request_seq", "target",
               "min_content_frame", "content_frame", "present_seq", "snapshot_id",
               "geometry", "logical_size", "layers", "content_rect", "roi_revision",
               "roi_sha256", "pixel_sha256", "backend", "rois", "runtime", "create"},
           "N07_REVIEW_RECEIPT_SHAPE")
    need(value["source"] == "host_renderer_readback"
         and all(_decimal(value[k]) for k in ("create_seq", "sdk_adapter"))
         and _decimal(r["runtime"]) and r["create"] == value["create_seq"]
         and r["target"] == value["target"], "N07_REVIEW_READBACK_IDENTITY")
    _exact(r["target"], {"session", "window", "scene", "generation"},
           "N07_REVIEW_TARGET")
    need(all(_decimal(v) for v in r["target"].values())
         and all(_uint(r[k], positive=True) for k in (
             "renderer_owner", "context_epoch", "request_seq", "present_seq"))
         and _decimal(r["content_frame"]) and _decimal(r["min_content_frame"])
         and int(r["content_frame"]) >= int(r["min_content_frame"])
         and type(r["roi_revision"]) is int and r["roi_revision"] == 1,
         "N07_REVIEW_READBACK_IDENTITY")
    expected_snapshot = ":".join((str(r["renderer_owner"]), r["target"]["session"],
                                  r["target"]["window"], r["content_frame"]))
    need(r["snapshot_id"] == expected_snapshot, "N07_REVIEW_SNAPSHOT_ID")
    backend = _backend_identity(r["backend"])
    _geometry(r["geometry"])
    _size(r["logical_size"])
    _exact(r["content_rect"], {"origin", "size"}, "N07_REVIEW_VIEWPORT")
    origin, size = r["content_rect"]["origin"], r["content_rect"]["size"]
    need(type(origin) is list and len(origin) == 2
         and all(type(v) is int and 0 <= v <= 16384 for v in origin)
         and type(size) is list and len(size) == 2
         and all(type(v) is int and 0 < v <= 16384 for v in size)
         and origin[0] + size[0] <= backend["width"]
         and origin[1] + size[1] <= backend["height"], "N07_REVIEW_VIEWPORT")
    layers = r["layers"]
    need(type(layers) is list and 0 < len(layers) <= 64, "N07_REVIEW_LAYERS")
    surfaces = set()
    for layer in layers:
        _exact(layer, {"surface", "content_seq", "buffer_size", "offset", "clip",
                       "scale", "transform"}, "N07_REVIEW_LAYER_SHAPE")
        need(_decimal(layer["surface"]) and layer["surface"] not in surfaces
             and _decimal(layer["content_seq"])
             and type(layer["scale"]) is int and 1 <= layer["scale"] <= 8
             and layer["transform"] in (
                 "Normal", "Rotate90", "Rotate180", "Rotate270", "Flipped",
                 "Flipped90", "Flipped180", "Flipped270"), "N07_REVIEW_LAYER")
        surfaces.add(layer["surface"])
        _size(layer["buffer_size"])
        _point(layer["offset"])
        _geometry(layer["clip"])
    pixels, rois, hashes = value["pixels"], r["rois"], r["pixel_sha256"]
    need(type(pixels) is list and 0 < len(pixels) <= 8
         and type(rois) is list and len(rois) == len(pixels)
         and type(hashes) is list and len(hashes) == len(pixels),
         "N07_REVIEW_PIXELS")
    raw_rois, ids, total = bytearray(struct.pack("<I", 1)), set(), 0
    for p, roi, digest in zip(pixels, rois, hashes):
        _exact(p, {"id", "rect", "rgba_b64", "sha256"}, "N07_REVIEW_PIXEL_SHAPE")
        pixel._rect(p["rect"])
        need(type(p["id"]) is int and 0 <= p["id"] < (1 << 32)
             and p["id"] not in ids, "N07_REVIEW_ROI_ID")
        ids.add(p["id"])
        rect = p["rect"]
        need(rect["x"] + rect["width"] <= backend["width"]
             and rect["y"] + rect["height"] <= backend["height"], "N07_REVIEW_ROI_BOUNDS")
        _exact(roi, {"roi"}, "N07_REVIEW_ROI_TICKET")
        expected_roi = {"id": p["id"], **rect}
        need(canonical(roi["roi"]) == canonical(expected_roi), "N07_REVIEW_ROI_TICKET")
        raw_rois.extend(struct.pack("<5I", *(expected_roi[k] for k in (
            "id", "x", "y", "width", "height"))))
        rgba = pixel._decode_b64(p["rgba_b64"], rect["width"] * rect["height"] * 4)
        total += len(rgba)
        need(_hex(digest, 64) and p["sha256"] == digest == sha(rgba),
             "N07_REVIEW_RGBA_SHA")
    need(total <= 1024 * 1024 and r["roi_sha256"] == sha(raw_rois),
         "N07_REVIEW_ROI_DIGEST")


def _provenance(value: dict, source: dict) -> None:
    _exact(value, {"repository", "workflow_run_id", "run_attempt", "artifact_id",
                   "artifact_name", "artifact_archive_sha256", "source_commit", "pr_head",
                   "artifact_status"},
           "N07_REVIEW_PROVENANCE_SHAPE")
    need(value["repository"] == REPOSITORY
         and (value["workflow_run_id"] is None or _decimal(value["workflow_run_id"]))
         and (value["run_attempt"] is None or _decimal(value["run_attempt"]))
         and (value["artifact_id"] is None or _decimal(value["artifact_id"]))
         and type(value["artifact_name"]) is str
         and re.fullmatch(r"[A-Za-z0-9_.-]{1,180}", value["artifact_name"]) is not None
         and (value["artifact_archive_sha256"] is None
              or _hex(value["artifact_archive_sha256"], 64))
         and _hex(value["source_commit"], 40)
         and value["source_commit"] == source["commit"]
         and (value["pr_head"] is None or _hex(value["pr_head"], 40))
         and (source["pr_head"] is None or source["pr_head"] == value["pr_head"]),
         "N07_REVIEW_PROVENANCE")
    # A source commit or artifact name is never interpreted as a main merge.
    if value["artifact_id"] is not None:
        need(value["workflow_run_id"] is not None, "N07_REVIEW_PROVENANCE")
    if value["artifact_status"] == "pending_upload":
        need(value["artifact_id"] is None and value["artifact_archive_sha256"] is None,
             "N07_REVIEW_ARTIFACT_DISPOSITION")
    else:
        need(value["artifact_status"] == "downloaded" and value["artifact_id"] is not None
             and _hex(value["artifact_archive_sha256"], 64), "N07_REVIEW_ARTIFACT_DISPOSITION")


def _inputs(native: dict, environment: dict, validation: dict,
            provenance: dict) -> dict:
    outcome = inspect_b2(native)
    probe = native["n07_probe"]
    need(type(probe["schema"]) is int and probe["schema"] == 1
         and type(probe["b2"]["schema"]) is int and probe["b2"]["schema"] == 1,
         "N07_REVIEW_PROBE_SCHEMA")
    for pointer in (probe["pointer"], probe["b2"]["item_pointer"], probe["b2"]["reopen_pointer"]):
        need(all(_uint(pointer[k]) for k in ("input_epoch", "epoch_after"))
             and type(pointer["uv_milli"]) is list and len(pointer["uv_milli"]) == 2
             and all(type(v) is int and 0 <= v <= 1000 for v in pointer["uv_milli"])
             and type(pointer["after"]["pressed"]) is int and pointer["after"]["pressed"] == 0,
             "N07_REVIEW_POINTER_TYPES")
    final_runtime = native["last_snapshot"]["runtime"]
    typed_zeros = [native["destroy_status"], *native["registry_counts"].values(),
                   final_runtime["render"]["queued"],
                   *(final_runtime["reclamation"][k] for k in (
                       "sdk_bindings", "native_targets", "panels", "pending_operations")),
                   *(final_runtime["render"]["composition"][k] for k in (
                       "textures", "texture_bytes", "decoration_texture_bytes", "gl_error"))]
    need(all(type(v) is int and v == 0 for v in typed_zeros), "N07_REVIEW_CLEANUP_TYPES")
    for event in native["trace"]["events"]:
        if event.get("kind") == "finalizer_published":
            need(type(event["data"]["status"]) is int and event["data"]["status"] == 0,
                 "N07_REVIEW_CLEANUP_TYPES")
    source = validation.get("source")
    _exact(source, {"commit", "tree_sha256", "dirty", "pr_head", "build_features"},
           "N07_REVIEW_SOURCE_SHAPE")
    need(_hex(source["commit"], 40) and _hex(source["tree_sha256"], 64)
         and source["dirty"] is False
         and (source["pr_head"] is None or _hex(source["pr_head"], 40))
         and type(source["build_features"]) is list
         and all(type(v) is str and len(v) <= 80 for v in source["build_features"]),
         "N07_REVIEW_SOURCE")
    _provenance(provenance, source)
    need(validation.get("status") == "probe_only"
         and validation.get("probe_status") == "probe_only"
         and validation.get("host_v3_acceptance_passed") is False
         and validation.get("verified_n07_assertions") == []
         and type(validation.get("exit_code")) is int and validation["exit_code"] == 0
         and validation.get("reason") == "N07_B2_DIAGNOSTIC_ONLY"
         and canonical(validation.get("probe")) == canonical(outcome),
         "N07_REVIEW_VALIDATION")
    need(type(environment) is dict and type(environment.get("schema")) is int
         and environment["schema"] == 1
         and environment.get("revision") == "eidolon-i6-n07-b2-environment-v1"
         and _hex(native.get("run_id"), 32)
         and environment.get("run_id") == native["run_id"]
         and environment.get("source_sha256") == native.get("source_sha256")
         == source["tree_sha256"]
         and environment.get("runner", {}).get("source_sha256") == source["tree_sha256"]
         and _hex(environment["runner"].get("binary_sha256"), 64),
         "N07_REVIEW_ENVIRONMENT_JOIN")
    build = native.get("build_source")
    need(type(build) is dict and build.get("commit") == source["commit"]
         and build.get("tree_sha256") == source["tree_sha256"]
         and build.get("dirty") is False
         and (build.get("pr_head") is None or build["pr_head"] == provenance["pr_head"]),
         "N07_REVIEW_BINARY_SOURCE")
    need(_hex(native.get("owner_id"), 32), "N07_REVIEW_OWNER")
    before, after = environment.get("before"), environment.get("after")
    need(type(before) is dict and canonical(before) == canonical(after)
         and _hex(before.get("bundle_sha256"), 64)
         and _hex(before.get("inventory_sha256"), 64), "N07_REVIEW_ENVIRONMENT_CHANGED")
    final = environment.get("final")
    _exact(final, {"app_process_count", "driver_child_count", "sshd_child_count",
                   "journal_entries", "journal_lock_held", "profile_lock_held"},
           "N07_REVIEW_CENSUS")
    need(all(type(final[k]) is int and final[k] == 0 for k in (
        "app_process_count", "driver_child_count", "sshd_child_count", "journal_entries"))
        and final["journal_lock_held"] is False and final["profile_lock_held"] is False,
        "N07_REVIEW_CLEANUP")
    return outcome


def _settling(native: dict, readbacks: dict, *, required: bool) -> None:
    """Check causal main-buffer repaint, without interpreting any glyph pixels."""
    probe = native["n07_probe"]
    proof = probe["b2"].get("post_popup_settling")
    if proof is None:
        need(not required, "N07_REVIEW_SETTLING_MISSING")
        return
    _exact(proof, {"schema", "source", "parent_surface", "baseline_content_frame",
                   "baseline_main_content_seq", "quiet_ms", "budget_ms", "status",
                   "samples", "content_frame", "main_content_seq"},
           "N07_REVIEW_SETTLING_SHAPE")
    before = readbacks["first_off_checkmark"]["receipt"]
    after = readbacks["first_on_gutter"]["receipt"]
    parent = probe["popup"]["parent_surface"]
    parents = [layer for layer in before["layers"] if layer["surface"] == parent]
    need(len(parents) == 1 and len(after["layers"]) == 1
         and after["layers"][0]["surface"] == parent, "N07_REVIEW_SETTLING_PARENT")
    old, new = parents[0], after["layers"][0]
    need(type(proof["schema"]) is int and proof["schema"] == 1
         and proof["source"] == "normal_sdk_renderer_main_content_sequence"
         and proof["parent_surface"] == parent
         and proof["baseline_content_frame"] == before["content_frame"]
         and proof["baseline_main_content_seq"] == old["content_seq"]
         and proof["content_frame"] == after["content_frame"]
         and proof["main_content_seq"] == new["content_seq"]
         and proof["status"] == "settled"
         and type(proof["quiet_ms"]) is int and proof["quiet_ms"] == 200
         and type(proof["budget_ms"]) is int and proof["budget_ms"] == 12000,
         "N07_REVIEW_SETTLING_IDENTITY")
    need(int(new["content_seq"]) > int(old["content_seq"])
         and int(after["content_frame"]) > int(before["content_frame"])
         and all(after[key] == before[key] for key in (
             "geometry", "logical_size", "content_rect"))
         and all(new[key] == old[key] for key in (
             "surface", "buffer_size", "offset", "clip", "scale", "transform")),
         "N07_REVIEW_MAIN_REPAINT_MISSING")
    samples = proof["samples"]
    need(type(samples) is list and 2 <= len(samples) <= 32, "N07_REVIEW_SETTLING_SAMPLES")
    last_request, last_elapsed = before["request_seq"], -1
    last_frame, last_main = int(before["content_frame"]), int(old["content_seq"])
    for sample in samples:
        _exact(sample, {"content_frame", "main_content_seq", "request_seq", "elapsed_ms"},
               "N07_REVIEW_SETTLING_SAMPLE")
        need(_decimal(sample["content_frame"]) and _decimal(sample["main_content_seq"])
             and _uint(sample["request_seq"], positive=True) and _uint(sample["elapsed_ms"])
             and last_request < sample["request_seq"] <= after["request_seq"]
             and last_elapsed <= sample["elapsed_ms"] <= proof["budget_ms"]
             and last_frame <= int(sample["content_frame"]) <= int(after["content_frame"])
             and last_main <= int(sample["main_content_seq"]) <= int(new["content_seq"]),
             "N07_REVIEW_SETTLING_ORDER")
        last_request, last_elapsed = sample["request_seq"], sample["elapsed_ms"]
        last_frame, last_main = int(sample["content_frame"]), int(sample["main_content_seq"])
    prior, final = samples[-2:]
    need(final["request_seq"] == after["request_seq"]
         and final["content_frame"] == prior["content_frame"] == after["content_frame"]
         and final["main_content_seq"] == prior["main_content_seq"] == new["content_seq"]
         and final["elapsed_ms"] - prior["elapsed_ms"] >= proof["quiet_ms"],
         "N07_REVIEW_SETTLING_QUIET_INTERVAL")


def _observed(native: dict, context_raw: bytes | None) -> tuple[dict, dict]:
    probe, result = native["n07_probe"], {}
    rb = {"first_off_gutter": probe["before_menu"]["readback"]}
    rb.update({name: probe["b2"][name]["readback"] for name in STATES[1:]})
    baseline = rb[STATES[0]]["receipt"]
    for name, readback in rb.items():
        _readback(readback)
        r = readback["receipt"]
        p = readback["pixels"][0]
        result[name] = {
            "source": "host_renderer_readback", "session": "first",
            "semantic_status": "unreviewed_hypothesis",
            "capture_id": probe["capture_id"], "run_id": native["run_id"],
            "runtime": native["runtime"], "owner_id": native["owner_id"],
            "sdk_adapter": readback["sdk_adapter"], "target": r["target"],
            "create": r["create"], "renderer_owner": r["renderer_owner"],
            "context_epoch": r["context_epoch"], "request_seq": r["request_seq"],
            "present_seq": r["present_seq"], "content_frame": r["content_frame"],
            "min_content_frame": r["min_content_frame"], "snapshot_id": r["snapshot_id"],
            "rect": p["rect"], "rgba_b64": p["rgba_b64"], "rgba_sha256": p["sha256"],
            "roi_id": p["id"], "roi_sha256": r["roi_sha256"],
            "coordinate_space": "target_pixels_top_left",
            "geometry": r["geometry"], "logical_size": r["logical_size"],
            "content_rect": r["content_rect"], "layers": r["layers"],
            "backend_identity": _backend_identity(r["backend"]),
            "dynamic_measurements": {"max_swap_us": r["backend"]["max_swap_us"]},
        }
    requests = [result[s]["request_seq"] for s in STATES]
    presents = [result[s]["present_seq"] for s in STATES]
    need(all(a < b for a, b in zip(requests, requests[1:]))
         and all(a < b for a, b in zip(presents, presents[1:])), "N07_REVIEW_STALE_TICKET")
    # B1's wider menu crop remains useful historical context. Its pixels are
    # diagnostic aids only and are never renamed as one of the six states.
    _readback(probe["menu_open"]["readback"])
    menu = probe["menu_open"]["readback"]
    need(menu["sdk_adapter"] == rb[STATES[0]]["sdk_adapter"]
         and _backend_identity(menu["receipt"]["backend"]) == _backend_identity(baseline["backend"])
         and baseline["request_seq"] < menu["receipt"]["request_seq"]
         < rb[STATES[1]]["receipt"]["request_seq"]
         and baseline["present_seq"] < menu["receipt"]["present_seq"]
         < rb[STATES[1]]["receipt"]["present_seq"]
         and int(baseline["content_frame"]) < int(menu["receipt"]["content_frame"])
         <= int(rb[STATES[1]]["receipt"]["content_frame"]), "N07_REVIEW_MENU_CONTEXT_IDENTITY")
    contexts = {"initial_menu_context": copy.deepcopy(probe["menu_open"]["readback"])}
    binding = native.get("review_context")
    _settling(native, rb, required=binding is not None)
    if binding is None:
        need(context_raw is None, "N07_REVIEW_CONTEXT_UNBOUND")
    else:
        _exact(binding, {"schema", "sha256", "bytes", "states", "complete"},
               "N07_REVIEW_CONTEXT_BINDING")
        need(type(binding["schema"]) is int and binding["schema"] == 1
             and binding["complete"] is True and binding["states"] == list(CONTEXT_STATES)
             and _uint(binding["bytes"], positive=True)
             and type(context_raw) is bytes and len(context_raw) <= MAX_INPUT_BYTES
             and len(context_raw) == binding["bytes"]
             and sha(context_raw) == binding["sha256"], "N07_REVIEW_CONTEXT_DIGEST")
        extra = _parse(context_raw)
        _exact(extra, {"schema", "revision", "source", "run_id", "runtime", "owner_id",
                       "source_sha256", "capture_id", "target", "complete", "states",
                       "error", "cleanup_error"}, "N07_REVIEW_CONTEXT_SHAPE")
        need(type(extra["schema"]) is int and extra["schema"] == 1
             and extra["revision"] == "eidolon-i6-n07-b3-review-context-v1"
             and extra["source"] == "host_renderer_review_context"
             and extra["complete"] is True and extra["error"] is None
             and extra["cleanup_error"] is None
             and all(extra[k] == native[k] for k in ("run_id", "runtime", "owner_id", "source_sha256"))
             and extra["capture_id"] == probe["capture_id"]
             and extra["target"] == probe["main_target"], "N07_REVIEW_CONTEXT_SOURCE")
        _exact(extra["states"], set(CONTEXT_STATES), "N07_REVIEW_CONTEXT_STATES")
        for index, name in enumerate(CONTEXT_STATES):
            stage = extra["states"][name]
            _exact(stage, {"phase", "readback"}, "N07_REVIEW_CONTEXT_SHAPE")
            need(stage["phase"] == name, "N07_REVIEW_CONTEXT_PHASE")
            _readback(stage["readback"])
            r = stage["readback"]["receipt"]
            need(all(r[k] == baseline[k] for k in (
                "target", "runtime", "create", "renderer_owner", "context_epoch"))
                and stage["readback"]["sdk_adapter"] == rb[STATES[0]]["sdk_adapter"]
                and _backend_identity(r["backend"]) == _backend_identity(baseline["backend"]),
                "N07_REVIEW_CONTEXT_IDENTITY")
            canonical_receipt = rb[STATES[index]]["receipt"]
            need(r["request_seq"] > canonical_receipt["request_seq"]
                 and r["present_seq"] > canonical_receipt["present_seq"]
                 and int(r["content_frame"]) >= int(canonical_receipt["content_frame"]),
                 "N07_REVIEW_CONTEXT_ORDER")
            need(all(r[k] == canonical_receipt[k] for k in (
                "geometry", "logical_size", "content_rect"))
                and len(r["layers"]) == len(canonical_receipt["layers"]),
                "N07_REVIEW_CONTEXT_GEOMETRY")
            for layer, anchor in zip(r["layers"], canonical_receipt["layers"]):
                need(all(layer[k] == anchor[k] for k in (
                    "surface", "buffer_size", "offset", "clip", "scale", "transform"))
                    and int(layer["content_seq"]) >= int(anchor["content_seq"])
                    and (r["content_frame"] != canonical_receipt["content_frame"]
                         or layer == anchor), "N07_REVIEW_CONTEXT_LAYERS")
            need(r["backend"]["max_swap_us"] >= canonical_receipt["backend"]["max_swap_us"],
                 "N07_REVIEW_CONTEXT_METRIC_ORDER")
            if index < 3:
                next_receipt = rb[STATES[index + 1]]["receipt"]
                need(r["request_seq"] < next_receipt["request_seq"]
                     and r["present_seq"] < next_receipt["present_seq"]
                     and int(r["content_frame"]) <= int(next_receipt["content_frame"]),
                     "N07_REVIEW_CONTEXT_ORDER")
                need(r["backend"]["max_swap_us"] <= next_receipt["backend"]["max_swap_us"],
                     "N07_REVIEW_CONTEXT_METRIC_ORDER")
            for p, (uid, x, y) in zip(stage["readback"]["pixels"], (
                    (101, 0, 0), (102, 256, 0), (103, 0, 128), (104, 256, 128))):
                need(p["id"] == uid and p["rect"] == {
                    "x": x, "y": y, "width": 256, "height": 128},
                    "N07_REVIEW_CONTEXT_TILES")
            need(len(stage["readback"]["pixels"]) == 4, "N07_REVIEW_CONTEXT_TILES")
            contexts[name] = copy.deepcopy(stage["readback"])
        proof = probe["b2"]["post_popup_settling"]
        need(proof["samples"][0]["request_seq"] > contexts["first_off_menu_context"]
             ["receipt"]["request_seq"], "N07_REVIEW_SETTLING_CONTEXT_ORDER")
    return result, contexts


def default_proposal(states: dict) -> dict:
    """Explicit conservative starting point, never inferred from pixel deltas."""
    return {
        "status": "unreviewed", "basis": "full_roi_exact_match_provisional",
        "states": {name: {"mask_b64": base64.b64encode(bytes([1]) * (
            item["rect"]["width"] * item["rect"]["height"])).decode("ascii"),
            "max_channel_delta": 0} for name, item in states.items()},
        "must_differ": [{"a": a, "b": b, "min_changed_pixels": 8,
                         "min_channel_delta": 1} for a, b in pixel.NEGATIVE_PAIRS],
    }


def _proposal(value: dict, states: dict) -> None:
    _exact(value, {"status", "basis", "states", "must_differ"}, "N07_REVIEW_PROPOSAL")
    need(value["status"] == "unreviewed" and value["basis"] in (
        "full_roi_exact_match_provisional", "manual_unreviewed_proposal"), "N07_REVIEW_PROPOSAL")
    _exact(value["states"], set(STATES), "N07_REVIEW_STATE_COVERAGE")
    masks = {}
    for name in STATES:
        item = value["states"][name]
        _exact(item, {"mask_b64", "max_channel_delta"}, "N07_REVIEW_MASK")
        _, mask, _ = pixel._expected_state({"rect": states[name]["rect"],
            "rgba_b64": states[name]["rgba_b64"], **item})
        masks[name] = mask
    need(type(value["must_differ"]) is list and len(value["must_differ"]) == 2,
         "N07_REVIEW_CONTRAST")
    for rule, (a, b) in zip(value["must_differ"], pixel.NEGATIVE_PAIRS):
        _exact(rule, {"a", "b", "min_changed_pixels", "min_channel_delta"},
               "N07_REVIEW_CONTRAST")
        need((rule["a"], rule["b"]) == (a, b) and masks[a] == masks[b]
             and type(rule["min_changed_pixels"]) is int
             and 1 <= rule["min_changed_pixels"] <= sum(masks[a])
             and type(rule["min_channel_delta"]) is int
             and 1 <= rule["min_channel_delta"] <= 128, "N07_REVIEW_CONTRAST")


def build_candidate(native: dict, environment: dict, validation: dict,
                    provenance: dict, fingerprint: dict | None = None,
                    proposal: dict | None = None,
                    context_raw: bytes | None = None) -> dict:
    outcome = _inputs(native, environment, validation, provenance)
    states, contexts = _observed(native, context_raw)
    if fingerprint is None:
        fp = {"status": "not_collected", "fingerprint": None,
              "missing": sorted(pixel.FINGERPRINT), "complete": False}
    else:
        from linux_real_app_v3_n07_fingerprint import validate_fingerprint
        fp = validate_fingerprint(fingerprint, native, environment, validation["source"])
    chosen = default_proposal(states) if proposal is None else copy.deepcopy(proposal)
    _proposal(chosen, states)
    complete = fp.get("status") == "complete"
    return {
        "schema": 1, "revision": REVISION,
        "status": "candidate_pending_review" if complete else "diagnostic_only_missing_fingerprint",
        "approval": {"status": "not_reviewed", "reviewer": None,
                     "review_commit": None, "approved_sha256": None},
        "host_v3_acceptance_passed": False, "verified_n07_assertions": [],
        "menu_identity_verified": False, "reviewed_golden": False,
        "production_compatible": False, "source": copy.deepcopy(validation["source"]),
        "origin": copy.deepcopy(provenance),
        "provenance_scope": "caller_supplied_retrieval_metadata_bound_to_saved_source",
        "mousepad_source_mapping": {
            "commit": pixel.MOUSEPAD_COMMIT,
            "url": "https://github.com/xfce-mirror/mousepad/blob/" + pixel.MOUSEPAD_COMMIT
                   + "/mousepad/resources/gtk/menus.ui",
            "menu_label": "_View", "item_label": "Line N_umbers",
            "action": "app.preferences.view.show-line-numbers",
            "rendered_semantics_verified": False,
        },
        "input_sha256": {name: sha(canonical(value)) for name, value in (
            ("native", native), ("environment", environment), ("validation", validation),
            ("provenance", provenance), ("fingerprint", fingerprint), ("proposal", proposal))},
        "context_sha256": sha(context_raw) if context_raw is not None else None,
        "capture_id": native["n07_probe"]["capture_id"], "run_id": native["run_id"],
        "fingerprint_observation": copy.deepcopy(fp),
        "coverage": {"observed_states": list(STATES), "missing_states": list(MISSING_STATES),
                     "observed_sessions": ["first"], "complete_six_state_reference": False},
        "states": states, "diagnostic_contexts": contexts,
        "popup": {"initial": copy.deepcopy(native["n07_probe"]["popup"]),
                  "reopened": copy.deepcopy(native["n07_probe"]["b2"]["reopened_popup"])},
        **({"post_popup_settling": copy.deepcopy(native["n07_probe"]["b2"]["post_popup_settling"])}
           if "post_popup_settling" in native["n07_probe"]["b2"] else {}),
        "pointer": {"view_uv_milli": native["n07_probe"]["pointer"]["uv_milli"],
                    "line_numbers_candidate_uv_milli":
                    native["n07_probe"]["b2"]["candidate_item_uv_milli"]},
        "proposal": chosen, "diagnostic_verdict": outcome,
        "normal_cleanup_verified": True, "review_checklist": list(CHECKLIST),
    }


def validate_candidate(raw: bytes, expected_sha256: str, *, native: dict,
                       environment: dict, validation: dict, provenance: dict,
                       fingerprint: dict | None = None,
                       proposal: dict | None = None,
                       context_raw: bytes | None = None) -> dict:
    """Check exact proposal bytes and rerun the saved-receipt audit.

    The expected digest must be retained outside the packet under review. This
    seam validates integrity; it cannot turn an unreviewed packet into approval.
    """
    need(_hex(expected_sha256, 64) and sha(raw) == expected_sha256,
         "N07_REVIEW_DIGEST_MISMATCH")
    actual = _parse(raw)
    expected = build_candidate(native, environment, validation, provenance, fingerprint,
                               proposal, context_raw)
    need(raw == canonical(actual) and canonical(actual) == canonical(expected),
         "N07_REVIEW_CANDIDATE_MISMATCH")
    return actual


def ensure_independent(candidate: dict, observed_run_id: str,
                       observed_capture_id: str, *, workflow_run_id: str | None = None) -> None:
    """Reject reuse even before the separate future production approval gate."""
    need(_hex(observed_run_id, 32) and _hex(observed_capture_id, 64),
         "N07_REVIEW_OBSERVED_IDENTITY")
    need(workflow_run_id is None or _decimal(workflow_run_id),
         "N07_REVIEW_OBSERVED_IDENTITY")
    need(observed_run_id != candidate["run_id"]
         and observed_capture_id != candidate["capture_id"]
         and (workflow_run_id is None or workflow_run_id != candidate["origin"]["workflow_run_id"]),
         "N07_OBSERVED_AS_EXPECTED")


def png(rgba: bytes, width: int, height: int) -> bytes:
    """Lossless RGBA, top-left rows, no color profile or pixel transformation."""
    need(type(width) is int and type(height) is int and 0 < width <= 4096
         and 0 < height <= 4096 and len(rgba) == width * height * 4,
         "N07_REVIEW_PNG_BOUNDS")
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(
            ">I", zlib.crc32(kind + body) & 0xffffffff)
    rows = b"".join(b"\0" + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">2I5B", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def _zoom(rgba: bytes, width: int, height: int, factor: int = 3) -> bytes:
    output = bytearray()
    for y in range(height):
        row = b"".join(rgba[(y * width + x) * 4:(y * width + x + 1) * 4] * factor
                       for x in range(width))
        output.extend(row * factor)
    return png(bytes(output), width * factor, height * factor)


def _assets(candidate: dict) -> dict[str, bytes]:
    result = {}
    for name, state in candidate["states"].items():
        rgba = pixel._decode_b64(state["rgba_b64"], state["rect"]["width"] * state["rect"]["height"] * 4)
        w, h = state["rect"]["width"], state["rect"]["height"]
        result[f"raw/{name}.rgba"] = rgba
        result[f"raw/{name}.png"] = png(rgba, w, h)
        result[f"aids/{name}-3x.png"] = _zoom(rgba, w, h)
        mask = pixel._decode_b64(candidate["proposal"]["states"][name]["mask_b64"], w * h)
        result[f"aids/{name}-mask.png"] = png(
            bytes(channel for bit in mask for channel in (bit * 255, bit * 255, bit * 255, 255)), w, h)
    for a, b in pixel.NEGATIVE_PAIRS:
        left, right = (candidate["states"][n] for n in (a, b))
        av, bv = (base64.b64decode(s["rgba_b64"], validate=True) for s in (left, right))
        diff = bytes(v for i in range(0, len(av), 4)
                     for v in (abs(av[i] - bv[i]), abs(av[i + 1] - bv[i + 1]),
                               abs(av[i + 2] - bv[i + 2]), 255))
        result[f"aids/{a}--{b}-rgb-absolute-diff.png"] = png(
            diff, left["rect"]["width"], left["rect"]["height"])
    for name, readback in candidate["diagnostic_contexts"].items():
        stitched = bytearray(512 * 256 * 4) if name in CONTEXT_STATES else None
        for item in readback["pixels"]:
            r = item["rect"]
            rgba = pixel._decode_b64(item["rgba_b64"], r["width"] * r["height"] * 4)
            stem = f"context/{name}-roi-{item['id']}"
            result[stem + ".rgba"] = rgba
            result[stem + ".png"] = png(rgba, r["width"], r["height"])
            if stitched is not None:
                for y in range(r["height"]):
                    offset = ((r["y"] + y) * 512 + r["x"]) * 4
                    stitched[offset:offset + r["width"] * 4] = rgba[
                        y * r["width"] * 4:(y + 1) * r["width"] * 4]
        if stitched is not None:
            result[f"aids/{name}-stitched.png"] = png(bytes(stitched), 512, 256)
    return result


def _html(candidate: dict, digest: str) -> bytes:
    e = html.escape
    parts = ["<!doctype html><html lang='en'><meta charset='utf-8'><title>N07 unreviewed pixel packet</title>",
        "<style>body{max-width:1200px;margin:2em auto;font:16px system-ui;background:#f5f5f5;color:#171717}"
        "section{background:white;padding:1em;margin:1em 0}img{max-width:100%;image-rendering:pixelated;border:1px solid #888}"
        "code{overflow-wrap:anywhere}table{border-collapse:collapse}td,th{padding:.5em;border:1px solid #aaa;text-align:left}</style>",
        "<h1>N07 Pixel review packet — UNAPPROVED</h1>",
        "<p>Four first-session observations; two second-session states are absent. "
        "The OFF/ON labels are hypotheses awaiting independent semantic review. N07 0/13; Host acceptance false.</p>",
        f"<p>Packet integrity SHA-256: <code>{digest}</code>. This is not an approval pin.</p>",
        "<h2>Provenance</h2><pre>" + e(json.dumps(candidate["origin"], indent=2, sort_keys=True)) + "</pre>",
        "<h2>Environment fingerprint</h2><pre>" + e(json.dumps(candidate["fingerprint_observation"], indent=2, sort_keys=True)) + "</pre>",
        "<h2>Review checklist</h2><ol>" + "".join("<li>" + e(v) + "</li>" for v in CHECKLIST) + "</ol>",
        "<p>Fixed source: <a href='" + e(candidate["mousepad_source_mapping"]["url"], quote=True)
        + "'>Mousepad menus.ui at " + pixel.MOUSEPAD_COMMIT + "</a>. Source labels/action identify "
        "the intended operation; matching the rendered pixels still requires review.</p>",
        "<h2>Actual renderer observations</h2><p>raw/*.rgba and raw/*.png preserve original pixels. "
        "aids/* images are enlarged or difference renderings and must never be used as expected golden pixels. "
        "A broad ROI and nonzero changed-pixel count alone do not identify a glyph.</p>"]
    for name in STATES:
        item = candidate["states"][name]
        parts.extend(["<section><h3>" + e(name) + "</h3>",
            "<p>Frame <code>" + e(item["content_frame"]) + "</code>; ROI <code>" +
            e(json.dumps(item["rect"], sort_keys=True)) + "</code>; RGBA SHA <code>" + item["rgba_sha256"] + "</code></p>",
            f"<p>Original <img src='raw/{name}.png' alt='{name} actual pixels'></p>",
            f"<details><summary>3× nearest-neighbor review aid</summary><img src='aids/{name}-3x.png' alt='Enlarged review aid'></details>",
            f"<details><summary>Proposed mask (white selects a pixel; unreviewed)</summary><img src='aids/{name}-mask.png' alt='Provisional binary mask'></details></section>"])
    parts.append("<h2>Diagnostic RGB differences</h2><p>Absolute RGB differences; alpha is set to 255 for viewing only.</p>")
    for a, b in pixel.NEGATIVE_PAIRS:
        parts.append(f"<section><h3>{a} → {b}</h3><img src='aids/{a}--{b}-rgb-absolute-diff.png' alt='Absolute RGB difference aid'></section>")
    parts.append("<h2>Context and coordinate mapping</h2><p>These additional raw crops preserve context only. "
                 "See candidate.json for actual layer/viewport/geometry, popup surface and pointer coordinates.</p>")
    for name, rb in sorted(candidate["diagnostic_contexts"].items()):
        parts.append("<section><h3>" + e(name) + "</h3>")
        if name in CONTEXT_STATES:
            parts.append(f"<p>Stitched diagnostic aid (four original tiles below)</p><img src='aids/{name}-stitched.png' alt='Stitched diagnostic context aid'>")
        for item in rb["pixels"]:
            stem = f"context/{name}-roi-{item['id']}"
            parts.append("<p>" + e(json.dumps(item["rect"], sort_keys=True)) +
                         f"</p><img src='{stem}.png' alt='Actual context crop'>")
        parts.append("</section>")
    parts.append("<h2>Separate approval procedure</h2><p>First inspect the saved receipts, source mapping, "
        "font/environment evidence and glyphs. Record any revised mask/tolerance as a new unreviewed proposal and "
        "retain its new packet digest. Acquire missing second-session reference states independently of future "
        "acceptance runs. A separately reviewed change must then supply genuine reviewer/commit metadata, "
        "all six reference states and the exact production fixture digest. This tool provides no promotion command.</p></html>")
    return "\n".join(parts).encode("utf-8")


def export_bundle(output: Path, *, native: dict, environment: dict,
                  validation: dict, provenance: dict, fingerprint: dict | None = None,
                  proposal: dict | None = None,
                  context_raw: bytes | None = None) -> dict:
    """Create a fresh bounded packet. Save source evidence even if validation fails."""
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    (output / "evidence").mkdir(mode=0o700)
    inputs = dict(native=native, environment=environment, validation=validation,
                  provenance=provenance, fingerprint=fingerprint, proposal=proposal)
    for name, value in inputs.items():
        _write(output / "evidence" / INPUT_NAMES[name], canonical(value))
    if context_raw is not None:
        need(type(context_raw) is bytes and 0 < len(context_raw) <= MAX_INPUT_BYTES,
             "N07_REVIEW_CONTEXT_BOUNDS")
        _write(output / "evidence" / "context.json", context_raw)
    inputs["context_raw"] = context_raw
    try:
        candidate = build_candidate(**inputs)
        raw = canonical(candidate)
        need(len(raw) <= MAX_INPUT_BYTES, "N07_REVIEW_CANDIDATE_BOUNDS")
        digest = sha(raw)
        validate_candidate(raw, digest, **inputs)
        for directory in ("raw", "aids", "context"):
            (output / directory).mkdir(mode=0o700)
        for name, data in _assets(candidate).items():
            _write(output / name, data)
        _write(output / "candidate.json", raw)
        _write(output / "candidate.sha256", (digest + "\n").encode("ascii"))
        _write(output / "review.html", _html(candidate, digest))
        return {"status": candidate["status"], "candidate_sha256": digest,
                "states": list(STATES), "missing_states": list(MISSING_STATES),
                "reviewed_golden": False, "host_v3_acceptance_passed": False}
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        # Raw fixed-name receipts remain available to audit failed interaction
        # and cleanup. Do not create images or a candidate from invalid receipts.
        reason = str(error) if re.fullmatch(r"[A-Z0-9_]{1,100}", str(error)) else "N07_REVIEW_REJECTED"
        _write(output / "diagnostic.json", canonical({
            "status": "rejected", "reason": reason, "candidate_created": False,
            "normal_cleanup_verified": False, "host_v3_acceptance_passed": False,
        }))
        raise


def validate_bundle(root: Path, expected_sha256: str, *,
                    observed_run_id: str | None = None,
                    observed_capture_id: str | None = None,
                    workflow_run_id: str | None = None) -> dict:
    inputs = {}
    for name, filename in INPUT_NAMES.items():
        raw = _read(root / "evidence" / filename)
        value = evidence.parse(raw, limit=MAX_INPUT_BYTES)
        need(raw == canonical(value), "N07_REVIEW_INPUT_CANONICAL")
        inputs[name] = value
    context_path = root / "evidence" / "context.json"
    inputs["context_raw"] = _read(context_path) if context_path.exists() else None
    candidate = validate_candidate(_read(root / "candidate.json"), expected_sha256, **inputs)
    need(_read(root / "candidate.sha256") == (expected_sha256 + "\n").encode("ascii"),
         "N07_REVIEW_DIGEST_FILE_MISMATCH")
    for path, expected in _assets(candidate).items():
        need(_read(root / path) == expected, "N07_REVIEW_ASSET_MISMATCH")
    need(_read(root / "review.html") == _html(candidate, expected_sha256), "N07_REVIEW_GUIDE_MISMATCH")
    if observed_run_id is not None or observed_capture_id is not None:
        ensure_independent(candidate, observed_run_id, observed_capture_id,
                           workflow_run_id=workflow_run_id)
    elif workflow_run_id is not None:
        need(False, "N07_REVIEW_OBSERVED_IDENTITY")
    return {"status": candidate["status"], "candidate_sha256": expected_sha256,
            "integrity_verified": True, "reviewed_golden": False,
            "host_v3_acceptance_passed": False, "verified_n07_assertions": []}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    for name in ("native", "environment", "validation", "provenance"):
        export.add_argument("--" + name, type=Path, required=True)
    export.add_argument("--fingerprint", type=Path)
    export.add_argument("--proposal", type=Path)
    export.add_argument("--context", type=Path)
    export.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("validate")
    verify.add_argument("--bundle", type=Path, required=True)
    verify.add_argument("--expected-sha256", required=True)
    verify.add_argument("--observed-run-id")
    verify.add_argument("--observed-capture-id")
    verify.add_argument("--observed-workflow-run-id")
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            inputs = {name: _parse(_read(getattr(args, name))) for name in (
                "native", "environment", "validation", "provenance")}
            inputs.update({name: _parse(_read(getattr(args, name))) if getattr(args, name)
                           else None for name in ("fingerprint", "proposal")})
            inputs["context_raw"] = _read(args.context) if args.context else None
            result = export_bundle(args.output, **inputs)
        else:
            result = validate_bundle(args.bundle, args.expected_sha256,
                observed_run_id=args.observed_run_id, observed_capture_id=args.observed_capture_id,
                workflow_run_id=args.observed_workflow_run_id)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        print(json.dumps({"status": "rejected", "reason": "N07_REVIEW_REJECTED",
                          "host_v3_acceptance_passed": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
