"""Independent I6 envelope attacks; specimens are not product-run evidence.

Exercise actual private files and trace decoding instead of replacing the
validator. The intentionally incomplete specimen never claims an SSH/GUI run.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_real_app_v3_evidence as evidence


class TraceSpecimen(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.source = {"commit": "1" * 40, "tree_sha256": "2" * 64, "dirty": False,
                       "build_features": [], "pr_head": None}
        self.report = evidence.incomplete_report(self.source, run_id="a" * 32)
        self.case = next(c for c in self.report["cases"]
                         if (c["case_id"], c["variant"]) == ("N02", "mousepad_editor"))
        self.owner = {"owner_id": "3" * 32, "runtime": "1", "admission_epoch": "1",
                      "connect_request": "2", "launch_request": "3", "session": "4",
                      "capture_sha256": "4" * 64, "context_sha256": "5" * 64,
                      "io_generation": "6", "nonce": "6" * 16, "scope": "7" * 64,
                      "native_origin": "7"}
        self.target = {"target_id": "8" * 32, "owner_id": self.owner["owner_id"],
                       "session": "4", "window": "5", "scene": "1", "generation": "1",
                       "create_seq": "6", "adapter_id": "7"}
        self.case["owners"] = [copy.deepcopy(self.owner)]
        self.case["targets"] = [copy.deepcopy(self.target)]
        self.case["trace"]["artifacts"] = ["trace.json"]
        self.case["assertions"] = [{"assertion_id": "same_actor_catalog", "status": "pass",
                                    "reason": "I6_OK", "evidence_refs": [self.ref()]}]
        self.trace = {"schema": 1, "kind": "eidolon-i6-trace-v1", "run_id": self.report["run_id"],
                      "case_id": self.case["case_id"], "variant": self.case["variant"],
                      "source_sha256": self.source["tree_sha256"], "overflow": False,
                      "events": [self.event()]}
        self.artifact = {"path": "trace.json", "size": 0, "sha256": "0" * 64,
                         "evidence_class": "R", "kind": "trace", "run_id": self.report["run_id"],
                         "case_id": self.case["case_id"], "variant": self.case["variant"],
                         "owner_id": self.owner["owner_id"], "target_id": self.target["target_id"],
                         "source_sha256": self.source["tree_sha256"]}
        self.report["artifacts"] = [self.artifact]
        self.publish()

    def ref(self, *, owner=None, target=True, seq="1", path="trace.json"):
        return {"artifact": path, "owner_id": owner or self.owner["owner_id"],
                "target_id": self.target["target_id"] if target else None, "event_seq": seq}

    def event(self, *, seq="1", kind="observation", target=True, assertion="same_actor_catalog"):
        return {"seq": seq, "event": kind, "owner": copy.deepcopy(self.owner),
                "target": copy.deepcopy(self.target) if target else None,
                "evidence_class": "R", "assertion_id": assertion, "resource": None}

    def publish(self):
        data = evidence.canonical(self.trace)
        path = self.root / self.artifact["path"]
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o600)
        self.artifact.update(size=len(data), sha256=evidence.sha(data))

    def validate(self):
        return evidence.validate_acceptance(evidence.canonical(self.report), self.root, self.source)

    def assert_invalid(self, result=None):
        result = self.validate() if result is None else result
        self.assertEqual(result["exit_code"], 3, self.brief(result))
        self.assertEqual(result["status"], "invalid")
        self.assertFalse(result["evidence_contract_valid"])
        self.assertFalse(result["host_v3_acceptance_passed"])

    @staticmethod
    def brief(result):
        return {key: result[key] for key in ("status", "reason", "exit_code")}


class OwnershipTraceTests(TraceSpecimen):
    def test_actual_trace_decodes_but_partial_run_stays_incomplete(self):
        result = self.validate()
        self.assertEqual(result["exit_code"], 2)
        self.assertTrue(result["evidence_contract_valid"])
        self.assertFalse(result["product_oracles_verified"])
        self.assertFalse(result["host_v3_acceptance_passed"])

    def test_each_correlation_field_is_joined_to_the_declared_owner(self):
        original = copy.deepcopy(self.trace)
        changes = {"runtime": "2", "admission_epoch": "2", "connect_request": "9",
                   "launch_request": "9", "session": "9", "capture_sha256": "a" * 64,
                   "context_sha256": "b" * 64, "io_generation": "9", "nonce": "a" * 16,
                   "scope": "b" * 64, "native_origin": "9", "owner_id": "9" * 32}
        for field, value in changes.items():
            with self.subTest(field=field):
                self.trace = copy.deepcopy(original)
                self.trace["events"][0]["owner"][field] = value
                self.publish()
                self.assert_invalid()

    def test_all_target_fields_are_joined_to_the_issued_generation(self):
        original = copy.deepcopy(self.trace)
        changes = {"session": "8", "window": "8", "scene": "8", "generation": "8",
                   "create_seq": "8", "adapter_id": "8", "target_id": "9" * 32,
                   "owner_id": "9" * 32}
        for field, value in changes.items():
            with self.subTest(field=field):
                self.trace = copy.deepcopy(original)
                self.trace["events"][0]["target"][field] = value
                self.publish()
                self.assert_invalid()

    def test_allocated_identity_cannot_be_erased_with_null(self):
        for field in ("session", "launch_request", "connect_request", "context_sha256",
                      "nonce", "scope", "io_generation", "native_origin"):
            with self.subTest(field=field):
                self.case["owners"][0] = copy.deepcopy(self.owner)
                self.case["owners"][0][field] = None
                self.assert_invalid()

    def test_cross_run_and_source_are_rejected_in_the_artifact_and_trace(self):
        for container_name, field, value in (("artifact", "run_id", "b" * 32),
                                              ("artifact", "source_sha256", "b" * 64),
                                              ("trace", "run_id", "b" * 32),
                                              ("trace", "source_sha256", "b" * 64)):
            with self.subTest(container=container_name, field=field):
                container = getattr(self, container_name)
                previous = container[field]
                container[field] = value
                self.publish()
                self.assert_invalid()
                container[field] = previous

    def test_actual_trace_cannot_use_another_cases_owner(self):
        second = next(c for c in self.report["cases"] if c["case_id"] == "N01")
        stolen = copy.deepcopy(self.owner)
        stolen["owner_id"] = "9" * 32
        second["owners"] = [stolen]
        self.trace["events"][0]["owner"] = stolen
        self.publish()
        self.assert_invalid()

    def test_repeated_or_reversed_event_sequence_is_invalid(self):
        for numbers in (("1", "1"), ("2", "1")):
            with self.subTest(numbers=numbers):
                self.trace["events"] = [self.event(seq=seq) for seq in numbers]
                self.publish()
                self.assert_invalid()

    def test_same_owner_sequence_cannot_be_replayed_in_another_trace_file(self):
        data = (self.root / "trace.json").read_bytes()
        (self.root / "replay.json").write_bytes(data)
        (self.root / "replay.json").chmod(0o600)
        replay = {**self.artifact, "path": "replay.json"}
        self.report["artifacts"].append(replay)
        self.case["trace"]["artifacts"].append("replay.json")
        self.assert_invalid()

    def test_same_owner_seal_must_name_the_census_resource(self):
        self.case["cleanup"]["census"] = [{"resource": "local_pipes", "owner_id": self.owner["owner_id"],
            "target_id": None, "state": "not_created", "missing_reasons": [],
            "evidence_refs": [self.ref(target=False)]}]
        self.trace["events"][0].update(event="allocation_sealed", resource="local_pipes",
                                       target=None, assertion_id=None)
        self.artifact["target_id"] = None
        self.case["assertions"] = []
        self.publish()
        self.assertTrue(self.validate()["evidence_contract_valid"])
        self.trace["events"][0]["resource"] = "remote_pipes"
        self.publish()
        self.assert_invalid()

    def test_trace_reference_cannot_satisfy_a_different_assertion(self):
        self.trace["events"][0]["assertion_id"] = "fixed_main"
        self.publish()
        self.assert_invalid()

    def test_overflow_is_sticky_and_a_disagreeing_summary_is_invalid(self):
        self.trace["overflow"] = True
        self.case["trace"]["overflow"] = True
        self.publish()
        result = self.validate()
        self.assertEqual(result["exit_code"], 2)
        self.assertIn("N02/mousepad_editor/trace_overflow", result["missing"])
        self.case["trace"]["overflow"] = False
        self.assert_invalid()

    def test_a_wrong_event_cannot_supply_allocation_seal(self):
        self.case["cleanup"]["census"] = [{"resource": "local_pipes", "owner_id": self.owner["owner_id"],
            "target_id": None, "state": "not_created", "missing_reasons": [],
            "evidence_refs": [self.ref(target=False)]}]
        self.trace["events"][0]["target"] = None
        self.artifact["target_id"] = None
        self.case["assertions"] = []
        self.publish()
        self.assert_invalid()

    def test_another_owner_cannot_supply_a_not_created_seal(self):
        other = copy.deepcopy(self.owner)
        other["owner_id"] = "9" * 32
        self.case["owners"].append(other)
        self.case["cleanup"]["census"] = [{"resource": "local_pipes", "owner_id": other["owner_id"],
            "target_id": None, "state": "not_created", "missing_reasons": [],
            "evidence_refs": [self.ref(target=False)]}]
        self.trace["events"][0].update(event="allocation_sealed", target=None, assertion_id=None,
                                        resource="local_pipes")
        self.artifact["target_id"] = None
        self.case["assertions"] = []
        self.publish()
        self.assert_invalid()

    def test_invalid_artifact_takes_priority_over_cancel_and_case_failure(self):
        self.report.update(status="cancelled", reason="I6_OPERATOR_CANCELLED", exit_code=130)
        self.case["first_case_failure"] = {"reason": "I6_PRODUCT_FAILURE",
                                           "owner_id": self.owner["owner_id"], "sequence": "1"}
        self.artifact["sha256"] = "f" * 64
        self.assert_invalid()

    def test_valid_cancel_preserves_the_original_case_and_cleanup_failures(self):
        self.report.update(status="cancelled", reason="I6_OPERATOR_CANCELLED", exit_code=130)
        self.case["first_case_failure"] = {"reason": "I6_PRODUCT_FAILURE",
                                           "owner_id": self.owner["owner_id"], "sequence": "1"}
        self.case["cleanup_failures"] = [{"reason": "I6_CLEANUP_FAILED",
                                          "owner_id": self.owner["owner_id"], "sequence": "2"}]
        result = self.validate()
        self.assertEqual(result["exit_code"], 130)
        self.assertEqual(result["first_case_failure"]["reason"], "I6_PRODUCT_FAILURE")
        self.assertEqual(result["cleanup_failures"][0]["reason"], "I6_CLEANUP_FAILED")

    def test_observed_cleanup_failure_is_not_downgraded_to_missing_evidence(self):
        for domain in ("product", "fault_resources", "fixture_rescue", "environment_disposal"):
            with self.subTest(domain=domain):
                previous = self.case["cleanup"][domain]
                self.case["cleanup"][domain] = "failed"
                result = self.validate()
                self.assertEqual(result["exit_code"], 1, self.brief(result))
                self.assertFalse(result["host_v3_acceptance_passed"])
                self.case["cleanup"][domain] = previous
        self.case["cleanup"]["census"] = [{"resource": "local_pipes", "owner_id": self.owner["owner_id"],
            "target_id": None, "state": "failed", "missing_reasons": ["I6_CLEANUP_FAILED"], "evidence_refs": []}]
        self.assertEqual(self.validate()["exit_code"], 1)

    def test_forged_success_fields_cannot_certify_a_host_run(self):
        self.report.update(status="pass", reason="I6_OK", exit_code=0, host_v3_acceptance_passed=True)
        for case in self.report["cases"]:
            case.update(status="pass", reason="I6_OK")
        result = self.validate()
        self.assertEqual(result["exit_code"], 2)
        self.assertFalse(result["host_v3_acceptance_passed"])
        self.assertFalse(result["product_oracles_verified"])

    def test_complete_envelope_still_requires_independent_product_oracles(self):
        """Even complete, internally consistent metadata is not a live run."""
        manifest = evidence.load_manifest()
        self.report = evidence.incomplete_report(self.source, run_id="a" * 32)
        inputs = dict.fromkeys(evidence.INPUT_FIELDS - {"compiled_source_hashes", "golden_revision"}, "c" * 64)
        inputs.update(evidence.source_input_hashes(), golden_revision="synthetic_contract_specimen")
        self.report["inputs"] = {"before": copy.deepcopy(inputs), "after": copy.deepcopy(inputs)}
        self.report.update(status="pass", reason="I6_OK", exit_code=0, host_v3_acceptance_passed=True)
        self.report["artifacts"] = []
        complete_kinds = {"journal_finalizer": "journal_finalized", "origin_retirement": "origin_retired",
                          "runtime_shutdown": "runtime_stopped", "registry_destroy": "registry_destroy_accepted"}
        for index, (case, row) in enumerate(zip(self.report["cases"], manifest["cases"]), 1):
            owner = {**self.owner, "owner_id": f"{index:032x}"}
            evidence_class = row["evidence_classes"][0]
            path = f"full/trace-{index}.json"
            events = []

            def append_event(kind, resource=None, assertion=None):
                seq = str(len(events) + 1)
                events.append({"seq": seq, "event": kind, "owner": copy.deepcopy(owner), "target": None,
                               "evidence_class": evidence_class, "assertion_id": assertion, "resource": resource})
                return {"artifact": path, "owner_id": owner["owner_id"], "target_id": None, "event_seq": seq}

            case.update(status="pass", reason="I6_OK", observed_product_outcome=row["expected_product_outcome"],
                        owners=[owner], targets=[])
            case["assertions"] = [{"assertion_id": assertion, "status": "pass", "reason": "I6_OK",
                                    "evidence_refs": [append_event("observation", assertion=assertion)]}
                                   for assertion in row["assertions"]]
            retained = row["cleanup_policy"] == "preserve_unknown_then_rescue"
            contract_only = row["cleanup_policy"] == "contract_only"
            case["cleanup"].update(product="not_applicable" if contract_only else "unknown" if retained else "complete",
                                   fault_resources="not_needed", fixture_rescue="complete" if retained else "not_needed",
                                   environment_disposal="not_needed" if contract_only else "complete", census=[])
            if not contract_only:
                for resource in sorted(evidence.RESOURCES - {"runtime_shutdown", "registry_destroy"}):
                    state = "not_created" if resource in evidence.TARGET_RESOURCES else "unknown" if retained else "complete"
                    kind = "allocation_sealed" if state == "not_created" else "resource_unknown" if retained else complete_kinds.get(resource, "resource_complete")
                    case["cleanup"]["census"].append({"resource": resource, "owner_id": owner["owner_id"],
                        "target_id": None, "state": state, "missing_reasons": [],
                        "evidence_refs": [append_event(kind, resource)]})
                if not retained:
                    append_event("runtime_stop")
                for resource in ("runtime_shutdown", "registry_destroy"):
                    if resource == "registry_destroy" and not retained:
                        append_event("census")
                    case["cleanup"]["census"].append({"resource": resource, "owner_id": owner["owner_id"],
                        "target_id": None, "state": "unknown" if retained else "complete", "missing_reasons": [],
                        "evidence_refs": [append_event("resource_unknown" if retained else complete_kinds[resource], resource)]})
                append_event("product_result_fixed")
                if retained:
                    append_event("fixture_rescue")
                append_event("environment_disposed")
            case["trace"] = {"overflow": False, "artifacts": [path]}
            trace = {"schema": 1, "kind": "eidolon-i6-trace-v1", "run_id": self.report["run_id"],
                     "case_id": case["case_id"], "variant": case["variant"], "source_sha256": self.source["tree_sha256"],
                     "overflow": False, "events": events}
            data = evidence.canonical(trace)
            destination = self.root / path
            destination.parent.mkdir(mode=0o700, exist_ok=True)
            destination.write_bytes(data)
            destination.chmod(0o600)
            self.report["artifacts"].append({"path": path, "size": len(data), "sha256": evidence.sha(data),
                "kind": "trace", "evidence_class": evidence_class, "run_id": self.report["run_id"],
                "case_id": case["case_id"], "variant": case["variant"], "owner_id": owner["owner_id"],
                "target_id": None, "source_sha256": self.source["tree_sha256"]})
        result = self.validate()
        self.assertTrue(result["evidence_contract_valid"], self.brief(result))
        self.assertEqual(result["missing"][:10], [], self.brief(result))
        self.assertEqual(result["reason"], "I6_ORACLE_UNAVAILABLE", self.brief(result))
        self.assertEqual(result["exit_code"], 2)
        self.assertGreater(result["unverified_assertions"], 500)
        self.assertFalse(result["host_v3_acceptance_passed"])
        self.assertFalse(result["product_oracles_verified"])


class N01PartialVerdictTests(unittest.TestCase):
    """A valid synthetic receipt cannot hide incomplete envelope references."""

    def setUp(self):
        from linux_real_app_v3_n01 import assemble_report
        from test_linux_real_app_v3_n01_oracle import specimen

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        receipt, self.source = specimen()
        self.report, verdict = assemble_report(
            self.source, self.source, receipt["native"], receipt["environment"], self.root)
        self.assertEqual(verdict["status"], "pass")
        self.case = next(c for c in self.report["cases"] if c["case_id"] == "N01")

    def validate(self):
        return evidence.validate_acceptance(evidence.canonical(self.report), self.root, self.source)

    def declare(self, path, *, kind="opaque", case=None, present=False):
        case = self.case if case is None else case
        data = b"{}\n"
        if present:
            leaf = self.root / path
            leaf.write_bytes(data)
            leaf.chmod(0o600)
        self.report["artifacts"].append({
            **self.report["artifacts"][0], "path": path, "kind": kind,
            "size": len(data), "sha256": evidence.sha(data),
            "case_id": case["case_id"], "variant": case["variant"],
            "owner_id": case["owners"][0]["owner_id"] if case["owners"] else None})

    def assert_incomplete_n01(self, missing):
        result = self.validate()
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["status"], "incomplete")
        self.assertIn(missing, result["missing"])
        self.assertFalse(result["n01_inspect_only_passed"])
        self.assertNotIn("N01/inspect_only", result["verified_case_oracles"])
        self.assertFalse(result["host_v3_acceptance_passed"])

    def test_missing_census_reference_blocks_partial_pass(self):
        original = copy.deepcopy(self.report)
        for declared in (False, True):
            for state in ("complete", "not_created"):
                with self.subTest(declared=declared, state=state):
                    self.report = copy.deepcopy(original)
                    self.case = next(c for c in self.report["cases"] if c["case_id"] == "N01")
                    row = next(c for c in self.case["cleanup"]["census"]
                               if c["resource"] == "journal_finalizer")
                    row["state"] = state
                    row["evidence_refs"][0]["artifact"] = "missing-finalizer.json"
                    if declared:
                        self.declare("missing-finalizer.json")
                    self.assert_incomplete_n01("artifact/missing-finalizer.json")

    def test_census_reference_to_opaque_bytes_blocks_partial_pass(self):
        row = next(c for c in self.case["cleanup"]["census"] if c["resource"] == "journal_finalizer")
        row["evidence_refs"][0]["artifact"] = "opaque-finalizer.json"
        self.declare("opaque-finalizer.json", present=True)
        self.assert_incomplete_n01("typed_receipt/opaque-finalizer.json")

    def test_extra_assertion_reference_must_also_exist(self):
        assertion = self.case["assertions"][0]
        extra = {**assertion["evidence_refs"][0], "artifact": "missing-assertion.json"}
        assertion["evidence_refs"].append(extra)
        self.assert_incomplete_n01("artifact/missing-assertion.json")

    def test_n01_trace_declaration_must_exist(self):
        self.case["trace"]["artifacts"] = ["missing-trace.json"]
        self.assert_incomplete_n01("artifact/missing-trace.json")
        self.declare("missing-trace.json", kind="trace")
        self.assert_incomplete_n01("artifact/missing-trace.json")

    def test_missing_declared_artifact_is_attached_to_its_case(self):
        other = next(c for c in self.report["cases"] if c["case_id"] == "N02")
        self.declare("other-missing.json", case=other)
        result = self.validate()
        self.assertIn("artifact/other-missing.json", result["missing"])
        self.assertTrue(result["n01_inspect_only_passed"])
        self.assertEqual(result["exit_code"], 2)
        self.declare("n01-missing.json")
        self.assert_incomplete_n01("artifact/n01-missing.json")

    def test_cancelled_n01_status_or_reason_blocks_partial_pass(self):
        for status, reason in (("cancelled", "I6_OPERATOR_CANCELLED"),
                               ("cancelled", "I6_OK"), ("pass", "I6_OPERATOR_CANCELLED")):
            with self.subTest(status=status, reason=reason):
                self.case.update(status=status, reason=reason)
                result = self.validate()
                self.assertEqual(result["status"], "cancelled")
                self.assertEqual(result["reason"], "I6_OPERATOR_CANCELLED")
                self.assertEqual(result["exit_code"], 130)
                self.assertFalse(result["n01_inspect_only_passed"])
                self.assertNotIn("N01/inspect_only", result["verified_case_oracles"])

    def test_other_case_cancellation_preserves_completed_n01(self):
        other = next(c for c in self.report["cases"] if c["case_id"] == "N02")
        other.update(status="cancelled", reason="I6_OPERATOR_CANCELLED")
        result = self.validate()
        self.assertEqual(result["exit_code"], 130)
        self.assertTrue(result["n01_inspect_only_passed"])
        self.assertEqual(result["verified_case_oracles"], ["N01/inspect_only"])
        self.assertFalse(result["host_v3_acceptance_passed"])

    def test_nonpass_n01_status_never_grants_partial_pass(self):
        for status, reason, code in (("not_run", "I6_NOT_RUN", 2),
                                     ("incomplete", "I6_EVIDENCE_MISSING", 2),
                                     ("fail", "I6_ASSERTION_FAILED", 1)):
            with self.subTest(status=status):
                self.case.update(status=status, reason=reason)
                result = self.validate()
                self.assertEqual(result["exit_code"], code)
                self.assertFalse(result["n01_inspect_only_passed"])

    def test_available_census_reference_still_rejects_not_created_contradiction(self):
        row = next(c for c in self.case["cleanup"]["census"] if c["resource"] == "journal_finalizer")
        row["state"] = "not_created"
        result = self.validate()
        self.assertEqual(result["exit_code"], 3)
        self.assertEqual(result["reason"], "I6_N01_RECEIPT_INVALID")
        self.assertFalse(result["n01_inspect_only_passed"])


class PrivateArtifactTests(TraceSpecimen):
    def test_missing_artifact_is_incomplete_not_a_corrupt_artifact(self):
        (self.root / "trace.json").unlink()
        result = self.validate()
        self.assertEqual(result["exit_code"], 2)
        self.assertIn("artifact/trace.json", result["missing"])

    def test_lexical_path_variations_never_escape_the_root(self):
        for path in ("/etc/passwd", "../trace.json", "a/../trace.json", "./trace.json",
                     "a//trace.json", "a\\trace.json", "trace\x00.json", "é.json", "a" * 241):
            with self.subTest(path=path):
                self.artifact["path"] = path
                self.case["trace"]["artifacts"] = [path]
                self.case["assertions"][0]["evidence_refs"][0]["artifact"] = path
                self.assert_invalid()

    def test_symlink_leaf_cannot_read_private_payload(self):
        private = self.root / "private-source"
        private.write_bytes(b"private-payload-must-never-escape")
        private.chmod(0o600)
        path = self.root / "trace.json"
        path.unlink()
        path.symlink_to(private)
        result = self.validate()
        self.assert_invalid(result)
        self.assertNotIn("private-payload", json.dumps(result))

    def test_symlink_ancestor_is_rejected(self):
        actual = self.root / "real"
        actual.mkdir(mode=0o700)
        (self.root / "trace.json").rename(actual / "trace.json")
        (self.root / "alias").symlink_to(actual, target_is_directory=True)
        self.artifact["path"] = "alias/trace.json"
        self.case["trace"]["artifacts"] = [self.artifact["path"]]
        self.case["assertions"][0]["evidence_refs"][0]["artifact"] = self.artifact["path"]
        self.assert_invalid()

    def test_fifo_is_rejected_without_opening_a_writer(self):
        path = self.root / "trace.json"
        path.unlink()
        os.mkfifo(path, 0o600)
        self.assert_invalid()

    def test_hard_link_is_rejected(self):
        os.link(self.root / "trace.json", self.root / "second-name")
        self.assert_invalid()

    def test_group_readable_artifact_is_rejected(self):
        (self.root / "trace.json").chmod(0o640)
        self.assert_invalid()

    def test_oversized_artifact_is_rejected_before_reading(self):
        with (self.root / "trace.json").open("r+b") as stream:
            stream.truncate(evidence.MAX_TRACE_BYTES + 1)
        self.assert_invalid()

    def test_artifact_size_and_digest_are_both_checked(self):
        self.artifact["size"] += 1
        self.assert_invalid()
        self.artifact["size"] -= 1
        self.artifact["sha256"] = "b" * 64
        self.assert_invalid()

    def test_parent_replacement_during_read_is_rejected(self):
        self.artifact["path"] = "nested/trace.json"
        self.case["trace"]["artifacts"] = [self.artifact["path"]]
        self.case["assertions"][0]["evidence_refs"][0]["artifact"] = self.artifact["path"]
        self.publish()
        original_read = os.read
        replaced = False

        def replace_parent(fd, count):
            nonlocal replaced
            data = original_read(fd, count)
            if not replaced:
                replaced = True
                (self.root / "nested").rename(self.root / "retained")
                (self.root / "nested").mkdir(mode=0o700)
            return data

        with mock.patch.object(evidence.os, "read", side_effect=replace_parent):
            self.assert_invalid()
        self.assertTrue(replaced)


if __name__ == "__main__":
    unittest.main()
