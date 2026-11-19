"""Six actual B4 states in an explicit unapproved candidate revision.

The B3 v1 exporter remains a four-state/single-session contract. The existing
v2 packet binds six images to the strict settings-v1 collection. A distinct v3
packet binds the same six-state acquisition to the explicit settings lifecycle
v2 contract; an old collection is never upgraded by inference. Both retain the
exact original native/context bytes and cannot approve or load a production
Golden. Reference acquisition and future Acceptance remain separate.
"""
from __future__ import annotations

import argparse
import copy
import html
import json
from pathlib import Path, PurePosixPath
import re

import linux_desktop_v3_contract as contract
import linux_real_app_v3_evidence as evidence
import linux_real_app_v3_n07_b4_probe as probe
import linux_real_app_v3_n07_pixel as pixel
import linux_real_app_v3_n07_review as b3

REVISION = "eidolon-i6-n07-review-candidate-v2"
LIFECYCLE_REVISION = "eidolon-i6-n07-review-candidate-v3"
SETTINGS_REVISION = "eidolon-i6-n07-settings-lifecycle-v2"
LIFECYCLE_COLLECTION_REVISION = "eidolon-i6-n07-b4-collection-v2"
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


def _candidate_contract(verdict, supervisor):
    """Select only the revision returned by independent collection validation."""
    revision = verdict.get("revision")
    b3.need(revision in (probe.COLLECTION_REVISION, LIFECYCLE_COLLECTION_REVISION),
            "N07_B4_REVIEW_COLLECTION_REVISION")
    if revision == probe.COLLECTION_REVISION:
        b3.need("settings_revision" not in verdict and "settings_evidence" not in verdict
                and "settings_revision" not in supervisor and "settings_observations" not in supervisor,
                "N07_B4_REVIEW_SETTINGS_REVISION")
        return {}
    b3.need(verdict.get("settings_revision") == SETTINGS_REVISION
            and supervisor.get("settings_revision") == SETTINGS_REVISION
            and type(verdict.get("settings_evidence")) is dict
            and type(supervisor.get("settings_observations")) is dict
            and supervisor["settings_observations"]
            and type(supervisor.get("supplemental_keyfile")) is dict,
            "N07_B4_REVIEW_SETTINGS_REVISION")
    return {
        "settings_contract": {"revision": SETTINGS_REVISION,
                              "evidence": copy.deepcopy(verdict["settings_evidence"])},
    }


def _public_storage_stat(value, *, control=False, relative=None):
    """Copy only bounded numeric fixture stat fields, never an input object."""
    fields = ("dev", "ino", "uid", "mode") + (
        ("nlink", "size", "mtime_ns", "ctime_ns") if control else ())
    b3._exact(value, set(fields) | ({"relative"} if relative is not None else set()),
              "N07_B4_REVIEW_STORAGE_FIELDS")
    result = {}
    for name in fields:
        number = value[name]
        minimum = 1 if name in ("dev", "ino", "nlink", "mtime_ns", "ctime_ns") else 0
        maximum = 0o7777 if name == "mode" else (1 << 63) - 1
        b3.need(type(number) is int and minimum <= number <= maximum,
                "N07_B4_REVIEW_STORAGE_STAT")
        result[name] = number
    if relative is not None:
        result["relative"] = relative
    probe.same(result, value, "N07_B4_REVIEW_STORAGE_PROJECTION")
    return result


