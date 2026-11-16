"""N05 typed partial I6 receipt. Its producer assertions carry zero authority.

The formal validator independently reopens the saved native R/S evidence and
one supervisor monotonic observation, then recomputes all ten frozen assertions.
"""
from __future__ import annotations

import linux_real_app_v3_evidence as evidence
from linux_real_app_v3_n05_oracle import ASSERTIONS, RECEIPT_REVISION, owner_targets


def assemble(source, after, native, environment, artifacts):
    evidence._source(source)
    evidence._source(after)
    evidence.require(source == after and source["dirty"] is False,
                     "I6_SOURCE_MISMATCH")
    evidence.require(native["run_id"] == environment["run_id"]
                     and native["source_sha256"] == environment["source_sha256"]
                     and native["source_sha256"] == source["tree_sha256"],
                     "I6_SOURCE_MISMATCH")
    owner, targets = owner_targets(native)
    run_id = native["run_id"]
    report = evidence.incomplete_report(source, run_id=run_id, reason="I6_NOT_RUN")
    report["source"]["after"] = after
    receipt_name = "n05-open_edit_save_as-receipt.json"
    receipt = {"schema":1, "kind":RECEIPT_REVISION, "run_id":run_id,
               "case_id":"N05", "variant":"open_edit_save_as", "source_sha256":source["tree_sha256"],
               "owner":owner, "targets":targets, "native":native, "environment":environment}
    evidence.write_report(artifacts / receipt_name, receipt)
    raw = evidence.read_report(artifacts / receipt_name)
    evidence.require(len(raw) <= evidence.MAX_TRACE_BYTES, "I6_ARTIFACT_INVALID")
    report["artifacts"].append({
        "path":receipt_name, "size":len(raw), "sha256":evidence.sha(raw),
        "evidence_class":"R", "kind":"n05_receipt", "run_id":run_id,
        "case_id":"N05", "variant":"open_edit_save_as", "owner_id":owner["owner_id"],
        "target_id":None, "source_sha256":source["tree_sha256"],
    })
    case = next(c for c in report["cases"]
                if (c["case_id"], c["variant"]) == ("N05","open_edit_save_as"))
    case["status"] = "pass"  # untrusted until formal independent validation
    case["reason"] = "I6_OK"
    case["owners"] = [owner]
    case["targets"] = targets
    case["observed_product_outcome"] = "complete"
    case["cleanup"].update({
        "product":"complete", "fault_resources":"not_needed",
        "fixture_rescue":"not_needed", "environment_disposal":"not_yet",
    })
    def reference(target_id=None):
        return {"artifact":receipt_name, "owner_id":owner["owner_id"],
                "target_id":target_id, "event_seq":None}
    case["assertions"] = [
        {"assertion_id":name, "status":"pass", "reason":"I6_OK",
         "evidence_refs":[reference()]} for name in ASSERTIONS
    ]
    census = []
    for resource in sorted(evidence.RESOURCES):
        owned = targets if resource in evidence.TARGET_RESOURCES else [None]
        for target in owned:
            target_id = target["target_id"] if target is not None else None
            census.append({
                "resource":resource, "owner_id":owner["owner_id"],
                "target_id":target_id, "state":"complete",
                "evidence_refs":[reference(target_id)], "missing_reasons":[],
            })
    case["cleanup"]["census"] = census
    return report


def write_checked(source, native, environment, artifacts):
    after = evidence.source_snapshot()
    report = assemble(source, after, native, environment, artifacts)
    acceptance = "n05-acceptance-open_edit_save_as.json"
    validation = "n05-acceptance-open_edit_save_as-validation.json"
    checked = evidence.validate_acceptance(evidence.canonical(report), artifacts, source)
    evidence.write_report(artifacts / acceptance, report)
    evidence.write_report(artifacts / validation, checked)
    evidence.require(checked["status"] == "incomplete"
                     and checked["host_v3_acceptance_passed"] is False
                     and checked["exit_code"] == 2
                     and checked["verified_case_oracles"] == ["N05/open_edit_save_as"]
                     and any(x.startswith("N06/") for x in checked["missing"]),
                     "I6_N05_RECEIPT_INVALID")
    return checked
