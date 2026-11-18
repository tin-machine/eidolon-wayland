"""Six actual B4 states in an explicit unapproved candidate revision.

The B3 v1 exporter remains a four-state/single-session contract. This v2 packet
binds each image to one of two independently audited product sessions and saves
the exact original native/context bytes. It cannot approve or load a production
Golden. Reference acquisition and any future Acceptance run remain separate.
"""
from __future__ import annotations

import argparse
import copy
import html
import json
from pathlib import Path
import re

import linux_real_app_v3_evidence as evidence
import linux_real_app_v3_n07_b4_probe as probe
import linux_real_app_v3_n07_pixel as pixel
import linux_real_app_v3_n07_review as b3

REVISION = "eidolon-i6-n07-review-candidate-v2"
MAX_PACKET_BYTES = 8 * 1024 * 1024
SESSIONS = ("first", "second")
CHECKLIST = tuple(item for item in b3.CHECKLIST if not item.startswith("Collect both")) + (
    "Verify second-session numerals 1 through 8 against the fixed document and the actual new "
    "Mousepad process. Startup ON, file persistence and readable glyphs remain separate claims.",
    "Keep this acquisition's run/capture/workflow identities separate from future Acceptance "
    "runs. Neither acquired pixels nor their digest supply independent review authority.",
)


def _state(native, name, readback):
    r, p = readback["receipt"], readback["pixels"][0]
    return {
        "source": "host_renderer_readback", "session": "second",
        "semantic_status": "unreviewed_hypothesis",
        "capture_id": native["n07_second"]["capture_id"], "run_id": native["run_id"],
        "runtime": native["runtime"], "owner_id": native["owner_id"],
        "sdk_adapter": readback["sdk_adapter"], "target": r["target"],
        "create": r["create"], "renderer_owner": r["renderer_owner"],
        "context_epoch": r["context_epoch"], "request_seq": r["request_seq"],
        "present_seq": r["present_seq"], "content_frame": r["content_frame"],
        "min_content_frame": r["min_content_frame"], "snapshot_id": r["snapshot_id"],
        "rect": p["rect"], "rgba_b64": p["rgba_b64"], "rgba_sha256": p["sha256"],
        "roi_id": p["id"], "roi_sha256": r["roi_sha256"],
        "coordinate_space": "target_pixels_top_left", "phase": name,
        "geometry": r["geometry"], "logical_size": r["logical_size"],
        "content_rect": r["content_rect"], "layers": r["layers"],
        "backend_identity": probe._backend_identity(r["backend"]),
        "dynamic_measurements": {"max_swap_us": r["backend"]["max_swap_us"]},
    }


def _proposal(value, states):
    b3._exact(value, {"status", "basis", "states", "must_differ"}, "N07_B4_REVIEW_PROPOSAL")
    b3.need(value["status"] == "unreviewed" and value["basis"] in (
        "full_roi_exact_match_provisional", "manual_unreviewed_proposal"), "N07_B4_REVIEW_PROPOSAL")
    b3._exact(value["states"], set(pixel.STATES), "N07_B4_REVIEW_STATE_COVERAGE")
    masks = {}
    for name in pixel.STATES:
        item = value["states"][name]
        b3._exact(item, {"mask_b64", "max_channel_delta"}, "N07_B4_REVIEW_MASK")
        _, masks[name], _ = pixel._expected_state({"rect": states[name]["rect"],
            "rgba_b64": states[name]["rgba_b64"], **item})
    b3.need(type(value["must_differ"]) is list and len(value["must_differ"]) == 2,
            "N07_B4_REVIEW_CONTRAST")
    for rule, (a, b) in zip(value["must_differ"], pixel.NEGATIVE_PAIRS):
        b3._exact(rule, {"a", "b", "min_changed_pixels", "min_channel_delta"},
                  "N07_B4_REVIEW_CONTRAST")
        b3.need((rule["a"], rule["b"]) == (a, b) and masks[a] == masks[b]
                and type(rule["min_changed_pixels"]) is int
                and 1 <= rule["min_changed_pixels"] <= sum(masks[a])
                and type(rule["min_channel_delta"]) is int
                and 1 <= rule["min_channel_delta"] <= 128, "N07_B4_REVIEW_CONTRAST")


