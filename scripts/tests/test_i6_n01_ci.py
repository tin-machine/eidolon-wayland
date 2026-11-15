"""N01 Docker lifecycle/receipt gates; mocked engine, never an SSH acceptance claim."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/ci/check-linux-desktop-v3-n01"
sys.path.insert(0, str(ROOT / "scripts/ci"))
import ci_scope
sys.path.insert(0, str(ROOT / "scripts"))
import linux_real_app_v3_n01_ci as export

SOURCE = {"commit": "c" * 40, "tree_sha256": "d" * 64, "dirty": False,
          "build_features": [], "pr_head": None}
CHECKED = {"host_v3_acceptance_passed": False, "n01_inspect_only_passed": True, "exit_code": 2}


FAKE_GIT = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ["N01_TEST_LOG"], "a") as output:
    output.write(json.dumps(["git", *args]) + "\n")
if "--show-toplevel" in args:
    print(os.environ["N01_TEST_SOURCE"])
elif "status" in args:
    if os.environ.get("N01_TEST_DIRTY") == "1":
        print(" M tracked-file")
elif "rev-parse" in args:
    print("c" * 40)
elif args[0] == "clone":
    pathlib.Path(args[-1]).mkdir()
'''

FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, pathlib, shutil, sys
args = sys.argv[1:]
with open(os.environ["N01_TEST_LOG"], "a") as output:
    output.write(json.dumps(["docker", *args]) + "\n")
if args[0] == "build":
    pass
elif args[:2] == ["image", "inspect"]:
    if args[3] == "{{.Id}}":
        print("sha256:" + "b" * 64)
    elif os.environ.get("N01_TEST_BAD_ENTRYPOINT") == "1" and not args[-1].endswith("-build"):
        print('["/bin/bash"]')
    else:
        print('["python3","/src/scripts/linux_real_app_v3_n01.py"]')
elif args[0] == "create":
    print(("d" if args[args.index("--network") + 1] == "bridge" else "a") * 64)
elif args[0] == "start":
    key = "N01_TEST_PREPARE_EXIT" if args[-1] == "d" * 64 else "N01_TEST_RUNTIME_EXIT"
    raise SystemExit(int(os.environ.get(key, "0")))
elif args[0] == "inspect":
    prepare = args[-1] == "d" * 64
    if args[2] == "{{.State.Running}}":
        print(os.environ.get("N01_TEST_PREPARE_RUNNING" if prepare else "N01_TEST_RUNNING", "false"))
    else:
        print(os.environ.get("N01_TEST_PREPARE_EXIT" if prepare else "N01_TEST_RUNTIME_EXIT", "0"))
elif args[0] == "cp":
    shutil.copytree(os.environ["N01_TEST_RECEIPTS"], args[-1], dirs_exist_ok=True, symlinks=True)
'''

FAKE_TIMEOUT = r'''#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
start = args.index("docker")
if (args[start + 1] == "start" and args[-1] == "a" * 64
        and os.environ.get("N01_TEST_WATCHDOG") == "1"):
    raise SystemExit(124)
os.execvp("docker", args[start:])
'''


class DockerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="eidolon-n01-ci-test-")
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.tools = self.root / "bin"
        self.tools.mkdir()
        for name, text in (("git", FAKE_GIT), ("docker", FAKE_DOCKER), ("timeout", FAKE_TIMEOUT)):
            path = self.tools / name
            path.write_text(text, encoding="utf-8")
            path.chmod(0o700)
        self.receipts = self.root / "incoming"
        self.receipts.mkdir()
        self.out = self.root / "artifacts"
        self.log = self.root / "calls.jsonl"
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "scripts").mkdir()
        # Only the mock-engine transport test replaces the oracle. The product
        # wrapper has no environment switch or success-injection interface.
        (self.source / "scripts/linux_real_app_v3_n01_ci.py").write_text(
            "import sys\nfrom unittest.mock import patch\n"
            f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
            "import linux_real_app_v3_n01_ci as module\n"
            f"with patch.object(module.evidence, 'source_snapshot', return_value={SOURCE!r}), "
            f"patch.object(module.evidence, 'validate_acceptance', return_value={CHECKED!r}):\n"
            "    raise SystemExit(module.main())\n", encoding="utf-8")
        self.env = {**os.environ, "PATH": str(self.tools) + os.pathsep + os.environ["PATH"],
                    "TMPDIR": str(self.root), "N01_TEST_LOG": str(self.log),
                    "N01_TEST_SOURCE": str(self.source), "N01_TEST_RECEIPTS": str(self.receipts)}
        self.write("acceptance.json", {"status": "incomplete"})
        self.write("acceptance-validation.json", CHECKED)
        self.write("n01-validation.json", {"status": "pass", "n01_inspect_only_passed": True, "source": SOURCE})
        self.write("n01-receipt.json", {"test_double": True})

    def write(self, name, value):
        (self.receipts / name).write_text(json.dumps(value), encoding="utf-8")

    def execute(self):
        return subprocess.run(["bash", str(SCRIPT), "--artifact-root", str(self.out)],
                              env=self.env, text=True, capture_output=True, timeout=20)

    def calls(self, tool=None):
        rows = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return [row for row in rows if tool is None or row[0] == tool]

    def assert_cleaned(self):
        self.assertTrue(any(row[:3] == ["docker", "rm", "--force"] for row in self.calls()))
        self.assertFalse(list(self.root.glob("eidolon-i6-n01.*")))

    def test_smoke_requires_partial_i6_receipts_and_disposable_boundaries(self):
        (self.receipts / "secret-key").write_text("not an artifact", encoding="utf-8")
        self.write("unrequested.json", {"private": "must not export"})
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({p.name for p in self.out.iterdir()},
                         {"acceptance.json", "acceptance-validation.json", "n01-validation.json",
                          "n01-receipt.json", "container.json"})
        envelope = json.loads((self.out / "container.json").read_text())
        self.assertEqual(envelope["source_commit"], "c" * 40)
        self.assertEqual(envelope["image_id"], "sha256:" + "b" * 64)
        self.assertIs(envelope["formal_i6_passed"], False)
        creates = [row for row in self.calls("docker") if row[1] == "create"]
        self.assertEqual(len(creates), 2)
        prepare, create = creates
        self.assertEqual(prepare[prepare.index("--network") + 1], "bridge")
        self.assertEqual(prepare[prepare.index("--entrypoint") + 1], "/bin/bash")
        self.assertEqual(prepare[-1], "/src/scripts/fixtures/linux-desktop-v3-acceptance/prepare-n01-image")
        self.assertEqual(create[create.index("--network") + 1], "none")
        self.assertEqual(create[create.index("--pids-limit") + 1], "256")
        self.assertEqual(create[create.index("--memory") + 1], "6g")
        for invocation in creates:
            self.assertFalse(set(invocation) & {"--privileged", "--pid", "--mount", "--volume", "-v", "--env", "-e"})
        commit = next(row for row in self.calls("docker") if row[1] == "commit")
        self.assertIn('ENTRYPOINT ["python3","/src/scripts/linux_real_app_v3_n01.py"]', commit)
        self.assertIn("CMD []", commit)
        removed = {row[3] for row in self.calls("docker") if row[1:3] == ["rm", "--force"]}
        self.assertEqual(removed, {"a" * 64, "d" * 64})
        clones = [row for row in self.calls("git") if row[1] == "clone"]
        self.assertEqual(len(clones), 1)
        self.assertIn("--no-local", clones[0])
        self.assertIn("--no-hardlinks", clones[0])
        self.assert_cleaned()

    def test_failed_preparation_never_commits_or_runs_product(self):
        self.env["N01_TEST_PREPARE_EXIT"] = "2"
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len([row for row in self.calls("docker") if row[1] == "create"]), 1)
        self.assertFalse(any(row[1] in {"commit", "cp"} for row in self.calls("docker")))
        self.assertEqual(list(self.out.iterdir()), [])
        self.assert_cleaned()

    def test_running_preparation_is_not_committed_even_with_zero_exit(self):
        self.env["N01_TEST_PREPARE_RUNNING"] = "true"
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(row[1] == "commit" for row in self.calls("docker")))
        self.assert_cleaned()

    def test_prepared_image_must_restore_runtime_entrypoint(self):
        self.env["N01_TEST_BAD_ENTRYPOINT"] = "1"
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len([row for row in self.calls("docker") if row[1] == "create"]), 1)
        self.assertFalse(any(row[1] == "cp" for row in self.calls("docker")))
        self.assert_cleaned()

    def test_product_exit_failure_is_not_overridden_by_pass_receipt(self):
        self.env["N01_TEST_RUNTIME_EXIT"] = "1"
        self.write("n01-native.json", {"schema": "eidolon-i6-n01-native-v1", "error": "I6_ASSERTION_FAILED"})
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads((self.out / "container.json").read_text())["container_exit"], 1)
        diagnostic = self.out / "n01-native.json"
        self.assertEqual(json.loads(diagnostic.read_text())["error"], "I6_ASSERTION_FAILED")
        self.assertEqual(diagnostic.stat().st_mode & 0o777, 0o600)
        self.assert_cleaned()

    def test_external_watchdog_and_rescue_cannot_pass_even_if_container_exits_zero(self):
        self.env.update(N01_TEST_WATCHDOG="1", N01_TEST_RUNNING="true")
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        envelope = json.loads((self.out / "container.json").read_text())
        self.assertEqual(envelope["attach_exit"], 124)
        self.assertIs(envelope["external_rescue"], True)
        self.assertTrue(any(row[:3] == ["docker", "stop", "--time"] for row in self.calls()))
        self.assert_cleaned()

    def test_missing_receipt_does_not_pass_with_success_exit(self):
        (self.receipts / "n01-receipt.json").unlink()
        self.write("n01-native.json", {"schema": "eidolon-i6-n01-native-v1", "error": None})
        self.assertNotEqual(self.execute().returncode, 0)
        self.assertTrue((self.out / "n01-native.json").is_file())
        self.assert_cleaned()

    def test_optional_diagnostic_is_not_an_acceptance_gate(self):
        self.write("n01-native.json", {"schema": "eidolon-i6-n01-native-v1", "error": "I6_ASSERTION_FAILED"})
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.out / "n01-native.json").is_file())
        self.assert_cleaned()

    def test_invalid_optional_diagnostic_is_omitted(self):
        (self.receipts / "n01-native.json").write_text('{"invalid":', encoding="utf-8")
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.out / "n01-native.json").exists())
        self.assert_cleaned()

    def test_optional_diagnostic_symlink_is_never_exported(self):
        target = self.root / "private.json"
        target.write_text('{"secret":"not exported"}', encoding="utf-8")
        (self.receipts / "n01-native.json").symlink_to(target)
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.out / "n01-native.json").exists())
        self.assert_cleaned()

    def test_optional_diagnostic_larger_than_bound_is_omitted(self):
        (self.receipts / "n01-native.json").write_bytes(b'"' + b"a" * (1024 * 1024) + b'"')
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.out / "n01-native.json").exists())
        self.assert_cleaned()

    def test_formal_acceptance_promotion_is_rejected(self):
        self.write("acceptance-validation.json", {"host_v3_acceptance_passed": True, "exit_code": 0})
        self.assertNotEqual(self.execute().returncode, 0)
        self.assert_cleaned()

    def test_receipt_symlink_is_never_exported(self):
        target = self.root / "private.json"
        target.write_text('{"secret":"not exported"}', encoding="utf-8")
        (self.receipts / "n01-receipt.json").unlink()
        (self.receipts / "n01-receipt.json").symlink_to(target)
        self.assertNotEqual(self.execute().returncode, 0)
        self.assertFalse((self.out / "n01-receipt.json").exists())
        self.assert_cleaned()

    def test_dirty_source_is_rejected_before_docker(self):
        self.env["N01_TEST_DIRTY"] = "1"
        self.assertNotEqual(self.execute().returncode, 0)
        self.assertEqual(self.calls("docker"), [])

    def test_nonempty_destination_is_preserved_and_rejected(self):
        self.out.mkdir(mode=0o700)
        saved = self.out / "saved.json"
        saved.write_text("preserve", encoding="utf-8")
        self.assertNotEqual(self.execute().returncode, 0)
        self.assertEqual(saved.read_text(), "preserve")
        self.assertEqual(self.calls("docker"), [])


class ScopeTests(unittest.TestCase):
    def test_n01_build_runner_and_oracle_paths_keep_all_existing_checks(self):
        for path in ("scripts/fixtures/linux-desktop-v3-acceptance/Dockerfile.n01",
                     "scripts/ci/check-linux-desktop-v3-n01", "scripts/linux_real_app_v3_n01.py",
                     "scripts/fixtures/linux-real-app-v3-host/src/main.rs"):
            with self.subTest(path=path):
                self.assertEqual(ci_scope.classify([path]), ci_scope.ALL)

    def test_n01_rename_into_documentation_does_not_hide_existing_checks(self):
        paths = ci_scope.decode_name_status(
            b"R100\0scripts/fixtures/linux-desktop-v3-acceptance/Dockerfile.n01\0docs/example.md\0")
        self.assertEqual(ci_scope.classify(paths), ci_scope.ALL)


class ExportRevalidationTests(unittest.TestCase):
    def revalidate_mutation(self, *, mutate_receipt=None, mutate_case=None):
        """Keep producer summaries consistent so only the real evidence gate decides."""
        from test_linux_real_app_v3_n01_oracle import specimen
        import linux_real_app_v3_n01 as supervisor

        receipt, source = specimen()
        with tempfile.TemporaryDirectory(prefix="eidolon-n01-export-mutation-") as temporary:
            root = Path(temporary)
            incoming = root / "incoming"
            incoming.mkdir(mode=0o700)
            report, _ = supervisor.assemble_report(source, source, receipt["native"], receipt["environment"], incoming)
            if mutate_receipt is not None:
                mutate_receipt(receipt)
                raw = export.evidence.canonical(receipt) + b"\n"
                (incoming / "n01-receipt.json").write_bytes(raw)
                artifact = next(a for a in report["artifacts"] if a["path"] == "n01-receipt.json")
                artifact.update(size=len(raw), sha256=export.evidence.sha(raw))
            case = next(row for row in report["cases"] if (row["case_id"], row["variant"]) == ("N01", "inspect_only"))
            if mutate_case is not None:
                mutate_case(case)
            export.evidence.write_report(incoming / "acceptance.json", report)
            checked = export.evidence.validate_acceptance(export.evidence.canonical(report), incoming, source)
            export.evidence.write_report(incoming / "acceptance-validation.json", checked)
            # A producer's pass flag must not bypass failed independent checks,
            # even when its saved aggregate matches the validator exactly.
            export.evidence.write_report(incoming / "n01-validation.json",
                                         {"status": "pass", "n01_inspect_only_passed": True, "source": source})
            image = root / "image.id"
            image.write_text("sha256:" + "b" * 64)
            output = root / "output"
            output.mkdir(mode=0o700)
            with mock.patch.object(export.evidence, "source_snapshot", return_value=source):
                accepted = export.export_and_validate(incoming, output, image, source["commit"], "0", "0", "false", ROOT)
            return checked, accepted

    def test_stop_and_ack_after_runtime_stop_cannot_pass_export_revalidation(self):
        def move_stop(receipt):
            native = receipt["native"]
            events = native["trace"]["events"]
            late = [event for event in events
                    if (event["kind"] == "control_send_attempt" and event["data"]["action"] == "stop")
                    or (event["kind"] == "protocol_event"
                        and event["data"]["record"]["event"]["type"] == "stopped")]
            self.assertEqual(len(late), 2)
            events[:] = [event for event in events if event not in late] + late
            renumbered = {int(event["sequence"]): number for number, event in enumerate(events, 1)}
            for number, event in enumerate(events, 1):
                event["sequence"] = str(number)
            for observation in native["observations"]:
                watermark = int(observation["transport_sequence"])
                observation["transport_sequence"] = str(renumbered[watermark] if watermark else 0)

        checked, accepted = self.revalidate_mutation(mutate_receipt=move_stop)
        self.assertEqual((checked["status"], checked["reason"]), ("invalid", "I6_TRACE_INVALID"))
        self.assertFalse(checked["n01_inspect_only_passed"])
        self.assertFalse(accepted)

    def test_missing_census_reference_cannot_pass_export_revalidation(self):
        def hide_finalizer(case):
            finalizer = next(row for row in case["cleanup"]["census"] if row["resource"] == "journal_finalizer")
            finalizer["state"] = "not_created"
            finalizer["evidence_refs"][0]["artifact"] = "missing-finalizer.json"

        checked, accepted = self.revalidate_mutation(mutate_case=hide_finalizer)
        self.assertEqual(checked["status"], "incomplete")
        self.assertIn("artifact/missing-finalizer.json", checked["missing"])
        self.assertFalse(checked["n01_inspect_only_passed"])
        self.assertNotIn("N01/inspect_only", checked["verified_case_oracles"])
        self.assertFalse(accepted)

    def test_cancelled_n01_has_no_partial_pass_and_cannot_pass_export_revalidation(self):
        checked, accepted = self.revalidate_mutation(
            mutate_case=lambda case: case.update(status="cancelled", reason="I6_OPERATOR_CANCELLED"))
        self.assertEqual((checked["status"], checked["reason"], checked["exit_code"]),
                         ("cancelled", "I6_OPERATOR_CANCELLED", 130))
        self.assertFalse(checked["n01_inspect_only_passed"])
        self.assertNotIn("N01/inspect_only", checked["verified_case_oracles"])
        self.assertFalse(accepted)

    def test_real_oracle_specimen_is_revalidated_and_damaged_receipt_rejected(self):
        from test_linux_real_app_v3_n01_oracle import specimen
        import linux_real_app_v3_n01 as supervisor

        receipt, source = specimen()
        with tempfile.TemporaryDirectory(prefix="eidolon-n01-export-test-") as temporary:
            root = Path(temporary)
            incoming = root / "incoming"
            incoming.mkdir(mode=0o700)
            report, _ = supervisor.assemble_report(source, source, receipt["native"], receipt["environment"], incoming)
            export.evidence.write_report(incoming / "acceptance.json", report)
            checked = export.evidence.validate_acceptance(export.evidence.canonical(report), incoming, source)
            self.assertTrue(checked["n01_inspect_only_passed"], checked)
            export.evidence.write_report(incoming / "acceptance-validation.json", checked)
            export.evidence.write_report(incoming / "n01-validation.json",
                                         {"status": "pass", "n01_inspect_only_passed": True, "source": source})
            image = root / "image.id"
            image.write_text("sha256:" + "b" * 64)
            with mock.patch.object(export.evidence, "source_snapshot", return_value=source):
                good = root / "good"
                good.mkdir(mode=0o700)
                self.assertTrue(export.export_and_validate(incoming, good, image, source["commit"], "0", "0", "false", ROOT))
                damaged = json.loads((incoming / "n01-receipt.json").read_bytes())
                damaged["native"]["start_count"] = 1
                (incoming / "n01-receipt.json").write_text(json.dumps(damaged))
                bad = root / "bad"
                bad.mkdir(mode=0o700)
                self.assertFalse(export.export_and_validate(incoming, bad, image, source["commit"], "0", "0", "false", ROOT))


if __name__ == "__main__":
    unittest.main()
