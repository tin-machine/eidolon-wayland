"""A0 orchestration gates; no installed application or user HOME is touched.

Runtime dependencies are replaced only at the explicit stage boundaries. These
tests prove refusal and preservation contracts, not real Mousepad feasibility.
The frozen-v2 fixture was captured from remote e401918, not computed by a build
or regenerated from the checkout under test.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import linux_real_app_check as check
import linux_real_app_wayland as wayland


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, data, mode=0o600):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)


class TemporaryCheck(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.home = self.base / "operator-home"
        self.home.mkdir(mode=0o700)
        self.home_sentinel = self.home / "personal-document.txt"
        write(self.home_sentinel, b"Personal data is outside the A0 check.\n")
        self.original_home = self.home_sentinel.read_bytes()

    def command(self, *args):
        output = io.StringIO()
        old_umask = os.umask(0o077)
        try:
            with contextlib.redirect_stdout(output):
                status = check.main(list(args))
        finally:
            os.umask(old_umask)
        result = json.loads(output.getvalue())
        self.assertEqual(self.home_sentinel.read_bytes(), self.original_home)
        return status, result

    def missing_package(self):
        system = self.base / "system"
        system.mkdir(mode=0o700)
        with mock.patch.object(check.inspect, "SYSTEM_OWNER_UID", os.getuid()):
            result = check.inspect.inspect_host(system_root=system, home=self.home)
        self.assertEqual(result["reason"], "APP_NOT_INSTALLED")
        check.inspect.validate_inspection_receipt(result)
        return result

    @contextlib.contextmanager
    def no_processes(self):
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("no process may run")) as spawn, \
             mock.patch.object(check, "run_bounded", side_effect=AssertionError("no command may run")) as command, \
             mock.patch.object(check, "build_settings", side_effect=AssertionError("no build may run")) as build, \
             mock.patch.object(check, "build_compositor", side_effect=AssertionError("no compositor may run")) as compositor, \
             mock.patch.object(wayland, "run_feasibility", side_effect=AssertionError("no app may run")) as application:
            yield
            for called in (spawn, command, build, compositor, application):
                called.assert_not_called()


class GateCommandTests(TemporaryCheck):
    def test_acceptance_is_not_implemented_exit_two_even_with_a_descriptor(self):
        artifact = self.base / "acceptance"
        with self.no_processes(), \
             mock.patch.object(check.inspect, "inspect_host", side_effect=AssertionError("no inspection")), \
             mock.patch.object(check.inspect, "_read_operator_descriptor", side_effect=AssertionError("no descriptor read")):
            status, result = self.command("--stage", "acceptance", "--artifact-root", str(artifact),
                                          "--reviewed-package", str(self.base / "absent-descriptor.json"))
        self.assertEqual(status, 2)
        self.assertEqual(result, {"schema": 1, "stage": "acceptance", "status": "not_implemented",
                                 "reason": "APP_V3_NOT_IMPLEMENTED", "available": False,
                                 "production_acceptance_passed": False, "quest_verified": False})
        receipt = artifact / "acceptance.json"
        self.assertEqual(json.loads(receipt.read_bytes()), result)
        self.assertEqual(artifact.stat().st_mode & 0o777, 0o700)
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(artifact.iterdir()), [receipt])

    def test_missing_package_writes_failure_without_building_or_spawning(self):
        observed = self.missing_package()
        artifact = self.base / "missing-package"
        with self.no_processes(), mock.patch.object(check.inspect, "inspect_host", return_value=observed) as inspection:
            status, result = self.command("--stage", "feasibility", "--artifact-root", str(artifact))
        inspection.assert_called_once()
        self.assertEqual(status, 2)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "APP_NOT_INSTALLED")
        for key in ("a0_passed", "available", "production_acceptance_passed", "quest_verified", "application_payload_logged"):
            self.assertIs(result[key], False)
        self.assertEqual(result["checks"], {})
        self.assertIn("v3_acceptance", result["pending"])
        self.assertIn("quest", result["pending"])
        self.assertEqual(json.loads((artifact / "feasibility.json").read_bytes()), result)
        recorded = json.loads((artifact / "inspection/inspection.json").read_bytes())
        self.assertEqual(recorded, observed)
        self.assertEqual({p.name for p in artifact.iterdir()}, {"inspection", "feasibility.json"})


class FoundationAggregationTests(TemporaryCheck):
    """Exercise the real orchestrator with explicit, nonexecuting stage doubles."""
    @staticmethod
    def inspection():
        value = {"schema": 1, "app_id": check.inspect.APP_ID, "stage": "inspection",
                 "inspection_passed": True, "available": False, "runtime_verified": False, "reason": "NONE",
                 "reviewed_descriptor_sha256": "1" * 64, "executable_sha256": "a" * 64,
                 "dependency_fingerprint": "2" * 64,
                 "checks": {"executable": True, "package": True, "default_backend": True, "schema": True},
                 "receipt_capacity": None, "pending": ["feasibility", "acceptance", "quest"]}
        check.inspect.validate_inspection_receipt(value)
        return value

    @staticmethod
    def cleanup():
        return {"schema": 1, "complete": True, "pending": False, "reason": "NONE",
                "term_signals": 0, "kill_signals": 0, "reaped": 4, "open_pidfds": 0,
                "root_pidfds_closed": True, "output_pipes_eof": True}

    def application(self):
        return {"schema": 1, "stage": "wayland", "state": "partial", "status": "incomplete", "error": None,
                "feasibility_verified": False, "physical_input_verified": False, "production_ready": False,
                "checks": {name: name not in wayland.PENDING_CHECKS for name in wayland.REQUIRED_CHECKS},
                "pending": list(wayland.PENDING_CHECKS), "cleanup": self.cleanup(), "output_bytes": {},
                "payload_logged": False, "titles_logged": False, "pixels_logged": False,
                "waypipe_ends": 2, "private_bus": True, "profile_kind": "synthetic",
                "waypipe_version": "0.11.3", "waypipe_sha256": sha(b"Waypipe stage fixture.\n"),
                "mousepad_sha256": "a" * 64, "source_argv": "mousepad --disable-server"}

    @contextlib.contextmanager
    def stages(self, application, *, final_inspection=None, raised=None):
        compositor = self.base / "compositor-stage-fixture"
        binary = self.base / "waypipe-stage-fixture"
        write(compositor, b"Compositor stage fixture.\n", 0o500)
        write(binary, b"Waypipe stage fixture.\n", 0o500)
        observed = self.inspection()
        settings = {"policy_values_types_and_readonly": True, "ordinary_notifications_and_transactions": True,
                    "ordinary_persistence_in_new_process": True, "separate_profile_isolation": True,
                    "missing_module_fallback_rejected": True, "invalid_profile_exits_without_fallback": True,
                    "probes": {}}
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("no process may run")), \
             mock.patch.object(check, "run_bounded", side_effect=AssertionError("no command may run")), \
             mock.patch.object(check.inspect, "inspect_host", side_effect=[observed, final_inspection or observed]), \
             mock.patch.object(check, "build_settings", return_value={"files": {"gio/libeidolon-settings.so": "b" * 64}}), \
             mock.patch.object(check, "settings_checks", return_value=settings), \
             mock.patch.object(check, "waypipe_binary", return_value=(binary, sha(binary.read_bytes()))), \
             mock.patch.object(check, "build_compositor", return_value=compositor), \
             mock.patch.object(wayland, "run_feasibility", return_value=application, side_effect=raised) as controller:
            yield
            controller.assert_called_once()

    def test_partial_gui_file_evidence_stays_incomplete_exit_two(self):
        self.assertEqual(tuple(check.APPLICATION_CHECKS), wayland.REQUIRED_CHECKS)
        self.assertEqual(tuple(check.FOUNDATION_PENDING), wayland.PENDING_CHECKS)
        artifact = self.base / "partial"
        with self.stages(self.application()):
            status, result = self.command("--stage", "feasibility", "--artifact-root", str(artifact))
        self.assertEqual((status, result["status"], result["reason"]), (2, "incomplete", "APP_A0_INCOMPLETE"))
        self.assertEqual(result["pending"], list(wayland.PENDING_CHECKS) + ["v3_acceptance", "quest"])
        for key in ("a0_passed", "available", "production_acceptance_passed", "quest_verified"):
            self.assertIs(result[key], False)
        self.assertEqual(json.loads((artifact / "feasibility.json").read_bytes()), result)

    def test_forged_full_pass_from_a_lower_stage_never_creates_a0_success(self):
        application = self.application()
        application.update(status="passed", state="complete", feasibility_verified=True, pending=[])
        application["checks"] = dict.fromkeys(wayland.REQUIRED_CHECKS, True)
        with self.stages(application):
            status, result = self.command("--stage", "feasibility", "--artifact-root", str(self.base / "forged-pass"))
        self.assertEqual((status, result["status"], result["reason"]), (2, "failed", "APP_WAYLAND_RECEIPT_INVALID"))
        self.assertIs(result["a0_passed"], False)
        self.assertIs(result["available"], False)

    def test_contradictory_or_unfinished_application_receipts_are_rejected(self):
        for mutate in (
            lambda value: value.update(schema=True),
            lambda value: value.update(state="fail"),
            lambda value: value.update(stage="inspection"),
            lambda value: value.update(error="APP_WAYLAND_FAILED"),
            lambda value: value.update(physical_input_verified=True),
            lambda value: value.update(pending=[]),
            lambda value: value["checks"].update(first_main=1),
            lambda value: value["checks"].update(unknown_check=True),
            lambda value: value["cleanup"].update(complete=False, pending=True),
            lambda value: value["cleanup"].update(output_pipes_eof=False),
            lambda value: value["cleanup"].update(open_pidfds=False),
        ):
            application = self.application()
            mutate(application)
            with self.subTest(mutation=mutate), self.assertRaises(check.BuildError):
                check.validate_application_foundation(application)

    def test_final_package_refusal_cannot_keep_the_earlier_static_success(self):
        final = self.inspection()
        final.update(inspection_passed=False, reason="APP_DEPENDENCY_CHANGED")
        with self.stages(self.application(), final_inspection=final):
            status, result = self.command("--stage", "feasibility", "--artifact-root", str(self.base / "changed-package"))
        self.assertEqual((status, result["status"], result["reason"]), (2, "failed", "APP_DEPENDENCY_CHANGED"))
        self.assertIs(result["a0_passed"], False)

    def test_application_evidence_must_match_the_inspected_executable(self):
        application = self.application()
        application["mousepad_sha256"] = "c" * 64
        with self.stages(application):
            status, result = self.command("--stage", "feasibility", "--artifact-root", str(self.base / "other-executable"))
        self.assertEqual((status, result["status"], result["reason"]), (2, "failed", "APP_WAYLAND_EVIDENCE_MISMATCH"))
        self.assertIs(result["a0_passed"], False)

    def test_controller_wrapper_refusal_is_a_fixed_failure_receipt(self):
        error = wayland.WaylandError("APP_WAYLAND_WAYPIPE_PATH")
        with self.stages(self.application(), raised=error):
            status, result = self.command("--stage", "feasibility", "--artifact-root", str(self.base / "wrapper-refusal"))
        self.assertEqual((status, result["status"], result["reason"]), (2, "failed", "APP_WAYLAND_WAYPIPE_PATH"))
        self.assertIs(result["a0_passed"], False)

    def test_live_cleanup_owner_is_retried_and_eventual_cleanup_does_not_erase_failure(self):
        application = self.application()
        application.update(state="fail", status="failed", error="APP_WAYLAND_CLEANUP_PENDING")
        application["cleanup"] = dict(self.cleanup(), complete=False, pending=True, open_pidfds=1)
        run = mock.Mock()
        first = wayland.CleanupPending(run)
        first.receipt = application
        run.finish.side_effect = [OSError("private transient descriptor text"),
                                  wayland.CleanupPending(run), self.cleanup()]
        signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
        handlers_before = {number: signal.getsignal(number) for number in signals}
        artifact = self.base / "pending-cleanup"
        with self.stages(application, raised=first):
            status, result = self.command("--stage", "feasibility", "--artifact-root", str(artifact))
        self.assertEqual(run.finish.call_count, 3)
        self.assertEqual({number: signal.getsignal(number) for number in signals}, handlers_before)
        self.assertEqual((status, result["status"], result["reason"]), (2, "failed", "APP_WAYLAND_CLEANUP_PENDING"))
        self.assertIs(result["a0_passed"], False)
        self.assertTrue(result["checks"]["application"]["cleanup"]["complete"])
        pending = json.loads((artifact / "cleanup-pending.json").read_bytes())
        self.assertEqual(pending, {"schema": 1, "stage": "cleanup", "complete": False,
                                   "reason": "APP_WAYLAND_CLEANUP_PENDING", "available": False})
        self.assertNotIn("private transient descriptor text", json.dumps(result))


class ArtifactSafetyTests(TemporaryCheck):
    def test_nonempty_artifact_is_refused_and_existing_bytes_are_preserved(self):
        artifact = self.base / "existing"
        artifact.mkdir(mode=0o700)
        sentinel = artifact / "previous.json"
        write(sentinel, b"Previous run must not be overwritten.\n")
        with self.no_processes():
            status, result = self.command("--stage", "acceptance", "--artifact-root", str(artifact))
        self.assertEqual((status, result["reason"]), (2, "APP_CHECK_ARTIFACT_NOT_EMPTY"))
        self.assertEqual(sentinel.read_bytes(), b"Previous run must not be overwritten.\n")
        self.assertEqual(list(artifact.iterdir()), [sentinel])

    def test_symlink_artifact_and_symlink_parent_never_write_to_target(self):
        target = self.base / "target"
        target.mkdir(mode=0o700)
        alias = self.base / "alias"
        alias.symlink_to(target, target_is_directory=True)
        for path in (alias, alias / "new-run"):
            with self.subTest(path=path), self.no_processes():
                status, result = self.command("--stage", "acceptance", "--artifact-root", str(path))
            self.assertEqual((status, result["reason"]), (2, "APP_CHECK_ARTIFACT_INVALID"))
        self.assertEqual(list(target.iterdir()), [])

    def test_insecure_or_relative_artifact_root_is_refused(self):
        insecure = self.base / "insecure"
        insecure.mkdir(mode=0o755)
        # Earlier suites may install umask077. Establish the intended unsafe
        # mode explicitly so this case cannot silently become a private root.
        insecure.chmod(0o755)
        for path in (insecure, Path("relative-artifact")):
            with self.subTest(path=path), self.no_processes():
                status, result = self.command("--stage", "acceptance", "--artifact-root", str(path))
            self.assertEqual((status, result["reason"]), (2, "APP_CHECK_ARTIFACT_INVALID"))
        self.assertEqual(list(insecure.iterdir()), [])
        self.assertEqual(insecure.stat().st_mode & 0o777, 0o755)

    def test_receipt_leaf_is_exclusive_and_cannot_follow_a_symlink(self):
        artifact = check.private_artifact_root(self.base / "new-run")
        receipt = artifact / "acceptance.json"
        check.write_json(receipt, {"schema": 1})
        original = receipt.read_bytes()
        with self.assertRaises(FileExistsError):
            check.write_json(receipt, {"schema": 2})
        self.assertEqual(receipt.read_bytes(), original)
        receipt.unlink()
        receipt.symlink_to(self.home_sentinel)
        with self.assertRaises(OSError):
            check.write_json(receipt, {"schema": 3})
        self.assertEqual(self.home_sentinel.read_bytes(), self.original_home)

    def test_replaced_artifact_parent_is_not_followed_after_validation(self):
        artifact = check.private_artifact_root(self.base / "captured")
        retained = self.base / "original-directory"
        artifact.rename(retained)
        artifact.symlink_to(self.home, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            check.write_json(artifact / "acceptance.json", {"schema": 1})
        self.assertFalse((self.home / "acceptance.json").exists())
        self.assertEqual(list(retained.iterdir()), [])
        self.assertEqual(self.home_sentinel.read_bytes(), self.original_home)

    def test_receipt_size_limit_is_enforced_before_creating_output(self):
        artifact = check.private_artifact_root(self.base / "bounded")
        receipt = artifact / "feasibility.json"
        with self.assertRaisesRegex(check.BuildError, "APP_CHECK_RECEIPT_LIMIT"):
            check.write_json(receipt, {"data": "x" * (128 * 1024)})
        self.assertFalse(receipt.exists())
        self.assertEqual(list(artifact.iterdir()), [])


class WaypipeInputTests(TemporaryCheck):
    def setUp(self):
        super().setUp()
        self.repo = self.base / "repository"
        self.repo.mkdir(mode=0o700)
        self.metadata = self.repo / "native/generated/waypipe-host.json"
        self.binary = self.repo / "fixture-waypipe"
        write(self.binary, b"Synthetic hash input. Never execute this file.\n", 0o500)
        self.value = {"target": "host", "version": "0.11.3", "executable": str(self.binary),
                      "sha256": sha(self.binary.read_bytes())}
        write(self.metadata, json.dumps(self.value).encode())
        self.root_patch = mock.patch.object(check, "ROOT", self.repo)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def test_metadata_hash_check_never_executes_the_binary(self):
        with self.no_processes():
            path, digest = check.waypipe_binary()
        self.assertEqual(path, self.binary)
        self.assertEqual(digest, self.value["sha256"])
        self.binary.chmod(0o700)
        self.binary.write_bytes(b"Changed executable bytes.\n")
        with self.assertRaisesRegex(check.BuildError, "APP_WAYPIPE_UNVERIFIED"):
            check.waypipe_binary()

    def test_metadata_and_executable_symlinks_are_refused(self):
        for path in (self.metadata, self.binary):
            with self.subTest(path=path):
                original = path.with_name(path.name + ".original")
                path.rename(original)
                path.symlink_to(original)
                with self.assertRaisesRegex(check.BuildError, "APP_WAYPIPE_UNVERIFIED"):
                    check.waypipe_binary()
                path.unlink()
                original.rename(path)

    def test_oversized_metadata_is_rejected_before_any_content_read(self):
        write(self.metadata, b"x" * (65536 + 1))
        with mock.patch.object(check.inspect.os, "read", side_effect=AssertionError("oversized file must not be read")):
            with self.assertRaisesRegex(check.BuildError, "APP_WAYPIPE_UNVERIFIED"):
                check.waypipe_binary()

    def test_oversized_executable_is_rejected_without_hashing_its_contents(self):
        # Sparse size avoids allocating 64 MiB merely to verify the bound.
        self.binary.chmod(0o700)
        with self.binary.open("wb") as output:
            output.truncate(64 * 1024 * 1024 + 1)
        real_read = os.read
        def bounded_read(fd, size):
            self.assertLessEqual(os.fstat(fd).st_size, 64 * 1024 * 1024)
            return real_read(fd, size)
        with mock.patch.object(check.inspect.os, "read", side_effect=bounded_read):
            with self.assertRaisesRegex(check.BuildError, "APP_WAYPIPE_UNVERIFIED"):
                check.waypipe_binary()


class RecoveryGuardIntegrationTests(TemporaryCheck):
    def test_seeded_recovery_is_refused_without_spawn_or_byte_changes(self):
        artifact = check.private_artifact_root(self.base / "recovery")
        observations = []
        actual_guard = check.inspect.guard_recovery_state
        def observed_guard(root):
            paths = (root / check.KEYFILE, root / "data/Mousepad/autosave-1", root / "Documents/input.txt")
            before = {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mode & 0o777,
                                                    path.stat().st_mtime_ns) for path in paths if path.exists()}
            result = actual_guard(root)
            after = {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mode & 0o777,
                                                   path.stat().st_mtime_ns) for path in paths if path.exists()}
            self.assertEqual(before, after)
            observations.append((before, result))
            return result
        with self.no_processes(), mock.patch.object(check.inspect, "guard_recovery_state", side_effect=observed_guard):
            result = check.recovery_checks(artifact)
        self.assertEqual(len(observations), 3)
        self.assertTrue(observations[0][1]["clear"])
        for _, state in observations[1:]:
            self.assertFalse(state["clear"])
            self.assertEqual(state["reason"], "APP_STATE_RECOVERY_REQUIRED")
        self.assertEqual(observations[1][0][str(check.KEYFILE)][0],
                         b"[org/xfce/mousepad/state/application]\nsession=['file:///fixed-a0-unsaved']\n")
        self.assertEqual(observations[2][0]["data/Mousepad/autosave-1"][0], b"Eidolon A0 unsaved document\n")
        self.assertEqual(result, {"empty_profile_admitted": True, "nonempty_session_refused": True,
                                 "autosave_refused": True, "recovery_bytes_preserved": True,
                                 "application_spawned_for_seeded_recovery": False})

    def test_a_guard_that_admits_recovery_data_cannot_issue_a_passing_receipt(self):
        artifact = check.private_artifact_root(self.base / "bad-guard")
        faulty = {"clear": True, "reason": "NONE", "session_empty": True, "recovery_data_empty": True}
        with self.no_processes(), mock.patch.object(check.inspect, "guard_recovery_state", return_value=faulty):
            with self.assertRaisesRegex(check.BuildError, "APP_RECOVERY_GUARD_UNVERIFIED"):
                check.recovery_checks(artifact)
        root = artifact / "recovery-profile"
        self.assertEqual((root / check.KEYFILE).read_bytes(),
                         b"[org/xfce/mousepad/state/application]\nsession=['file:///fixed-a0-unsaved']\n")
        self.assertEqual((root / "Documents/input.txt").read_bytes(), b"Eidolon A0 recovery sentinel\n")


class FrozenV2SourceTests(unittest.TestCase):
    def test_all_five_v2_inputs_match_the_pre_l7a_source_bytes(self):
        fixture = Path(__file__).parent / "fixtures/linux-desktop-v2-e401918-sources.json"
        golden = json.loads(fixture.read_bytes())
        self.assertEqual(golden["schema"], 1)
        self.assertEqual(golden["source_revision"], "e401918fa657fa91c2256353b7992c4376d47f43")
        self.assertEqual(set(golden["source_sha256"]), set(check.FROZEN_V2))
        self.assertEqual(len(golden["source_sha256"]), 5)
        for name, expected in golden["source_sha256"].items():
            with self.subTest(source=name):
                self.assertEqual(sha((ROOT / name).read_bytes()), expected,
                                 "L7a must add separate sources; existing v2 journal pins require these exact bytes")


if __name__ == "__main__":
    unittest.main()