def _evidence_bytes(inputs, provenance, proposal):
    result = {"native.json": evidence.canonical(inputs["native"]),
              "supervisor.json": evidence.canonical(inputs["supervisor"]),
              "source.json": evidence.canonical(inputs["source"]),
              "provenance.json": evidence.canonical(provenance),
              "proposal.json": evidence.canonical(proposal)}
    for label in SESSIONS:
        result[label + "-native.json"] = inputs["native_raws"][label]
        result[label + "-context.json"] = inputs["contexts"][label]
        for category, suffix in (("environments", "environment"), ("fingerprints", "fingerprint")):
            result[label + "-" + suffix + ".json"] = evidence.canonical(inputs[category][label])
    return result


def build_candidate(native, supervisor, first, second, environments, fingerprints,
                    contexts, source, *, native_raws, provenance, proposal=None):
    inputs = dict(native=native, supervisor=supervisor, first=first, second=second,
                  environments=environments, fingerprints=fingerprints,
                  contexts=contexts, source=source, native_raws=native_raws)
    verdict = probe.validate_collection(**inputs)
    b3._provenance(provenance, source)
    states, extra = b3._observed(first, contexts["first"])
    second_rb = probe.second_readbacks(second)
    extra.update(probe.second_context(second, contexts["second"], second_rb))
    # The real startup/pre-confirmation images are supplementary evidence,
    # never renamed to the six canonical candidate states.
    extra.update({"second_startup_context": second_rb["before_open"],
                  "second_before_confirm_context": second_rb["before_open_confirm"]})
    states.update({name: _state(second, name, second_rb[name]) for name in pixel.STATES[4:]})
    chosen = b3.default_proposal(states) if proposal is None else copy.deepcopy(proposal)
    _proposal(chosen, states)
    mapping = {}
    for label in SESSIONS:
        session = verdict["sessions"][label]
        mapping[label] = {"nonce": session["capture"]["nonce"],
            "capture_id": session["capture"]["capture_id"], "runtime": session["runtime"],
            "owner_id": session["owner_id"], "storage_digest": supervisor["trusted_storage"]["storage_digest"],
            "native_file": label + "-native.json", "fingerprint_file": label + "-fingerprint.json",
            "states": list(pixel.STATES[:4] if label == "first" else pixel.STATES[4:]),
            "process": {key: fingerprints[label]["evidence"]["process"][key]
                        for key in ("pid", "start_ticks", "uid")}}
    raw_inputs = _evidence_bytes(inputs, provenance, proposal)
    missing = fingerprints["first"]["missing"]
    return {
        "schema": 2, "revision": REVISION,
        "status": "diagnostic_only_missing_fingerprint" if missing else "candidate_pending_review",
        "approval": {"status": "not_reviewed", "reviewer": None,
                     "review_commit": None, "approved_sha256": None},
        "host_v3_acceptance_passed": False, "verified_n07_assertions": [],
        "menu_identity_verified": False, "reviewed_golden": False, "production_compatible": False,
        "source": copy.deepcopy(source), "origin": copy.deepcopy(provenance),
        "run_id": native["run_id"], "sessions": mapping,
        "provenance_scope": "one_supervisor_two_sequential_product_sessions_exact_saved_bytes",
        "compatibility": {"prior_revision": b3.REVISION,
            "prior_revision_mutated": False, "production_revision": "eidolon-i6-n07-pixel-v1",
            "production_conversion_performed": False,
            "frame_order_scope": "within_actual_runtime_renderer_only"},
        "mousepad_source_mapping": {
            "commit": pixel.MOUSEPAD_COMMIT,
            "url": "https://github.com/xfce-mirror/mousepad/blob/" + pixel.MOUSEPAD_COMMIT
                   + "/mousepad/resources/gtk/menus.ui",
            "menu_label": "_View", "item_label": "Line N_umbers",
            "action": "app.preferences.view.show-line-numbers", "rendered_semantics_verified": False},
        "input_sha256": {name: evidence.sha(raw) for name, raw in raw_inputs.items()},
        "coverage": {"observed_states": list(pixel.STATES), "missing_states": [],
                     "observed_sessions": list(SESSIONS), "complete_six_state_acquisition": True,
                     "independently_reviewed_reference": False},
        "fingerprint_observation": copy.deepcopy(fingerprints),
        "fingerprint_missing": list(missing), "states": states, "diagnostic_contexts": extra,
        "document": copy.deepcopy(supervisor["document"]),
        "storage": copy.deepcopy(supervisor["trusted_storage"]),
        "keyfile_sequence": copy.deepcopy(supervisor["keyfile"]),
        "popup": {"first_initial": copy.deepcopy(first["n07_probe"]["popup"]),
                  "first_reopened": copy.deepcopy(first["n07_probe"]["b2"]["reopened_popup"]),
                  "second": copy.deepcopy(second["n07_second"]["popup"])},
        "settling": {"first": copy.deepcopy(first["n07_probe"]["b2"]["post_popup_settling"]),
                     "second": copy.deepcopy(second["n07_second"]["post_open_settling"])},
        "proposal": chosen, "diagnostic_verdict": verdict, "normal_cleanup_verified": True,
        "review_checklist": list(CHECKLIST),
    }


