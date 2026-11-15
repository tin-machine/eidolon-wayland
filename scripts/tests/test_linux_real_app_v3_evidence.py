"""I6 validator contracts. Specimens are not measurements or Host acceptance."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_real_app_v3_evidence as evidence


def snapshot():
    return {"commit": "1" * 40, "tree_sha256": "2" * 64, "dirty": False,
            "build_features": [], "pr_head": None}


def owner():
    return {"owner_id": "3" * 32, "runtime": "1", "admission_epoch": "1", "connect_request": "2",
            "launch_request": "3", "session": "4", "capture_sha256": "4" * 64,
            "context_sha256": "5" * 64, "io_generation": "1", "nonce": "6" * 16,
            "scope": "7" * 64, "native_origin": "1"}


class EvidenceContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = snapshot()
        self.report = evidence.incomplete_report(self.source, run_id="a" * 32)

    def validate(self, report=None, raw=None, source=None):
        raw = raw if raw is not None else json.dumps(self.report if report is None else report).encode()
        return evidence.validate_acceptance(raw, self.root, self.source if source is None else source)

    def assertInvalid(self, **kwargs):
        result = self.validate(**kwargs)
        self.assertEqual(result["exit_code"], 3, result)
        self.assertFalse(result["host_v3_acceptance_passed"])
        self.assertFalse(result["evidence_contract_valid"])
        return result

    def test_unperformed_profile_is_a_valid_incomplete_envelope(self):
        result = self.validate()
        self.assertEqual((result["status"], result["exit_code"]), ("incomplete", 2))
        self.assertTrue(result["evidence_contract_valid"])
        self.assertEqual(len(self.report["cases"]), 87)
        self.assertIn("N01/inspect_only", result["missing"])
        self.assertEqual(result["coverage"], evidence.COVERAGE)
        self.assertFalse(result["product_oracles_verified"])

    def test_duplicate_unknown_or_mixed_version_at_every_boundary_is_invalid(self):
        raw = json.dumps(self.report).encode()
        self.assertInvalid(raw=raw.replace(b'"schema": 1', b'"schema": 1, "schema": 1', 1))
        changes = [lambda r: r.update(unrecognized=True), lambda r: r.update(required=[]),
                   lambda r: r.update(schema=2), lambda r: r.update(schema=True),
                   lambda r: r.update(stage="feasibility"), lambda r: r.update(revision="eidolon-linux-desktop-v2"),
                   lambda r: r.update(profile="i6-real-reboot-v1"), lambda r: r.update(verification_ids=["V11"]),
                   lambda r: r["source"].update(secret="do-not-print"),
                   lambda r: r["source"]["before"].update(extra=True),
                   lambda r: r["cases"][0].update(required=False),
                   lambda r: r["cases"][0]["cleanup"].update(complete=True),
                   lambda r: r["cases"][0]["trace"].update(payload="do-not-print")]
        for mutate in changes:
            with self.subTest(mutation=changes.index(mutate)):
                report = copy.deepcopy(self.report)
                mutate(report)
                result = self.assertInvalid(report=report)
                self.assertNotIn("do-not-print", json.dumps(result))

    def test_strict_json_rejects_nonfinite_float_utf8_surrogates_trailing_and_depth(self):
        for raw in (b'{"schema":NaN}', b'{"schema":Infinity}', b'{"schema":1.0}', b'{"schema":-0}',
                    b'{"schema":"\xff"}', b'{"schema":"\\ud800"}', b'{}{}',
                    b'[' * 2000 + b']' * 2000, b'', b'null', b'true', b'[]'):
            with self.subTest(raw=raw[:40]):
                self.assertInvalid(raw=raw)
        self.assertInvalid(raw=json.dumps(self.report).encode() + b'\x00')

    def test_report_size_limit_is_bytes_not_characters(self):
        raw = json.dumps(self.report).encode()
        padded = raw + b' ' * (evidence.MAX_REPORT_BYTES - len(raw))
        self.assertEqual(self.validate(raw=padded)["exit_code"], 2)
        self.assertInvalid(raw=padded + b' ')
        self.assertInvalid(raw=b'"' + ('日' * (evidence.MAX_REPORT_BYTES // 2)).encode() + b'"')

    def test_case_variant_assertion_missing_is_incomplete_but_unknown_or_duplicate_is_invalid(self):
        report = copy.deepcopy(self.report)
        absent = report["cases"].pop()
        result = self.validate(report)
        self.assertEqual(result["exit_code"], 2)
        self.assertIn(absent["case_id"] + "/" + absent["variant"], result["missing"])
        report["cases"].append(report["cases"][0])
        self.assertInvalid(report=report)
        report = copy.deepcopy(self.report)
        report["cases"][0]["variant"] = "unregistered"
        self.assertInvalid(report=report)
        report = copy.deepcopy(self.report)
        report["cases"][0]["assertions"] = [{"assertion_id": "made_up", "status": "pass",
                                             "reason": "I6_OK", "evidence_refs": []}]
        self.assertInvalid(report=report)
        row = evidence.load_manifest()["cases"][0]
        report["cases"][0]["assertions"][0]["assertion_id"] = row["assertions"][0]
        self.assertEqual(self.validate(report)["exit_code"], 2)
        report["cases"][0]["assertions"] *= 2
        self.assertInvalid(report=report)

    def test_manifest_identity_cannot_be_replaced_by_report(self):
        self.report["manifest_sha256"] = "0" * 64
        self.assertInvalid()
        manifest = evidence.load_manifest()
        manifest["cases"] = manifest["cases"][:1]
        result = evidence.validate_acceptance(json.dumps(self.report).encode(), self.root,
                                              self.source, json.dumps(manifest).encode())
        self.assertEqual((result["exit_code"], result["reason"]), (3, "I6_MANIFEST_INVALID"))

    def test_source_mismatch_dirty_missing_and_merge_head_are_distinct(self):
        for key, value in (("commit", "8" * 40), ("tree_sha256", "9" * 64), ("pr_head", "a" * 40),
                           ("build_features", ["host-acceptance"])):
            with self.subTest(key=key):
                report = copy.deepcopy(self.report)
                report["source"]["after"][key] = value
                self.assertInvalid(report=report)
        source = dict(self.source, dirty=True)
        result = self.validate(evidence.incomplete_report(source), source=source)
        self.assertTrue(result["evidence_contract_valid"])
        self.assertIn("clean_source", result["missing"])
        self.assertEqual(result["exit_code"], 2)
        result = evidence.validate_acceptance(json.dumps(evidence.incomplete_report(None)).encode(), self.root, None)
        self.assertEqual(result["exit_code"], 2)
        self.assertIn("source", result["missing"])

    def test_input_inventory_is_bound_to_current_source_bytes(self):
        inputs = {**dict.fromkeys(evidence.INPUT_FIELDS - {"compiled_source_hashes", "golden_revision"}, "b" * 64),
                  "golden_revision": "unit-specimen-v1", **evidence.source_input_hashes()}
        self.report["inputs"] = {"before": copy.deepcopy(inputs), "after": copy.deepcopy(inputs)}
        self.assertTrue(self.validate()["evidence_contract_valid"])
        for field in ("package_fingerprint", "bundle_sha256", "renderer_fingerprint"):
            with self.subTest(field=field):
                report = copy.deepcopy(self.report)
                report["inputs"]["after"][field] = "c" * 64
                self.assertInvalid(report=report)
        for field in ("inventory_sha256", "compiled_source_hashes"):
            with self.subTest(field=field):
                report = copy.deepcopy(self.report)
                for stage in ("before", "after"):
                    if field == "compiled_source_hashes":
                        report["inputs"][stage][field]["eidolon_linux_desktop_v3.py"] = "c" * 64
                    else:
                        report["inputs"][stage][field] = "c" * 64
                self.assertInvalid(report=report)

    def test_positive_i64_identifiers_use_decimal_strings_without_precision_loss(self):
        self.report["cases"][0]["owners"] = [owner()]
        self.report["cases"][0]["owners"][0]["runtime"] = str((1 << 63) - 1)
        self.assertTrue(self.validate()["evidence_contract_valid"])
        for invalid in (1, True, 1.0, "0", "01", "-1", str(1 << 63), "1e3", "１", None):
            with self.subTest(value=invalid):
                report = copy.deepcopy(self.report)
                report["cases"][0]["owners"][0]["runtime"] = invalid
                self.assertInvalid(report=report)

    def test_assigned_launch_cannot_hide_capture_session_or_generation_with_null(self):
        self.report["cases"][0]["owners"] = [owner()]
        for field in ("session", "native_origin", "connect_request", "context_sha256", "nonce", "scope", "io_generation"):
            with self.subTest(field=field):
                report = copy.deepcopy(self.report)
                report["cases"][0]["owners"][0][field] = None
                self.assertInvalid(report=report)

    def test_invalid_wins_over_cancel_failure_and_missing(self):
        self.report["status"] = "cancelled"
        self.report["cases"][0]["first_case_failure"] = {"reason": "I6_ASSERTION_FAILED", "owner_id": None, "sequence": None}
        self.assertEqual(self.validate()["exit_code"], 130)
        self.report["cases"][-1]["extra"] = True
        self.assertInvalid()

    def test_case_failure_is_not_erased_by_cleanup_or_claimed_summary_success(self):
        self.report.update(status="pass", exit_code=0, host_v3_acceptance_passed=True)
        case = self.report["cases"][0]
        failure = {"reason": "I6_FAULT_NOT_REACHED", "owner_id": None, "sequence": "1"}
        case["first_case_failure"] = failure
        case["cleanup_failures"] = [{"reason": "I6_CLEANUP_FAILED", "owner_id": None, "sequence": "2"}]
        case["cleanup"].update(product="complete", fixture_rescue="complete", environment_disposal="complete")
        result = self.validate()
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["first_case_failure"]["reason"], failure["reason"])
        self.assertEqual(result["cleanup_failures"][0]["reason"], "I6_CLEANUP_FAILED")
        self.assertFalse(result["host_v3_acceptance_passed"])

    def test_expected_product_failure_and_retained_unknown_are_not_case_failure(self):
        case = next(c for c in self.report["cases"] if c["case_id"] == "F07")
        case.update(status="pass", reason="I6_EXPECTED_PENDING", first_product_failure="authority_lost",
                    observed_product_outcome="retained_pending")
        case["cleanup"].update(product="unknown", fixture_rescue="complete", environment_disposal="complete")
        result = self.validate()
        self.assertEqual(result["exit_code"], 2)  # Remaining evidence is still absent.
        self.assertIsNone(result["first_case_failure"])
        self.assertEqual(case["cleanup"]["product"], "unknown")
        case["expected_product_outcome"] = "complete"
        self.assertInvalid()

    def test_unexpected_cleanup_outcome_is_case_failure_not_skip(self):
        case = self.report["cases"][0]
        case["observed_product_outcome"] = "retained_pending"
        case["cleanup"]["product"] = "unknown"
        result = self.validate()
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["first_case_failure"]["reason"], "I6_CLEANUP_FAILED")

    def test_contradictory_cleanup_outcome_is_invalid(self):
        self.report["cases"][0]["observed_product_outcome"] = "complete"
        self.assertInvalid()

    def test_actual_trace_overflow_is_incomplete_and_summary_cannot_clear_it(self):
        self.report.update(status="pass", exit_code=0, host_v3_acceptance_passed=True)
        self.report["cases"][0]["trace"]["overflow"] = True
        result = self.validate()
        self.assertEqual(result["exit_code"], 2)
        self.assertTrue(any(m.endswith("/trace_overflow") for m in result["missing"]))
        self.assertFalse(result["host_v3_acceptance_passed"])

    def test_host_report_cannot_claim_android_sdk_quest_or_physical_evidence(self):
        for flag in evidence.PUBLIC_FLAGS:
            with self.subTest(flag=flag):
                report = copy.deepcopy(self.report)
                report[flag] = True
                self.assertInvalid(report=report)
        report = copy.deepcopy(self.report)
        report["coverage"]["sdk"] = "real"
        self.assertInvalid(report=report)

    def test_writer_is_exclusive_private_and_has_separate_i6_bound(self):
        path = self.root / "report.json"
        large = {"data": "x" * (129 * 1024)}
        evidence.write_report(path, large)
        self.assertEqual(json.loads(evidence.read_report(path)), large)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            evidence.write_report(path, {})
        with self.assertRaises(evidence.EvidenceError):
            evidence.write_report(self.root / "too-large.json", {"data": "x" * evidence.MAX_REPORT_BYTES})
        self.assertFalse((self.root / "too-large.json").exists())


class SourceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "checkout"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "I6 contract fixture")
        (self.root / "plain.txt").write_bytes(b"tracked\n")
        executable = self.root / "executable"
        executable.write_bytes(b"#!/bin/false\n")
        executable.chmod(0o755)
        self.outside = self.root.parent / "outside.txt"
        self.outside.write_bytes(b"must not be followed\n")
        (self.root / "link").symlink_to("../outside.txt")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

    def test_canonical_live_tree_digest_hashes_modes_paths_and_symlink_target_bytes(self):
        measured = evidence.source_snapshot(self.root)
        rows = [[b"plain.txt".hex(), "100644", hashlib.sha256(b"tracked\n").hexdigest()],
                [b"executable".hex(), "100755", hashlib.sha256(b"#!/bin/false\n").hexdigest()],
                [b"link".hex(), "120000", hashlib.sha256(b"../outside.txt").hexdigest()]]
        expected = hashlib.sha256(evidence.canonical(sorted(rows))).hexdigest()
        self.assertEqual(measured["tree_sha256"], expected)
        self.assertFalse(measured["dirty"])
        self.outside.write_bytes(b"different external bytes must not change provenance\n")
        self.assertEqual(evidence.source_snapshot(self.root), measured)
        (self.root / "plain.txt").write_bytes(b"changed\n")
        modified = evidence.source_snapshot(self.root)
        self.assertTrue(modified["dirty"])
        self.assertEqual(modified["commit"], measured["commit"])
        self.assertNotEqual(modified["tree_sha256"], measured["tree_sha256"])

    def test_untracked_source_marks_checkout_dirty(self):
        (self.root / "new.py").write_text("pass\n")
        self.assertTrue(evidence.source_snapshot(self.root)["dirty"])

    def test_source_parent_symlink_is_not_followed(self):
        directory = self.root / "nested"
        directory.mkdir()
        (directory / "file").write_bytes(b"owned\n")
        self.git("add", ".")
        self.git("commit", "-qm", "nested fixture")
        retained = self.root.parent / "retained"
        directory.rename(retained)
        directory.symlink_to(retained, target_is_directory=True)
        with self.assertRaisesRegex(evidence.EvidenceError, "^I6_SOURCE_UNAVAILABLE$"):
            evidence.source_snapshot(self.root)

    def test_source_inspection_does_not_refresh_git_index(self):
        index = self.root / ".git/index"
        before, metadata = index.read_bytes(), index.stat()
        path = self.root / "plain.txt"
        info = path.stat()
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
        evidence.source_snapshot(self.root)
        self.assertEqual(index.read_bytes(), before)
        self.assertEqual(index.stat().st_mtime_ns, metadata.st_mtime_ns)

    def test_git_error_never_discloses_stderr(self):
        with mock.patch.object(subprocess, "run", side_effect=subprocess.CalledProcessError(1, "private-command", stderr=b"private-data")):
            with self.assertRaisesRegex(evidence.EvidenceError, "^I6_SOURCE_UNAVAILABLE$"):
                evidence.source_snapshot(self.root)


if __name__ == "__main__":
    unittest.main()
