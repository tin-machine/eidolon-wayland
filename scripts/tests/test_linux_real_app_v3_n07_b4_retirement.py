"""Failure cleanup remains independent of exercise success and external rescue.

The source/admission binding alone is stubbed in these unit tests. Actual
per-target SDK, C2 control, reclamation, finalizer, Registry and census checks
are exercised unchanged. Real GUI CI supplies the unmocked binding as well.
"""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_real_app_v3_n07_b4_probe as probe
from test_linux_real_app_v3_n07_b4_probe import open_fixture


def retired_fixture():
    native = open_fixture()
    native.update(error="I6_N07_B4_SUPERVISOR_REFUSED", cleanup_error=None, sdk_retired=True,
                  owner_id="e" * 32, destroy_status=0, registry_counts={"runtimes": 0, "workers": 0})
    reclamation = {**{key: 0 for key in probe.n01.RECLAMATION_COUNTS},
                   **{key: False for key in probe.n01.RECLAMATION_FLAGS},
                   "runtime": "2", "origin_retired": True, "shutdown_complete": True}
    shutdown = {"requested": True, "complete": True, "timed_out": False, "pending_owners": [],
                "sdk_bindings": 0, "deadline_ms": 10000, "elapsed_ms": 5}
    runtime = {"reclamation": reclamation, "shutdown": shutdown, "state": "Stopped", "panels": [],
        "desktop_v3_finalizer_pending": False, "desktop_v3_cleanup_error": None, "error": None,
        **{k: 0 for k in ("app_sdk_bindings", "commands_pending", "desktop_v3_pending_targets",
                          "helper_workers", "dependency_workers", "compositor_workers")},
        "app": {"error": None, "state": "stopped", "ready": False, "directory_cleaned": True, "windows": [],
                **{k: 0 for k in ("clients", "objects", "surfaces", "pixel_bytes", "pool_bytes", "pool_count",
                                  "memfd_fds_after", "memfd_maps_after")},
                "local": {"workers": 0, "owned_groups": 0, "waited": True, "error": None}},
        "render": {"workers": 0, "contexts": 0, "latest_images": 0, "queued": 0, "targets": [], "error": None,
                   "composition": {"textures": 0, "texture_bytes": 0, "decoration_texture_bytes": 0,
                                   "gl_error": 0, "error": None}},
        "transport": {"workers": 0, "tokio_threads": 0, "owned_connections": 0, "state": "Stopped",
                      "desktop_pending": False, "pending_cleanup": [], "error": None, "forward": {"active": 0}}}
    native["last_snapshot"] = {"schema": 1, "code": 0, "handle": "2", "runtime": runtime,
                               "registry": {"runtimes": 1, "workers": 0}}
    rows = []

    def event(kind, data):
        rows.append({"sequence": str(len(rows) + 1), "kind": kind, "data": data})

    def protocol(number, request, kind, payload):
        event("protocol_event", {"record": {"sequence": str(number), "request_sequence": str(request),
                                            "event": {"type": kind, "payload": payload}}})

    protocol(1, 0, "prepared", {})
    for number, (command, response) in enumerate((("inspect", "catalog"), ("start", "started"), ("stop", "stopped")), 2):
        event("control_send_attempt", {"action": command})
        protocol(number, number - 1, response, {} if command != "stop" else {
            "reason": "stop", "process_complete": True, "pipes_eof": True, "remote_cleaned": True, "receipt_written": True})
    event("remote_cleanup_observed", {})
    event("io_retired", {})
    event("journal_unlinked", {"directory_fsynced": True})
    event("finalizer_published", {"status": 0})
    native["trace"] = {"overflow": False, "events": rows}
    native["observations"] = [{"sequence": str(i), "kind": kind, "transport_sequence": str(len(rows)), "data": data}
        for i, (kind, data) in enumerate((
            ("origin_retired", {"reclamation": {"origin_retired": True}}),
            ("runtime_stop_requested", {"status": 0}),
            ("last_live_census", {"snapshot": native["last_snapshot"]}),
            ("registry_destroy_return", {"status": 0, "counts": native["registry_counts"]})), 1)]
    environment = {"final": {"app_process_count": 0, "driver_child_count": 0, "sshd_child_count": 0,
                             "journal_entries": 0, "profile_lock_held": False, "journal_lock_held": False}}
    return native, environment