def validate_candidate(raw, expected_sha256, **inputs):
    b3.need(type(raw) is bytes and 0 < len(raw) <= MAX_PACKET_BYTES
            and b3._hex(expected_sha256, 64) and evidence.sha(raw) == expected_sha256,
            "N07_B4_REVIEW_DIGEST")
    candidate = evidence.parse(raw, limit=MAX_PACKET_BYTES)
    expected = build_candidate(**inputs)
    b3.need(raw == evidence.canonical(candidate)
            and raw == evidence.canonical(expected), "N07_B4_REVIEW_CANDIDATE_MISMATCH")
    return candidate


def ensure_independent(candidate, observed_run_id, observed_capture_id=None, *,
                       observed_capture_ids=None, workflow_run_id=None):
    b3.need(b3._hex(observed_run_id, 32)
            and not (observed_capture_id is not None and observed_capture_ids is not None),
            "N07_B4_REVIEW_OBSERVED_IDENTITY")
    captures = [observed_capture_id] if observed_capture_ids is None else observed_capture_ids
    b3.need(type(captures) is list and 1 <= len(captures) <= 2
            and all(b3._hex(value, 64) for value in captures)
            and len(set(captures)) == len(captures)
            and (workflow_run_id is None or b3._decimal(workflow_run_id)),
            "N07_B4_REVIEW_OBSERVED_IDENTITY")
    reference = {session["capture_id"] for session in candidate["sessions"].values()}
    b3.need(observed_run_id != candidate["run_id"] and not reference.intersection(captures)
            and (workflow_run_id is None or workflow_run_id != candidate["origin"]["workflow_run_id"]),
            "N07_OBSERVED_AS_EXPECTED")


def _assets(candidate):
    result = b3._assets(candidate)
    for name in probe.SECOND_CONTEXT_STATES:
        stitched = bytearray(512 * 256 * 4)
        for item in candidate["diagnostic_contexts"][name]["pixels"]:
            r = item["rect"]
            rgba = pixel._decode_b64(item["rgba_b64"], r["width"] * r["height"] * 4)
            for y in range(r["height"]):
                start = ((r["y"] + y) * 512 + r["x"]) * 4
                stitched[start:start + r["width"] * 4] = rgba[y * r["width"] * 4:(y + 1) * r["width"] * 4]
        result["aids/" + name + "-stitched.png"] = b3.png(bytes(stitched), 512, 256)
    return result


