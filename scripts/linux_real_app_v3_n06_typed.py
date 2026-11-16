"""Four N06 typed partial I6 receipts, independently revalidated on disk.

An untrusted producer's assertion/pass flags never authorize any formal row.
The formal validator reopens the receipt and reruns the N06 independent oracle.
"""
from __future__ import annotations

import linux_real_app_v3_evidence as evidence
from linux_real_app_v3_n06_oracle import ASSERTIONS_BY_VARIANT, RECEIPT_REVISION, owner_targets


def assemble(source, after, native, environment, artifacts, variant):
    evidence._source(source)
    evidence._source(after)
    evidence.require(source==after and source["dirty"] is False,"I6_SOURCE_MISMATCH")
    evidence.require(variant in ASSERTIONS_BY_VARIANT
                     and native["run_id"]==environment["run_id"]
                     and native["source_sha256"]==environment["source_sha256"]
                     and native["source_sha256"]==source["tree_sha256"],
                     "I6_SOURCE_MISMATCH")
    owner,targets=owner_targets(native)
    run_id=native["run_id"]
    report=evidence.incomplete_report(source,run_id=run_id,reason="I6_NOT_RUN")
    report["source"]["after"]=after
    receipt_name=f"n06-{variant}-receipt.json"
    receipt={"schema":1,"kind":RECEIPT_REVISION,"run_id":run_id,
        "case_id":"N06","variant":variant,"source_sha256":source["tree_sha256"],
        "owner":owner,"targets":targets,"native":native,"environment":environment}
    evidence.write_report(artifacts/receipt_name,receipt)
    raw=evidence.read_report(artifacts/receipt_name)
    evidence.require(len(raw)<=evidence.MAX_TRACE_BYTES,"I6_ARTIFACT_INVALID")
    report["artifacts"].append({
        "path":receipt_name,"size":len(raw),"sha256":evidence.sha(raw),
        "evidence_class":"R","kind":"n06_receipt","run_id":run_id,
        "case_id":"N06","variant":variant,"owner_id":owner["owner_id"],
        "target_id":None,"source_sha256":source["tree_sha256"],
    })
    case=next(c for c in report["cases"]
              if (c["case_id"],c["variant"])==("N06",variant))
    case["status"]="pass"  # independently revalidated, not trusted by itself
    case["reason"]="I6_OK"
    case["owners"]=[owner]
    case["targets"]=targets
    case["observed_product_outcome"]="complete"
    case["cleanup"].update({"product":"complete","fault_resources":"not_needed",
        "fixture_rescue":"not_needed","environment_disposal":"not_yet"})
    def reference(target_id=None):
        return {"artifact":receipt_name,"owner_id":owner["owner_id"],
                "target_id":target_id,"event_seq":None}
    case["assertions"]=[
        {"assertion_id":name,"status":"pass","reason":"I6_OK",
         "evidence_refs":[reference()]} for name in ASSERTIONS_BY_VARIANT[variant]
    ]
    census=[]
    for resource in sorted(evidence.RESOURCES):
        members=targets if resource in evidence.TARGET_RESOURCES else [None]
        for target in members:
            tid=target["target_id"] if target is not None else None
            census.append({"resource":resource,"owner_id":owner["owner_id"],
                "target_id":tid,"state":"complete",
                "evidence_refs":[reference(tid)],"missing_reasons":[]})
    case["cleanup"]["census"]=census
    return report


def write_checked(source,native,environment,artifacts,variant):
    after=evidence.source_snapshot()
    report=assemble(source,after,native,environment,artifacts,variant)
    acceptance=f"n06-acceptance-{variant}.json"
    validation=f"n06-acceptance-{variant}-validation.json"
    checked=evidence.validate_acceptance(evidence.canonical(report),artifacts,source)
    evidence.write_report(artifacts/acceptance,report)
    evidence.write_report(artifacts/validation,checked)
    evidence.require(checked["status"]=="incomplete"
                     and checked["host_v3_acceptance_passed"] is False
                     and checked["exit_code"]==2
                     and checked["verified_case_oracles"]==[f"N06/{variant}"]
                     and any(x.startswith("N07/") for x in checked["missing"]),
                     "I6_N06_RECEIPT_INVALID")
    return checked