def _public_storage_evidence(value):
    """Reconstruct the public fixture receipt after full collection validation.

    The live storage owner holds FDs and private observer state. Its receipt
    is a separate exact schema of contract identities and bounded stat facts;
    copying that owner or an arbitrary metadata dictionary is not an export
    boundary. Keep every existing wire field and reject extra fields rather
    than dropping or normalizing them. The product/storage oracle remains
    responsible for joining these facts to both actual sessions.
    """
    b3._exact(value, {"record", "storage_digest", "uid", "passwd_home", "root_path",
                      "root_identity", "owner", "owner_sha256", "directories",
                      "control_files", "control_files_final"}, "N07_B4_REVIEW_STORAGE_FIELDS")
    record = contract.StorageRecord.parse(contract.canonical_json(value["record"]))
    owner = contract.StorageOwner.parse(contract.canonical_json(value["owner"]))
    record_fields, owner_fields = record.record(), owner.record()
    b3.need(all(record_fields[name] == owner_fields[name]
                for name in ("installation_id", "profile_storage_id")),
            "N07_B4_REVIEW_STORAGE_PROJECTION")
    uid = owner_fields["uid"]
    root_path = "/home/eidolon-app/.local/share/eidolon/apps/" + record_fields["installation_id"] + "/" \
                + record_fields["profile_storage_id"] + "/" + contract.EDITOR
    root = _public_storage_stat(value["root_identity"])
    b3.need(root["uid"] == uid and root["mode"] == 0o700, "N07_B4_REVIEW_STORAGE_STAT")
    names = list(PurePosixPath(root_path).parts)
    names[-1] = root_path
    b3.need(type(value["directories"]) is list and len(value["directories"]) == len(names),
            "N07_B4_REVIEW_STORAGE_FIELDS")
    directories = [_public_storage_stat(item, relative=name)
                   for item, name in zip(value["directories"], names)]
    b3.need(all(item["uid"] == (0 if index < 2 else uid) and item["mode"] & 0o022 == 0
                for index, item in enumerate(directories)), "N07_B4_REVIEW_STORAGE_STAT")
    probe.same({name: directories[-1][name] for name in root}, root,
               "N07_B4_REVIEW_STORAGE_PROJECTION")
    controls = {}
    for field in ("control_files", "control_files_final"):
        b3._exact(value[field], {"owner.json", "profile.lock"}, "N07_B4_REVIEW_STORAGE_FIELDS")
        controls[field] = {}
        for name in ("owner.json", "profile.lock"):
            stamp = _public_storage_stat(value[field][name], control=True)
            b3.need(stamp["uid"] == uid and stamp["mode"] == 0o600 and stamp["nlink"] == 1
                    and stamp["size"] == (len(owner.canonical()) if name == "owner.json" else 0),
                    "N07_B4_REVIEW_STORAGE_STAT")
            controls[field][name] = stamp
    probe.same(controls["control_files"], controls["control_files_final"],
               "N07_B4_REVIEW_STORAGE_PROJECTION")
    result = {"record": record_fields, "storage_digest": record.sha256(), "uid": uid,
              "passwd_home": "/home/eidolon-app", "root_path": root_path,
              "root_identity": root, "owner": owner_fields, "owner_sha256": owner.sha256(),
              "directories": directories, **controls}
    probe.same(result, value, "N07_B4_REVIEW_STORAGE_PROJECTION")
    return result


