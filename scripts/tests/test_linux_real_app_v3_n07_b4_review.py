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
    verdict = {"status": "collected", "revision": probe.COLLECTION_REVISION, "sessions": {label: {
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


def lifecycle_packet_inputs():
    """Synthetic collection-verdict seam; no product/lifecycle success claim."""
    inputs, verdict = packet_inputs()
    inputs["native"].update(schema=1, revision="eidolon-i6-n07-b4-host-v2")
    phases = ("first_before_launch", "first_after_toggle", "first_retired", "second_before_launch",
              "second_before_open", "second_after_observation", "second_retired")
    inputs["supervisor"].update(
        schema=1, revision="eidolon-i6-n07-b4-supervisor-v2",
        settings_revision=review.SETTINGS_REVISION,
        settings_observations={phase: {"fixture": "synthetic unit same-read observation"}
                               for phase in phases},
        supplemental_keyfile={phase: {"fixture": "synthetic unit supplemental proof"}
                              for phase in ("first_retired", "second_before_launch",
                                            "second_before_open", "second_retired")})
    verdict.update(revision=review.LIFECYCLE_COLLECTION_REVISION,
        settings_revision=review.SETTINGS_REVISION,
        settings_evidence={"fixture": "synthetic unit validated lifecycle",
                           "phase_sha256": {phase: evidence.sha(evidence.canonical(
                               inputs["supervisor"]["settings_observations"][phase])) for phase in phases}})
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

    def test_rejected_packet_preserves_six_images_without_a_candidate(self):
        inputs, _ = packet_inputs()
        provenance = inputs.pop("provenance")
        inputs.pop("proposal")
        first, _ = b3._observed(inputs["first"], inputs["contexts"]["first"])
        second = probe.second_readbacks(inputs["second"])
        states = {**first, **{name: review._state(inputs["second"], name, second[name]) for name in pixel.STATES[4:]}}
        observed = {"rejection": "N07_B4_COLLECTION_INCOMPLETE", "settings_rejection": "N07_UNEXPECTED_SETTING_CHANGE",
                    "states": states, "sessions": {"synthetic": True}, "retirement": {"synthetic": True}}
        inputs["supervisor"].update(supplemental_keyfile={}, external_rescue=False)
        # This test isolates the preservation layer; real collected inputs
        # exercise _rejected_observed and semantic mutations unmocked in CI.
        with tempfile.TemporaryDirectory() as temporary, patch.object(review, "_rejected_observed", return_value=observed):
            root = Path(temporary) / "rejected"
            result = review.export_rejected_bundle(root, **inputs, provenance=provenance)
            checked = review.validate_rejected_bundle(root, result["diagnostic_sha256"])
            self.assertTrue(checked["integrity_verified"])
            self.assertFalse(checked["full_collection_valid"])
            self.assertFalse(checked["candidate_created"])
            self.assertFalse((root / "candidate.json").exists())
            self.assertEqual(checked["states"], list(pixel.STATES))
            for name in pixel.STATES:
                raw = (root / "raw" / (name + ".rgba")).read_bytes()
                self.assertEqual(decode_png((root / "raw" / (name + ".png")).read_bytes())[2], raw)
            for label in ("first", "second"):
                self.assertEqual((root / "evidence" / (label + "-native.json")).read_bytes(), inputs["native_raws"][label])
            asset = root / "raw" / "second_on_numbers_1_8.png"
            asset.write_bytes(asset.read_bytes() + b"changed")
            with self.assertRaisesRegex(ValueError, "N07_B4_REVIEW_ASSET_CHANGED"):
                review.validate_rejected_bundle(root, result["diagnostic_sha256"])

    def test_successful_collection_cannot_be_reclassified_as_rejected(self):
        inputs, _ = packet_inputs()
        provenance = inputs.pop("provenance")
        inputs.pop("proposal")
        with patch.object(probe, "validate_collection", return_value={"status": "collected"}), \
                self.assertRaisesRegex(ValueError, "N07_B4_DIAGNOSTIC_REQUIRES_REJECTION"):
            review._rejected_observed(inputs, provenance)


class ReviewV3LifecycleTests(unittest.TestCase):
    def test_rejected_diagnostic_cannot_relabel_a_second_native_revision(self):
        for lifecycle_packet in (False, True):
            inputs, _ = lifecycle_packet_inputs() if lifecycle_packet else packet_inputs()
            provenance = inputs.pop("provenance")
            inputs.pop("proposal")
            if not lifecycle_packet:
                inputs["native"].update(schema=1, revision=probe.NATIVE_REVISION)
                inputs["supervisor"].update(schema=1, revision=probe.SUPERVISOR_REVISION)
                inputs["second"]["schema"] = probe.SECOND_SCHEMA_V2
            else:
                inputs["second"]["schema"] = probe.SECOND_SCHEMA
            with self.subTest(lifecycle_packet=lifecycle_packet), self.assertRaisesRegex(
                    ValueError, "N07_B4_DIAGNOSTIC_REVISION"):
                review._rejected_observed(inputs, provenance)

    def test_explicit_new_revision_retains_six_state_and_unapproved_boundaries(self):
        inputs, verdict = lifecycle_packet_inputs()
        with patch.object(probe, "validate_collection", return_value=verdict):
            candidate = review.build_candidate(**inputs)
        self.assertEqual((candidate["schema"], candidate["revision"]), (3, review.LIFECYCLE_REVISION))
        self.assertEqual(candidate["compatibility"]["prior_revision"], review.REVISION)
        self.assertFalse(candidate["compatibility"]["prior_revision_mutated"])
        self.assertEqual(candidate["settings_contract"], {
            "revision": review.SETTINGS_REVISION, "evidence": verdict["settings_evidence"]})
        self.assertEqual(candidate["coverage"]["observed_states"], list(pixel.STATES))
        self.assertEqual(candidate["fingerprint_missing"], ["theme"])
        self.assertFalse(candidate["reviewed_golden"])
        self.assertFalse(candidate["host_v3_acceptance_passed"])
        self.assertEqual(candidate["verified_n07_assertions"], [])
        self.assertIsNone(candidate["approval"]["approved_sha256"])
        self.assertIn(b"Explicit settings lifecycle contract", review._html(candidate, "f" * 64))
        # The source observations live once in evidence/supervisor.json;
        # their bytes are bound by input_sha256 instead of silently projected.
        self.assertNotIn("settings_observations", candidate)
        self.assertEqual(candidate["input_sha256"]["supervisor.json"],
                         evidence.sha(evidence.canonical(inputs["supervisor"])))
        verdict["settings_evidence"]["phase_sha256"]["second_before_open"] = "f" * 64
        self.assertNotEqual(candidate["settings_contract"]["evidence"], verdict["settings_evidence"])

    def test_missing_unknown_or_mixed_revision_cannot_select_a_contract(self):
        mutations = (
            lambda i, v: v.pop("revision"),
            lambda i, v: v.update(revision="eidolon-i6-n07-b4-collection-v999"),
            lambda i, v: v.update(revision=probe.COLLECTION_REVISION),
            lambda i, v: v.pop("settings_revision"),
            lambda i, v: v.update(settings_revision="eidolon-i6-n07-settings-v1"),
            lambda i, v: v.pop("settings_evidence"),
            lambda i, v: i["supervisor"].pop("settings_revision"),
            lambda i, v: i["supervisor"].update(settings_revision="eidolon-i6-n07-settings-v1"),
            lambda i, v: i["supervisor"].pop("settings_observations"),
            lambda i, v: i["supervisor"].update(settings_observations={}),
        )
        for index, mutate in enumerate(mutations):
            inputs, verdict = lifecycle_packet_inputs()
            mutate(inputs, verdict)
            with self.subTest(index=index), patch.object(probe, "validate_collection", return_value=verdict), \
                    self.assertRaisesRegex(ValueError, "N07_B4_REVIEW_(COLLECTION|SETTINGS)_REVISION"):
                review.build_candidate(**inputs)

    def test_saved_lifecycle_inputs_and_revision_are_bound_after_rehash(self):
        inputs, verdict = lifecycle_packet_inputs()
        with tempfile.TemporaryDirectory() as temporary, patch.object(
                probe, "validate_collection", return_value=verdict):
            root = Path(temporary) / "packet"
            result = review.export_bundle(root, **inputs)
            self.assertTrue(review.validate_bundle(root, result["candidate_sha256"])["integrity_verified"])
            candidate = evidence.parse((root / "candidate.json").read_bytes(),
                                       limit=review.MAX_PACKET_BYTES)
            for mutate in (
                lambda c: c.update(schema=2, revision=review.REVISION),
                lambda c: c["settings_contract"].update(revision="eidolon-i6-n07-settings-v1"),
                lambda c: c["settings_contract"]["evidence"]["phase_sha256"].update(second_before_open="f" * 64),
                lambda c: c.update(reviewed_golden=True),
            ):
                changed = copy.deepcopy(candidate)
                mutate(changed)
                raw = evidence.canonical(changed)
                with self.assertRaisesRegex(ValueError, "N07_B4_REVIEW_CANDIDATE_MISMATCH"):
                    review.validate_candidate(raw, evidence.sha(raw), **inputs)
            supervisor_path = root / "evidence" / "supervisor.json"
            supervisor = evidence.parse(supervisor_path.read_bytes())
            supervisor["settings_observations"].pop("second_before_open")
            supervisor_path.write_bytes(evidence.canonical(supervisor))
            with self.assertRaisesRegex(ValueError, "N07_B4_REVIEW_CANDIDATE_MISMATCH"):
                review.validate_bundle(root, result["candidate_sha256"])

    def test_new_revision_does_not_grant_self_reference_or_golden_authority(self):
        inputs, verdict = lifecycle_packet_inputs()
        with patch.object(probe, "validate_collection", return_value=verdict):
            candidate = review.build_candidate(**inputs)
        with self.assertRaisesRegex(ValueError, "N07_OBSERVED_AS_EXPECTED"):
            review.ensure_independent(candidate, "f" * 32, observed_capture_ids=[
                candidate["sessions"]["second"]["capture_id"]])
        raw = evidence.canonical(candidate)
        with self.assertRaises(ValueError):
            pixel._decode_fixture(raw, evidence.sha(raw))
        self.assertIsNone(pixel.APPROVED_SHA256)

    def test_rejected_lifecycle_packet_keeps_its_revision_and_failure(self):
        inputs, _ = lifecycle_packet_inputs()
        provenance = inputs.pop("provenance")
        inputs.pop("proposal")
        first, _ = b3._observed(inputs["first"], inputs["contexts"]["first"])
        second = probe.second_readbacks(inputs["second"])
        states = {**first, **{name: review._state(inputs["second"], name, second[name])
                             for name in pixel.STATES[4:]}}
        observed = {"rejection": "N07_B4_COLLECTION_INCOMPLETE",
                    "settings_rejection": "N07_SETTINGS_UNEXPECTED_KEY",
                    "settings_revision": review.SETTINGS_REVISION,
                    "states": states, "sessions": {"synthetic": True},
                    "retirement": {"synthetic": True}}
        inputs["supervisor"]["external_rescue"] = False
        with tempfile.TemporaryDirectory() as temporary, patch.object(
                review, "_rejected_observed", return_value=observed):
            root = Path(temporary) / "rejected"
            result = review.export_rejected_bundle(root, **inputs, provenance=provenance)
            packet = evidence.parse((root / "diagnostic.json").read_bytes(),
                                    limit=review.MAX_PACKET_BYTES)
            self.assertEqual((packet["schema"], packet["revision"]),
                             (2, review.REJECTED_LIFECYCLE_REVISION))
            self.assertEqual(packet["settings_revision"], review.SETTINGS_REVISION)
            self.assertEqual(packet["settings_rejection"], observed["settings_rejection"])
            self.assertFalse(packet["candidate_created"])
            self.assertFalse(packet["full_collection_valid"])
            self.assertFalse((root / "candidate.json").exists())
            self.assertTrue(review.validate_rejected_bundle(
                root, result["diagnostic_sha256"])["integrity_verified"])
            packet.update(schema=1, revision=review.REJECTED_REVISION)
            raw = evidence.canonical(packet)
            digest = evidence.sha(raw)
            (root / "diagnostic.json").write_bytes(raw)
            (root / "diagnostic.sha256").write_bytes((digest + "\n").encode("ascii"))
            with self.assertRaisesRegex(ValueError, "N07_B4_DIAGNOSTIC_CHANGED"):
                review.validate_rejected_bundle(root, digest)


if __name__ == "__main__":
    unittest.main()
