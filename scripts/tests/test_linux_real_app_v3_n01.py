"""Disposable N01 supervisor rejection tests; no root, account or SSH mutation."""
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_real_app_v3_evidence as evidence
import linux_real_app_v3_n01 as n01
from test_linux_real_app_v3_n01_oracle import specimen


class N01SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="eidolon-n01-unit-")
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)

    def execute_receipt(self, receipt, source, name="artifacts"):
        args = n01.argparse.Namespace(artifact_root=self.root / name)

        def completed_fixture(_args, artifacts, _source, _progress):
            return n01.assemble_report(source, source, receipt["native"], receipt["environment"], artifacts)

        with mock.patch.object(n01.evidence, "source_snapshot", return_value=source), \
             mock.patch.object(n01, "run_fixture", side_effect=completed_fixture):
            code = n01.execute(args)
        report = json.loads((args.artifact_root / "acceptance.json").read_bytes())
        validation = json.loads((args.artifact_root / "acceptance-validation.json").read_bytes())
        result = json.loads((args.artifact_root / "n01-validation.json").read_bytes())
        return code, args.artifact_root, report, validation, result

    def test_rejected_typed_receipt_reaches_validator_as_invalid_not_environment_unavailable(self):
        for mutation, reason in (("mixed_capture", "I6_OWNER_MISMATCH"),
                                 ("cleanup_order", "I6_TRACE_INVALID"),
                                 ("malformed_protocol", "I6_REPORT_INVALID")):
            with self.subTest(mutation=mutation):
                receipt, source = specimen()
                events = receipt["native"]["trace"]["events"]
                if mutation == "mixed_capture":
                    next(e for e in events if e["kind"] == "journal_unlinked")["capture"]["capture_id"] = "f" * 64
                elif mutation == "cleanup_order":
                    io = next(i for i, e in enumerate(events) if e["kind"] == "io_retired")
                    unlink = next(i for i, e in enumerate(events) if e["kind"] == "journal_unlinked")
                    events[io], events[unlink] = events[unlink], events[io]
                    for sequence, event in enumerate(events, 1):
                        event["sequence"] = str(sequence)
                else:
                    prepared = next(e for e in events if e["kind"] == "protocol_event")
                    prepared["data"]["record"]["event"]["payload"]["lease_ms"] = "private-value-must-not-appear"
                code, artifacts, report, validation, result = self.execute_receipt(receipt, source, mutation)
                self.assertEqual(code, 3)
                self.assertEqual((validation["status"], validation["reason"]), ("invalid", reason))
                self.assertEqual(result["acceptance_exit_code"], 3)
                self.assertEqual(result["oracle"]["status"], "invalid")
                self.assertEqual(result["oracle"]["reason"], reason)
                self.assertIsNone(result["fixture_failure"])
                self.assertFalse(result["n01_inspect_only_passed"])
                self.assertFalse(result["host_v3_acceptance_passed"])
                self.assertNotIn("private-value-must-not-appear", json.dumps(result))
                self.assertEqual(len(report["artifacts"]), 1)
                self.assertEqual(report["artifacts"][0]["kind"], "n01_receipt")
                self.assertEqual(json.loads((artifacts / "n01-receipt.json").read_bytes()), receipt)
                self.assertEqual(report["cases"][0]["assertions"], [])

    def test_valid_typed_receipt_verifies_n01_with_all_other_required_cases_retained(self):
        receipt, source = specimen()
        code, _artifacts, report, validation, result = self.execute_receipt(receipt, source)
        self.assertEqual(code, 0)
        self.assertEqual(validation["exit_code"], 2)
        self.assertTrue(result["n01_inspect_only_passed"])
        self.assertFalse(result["host_v3_acceptance_passed"])
        self.assertEqual(len(report["cases"]), 87)
        self.assertEqual(len(report["cases"][0]["assertions"]), 8)
        self.assertTrue(all(row["status"] == "not_run" for row in report["cases"][1:]))
        self.assertEqual(result["required_assertions"], 533)

    def test_without_explicit_disposable_flag_no_account_probe_or_mutation(self):
        with mock.patch.object(n01.pwd, "getpwnam", side_effect=AssertionError("account lookup")), \
             mock.patch.object(n01, "command", side_effect=AssertionError("mutation")):
            with self.assertRaisesRegex(n01.FixtureError, "I6_ENVIRONMENT_UNAVAILABLE"):
                n01.disposable_preflight(False)

    def test_nonroot_is_rejected_even_with_explicit_flag(self):
        with mock.patch.object(n01.os, "getresuid", return_value=(1000, 1000, 1000)), \
             mock.patch.object(n01, "prepare_accounts", side_effect=AssertionError("mutation")):
            with self.assertRaisesRegex(n01.FixtureError, "I6_ENVIRONMENT_UNAVAILABLE"):
                n01.disposable_preflight(True)

    def test_inherited_desktop_agents_and_linker_settings_are_not_forwarded(self):
        with mock.patch.dict(os.environ, {"DISPLAY": ":0", "SSH_AUTH_SOCK": "/secret", "LD_PRELOAD": "/secret",
                                          "HOME": "/ordinary", "PYTHONPATH": "/secret"}):
            clean = n01.clean_environment("/home/eidolon-app")
        self.assertEqual(clean["HOME"], "/home/eidolon-app")
        self.assertFalse(set(clean) & {"DISPLAY", "WAYLAND_DISPLAY", "SSH_AUTH_SOCK", "LD_PRELOAD", "PYTHONPATH"})

    def test_private_read_rejects_symlink_hardlink_public_mode_wrong_uid_and_oversize(self):
        path = self.root / "receipt.json"
        path.write_bytes(b"{}")
        path.chmod(0o600)
        self.assertEqual(n01.read_owned(path, os.getuid()), b"{}")
        linked = self.root / "symlink.json"
        linked.symlink_to(path)
        with self.assertRaises((OSError, n01.FixtureError)):
            n01.read_owned(linked, os.getuid())
        hard = self.root / "hard.json"
        os.link(path, hard)
        with self.assertRaises(n01.FixtureError):
            n01.read_owned(path, os.getuid())
        hard.unlink()
        with self.assertRaises(n01.FixtureError):
            n01.read_owned(path, os.getuid() + 1)
        with self.assertRaises(n01.FixtureError):
            n01.read_owned(path, os.getuid(), limit=1)
        path.chmod(0o644)
        with self.assertRaises(n01.FixtureError):
            n01.read_owned(path, os.getuid())

    def test_private_read_never_blocks_on_fifo(self):
        pipe = self.root / "pipe"
        os.mkfifo(pipe, 0o600)
        with self.assertRaises(n01.FixtureError):
            n01.read_owned(pipe, os.getuid())

    def test_child_output_is_bounded_and_nonzero_status_is_preserved(self):
        diagnostics = {}
        status, output = n01.run_capture([sys.executable, "-c", "print('bounded'); raise SystemExit(7)"],
                                         diagnostic=diagnostics)
        self.assertEqual((status, output), (7, b"bounded\n"))
        self.assertEqual((diagnostics["returncode"], diagnostics["exit_kind"], diagnostics["classification"]),
                         (7, "nonzero", "empty"))
        self.assertIsInstance(diagnostics["elapsed_ms"], int)
        self.assertGreaterEqual(diagnostics["elapsed_ms"], 0)
        with self.assertRaisesRegex(n01.FixtureError, "I6_EVIDENCE_MISSING"):
            n01.run_capture([sys.executable, "-c", "import sys; sys.stdout.write('x' * 1100000)"])

    def test_signaled_child_and_watchdog_keep_only_sanitized_exit_observations(self):
        diagnostics = {}
        status, output = n01.run_capture([sys.executable, "-c",
            "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"], diagnostic=diagnostics)
        self.assertEqual((status, output), (-signal.SIGTERM, b""))
        self.assertEqual(diagnostics["exit_kind"], "signal")
        self.assertEqual(diagnostics["signal_number"], signal.SIGTERM)
        self.assertEqual(diagnostics["stderr_tail_bytes"], 0)
        diagnostics = {}
        with self.assertRaisesRegex(n01.FixtureError, "I6_WATCHDOG"):
            n01.run_capture([sys.executable, "-c", "import time; time.sleep(30)"],
                            seconds=0.1, diagnostic=diagnostics)
        self.assertEqual(diagnostics["exit_kind"], "signal")
        self.assertEqual(diagnostics["signal_number"], signal.SIGTERM)

    def test_stderr_tail_is_bounded_and_unknown_markers_and_private_payloads_are_discarded(self):
        raw = ("private-prefix" * 400 + "\nI6_RUNNER_STAGE descriptor\nI6_RUNNER_STAGE registry_create\n"
               "I6_RUNNER_STAGE private-profile\nI6_PRIVATE_SECRET\nI6_INPUT_INVALID\n"
               "-----BEGIN OPENSSH PRIVATE KEY-----\nprivate-key /home/eidolon-app/Documents/profile.json\n")
        diagnostics = {}
        code, _ = n01.run_capture([sys.executable, "-c", "import sys; sys.stderr.write(" + repr(raw) +
                                  "); raise SystemExit(1)"], diagnostic=diagnostics)
        self.assertEqual(code, 1)
        self.assertEqual(diagnostics["stderr_tail_bytes"], 2048)
        self.assertEqual(diagnostics["stages"], ["descriptor", "registry_create"])
        self.assertEqual(diagnostics["reason"], "I6_INPUT_INVALID")
        self.assertEqual(diagnostics["classification"], "fixed_reason")
        serialized = json.dumps(diagnostics)
        for private in ("private", "OPENSSH", "/home/", "Documents", "I6_PRIVATE_SECRET"):
            self.assertNotIn(private, serialized)
        unknown = n01.runner_diagnostics(1, 12, b"I6_PRIVATE_SECRET\nI6_RUNNER_STAGE private-profile\n\xff\x00")
        self.assertEqual(unknown["classification"], "unknown")
        self.assertEqual(unknown["stages"], [])
        self.assertIsNone(unknown["reason"])
        self.assertNotIn("PRIVATE", json.dumps(unknown))

    def test_panic_diagnostic_allows_existing_source_location_and_never_panic_message(self):
        raw = (b"thread 'private-thread' (123) panicked at /src/native/core/src/runtime.rs:123:9:\n"
               b"profile=/home/private/Documents/settings.json password=private-password\n")
        diagnostic = n01.runner_diagnostics(101, 42, raw)
        self.assertEqual(diagnostic["classification"], "rust_panic")
        self.assertEqual(diagnostic["panic_location"], "native/core/src/runtime.rs:123:9")
        self.assertNotIn("private", json.dumps(diagnostic))
        for location in (b"/home/private/secret.rs:1:2:", b"/src/native/core/src/private_secret.rs:1:2:",
                         b"/src/native/core/src/../private.rs:1:2:",
                         b"/src/native/core/src/runtime.rs:1:2: private-message"):
            with self.subTest(location=location):
                diagnostic = n01.runner_diagnostics(101, 42, b"thread 'main' panicked at " + location)
                self.assertEqual(diagnostic["classification"], "rust_panic")
                self.assertIsNone(diagnostic["panic_location"])
                self.assertNotIn("private", json.dumps(diagnostic))

    def test_loader_soname_and_io_safety_classification_reject_private_paths_and_free_text(self):
        prefix = b"/home/private/executable: error while loading shared libraries: "
        suffix = b": cannot open shared object file: No such file or directory"
        diagnostic = n01.runner_diagnostics(127, 2, prefix + b"libEGL.so.1" + suffix)
        self.assertEqual(diagnostic["classification"], "loader_missing")
        self.assertEqual(diagnostic["missing_soname"], "libEGL.so.1")
        self.assertNotIn("private", json.dumps(diagnostic))
        for soname in (b"/home/private/libEGL.so.1", b"libEGL.so.private-key", b"libEGL.so.1 private-key"):
            diagnostic = n01.runner_diagnostics(127, 2, prefix + soname + suffix)
            self.assertEqual(diagnostic["classification"], "unknown")
            self.assertIsNone(diagnostic["missing_soname"])
            self.assertNotIn("private", json.dumps(diagnostic))
        diagnostic = n01.runner_diagnostics(-signal.SIGABRT, 3,
            b"fatal runtime error: IO Safety violation: owned file descriptor already closed, aborting\nprivate-key")
        self.assertEqual(diagnostic["classification"], "io_safety_abort")
        self.assertEqual(diagnostic["signal_number"], signal.SIGABRT)
        self.assertNotIn("private", json.dumps(diagnostic))

    def test_runner_diagnostics_survive_missing_native_receipt_without_entering_acceptance_report(self):
        args = n01.argparse.Namespace(artifact_root=self.root / "artifacts")
        source = {"commit": "a" * 40, "tree_sha256": "b" * 64, "dirty": False,
                  "build_features": [], "pr_head": None}
        diagnostic = n01.runner_diagnostics(-signal.SIGABRT, 12,
            b"I6_RUNNER_STAGE registry_create\nfatal runtime error: IO Safety violation: private-key")

        def failed_fixture(_args, _artifacts, _source, progress):
            progress.update(run_id="c" * 32, first_reason="I6_DRIVER_DIED", diagnostics=diagnostic)
            raise FileNotFoundError("/home/private/native.json")

        with mock.patch.object(n01.evidence, "source_snapshot", return_value=source), \
             mock.patch.object(n01, "run_fixture", side_effect=failed_fixture), \
             mock.patch("builtins.print") as output:
            self.assertEqual(n01.execute(args), 1)
        report = json.loads((args.artifact_root / "acceptance.json").read_bytes())
        result = json.loads((args.artifact_root / "n01-validation.json").read_bytes())
        self.assertEqual(result["diagnostics"], diagnostic)
        self.assertEqual(result["reason"], "I6_ASSERTION_FAILED")
        self.assertFalse(result["n01_inspect_only_passed"])
        self.assertNotIn("diagnostics", report)
        self.assertEqual(report["artifacts"], [])
        self.assertNotIn("private", json.dumps(result))
        self.assertNotIn("private", str(output.call_args))

    def test_strict_native_parse_rejection_keeps_fingerprint_and_first_failure_without_raw_export(self):
        receipt, source = specimen()
        native = receipt["native"]
        native["last_snapshot"]["runtime"]["workspace"] = {"scale": 1.0, "private": "private-profile"}
        specimens = (("float", json.dumps(native).encode(), None),
                     ("duplicate", b'{"schema":1,"schema":1,"private":"private-key"}', "I6_DRIVER_DIED"),
                     ("array", b'["private-record"]', None))
        for name, raw, first_reason in specimens:
            with self.subTest(name=name):
                path = self.root / (name + ".json")
                path.write_bytes(raw)
                path.chmod(0o600)
                args = n01.argparse.Namespace(artifact_root=self.root / (name + "-artifacts"))

                def malformed_fixture(_args, _artifacts, _source, progress):
                    progress.update(run_id=receipt["run_id"], first_reason=first_reason,
                                    runner_reason="I6_RECEIPT_INVALID" if first_reason else None)
                    n01.read_native_receipt(path, os.getuid(), progress)
                    self.fail("strict native parse must reject this record")

                with mock.patch.object(n01.evidence, "source_snapshot", return_value=source), \
                     mock.patch.object(n01, "run_fixture", side_effect=malformed_fixture), \
                     mock.patch("builtins.print") as output:
                    self.assertEqual(n01.execute(args), 3)
                report = json.loads((args.artifact_root / "acceptance.json").read_bytes())
                result = json.loads((args.artifact_root / "n01-validation.json").read_bytes())
                self.assertEqual((result["status"], result["reason"], result["exit_code"]),
                                 ("invalid", "I6_REPORT_INVALID", 3))
                self.assertEqual(result["acceptance_exit_code"], 1 if first_reason else 2)
                self.assertEqual(result["fixture_failure"], "I6_REPORT_INVALID")
                self.assertEqual(result["fixture_stage"], "native_receipt")
                self.assertEqual(result["native_receipt"], {"status": "invalid", "reason": "I6_REPORT_INVALID",
                                                          "size": len(raw), "sha256": evidence.sha(raw)})
                self.assertFalse(result["n01_inspect_only_passed"])
                self.assertFalse(result["host_v3_acceptance_passed"])
                self.assertFalse((args.artifact_root / "n01-native.json").exists())
                self.assertEqual(report["artifacts"], [])
                if first_reason:
                    self.assertEqual(report["cases"][0]["first_case_failure"]["reason"], first_reason)
                    self.assertEqual(result["runner_reason"], "I6_RECEIPT_INVALID")
                self.assertNotIn("private", json.dumps(result))
                self.assertNotIn("private", json.dumps(report))
                self.assertNotIn("private", str(output.call_args))

    def test_integer_native_projection_is_parsed_without_granting_product_success(self):
        receipt, _ = specimen()
        path = self.root / "native.json"
        raw = evidence.canonical(receipt["native"])
        path.write_bytes(raw)
        path.chmod(0o600)
        progress = {}
        native = n01.read_native_receipt(path, os.getuid(), progress)
        self.assertEqual(native, receipt["native"])
        self.assertEqual(progress, {"stage": "native_receipt", "native_receipt": {
            "status": "parsed", "reason": None, "size": len(raw), "sha256": evidence.sha(raw)}})

    def test_cancellation_retains_prior_failures_without_turning_interrupted_parse_into_invalid(self):
        args = n01.argparse.Namespace(artifact_root=self.root / "artifacts")
        receipt, source = specimen()
        path = self.root / "native.json"
        path.write_bytes(evidence.canonical(receipt["native"]))
        path.chmod(0o600)

        def cancelled_fixture(_args, _artifacts, _source, progress):
            progress.update(run_id=receipt["run_id"], first_reason="I6_ASSERTION_FAILED",
                            cleanup_reason="I6_CLEANUP_FAILED", runner_reason="I6_FINALIZER_MISSING")
            with mock.patch.object(n01.evidence, "parse", side_effect=KeyboardInterrupt):
                n01.read_native_receipt(path, os.getuid(), progress)

        with mock.patch.object(n01.evidence, "source_snapshot", return_value=source), \
             mock.patch.object(n01, "run_fixture", side_effect=cancelled_fixture), \
             mock.patch("builtins.print"):
            self.assertEqual(n01.execute(args), 130)
        report = json.loads((args.artifact_root / "acceptance.json").read_bytes())
        result = json.loads((args.artifact_root / "n01-validation.json").read_bytes())
        self.assertEqual((result["status"], result["reason"], result["exit_code"]),
                         ("cancelled", "I6_OPERATOR_CANCELLED", 130))
        self.assertEqual(result["acceptance_exit_code"], 130)
        self.assertEqual(result["native_receipt"]["status"], "unparsed")
        self.assertIsNone(result["native_receipt"]["reason"])
        self.assertEqual(report["cases"][0]["first_case_failure"]["reason"], "I6_ASSERTION_FAILED")
        self.assertEqual(report["cases"][0]["cleanup_failures"][0]["reason"], "I6_CLEANUP_FAILED")
        self.assertEqual(result["runner_reason"], "I6_FINALIZER_MISSING")
        self.assertFalse(result["n01_inspect_only_passed"])
        self.assertFalse(result["host_v3_acceptance_passed"])

    def test_external_watchdog_returns_non_success_and_reaps_its_live_child(self):
        with self.assertRaisesRegex(n01.FixtureError, "I6_WATCHDOG"):
            n01.run_capture([sys.executable, "-c", "import time; time.sleep(30)"], seconds=0.1)

    def test_environment_refusal_retains_all_required_cases_and_writes_no_product_receipt(self):
        args = n01.argparse.Namespace(artifact_root=self.root / "artifacts", disposable_container=False,
                                     source_root=n01.ROOT, fixture_root=self.root / "never-created",
                                     reviewed_package=self.root / "unused", runner=self.root / "unused")
        source = {"commit": "a" * 40, "tree_sha256": "b" * 64, "dirty": False,
                  "build_features": [], "pr_head": None}
        with mock.patch.object(n01.evidence, "source_snapshot", return_value=source), \
             mock.patch.object(n01, "prepare_accounts", side_effect=AssertionError("mutation")):
            self.assertEqual(n01.execute(args), 2)
        report = json.loads((args.artifact_root / "acceptance.json").read_bytes())
        result = json.loads((args.artifact_root / "n01-validation.json").read_bytes())
        self.assertEqual(len(report["cases"]), 87)
        self.assertEqual({r["case_id"] for r in report["cases"]}, evidence.FAMILIES)
        self.assertTrue(all(row["status"] == "not_run" for row in report["cases"]))
        self.assertFalse(result["n01_inspect_only_passed"])
        self.assertFalse(result["host_v3_acceptance_passed"])
        self.assertEqual(result["required_assertions"], 533)
        self.assertFalse((args.artifact_root / "n01-receipt.json").exists())
        self.assertFalse(args.fixture_root.exists())

    def test_owner_mapping_requires_same_native_owner_and_uses_original_connect(self):
        native = {"owner_id": "a" * 32, "runtime": "7", "trace": {"events": [
            {"capture": None}, {"capture": {"session": "8", "capture_id": "b" * 64,
             "context_digest": "c" * 64, "io_generation": "9", "nonce": "d" * 16, "scope": "e" * 64}}]},
            "observations": [{"kind": "command_accepted", "data": {"command": "connect", "request_id": "1"}},
                             {"kind": "command_accepted", "data": {"command": "connect", "request_id": "4"}}]}
        owner = n01.owner_from_native(native, native["owner_id"])
        self.assertEqual(owner["connect_request"], "1")
        self.assertEqual(owner["session"], "8")
        self.assertIsNone(owner["native_origin"])
        self.assertIsNone(owner["launch_request"])
        with self.assertRaisesRegex(n01.FixtureError, "I6_EVIDENCE_MISSING"):
            n01.owner_from_native(native, "f" * 32)

    def test_missing_native_capture_is_incomplete_instead_of_unhandled_stopiteration(self):
        native = {"owner_id": None, "runtime": "1", "trace": {"events": []}, "observations": []}
        with self.assertRaisesRegex(n01.FixtureError, "I6_EVIDENCE_MISSING"):
            n01.owner_from_native(native, None)

    def test_later_environment_error_does_not_erase_first_runner_and_cleanup_failure(self):
        args = n01.argparse.Namespace(artifact_root=self.root / "artifacts")
        source = {"commit": "a" * 40, "tree_sha256": "b" * 64, "dirty": False,
                  "build_features": [], "pr_head": None}
        def failing_fixture(_args, _artifacts, _source, progress):
            progress.update(run_id="c" * 32, first_reason="I6_ASSERTION_FAILED",
                            cleanup_reason="I6_CLEANUP_FAILED", runner_reason="I6_FINALIZER_MISSING")
            raise FileNotFoundError("private-path-must-not-appear")
        with mock.patch.object(n01.evidence, "source_snapshot", return_value=source), \
             mock.patch.object(n01, "run_fixture", side_effect=failing_fixture):
            self.assertEqual(n01.execute(args), 1)
        report = json.loads((args.artifact_root / "acceptance.json").read_bytes())
        validation = json.loads((args.artifact_root / "acceptance-validation.json").read_bytes())
        self.assertEqual(report["run_id"], "c" * 32)
        self.assertEqual(report["cases"][0]["first_case_failure"]["reason"], "I6_ASSERTION_FAILED")
        self.assertEqual(report["cases"][0]["cleanup_failures"][0]["reason"], "I6_CLEANUP_FAILED")
        self.assertEqual(validation["exit_code"], 1)
        self.assertNotIn("private-path-must-not-appear", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
