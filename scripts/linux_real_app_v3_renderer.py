#!/usr/bin/env python3
"""Bounded renderer T evidence; never an N02 or full I6 acceptance oracle."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import struct
import subprocess
import sys

from linux_real_app_v3_evidence import (
    EvidenceError, canonical, load_manifest, manifest_digest, parse, read_report,
    source_input_hashes, source_snapshot, write_report,
)

REVISION = "eidolon-i6-renderer-v1"
MAX_BYTES = 1024 * 1024
LABELS = ["initial", "idle", "decoration", "content_update", "new_generation"]
NEGATIVES = {
    "foreign_owner", "ticket_replay", "wrong_session", "wrong_window",
    "uncreated_generation", "roi_count", "roi_bytes", "roi_bounds",
    "min_content_frame", "queue_capacity", "foreign_fence", "late_attach",
    "retired_generation", "old_target_new_generation", "retired_window_attach", "after_stop",
}
COVERAGE = {key: "not_run" for key in (
    "normal_launch", "sdk", "b1_ready", "current_input_gate", "n02_gtk3_desktop",
    "n02_mousepad_editor", "android_storage", "quest",
)}
FIXED_ROIS = [{"id": 1, "x": 624, "y": 624, "width": 32, "height": 32},
              {"id": 2, "x": 8, "y": 8, "width": 2, "height": 2}]
RECEIPT_FIELDS = {"renderer_owner", "context_epoch", "request_seq", "target", "min_content_frame",
                  "content_frame", "present_seq", "snapshot_id", "geometry", "logical_size", "layers",
                  "content_rect", "roi_revision", "roi_sha256", "pixel_sha256", "backend", "rois"}
CENSUS_FIELDS = {"workers", "owner_tid", "queued", "contexts", "targets", "latest_images",
                 "attaches", "detaches", "paused", "error", "composition"}
COMPOSITION_FIELDS = {"textures", "texture_bytes", "decoration_texture_bytes", "gl_error", "error"}
# Independent limits for this fixed fixture, not values read from producer
# configuration or accepted-count evidence.
READBACK_CAPACITY = 8
MAX_RETAINED_THEME_BYTES = 32 * 1024 * 1024


def need(condition, reason="I6_RENDERER_EVIDENCE_INVALID"):
    if not condition:
        raise ValueError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def same_typed(value, expected):
    """Reject bool-as-int, floats in integer fields, and extra nested keys."""
    if type(value) is not type(expected):
        return False
    if type(expected) is dict:
        return set(value) == set(expected) and all(same_typed(value[k], v) for k, v in expected.items())
    if type(expected) is list:
        return len(value) == len(expected) and all(same_typed(a, b) for a, b in zip(value, expected))
    return value == expected


def roi_digest():
    raw = struct.pack("<I", 1)
    for roi in FIXED_ROIS:
        raw += struct.pack("<IIIII", *(roi[k] for k in ("id", "x", "y", "width", "height")))
    return sha(raw)


def positive(value):
    if type(value) is str:
        need(re.fullmatch(r"[1-9][0-9]{0,18}", value) is not None)
        value = int(value)
    need(type(value) is int and 0 < value < 2**63)
    return value


def expected_bytes(frame, roi, focused):
    """Independent constants/formula, not renderer/reference_pixel output."""
    if roi == 2:
        return bytes([80, 96, 112, 255] if focused else [16, 32, 64, 255]) * 4
    need(roi == 1)
    return bytes(channel for y in range(32) for x in range(32)
                 for channel in (frame % 128, (y + frame) % 128, (x + frame) % 128, 255))


def validate_census(value, stage):
    need(type(value) is dict and set(value) == CENSUS_FIELDS)
    before, final = stage == "before_target", stage == "final_renderer"
    expected = {"workers": 0 if final else 1, "queued": 0, "targets": [],
                "latest_images": 0, "attaches": 0 if before else 2,
                "detaches": 0 if before else 2, "paused": before, "error": None}
    need(all(same_typed(value[name], wanted) for name, wanted in expected.items()))
    need(type(value["owner_tid"]) is int and 0 < value["owner_tid"] < 2**31)
    composition = value["composition"]
    need(type(composition) is dict and set(composition) == COMPOSITION_FIELDS)
    need(all(same_typed(composition[name], 0) for name in ("textures", "texture_bytes", "gl_error"))
         and composition["error"] is None)
    if stage == "retired":
        # Window retirement releases window-owned images/cache/targets. The
        # renderer may retain its one context and bounded global theme until Stop.
        need(type(value["contexts"]) is int and 0 <= value["contexts"] <= 1)
        retained = composition["decoration_texture_bytes"]
        need(type(retained) is int and 0 <= retained <= MAX_RETAINED_THEME_BYTES)
        need(value["contexts"] != 0 or retained == 0)
    else:
        need(same_typed(value["contexts"], 0)
             and same_typed(composition["decoration_texture_bytes"], 0))
    return value["owner_tid"]


def validate(native, source, inputs, unavailable):
    need(type(native) is dict and same_typed(native.get("schema"), 1) and native.get("revision") == REVISION
         and native.get("status") == "pass" and native.get("evidence_class") == "T")
    need(native.get("host_v3_acceptance_passed") is False and native.get("coverage") == COVERAGE)
    need(source["dirty"] is False and source["build_features"] == [])
    need(native.get("identity") == {"source": {**source, "build_features": ["host-acceptance"]}, "inputs": inputs},
         "I6_SOURCE_MISMATCH")
    census_owners = [validate_census(native[stage], stage)
                     for stage in ("before_target", "retired", "final_renderer")]
    need(len(set(census_owners)) == 1 and native["owner_reclaimed"] is True)
    rows = native["readbacks"]
    need(type(rows) is list and [row["label"] for row in rows] == LABELS)
    keys, frames, owners, presents, requests, minimums, contexts, surfaces, contents = [], [], [], [], [], [], [], [], []
    backend = None
    for index, row in enumerate(rows):
        frame, focused = [(0, False), (0, False), (0, True), (1, True), (2, True)][index]
        need(row["fixture_frame"] == frame and type(row["fixture_frame"]) is int
             and row["focused"] is focused)
        receipt = row["receipt"]
        need(type(receipt) is dict and set(receipt) == RECEIPT_FIELDS)
        key = receipt["target"]
        need(type(key) is dict and set(key) == {"session", "window", "scene", "generation"})
        keys.append({name: positive(value) for name, value in key.items()})
        owners.append(positive(receipt["renderer_owner"]))
        frames.append(positive(receipt["content_frame"]))
        presents.append(positive(receipt["present_seq"]))
        requests.append(positive(receipt["request_seq"]))
        contexts.append(positive(receipt["context_epoch"]))
        minimums.append(positive(receipt["min_content_frame"]))
        need(minimums[-1] <= frames[-1])
        need(receipt["snapshot_id"] == f"{owners[-1]}:{keys[-1]['session']}:{keys[-1]['window']}:{frames[-1]}")
        need(same_typed(receipt["content_rect"], {"origin": [624, 624], "size": [32, 32]}))
        need(same_typed(receipt["logical_size"], {"width": 32, "height": 32}))
        logical_geometry = {"origin": {"x": 0, "y": 0}, "size": {"width": 32, "height": 32}}
        need(same_typed(receipt["geometry"], logical_geometry))
        need(type(receipt["layers"]) is list and len(receipt["layers"]) == 1)
        layer = receipt["layers"][0]
        need(type(layer) is dict and set(layer) == {"surface", "content_seq", "buffer_size", "offset", "clip", "scale", "transform"})
        surfaces.append(positive(layer["surface"]))
        contents.append(positive(layer["content_seq"]))
        need(same_typed(layer["buffer_size"], {"width": 32, "height": 32})
             and same_typed(layer["offset"], {"x": 0, "y": 0}) and same_typed(layer["clip"], logical_geometry)
             and same_typed(layer["scale"], 1) and layer["transform"] == "Normal")
        need(same_typed(receipt["roi_revision"], 1) and receipt["roi_sha256"] == roi_digest())
        gpu = receipt["backend"]
        need(same_typed(gpu["rgba_bits"], [8, 8, 8, 8]) and same_typed(gpu["width"], 1280)
             and same_typed(gpu["height"], 1280)
             and type(gpu["config_id"]) is int and gpu["config_id"] > 0
             and "llvmpipe" in gpu["renderer"].lower() and "OpenGL ES 3" in gpu["version"]
             and gpu["readback"] is True and bool(gpu["vendor"]))
        fingerprint = {name: gpu[name] for name in ("vendor", "renderer", "version", "rgba_bits", "config_id")}
        backend = fingerprint if backend is None else backend
        need(fingerprint == backend)
        comparisons = row["comparisons"]
        need(type(comparisons) is list and [c["roi_id"] for c in comparisons] == [1, 2])
        need(type(receipt["rois"]) is list and len(receipt["rois"]) == 2)
        expected_digests = []
        for pixels, comparison, wanted_roi in zip(receipt["rois"], comparisons, FIXED_ROIS):
            need(type(pixels) is dict and set(pixels) == {"roi"} and same_typed(pixels["roi"], wanted_roi))
            wanted = expected_bytes(frame, wanted_roi["id"], focused)
            expected_digests.append(sha(wanted))
            need(comparison["bytes"] == len(wanted) and type(comparison["bytes"]) is int
                 and comparison["actual_sha256"] == comparison["expected_sha256"] == sha(wanted))
        need(receipt["pixel_sha256"] == expected_digests)
    need(len(set(owners)) == len(set(contexts)) == len(set(surfaces)) == 1 and len(set(requests)) == len(rows))
    # These are the fixed fixture's requests, not arbitrary external success
    # values. The future-frame request is issued before the next real commit.
    need(requests == [1, 2, 4, 5, 91])
    need(minimums == [frames[0], frames[0], frames[0], frames[0] + 1, frames[4]])
    need(keys[0] == keys[1] == keys[2] == keys[3])
    need(all(keys[4][name] == keys[0][name] for name in ("session", "window", "scene"))
         and keys[4]["generation"] > keys[0]["generation"])
    need(frames[0] == frames[1] == frames[2] < frames[3] < frames[4])
    need(contents[0] == contents[1] == contents[2] < contents[3] < contents[4])
    need(presents[0] < presents[1] < presents[2] < presents[3])
    # Present counters belong to a full target; a new generation starts anew.
    negatives = native["negative_cases"]
    need(type(negatives) is list and len(negatives) == len(NEGATIVES)
         and {row["case"] for row in negatives} == NEGATIVES)
    permitted = {"foreign_owner": "Stale", "ticket_replay": "Stale", "wrong_session": "StaleOrNotReady",
                 "wrong_window": "StaleOrNotReady", "uncreated_generation": "StaleOrNotReady",
                 "roi_count": "InvalidArgument", "roi_bytes": "InvalidArgument", "roi_bounds": "InvalidArgument",
                 "min_content_frame": "Pending", "queue_capacity": "Busy", "foreign_fence": "Rejected",
                 "late_attach": "Stale", "retired_generation": "StaleOrNotReady",
                 "old_target_new_generation": "Preserved", "retired_window_attach": "Stale",
                 "after_stop": "NotReadyOrStale"}
    for row in negatives:
        need(row["status"] == permitted[row["case"]])
        if row["case"] == "queue_capacity":
            need(same_typed(row["accepted"], READBACK_CAPACITY))
    need(same_typed(native["client"]["frames"], 100) and native["client"]["protocol_error"] is False)
    need(type(unavailable) is dict and same_typed(unavailable.get("returncode"), 1)
         and unavailable.get("status") == "fail" and same_typed(unavailable.get("successful_receipts"), 0)
         and unavailable.get("reason") in {"EGL_INIT_FAILED", "EGL_DRIVER_UNAVAILABLE", "EGL_UNAVAILABLE"},
         "I6_RENDERER_MISSING_BACKEND_NOT_REJECTED")
    manifest = load_manifest()
    return {"schema": 1, "revision": REVISION, "evidence_class": "T", "renderer_t_passed": True,
            "backend_fingerprint": backend, "backend_fingerprint_sha256": sha(canonical(backend)),
            "readbacks": len(rows), "negative_cases": len(negatives) + 1,
            "host_v3_acceptance_passed": False, "coverage": COVERAGE,
            "i6_manifest": {"sha256": manifest_digest(), "families": len({c["case_id"] for c in manifest["cases"]}),
                            "required_variants": len(manifest["cases"]),
                            "assertion_references": sum(len(c["assertions"]) for c in manifest["cases"])},
            "environment_disposal": "not_yet"}


def execute(runner, environment):
    completed = subprocess.run([os.fspath(runner)], env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
    need(len(completed.stdout) <= MAX_BYTES and len(completed.stderr) <= MAX_BYTES,
         "I6_RENDERER_OUTPUT_LIMIT")
    return completed.returncode, parse(completed.stdout)


def run(source_root, runner, artifact_root):
    os.umask(0o077)
    need(Path("/.dockerenv").is_file() and os.getuid() == 12001, "I6_RENDERER_NOT_DISPOSABLE")
    account = pwd.getpwuid(os.getuid())
    need(account.pw_name == "eidolon-driver" and account.pw_dir == "/home/eidolon-driver")
    info = artifact_root.lstat()
    need(artifact_root.is_absolute() and stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
         and info.st_mode & 0o077 == 0 and not any(artifact_root.iterdir()), "I6_RENDERER_ARTIFACT_ROOT")
    source = source_snapshot(source_root)
    need(source["dirty"] is False, "I6_SOURCE_MISMATCH")
    inputs = source_input_hashes()
    environment = {"HOME": account.pw_dir, "USER": account.pw_name, "LOGNAME": account.pw_name,
                   "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                   "LIBGL_ALWAYS_SOFTWARE": "1", "GALLIUM_DRIVER": "llvmpipe", "MESA_LOADER_DRIVER_OVERRIDE": "llvmpipe",
                   "EGL_PLATFORM": "surfaceless"}
    code, native = execute(runner, environment)
    write_report(artifact_root / "renderer-native.json", native)
    need(code == 0, "I6_RENDERER_EXECUTION_FAILED")
    # GLVND has no usable vendor in this process. Merely changing Mesa's DRI
    # path is insufficient on releases with a linked software fallback.
    code, failed = execute(runner, {**environment, "__EGL_VENDOR_LIBRARY_FILENAMES": "/run/eidolon-no-egl.json"})
    unavailable = {"returncode": code, "status": failed.get("status"), "reason": failed.get("reason"),
                   "successful_receipts": len(failed.get("readbacks", []))}
    write_report(artifact_root / "renderer-unavailable.json", unavailable)
    need(source_snapshot(source_root) == source, "I6_SOURCE_MISMATCH")
    validation = validate(native, source, inputs, unavailable)
    packages = subprocess.check_output(["dpkg-query", "-W", "-f=${Package}=${Version}\n",
                                       "libegl1", "libegl-mesa0", "libgles2", "libgl1-mesa-dri"], text=True)
    environment_receipt = {"schema": 1, "source": source, "inputs": inputs,
                           "uid": os.getuid(), "renderer_packages": packages.splitlines(),
                           "environment": {k: environment[k] for k in ("LIBGL_ALWAYS_SOFTWARE", "GALLIUM_DRIVER", "MESA_LOADER_DRIVER_OVERRIDE", "EGL_PLATFORM")}}
    write_report(artifact_root / "renderer-environment.json", environment_receipt)
    write_report(artifact_root / "renderer-validation.json", validation)
    print(json.dumps(validation, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args.source_root, args.runner, args.artifact_root)
    except (OSError, ValueError, KeyError, TypeError, EvidenceError, subprocess.SubprocessError):
        print("I6_RENDERER_EXECUTION_FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
