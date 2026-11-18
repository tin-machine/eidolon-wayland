"""B4 packet tests isolate export integrity, never substitute for real GUI CI.

Only the full product collection verdict is patched here. Actual ticket,
context, settling, PNG bytes, candidate digest, masks and independence checks
run unpatched. The separate real CI reopens the whole collected proof without
this test seam and runs the saved-evidence mutations.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_real_app_v3_evidence as evidence
import linux_real_app_v3_n07_b4_probe as probe
import linux_real_app_v3_n07_b4_review as review
import linux_real_app_v3_n07_pixel as pixel
import linux_real_app_v3_n07_review as b3
from test_linux_real_app_v3_n07_b3 import decode_png
from test_linux_real_app_v3_n07_b4_probe import second_fixture, storage_fixture
from test_linux_real_app_v3_n07_fingerprint import fixture as fingerprint_fixture


def packet_inputs():
    second, context, old = second_fixture()
    first = old["native"]
    storage, _, _ = storage_fixture()
    fp, _, _, _ = fingerprint_fixture()
    first_fp, second_fp = copy.deepcopy(fp), copy.deepcopy(fp)
    second_fp["evidence"]["process"].update(pid=201, start_ticks=23456)
    verdict = {"status": "collected", "sessions": {label: {
        "capture": {"nonce": str(index) * 16, "capture_id": n[key]["capture_id"]},
        "runtime": n["runtime"], "owner_id": n["owner_id"]}
        for index, (label, n, key) in enumerate((
            ("first", first, "n07_probe"), ("second", second, "n07_second")), 1)},
        "host_v3_acceptance_passed": False, "verified_n07_assertions": []}
    inputs = {"native": {"run_id": first["run_id"]},
        "supervisor": {"document": {"line_count": 8, "fixture": "synthetic unit document"},
                       "trusted_storage": storage, "keyfile": {"fixture": "synthetic unit snapshots"}},
        "first": first, "second": second,
        "environments": {"first": old["environment"], "second": old["environment"]},
        "fingerprints": {"first": first_fp, "second": second_fp},
        "contexts": {"first": old["context_raw"], "second": context},
        "source": old["validation"]["source"],
        "native_raws": {"first": json.dumps(first, ensure_ascii=False, indent=1).encode("utf-8"),
                        "second": json.dumps(second, ensure_ascii=False, indent=2).encode("utf-8")},
        "provenance": old["provenance"], "proposal": None}
    return inputs, verdict


class ReviewV2Tests(unittest.TestCase):
    def test_explicit_revision_six_states_and_session_mapping(self):
        inputs, verdict = packet_inputs()
        with patch.object(probe, "validate_collection", return_value=verdict):
            candidate = review.build_candidate(**inputs)
        self.assertEqual(candidate["revision"], review.REVISION)
        self.assertEqual(candidate["schema"], 2)
        self.assertEqual(candidate["coverage"]["observed_states"], list(pixel.STATES))
        self.assertEqual(candidate["coverage"]["missing_states"], [])
        self.assertEqual(candidate["fingerprint_missing"], ["theme"])
        self.assertFalse(candidate["production_compatible"])
        self.assertFalse(candidate["reviewed_golden"])
        self.assertIsNone(candidate["approval"]["approved_sha256"])
        for name in pixel.STATES:
            state = candidate["states"][name]
            label = "first" if name.startswith("first_") else "second"
            self.assertEqual(state["session"], label)
            self.assertEqual(state["capture_id"], candidate["sessions"][label]["capture_id"])
            self.assertEqual(state["runtime"], candidate["sessions"][label]["runtime"])
        self.assertNotEqual(candidate["states"][pixel.STATES[2]]["rect"],
                            candidate["states"][pixel.STATES[4]]["rect"])
        self.assertEqual(b3.REVISION, "eidolon-i6-n07-review-candidate-v1")
        self.assertEqual(b3.STATES, pixel.STATES[:4])
        self.assertEqual(b3.MISSING_STATES, pixel.STATES[4:])

    def test_saved_bundle_preserves_raw_native_and_png_exact_bytes(self):
        inputs, verdict = packet_inputs()
        with tempfile.TemporaryDirectory() as temporary, patch.object(
                probe, "validate_collection", return_value=verdict):
            directory = Path(temporary) / "packet"
            result = review.export_bundle(directory, **inputs)
            checked = review.validate_bundle(directory, result["candidate_sha256"])
            self.assertTrue(checked["integrity_verified"])
            self.assertEqual(checked["states"], list(pixel.STATES))
            self.assertFalse(checked["reviewed_golden"])
            for label in ("first", "second"):
                self.assertEqual((directory / "evidence" / (label + "-native.json")).read_bytes(),
                                 inputs["native_raws"][label])
                self.assertEqual((directory / "evidence" / (label + "-context.json")).read_bytes(),
                                 inputs["contexts"][label])
            candidate = evidence.parse((directory / "candidate.json").read_bytes(),
                                       limit=review.MAX_PACKET_BYTES)
            for name in pixel.STATES:
                rgba = (directory / "raw" / (name + ".rgba")).read_bytes()
                width, height, decoded = decode_png((directory / "raw" / (name + ".png")).read_bytes())
                self.assertEqual(rgba, decoded)
                self.assertEqual(evidence.sha(rgba), candidate["states"][name]["rgba_sha256"])
                self.assertEqual((width, height), (candidate["states"][name]["rect"]["width"],
                                                candidate["states"][name]["rect"]["height"]))

    def test_packet_and_assets_cannot_be_rehashed_into_review_authority(self):
        inputs, verdict = packet_inputs()
        with tempfile.TemporaryDirectory() as temporary, patch.object(
                probe, "validate_collection", return_value=verdict):
            directory = Path(temporary) / "packet"
            result = review.export_bundle(directory, **inputs)
            path = directory / "raw" / "second_on_numbers_1_8.rgba"
            original = path.read_bytes()
            path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
            with self.assertRaisesRegex(ValueError, "N07_B4_REVIEW_ASSET_CHANGED"):
                review.validate_bundle(directory, result["candidate_sha256"])
            path.write_bytes(original)
            candidate = evidence.parse((directory / "candidate.json").read_bytes(),
                                       limit=review.MAX_PACKET_BYTES)
            candidate["reviewed_golden"] = True
            changed = evidence.canonical(candidate)
            with self.assertRaisesRegex(ValueError, "N07_B4_REVIEW_CANDIDATE_MISMATCH"):
                review.validate_candidate(changed, evidence.sha(changed), **inputs)

    def test_independence_rejects_either_capture_run_or_workflow_reuse(self):
        inputs, verdict = packet_inputs()
        with patch.object(probe, "validate_collection", return_value=verdict):
            candidate = review.build_candidate(**inputs)
        review.ensure_independent(candidate, "f" * 32, "f" * 64, workflow_run_id="888888")
        cases = (
            (candidate["run_id"], "f" * 64, None),
            ("f" * 32, candidate["sessions"]["first"]["capture_id"], None),
            ("f" * 32, candidate["sessions"]["second"]["capture_id"], None),
            ("f" * 32, "f" * 64, candidate["origin"]["workflow_run_id"]),
        )
        for run, capture, workflow in cases:
            with self.subTest(run=run, capture=capture), self.assertRaisesRegex(ValueError, "N07_OBSERVED_AS_EXPECTED"):
                review.ensure_independent(candidate, run, capture, workflow_run_id=workflow)
        with self.assertRaisesRegex(ValueError, "N07_OBSERVED_AS_EXPECTED"):
            review.ensure_independent(candidate, "f" * 32, observed_capture_ids=[
                "f" * 64, candidate["sessions"]["second"]["capture_id"]])

    def test_partial_or_failed_product_keeps_evidence_and_never_images(self):
        inputs, _ = packet_inputs()
        with tempfile.TemporaryDirectory() as temporary, patch.object(
                probe, "validate_collection", side_effect=probe.B4Invalid("N07_B4_SECOND_BEFORE_CLEANUP")):
            root = Path(temporary) / "packet"
            with self.assertRaisesRegex(ValueError, "N07_B4_SECOND_BEFORE_CLEANUP"):
                review.export_bundle(root, **inputs)
            self.assertTrue((root / "evidence" / "first-native.json").is_file())
            self.assertFalse((root / "candidate.json").exists())
            self.assertFalse((root / "raw").exists())
            diagnostic = json.loads((root / "diagnostic.json").read_text())
            self.assertFalse(diagnostic["normal_cleanup_verified"])
            self.assertFalse(diagnostic["candidate_created"])
            self.assertEqual(diagnostic["reason"], "N07_B4_SECOND_BEFORE_CLEANUP")

    def test_unapproved_six_state_packet_is_not_production_golden(self):
        inputs, verdict = packet_inputs()
        with patch.object(probe, "validate_collection", return_value=verdict):
            candidate = review.build_candidate(**inputs)
        raw = evidence.canonical(candidate)
        with self.assertRaises(ValueError):
            pixel._decode_fixture(raw, evidence.sha(raw))
        with self.assertRaisesRegex(ValueError, "N07_GOLDEN_UNREVIEWED"):
            pixel.load_reviewed()


if __name__ == "__main__":
    unittest.main()
