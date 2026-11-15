"""I6 CLI integration, with no SSH, application, rendering or product runner.

The independent evidence validator is real except for the explicitly labelled
dispatch-only test. Source snapshots are fixed read-only boundary doubles;
none of these tests claims an I6 Host product case has executed.
"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import linux_real_app_check as check
import linux_real_app_v3_acceptance as acceptance

SOURCE = {"commit": "a" * 40, "tree_sha256": "b" * 64, "dirty": False,
          "build_features": [], "pr_head": None}


class AcceptanceCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        patch = mock.patch.object(acceptance.evidence, "source_snapshot",
                                  side_effect=lambda _: copy.deepcopy(SOURCE))
        self.source_snapshot = patch.start()
        self.addCleanup(patch.stop)

    def command(self, *args):
        output = io.StringIO()
        previous = os.umask(0o077)
        try:
            with contextlib.redirect_stdout(output):
                status = check.main(list(args))
        finally:
            os.umask(previous)
        return status, json.loads(output.getvalue())

    def incomplete(self):
        return acceptance.evidence.incomplete_report(copy.deepcopy(SOURCE), run_id="1" * 32)

    def stored(self, report=None):
        root = self.root / "evidence"
        root.mkdir(mode=0o700)
        path = root / "acceptance.json"
        acceptance.evidence.write_report(path, self.incomplete() if report is None else report)
        return root, path

    def test_fresh_run_uses_two_source_observations_and_the_real_validator(self):
        artifact = self.root / "new"
        with mock.patch.object(check, "run_feasibility", side_effect=AssertionError("not A0")), \
             mock.patch.object(check, "build_compositor", side_effect=AssertionError("no product runner")):
            status, result = self.command("--stage", "acceptance", "--artifact-root", str(artifact))
        self.assertEqual(status, 2)
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["host_v3_acceptance_passed"])
        self.assertTrue(result["evidence_contract_valid"])
        self.assertEqual(self.source_snapshot.call_args_list, [mock.call(ROOT), mock.call(ROOT)])
        report = json.loads((artifact / "acceptance.json").read_bytes())
        self.assertEqual(report["source"], {"before": SOURCE, "after": SOURCE})
        self.assertEqual(json.loads((artifact / "acceptance-validation.json").read_bytes()), result)
        self.assertEqual(len({case["case_id"] for case in report["cases"]}), 28)
        self.assertTrue(all(case["status"] == "not_run" for case in report["cases"]))

    def test_source_change_between_observations_is_invalid(self):
        after = {**SOURCE, "tree_sha256": "c" * 64}
        self.source_snapshot.side_effect = [copy.deepcopy(SOURCE), after]
        artifact = self.root / "changed-source"
        status, result = self.command("--stage", "acceptance", "--artifact-root", str(artifact))
        self.assertEqual(status, 3)
        self.assertFalse(result["host_v3_acceptance_passed"])
        self.assertEqual(json.loads((artifact / "acceptance.json").read_bytes())["source"]["after"], after)

    def test_unavailable_source_remains_incomplete_without_exception_text(self):
        self.source_snapshot.side_effect = OSError("private path and credential must not be printed")
        artifact = self.root / "missing-source"
        status, result = self.command("--stage", "acceptance", "--artifact-root", str(artifact))
        self.assertEqual(status, 2)
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["host_v3_acceptance_passed"])
        self.assertNotIn("credential", json.dumps(result))
        report = json.loads((artifact / "acceptance.json").read_bytes())
        self.assertEqual(report["source"], {"before": None, "after": None})

    def test_saved_report_validation_is_read_only_and_uses_current_checkout(self):
        root, path = self.stored()
        sentinel = root / "prior-run.txt"
        sentinel.write_bytes(b"preserve previous run\n")
        sentinel.chmod(0o600)
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.iterdir()}
        with mock.patch.object(check, "private_artifact_root", side_effect=AssertionError("read only")), \
             mock.patch.object(acceptance.evidence, "write_report", side_effect=AssertionError("read only")):
            status, result = self.command("--stage", "acceptance", "--artifact-root", str(root),
                                          "--acceptance-report", str(path))
        self.assertEqual(status, 2)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(self.source_snapshot.call_args_list, [mock.call(ROOT)])
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.iterdir()})

    def test_saved_report_cannot_select_its_own_expected_source(self):
        report = self.incomplete()
        wrong = {**SOURCE, "commit": "c" * 40}
        report["source"] = {"before": wrong, "after": wrong}
        root, path = self.stored(report)
        status, result = self.command("--stage", "acceptance", "--artifact-root", str(root),
                                      "--acceptance-report", str(path))
        self.assertEqual(status, 3)
        self.assertFalse(result["host_v3_acceptance_passed"])

    def test_missing_or_malformed_saved_report_is_invalid_and_never_rewritten(self):
        root = self.root / "malformed"
        root.mkdir(mode=0o700)
        path = root / "acceptance.json"
        status, result = self.command("--stage", "acceptance", "--artifact-root", str(root),
                                      "--acceptance-report", str(path))
        self.assertEqual(status, 3)
        self.assertEqual(list(root.iterdir()), [])
        payload = b'{"secret":"do-not-echo", "schema":1, "schema":2}'
        path.write_bytes(payload)
        path.chmod(0o600)
        status, result = self.command("--stage", "acceptance", "--artifact-root", str(root),
                                      "--acceptance-report", str(path))
        self.assertEqual(status, 3)
        self.assertNotIn("do-not-echo", json.dumps(result))
        self.assertEqual(path.read_bytes(), payload)
        self.assertEqual(list(root.iterdir()), [path])

    def test_report_claimed_success_is_not_authority(self):
        report = self.incomplete()
        report["status"] = "pass"
        report["host_v3_acceptance_passed"] = True
        root, path = self.stored(report)
        status, result = self.command("--stage", "acceptance", "--artifact-root", str(root),
                                      "--acceptance-report", str(path))
        self.assertNotEqual(status, 0)
        self.assertFalse(result["host_v3_acceptance_passed"])

    def test_acceptance_report_option_is_not_accepted_by_feasibility(self):
        with contextlib.redirect_stderr(io.StringIO()), \
             mock.patch.object(check, "run_feasibility", side_effect=AssertionError("invalid arguments")), \
             self.assertRaises(SystemExit) as raised:
            check.main(["--stage", "feasibility", "--artifact-root", str(self.root),
                        "--acceptance-report", str(self.root / "report.json")])
        self.assertEqual(raised.exception.code, 2)
        self.source_snapshot.assert_not_called()

    def test_stage_exit_dispatch_uses_only_validator_outcome(self):
        # Deliberately mock the validator boundary here: this checks dispatch,
        # not whether a synthetic report establishes real Host acceptance.
        for expected in (0, 1, 2, 3, 130):
            result = acceptance.evidence.invalid_result()
            result.update(exit_code=expected, a0_passed=expected != 0)
            with self.subTest(exit_code=expected), \
                 mock.patch.object(acceptance, "run_acceptance", return_value=result):
                status, actual = self.command("--stage", "acceptance", "--artifact-root", str(self.root))
            self.assertEqual(status, expected)
            self.assertEqual(actual, result)

    def test_operator_interrupt_has_cancelled_exit_without_cleanup_claim(self):
        with mock.patch.object(acceptance, "run_acceptance", side_effect=KeyboardInterrupt):
            status, result = self.command("--stage", "acceptance", "--artifact-root", str(self.root))
        self.assertEqual(status, 130)
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(result["host_v3_acceptance_passed"])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_acceptance_io_error_uses_fixed_diagnostic_only(self):
        with mock.patch.object(acceptance, "run_acceptance", side_effect=OSError("private secret path")):
            status, result = self.command("--stage", "acceptance", "--artifact-root", str(self.root))
        self.assertEqual(status, 3)
        self.assertEqual(result, acceptance.evidence.invalid_result())
        self.assertNotIn("secret", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
