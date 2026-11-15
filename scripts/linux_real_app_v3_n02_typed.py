"""N02 -> fixed I6 acceptance manifest, without inventing any other case.

This producer writes a typed *candidate* from the ordinary native/SSH/SDK
observations. Only linux_real_app_v3_evidence independently reopens it and
grants case assertions. One acceptance report per real native run_id preserves
the frozen run/owner/source identity (GTK and Mousepad have distinct runs).
"""
from __future__ import annotations

import linux_real_app_v3_evidence as evidence
from linux_real_app_v3_n02_oracle import (
    ASSERTIONS, RECEIPT_REVISION, case_owner_target,
)


def assemble(source, after, native, environment, variant, artifacts):
    """Write and independently validate exactly one N02 typed case.

    The I6 manifest stays 28 families / 87 variants / 533 assertions. Other
    cases remain not_run; environment disposal is an external Docker owner's
    separate responsibility, never a native product-acceptance flag.
    """
    evidence.require(variant in {"gtk3_desktop", "mousepad_editor"})
    evidence._source(source)
    evidence._source(after)
    evidence.require(source == after and not source["dirty"], "I6_SOURCE_MISMATCH")
    evidence.require(
        native["run_id"] == environment["run_id"]
        and native["source_sha256"] == environment["source_sha256"]
        and native["source_sha256"] == source["tree_sha256"],
        "I6_SOURCE_MISMATCH",
    )
    owner, target = case_owner_target(native)
    run_id = native["run_id"]
    report = evidence.incomplete_report(source, run_id=run_id, reason="I6_NOT_RUN")
    report["source"]["after"] = after
    filename = f"n02-{variant}-receipt.json"
    receipt = {
        "schema": 1, "kind": RECEIPT_REVISION, "run_id": run_id,
        "case_id": "N02", "variant": variant,
        "source_sha256": source["tree_sha256"], "owner": owner,
        "target": target, "native": native, "environment": environment,
    }
    evidence.write_report(artifacts / filename, receipt)
    raw = evidence.read_report(artifacts / filename)
    evidence.require(len(raw) <= evidence.MAX_TRACE_BYTES, "I6_ARTIFACT_INVALID")
    artifact = {
        "path": filename, "size": len(raw), "sha256": evidence.sha(raw),
        "evidence_class": "R", "kind": "n02_receipt",
        "run_id": run_id, "case_id": "N02", "variant": variant,
        "owner_id": owner["owner_id"], "target_id": None,
        "source_sha256": source["tree_sha256"],
    }
    report["artifacts"].append(artifact)
    case = next(row for row in report["cases"]
                if (row["case_id"], row["variant"]) == ("N02", variant))
    case["status"] = "pass"  # an untrusted claim until independent validation
    case["reason"] = "I6_OK"
    case["owners"] = [owner]
    case["targets"] = [target]
    case["observed_product_outcome"] = "complete"
    case["cleanup"].update({
        "product": "complete", "fault_resources": "not_needed",
        "fixture_rescue": "not_needed", "environment_disposal": "not_yet",
    })
    def reference(target_id=None):
        return {"artifact": filename, "owner_id": owner["owner_id"],
                "target_id": target_id, "event_seq": None}
    case["assertions"] = [
        {"assertion_id": name, "status": "pass", "reason": "I6_OK",
         "evidence_refs": [reference()]}
        for name in ASSERTIONS
    ]
    case["cleanup"]["census"] = [
        {"resource": name, "owner_id": owner["owner_id"],
         "target_id": target["target_id"] if name in evidence.TARGET_RESOURCES else None,
         "state": "complete", "evidence_refs": [
             reference(target["target_id"] if name in evidence.TARGET_RESOURCES else None)
         ], "missing_reasons": []}
        for name in sorted(evidence.RESOURCES)
    ]
    return report


def write_checked(source, native, environment, variant, artifacts):
    after = evidence.source_snapshot()
    report = assemble(source, after, native, environment, variant, artifacts)
    acceptance = f"acceptance-{variant}.json"
    validation = f"acceptance-{variant}-validation.json"
    result = evidence.validate_acceptance(evidence.canonical(report), artifacts, source)
    # Do not normalize an invalid/missing reference or an incomplete N02
    # result to success, even if the standalone producer oracle passed.
    expected = f"N02/{variant}"
    evidence.require(
        result["status"] == "incomplete"
        and result["host_v3_acceptance_passed"] is False
        and result["exit_code"] == 2
        and result["verified_case_oracles"] == [expected]
        and result["unverified_assertions"] > 0,
        "I6_N02_RECEIPT_INVALID",
    )
    evidence.write_report(artifacts / acceptance, report)
    evidence.write_report(artifacts / validation, result)
    return result