class RetirementTests(unittest.TestCase):
    def check(self, native, environment):
        bound = (environment, probe.n02.ordinal_events(native["trace"]["events"], limit=128),
                 probe.n02.ordinal_events(native["observations"], limit=128),
                 {"session": "2", "capture_id": "a" * 64}, {"synthetic": True})
        with patch.object(probe, "_retirement_binding", return_value=bound):
            return probe.validate_retirement(native, environment, {}, "second")

    def test_refused_observation_keeps_error_and_verifies_only_product_retirement(self):
        native, env = retired_fixture()
        before = copy.deepcopy(native)
        result = self.check(native, env)
        self.assertTrue(result["product_cleanup_verified"])
        self.assertFalse(result["exercise_success_verified"])
        self.assertEqual(result["native_error"], "I6_N07_B4_SUPERVISOR_REFUSED")
        self.assertEqual(native, before)
        self.assertFalse(result["host_v3_acceptance_passed"])
        self.assertEqual(result["verified_n07_assertions"], [])

    def test_actual_cleanup_residue_missing_sdk_terminal_and_finalizer_rejected(self):
        mutations = (
            lambda n, e: e["final"].update(app_process_count=1),
            lambda n, e: e["final"].update(profile_lock_held=True),
            lambda n, e: n["last_snapshot"]["runtime"]["app"]["local"].update(workers=1),
            lambda n, e: n["last_snapshot"]["runtime"]["render"].update(contexts=1),
            lambda n, e: n["last_snapshot"]["runtime"]["transport"].update(pending_cleanup=["unretired"]),
            lambda n, e: n["last_snapshot"]["runtime"]["shutdown"].update(timed_out=True),
            lambda n, e: n.update(destroy_status=1),
            lambda n, e: n.update(sdk_retired=False),
            lambda n, e: n["sdk_observations"][-1]["data"].update(create_seq="9999"),
            lambda n, e: next(row["data"]["record"]["event"]["payload"] for row in n["trace"]["events"]
                              if row["kind"] == "protocol_event" and row["data"]["record"]["event"]["type"] == "stopped").update(remote_cleaned=False),
            lambda n, e: next(row["data"] for row in n["trace"]["events"] if row["kind"] == "journal_unlinked").update(directory_fsynced=False),
        )
        for index, mutate in enumerate(mutations):
            native, env = retired_fixture()
            mutate(native, env)
            with self.subTest(index=index), self.assertRaises((ValueError, KeyError, TypeError)):
                self.check(native, env)

    def test_optional_keyfile_inventory_is_bound_to_the_same_snapshot(self):
        proof = {"storage_digest": "a" * 64, "root_dev": 1, "root_ino": 2,
                 "file": {"sha256": "b" * 64, "size": 60}, "key_present": True,
                 "value": True, "others_sha256": "c" * 64}
        diagnostic = {"schema": 1, "revision": probe.settings.DIAGNOSTIC_REVISION,
            "source": "same_snapshot_keyfile_bytes", "proof_sha256": probe.evidence.sha(probe.evidence.canonical(proof)),
            "file_sha256": "b" * 64, "file_size": 60, "value_hash": "sha256_utf8_parsed_value",
            **probe.settings._diagnostic_inventory({probe.settings.SECTION: {probe.settings.KEY: "true"}})}
        supervisor = {"keyfile": {"after_first_toggle": proof}, "supplemental_keyfile": {},
                      "keyfile_diagnostics": {"first_after_toggle": diagnostic}}
        probe._keyfile_diagnostics(supervisor)
        mutations = (
            lambda d: d.update(proof_sha256="f" * 64),
            lambda d: d.update(file_size=61),
            lambda d: d["sections"][0]["keys"][0].update(value_sha256=probe.evidence.sha(b"false")),
            lambda d: d["sections"][0].update(section="unsafe:name"),
        )
        for index, mutate in enumerate(mutations):
            changed = copy.deepcopy(supervisor)
            mutate(changed["keyfile_diagnostics"]["first_after_toggle"])
            with self.subTest(index=index), self.assertRaises(ValueError):
                probe._keyfile_diagnostics(changed)


if __name__ == "__main__":
    unittest.main()
