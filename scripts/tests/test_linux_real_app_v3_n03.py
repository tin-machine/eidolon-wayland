"""N03 fixed-contract and adversarial request-correlation unit probes.

These are NOT actual SSH/Registry integration evidence. CI independently
reopens each real five-variant native receipt and mutates it after the Docker
product run, covering same-owner cleanup and source binding.
"""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_real_app_v3_evidence as evidence
import linux_real_app_v3_n03_oracle as n03


class N03RequestCorrelationTests(unittest.TestCase):
    def setUp(self):
        self.old = {"scope":"a" * 64,"revision":"b" * 64,"app_id":"gtk3-desktop"}
        self.new = {"scope":"c" * 64,"revision":"b" * 64,"app_id":"gtk3-desktop"}

    def fixture(self, variant):
        request, expected, immediate = n03.EXPECTED[variant]
        if variant == "old_catalog":
            phases = [{"capture":{"session":"3"},"selection":self.old},
                      {"capture":{"session":"4"},"selection":self.new}]
            selected = self.old
        else:
            phases = [{"capture":{"session":"4"},"selection":self.old}]
            selected = copy.deepcopy(self.old)
            if variant == "unknown_app_id":
                selected["app_id"] = "malicious-unregistered"
            if variant == "scope_changed":
                selected["scope"] = "c" * 64
            if variant == "revision_changed":
                selected["revision"] = "c" * 64
        rows = {
            "n03_launch_submitted": [(1, {"data":{
                "variant":variant,"request_id":request,"session":"4","selection":selected}})],
            "n03_registry_return": [(2, {"data":{
                "request_id":request,"status":immediate}})],
            "n03_rejection_observed": [(4, {"data":{
                "request_id":request,"status":expected,
                "completion":None if variant == "unknown_app_id" else expected}})],
            "n03_rejection_boundary": [(5, {"data":{
                "request_id":request,
                "app":{"state":"running"} if variant == "double_launch" else None,
                "pending_targets":0,"sdk_bindings":0,"render_targets":[],
                "origin_retired":False,"launch_request":"32" if variant == "double_launch" else None,
                "start_attempted":False,"presentation":"pending"}})],
            "command_complete": [] if variant == "unknown_app_id"
                else [(3, {"data":{"request_id":request,"status":expected}})],
        }
        return rows, phases

    def test_fixed_manifest_counts_and_variants(self):
        manifest = evidence.load_manifest()
        self.assertEqual(len(manifest["cases"]), 87)
        self.assertEqual(len({r["case_id"] for r in manifest["cases"]}), 28)
        self.assertEqual(sum(map(lambda r: len(r["assertions"]), manifest["cases"])), 533)
        self.assertEqual(list(n03.EXPECTED), list(n03.ASSERTIONS))
        self.assertEqual(len(n03.EXPECTED), 5)
        self.assertEqual(sum(map(len, n03.ASSERTIONS.values())), 35)
        self.assertTrue(all(len(a) == 7 for a in n03.ASSERTIONS.values()))
        for case in n03.ASSERTIONS.values():
            self.assertIn("journal_finalized", case)
            self.assertIn("registry_destroy_accepted", case)

    def test_valid_refusal_records_are_distinguished_from_queue_acceptance(self):
        for variant in n03.EXPECTED:
            with self.subTest(variant=variant):
                seen, phases = self.fixture(variant)
                n03.request_and_status({}, seen, phases, variant)

    def test_wrong_parser_status_rejected(self):
        seen, phases = self.fixture("unknown_app_id")
        seen["n03_rejection_observed"][0][1]["data"]["status"] = "NOT_READY"
        with self.assertRaises(ValueError):
            n03.request_and_status({}, seen, phases, "unknown_app_id")

    def test_changed_revision_queue_acceptance_is_not_completion(self):
        for variant in ("scope_changed", "revision_changed", "old_catalog"):
            with self.subTest(variant=variant):
                seen, phases = self.fixture(variant)
                seen["command_complete"][0][1]["data"]["status"] = "ACCEPTED"
                with self.assertRaises(ValueError):
                    n03.request_and_status({}, seen, phases, variant)

    def test_reused_other_request_status_cannot_pass(self):
        seen, phases = self.fixture("scope_changed")
        seen["command_complete"][0][1]["data"]["request_id"] = "90"
        with self.assertRaises(ValueError):
            n03.request_and_status({}, seen, phases, "scope_changed")

    def test_local_allocation_must_remain_absent_for_failed_admission(self):
        for variant in ("unknown_app_id", "scope_changed", "revision_changed", "old_catalog"):
            with self.subTest(variant=variant):
                seen, phases = self.fixture(variant)
                seen["n03_rejection_boundary"][0][1]["data"]["app"] = {"state":"running"}
                with self.assertRaises(ValueError):
                    n03.request_and_status({}, seen, phases, variant)

    def test_old_catalog_is_real_prior_selection(self):
        seen, phases = self.fixture("old_catalog")
        seen["n03_launch_submitted"][0][1]["data"]["selection"] = self.new
        with self.assertRaises(ValueError):
            n03.request_and_status({}, seen, phases, "old_catalog")

    def test_busy_is_second_live_request_not_success_of_first(self):
        seen, phases = self.fixture("double_launch")
        seen["n03_rejection_observed"][0][1]["data"]["status"] = "ACCEPTED"
        with self.assertRaises(ValueError):
            n03.request_and_status({}, seen, phases, "double_launch")
        seen, phases = self.fixture("double_launch")
        seen["n03_launch_submitted"][0][1]["data"]["request_id"] = "32"
        with self.assertRaises(ValueError):
            n03.request_and_status({}, seen, phases, "double_launch")

    def test_external_disposal_is_not_a_native_product_result(self):
        source = {"commit":"1" * 40,"tree_sha256":"2" * 64,
                  "dirty":False,"build_features":[],"pr_head":None}
        report = evidence.incomplete_report(source, run_id="a" * 32)
        self.assertFalse(report["host_v3_acceptance_passed"])
        for case in report["cases"]:
            if case["case_id"] == "N03":
                self.assertEqual(case["status"], "not_run")
                self.assertEqual(case["cleanup"]["environment_disposal"], "not_yet")


if __name__ == "__main__":
    unittest.main()
