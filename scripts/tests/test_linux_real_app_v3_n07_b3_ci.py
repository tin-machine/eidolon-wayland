"""B3 saved-export integration: failure evidence survives without approval."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_real_app_v3_n07_b3_ci as ci
import linux_real_app_v3_n07_fingerprint as fp
import linux_real_app_v3_n07_review as review
from test_linux_real_app_v3_n07_b3 import inputs, add_context
from test_linux_real_app_v3_n07_fingerprint import fixture as fingerprint_fixture


def bundle_inputs():
    data = inputs()
    add_context(data)
    record, _, env, _ = fingerprint_fixture()
    data["environment"]["app"] = env["app"]
    for stage in ("before", "after"):
        data["environment"][stage]["pins"] = env[stage]["pins"]
    record["binding"] = fp._binding(data["native"], data["environment"], data["validation"]["source"])
    record["evidence"]["renderer"] = fp._renderer(data["native"])
    record["values"] = fp._derive(record, data["native"], data["environment"], data["validation"]["source"])
    record["missing"] = sorted(fp.pixel.FINGERPRINT - set(record["values"]))
    data["fingerprint"] = record
    return data


class PreparationTests(unittest.TestCase):
    def prepare(self, directory, data, *, omit=(), container_change=None):
        base = Path(directory)
        incoming, output = base / "incoming", base / "output"
        incoming.mkdir(mode=0o700)
        output.mkdir(mode=0o700)
        source = data["validation"]["source"]
        for key, name in (("native", "n07-b2-native.json"),
                          ("environment", "n07-b2-environment.json"),
                          ("validation", "n07-b2-validation.json")):
            (output / name).write_bytes(review.canonical(data[key]))
        for key, name in (("fingerprint", "n07-b3-fingerprint.json"),
                          ("context_raw", "n07-b3-context.json")):
            if name not in omit:
                value = data[key]
                (incoming / name).write_bytes(value if type(value) is bytes else review.canonical(value))
        (incoming / "unrelated-private-file").write_text("must not export")
        container = {"schema": 1, "source_commit": source["commit"],
                     "pr_head_sha": "7" * 40, "image_id": "sha256:" + "a" * 64,
                     "attach_exit": 0, "container_exit": 0, "external_rescue": False,
                     "network": "none", "host_mounts": False, "formal_i6_passed": False,
                     "n07_assertions_verified": []}
        if container_change:
            container.update(container_change)
        (output / "container.json").write_bytes(review.canonical(container))
        with patch.object(ci.evidence, "source_snapshot", return_value=copy.deepcopy(source)):
            ok = ci.prepare(incoming, output, Path("/synthetic-source"), source["commit"], "7" * 40,
                            {"GITHUB_RUN_ID": "1234", "GITHUB_RUN_ATTEMPT": "1"})
        report = json.loads((output / "n07-b3-validation.json").read_bytes())
        self.assertFalse((output / "unrelated-private-file").exists())
        return ok, report, output

    def test_saved_export_reopens_context_and_preserves_partial_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            data = bundle_inputs()
            ok, report, output = self.prepare(directory, data)
            self.assertTrue(ok, report)
            self.assertEqual(report["status"], "review_prepared")
            self.assertEqual(report["fingerprint_status"], "partial")
            self.assertEqual(report["fingerprint_missing"], ["theme"])
            self.assertTrue(report["product_cleanup_verified"])
            self.assertFalse(report["reviewed_golden"])
            self.assertFalse(report["host_v3_acceptance_passed"])
            self.assertEqual((output / "n07-b3-context.json").read_bytes(), data["context_raw"])
            checked = review.validate_bundle(output / "n07-b3-review", report["candidate"]["candidate_sha256"])
            self.assertTrue(checked["integrity_verified"])
            candidate = json.loads((output / "n07-b3-review/candidate.json").read_bytes())
            self.assertEqual(candidate["origin"]["artifact_status"], "pending_upload")
            self.assertIsNone(candidate["origin"]["artifact_id"])
            self.assertEqual(candidate["coverage"]["missing_states"], list(review.MISSING_STATES))

    def test_missing_context_keeps_fingerprint_and_cleanup_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            ok, report, output = self.prepare(directory, bundle_inputs(), omit=("n07-b3-context.json",))
            self.assertFalse(ok)
            self.assertEqual(report["reason"], "N07_B3_OBSERVATION_MISSING")
            self.assertTrue(report["product_cleanup_verified"])
            self.assertTrue((output / "n07-b3-fingerprint.json").is_file())
            self.assertFalse((output / "n07-b3-review/candidate.json").exists())

    def test_fresh_collection_failure_keeps_diagnostic_packet_but_fails_ci(self):
        for missing_process in (False, True):
            with self.subTest(missing_process=missing_process), tempfile.TemporaryDirectory() as directory:
                data = bundle_inputs()
                record = data["fingerprint"]
                record["evidence"]["observation_errors"] = ["N07_FINGERPRINT_OBSERVATION_FAILED"]
                if missing_process:
                    record["evidence"].update(process=None, fonts=[], libraries={})
                record["values"] = fp._derive(record, data["native"], data["environment"],
                                               data["validation"]["source"])
                record["missing"] = sorted(fp.pixel.FINGERPRINT - set(record["values"]))
                ok, report, output = self.prepare(directory, data)
                self.assertFalse(ok)
                self.assertEqual(report["reason"], "N07_B3_REQUIRED_OBSERVATION_MISSING")
                self.assertTrue(report["candidate_created"])
                self.assertTrue(report["product_cleanup_verified"])
                self.assertTrue((output / "n07-b3-fingerprint.json").is_file())
                self.assertFalse(report["reviewed_golden"])
                self.assertFalse(report["host_v3_acceptance_passed"])
                # The offline diagnostic exporter continues to accept honest
                # missing data; CI collection success is a separate contract.
                checked = review.validate_bundle(output / "n07-b3-review",
                                                 report["candidate"]["candidate_sha256"])
                self.assertEqual(checked["status"], "diagnostic_only_missing_fingerprint")

    def test_malformed_context_keeps_other_observations_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            data = bundle_inputs()
            data["context_raw"] = b"{broken"
            ok, report, output = self.prepare(directory, data)
            self.assertFalse(ok)
            self.assertEqual(report["reason"], "N07_B3_OBSERVATION_INVALID")
            self.assertTrue(report["product_cleanup_verified"])
            self.assertTrue((output / "n07-b3-fingerprint.json").is_file())
            self.assertEqual(report["observations"]["n07-b3-context.json"], {"status": "rejected"})
            self.assertFalse((output / "n07-b3-context.json").exists())

    def test_partial_context_or_foreign_source_cannot_make_review_packet(self):
        for mutate in (
            lambda data: data["native"]["review_context"].update(complete=False),
            lambda data: data["environment"].update(run_id="f" * 32),
            lambda data: data["fingerprint"]["values"].update(font_sha256="f" * 64),
            lambda data: data["fingerprint"]["values"].update(theme="Adwaita"),
            lambda data: data["native"].update(error="I6_N07_REVIEW_FAILED"),
        ):
            with tempfile.TemporaryDirectory() as directory:
                data = bundle_inputs()
                mutate(data)
                ok, report, output = self.prepare(directory, data)
                self.assertFalse(ok, report)
                self.assertFalse(report["candidate_created"])
                self.assertTrue((output / "n07-b3-context.json").exists())
                self.assertTrue((output / "n07-b2-native.json").exists())

    def test_rescue_wrong_types_and_foreign_pr_rejected(self):
        for mutation in ({"external_rescue": True}, {"attach_exit": False},
                         {"container_exit": "0"}, {"pr_head_sha": "f" * 40},
                         {"host_mounts": True}):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                ok, report, output = self.prepare(directory, bundle_inputs(), container_change=mutation)
                self.assertFalse(ok)
                self.assertEqual(report["reason"], "N07_B3_CONTAINER_REJECTED")
                self.assertTrue((output / "n07-b3-context.json").is_file())

    def test_receipt_file_is_bounded_regular_and_not_a_link(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            path.write_bytes(b'{"schema":1}')
            self.assertEqual(ci.read_bounded(path, 32)[1], {"schema": 1})
            with self.assertRaises(ValueError):
                ci.read_bounded(path, 4)
            alias = Path(directory) / "alias.json"
            alias.symlink_to(path)
            with self.assertRaises(OSError):
                ci.read_bounded(alias, 32)
            path.write_bytes(b'{"schema":1,"schema":2}')
            with self.assertRaises(ValueError):
                ci.read_bounded(path, 32)


if __name__ == "__main__":
    unittest.main()
