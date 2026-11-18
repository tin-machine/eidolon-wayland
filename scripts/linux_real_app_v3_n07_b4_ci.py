"""Export fixed B4 diagnostics and independently audit the saved two sessions.

Docker exit 0 and producer status cannot satisfy this gate. Export and audit
retain each readable partial receipt even if another file/session is missing.
Neither this gate nor a candidate digest supplies Golden approval.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import sys

import linux_real_app_v3_evidence as evidence
from linux_real_app_v3_n07_b3_ci import read_bounded
from linux_real_app_v3_n07_b4 import fixed_reason

FILES = {
    "n07-b4-validation.json": 1024 * 1024,
    "n07-b4-native.json": 1024 * 1024,
    "n07-b4-supervisor.json": 1024 * 1024,
    **{session + "-" + role + ".json": (4 if role == "context" else 1) * 1024 * 1024
       for session in ("first", "second")
       for role in ("native", "environment", "fingerprint", "context", "fingerprint-diagnostic")},
}


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


def export_and_validate(incoming, output, image_file, head, attached, runtime,
                        running, source_root, pr_head, ci=None):
    ci = os.environ if ci is None else ci
    report = {"schema": 1, "revision": "eidolon-i6-n07-b4-export-v1",
              "status": "rejected", "reason": "N07_B4_INPUT_MISSING",
              "candidate_created": False, "saved_receipts_revalidated": False,
              "rejected_diagnostic_created": False,
              "rejected_diagnostic_revalidated": False,
              "reviewed_golden": False, "menu_identity_verified": False,
              "verified_n07_assertions": [], "host_v3_acceptance_passed": False,
              "observations": {}, "export_errors": []}
    inputs, provenance = None, None
    try:
        image_id = image_file.read_text(encoding="ascii").strip()
        need(re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is not None
             and re.fullmatch(r"[0-9a-f]{40}", head) is not None
             and (not pr_head or re.fullmatch(r"[0-9a-f]{40}", pr_head) is not None)
             and attached in ("0", "1", "2", "124", "125", "130", "137", "143")
             and runtime.isdecimal() and running in ("true", "false"),
             "N07_B4_CONTAINER_METADATA")
        container = {"schema": 1, "source_commit": head, "pr_head_sha": pr_head or None,
            "image_id": image_id, "attach_exit": int(attached), "container_exit": int(runtime),
            "external_rescue": running == "true", "network": "none", "host_mounts": False,
            "formal_i6_passed": False, "n07_assertions_verified": []}
        evidence.write_report(output / "container.json", container)
        for name, limit in FILES.items():
            try:
                raw, _ = read_bounded(incoming / name, limit)
                with (output / name).open("xb") as stream:
                    stream.write(raw)
                (output / name).chmod(0o600)
                report["observations"][name] = {"bytes": len(raw), "sha256": evidence.sha(raw)}
            except FileNotFoundError:
                report["observations"][name] = {"status": "missing"}
            except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
                report["observations"][name] = {"status": "rejected", "reason": fixed_reason(error)}
                report["export_errors"].append(name)
        source = evidence.source_snapshot(source_root)
        need(source["dirty"] is False and source["commit"] == head, "I6_SOURCE_MISMATCH")
        need(not report["export_errors"], "N07_B4_EXPORT_INVALID")
        from linux_real_app_v3_n07_b4_probe import load_collection, validate_collection, adversarial_checks
        from linux_real_app_v3_n07_b4_review import export_bundle, validate_bundle
        inputs = load_collection(output)
        need(evidence.canonical(inputs["source"]) == evidence.canonical(source), "I6_SOURCE_MISMATCH")
        provenance = {
            "repository": "tin-machine/eidolon-wayland", "workflow_run_id": ci.get("GITHUB_RUN_ID") or None,
            "run_attempt": ci.get("GITHUB_RUN_ATTEMPT") or None,
            "artifact_id": None, "artifact_name": "linux-desktop-v3-n07-b4-" + head,
            "artifact_archive_sha256": None, "artifact_status": "pending_upload",
            "source_commit": head, "pr_head": pr_head or None,
        }
        # Inspect the product and independent observations before requiring
        # process success. The latter cannot replace these actual predicates.
        result = validate_collection(**inputs)
        report["collection"] = result
        report["saved_receipts_revalidated"] = True
        need(attached == "0" and runtime == "0" and running == "false",
             "N07_B4_EXTERNAL_RESCUE_OR_EXIT")
        _, producer = read_bounded(output / "n07-b4-validation.json", 1024 * 1024)
        need(producer.get("host_v3_acceptance_passed") is False
             and producer.get("verified_n07_assertions") == []
             and producer.get("reviewed_golden") is False
             and producer.get("menu_identity_verified") is False
             and producer.get("exit_code") == 0
             and producer.get("status") == "collected", "N07_B4_PRODUCER_DISPOSITION")
        adversarial_checks(**inputs)
        candidate = export_bundle(output / "n07-b4-review", **inputs, provenance=provenance)
        validate_bundle(output / "n07-b4-review", candidate["candidate_sha256"])
        report.update(status="collected", reason="N07_B4_UNAPPROVED_COLLECTION",
                      candidate_created=True, candidate=candidate,
                      mutations_rejected=True)
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        report["reason"] = fixed_reason(error)
        if inputs is not None and provenance is not None:
            try:
                from linux_real_app_v3_n07_b4_review import (
                    export_rejected_bundle, validate_rejected_bundle, adversarial_rejected_checks)
                root = output / "n07-b4-rejected-review"
                diagnostic = export_rejected_bundle(root, **inputs, provenance=provenance)
                report.update(rejected_diagnostic_created=True, rejected_diagnostic=diagnostic)
                validate_rejected_bundle(root, diagnostic["diagnostic_sha256"])
                mutations = adversarial_rejected_checks(**inputs, provenance=provenance)
                report.update(rejected_diagnostic_revalidated=True,
                              rejected_diagnostic_mutations=mutations)
            except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as diagnostic_error:
                report["rejected_diagnostic_error"] = fixed_reason(diagnostic_error)
        # A readable, independently checked rejected packet never changes the
        # full collection status, process exit, candidate flag or Golden gate.
    evidence.write_report(output / "n07-b4-export.json", report)
    return report["status"] == "collected"


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 9:
        print("N07-B4 exporter requires 9 arguments", file=sys.stderr)
        return 2
    ok = export_and_validate(Path(args[0]), Path(args[1]), Path(args[2]),
        *args[3:7], Path(args[7]), args[8])
    print("N07-B4 saved two-session collection " + ("revalidated" if ok else "rejected") +
          "; Golden unapproved, N07 0/13, formal I6 incomplete")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
