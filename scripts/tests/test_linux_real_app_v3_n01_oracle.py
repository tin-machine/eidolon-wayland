"""N01 receipt mutations. These synthetic specimens are not real SSH evidence."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_desktop_v3_contract as contract
import linux_desktop_v3_wire as wire
import linux_real_app_v3_evidence as evidence
import linux_real_app_v3_n01_oracle as oracle


def specimen():
    records = {r["id"]: r for r in json.loads((Path(__file__).parent / "fixtures/linux-desktop-v3-contract.json").read_bytes())["records"]}
    source = {"commit": "1" * 40, "tree_sha256": "2" * 64, "dirty": False, "build_features": [], "pr_head": None}
    run = "a" * 32
    raw_context = json.loads(records["context"]["raw"])
    raw_context.update(uid=12002, session="1")
    context = contract.Context.parse(evidence.canonical(raw_context))
    pins = contract.Pins.parse(records["pins"]["raw"].encode())
    manifest = contract.Manifest.parse(records["manifest"]["raw"].encode())
    sources = evidence.source_input_hashes()
    capture = {"session": "1", "inspect_request": "2", "io_generation": "1", "nonce": raw_context["nonce"],
               "scope": context.scope(), "context_digest": context.sha256(),
               "capture_id": contract.digest({"context": raw_context, "pins": pins.record(),
                                               "sources": sources["compiled_source_hashes"], "inspect_request": "2", "generation": "1"})}
    owner = {"owner_id": evidence.sha((run + ":1:" + capture["capture_id"]).encode())[:32], "runtime": "1",
             "admission_epoch": "1", "connect_request": "1", "launch_request": None, "session": "1",
             "capture_sha256": capture["capture_id"], "context_sha256": context.sha256(), "io_generation": "1",
             "nonce": capture["nonce"], "scope": context.scope(), "native_origin": None}
    expected = wire.ExpectedEvidence.capture(pins, manifest, sources["compiled_source_hashes"])
    events = []

    def trace(kind, data):
        events.append({"sequence": str(len(events) + 1), "kind": kind, "capture": copy.deepcopy(capture), "data": data})

    def protocol(sequence, request, kind, payload):
        trace("protocol_event", {"record": {"schema": 1, "protocol_revision": contract.REVISION,
              "context": raw_context, "scope": context.scope(), "sequence": str(sequence), "request_sequence": str(request),
              "event": {"type": kind, "payload": payload}}})

    trace("capture_created", {"context": raw_context, "pins": pins.record(), "compiled_source_hashes": sources["compiled_source_hashes"]})
    trace("journal_reserved", {"stage": "reserved"})
    protocol(1, 0, "prepared", {"lease_ms": 15000, "boot_id": "12345678-1234-1234-1234-123456789abc",
                                "remote_socket": "/home/eidolon-app/.eidolon-desktop-v3/s-" + context.record()["nonce"] + "/p"})
    trace("journal_prepared", {"stage": "prepared"})
    trace("control_send_attempt", {"action": "inspect"})
    protocol(2, 1, "catalog", wire.catalog_payload(wire.Binding(context, pins), expected))
    trace("catalog_published", {"selections": [{"scope": context.scope(), "revision": contract.catalog_revision(context, pins), "app_id": app}
                                                for app in (contract.DEMO, contract.EDITOR)]})
    trace("no_local_allocation_sealed", {"before_cancel": True})
    trace("control_send_attempt", {"action": "stop"})
    protocol(3, 2, "stopped", {"reason": "stop", "process_complete": True, "pipes_eof": True,
                               "remote_cleaned": True, "receipt_written": True})
    remote = {"phase": "stopped", "started": False, "stdout_bytes": 1234, "stderr_bytes": 0,
              "process_complete": True, "pipes_eof": True, "remote_cleaned": True, "receipt_written": True,
              "terminal_event": True, "stop_reason": "stop", "eof": True, "exit_status": 0,
              "exit_signal": False, "channel_closed": True, "protocol_valid": True, "reason": None}
    trace("remote_cleanup_observed", {"remote": remote, "channel_complete": True, "forward_retired": True,
                                       "router_drained": True, "start_attempted": False})
    trace("journal_complete", {"stage": "complete"})
    trace("io_retired", {"generation": "1", "tokio_threads": 0})
    trace("journal_unlinked", {"generation": "1", "directory_fsynced": True})
    trace("finalizer_published", {"generation": "1", "status": 0})
    trace("controller_stop_requested", {})
    reclamation = {"runtime": "1", **dict.fromkeys(oracle.RECLAMATION_COUNTS, 0),
                   **dict.fromkeys(oracle.RECLAMATION_FLAGS, False), "origin_retired": True, "shutdown_complete": True}
    shutdown = {"requested": True, "complete": True, "elapsed_ms": 100, "deadline_ms": 10000,
                "timed_out": False, "pending_owners": [], "sdk_bindings": 0}
    runtime = {"state": "Stopped", "shutdown": shutdown, "reclamation": reclamation, "commands_pending": 0,
               "app_sdk_bindings": 0, "helper_workers": 0, "dependency_workers": 0, "compositor_workers": 0,
               "app": None, "desktop_v3_pending_targets": 0, "desktop_v3_finalizer_pending": False,
               "desktop_v3_cleanup_error": None, "panels": [], "error": None,
               "render": {"workers": 0, "targets": [], "contexts": 0, "latest_images": 0, "queued": 0, "error": None},
               "transport": {"workers": 0, "tokio_threads": 0, "owned_connections": 0, "desktop_pending": False,
                             "pending_cleanup": [], "forward": {"active": 0}, "error": None}}
    snapshot = {"schema": 1, "code": 0, "handle": "1", "runtime": runtime, "registry": {"runtimes": 1, "workers": 0}}
    observations = []

    def observe(kind, watermark, data):
        observations.append({"sequence": str(len(observations) + 1), "kind": kind, "transport_sequence": str(watermark), "data": data})

    observe("registry_created", 0, {"status": 0, "handle": "1"})
    observe("runtime_ready", 0, {})
    observe("command_accepted", 0, {"command": "connect", "request_id": "1", "status": 0})
    observe("credential_provided", 0, {"session": "1", "request_id": "1", "status": 0})
    observe("command_complete", 0, {"request_id": "1", "status": "ACCEPTED"})
    observe("command_accepted", 1, {"command": "linux_desktop_v3_inspect", "request_id": "2", "status": 0})
    observe("command_complete", 7, {"request_id": "2", "status": "ACCEPTED"})
    observe("catalog_observed", 7, {})
    observe("command_accepted", 8, {"command": "disconnect", "request_id": "3", "status": 0})
    observe("command_complete", 15, {"request_id": "3", "status": "ACCEPTED"})
    observe("origin_retired", 15, {"reclamation": {**reclamation, "workers": 3, "shutdown_complete": False}})
    observe("runtime_stop_requested", 16, {"status": 0})
    observe("runtime_shutdown_complete", 16, {"shutdown": shutdown, "reclamation": reclamation})
    observe("last_live_census", 16, {"snapshot": snapshot})
    observe("registry_destroy_return", 16, {"status": 0, "counts": {"runtimes": 0, "workers": 0}})
    native = {"schema": oracle.NATIVE_REVISION, "run_id": run, "runtime": "1", "owner_id": owner["owner_id"],
              "source_sha256": source["tree_sha256"], "build_source": source, **sources,
              "trace": {"overflow": False, "events": events}, "observations": observations, "start_count": 0,
              "catalog": {"schema": 1, "code": 0, "catalog": {"session": "1", "request_id": "2", "pending": False,
                          "reply": None, "error": None, "desktop": None, "desktop_v3": contract.catalog_descriptor(context, pins)}},
              "last_snapshot": snapshot, "destroy_status": 0, "registry_counts": {"runtimes": 0, "workers": 0},
              "error": None, "cleanup_error": None}
    inputs = {**sources, "bundle_build_id": pins.build_id(), "bundle_sha256": pins.record()["desktop_bundle_sha256"], "pins": pins.record()}
    account = lambda role, uid: {"uid": uid, "passwd_home": "/home/eidolon-" + role, "home_uid": uid,
                                 "home_mode": 0o700, "ancestors": [{"uid": 0, "mode": 0o755}] * 2, "fresh_home": True}
    environment = {"schema": 1, "revision": oracle.ENVIRONMENT_REVISION, "run_id": run, "owner_id": owner["owner_id"],
                   "source_sha256": source["tree_sha256"], "driver": account("driver", 12001), "app": account("app", 12002),
                   "ssh": {"listen_host": "127.0.0.1", "port": 22222, "host_key_sha256": "8" * 64,
                           "pinned_host_key_sha256": "8" * 64, "dedicated": True},
                   "before": copy.deepcopy(inputs), "after": copy.deepcopy(inputs),
                   "runner": {"source_sha256": source["tree_sha256"], "binary_sha256": "9" * 64},
                   "isolation": {"kind": "docker", "pid_namespace": "123456", "mount_namespace": "123457",
                                 "docker_marker": True, "docker_socket_present": False},
                   "final": {"app_process_count": 0, "driver_child_count": 0, "sshd_child_count": 0, "journal_entries": 0,
                             "profile_lock_held": False, "journal_lock_held": False}}
    receipt = {"schema": 1, "kind": oracle.REVISION, "run_id": run, "case_id": "N01", "variant": "inspect_only",
               "source_sha256": source["tree_sha256"], "owner": owner, "native": native, "environment": environment}
    return copy.deepcopy(receipt), source


def recovery_specimen(*, durable=False):
    """Same live Runtime/capture, newly fenced I/O generation, no new origin."""
    receipt, source = specimen()
    native = receipt["native"]
    events = native["trace"]["events"]
    capture = events[0]["capture"]
    context = events[0]["data"]["context"]
    pins = events[0]["data"]["pins"]
    boot = next(e["data"]["record"]["event"]["payload"]["boot_id"] for e in events
                if e["kind"] == "protocol_event" and e["data"]["record"]["event"]["type"] == "prepared")
    if not durable:
        events[:] = [e for e in events if e["kind"] != "journal_complete"]
        next(e for e in events if e["kind"] == "remote_cleanup_observed")["data"]["channel_complete"] = False
        recovered = {"schema": 1, "protocol_revision": contract.REVISION, "context": context, "pins": pins,
                     "build_id": contract.Pins.parse(evidence.canonical(pins)).build_id(),
                     "source_hashes": native["compiled_source_hashes"], "expected_boot_id": boot,
                     "origin_boot_id": boot, "boot_id": boot, "status": "complete", "process_evidence": "receipt",
                     "process_complete": True, "pipes_eof": True, "remote_cleaned": True, "receipt_written": True}
        recovery = {"kind": "recovery_receipt", "data": {"record": recovered, "generation": "2", "expected_boot_id": boot,
                    "exit_status": 0, "eof": True, "exit_signal": False, "channel_closed": True, "router_active": 0}}
    else:
        recovery = {"kind": "recovery_durable_complete", "data": {"generation": "2", "cleanup": {
            "process_complete": True, "pipes_eof": True, "remote_cleaned": True, "receipt_written": True,
            "channel_complete": True, "forward_retired": True, "router_drained": True, "boot_changed": False}}}
    insert_at = next(i for i, e in enumerate(events) if e["kind"] == "journal_unlinked")
    extras = [recovery, {"kind": "recovery_bound", "data": {"generation": "2"}},
              {"kind": "io_retired", "data": {"generation": "2", "tokio_threads": 0}}]
    for event in extras:
        event["capture"] = copy.deepcopy(capture)
    events[insert_at:insert_at] = extras
    for number, event in enumerate(events, 1):
        event["sequence"] = str(number)
        if event["kind"] in {"journal_unlinked", "finalizer_published"}:
            event["data"]["generation"] = "2"
    old_io = next(e["sequence"] for e in events if e["kind"] == "io_retired")
    finalized = next(e["sequence"] for e in events if e["kind"] == "finalizer_published")
    stopped = events[-1]["sequence"]
    observations = native["observations"]
    at = next(i for i, e in enumerate(observations) if e["kind"] == "origin_retired")
    observations[at - 1]["transport_sequence"] = old_io
    recovery_steps = [
        {"kind": "command_accepted", "transport_sequence": old_io, "data": {"command": "connect", "request_id": "4", "status": 0}},
        {"kind": "credential_provided", "transport_sequence": old_io, "data": {"session": "2", "request_id": "4", "status": 0}},
        {"kind": "command_complete", "transport_sequence": old_io, "data": {"request_id": "4", "status": "ACCEPTED"}},
        {"kind": "command_accepted", "transport_sequence": old_io, "data": {"command": "linux_desktop_v3_recover", "request_id": "5", "status": 0}},
        {"kind": "command_complete", "transport_sequence": finalized, "data": {"request_id": "5", "status": "ACCEPTED"}},
    ]
    observations[at:at] = recovery_steps
    for number, event in enumerate(observations, 1):
        event["sequence"] = str(number)
        if event["kind"] == "origin_retired":
            event["transport_sequence"] = finalized
        elif event["kind"] in {"runtime_stop_requested", "runtime_shutdown_complete", "last_live_census", "registry_destroy_return"}:
            event["transport_sequence"] = stopped
    return receipt, source


class N01OracleTests(unittest.TestCase):
    def setUp(self):
        self.receipt, self.source = specimen()
        self.expected_owner = copy.deepcopy(self.receipt["owner"])

    def verify(self):
        return oracle.verify_receipt(self.receipt, expected_run_id="a" * 32,
                                     expected_source_sha256=self.source["tree_sha256"],
                                     expected_owner=self.expected_owner, expected_source=self.source)

    def trace(self, kind):
        return next(e for e in self.receipt["native"]["trace"]["events"] if e["kind"] == kind)

    def observation(self, kind):
        return next(e for e in self.receipt["native"]["observations"] if e["kind"] == kind)

    def test_complete_specimen_derives_all_eight_fixed_assertions_only(self):
        result = self.verify()
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(set(result["verified_assertions"]), oracle.ASSERTIONS)
        self.assertEqual(set(result["census"]), evidence.RESOURCES)
        self.assertNotIn("host_v3_acceptance_passed", result)

    def test_unknown_duplicate_types_and_raw_json_are_strict(self):
        self.receipt["native"]["extra"] = True
        with self.assertRaises(ValueError):
            self.verify()
        del self.receipt["native"]["extra"]
        for holder, field in ((self.receipt, "schema"), (self.receipt["native"], "start_count"),
                               (self.receipt["environment"]["final"], "app_process_count")):
            old = holder[field]
            holder[field] = True
            with self.assertRaises(ValueError):
                self.verify()
            holder[field] = old
        with self.assertRaises(evidence.EvidenceError):
            evidence.parse(b'{"schema":1,"schema":1}')

    def test_every_owner_capture_field_is_joined(self):
        for field in evidence.OWNER_FIELDS:
            with self.subTest(field=field):
                old = self.receipt["owner"][field]
                self.receipt["owner"][field] = None if old is not None else "1"
                with self.assertRaises(ValueError):
                    self.verify()
                self.receipt["owner"][field] = old
        self.trace("journal_unlinked")["capture"]["capture_id"] = "f" * 64
        with self.assertRaises(ValueError):
            self.verify()

    def test_artifact_native_environment_cannot_mix_run_owner_or_source(self):
        for part in (self.receipt, self.receipt["native"], self.receipt["environment"]):
            for field in ("run_id", "source_sha256"):
                old = part[field]
                part[field] = "f" * len(old)
                with self.assertRaises(ValueError):
                    self.verify()
                part[field] = old
        self.receipt["native"]["build_source"]["commit"] = "e" * 40
        with self.assertRaises(ValueError):
            self.verify()

    def test_compiled_source_and_immutable_bundle_are_independent(self):
        for stage in ("before", "after"):
            old = self.receipt["environment"][stage]["bundle_sha256"]
            self.receipt["environment"][stage]["bundle_sha256"] = "f" * 64
            with self.assertRaises(ValueError):
                self.verify()
            self.receipt["environment"][stage]["bundle_sha256"] = old
        self.receipt["native"]["compiled_source_hashes"]["eidolon_linux_desktop_v3.py"] = "f" * 64
        with self.assertRaises(ValueError):
            self.verify()

    def test_live_environment_requires_dedicated_nonroot_passwd_homes_and_pin(self):
        mutations = [lambda r: r["environment"]["app"].update(uid=0),
                     lambda r: r["environment"]["app"].update(uid=r["environment"]["driver"]["uid"]),
                     lambda r: r["environment"]["app"].update(passwd_home="/tmp/fake-home"),
                     lambda r: r["environment"]["app"].update(home_mode=0o777),
                     lambda r: r["environment"]["app"].update(fresh_home=False),
                     lambda r: r["environment"]["isolation"].update(kind="host"),
                     lambda r: r["environment"]["isolation"].update(docker_socket_present=True),
                     lambda r: r["environment"]["ssh"].update(pinned_host_key_sha256="f" * 64)]
        original = copy.deepcopy(self.receipt)
        for mutate in mutations:
            self.receipt = copy.deepcopy(original)
            mutate(self.receipt)
            with self.assertRaises(ValueError):
                self.verify()

    def test_catalog_and_wire_ack_are_independently_reconstructed(self):
        self.receipt["native"]["catalog"]["catalog"]["desktop_v3"]["entries"][0]["id"] = "shell"
        with self.assertRaises(ValueError):
            self.verify()
        self.receipt, _ = specimen()
        self.trace("protocol_event")["data"]["record"]["sequence"] = "2"
        with self.assertRaises(ValueError):
            self.verify()

    def test_missing_receipt_steps_stay_incomplete_and_overflow_is_sticky(self):
        self.receipt["native"]["trace"]["overflow"] = True
        result = self.verify()
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["verified_assertions"], [])
        self.receipt["native"]["trace"]["overflow"] = False
        observations = self.receipt["native"]["observations"]
        observations.pop()
        result = self.verify()
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("receipt/registry_destroy_return", result["missing"])

    def test_start_count_and_command_completion_are_not_claimed_booleans(self):
        self.receipt["native"]["start_count"] = 1
        with self.assertRaises(ValueError):
            self.verify()
        self.receipt["native"]["start_count"] = 0
        self.observation("command_complete")["data"]["status"] = "FAILED"
        self.assertEqual(self.verify()["status"], "fail")

    def test_cleanup_order_is_not_replaced_by_empty_resource_counts(self):
        events = self.receipt["native"]["trace"]["events"]
        seal = events.index(self.trace("no_local_allocation_sealed"))
        events[seal], events[seal + 1] = events[seal + 1], events[seal]
        for i, event in enumerate(events, 1):
            event["sequence"] = str(i)
        with self.assertRaises(ValueError):
            self.verify()
        self.receipt, _ = specimen()
        self.observation("origin_retired")["transport_sequence"] = "14"
        # Previous watermark is15: both order and finalizer causality fail.
        with self.assertRaises(ValueError):
            self.verify()

    def test_failed_remote_finalizer_and_destroy_cannot_pass(self):
        for kind, field, value in (("remote_cleanup_observed", "channel_complete", False),
                                    ("finalizer_published", "status", 4)):
            old = self.trace(kind)["data"][field]
            self.trace(kind)["data"][field] = value
            self.assertEqual(self.verify()["status"], "fail")
            self.trace(kind)["data"][field] = old
        self.receipt["native"]["destroy_status"] = 2
        self.observation("registry_destroy_return")["data"]["status"] = 2
        self.assertEqual(self.verify()["status"], "fail")

    def test_independent_process_and_filesystem_census_rejects_leaks(self):
        final = self.receipt["environment"]["final"]
        for field in final:
            old = final[field]
            final[field] = True if type(old) is bool else 1
            self.assertEqual(self.verify()["status"], "fail", field)
            final[field] = old

    def test_source_dirty_error_and_deadline_never_pass(self):
        self.receipt["native"]["error"] = "I6_OUTER_TIMEOUT"
        self.assertEqual(self.verify()["status"], "incomplete")
        self.receipt["native"]["error"] = "I6_FINALIZER_MISSING"
        self.assertEqual(self.verify()["status"], "fail")
        self.receipt["native"]["error"] = None
        self.observation("runtime_shutdown_complete")["data"]["shutdown"]["deadline_ms"] = 11000
        self.assertEqual(self.verify()["status"], "fail")

    def test_live_recover_joins_original_capture_and_new_fenced_io(self):
        for durable in (False, True):
            with self.subTest(durable=durable):
                self.receipt, self.source = recovery_specimen(durable=durable)
                result = self.verify()
                self.assertEqual(result["status"], "pass", result)
                self.assertEqual(self.receipt["owner"]["io_generation"], "1")
                self.assertEqual(self.trace("finalizer_published")["data"]["generation"], "2")

    def test_recovery_cannot_replace_owner_generation_boot_or_durable_cleanup(self):
        mutations = [lambda r: next(e for e in r["native"]["trace"]["events"] if e["kind"] == "recovery_bound")["capture"].update(capture_id="f" * 64),
                     lambda r: next(e for e in r["native"]["trace"]["events"] if e["kind"] == "recovery_receipt")["data"].update(generation="3"),
                     lambda r: next(e for e in r["native"]["trace"]["events"] if e["kind"] == "recovery_receipt")["data"].update(expected_boot_id="87654321-4321-4321-4321-abcdef123456"),
                     lambda r: next(e for e in r["native"]["observations"] if e["kind"] == "credential_provided" and e["data"]["request_id"] == "4")["data"].update(session="3")]
        for mutate in mutations:
            self.receipt, self.source = recovery_specimen()
            mutate(self.receipt)
            with self.assertRaises(ValueError):
                self.verify()
        self.receipt, self.source = recovery_specimen(durable=True)
        self.trace("recovery_durable_complete")["data"]["cleanup"]["channel_complete"] = False
        self.assertEqual(self.verify()["status"], "fail")

    def test_cleanup_error_is_separate_from_first_failure_and_cannot_verify(self):
        self.receipt["native"]["cleanup_error"] = "I6_FINALIZER_MISSING"
        self.receipt["native"]["error"] = "I6_OUTER_TIMEOUT"
        result = self.verify()
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["verified_assertions"], [])


class N01ValidatorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.receipt, self.source = specimen()
        self.report = evidence.incomplete_report(self.source, run_id=self.receipt["run_id"])
        self.case = next(c for c in self.report["cases"] if (c["case_id"], c["variant"]) == oracle.CASE)
        self.case.update(status="pass", reason="I6_OK", observed_product_outcome="complete", owners=[self.receipt["owner"]])
        ref = {"artifact": "n01-receipt.json", "owner_id": self.receipt["owner"]["owner_id"], "target_id": None, "event_seq": None}
        self.case["assertions"] = [{"assertion_id": assertion, "status": "pass", "reason": "I6_OK", "evidence_refs": [copy.deepcopy(ref)]}
                                    for assertion in sorted(oracle.ASSERTIONS)]
        self.case["cleanup"].update(product="complete", environment_disposal="not_yet",
             census=[{"resource": resource, "owner_id": ref["owner_id"], "target_id": None,
                      "state": "not_created" if resource in oracle.UNALLOCATED_RESOURCES else "complete",
                      "evidence_refs": [copy.deepcopy(ref)], "missing_reasons": []} for resource in sorted(evidence.RESOURCES)])
        self.artifact = {"path": ref["artifact"], "size": 0, "sha256": "0" * 64, "evidence_class": "R", "kind": "n01_receipt",
                         "run_id": self.report["run_id"], "case_id": "N01", "variant": "inspect_only", "owner_id": ref["owner_id"],
                         "target_id": None, "source_sha256": self.source["tree_sha256"]}
        self.report["artifacts"] = [self.artifact]
        self.publish()

    def publish(self):
        data = evidence.canonical(self.receipt)
        path = self.root / self.artifact["path"]
        path.write_bytes(data)
        path.chmod(0o600)
        self.artifact.update(size=len(data), sha256=evidence.sha(data))

    def validate(self):
        return evidence.validate_acceptance(evidence.canonical(self.report), self.root, self.source)

    def test_n01_verifies_but_all_87_required_variants_and_i6_incomplete_remain(self):
        result = self.validate()
        self.assertEqual(result["exit_code"], 2, result)
        self.assertTrue(result["n01_inspect_only_passed"], result)
        self.assertEqual(result["verified_case_oracles"], ["N01/inspect_only"])
        self.assertFalse(result["host_v3_acceptance_passed"])
        self.assertFalse(result["product_oracles_verified"])
        self.assertEqual(len(self.report["cases"]), 87)
        self.assertIn("N02/mousepad_editor", result["missing"])
        self.assertIn("N01/inspect_only/environment_disposal", result["missing"])

    def test_missing_or_foreign_receipt_is_never_n01_success(self):
        (self.root / self.artifact["path"]).unlink()
        result = self.validate()
        self.assertEqual(result["exit_code"], 2)
        self.assertFalse(result["n01_inspect_only_passed"])
        self.receipt["environment"]["owner_id"] = "f" * 32
        self.publish()
        self.assertEqual(self.validate()["exit_code"], 3)

    def test_census_state_cannot_disagree_with_seal_even_after_digest_rewrite(self):
        row = next(c for c in self.case["cleanup"]["census"] if c["resource"] == "local_waypipe")
        row["state"] = "complete"
        self.assertEqual(self.validate()["exit_code"], 3)

    def test_failure_cancel_and_first_product_error_remain_non_success(self):
        self.case["status"] = "fail"
        self.case["reason"] = "I6_PRODUCT_FAILURE"
        result = self.validate()
        self.assertEqual(result["exit_code"], 1)
        self.assertFalse(result["n01_inspect_only_passed"])
        self.case["status"] = "pass"
        self.receipt["native"]["error"] = "I6_FINALIZER_MISSING"
        self.publish()
        result = self.validate()
        self.assertEqual(result["exit_code"], 1)
        self.assertFalse(result["n01_inspect_only_passed"])

    def test_typed_receipt_cannot_supply_another_case_or_multiple_owners(self):
        self.artifact.update(case_id="N02", variant="mousepad_editor")
        self.assertEqual(self.validate()["exit_code"], 3)


if __name__ == "__main__":
    unittest.main()
