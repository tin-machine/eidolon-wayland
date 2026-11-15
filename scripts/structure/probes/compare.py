#!/usr/bin/env python3
"""Fixed-query retrieval comparison. Measures packets, NOT reviewer speed or accuracy."""
import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("eidolon_structure", ROOT / "scripts/structure.py")
structure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(structure)

QUESTIONS = [
    ("destroy", "save.receipt", 1, r"nativeDestroy|StopReceipt"),
    ("resume", "save.resume", 1, r"resumePublication|SAVE_OWNER_UNKNOWN"),
    ("journal", "proof.finalize", 2, r"finalize_reclaimed|publish_v3_finalized"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--questions", type=Path, help="frozen JSON question packet; defaults to the original three")
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if out.is_relative_to(ROOT):
        parser.error("output-dir must be outside the repository")
    os.umask(0o077)
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    packet = json.loads(args.questions.read_bytes()) if args.questions else None
    questions = packet["questions"] if packet else [
        {"id": name, "node": node, "hops": hops, "rg": pattern} for name, node, hops, pattern in QUESTIONS]
    if len({q["id"] for q in questions}) != len(questions) or any(
            not re.fullmatch(r"[a-z0-9-]+", q["id"]) for q in questions):
        parser.error("question IDs must be unique lowercase names")
    started = time.perf_counter()
    graph = structure.build(ROOT, "android-product")
    if packet and packet["revision"] != graph["revision"]:
        parser.error("question packet revision differs from source HEAD")
    build_seconds = time.perf_counter() - started
    results = []
    for question in questions:
        name, node, hops, pattern = (question[k] for k in ("id", "node", "hops", "rg"))
        command = ["rg", "-n", "--color", "never", "--glob", "*.kt", "--glob", "*.rs", "-e", pattern,
                   "android/app/src/main", "android/app/src/test", "native"]
        started = time.perf_counter()
        search_result = subprocess.run(command, cwd=ROOT, capture_output=True)
        if search_result.returncode not in (0, 1):
            raise RuntimeError(search_result.stderr.decode())
        search = search_result.stdout
        elapsed = time.perf_counter() - started
        lines = search.decode().splitlines()
        paths = sorted({line.split(":", 1)[0] for line in lines})
        slice_packet = structure.bounded_slice(graph, node, hops=hops, max_bytes=32000)
        assert slice_packet["complete"]
        indexed_paths = sorted({n["path"] for n in slice_packet["nodes"]})
        (out / f"{name}-rg.txt").write_bytes(search)
        (out / f"{name}-slice.json").write_bytes(structure.encoded(slice_packet))
        # File-size comparison is only a possible whole-file read cost, not measured consumption.
        required = set(question.get("required_sources", []))
        search_names = {Path(p).name for p in paths}
        slice_names = {Path(p).name for p in indexed_paths}
        results.append({"question": name, "prompt": question.get("question"), "query": pattern, "rg_command": command,
            "rg": {"bytes": len(search), "matching_lines": len(lines), "candidate_files": paths,
                   "seconds": round(elapsed, 6)},
            "slice": {"bytes": len(structure.encoded(slice_packet)), "nodes": len(slice_packet["nodes"]),
                      "edges": len(slice_packet["edges"]), "referenced_files": indexed_paths},
            "required_source_names": sorted(required),
            "required_missing_from_rg": sorted(required - search_names),
            "required_missing_from_slice": sorted(required - slice_names),
            "required_missing_from_union": sorted(required - search_names - slice_names),
            "same_evidence_files_in_rg_candidates": sorted(set(paths) & set(indexed_paths)),
            "evidence_files_not_in_initial_rg_candidates": sorted(set(indexed_paths) - set(paths)),
            "reading_source_still_required": True})
    candidates = [
        "android/app/src/main/java/io/github/tinmachine/eidolon/SdkResourceScope.kt",
        "android/app/src/main/java/io/github/tinmachine/eidolon/SpatialPanelAdapter.kt",
        "android/app/src/main/java/io/github/tinmachine/eidolon/WindowControls.kt",
        "android/app/src/main/java/io/github/tinmachine/eidolon/WindowModeBadge.kt",
        "android/app/src/main/java/io/github/tinmachine/eidolon/WindowMenuView.kt",
        "android/app/src/main/java/io/github/tinmachine/eidolon/NativeBridge.kt",
        "native/core/src/diagnostics.rs", "native/model/src/ids.rs", "native/model/src/commands.rs",
    ]
    registered = {f["path"] for f in graph["files"].values()}
    result = {"revision": graph["revision"], "graph_sha256": graph["artifact_sha256"],
              "generator_sha256": graph["generator_sha256"], "registry_sha256": graph["registry_sha256"],
              "graph_build_seconds": round(build_seconds, 6), "questions": results,
              "watch_files": graph["coverage"]["watched_files"],
              "boundary_review_files_missing_from_watch": [p for p in candidates if p not in graph["watch"]["inventory"]],
              "boundary_review_files_missing_from_registry": [p for p in candidates if p not in registered],
              "limits": [packet["limits"] if packet else "Known questions used to construct the registry; not an independent holdout benchmark",
                         "Single warm-context reviewer; no controlled reviewer-time or accuracy comparison",
                         "Timing is tool execution only; source/test reading and index maintenance are excluded",
                         "rg packets contain matching lines; slice packets contain curated claims and pointers. These are different products."]}
    (out / "comparison.json").write_bytes(structure.encoded(result))
    print(json.dumps({"questions": [{"id": r["question"], "rg_bytes": r["rg"]["bytes"],
        "rg_files": len(r["rg"]["candidate_files"]), "slice_bytes": r["slice"]["bytes"],
        "slice_files": len(r["slice"]["referenced_files"]),
        "initial_rg_missing_evidence_files": len(r["evidence_files_not_in_initial_rg_candidates"]),
        "required_missing_from_rg": r["required_missing_from_rg"],
        "required_missing_from_slice": r["required_missing_from_slice"],
        "required_missing_from_union": r["required_missing_from_union"]} for r in results],
        "unregistered_boundary_files": len(result["boundary_review_files_missing_from_registry"]),
        "unwatched_boundary_files": len(result["boundary_review_files_missing_from_watch"])}, indent=2))


if __name__ == "__main__":
    main()