def _html(candidate, digest):
    e = html.escape
    parts = ["<!doctype html><html lang='en'><meta charset='utf-8'><title>N07 B4 unapproved six-state packet</title>",
        "<style>body{max-width:1200px;margin:2em auto;font:16px system-ui;background:#f5f5f5;color:#171717}"
        "section{background:white;padding:1em;margin:1em 0}img{max-width:100%;image-rendering:pixelated;border:1px solid #888}"
        "code,pre{overflow-wrap:anywhere;white-space:pre-wrap}table{border-collapse:collapse}td,th{padding:.5em;border:1px solid #aaa;text-align:left}</style>",
        "<h1>N07 B4: six acquired Renderer states — UNAPPROVED</h1>",
        "<p>Two sequential real product sessions reuse one independently held C1 storage root. "
        "Labels are hypotheses awaiting independent semantic review. N07 remains 0/13; Host acceptance false.</p>",
        f"<p>Packet integrity SHA-256: <code>{digest}</code>. This is not an approval pin.</p>",
        "<h2>Provenance</h2><pre>" + e(json.dumps(candidate["origin"], indent=2, sort_keys=True)) + "</pre>",
        "<h2>Actual session identities</h2><pre>" + e(json.dumps(candidate["sessions"], indent=2, sort_keys=True)) + "</pre>",
        "<p>Frame and request sequences are compared within each actual Runtime/Renderer only. "
        "The second session does not toggle Line Numbers.</p>",
        "<h2>Independent keyfile sequence</h2><pre>" + e(json.dumps(candidate["keyfile_sequence"], indent=2, sort_keys=True)) + "</pre>",
        "<h2>Fixed eight-line document</h2><pre>" + e(json.dumps(candidate["document"], indent=2, sort_keys=True)) + "</pre>",
        "<h2>Fingerprint observations</h2><p>Missing contract fields: <code>" +
        e(", ".join(candidate["fingerprint_missing"]) or "none") + "</code>. Configured theme inputs are not effective-theme observations.</p>",
        "<pre>" + e(json.dumps({label: {"status": value["status"], "values": value["values"], "missing": value["missing"],
                    "observation_errors": value["evidence"]["observation_errors"]}
                    for label, value in candidate["fingerprint_observation"].items()}, indent=2, sort_keys=True)) + "</pre>",
        "<h2>Independent review checklist</h2><ol>" + "".join("<li>" + e(v) + "</li>" for v in CHECKLIST) + "</ol>",
        "<p>Source mapping: <a href='" + e(candidate["mousepad_source_mapping"]["url"], quote=True)
        + "'>Mousepad menus.ui</a>. Rendered labels, checkmarks and numerals still require review.</p>",
        "<h2>Canonical candidate observations</h2><p>raw/*.rgba and raw/*.png preserve the original pixels. "
        "aids/* are review aids and are never expected-image inputs.</p>"]
    for name in pixel.STATES:
        state = candidate["states"][name]
        parts.extend(["<section><h3>" + name + "</h3><p>Session <code>" + state["session"]
            + "</code>, Runtime <code>" + e(state["runtime"]) + "</code>, frame <code>"
            + e(state["content_frame"]) + "</code>, RGBA SHA <code>" + state["rgba_sha256"] + "</code></p>",
            "<pre>" + e(json.dumps({key: state[key] for key in ("capture_id", "owner_id", "target", "create",
                "sdk_adapter", "renderer_owner", "context_epoch", "rect")}, sort_keys=True)) + "</pre>",
            f"<p><img src='raw/{name}.png' alt='{name} actual pixels'></p>",
            f"<details><summary>3× nearest-neighbor review aid</summary><img src='aids/{name}-3x.png' alt='Enlarged review aid'></details>",
            f"<details><summary>Unreviewed full ROI mask</summary><img src='aids/{name}-mask.png' alt='Proposed mask'></details></section>"])
    parts.append("<h2>OFF/ON diagnostic differences</h2>")
    for a, b in pixel.NEGATIVE_PAIRS:
        parts.append(f"<section><h3>{a} → {b}</h3><img src='aids/{a}--{b}-rgb-absolute-diff.png' alt='Absolute RGB difference'></section>")
    parts.append("<h2>Actual supplementary context</h2><p>Startup/pre-confirmation and context captures are separate from the six candidate states.</p>")
    stitched = set(b3.CONTEXT_STATES) | set(probe.SECOND_CONTEXT_STATES)
    for name, rb in sorted(candidate["diagnostic_contexts"].items()):
        parts.append("<section><h3>" + e(name) + "</h3>")
        if name in stitched:
            parts.append(f"<img src='aids/{name}-stitched.png' alt='Stitched diagnostic context'>")
        for item in rb["pixels"]:
            parts.append(f"<p>ROI {item['id']}</p><img src='context/{name}-roi-{item['id']}.png' alt='Original context crop'>")
        parts.append("</section>")
    parts.append("<h2>Approval remains separate</h2><p>This tool neither creates reviewer identity nor modifies "
        "APPROVED_SHA256. A separate independent review must resolve exact GUI semantics, missing environment "
        "observations, masks and tolerances. A later Acceptance run must have different run and capture identities.</p></html>")
    return "\n".join(parts).encode("utf-8")