def build_candidate(native, supervisor, first, second, environments, fingerprints,
                    contexts, source, *, native_raws, provenance, proposal=None):
    inputs = dict(native=native, supervisor=supervisor, first=first, second=second,
                  environments=environments, fingerprints=fingerprints,
                  contexts=contexts, source=source, native_raws=native_raws)
    verdict = probe.validate_collection(**inputs)
    storage = _public_storage_evidence(supervisor["trusted_storage"])
    settings_contract = _candidate_contract(verdict, supervisor)
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
            "owner_id": session["owner_id"], "storage_digest": storage["storage_digest"],
            "native_file": label + "-native.json", "fingerprint_file": label + "-fingerprint.json",
            "states": list(pixel.STATES[:4] if label == "first" else pixel.STATES[4:]),
            "process": {key: fingerprints[label]["evidence"]["process"][key]
                        for key in ("pid", "start_ticks", "uid")}}
    raw_inputs = _evidence_bytes(inputs, provenance, proposal)
    missing = fingerprints["first"]["missing"]
    return {
        "schema": 3 if settings_contract else 2,
        "revision": LIFECYCLE_REVISION if settings_contract else REVISION,
        "status": "diagnostic_only_missing_fingerprint" if missing else "candidate_pending_review",
        "approval": {"status": "not_reviewed", "reviewer": None,
                     "review_commit": None, "approved_sha256": None},
        "host_v3_acceptance_passed": False, "verified_n07_assertions": [],
        "menu_identity_verified": False, "reviewed_golden": False, "production_compatible": False,
        "source": copy.deepcopy(source), "origin": copy.deepcopy(provenance),
        "run_id": native["run_id"], "sessions": mapping,
        "provenance_scope": "one_supervisor_two_sequential_product_sessions_exact_saved_bytes",
        "compatibility": {"prior_revision": REVISION if settings_contract else b3.REVISION,
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
        "storage": storage,
        "keyfile_sequence": copy.deepcopy(supervisor["keyfile"]),
        **settings_contract,
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
    if "settings_contract" in candidate:
        parts.extend([
            "<h2>Explicit settings lifecycle contract</h2><p>This acquisition uses the versioned "
            "settings lifecycle predicate. The original strict settings-v1 contract and its failed "
            "acquisitions remain distinct; no legacy receipt was upgraded. Automatic UI state is "
            "checked against exact keys, types and product lifecycle evidence.</p>",
            "<pre>" + e(json.dumps(candidate["settings_contract"], indent=2, sort_keys=True)) + "</pre>",
            "<p>The exact same-read settings observations and supplemental keyfile snapshots are saved "
            "in <code>evidence/supervisor.json</code>, covered by the candidate's input digest and "
            "independently reconstructed during bundle verification.</p>"])
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
    try:
        candidate = build_candidate(**inputs, provenance=provenance, proposal=proposal)
        raw = evidence.canonical(candidate)
        b3.need(len(raw) <= MAX_PACKET_BYTES, "N07_B4_REVIEW_PACKET_BOUNDS")
        digest = evidence.sha(raw)
        validate_candidate(raw, digest, **inputs, provenance=provenance, proposal=proposal)
        # Public candidate inputs are written only after both reconstruction
        # passes. Product partial receipts and rejected diagnostic exports
        # retain their separate preservation paths; invalid public metadata
        # must not be copied first and rejected after publication.
        raw_inputs = _evidence_bytes(inputs, provenance, proposal)
        for name, content in raw_inputs.items():
            b3.need(type(content) is bytes and 0 < len(content) <= probe.MAX_CONTEXT_BYTES,
                    "N07_B4_REVIEW_INPUT_BOUNDS")
            b3._write(output / "evidence" / name, content)
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
    # six-state candidates aggregate contexts within the explicit 8 MiB cap.
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


REJECTED_REVISION = "eidolon-i6-n07-b4-rejected-renderer-diagnostics-v1"
REJECTED_LIFECYCLE_REVISION = "eidolon-i6-n07-b4-rejected-renderer-diagnostics-v2"


def _rejection(call):
    try:
        call()
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        reason = str(error)
        return reason if re.fullmatch(r"(?:N07|I6)_[A-Z0-9_]{1,100}", reason) else "N07_B4_DIAGNOSTIC_REJECTED"
    return None


def _rejected_observed(inputs, provenance):
    """Audit the acquired pixels while preserving every rejected disposition.

    This function is deliberately separate from candidate construction.
    Neither source normalization nor diagnostic rendering changes native
    error/complete fields or supplies the missing settings/cleanup verdict.
    """
    b3._provenance(provenance, inputs["source"])
    rejection = _rejection(lambda: probe.validate_collection(**inputs))
    b3.need(rejection is not None, "N07_B4_DIAGNOSTIC_REQUIRES_REJECTION")
    native, supervisor = inputs["native"], inputs["supervisor"]
    collection_revision = probe.collection_revision(native, supervisor)
    lifecycle = collection_revision == LIFECYCLE_COLLECTION_REVISION
    b3.need(type(inputs.get("second")) is dict
            and inputs["second"].get("schema") == (probe.SECOND_SCHEMA_V2 if lifecycle else probe.SECOND_SCHEMA),
            "N07_B4_DIAGNOSTIC_REVISION")
    probe.exact(native, {"schema", "revision", "run_id", "source_sha256", "build_source", "sessions", "barriers",
                         "error", "reviewed_golden", "menu_identity_verified", "verified_n07_assertions",
                         "host_v3_acceptance_passed"})
    probe.exact(supervisor, {"schema", "revision", "run_id", "source", "trusted_storage", "document", "keyfile",
                             "supplemental_keyfile", "barriers", "fingerprint_observers", "failures", "external_rescue",
                             "final_census"} | ({"keyfile_diagnostics"} if "keyfile_diagnostics" in supervisor else set())
                | ({"settings_revision", "settings_observations"} if lifecycle else set()))
    b3.need(native["reviewed_golden"] is False and native["menu_identity_verified"] is False
            and native["verified_n07_assertions"] == [] and native["host_v3_acceptance_passed"] is False
            and native["run_id"] == supervisor["run_id"]
            and native["error"] in (None, "I6_N07_B4_SUPERVISOR_REFUSED")
            and type(supervisor["external_rescue"]) is bool and type(supervisor["failures"]) is list
            and all(type(value) is str and re.fullmatch(r"(?:N07|I6)_[A-Z0-9_]{1,100}", value)
                    for value in supervisor["failures"]), "N07_B4_DIAGNOSTIC_PROMOTION")
    evidence._source(inputs["source"])
    b3.need(inputs["source"]["dirty"] is False and inputs["source"]["build_features"] == []
            and native["source_sha256"] == inputs["source"]["tree_sha256"], "N07_B4_SOURCE")
    probe.same(native["build_source"], {**inputs["source"], "build_features": ["host-acceptance"]}, "N07_B4_SOURCE")
    probe.same(supervisor["source"], inputs["source"], "N07_B4_SOURCE")
    b3.need(len(native["sessions"]) == 2, "N07_B4_DIAGNOSTIC_SESSION_MISSING")
    sessions, retirement = {}, {}
    for label, summary in zip(SESSIONS, native["sessions"]):
        n, raw, env = inputs[label], inputs["native_raws"][label], inputs["environments"][label]
        probe.exact(summary, {"session", "runtime", "owner_id", "native_file", "native_sha256", "native_bytes",
                              "context_file", "review_context", "error", "cleanup_error", "product_cleanup_complete"})
        b3.need(type(raw) is bytes and 0 < len(raw) <= probe.MAX_NATIVE_BYTES
                and summary["session"] == label and summary["native_file"] == label + "-native.json"
                and summary["native_sha256"] == evidence.sha(raw) and summary["native_bytes"] == len(raw)
                and n["run_id"] == native["run_id"] and n["runtime"] == summary["runtime"]
                and n["owner_id"] == summary["owner_id"]
                and n["host_v3_acceptance_passed"] is False and n["n07_assertions_verified"] == []
                and type(summary["native_bytes"]) is int and type(summary["product_cleanup_complete"]) is bool
                and summary["error"] == n["error"] and summary["cleanup_error"] == n["cleanup_error"]
                and summary["context_file"] == label + "-context.json",
                "N07_B4_DIAGNOSTIC_NATIVE_BINDING")
        probe.same(summary["review_context"], n["review_context"], "N07_B4_DIAGNOSTIC_CONTEXT_BINDING")
        probe.same(evidence.parse(raw, limit=probe.MAX_NATIVE_BYTES), n, "N07_B4_NATIVE_RAW_CHANGED")
        probe.same(n["build_source"], {**inputs["source"], "build_features": ["host-acceptance"]}, "N07_B4_SOURCE")
        b3.need(n["source_sha256"] == inputs["source"]["tree_sha256"], "N07_B4_SOURCE")
        hashes = evidence.source_input_hashes()
        probe.same({key: n[key] for key in hashes}, hashes, "N07_B4_SOURCE")
        # N01's environment *shape/source* check permits nonzero observed
        # counts. It does not declare cleanup; those counts remain displayed.
        normalized = copy.deepcopy(env)
        normalized.pop("driver_control")
        if normalized["revision"] == "eidolon-i6-n07-b4-partial-environment-v1":
            b3.need(normalized.pop("observation_phase") == "after_driver_exit", "N07_B4_DIAGNOSTIC_ENVIRONMENT")
        else:
            b3.need(normalized["revision"] == "eidolon-i6-n07-b4-environment-v1", "N07_B4_DIAGNOSTIC_ENVIRONMENT")
        normalized["revision"] = probe.n01.ENVIRONMENT_REVISION
        probe.n01._environment(normalized, n["run_id"], n["owner_id"], n["source_sha256"])
        _, capture, context, _, _ = probe.n02.transport(n, normalized, "mousepad_editor")
        owner = evidence.sha((n["run_id"] + ":" + n["runtime"] + ":" + capture["capture_id"]).encode("ascii"))[:32]
        b3.need(owner == n["owner_id"], "N07_B4_DIAGNOSTIC_OWNER")
        sessions[label] = {"capture": capture, "context": context.record(),
                           "runtime": n["runtime"], "owner_id": owner}
        failure = _rejection(lambda: probe.validate_retirement(n, env, inputs["source"], label))
        retirement[label] = {"product_cleanup_verified": failure is None, "rejection": failure,
                             "native_error": n["error"], "cleanup_error": n["cleanup_error"],
                             "final_census": copy.deepcopy(env["final"])}
        binding = n["review_context"]
        context_raw = inputs["contexts"][label]
        b3.need(type(context_raw) is bytes and 0 < len(context_raw) <= probe.MAX_CONTEXT_BYTES
                and binding["bytes"] == len(context_raw) and binding["sha256"] == evidence.sha(context_raw),
                "N07_B4_DIAGNOSTIC_CONTEXT_BINDING")
    first, second = sessions["first"], sessions["second"]
    b3.need(first["runtime"] != second["runtime"] and first["owner_id"] != second["owner_id"]
            and all(first["capture"][key] != second["capture"][key] for key in ("nonce", "capture_id")),
            "N07_B4_SESSION_REUSED")
    # The first successful product retirement is the gate for even creating
    # the actual second Runtime, including in a rejected acquisition.
    probe.validate_session(inputs["first"], inputs["environments"]["first"], inputs["source"],
                           "first", inputs["contexts"]["first"])
    host_gate = [row for row in native["barriers"] if row["request"]["phase"] == "first_retired"]
    outer_gate = [row for row in supervisor["barriers"] if row["request"]["phase"] == "first_retired"]
    b3.need(len(host_gate) == len(outer_gate) == 1, "N07_B4_DIAGNOSTIC_GATE_MISSING")
    a, b = host_gate[0], outer_gate[0]
    probe.same(a["request"], b["request"])
    probe.same(a["response"], b["response"])
    b3.need(a["error"] is None and a["response"]["allow"] is True
            and b["observation"]["product_cleanup_verified"] is True
            and a["request_sha256"] == b["request_sha256"] == evidence.sha(evidence.canonical(a["request"]))
            and b["observation_sha256"] == a["response"]["observation_sha256"]
            == evidence.sha(evidence.canonical(b["observation"])), "N07_B4_DIAGNOSTIC_GATE_INVALID")
    probe.same(a["request"]["capture"], first["capture"])
    probe.same(b["observation"]["census"], inputs["environments"]["first"]["final"])
    creation = probe.n02.only(probe.n02.ordinal_events(inputs["second"]["observations"], limit=128),
                              "n07_b4_runtime_created")[1]["data"]
    b3.need(type(creation["started_at_ns"]) is int and type(a["response_monotonic_ns"]) is int
            and creation["runtime"] == second["runtime"] and creation["session"] == "second"
            and creation["started_at_ns"] > a["response_monotonic_ns"], "N07_B4_SECOND_BEFORE_CLEANUP")
    probe._storage(supervisor["trusted_storage"], sessions, inputs["environments"])
    probe._keyfile_diagnostics(supervisor)
    first_states, _ = b3._observed(inputs["first"], inputs["contexts"]["first"])
    second_rb = probe.second_readbacks(inputs["second"])
    second_contexts = probe.diagnostic_second_context(inputs["second"], inputs["contexts"]["second"], second_rb)
    probe._open(inputs["second"], probe.n02.ordinal_events(inputs["second"]["observations"], limit=128))
    probe._pixel_events(inputs["second"], {**second_rb, **second_contexts})
    probe._fingerprints(inputs["fingerprints"], {label: inputs[label] for label in SESSIONS},
                        inputs["environments"], inputs["source"])
    states = {**first_states, **{name: _state(inputs["second"], name, second_rb[name]) for name in pixel.STATES[4:]}}
    for name, state in states.items():
        state["session"] = "first" if name.startswith("first_") else "second"
    keys = supervisor["keyfile"]
    settings_rejection = _rejection(lambda: probe.validate_settings(supervisor,
        first=inputs["first"], second=inputs["second"])) if lifecycle else _rejection(
            lambda: probe.settings.validate_file_sequence(
                keys["before_first_launch"], keys["after_first_toggle"], keys["second_session"]))
    document = supervisor["document"]
    first_barrier = next(row for row in supervisor["barriers"] if row["request"]["phase"] == "first_before_launch")
    probe._document(document, supervisor["trusted_storage"], first_barrier, inputs["second"])
    return {"rejection": rejection, "settings_rejection": settings_rejection, "states": states,
            "sessions": sessions, "retirement": retirement,
            **({"settings_revision": supervisor["settings_revision"]} if lifecycle else {})}


def _build_rejected(inputs, provenance):
    observed = _rejected_observed(inputs, provenance)
    raw_inputs = _evidence_bytes(inputs, provenance, None)
    lifecycle = observed.get("settings_revision") == SETTINGS_REVISION
    return {"schema": 2 if lifecycle else 1,
            "revision": REJECTED_LIFECYCLE_REVISION if lifecycle else REJECTED_REVISION,
            "status": "diagnostic_only_rejected_collection",
            "candidate_created": False, "full_collection_valid": False,
            "reviewed_golden": False, "menu_identity_verified": False,
            "verified_n07_assertions": [], "host_v3_acceptance_passed": False,
            "run_id": inputs["native"]["run_id"], "source": inputs["source"], "origin": provenance,
            "inputs": {name: {"sha256": evidence.sha(raw), "bytes": len(raw)} for name, raw in sorted(raw_inputs.items())},
            "keyfile": inputs["supervisor"]["keyfile"], "supplemental_keyfile": inputs["supervisor"]["supplemental_keyfile"],
            "keyfile_diagnostics": inputs["supervisor"].get("keyfile_diagnostics", {}),
            "fingerprints": {label: {"missing": inputs["fingerprints"][label]["missing"],
                                      "values": inputs["fingerprints"][label]["values"]} for label in SESSIONS},
            "document": inputs["supervisor"]["document"],
            "external_rescue": inputs["supervisor"]["external_rescue"], **observed}


def _rejected_assets(packet):
    assets = {}
    for name in pixel.STATES:
        state = packet["states"][name]
        width, height = state["rect"]["width"], state["rect"]["height"]
        rgba = pixel._decode_b64(state["rgba_b64"], width * height * 4)
        assets["raw/" + name + ".rgba"] = rgba
        assets["raw/" + name + ".png"] = b3.png(rgba, width, height)
    return assets


def _rejected_html(packet, digest):
    esc = html.escape
    blocks = ["<!doctype html><html lang='en'><meta charset='utf-8'><title>Rejected B4 acquisition diagnostics</title>",
        "<style>body{max-width:1100px;margin:2em auto;font:16px system-ui}section{border:1px solid #aaa;padding:1em;margin:1em 0}img{image-rendering:pixelated;max-width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>",
        "<h1>REJECTED B4 acquisition — diagnostic images only</h1>",
        "<p>No candidate was created. This failed collection cannot be a reference or production Golden. "
        "Image labels remain unreviewed hypotheses. N07 verified assertions remain empty; Host acceptance remains false.</p>",
        "<p>Integrity digest: " + esc(digest) + "</p>",
        "<h2>Observed failure and cleanup</h2><pre>" + esc(json.dumps({key: packet[key] for key in (
            "rejection", "settings_rejection", "retirement", "external_rescue")}, sort_keys=True, indent=2)) + "</pre>",
        "<h2>Source and storage observations</h2><pre>" + esc(json.dumps({key: packet[key] for key in (
            "source", "origin", "sessions", "keyfile", "supplemental_keyfile", "keyfile_diagnostics", "document", "fingerprints")},
            sort_keys=True, indent=2)) + "</pre>"]
    if "settings_revision" in packet:
        blocks.append("<h2>Settings contract revision</h2><p><code>" + esc(packet["settings_revision"])
            + "</code>. Exact same-read lifecycle inputs remain in <code>evidence/supervisor.json</code>. "
            "This rejected packet does not replace the old strict contract or create review authority.</p>")
    for name in pixel.STATES:
        state = packet["states"][name]
        blocks += ["<section><h2>" + esc(name) + "</h2><img alt='Unapproved actual Renderer ROI' src='raw/" + name +
                   ".png' width='" + str(state["rect"]["width"] * 3) + "'><pre>" +
                   esc(json.dumps({k: v for k, v in state.items() if k != "rgba_b64"}, sort_keys=True, indent=2)) + "</pre></section>"]
    return ("\n".join(blocks) + "</html>\n").encode("utf-8")


def export_rejected_bundle(output: Path, *, provenance, **inputs):
    packet = _build_rejected(inputs, provenance)
    raw = evidence.canonical(packet)
    b3.need(len(raw) <= MAX_PACKET_BYTES, "N07_B4_REVIEW_PACKET_BOUNDS")
    digest = evidence.sha(raw)
    output.mkdir(mode=0o700)
    (output / "evidence").mkdir(mode=0o700)
    (output / "raw").mkdir(mode=0o700)
    for name, content in _evidence_bytes(inputs, provenance, None).items():
        b3.need(type(content) is bytes and 0 < len(content) <= probe.MAX_CONTEXT_BYTES, "N07_B4_REVIEW_INPUT_BOUNDS")
        b3._write(output / "evidence" / name, content)
    for name, content in _rejected_assets(packet).items():
        b3._write(output / name, content)
    b3._write(output / "diagnostic.json", raw)
    b3._write(output / "diagnostic.sha256", (digest + "\n").encode("ascii"))
    b3._write(output / "review.html", _rejected_html(packet, digest))
    return {"status": packet["status"], "diagnostic_sha256": digest, "candidate_created": False,
            "full_collection_valid": False, "states": list(pixel.STATES), "rejection": packet["rejection"],
            "settings_rejection": packet["settings_rejection"], "reviewed_golden": False,
            "verified_n07_assertions": [], "host_v3_acceptance_passed": False}


def validate_rejected_bundle(root: Path, expected_digest):
    inputs = _load_bundle(root)
    provenance = inputs.pop("provenance")
    b3.need(inputs.pop("proposal") is None, "N07_B4_DIAGNOSTIC_PROPOSAL")
    b3.need(not (root / "candidate.json").exists() and not (root / "candidate.sha256").exists(),
            "N07_B4_DIAGNOSTIC_PROMOTION")
    raw = _read_packet(root / "diagnostic.json")
    b3.need(b3._hex(expected_digest, 64) and evidence.sha(raw) == expected_digest,
            "N07_B4_DIAGNOSTIC_DIGEST")
    packet = _build_rejected(inputs, provenance)
    b3.need(raw == evidence.canonical(packet), "N07_B4_DIAGNOSTIC_CHANGED")
    b3.need(b3._read(root / "diagnostic.sha256") == (expected_digest + "\n").encode("ascii"),
            "N07_B4_DIAGNOSTIC_DIGEST")
    for name, content in _rejected_assets(packet).items():
        b3.need(b3._read(root / name) == content, "N07_B4_REVIEW_ASSET_CHANGED")
    b3.need(b3._read(root / "review.html") == _rejected_html(packet, expected_digest), "N07_B4_REVIEW_GUIDE_CHANGED")
    return {"status": packet["status"], "diagnostic_sha256": expected_digest, "integrity_verified": True,
            "candidate_created": False, "full_collection_valid": False, "states": list(pixel.STATES),
            "reviewed_golden": False, "verified_n07_assertions": [], "host_v3_acceptance_passed": False}


def adversarial_rejected_checks(*, provenance, **inputs):
    _build_rejected(inputs, provenance)

    def foreign_context(data):
        value = evidence.parse(data["contexts"]["second"], limit=probe.MAX_CONTEXT_BYTES)
        value["capture_id"] = data["first"]["n07_probe"]["capture_id"]
        raw = evidence.canonical(value)
        data["contexts"]["second"] = raw
        data["second"]["review_context"].update(bytes=len(raw), sha256=evidence.sha(raw))
        data["native"]["sessions"][1]["review_context"] = copy.deepcopy(data["second"]["review_context"])

    def foreign_source(data):
        hashes = data["second"]["compiled_source_hashes"]
        hashes[next(iter(hashes))] = "f" * 64

    mutations = (
        ("first_relabel", lambda d: d["second"]["n07_second"].update(
            second_on_checkmark=copy.deepcopy(d["first"]["n07_probe"]["b2"]["first_on_checkmark"]))),
        ("foreign_create", lambda d: d["second"]["n07_second"]["second_on_checkmark"]["readback"]["receipt"].update(create="999999")),
        ("foreign_renderer", lambda d: d["second"]["n07_second"]["second_on_checkmark"]["readback"]["receipt"].update(renderer_owner=999999)),
        ("integer_frame", lambda d: d["second"]["n07_second"]["second_on_checkmark"]["readback"]["receipt"].update(content_frame=8)),
        ("stale_main", lambda d: d["second"]["n07_second"]["post_open_settling"].update(main_content_seq="1")),
        ("fingerprint_mismatch", lambda d: d["fingerprints"]["second"]["values"].update(gtk_version="0.0.0")),
        ("cleanup_gate_removed", lambda d: d["supervisor"]["barriers"][2]["observation"].update(product_cleanup_verified=False)),
        ("capture_reuse", lambda d: d["second"]["n07_second"].update(capture_id=d["first"]["n07_probe"]["capture_id"])),
        ("foreign_incomplete_context", foreign_context),
        ("foreign_compiled_source", foreign_source),
        ("fake_approval", lambda d: d["native"].update(reviewed_golden=True)),
    )
    rejected = []
    for name, mutate in mutations:
        changed = copy.deepcopy(inputs)
        mutate(changed)
        for label, summary in zip(SESSIONS, changed["native"]["sessions"]):
            raw = evidence.canonical(changed[label])
            changed["native_raws"][label] = raw
            summary["native_sha256"], summary["native_bytes"] = evidence.sha(raw), len(raw)
        b3.need(_rejection(lambda: _build_rejected(changed, provenance)) is not None, "N07_B4_DIAGNOSTIC_MUTATION_ACCEPTED")
        rejected.append(name)
    return {"status": "rejected_all", "mutations": rejected, "candidate_created": False,
            "full_collection_valid": False, "verified_n07_assertions": []}


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
