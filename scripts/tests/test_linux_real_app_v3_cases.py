"""Fixed I6 obligations; a successful contract test is not a product run.

The independent expectations below guard the design's negative cases and
unallocated-owner boundaries. No fixture result is used to choose an outcome.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import linux_real_app_v3_evidence as evidence


FIXTURE = ROOT / "scripts/fixtures/linux-desktop-v3-acceptance/cases-v1.json"


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


class FixedCaseManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = FIXTURE.read_bytes()
        cls.manifest = evidence.load_manifest(cls.raw)
        cls.cases = cls.manifest["cases"]
        cls.rows = {(row["case_id"], row["variant"]): row for row in cls.cases}

    def assert_rejected(self, value):
        with self.assertRaises(ValueError):
            evidence.load_manifest(encoded(value))

    def test_default_load_and_digest_are_pinned_to_reviewed_manifest_bytes(self):
        self.assertEqual(evidence.load_manifest(), self.manifest)
        self.assertEqual(evidence.manifest_digest(), hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(self.manifest["schema"], 1)
        self.assertEqual(self.manifest["profile"], "i6-host-v1")
        self.assertEqual(self.manifest["revision"], "eidolon-i6-host-acceptance-v1")
        self.assertEqual(self.manifest["verification_ids"], ["I3-T20", "V11"])

    def test_all_28_families_and_each_variant_remain_required(self):
        families = {f"N{i:02d}" for i in range(1, 12)}
        families |= {f"F{i:02d}" for i in range(1, 13)}
        families |= {f"C{i:02d}" for i in range(1, 6)}
        self.assertEqual({row["case_id"] for row in self.cases}, families)
        self.assertEqual(len(self.cases), 87)
        self.assertEqual(len(self.rows), len(self.cases))
        for family in families:
            with self.subTest(family=family):
                candidate = copy.deepcopy(self.manifest)
                candidate["cases"] = [row for row in candidate["cases"] if row["case_id"] != family]
                self.assert_rejected(candidate)
        for index, row in enumerate(self.cases):
            with self.subTest(case=row["case_id"], variant=row["variant"]):
                candidate = copy.deepcopy(self.manifest)
                del candidate["cases"][index]
                self.assert_rejected(candidate)

    def test_each_required_assertion_cannot_be_removed(self):
        self.assertEqual(sum(len(row["assertions"]) for row in self.cases), 533)
        for index, row in enumerate(self.cases):
            self.assertEqual(len(row["assertions"]), len(set(row["assertions"])))
            for offset, assertion in enumerate(row["assertions"]):
                with self.subTest(case=row["case_id"], variant=row["variant"], assertion=assertion):
                    candidate = copy.deepcopy(self.manifest)
                    del candidate["cases"][index]["assertions"][offset]
                    self.assert_rejected(candidate)

    def test_no_runtime_optional_or_required_switch_exists(self):
        for row in self.cases:
            self.assertNotIn("required", row)
            self.assertNotIn("optional", row)
        for key, value in (("required", False), ("optional", True), ("status", "pass")):
            with self.subTest(key=key):
                candidate = copy.deepcopy(self.manifest)
                candidate["cases"][0][key] = value
                self.assert_rejected(candidate)

    def test_duplicate_unknown_empty_and_mixed_version_manifest_rejected(self):
        candidates = []
        duplicate = copy.deepcopy(self.manifest)
        duplicate["cases"].append(copy.deepcopy(duplicate["cases"][0]))
        candidates.append(duplicate)
        for key, value in (("profile", "i6-real-reboot-v1"), ("schema", True), ("revision", "v2")):
            candidate = copy.deepcopy(self.manifest)
            candidate[key] = value
            candidates.append(candidate)
        unknown = copy.deepcopy(self.manifest)
        unknown["cases"][0]["case_id"] = "N99"
        candidates.append(unknown)
        empty = copy.deepcopy(self.manifest)
        empty["cases"][0]["assertions"] = []
        candidates.append(empty)
        for candidate in candidates:
            with self.subTest(candidate=candidate.get("profile")):
                self.assert_rejected(candidate)
        for raw in (
            self.raw.replace(b'"schema": 1', b'"schema": 1, "schema": 1', 1),
            self.raw + b"{}",
            self.raw.replace(b'"schema": 1', b'"schema": NaN', 1),
            b"\xff" + self.raw,
        ):
            with self.subTest(raw_prefix=raw[:24]):
                with self.assertRaises(ValueError):
                    evidence.load_manifest(raw)

    def test_f11_cancel_outcomes_are_fixed_before_any_execution(self):
        variants = {"before_session", "catalog_wait", "launch_pending", "after_ready"}
        self.assertEqual({v for c, v in self.rows if c == "F11"}, variants)
        for variant in variants:
            row = self.rows["F11", variant]
            self.assertEqual(row["expected_product_outcome"], "complete")
            self.assertEqual(row["cleanup_policy"], "product_complete")
            self.assertTrue({"cancel_original_request_complete", "start_count_at_most_one",
                             "boundary_ownership_preserved", "late_events_fenced"} <= set(row["assertions"]))

    def test_f12_pending_then_repair_is_not_unconditional_complete(self):
        expected = {
            "callback_hold_repaired": "complete",
            "dispose_failure_repaired": "complete",
            "old_scene_delete_missing": "retained_pending",
        }
        self.assertEqual({v for c, v in self.rows if c == "F12"}, set(expected))
        for variant, outcome in expected.items():
            row = self.rows["F12", variant]
            self.assertEqual(row["expected_product_outcome"], outcome)
            self.assertIn("exact_lease_pending", row["assertions"])
            if outcome == "complete":
                self.assertIn("same_lease_retired", row["assertions"])
                self.assertIn("real_renderer_fence", row["assertions"])
            else:
                self.assertIn("new_scene_not_old_receipt", row["assertions"])
                self.assertIn("empty_ledger_no_success", row["assertions"])
                self.assertIn("timeout_no_success", row["assertions"])
        for index, row in enumerate(self.cases):
            if row["case_id"] in {"F11", "F12"}:
                candidate = copy.deepcopy(self.manifest)
                candidate["cases"][index]["expected_product_outcome"] = (
                    "retained_pending" if row["expected_product_outcome"] == "complete" else "complete")
                self.assert_rejected(candidate)

    def test_retained_pending_cases_cannot_be_promoted_by_rescue(self):
        pending = {key for key, row in self.rows.items() if row["expected_product_outcome"] == "retained_pending"}
        self.assertEqual(pending, {("F05", "write_end_held"), ("F07", "helper_owner_lost"),
                                   ("F10", "host_process_restart"), ("F12", "old_scene_delete_missing")})
        for key in pending:
            row = self.rows[key]
            self.assertEqual(row["cleanup_policy"], "preserve_unknown_then_rescue")
            self.assertTrue({"unknown_pending_preserved", "journal_retained", "save_launch_denied",
                             "rescue_after_result_fixed", "fault_resources_released"} <= set(row["assertions"]))
            self.assertNotIn("product_census_complete", row["assertions"])
            self.assertNotIn("registry_destroy_accepted", row["assertions"])

    def test_n03_rejections_bind_the_specific_parser_or_gate_status(self):
        expected = {"unknown_app_id": "invalid_argument", "scope_changed": "not_ready",
                    "revision_changed": "not_ready", "old_catalog": "not_ready", "double_launch": "busy"}
        self.assertEqual({v for c, v in self.rows if c == "N03"}, set(expected))
        for variant, status in expected.items():
            assertions = set(self.rows["N03", variant]["assertions"])
            self.assertEqual(assertions & {"invalid_argument", "not_ready", "busy"}, {status})
            self.assertTrue({"start_count_at_most_one", "rejected_launch_no_spawn"} <= assertions)

    def test_unallocated_and_legacy_owners_do_not_require_invented_v3_finalizers(self):
        before = set(self.rows["F11", "before_session"]["assertions"])
        self.assertTrue({"no_journal_allocation_sealed", "no_origin_allocation_sealed_before_stop"} <= before)
        self.assertNotIn("journal_finalized", before)
        self.assertNotIn("origin_retirement_before_runtime_stop", before)
        no_local = [("N01", "inspect_only"), ("F11", "catalog_wait"),
                    ("F08", "journal_publish"), ("F08", "rename_dir_fsync")]
        no_local += [("N03", variant) for variant in ("unknown_app_id", "scope_changed", "revision_changed", "old_catalog")]
        no_local += [("N09", variant) for variant in ("session_candidate", "autosave_candidate")]
        for key in no_local:
            assertions = set(self.rows[key]["assertions"])
            self.assertIn("no_local_allocation_sealed_before_stop", assertions)
            self.assertNotIn("origin_retirement_before_runtime_stop", assertions)
        legacy = set(self.rows["N11", "frozen_v2_recover"]["assertions"])
        self.assertTrue({"legacy_journal_finalized", "legacy_cleanup_before_runtime_stop"} <= legacy)
        self.assertNotIn("origin_retirement_before_runtime_stop", legacy)

    def test_flood_writer_release_and_held_fd_have_different_order(self):
        for variant in ("low_rate_stop", "stdout_limit", "stderr_limit"):
            row = self.rows["F05", variant]
            self.assertEqual(row["expected_product_outcome"], "complete")
            self.assertIn("writer_fds_closed_before_terminal", row["assertions"])
            self.assertIn("shared_fd_flags_unchanged", row["assertions"])
        held = self.rows["F05", "write_end_held"]
        self.assertIn("product_result_before_fd_release", held["assertions"])
        self.assertIn("eof_missing_observed", held["assertions"])
        self.assertNotIn("writer_fds_closed_before_terminal", held["assertions"])

    def test_contract_variants_cover_each_design_boundary(self):
        expected = {
            "C01": {"config", "storage", "catalog", "mixed_version", "byte_limits"},
            "C02": {"deadline_exact", "heartbeat_inspect_race", "heartbeat_launch_race",
                    "heartbeat_stop_race", "heartbeat_eof_race"},
            "C03": {"boot_changed", "boot_same", "receipt_contradiction"},
            "C04": {"wrong_runtime", "wrong_capture", "stale_ack", "stale_generation",
                    "stale_input_epoch", "stale_focus_token"},
            "C05": {"malformed_report", "duplicate_key", "unknown_key", "nonfinite_number",
                    "invalid_utf8", "bool_as_int", "trailing_bytes", "report_size_limit",
                    "missing_case", "missing_variant", "missing_assertion", "missing_artifact",
                    "artifact_digest_mismatch", "artifact_path_escape", "cross_run", "cross_capture",
                    "cross_source", "wrong_owner", "wrong_target_generation", "trace_overflow",
                    "cleanup_evidence_missing", "first_failure_preserved"},
        }
        for family, variants in expected.items():
            self.assertEqual({v for c, v in self.rows if c == family}, variants)
            for variant in variants:
                row = self.rows[family, variant]
                self.assertEqual(row["expected_product_outcome"], "not_applicable")
                self.assertEqual(row["cleanup_policy"], "contract_only")
                self.assertEqual(row["evidence_classes"], ["T"])
        for variant in expected["C04"]:
            self.assertTrue({"actual_gate_reached", "window_not_admitted", "input_no_effect", "file_unchanged"}
                            <= set(self.rows["C04", variant]["assertions"]))
        for variant in expected["C03"]:
            self.assertIn("synthetic_boot_declared", self.rows["C03", variant]["assertions"])
            self.assertIn("pipes_eof_not_forged", self.rows["C03", variant]["assertions"])

    def test_real_frames_and_independent_gui_oracles_remain_obligations(self):
        for variant in ("gtk3_desktop", "mousepad_editor"):
            row = self.rows["N02", variant]
            self.assertEqual(row["evidence_classes"], ["R", "S"])
            self.assertTrue({"real_content_frame", "b1_ready", "same_actor_catalog"} <= set(row["assertions"]))
        self.assertTrue({"input_exact_bytes", "output_exact_bytes", "input_unchanged", "file_owner_inode_checked"}
                        <= set(self.rows["N05", "open_edit_save_as"]["assertions"]))
        self.assertTrue({"first_gui_off", "first_key_absent", "first_gui_on", "first_key_true",
                         "second_gui_on", "second_key_true", "same_storage_new_nonce", "both_sessions_complete"}
                        <= set(self.rows["N07", "same_storage_restart"]["assertions"]))


if __name__ == "__main__":
    unittest.main()