def export_bundle(output: Path, *, provenance, proposal=None, **inputs):
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    (output / "evidence").mkdir(mode=0o700)
    raw_inputs = _evidence_bytes(inputs, provenance, proposal)
    for name, raw in raw_inputs.items():
        b3.need(type(raw) is bytes and 0 < len(raw) <= probe.MAX_CONTEXT_BYTES, "N07_B4_REVIEW_INPUT_BOUNDS")
        b3._write(output / "evidence" / name, raw)
    try:
        candidate = build_candidate(**inputs, provenance=provenance, proposal=proposal)
        raw = evidence.canonical(candidate)
        b3.need(len(raw) <= MAX_PACKET_BYTES, "N07_B4_REVIEW_PACKET_BOUNDS")
        digest = evidence.sha(raw)
        validate_candidate(raw, digest, **inputs, provenance=provenance, proposal=proposal)
        for name in ("raw", "aids", "context"):
            (output / name).mkdir(mode=0o700)
        for name, content in _assets(candidate).items():
            b3._write(output / name, content)
        b3._write(output / "candidate.json", raw)
        b3._write(output / "candidate.sha256", (digest + "\n").encode("ascii"))
        b3._write(output / "review.html", _html(candidate, digest))
        return {"status": candidate["status"], "candidate_sha256": digest,
                "states": list(pixel.STATES), "missing_states": [],
                "fingerprint_missing": candidate["fingerprint_missing"],
                "reviewed_golden": False, "verified_n07_assertions": [],
                "host_v3_acceptance_passed": False}
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        reason = str(error) if re.fullmatch(r"[A-Z0-9_]{1,100}", str(error)) else "N07_B4_REVIEW_REJECTED"
        b3._write(output / "diagnostic.json", evidence.canonical({"status": "rejected", "reason": reason,
            "candidate_created": False, "normal_cleanup_verified": False,
            "host_v3_acceptance_passed": False, "verified_n07_assertions": []}))
        raise


