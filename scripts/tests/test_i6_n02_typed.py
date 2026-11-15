"""Typed N02 -> I6 acceptance wiring tests. Synthetic fixture is NOT Host proof.

Only the actual disposable N02 CI job runs unmocked R/S/T oracle. These unit
tests isolate outer manifest/receipt joins against spoofed producer metadata.
"""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import linux_real_app_v3_evidence as evidence
import linux_real_app_v3_n02_oracle as oracle
import linux_real_app_v3_n02_typed as typed


class TypedN02Tests(unittest.TestCase):
    def setUp(self):
        self.source = {
            "commit": "1" * 40,
            "tree_sha256": "2" * 64,
            "dirty": False,
            "build_features": [],
            "pr_head": None,
        }
        self.run_id = "3" * 32
        self.owner_id = "4" * 32
        self.key = {
            "session": "1", "window": "1",
            "scene": "1", "generation": "1",
        }
        capture = {
            "session": "1", "inspect_request": "31",
            "io_generation": "1", "nonce": "0123456789abcdef",
            "scope": "5" * 64, "context_digest": "5" * 64,
            "capture_id": "6" * 64,
        }
        self.native = {
            "run_id": self.run_id, "source_sha256": self.source["tree_sha256"],
            "owner_id": self.owner_id, "runtime": "1",
            "trace": {"events": [{"capture": capture}]},
            "observations": [{
                "kind": "command_accepted",
                "data": {"request_id": "30", "command": "connect"},
            }],
            "sdk_observations": [{"adapter": "7"}],
            "readback": {"target": self.key, "create": "1"},
        }
        self.environment = {
            "run_id": self.run_id, "owner_id": self.owner_id,
            "source_sha256": self.source["tree_sha256"],
        }
        self.good_oracle = {
            "status": "pass", "verified_assertions": list(oracle.ASSERTIONS),
            "host_v3_acceptance_passed": False,
            "environment_disposal": "not_yet",
        }

    def candidate(self, root):
        return typed.assemble(
            self.source, self.source, self.native, self.environment,
            "gtk3_desktop", root,
        )

    def test_run_target_and_owner_are_derived_from_native_not_claims(self):
        owner, target = oracle.case_owner_target(self.native)
        self.assertEqual(owner["connect_request"], "30")
        self.assertEqual(owner["launch_request"], "32")
        self.assertEqual(owner["native_origin"], "32")
        self.assertEqual(owner["capture_sha256"], "6" * 64)
        self.assertEqual(target["session"], owner["session"])
        self.assertEqual(target["adapter_id"], "7")
        self.assertEqual(target["create_seq"], "1")
        self.assertEqual(len(target["target_id"]), 32)
        changed = copy.deepcopy(self.native)
        changed["sdk_observations"][0]["adapter"] = "8"
        self.assertNotEqual(target["target_id"], oracle.case_owner_target(changed)[1]["target_id"])
        changed["readback"]["target"]["window"] = "2"
        self.assertNotEqual(target["target_id"], oracle.case_owner_target(changed)[1]["target_id"])

    def test_manifest_only_this_n02_case_verifies_with_a_patched_oracle(self):
        # Patch ONLY the resource-level oracle to exercise the schema wiring.
        # The real CI runs the oracle unmocked for GTK and Mousepad.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self.candidate(root)
            with patch.object(oracle, "verify", return_value=self.good_oracle) as independent:
                checked = evidence.validate_acceptance(evidence.canonical(report), root, self.source)
            self.assertEqual(checked["status"], "incomplete")
            self.assertEqual(checked["exit_code"], 2)
            self.assertFalse(checked["host_v3_acceptance_passed"])
            self.assertEqual(checked["verified_case_oracles"], ["N02/gtk3_desktop"])
            self.assertTrue(checked["unverified_assertions"] > 0)
            self.assertEqual(independent.call_count, 1)
            self.assertEqual(report["manifest_sha256"], evidence.MANIFEST_SHA256)
            self.assertEqual(len(report["cases"]), 87)
            self.assertEqual(sum(case["status"] == "pass" for case in report["cases"]), 1)
            self.assertEqual(sum(case["status"] == "not_run" for case in report["cases"]), 86)

    def test_producer_pass_never_substitutes_unavailable_independent_oracle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self.candidate(root)
            # The synthetic native fixture intentionally lacks real S/R/T facts.
            checked = evidence.validate_acceptance(evidence.canonical(report), root, self.source)
            self.assertEqual(checked["status"], "invalid")
            self.assertFalse(checked["verified_case_oracles"])
            self.assertFalse(checked["host_v3_acceptance_passed"])

    def test_cross_owner_target_source_and_artifact_mutations_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self.candidate(root)
            mutations = {
                "different_owner": lambda r: r["cases"][1]["owners"][0].update(
                    runtime="99"),
                "wrong_target": lambda r: next(c for c in r["cases"]
                    if (c["case_id"],c["variant"]) == ("N02","gtk3_desktop")
                    )["targets"][0].update(adapter_id="9"),
                "tampered_bytes": lambda r: r["artifacts"][0].update(sha256="0" * 64),
                "wrong_run_id": lambda r: r.update(run_id="9" * 32),
                "different_source": lambda r: r["artifacts"][0].update(
                    source_sha256="e" * 64),
            }
            for name, mutate in mutations.items():
                with self.subTest(name=name):
                    altered = copy.deepcopy(report)
                    mutate(altered)
                    with patch.object(oracle, "verify", return_value=self.good_oracle):
                        result = evidence.validate_acceptance(
                            evidence.canonical(altered), root, self.source)
                    self.assertEqual(result["status"], "invalid", name)
                    self.assertFalse(result["verified_case_oracles"], name)

    def test_no_receipt_means_no_verified_case_even_if_producer_claims_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self.candidate(root)
            (root / "n02-gtk3_desktop-receipt.json").unlink()
            with patch.object(oracle, "verify", return_value=self.good_oracle) as independent:
                checked = evidence.validate_acceptance(
                    evidence.canonical(report), root, self.source)
            self.assertEqual(checked["status"], "incomplete")
            self.assertEqual(checked["verified_case_oracles"], [])
            independent.assert_not_called()


if __name__ == "__main__":
    unittest.main()
