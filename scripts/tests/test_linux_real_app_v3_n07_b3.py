"""B3 packet integrity/provenance tests. Synthetic pixels are not a golden."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_real_app_v3_n07_review as review
import linux_real_app_v3_n07_pixel as pixel
from linux_real_app_v3_n07_b2_probe import inspect
from test_linux_real_app_v3_n07_b2 import candidate as b2_native


def complete_ticket(rb):
    r = rb["receipt"]
    r["request_seq"] = int(r["content_frame"]) * 10
    r["present_seq"] = int(r["content_frame"]) * 10
    r["snapshot_id"] = ":".join((str(r["renderer_owner"]), r["target"]["session"],
                                  r["target"]["window"], r["content_frame"]))
    r["geometry"] = {"origin": {"x": 26, "y": 23},
                     "size": {"width": 1024, "height": 768}}
    r["logical_size"] = {"width": 1024, "height": 768}
    r["content_rect"] = {"origin": [0, 0], "size": [1024, 768]}
    for layer in r["layers"]:
        layer.update(content_seq=r["content_frame"],
                     buffer_size={"width": 1024, "height": 768},
                     offset={"x": 0, "y": 0},
                     clip={"origin": {"x": 0, "y": 0},
                           "size": {"width": 1024, "height": 768}},
                     scale=1, transform="Normal")
    r["rois"] = [{"roi": {"id": p["id"], **p["rect"]}} for p in rb["pixels"]]
    raw = bytearray(struct.pack("<I", 1))
    for roi in r["rois"]:
        raw.extend(struct.pack("<5I", *(roi["roi"][k] for k in (
            "id", "x", "y", "width", "height"))))
    r["roi_sha256"] = review.sha(raw)


def inputs():
    n = b2_native()
    n["owner_id"] = "d" * 32
    source = {"commit": "1" * 40, "tree_sha256": "2" * 64,
              "dirty": False, "pr_head": None, "build_features": []}
    n["source_sha256"] = source["tree_sha256"]
    n["build_source"] = {**source, "build_features": ["host-acceptance"]}
    for stage in (n["n07_probe"]["before_menu"], n["n07_probe"]["menu_open"],
                  *(n["n07_probe"]["b2"][k] for k in review.STATES[1:])):
        complete_ticket(stage["readback"])
    e = {"schema": 1, "revision": "eidolon-i6-n07-b2-environment-v1",
         "run_id": n["run_id"], "source_sha256": source["tree_sha256"],
         "runner": {"source_sha256": source["tree_sha256"], "binary_sha256": "3" * 64},
         "before": {"bundle_sha256": "4" * 64, "inventory_sha256": "5" * 64},
         "final": {"app_process_count": 0, "driver_child_count": 0,
                   "sshd_child_count": 0, "journal_entries": 0,
                   "journal_lock_held": False, "profile_lock_held": False}}
    e["after"] = copy.deepcopy(e["before"])
    v = {"source": source, "status": "probe_only", "probe_status": "probe_only",
         "host_v3_acceptance_passed": False, "verified_n07_assertions": [],
         "exit_code": 0, "reason": "N07_B2_DIAGNOSTIC_ONLY", "probe": inspect(n)}
    p = {"repository": review.REPOSITORY, "workflow_run_id": "123456",
         "run_attempt": "1", "artifact_id": "456789", "artifact_name": "test-only-b2",
         "artifact_archive_sha256": "6" * 64, "source_commit": source["commit"],
         "pr_head": "7" * 40, "artifact_status": "downloaded"}
    return dict(native=n, environment=e, validation=v, provenance=p)


def add_context(data):
    native = data["native"]
    stages = {}
    for index, name in enumerate(review.CONTEXT_STATES):
        canonical = (native["n07_probe"]["before_menu"] if index == 0
                     else native["n07_probe"]["b2"][review.STATES[index]])
        rb = copy.deepcopy(canonical["readback"])
        rb["pixels"] = []
        for uid, x, y in ((101, 0, 0), (102, 256, 0), (103, 0, 128), (104, 256, 128)):
            rgba = bytes((0, 0, uid, 255)) * (256 * 128)
            rb["pixels"].append({"id": uid, "rect": {
                "x": x, "y": y, "width": 256, "height": 128},
                "rgba_b64": base64.b64encode(rgba).decode("ascii"), "sha256": review.sha(rgba)})
        rb["receipt"]["pixel_sha256"] = [p["sha256"] for p in rb["pixels"]]
        complete_ticket(rb)
        rb["receipt"]["request_seq"] += 1
        rb["receipt"]["present_seq"] += 1
        stages[name] = {"phase": name, "readback": rb}
    sidecar = {"schema": 1, "revision": "eidolon-i6-n07-b3-review-context-v1",
               "source": "host_renderer_review_context",
               **{k: native[k] for k in ("run_id", "runtime", "owner_id", "source_sha256")},
               "capture_id": native["n07_probe"]["capture_id"],
               "target": native["n07_probe"]["main_target"], "complete": True,
               "states": stages, "error": None, "cleanup_error": None}
    data["context_raw"] = review.canonical(sidecar)
    native["review_context"] = {"schema": 1, "sha256": review.sha(data["context_raw"]),
                               "bytes": len(data["context_raw"]),
                               "states": list(review.CONTEXT_STATES), "complete": True}


def decode_png(raw):
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    offset, compressed, dimensions = 8, bytearray(), None
    while offset < len(raw):
        size = struct.unpack(">I", raw[offset:offset + 4])[0]
        kind, body = raw[offset + 4:offset + 8], raw[offset + 8:offset + 8 + size]
        crc = struct.unpack(">I", raw[offset + 8 + size:offset + 12 + size])[0]
        assert crc == zlib.crc32(kind + body) & 0xffffffff
        if kind == b"IHDR":
            dimensions = struct.unpack(">2I5B", body)
        elif kind == b"IDAT":
            compressed.extend(body)
        offset += size + 12
    w, h, depth, color, compression, filtering, interlace = dimensions
    assert (depth, color, compression, filtering, interlace) == (8, 6, 0, 0, 0)
    scan = zlib.decompress(compressed)
    assert len(scan) == h * (w * 4 + 1)
    assert all(scan[y * (w * 4 + 1)] == 0 for y in range(h))
    return w, h, b"".join(scan[y * (w * 4 + 1) + 1:(y + 1) * (w * 4 + 1)] for y in range(h))


class ReviewTests(unittest.TestCase):
    def reject_input(self, mutate):
        data = inputs()
        mutate(data)
        with self.assertRaises((ValueError, TypeError, KeyError)):
            review.build_candidate(**data)

    def test_saved_receipt_export_is_exact_and_deterministic(self):
        data = inputs()
        candidate = review.build_candidate(**data)
        self.assertEqual(candidate["status"], "diagnostic_only_missing_fingerprint")
        self.assertEqual(candidate["coverage"]["missing_states"], list(pixel.STATES[4:]))
        self.assertFalse(candidate["production_compatible"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "packet"
            result = review.export_bundle(root, **data)
            checked = review.validate_bundle(root, result["candidate_sha256"])
            self.assertTrue(checked["integrity_verified"])
            self.assertFalse(checked["reviewed_golden"])
            for name, state in candidate["states"].items():
                raw = (root / "raw" / (name + ".rgba")).read_bytes()
                self.assertEqual(review.sha(raw), state["rgba_sha256"])
                w, h, decoded = decode_png((root / "raw" / (name + ".png")).read_bytes())
                self.assertEqual((w, h), (state["rect"]["width"], state["rect"]["height"]))
                self.assertEqual(raw, decoded)
            other = Path(directory) / "repeat"
            repeated = review.export_bundle(other, **data)
            self.assertEqual(result, repeated)
            self.assertEqual((root / "review.html").read_bytes(), (other / "review.html").read_bytes())

    def test_bytes_sha_roi_missing_and_wrong_types_rejected(self):
        mutations = (
            lambda d: d["native"]["n07_probe"]["b2"]["first_on_gutter"]["readback"]["pixels"][0].update(rgba_b64="AAAA"),
            lambda d: d["native"]["n07_probe"]["b2"]["first_on_gutter"]["readback"]["pixels"][0].update(sha256="0" * 64),
            lambda d: d["native"]["n07_probe"]["before_menu"]["readback"]["pixels"][0]["rect"].update(x=False),
            lambda d: d["native"]["n07_probe"]["before_menu"]["readback"]["pixels"][0].update(id=True),
            lambda d: d["native"]["n07_probe"]["before_menu"]["readback"]["receipt"].update(roi_revision=True),
            lambda d: d["native"]["n07_probe"]["before_menu"]["readback"]["receipt"].update(roi_sha256="f" * 64),
            lambda d: d["native"]["n07_probe"]["before_menu"]["readback"]["receipt"]["rois"][0]["roi"].update(x=1),
            lambda d: d["native"]["n07_probe"]["before_menu"]["readback"]["receipt"].pop("request_seq"),
            lambda d: d["native"]["n07_probe"]["before_menu"]["readback"]["receipt"].update(snapshot_id="different"),
            lambda d: d["native"]["n07_probe"]["b2"].update(schema=True),
            lambda d: d["native"]["n07_probe"]["b2"]["item_pointer"]["after"].update(pressed=False),
            lambda d: d["native"].update(destroy_status=False),
        )
        for i, mutation in enumerate(mutations):
            with self.subTest(i=i):
                self.reject_input(mutation)

    def test_capture_target_sdk_and_owner_cannot_be_mixed(self):
        for key, value in (("runtime", "99"), ("create", "99"),
                           ("renderer_owner", 99), ("context_epoch", 99)):
            with self.subTest(key=key):
                self.reject_input(lambda d: d["native"]["n07_probe"]["b2"]["first_on_checkmark"]
                    ["readback"]["receipt"].update({key: value}))
        self.reject_input(lambda d: d["native"]["n07_probe"].update(capture_id="f" * 64))
        self.reject_input(lambda d: d["native"]["n07_probe"]["b2"]["first_on_checkmark"]
            ["readback"].update(sdk_adapter="99"))
        self.reject_input(lambda d: d["native"]["n07_probe"]["b2"]["first_on_checkmark"]
            ["readback"]["receipt"]["target"].update(generation="99"))
        self.reject_input(lambda d: d["environment"].update(run_id="f" * 32))
        self.reject_input(lambda d: d["environment"]["runner"].update(source_sha256="f" * 64))

    def test_frame_string_and_ticket_order_are_typed(self):
        for value in (True, 24, 24.0, "24.0", "024", "0", "22"):
            with self.subTest(value=value):
                self.reject_input(lambda d: d["native"]["n07_probe"]["b2"]["first_on_checkmark"]
                    ["readback"]["receipt"].update(content_frame=value))
        for field, value in (("request_seq", 220), ("present_seq", 220), ("request_seq", True)):
            self.reject_input(lambda d: d["native"]["n07_probe"]["b2"]["first_on_checkmark"]
                ["readback"]["receipt"].update({field: value}))

    def test_pending_artifact_is_explicit_not_invented(self):
        data = inputs()
        data["provenance"].update(artifact_status="pending_upload", artifact_id=None,
                                  artifact_archive_sha256=None)
        candidate = review.build_candidate(**data)
        self.assertIsNone(candidate["origin"]["artifact_id"])
        self.assertNotEqual(candidate["origin"]["source_commit"], candidate["origin"]["pr_head"])
        self.reject_input(lambda d: d["provenance"].update(artifact_status="pending_upload"))
        self.reject_input(lambda d: d["provenance"].update(source_commit="e" * 40))

    def test_unapproved_candidate_cannot_load_as_production_golden(self):
        candidate = review.build_candidate(**inputs())
        self.assertIsNone(pixel.APPROVED_SHA256)
        with self.assertRaises(pixel.PixelEvidenceError):
            pixel.load_reviewed()
        raw = review.canonical(candidate)
        with self.assertRaises(pixel.PixelEvidenceError):
            pixel._decode_fixture(raw, review.sha(raw))
        self.assertEqual(candidate["approval"], {"status": "not_reviewed", "reviewer": None,
            "review_commit": None, "approved_sha256": None})

    def test_self_reference_run_capture_and_workflow_are_rejected(self):
        candidate = review.build_candidate(**inputs())
        with self.assertRaisesRegex(review.ReviewError, "N07_OBSERVED_AS_EXPECTED"):
            review.ensure_independent(candidate, candidate["run_id"], "f" * 64)
        with self.assertRaisesRegex(review.ReviewError, "N07_OBSERVED_AS_EXPECTED"):
            review.ensure_independent(candidate, "f" * 32, candidate["capture_id"])
        with self.assertRaisesRegex(review.ReviewError, "N07_OBSERVED_AS_EXPECTED"):
            review.ensure_independent(candidate, "f" * 32, "f" * 64, workflow_run_id="123456")
        review.ensure_independent(candidate, "f" * 32, "f" * 64, workflow_run_id="123457")
        for invalid in (123456, True, 123456.0, "0123456"):
            with self.assertRaisesRegex(review.ReviewError, "N07_REVIEW_OBSERVED_IDENTITY"):
                review.ensure_independent(candidate, "f" * 32, "f" * 64, workflow_run_id=invalid)

    def test_partial_fingerprint_is_bound_and_never_silently_complete(self):
        import linux_real_app_v3_n07_fingerprint as fp
        from test_linux_real_app_v3_n07_fingerprint import fixture
        data = inputs()
        record = fixture()[0]
        data["environment"]["app"] = {"uid": 12002}
        for phase in ("before", "after"):
            data["environment"][phase]["pins"] = {"waypipe_sha256": "4" * 64}
        record["binding"] = fp._binding(data["native"], data["environment"], data["validation"]["source"])
        record["evidence"]["renderer"] = fp._renderer(data["native"])
        record["values"] = fp._derive(record, data["native"], data["environment"], data["validation"]["source"])
        data["fingerprint"] = record
        candidate = review.build_candidate(**data)
        self.assertEqual(candidate["status"], "diagnostic_only_missing_fingerprint")
        self.assertEqual(candidate["fingerprint_observation"]["missing"], ["theme"])
        for mutate in (
            lambda r: r.update(status="complete", missing=[]),
            lambda r: r["values"].update(theme="Adwaita"),
            lambda r: r["values"].pop("gtk_version"),
            lambda r: r["binding"].update(capture_id="f" * 64),
            lambda r: r["values"].update(font_sha256="f" * 64),
            lambda r: r["evidence"]["libraries"].pop("pango"),
        ):
            altered = copy.deepcopy(data)
            mutate(altered["fingerprint"])
            with self.assertRaises(ValueError):
                review.build_candidate(**altered)

    def test_second_session_is_never_synthesized_or_promoted(self):
        data = inputs()
        candidate = review.build_candidate(**data)
        self.assertEqual(set(candidate["states"]), set(review.STATES))
        self.assertEqual(candidate["coverage"]["observed_sessions"], ["first"])
        self.reject_input(lambda d: d["native"]["n07_probe"]["b2"].update(
            second_on_checkmark=d["native"]["n07_probe"]["b2"]["first_on_checkmark"]))
        candidate["states"]["second_on_checkmark"] = candidate["states"]["first_on_checkmark"]
        raw = review.canonical(candidate)
        with self.assertRaisesRegex(review.ReviewError, "N07_REVIEW_CANDIDATE_MISMATCH"):
            review.validate_candidate(raw, review.sha(raw), **data)

    def test_mask_tolerance_fingerprint_require_new_integrity_digest(self):
        data = inputs()
        original = review.build_candidate(**data)
        digest = review.sha(review.canonical(original))
        mutations = (
            lambda c: c["proposal"]["states"][review.STATES[0]].update(max_channel_delta=1),
            lambda c: c["proposal"]["states"][review.STATES[0]].update(mask_b64="AAAA"),
            lambda c: c["proposal"]["must_differ"][0].update(min_changed_pixels=1),
            lambda c: c["fingerprint_observation"].update(missing=[]),
            lambda c: c["approval"].update(reviewer="invented", status="approved"),
        )
        for i, mutate in enumerate(mutations):
            with self.subTest(i=i):
                candidate = copy.deepcopy(original)
                mutate(candidate)
                with self.assertRaisesRegex(review.ReviewError, "N07_REVIEW_DIGEST_MISMATCH"):
                    review.validate_candidate(review.canonical(candidate), digest, **data)
                with self.assertRaises(ValueError):
                    review.validate_candidate(review.canonical(candidate),
                                              review.sha(review.canonical(candidate)), **data)

    def test_proposal_validation_does_not_choose_masks_from_deltas(self):
        data = inputs()
        candidate = review.build_candidate(**data)
        proposal = copy.deepcopy(candidate["proposal"])
        self.assertEqual(proposal["basis"], "full_roi_exact_match_provisional")
        proposal["states"][review.STATES[0]]["max_channel_delta"] = True
        data["proposal"] = proposal
        with self.assertRaises(ValueError):
            review.build_candidate(**data)
        proposal["states"][review.STATES[0]]["max_channel_delta"] = 33
        with self.assertRaises(ValueError):
            review.build_candidate(**data)

    def test_failure_preserves_receipts_without_candidate_or_golden(self):
        data = inputs()
        data["native"]["cleanup_error"] = "cleanup failed"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "failed"
            with self.assertRaises(ValueError):
                review.export_bundle(root, **data)
            self.assertTrue((root / "evidence/native.json").is_file())
            self.assertFalse((root / "candidate.json").exists())
            self.assertFalse((root / "raw").exists())
            diagnostic = json.loads((root / "diagnostic.json").read_text())
            self.assertEqual(diagnostic["status"], "rejected")
            self.assertFalse(diagnostic["normal_cleanup_verified"])

    def test_image_tampering_and_symlinks_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "packet"
            result = review.export_bundle(root, **inputs())
            path = root / "raw/first_off_gutter.png"
            raw = path.read_bytes()
            path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
            with self.assertRaises(ValueError):
                review.validate_bundle(root, result["candidate_sha256"])
            path.unlink()
            path.symlink_to(root / "raw/first_on_gutter.png")
            with self.assertRaises(OSError):
                review.validate_bundle(root, result["candidate_sha256"])

    def test_full_context_sidecar_is_raw_digest_bound_and_diagnostic_only(self):
        data = inputs()
        add_context(data)
        c = review.build_candidate(**data)
        self.assertEqual(set(c["states"]), set(review.STATES))
        self.assertEqual(set(c["diagnostic_contexts"]), {"initial_menu_context", *review.CONTEXT_STATES})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "packet"
            result = review.export_bundle(root, **data)
            review.validate_bundle(root, result["candidate_sha256"])
            self.assertEqual((root / "evidence/context.json").read_bytes(), data["context_raw"])
        for mutate in (
            lambda d: d.update(context_raw=d["context_raw"] + b"\n"),
            lambda d: d.update(context_raw=None),
            lambda d: d["native"]["review_context"].update(complete=False),
            lambda d: d["native"]["review_context"].update(bytes=True),
        ):
            altered = copy.deepcopy(data)
            mutate(altered)
            with self.assertRaises(ValueError):
                review.build_candidate(**altered)

    def test_context_cross_owner_stale_ticket_and_second_state_rejected(self):
        for mutation in (
            lambda c: c.update(capture_id="f" * 64),
            lambda c: c.update(owner_id="f" * 32),
            lambda c: c["states"][review.CONTEXT_STATES[0]]["readback"]["receipt"].update(request_seq=1),
            lambda c: c["states"]["first_on_menu_context"]["readback"]["receipt"]
                ["layers"][1].update(surface="99"),
            lambda c: c["states"]["first_on_menu_context"]["readback"]["receipt"]
                ["geometry"]["origin"].update(x=999),
            lambda c: c["states"]["first_on_menu_context"]["readback"]["receipt"]
                ["layers"][1]["offset"].update(x=999),
            lambda c: c["states"]["first_on_menu_context"]["readback"]["receipt"]
                ["backend"].update(max_swap_us=0),
            lambda c: c["states"].update(second_on_context=c["states"][review.CONTEXT_STATES[0]]),
        ):
            data = inputs()
            add_context(data)
            sidecar = json.loads(data["context_raw"])
            mutation(sidecar)
            data["context_raw"] = review.canonical(sidecar)
            data["native"]["review_context"].update(sha256=review.sha(data["context_raw"]),
                                                  bytes=len(data["context_raw"]))
            with self.assertRaises(ValueError):
                review.build_candidate(**data)


if __name__ == "__main__":
    unittest.main()