def _read_packet(path):
    # Native/context readers retain their original 1/4 MiB limits. Only the
    # v2 candidate aggregates six contexts and may reach the explicit 8 MiB cap.
    import os
    import stat
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        b3.need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and 0 < before.st_size <= MAX_PACKET_BYTES, "N07_B4_REVIEW_PACKET_BOUNDS")
        raw = bytearray()
        while len(raw) <= MAX_PACKET_BYTES:
            block = os.read(fd, min(65536, MAX_PACKET_BYTES + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
        after, named = os.fstat(fd), path.stat(follow_symlinks=False)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        b3.need(len(raw) == before.st_size and identity(before) == identity(after) == identity(named),
                "N07_B4_REVIEW_PACKET_RACE")
        return bytes(raw)
    finally:
        os.close(fd)


def _load_bundle(root):
    directory = root / "evidence"
    inputs = {}
    for key in ("native", "supervisor", "source", "provenance", "proposal"):
        raw = b3._read(directory / (key + ".json"))
        value = evidence.parse(raw, limit=probe.MAX_CONTEXT_BYTES)
        b3.need(raw == evidence.canonical(value), "N07_B4_REVIEW_INPUT_CANONICAL")
        inputs[key] = value
    inputs.update(environments={}, fingerprints={}, contexts={}, native_raws={})
    for label in SESSIONS:
        raw = b3._read(directory / (label + "-native.json"))
        inputs["native_raws"][label] = raw
        inputs[label] = evidence.parse(raw, limit=probe.MAX_NATIVE_BYTES)
        inputs["contexts"][label] = b3._read(directory / (label + "-context.json"))
        for category, suffix in (("environments", "environment"), ("fingerprints", "fingerprint")):
            raw = b3._read(directory / (label + "-" + suffix + ".json"))
            value = evidence.parse(raw)
            b3.need(raw == evidence.canonical(value), "N07_B4_REVIEW_INPUT_CANONICAL")
            inputs[category][label] = value
    return inputs


def validate_bundle(root: Path, expected_sha256, *, observed_run_id=None,
                    observed_capture_id=None, observed_capture_ids=None, workflow_run_id=None):
    inputs = _load_bundle(root)
    candidate = validate_candidate(_read_packet(root / "candidate.json"), expected_sha256, **inputs)
    b3.need(b3._read(root / "candidate.sha256") == (expected_sha256 + "\n").encode("ascii"),
            "N07_B4_REVIEW_DIGEST_FILE")
    for path, expected in _assets(candidate).items():
        b3.need(b3._read(root / path) == expected, "N07_B4_REVIEW_ASSET_CHANGED")
    b3.need(b3._read(root / "review.html") == _html(candidate, expected_sha256), "N07_B4_REVIEW_GUIDE_CHANGED")
    if any(value is not None for value in (observed_run_id, observed_capture_id, observed_capture_ids, workflow_run_id)):
        ensure_independent(candidate, observed_run_id, observed_capture_id,
                           observed_capture_ids=observed_capture_ids, workflow_run_id=workflow_run_id)
    return {"status": candidate["status"], "candidate_sha256": expected_sha256,
            "integrity_verified": True, "states": list(pixel.STATES), "reviewed_golden": False,
            "host_v3_acceptance_passed": False, "verified_n07_assertions": []}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--evidence-root", type=Path, required=True)
    export.add_argument("--provenance", type=Path, required=True)
    export.add_argument("--proposal", type=Path)
    export.add_argument("--output", type=Path, required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--bundle", type=Path, required=True)
    validate.add_argument("--expected-sha256", required=True)
    validate.add_argument("--observed-run-id")
    validate.add_argument("--observed-capture-id")
    validate.add_argument("--observed-workflow-run-id")
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            inputs = probe.load_collection(args.evidence_root)
            result = export_bundle(args.output, **inputs,
                provenance=evidence.parse(b3._read(args.provenance)),
                proposal=evidence.parse(b3._read(args.proposal)) if args.proposal else None)
        else:
            result = validate_bundle(args.bundle, args.expected_sha256,
                observed_run_id=args.observed_run_id, observed_capture_id=args.observed_capture_id,
                workflow_run_id=args.observed_workflow_run_id)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        print(json.dumps({"status": "rejected", "reason": "N07_B4_REVIEW_REJECTED",
                          "reviewed_golden": False, "host_v3_acceptance_passed": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
