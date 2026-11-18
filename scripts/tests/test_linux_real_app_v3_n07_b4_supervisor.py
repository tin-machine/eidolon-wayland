"""Supervisor admission failures without launching a GUI or granting authority."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_desktop_v3_contract as contract
import linux_real_app_v3_evidence as evidence
import linux_real_app_v3_n07_b4 as live
import linux_real_app_v3_n07_b4_ci as ci


class SupervisorAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.descriptor = {"run_id": "a" * 32, "installation": "b" * 32,
            "installation_id": "c" * 32, "profile_storage_id": "d" * 32,
            "profile": {"installation": "b" * 32, "profile_id": "remote-linux",
                "backend": "linux_ssh", "revision": "1", "host": "127.0.0.1",
                "port": 2200, "username": "eidolon-app", "host_key": "synthetic-test-key"}}
        self.supervisor = live.BarrierSupervisor(descriptor=self.descriptor,
            accounts={"app": {"uid": 12002, "passwd_home": "/home/eidolon-app"},
                      "driver": {"uid": 12001, "passwd_home": "/home/eidolon-driver"}},
            source={}, artifacts=Path("/unused"), output_dir=Path("/unused"), files=Path("/unused"),
            sshd=None, ssh={}, isolation={}, before={}, build_id="x", runner_hash="e"*64, policy={})

    def request(self, phase):
        second = phase.startswith("second_")
        record = self.supervisor.storage.record.record()
        context = {"schema": 1, "backend": "linux_desktop", "protocol_revision": contract.REVISION,
            "session": "2" if second else "1", "profile_revision": "1",
            "profile_binding": record["profile_binding"], "nonce": ("2" if second else "1")*16,
            "uid": 12002, "installation_id": record["installation_id"],
            "profile_storage_id": record["profile_storage_id"]}
        scope = contract.Context.parse(contract.canonical_json(context)).scope()
        capture = {"session": context["session"], "inspect_request": "31", "io_generation": "1",
            "nonce": context["nonce"], "scope": scope, "context_digest": scope,
            "capture_id": ("f" if second else "e")*64}
        return {"schema": 1, "run_id": self.descriptor["run_id"],
            "session": "second" if second else "first", "phase": phase,
            "runtime": "2" if second else "1", "capture_id": capture["capture_id"],
            "capture": capture, "context": context, "transport_watermark": 3,
            "monotonic_ns": time.monotonic_ns()}

    def test_first_barrier_storage_and_types_bound_before_any_filesystem_read(self):
        original = self.request("first_before_launch")
        for mutate in (
            lambda r: r["context"].update(profile_storage_id="0"*32),
            lambda r: r["context"].update(installation_id="0"*32),
            lambda r: r["context"].update(profile_binding="0"*64),
            lambda r: r.update(schema=True),
            lambda r: r.update(runtime=1),
            lambda r: r.update(transport_watermark=True),
            lambda r: r.update(monotonic_ns=True),
            lambda r: r.update(root_path="/foreign"),
            lambda r: r["capture"].update(context_digest="0"*64),
        ):
            with self.subTest(mutation=mutate):
                altered = copy.deepcopy(original)
                mutate(altered)
                with self.assertRaises(ValueError):
                    self.supervisor._request(altered)
                self.assertEqual(self.supervisor.identities, {})
        self.supervisor._request(original)
        self.assertEqual(self.supervisor.index, 1)

    def test_second_start_requires_recorded_complete_product_retirement(self):
        self.supervisor._request(self.request("first_before_launch"))
        self.supervisor.index = 3
        for previous in (
            {"request": {"phase": "first_after_toggle"}, "response": {"allow": True},
             "observation": {"product_cleanup_verified": True}},
            {"request": {"phase": "first_retired"}, "response": {"allow": False},
             "observation": {"product_cleanup_verified": True}},
            {"request": {"phase": "first_retired"}, "response": {"allow": True},
             "observation": {"product_cleanup_verified": False}},
        ):
            self.supervisor.record["barriers"] = [previous]
            with self.assertRaisesRegex(ValueError, "SECOND_BEFORE_CLEANUP"):
                self.supervisor._request(self.request("second_before_launch"))
            self.assertNotIn("second", self.supervisor.identities)

    def test_new_runtime_nonce_and_capture_required_even_with_cleanup_ack(self):
        self.supervisor._request(self.request("first_before_launch"))
        self.supervisor.index = 3
        self.supervisor.record["barriers"] = [{"request": {"phase": "first_retired"},
            "response": {"allow": True}, "observation": {"product_cleanup_verified": True}}]
        for field in ("runtime", "capture_id", "nonce"):
            request = self.request("second_before_launch")
            if field == "nonce":
                request["context"]["nonce"] = "1" * 16
                request["capture"]["nonce"] = "1" * 16
                scope = contract.Context.parse(contract.canonical_json(request["context"])).scope()
                request["capture"].update(scope=scope, context_digest=scope)
            else:
                request[field] = self.supervisor.identities["first"][field]
                if field == "capture_id":
                    request["capture"][field] = request[field]
            with self.assertRaisesRegex(ValueError, "IDENTITY_REUSED"):
                self.supervisor._request(request)
        self.supervisor._request(self.request("second_before_launch"))
        self.assertEqual(self.supervisor.index, 4)

    def test_failed_observation_emits_negative_ack_and_retains_failure(self):
        request = self.request("first_before_launch")
        with patch.object(self.supervisor.storage, "open_after_inspect",
                          side_effect=ValueError("N07_B4_OWNER_CHANGED")):
            raw, event = self.supervisor.respond(evidence.canonical(request))
        response = json.loads(raw)
        self.assertFalse(response["allow"])
        self.assertEqual(response["request_sha256"], evidence.sha(evidence.canonical(request)))
        self.assertEqual(self.supervisor.record["failures"], ["N07_B4_OWNER_CHANGED"])
        self.assertEqual(event["observation"]["product_cleanup_verified"], False)
        with self.assertRaisesRegex(ValueError, "FAILURE_LAUNCH_REFUSED"):
            self.supervisor._request(self.request("second_before_launch"))

    def test_setting_rejection_stops_observer_before_cleanup_census(self):
        self.supervisor._request(self.request("first_before_launch"))
        self.supervisor.record["keyfile"]["before_first_launch"] = {"synthetic": "before"}
        proof = {"synthetic": "after", "key_present": True, "value": True}
        with patch.object(self.supervisor, "_snapshot_true", return_value=proof), \
                patch.object(self.supervisor, "_keyfile_diagnostic"), \
                patch.object(self.supervisor, "_stop_observer") as stopped, \
                patch.object(live, "validate_file_sequence",
                             side_effect=ValueError("N07_UNEXPECTED_SETTING_CHANGE")):
            raw, event = self.supervisor.respond(evidence.canonical(self.request("first_after_toggle")))
        stopped.assert_called_once_with("first")
        self.assertFalse(json.loads(raw)["allow"])
        self.assertEqual(self.supervisor.record["failures"], ["N07_UNEXPECTED_SETTING_CHANGE"])
        self.assertEqual(self.supervisor.record["keyfile"]["after_first_toggle"], proof)
        self.assertEqual(event["observation"]["keyfile"], proof)
        self.assertGreaterEqual(event["observation"]["observed_at_ns"], event["received_at_ns"])

    def test_setting_error_precedes_stop_timeout_and_keeps_negative_ack(self):
        self.supervisor._request(self.request("first_before_launch"))
        self.supervisor.record["keyfile"]["before_first_launch"] = {"synthetic": "before"}
        proof = {"synthetic": "after", "key_present": True, "value": True}
        with patch.object(self.supervisor, "_snapshot_true", return_value=proof), \
                patch.object(self.supervisor, "_keyfile_diagnostic"), \
                patch.object(live, "validate_file_sequence",
                             side_effect=ValueError("N07_UNEXPECTED_SETTING_CHANGE")), \
                patch.object(self.supervisor, "_stop_observer",
                             side_effect=subprocess.TimeoutExpired("synthetic observer", 5)) as stopped:
            raw, event = self.supervisor.respond(evidence.canonical(self.request("first_after_toggle")))
        stopped.assert_called_once_with("first")
        self.assertEqual(self.supervisor.record["failures"], [
            "N07_UNEXPECTED_SETTING_CHANGE", "N07_B4_OBSERVATION_FAILED"])
        self.assertFalse(json.loads(raw)["allow"])
        self.assertEqual(event["observation"]["keyfile"], proof)
        self.assertFalse(event["observation"]["product_cleanup_verified"])
        with self.assertRaisesRegex(ValueError, "FAILURE_LAUNCH_REFUSED"):
            self.supervisor._request(self.request("second_before_launch"))

    def test_retired_cleanup_survives_fingerprint_failure_without_launch_admission(self):
        self.supervisor._request(self.request("first_before_launch"))
        self.supervisor.index = 2
        self.supervisor.source = {"tree_sha256": "1" * 64}
        native = {"owner_id": "2" * 32, "synthetic": True}
        self.supervisor.natives["first"] = native
        counts = {"app_process_count": 0, "driver_child_count": 0, "sshd_child_count": 0,
                  "journal_entries": 0, "profile_lock_held": False, "journal_lock_held": False}
        control = {"synthetic": "live retained Host"}
        order = []

        def fingerprint_failure(*_args):
            order.append("fingerprint")
            raise ValueError("N07_FINGERPRINT_FILE_CHANGED")

        observer = SimpleNamespace(finish=Mock(side_effect=fingerprint_failure))
        self.supervisor.observers["first"] = observer
        with tempfile.TemporaryDirectory() as tmp:
            self.supervisor.artifacts = Path(tmp)
            with patch.object(self.supervisor, "_stop_observer",
                              side_effect=lambda *_: order.append("observer_stopped")), \
                    patch.object(self.supervisor, "export_session"), \
                    patch.object(self.supervisor, "_census",
                                 side_effect=lambda: (order.append("census") or (counts, control))), \
                    patch.object(live.fixture, "export_bundle", return_value=({}, {})), \
                    patch("linux_real_app_v3_n07_b4_probe.validate_retirement",
                          side_effect=lambda *_: order.append("product_retirement")) as retired, \
                    patch("linux_real_app_v3_n07_b4_probe.validate_session") as full_session:
                raw, event = self.supervisor.respond(evidence.canonical(self.request("first_retired")))
            environment = json.loads((Path(tmp) / "first-environment.json").read_bytes())
            self.assertEqual(environment["revision"], "eidolon-i6-n07-b4-environment-v1")
            self.assertEqual(environment["final"], counts)
            self.assertEqual(environment["driver_control"], control)
            self.assertFalse((Path(tmp) / "first-fingerprint.json").exists())
        self.assertEqual(order, ["observer_stopped", "census", "product_retirement", "fingerprint"])
        retired.assert_called_once_with(native, self.supervisor.environments["first"],
                                        self.supervisor.source, "first")
        full_session.assert_not_called()
        self.assertTrue(event["observation"]["product_cleanup_verified"])
        self.assertEqual(self.supervisor.record["failures"], ["N07_FINGERPRINT_FILE_CHANGED"])
        self.assertFalse(json.loads(raw)["allow"])
        self.assertNotIn("first", self.supervisor.fingerprints)
        with self.assertRaisesRegex(ValueError, "FAILURE_LAUNCH_REFUSED"):
            self.supervisor._request(self.request("second_before_launch"))

    def test_full_census_and_live_control_are_not_interchangeable(self):
        counts = {"app_process_count": 0, "driver_child_count": 0, "sshd_child_count": 0,
                  "journal_entries": 0, "profile_lock_held": False, "journal_lock_held": False}
        live.clean_census(counts)
        for key in counts:
            bad = dict(counts)
            bad[key] = True if isinstance(counts[key], bool) else 1
            with self.assertRaises(ValueError):
                live.clean_census(bad)


class PartialExportTests(unittest.TestCase):
    def test_original_prelaunch_failure_survives_final_collection_check(self):
        supervisor = SimpleNamespace(record={"failures": ["N07_B4_UNSAFE_ANCESTOR"],
                                              "external_rescue": False})
        with self.assertRaisesRegex(ValueError, "^N07_B4_UNSAFE_ANCESTOR$"):
            live.require_complete_run(2, {"error": "I6_N07_B4_SUPERVISOR_REFUSED"}, supervisor)
        supervisor.record["failures"] = []
        with self.assertRaisesRegex(ValueError, "^I6_N07_B4_SUPERVISOR_REFUSED$"):
            live.require_complete_run(2, {"error": "I6_N07_B4_SUPERVISOR_REFUSED"}, supervisor)
        supervisor.record["external_rescue"] = True
        with self.assertRaisesRegex(ValueError, "^N07_B4_RUN_INCOMPLETE$"):
            live.require_complete_run(0, {"error": None}, supervisor)

    def test_checkpoint_failure_still_closes_observer_and_owned_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("client-key", "host-key"):
                (root/name).write_bytes(b"synthetic fixture key")
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            supervisor = SimpleNamespace(save=Mock(side_effect=OSError("synthetic fsync failure")),
                                         close=Mock(side_effect=lambda: os.close(fd)))
            sshd = object()
            with patch.object(live.fixture, "stop_owned") as stopped:
                with self.assertRaisesRegex(OSError, "synthetic fsync failure"):
                    live.close_fixture(supervisor, sshd, 123, root)
                stopped.assert_called_once_with(sshd, 123)
            supervisor.close.assert_called_once_with()
            with self.assertRaises(OSError):
                os.fstat(fd)
            self.assertEqual(list(root.iterdir()), [])

    def test_fixture_stop_failure_still_removes_only_owned_fixture_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/"client-key").write_bytes(b"synthetic fixture key")
            (root/"host-key").write_bytes(b"synthetic fixture key")
            (root/"unrelated").write_bytes(b"retain")
            with patch.object(live.fixture, "stop_owned", side_effect=OSError("synthetic stop failure")):
                with self.assertRaisesRegex(OSError, "synthetic stop failure"):
                    live.close_fixture(None, object(), 123, root)
            self.assertEqual([p.name for p in root.iterdir()], ["unrelated"])

    def test_failed_second_session_preserves_available_first_and_rescue(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            incoming, out = base/"incoming", base/"out"
            incoming.mkdir(mode=0o700)
            out.mkdir(mode=0o700)
            (incoming/"first-native.json").write_text('{"partial":true}')
            (incoming/"n07-b4-validation.json").write_text('{"status":"fail"}')
            (incoming/"second-context.json").write_text('malformed')
            (incoming/"private-key").write_text('not-exportable')
            image = base/"image"
            image.write_text("sha256:"+"a"*64)
            with patch.object(ci.evidence, "source_snapshot",
                              return_value={"dirty":False,"commit":"b"*40}):
                self.assertFalse(ci.export_and_validate(incoming,out,image,"b"*40,
                    "124","137","true",base,"c"*40,ci={}))
            self.assertEqual((out/"first-native.json").read_bytes(), b'{"partial":true}')
            self.assertFalse((out/"private-key").exists())
            self.assertFalse((out/"second-context.json").exists())
            container = json.loads((out/"container.json").read_text())
            self.assertTrue(container["external_rescue"])
            report = json.loads((out/"n07-b4-export.json").read_text())
            self.assertEqual(report["status"],"rejected")
            self.assertFalse(report["candidate_created"])
            self.assertEqual(report["export_errors"],["second-context.json"])
            self.assertEqual(report["verified_n07_assertions"],[])


if __name__ == "__main__":
    unittest.main()
