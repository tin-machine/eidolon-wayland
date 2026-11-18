"""Supervisor admission failures without launching a GUI or granting authority."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

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
