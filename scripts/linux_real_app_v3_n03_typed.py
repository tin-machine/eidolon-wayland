"""One independently checkable fixed N03 partial-I6 receipt per actual run.

The N03 producer never grants I6 acceptance: linux_real_app_v3_evidence
must reopen the raw native + independent environment receipts, derive the
owner/target again, and recompute all seven fixed assertions.
"""
from __future__ import annotations

import linux_real_app_v3_evidence as evidence
from linux_real_app_v3_n03_oracle import (
    ASSERTIONS, EXPECTED, RECEIPT_REVISION, owner_target, verify_receipt,
)
from linux_real_app_v3_n01_oracle import UNALLOCATED_RESOURCES


def assemble(source, after, native, environment, variant, artifacts):
    evidence.require(variant in EXPECTED and native["variant"] == variant)
    evidence._source(source)
    evidence._source(after)
    evidence.require(source == after and not source["dirty"], "I6_SOURCE_MISMATCH")
    evidence.require(
        native["run_id"] == environment["run_id"]
        and native["source_sha256"] == environment["source_sha256"]
        and native["source_sha256"] == source["tree_sha256"],
        "I6_SOURCE_MISMATCH",
    )
    owner, target = owner_target(native)
    run_id = native["run_id"]
    report = evidence.incomplete_report(source, run_id=run_id, reason="I6_NOT_RUN")
    report["source"]["after"] = after
    filename = f"n03-{variant}-receipt.json"
    receipt = {
        "schema":1,"kind":RECEIPT_REVISION,"run_id":run_id,
        "case_id":"N03","variant":variant,
        "source_sha256":source["tree_sha256"],
        "owner":owner,"target":target,"native":native,"environment":environment,
    }
    evidence.write_report(artifacts / filename, receipt)
    raw = evidence.read_report(artifacts / filename)
    evidence.require(len(raw) <= evidence.MAX_TRACE_BYTES, "I6_ARTIFACT_INVALID")
    report["artifacts"].append({
        "path":filename,"size":len(raw),"sha256":evidence.sha(raw),
        "evidence_class":"R","kind":"n03_receipt",
        "run_id":run_id,"case_id":"N03","variant":variant,
        "owner_id":owner["owner_id"],"target_id":None,
        "source_sha256":source["tree_sha256"],
    })
    case = next(row for row in report["cases"]
                if (row["case_id"], row["variant"]) == ("N03", variant))
    case["status"] = "pass"  # intentionally untrusted until independent revalidation
    case["reason"] = "I6_OK"
    case["owners"] = [owner]
    case["targets"] = [target] if target is not None else []
    case["observed_product_outcome"] = "complete"
    case["cleanup"].update({
        "product":"complete","fault_resources":"not_needed",
        "fixture_rescue":"not_needed","environment_disposal":"not_yet",
    })

    def reference(target_id=None):
        return {"artifact":filename,"owner_id":owner["owner_id"],
                "target_id":target_id,"event_seq":None}

    case["assertions"] = [
        {"assertion_id":name,"status":"pass","reason":"I6_OK",
         "evidence_refs":[reference()]}
        for name in ASSERTIONS[variant]
    ]
    case["cleanup"]["census"] = []
    for resource in sorted(evidence.RESOURCES):
        state = ("complete" if target is not None
                 or resource not in UNALLOCATED_RESOURCES else "not_created")
        target_id = (target["target_id"] if target is not None
                     and resource in evidence.TARGET_RESOURCES else None)
        case["cleanup"]["census"].append({
            "resource":resource,"owner_id":owner["owner_id"],
            "target_id":target_id,"state":state,
            "evidence_refs":[reference(target_id)],
            "missing_reasons":[],
        })
    return report


def write_checked(source, native, environment, variant, artifacts):
    after = evidence.source_snapshot()
    report = assemble(source, after, native, environment, variant, artifacts)
    acceptance_name = f"n03-acceptance-{variant}.json"
    validation_name = f"n03-acceptance-{variant}-validation.json"
    checked = evidence.validate_acceptance(evidence.canonical(report), artifacts, source)
    evidence.write_report(artifacts / acceptance_name, report)
    evidence.write_report(artifacts / validation_name, checked)
    evidence.require(
        checked["status"] == "incomplete"
        and checked["host_v3_acceptance_passed"] is False
        and checked["exit_code"] == 2
        and checked["verified_case_oracles"] == [f"N03/{variant}"]
        and any(m.startswith("N04/") for m in checked["missing"]),
        "I6_N03_RECEIPT_INVALID",
    )
    return checked
