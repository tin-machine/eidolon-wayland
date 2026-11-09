"""A0 orchestration gates; no installed application or user HOME is touched.

Runtime dependencies are replaced only at the explicit stage boundaries. These
tests prove refusal and preservation contracts, not real Mousepad feasibility.
The frozen-v2 fixture was captured from remote e401918, not computed by a build
or regenerated from the checkout under test.
"""
import contextlib
import copy
import errno
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
        return {"schema": 1, "complete": True, "pending": False, "reason": "COMPLETED",
                "term_signals": 0, "kill_signals": 0, "reaped": 4, "open_pidfds": 0,
                "root_pidfds_closed": True, "output_pipes_eof": True}

    def application(self):
        value = {"schema": 1, "stage": "wayland", "state": "partial", "status": "incomplete", "error": None,
                "feasibility_verified": False, "physical_input_verified": False, "production_ready": False,
                "checks": {name: name not in check.FOUNDATION_PENDING for name in wayland.REQUIRED_CHECKS},
                "pending": list(check.FOUNDATION_PENDING), "cleanup": self.cleanup(), "output_bytes": {},
                "payload_logged": False, "titles_logged": False, "pixels_logged": False,
                "waypipe_ends": 2, "private_bus": True, "profile_kind": "synthetic",
                "waypipe_version": "0.11.3", "waypipe_sha256": sha(b"Waypipe stage fixture.\n"),
                "mousepad_sha256": "a" * 64, "source_argv": "mousepad --disable-server"}
        value["lifecycle"] = self.lifecycle(value)
        value["cleanup"].update(workers_complete=True, worker_count=2,
                                worker_returncodes={"precursor": 0, "target": 0})
        return value

    def lifecycle(self, source):
        """A fixed nonexecuting receipt specimen for adversarial validator tests.

        The actual application oracle runs only in the mandatory live CI job.
        Identical WindowIds on fresh compositor runs are intentional here.
        """
        identity = lambda number: format(number, "032x")
        empty = {"documents_sha256": sha(b"synthetic saved documents"), "settings_sha256": None,
                 "settings_present": False, "preferences_sha256": None, "preferences_count": 0,
                 "tree_sha256": sha(b"initial synthetic profile")}
        saved = {**empty, "settings_present": True, "settings_sha256": sha(b"flushed settings"),
                 "preferences_sha256": sha(b"ordinary window geometry"), "preferences_count": 4,
                 "tree_sha256": sha(b"closed synthetic profile")}

        def run(number, ending="close", profile_id=100):
            return {"state": "partial", "status": "incomplete", "error": None,
                    "run_id": identity(number), "owner_scope_id": identity(number + 1000),
                    "runtime_id": identity(number + 2000), "profile_id": identity(profile_id),
                    "termination_kind": ending,
                    "termination": {"kind": ending, "observed": True,
                                    "app_pidfd_exit": True if ending == "crash" else None,
                                    "server_returncode": 137 if ending == "crash" else None if ending == "stop" else 0,
                                    "forced_scope_cleanup": ending == "stop"},
                    "initial_main": {"window": "1", "frame": "1", "app_id": "mousepad", "client_count": 1,
                                     "prelaunch_client_count": 0, "observed_mapped_count": 1,
                                     "input_delivered": 0, "pressed": 0},
                    "profile_before": copy.deepcopy(saved), "profile_after": copy.deepcopy(saved),
                    "documents_before_termination": saved["documents_sha256"],
                    "app_spawned": True, "cleanup": self.cleanup(),
                    "final_graph": None if ending == "stop" else {"window_count": 0, "client_count": 0,
                                                                    "pressed": 0, "focus_window": None},
                    "recovery_guard": {"clear": True, "reason": "NONE", "session_empty": True,
                                       "recovery_data_empty": True, "profile_lock_held": True},
                    "mousepad_sha256": source["mousepad_sha256"], "waypipe_sha256": source["waypipe_sha256"],
                    "waypipe_version": "0.11.3"}

        cases = {}
        for number, ending in enumerate(("close", "stop", "crash"), 1):
            first = run(number * 10, ending, profile_id=100 + number)
            first["profile_before"] = copy.deepcopy(empty)
            cases[ending + "_restart"] = {
                "first": first, "restart": run(number * 10 + 1, profile_id=100 + number), "outcome": "restarted",
                "documents_preserved": True, "settings_preserved": True if ending == "close" else None,
            }
        recovery = {}
        for number, kind in enumerate(("session", "autosave", "ambiguous"), 1):
            refused = run(number + 40, profile_id=200 + number)
            refused.update(state="fail", status="failed", error="APP_STATE_RECOVERY_REQUIRED",
                           app_spawned=False, initial_main=None, final_graph=None, documents_before_termination=None,
                           termination={"kind": "close", "observed": False, "app_pidfd_exit": None,
                                        "server_returncode": None, "forced_scope_cleanup": False})
            refused["recovery_guard"].update(clear=False, reason="APP_STATE_RECOVERY_REQUIRED",
                    session_empty=kind == "autosave", recovery_data_empty=kind != "autosave")
            recovery[kind] = {"run": refused, "profile_preserved": True}
        final = run(50, profile_id=300)
        observations = []
        for index in range(3):
            observations.append({**{key: final[key] for key in ("run_id", "owner_scope_id", "profile_id", "runtime_id")},
                "main": "1", "app_id": "mousepad", "client_count": 1, "topology_sha256": sha(b"single main"),
                "profile": copy.deepcopy(saved),
                "response": {"responsive": True, "main": "1", "frame": str(index + 2),
                             "input_delivered_before": index * 10, "input_delivered_after": index * 10 + 6,
                             "window_count": 1, "client_count": 1, "pressed": 0,
                             "topology_preserved": True, "dialog_window": str(index + 2)}})
        return {"schema": 1, "cases": cases, "recovery": recovery,
                "independence": {"precursor_worker_id": identity(500), "target_worker_id": identity(501),
                                 "precursor_initial": observations[0], "after_close": observations[1],
                                 "after_stop": observations[2], "precursor_final": final, "hold_probe": None}}

    def held_application(self):
        value = self.application()
        independent = value["lifecycle"]["independence"]
        held = copy.deepcopy(independent["after_close"])
        independent["hold_probe"] = {"elapsed_ms": 125_000, "observation": held}
        for phase in ("after_close", "after_stop"):
            response = independent[phase]["response"]
            response["input_delivered_before"] += 10
            response["input_delivered_after"] += 10
            response["dialog_window"] = str(int(response["dialog_window"]) + 1)
            response["frame"] = str(int(response["frame"]) + 1)
        return value

    def test_long_hold_requires_measured_time_and_an_additional_response(self):
        value = self.held_application()
        self.assertIs(check.validate_application_foundation(value, require_precursor_hold=True), value)
        for elapsed in (None, True, "125000", -1, 120_000, 124_999, 600_000):
            forged = copy.deepcopy(value)
            forged["lifecycle"]["independence"]["hold_probe"]["elapsed_ms"] = elapsed
            with self.subTest(elapsed=elapsed), self.assertRaises(check.BuildError):
                check.validate_application_foundation(forged, require_precursor_hold=True)

    def test_long_hold_missing_or_replayed_observation_is_rejected(self):
        for invalid in (None, {}, {"elapsed_ms": 125_000}):
            value = self.held_application()
            value["lifecycle"]["independence"]["hold_probe"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(check.BuildError):
                check.validate_application_foundation(value, require_precursor_hold=True)
        for phase in ("precursor_initial", "after_close", "after_stop"):
            value = self.held_application()
            independent = value["lifecycle"]["independence"]
            independent["hold_probe"]["observation"] = copy.deepcopy(independent[phase])
            with self.subTest(replayed=phase), self.assertRaises(check.BuildError):
                check.validate_application_foundation(value, require_precursor_hold=True)

    def test_optional_hold_still_validates_any_supplied_measurement(self):
        value = self.held_application()
        value["lifecycle"]["independence"]["hold_probe"]["elapsed_ms"] = 1
        with self.assertRaises(check.BuildError):
            check.validate_application_foundation(value)

    def test_required_hold_cannot_be_dropped_by_the_feasibility_aggregator(self):
        with self.stages(self.application()):
            result = check.run_feasibility(self.base / "missing-hold", None, precursor_hold_check=True)
        self.assertEqual((result["status"], result["reason"]), ("failed", "APP_WAYLAND_RECEIPT_INVALID"))
        self.assertFalse(result["a0_passed"])

    def assert_invalid_evidence(self, mutate):
        value = self.application()
        mutate(value)
        with self.assertRaises(check.BuildError):
            check.validate_application_foundation(value)

    def test_fresh_compositors_may_reuse_the_same_window_id(self):
        value = self.application()
        for case in value["lifecycle"]["cases"].values():
            self.assertEqual(case["first"]["initial_main"]["window"], case["restart"]["initial_main"]["window"])
        self.assertIs(check.validate_application_foundation(value), value)

    def test_summary_booleans_cannot_replace_missing_lifecycle_evidence(self):
        for replacement in (None, {}, {"schema": 1, "cases": {}}):
            with self.subTest(replacement=replacement):
                self.assert_invalid_evidence(lambda value: value.update(lifecycle=replacement))

    def test_missing_or_unknown_case_and_boolean_schema_are_rejected(self):
        for mutate in (
            lambda value: value["lifecycle"].update(schema=True),
            lambda value: value["lifecycle"]["cases"].pop("crash_restart"),
            lambda value: value["lifecycle"]["cases"].update(extra={}),
            lambda value: value["lifecycle"]["recovery"].pop("ambiguous"),
            lambda value: value["lifecycle"].update(quest_verified=True),
        ):
            with self.subTest(mutation=mutate):
                self.assert_invalid_evidence(mutate)

    def test_restart_requires_new_run_owner_and_runtime_on_the_same_profile(self):
        for field in ("run_id", "owner_scope_id", "runtime_id"):
            def duplicate(value):
                case = value["lifecycle"]["cases"]["close_restart"]
                case["restart"][field] = case["first"][field]
            with self.subTest(duplicate=field):
                self.assert_invalid_evidence(duplicate)
        self.assert_invalid_evidence(lambda value: value["lifecycle"]["cases"]["close_restart"]["restart"].update(profile_id="f" * 32))

    def test_second_main_or_preexisting_client_cannot_certify_fresh_start(self):
        for field, replacement in (("prelaunch_client_count", 1), ("client_count", 2),
                                   ("observed_mapped_count", 2), ("app_id", "org.xfce.mousepad"),
                                   ("frame", "0"), ("pressed", True)):
            with self.subTest(field=field):
                self.assert_invalid_evidence(lambda value: value["lifecycle"]["cases"]["close_restart"]["restart"]["initial_main"].update({field: replacement}))

    def test_each_run_must_match_the_inspected_binary_hashes(self):
        for field in ("mousepad_sha256", "waypipe_sha256", "waypipe_version"):
            with self.subTest(field=field):
                self.assert_invalid_evidence(lambda value: value["lifecycle"]["cases"]["crash_restart"]["restart"].update({field: "0" * 64}))

    def test_profile_reset_and_document_loss_cannot_hide_behind_preserved_true(self):
        for stage, field in (("profile_before", "tree_sha256"), ("profile_after", "documents_sha256")):
            with self.subTest(stage=stage, field=field):
                self.assert_invalid_evidence(lambda value: value["lifecycle"]["cases"]["close_restart"]["restart"][stage].update({field: "0" * 64}))

    def test_matching_empty_post_shutdown_snapshots_cannot_hide_lost_saved_documents(self):
        for ending in ("close", "stop", "crash"):
            for changed in (sha(b"[]"), sha(b"changed saved documents")):
                def lose_documents(value):
                    case = value["lifecycle"]["cases"][ending + "_restart"]
                    case["first"]["profile_after"]["documents_sha256"] = changed
                    case["restart"]["profile_before"]["documents_sha256"] = changed
                    case["restart"]["profile_after"]["documents_sha256"] = changed
                    case["restart"]["documents_before_termination"] = changed
                with self.subTest(ending=ending, changed=changed):
                    self.assert_invalid_evidence(lose_documents)

    def test_document_witness_is_required_only_for_executed_endings(self):
        for replacement in (None, False, "invalid"):
            with self.subTest(replacement=replacement):
                self.assert_invalid_evidence(lambda value: value["lifecycle"]["cases"]["stop_restart"]["first"].update(
                    documents_before_termination=replacement))
        self.assert_invalid_evidence(lambda value: value["lifecycle"]["cases"]["close_restart"]["first"].pop(
            "documents_before_termination"))
        self.assert_invalid_evidence(lambda value: value["lifecycle"]["recovery"]["session"]["run"].update(
            documents_before_termination=sha(b"invented pre-termination snapshot")))

    def test_stop_or_crash_protected_refusal_remains_distinct_from_restart_success(self):
        for ending in ("stop", "crash"):
            value = self.application()
            case = value["lifecycle"]["cases"][ending + "_restart"]
            restart = case["restart"]
            restart.update(state="fail", status="failed", error="APP_STATE_RECOVERY_REQUIRED",
                           app_spawned=False, initial_main=None, final_graph=None, documents_before_termination=None,
                           termination={"kind": "close", "observed": False, "app_pidfd_exit": None,
                                        "server_returncode": None, "forced_scope_cleanup": False})
            restart["recovery_guard"].update(clear=False, reason="APP_STATE_RECOVERY_REQUIRED", session_empty=False)
            case["outcome"] = "recovery_required"
            with self.subTest(ending=ending):
                self.assertIs(check.validate_application_foundation(value), value)
                case["outcome"] = "restarted"
                with self.assertRaises(check.BuildError):
                    check.validate_application_foundation(value)

    def test_close_setting_preservation_requires_nonempty_flushed_settings(self):
        for stage in ("first", "restart"):
            def erase(value):
                value["lifecycle"]["cases"]["close_restart"][stage]["profile_after"].update(
                    preferences_count=0, preferences_sha256=None)
            with self.subTest(stage=stage):
                self.assert_invalid_evidence(erase)
        self.assert_invalid_evidence(lambda value: value["lifecycle"]["cases"]["close_restart"]["restart"]["profile_after"].update(preferences_sha256="0" * 64))

    def test_stop_and_crash_need_their_own_termination_evidence(self):
        for case, field, replacement in (("stop_restart", "forced_scope_cleanup", False),
                                          ("crash_restart", "app_pidfd_exit", False),
                                          ("crash_restart", "server_returncode", 0),
                                          ("crash_restart", "server_returncode", True)):
            with self.subTest(case=case, field=field):
                self.assert_invalid_evidence(lambda value: value["lifecycle"]["cases"][case]["first"]["termination"].update({field: replacement}))
        self.assert_invalid_evidence(lambda value: value["lifecycle"]["cases"]["stop_restart"].update(settings_preserved=True))
        self.assert_invalid_evidence(lambda value: value["lifecycle"]["cases"]["close_restart"]["first"]["final_graph"].update(window_count=1))
        self.assert_invalid_evidence(lambda value: value["lifecycle"]["cases"]["crash_restart"]["first"]["final_graph"].update(focus_window="1"))

    def test_always_refusing_does_not_pass_the_successful_restart_gate(self):
        def refuse(value):
            case = value["lifecycle"]["cases"]["close_restart"]
            case.update(outcome="recovery_required", restart=value["lifecycle"]["recovery"]["session"]["run"])
        self.assert_invalid_evidence(refuse)

    def test_recovery_refusal_requires_no_spawn_and_unchanged_bytes(self):
        for field, replacement in (("app_spawned", True), ("error", "APP_WAYLAND_DEPENDENCY_OR_IO")):
            with self.subTest(field=field):
                self.assert_invalid_evidence(lambda value: value["lifecycle"]["recovery"]["session"]["run"].update({field: replacement}))
        self.assert_invalid_evidence(lambda value: value["lifecycle"]["recovery"]["autosave"]["run"]["profile_after"].update(tree_sha256="0" * 64))
        self.assert_invalid_evidence(lambda value: value["lifecycle"]["recovery"]["ambiguous"]["run"]["recovery_guard"].update(clear=True))

    def test_precursor_requires_independent_scope_and_new_application_responses(self):
        for field in ("run_id", "owner_scope_id", "profile_id", "runtime_id"):
            with self.subTest(field=field):
                self.assert_invalid_evidence(lambda value: value["lifecycle"]["independence"]["after_stop"].update({field: "f" * 32}))
        self.assert_invalid_evidence(lambda value: value["lifecycle"]["independence"].update(target_worker_id=value["lifecycle"]["independence"]["precursor_worker_id"]))
        for phase in ("after_close", "after_stop"):
            with self.subTest(stale=phase):
                self.assert_invalid_evidence(lambda value: value["lifecycle"]["independence"].update({phase: copy.deepcopy(value["lifecycle"]["independence"]["precursor_initial"])}))
        self.assert_invalid_evidence(lambda value: value["lifecycle"]["independence"]["after_stop"]["response"].update(responsive=False))

    def test_target_stop_cannot_change_precursor_configuration_or_window(self):
        for group, field, replacement in ((None, "topology_sha256", "0" * 64),
                                           ("profile", "settings_sha256", "0" * 64),
                                           ("profile", "documents_sha256", "0" * 64),
                                           ("response", "main", "9")):
            def mutate(value):
                observation = value["lifecycle"]["independence"]["after_stop"]
                (observation[group] if group else observation)[field] = replacement
            with self.subTest(group=group, field=field):
                self.assert_invalid_evidence(mutate)

    def test_empty_precursor_settings_cannot_form_an_isolation_baseline(self):
        def erase_observations(value):
            for phase in ("precursor_initial", "after_close", "after_stop"):
                value["lifecycle"]["independence"][phase]["profile"].update(
                    preferences_count=0, preferences_sha256=None)
        self.assert_invalid_evidence(erase_observations)

    def test_complete_coordinator_cannot_mask_any_unfinished_worker_or_run(self):
        for field, replacement in (("worker_count", 1), ("worker_count", True), ("workers_complete", False),
                                   ("worker_returncodes", {"precursor": 0, "target": 1}),
                                   ("worker_returncodes", {"precursor": 0, "target": False})):
            with self.subTest(field=field):
                self.assert_invalid_evidence(lambda value: value["cleanup"].update({field: replacement}))
        self.assert_invalid_evidence(lambda value: value["lifecycle"]["cases"]["stop_restart"]["first"]["cleanup"].update(complete=False, pending=True))
        self.assert_invalid_evidence(lambda value: value["lifecycle"]["independence"]["precursor_final"]["cleanup"].update(output_pipes_eof=False))

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
        self.assertEqual(tuple(check.FOUNDATION_PENDING),
                         ("gui_setting_persistence", "protected_settings_gui_readonly"))
        artifact = self.base / "partial"
        with self.stages(self.application()):
            status, result = self.command("--stage", "feasibility", "--artifact-root", str(artifact))
        self.assertEqual((status, result["status"], result["reason"]), (2, "incomplete", "APP_A0_INCOMPLETE"))
        self.assertEqual(result["pending"], list(check.FOUNDATION_PENDING) + ["v3_acceptance", "quest"])
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
        application["lifecycle"] = self.lifecycle(application)
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

    def assert_cleanup_marker_failure_keeps_owner(self, error_number):
        application = self.application()
        application.update(state="fail", status="failed", error="APP_WAYLAND_CLEANUP_PENDING")
        application["cleanup"] = dict(self.cleanup(), complete=False, pending=True, open_pidfds=1)
        run = mock.Mock()
        first, subsequent = wayland.CleanupPending(run), wayland.CleanupPending(run)
        first.receipt = application
        run.finish.side_effect = [OSError(errno.EIO, "private cleanup descriptor"), subsequent, self.cleanup()]
        artifact = self.base / "marker-io-failure"
        marker_attempts, retried = [], []
        real_write, real_retry = check.write_json, wayland.retry_cleanup

        def write_with_marker_failure(path, value):
            if path.name == "cleanup-pending.json":
                marker_attempts.append(value)
                raise OSError(error_number, "private marker write detail")
            return real_write(path, value)

        def retry_same_owner(pending):
            self.assertIs(pending.run, run)
            retried.append(pending)
            return real_retry(pending)

        with self.stages(application, raised=first), \
             mock.patch.object(check, "write_json", side_effect=write_with_marker_failure), \
             mock.patch.object(wayland, "retry_cleanup", side_effect=retry_same_owner), \
             mock.patch.object(check.time, "sleep", return_value=None):
            status, result = self.command("--stage", "feasibility", "--artifact-root", str(artifact))
        self.assertEqual(retried, [first, first, subsequent])
        self.assertEqual(run.finish.call_count, 3)
        self.assertEqual(marker_attempts, [{"schema": 1, "stage": "cleanup", "complete": False,
                                           "reason": "APP_WAYLAND_CLEANUP_PENDING", "available": False}])
        self.assertFalse((artifact / "cleanup-pending.json").exists())
        self.assertEqual((status, result["status"], result["reason"]), (2, "failed", "APP_WAYLAND_CLEANUP_PENDING"))
        self.assertFalse(result["a0_passed"])
        self.assertFalse(result["available"])
        self.assertEqual(result["checks"]["application"]["error"], "APP_WAYLAND_CLEANUP_PENDING")
        self.assertTrue(result["checks"]["application"]["cleanup"]["complete"])
        self.assertEqual(json.loads((artifact / "feasibility.json").read_bytes()), result)
        self.assertNotIn("private cleanup descriptor", json.dumps(result))
        self.assertNotIn("private marker write detail", json.dumps(result))

    def test_cleanup_marker_enospc_does_not_interrupt_retry(self):
        self.assert_cleanup_marker_failure_keeps_owner(errno.ENOSPC)

    def test_cleanup_marker_edquot_does_not_interrupt_retry(self):
        self.assert_cleanup_marker_failure_keeps_owner(errno.EDQUOT)

    def test_cleanup_marker_eio_does_not_interrupt_retry(self):
        self.assert_cleanup_marker_failure_keeps_owner(errno.EIO)

    def test_termination_guard_survives_handoff_marker_failure_and_repeated_retries(self):
        application = self.application()
        application.update(state="fail", status="failed", error="APP_WAYLAND_CLEANUP_PENDING")
        application["cleanup"] = dict(self.cleanup(), complete=False, pending=True, open_pidfds=1)
        run = mock.Mock()
        first, subsequent = wayland.CleanupPending(run), wayland.CleanupPending(run)
        first.receipt = application
        artifact = self.base / "interrupted-pending-cleanup"
        signals = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
        original = {number: signal.getsignal(number) for number in signals}
        chained, phases, retried = [], [], []
        guard = None

        def previous_handler(number, _frame):
            chained.append(number)

        try:
            # A recording prior handler also makes an accidental early restore
            # observable without terminating the test runner itself.
            for number in signals:
                signal.signal(number, previous_handler)
            guard = wayland._TerminationGuard()
            run.termination = guard

            def request_stop(phase):
                phases.append(phase)
                for number in signals * 2:
                    self.assertEqual(signal.getsignal(number), guard._request)
                    signal.raise_signal(number)
                self.assertEqual(guard.requested, signal.SIGTERM)

            def transfer(*_args, **_kwargs):
                request_stop("handoff")
                raise first

            real_write, real_retry = check.write_json, wayland.retry_cleanup

            def write_with_marker_failure(path, value):
                if path.name == "cleanup-pending.json":
                    request_stop("marker_write")
                    raise OSError(errno.ENOSPC, "private marker detail")
                return real_write(path, value)

            def retry_same_owner(pending):
                self.assertIs(pending.run, run)
                retried.append(pending)
                return real_retry(pending)

            def finish():
                attempt = run.finish.call_count
                request_stop("retry_" + str(attempt))
                if attempt == 1:
                    raise OSError(errno.EIO, "private cleanup detail")
                if attempt == 2:
                    raise subsequent
                self.assertEqual(attempt, 3)
                # Production _Run.finish restores this guard only after the
                # descendant closure is proved gone. No outer handler stack
                # may subsequently reinstall the now-completed run's guard.
                guard.restore()
                return self.cleanup()

            run.finish.side_effect = finish
            with self.stages(application, raised=transfer), \
                 mock.patch.object(check, "write_json", side_effect=write_with_marker_failure), \
                 mock.patch.object(wayland, "retry_cleanup", side_effect=retry_same_owner), \
                 mock.patch.object(check.time, "sleep", return_value=None):
                status, result = self.command("--stage", "feasibility", "--artifact-root", str(artifact))
            self.assertEqual(phases, ["handoff", "marker_write", "retry_1", "retry_2", "retry_3"])
            self.assertEqual(retried, [first, first, subsequent])
            self.assertEqual(run.finish.call_count, 3)
            self.assertEqual(chained, [])
            self.assertEqual({number: signal.getsignal(number) for number in signals},
                             dict.fromkeys(signals, previous_handler))
            self.assertEqual((status, result["status"], result["reason"]),
                             (2, "failed", "APP_WAYLAND_CLEANUP_PENDING"))
            self.assertTrue(result["checks"]["application"]["cleanup"]["complete"])
            self.assertFalse(result["a0_passed"])
            self.assertFalse(result["available"])
            self.assertNotIn("private marker detail", json.dumps(result))
            self.assertNotIn("private cleanup detail", json.dumps(result))
        finally:
            if guard is not None:
                guard.restore()
            for number, handler in original.items():
                signal.signal(number, handler)


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
