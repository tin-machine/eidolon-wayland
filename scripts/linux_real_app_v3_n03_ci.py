"""Fixed-name N03 Docker exporter; independently reverify all saved evidence.

The outer Docker observer is not the product cleanup owner and never upgrades
the formal I6 status. A missing artifact, mutable source, altered status, or
incomplete product retirement is a failed CI job, not skip-success.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import stat
import sys

import linux_real_app_v3_evidence as evidence
from linux_real_app_v3_n03_oracle import EXPECTED, ASSERTIONS, verify

NAMES = ("n03-validation.json",) + tuple(
    name
    for variant in EXPECTED
    for name in (
        f"n03-{variant}-native.json",
        f"n03-{variant}-environment.json",
        f"n03-{variant}-receipt.json",
        f"n03-acceptance-{variant}.json",
        f"n03-acceptance-{variant}-validation.json",
    )
)


def read_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or not 0 < info.st_size <= 1024 * 1024):
            raise ValueError("N03 export is not a bounded regular receipt")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) != info.st_size:
            raise ValueError("N03 export changed")
        return raw, evidence.parse(raw)
    finally:
        os.close(fd)


def reject_mutations(native, environment, source, variant):
    """Fail the job if a tampered real run still satisfies any N03 oracle."""
    def rejection(n):
        return next(row for row in n["observations"]
                    if row["kind"] == "n03_rejection_observed")["data"]
    def completion(n):
        return next(row for row in n["observations"]
                    if row["kind"] == "command_complete"
                    and row["data"]["status"] in {"NOT_READY", "BUSY"})["data"]
    def boundary(n):
        return next(row for row in n["observations"]
                    if row["kind"] == "n03_rejection_boundary")["data"]
    def trace_event(n, kind):
        return next(row for row in n["trace"]["events"] if row["kind"] == kind)
    def change_completion(n):
        if variant == "unknown_app_id":
            rejection(n)["status"] = "NOT_READY"
        else:
            completion(n)["status"] = "ACCEPTED"
    def change_request(n):
        if variant == "unknown_app_id":
            rejection(n)["request_id"] = "999"
        else:
            completion(n)["request_id"] = "999"
    changes = [
        ("source", lambda n, e: n.update(source_sha256="f" * 64)),
        ("runtime", lambda n, e: n.update(runtime="999999")),
        ("owner", lambda n, e: n.update(owner_id="f" * 32)),
        ("unexpected_status", lambda n, e: rejection(n).update(status="ACCEPTED")),
        ("completion", lambda n, e: change_completion(n)),
        ("request_reuse", lambda n, e: change_request(n)),
        ("lost_start_count", lambda n, e: n.update(start_count=1 if variant != "double_launch" else 0)),
        ("hidden_app", lambda n, e: boundary(n).update(app={} if variant != "double_launch" else None)),
        ("hidden_target", lambda n, e: boundary(n).update(pending_targets=1 if variant != "double_launch" else -1)),
        ("lost_finalizer", lambda n, e: trace_event(n, "finalizer_published")["data"].update(status=1)),
        ("remote_process", lambda n, e: trace_event(n, "remote_cleanup_observed")["data"]["remote"].update(started=True)
         if variant != "double_launch" else n.update(start_count=0)),
        ("leaked_process", lambda n, e: e["final"].update(app_process_count=1)),
        ("leaked_lock", lambda n, e: e["final"].update(profile_lock_held=True)),
        ("registry_destroy", lambda n, e: n.update(destroy_status=1)),
        ("producer_status", lambda n, e: n.update(status="pass")),
    ]
    if variant == "old_catalog":
        changes.extend([
            ("old_capture_spoof", lambda n, e:
             next(row for row in n["observations"] if row["kind"] == "n03_catalog")
             ["data"].update(capture_id=n["trace"]["events"][-1]["capture"]["capture_id"])),
            ("missing_old_finalizer", lambda n, e:
             next(row for row in n["trace"]["events"] if row["kind"] == "finalizer_published")
             ["data"].update(status=1)),
        ])
    if variant == "double_launch":
        changes.extend([
            ("late_busy", lambda n, e: next(row for row in n["observations"]
             if row["kind"] == "n03_first_live")["data"].update(origin_retired=True)),
            ("sdk_not_retired", lambda n, e: n.update(sdk_retired=False)),
            ("target_digest", lambda n, e: n["readback"].update(create="999999")),
        ])
    for label, mutate in changes:
        n, e = copy.deepcopy(native), copy.deepcopy(environment)
        mutate(n, e)
        try:
            outcome = verify(n, e, source, variant)
        except (ValueError, TypeError, KeyError, RecursionError):
            continue
        if outcome.get("status") == "pass":
            raise ValueError("N03 independent oracle accepted mutation " + label)


def reject_artifact_mutations(acceptance, root, source, variant):
    """Tampered artifact refs are not authorized by the producer's pass."""
    changed = copy.deepcopy(acceptance)
    key = ("N03", variant)
    descriptors = [a for a in changed["artifacts"]
                   if (a["case_id"], a["variant"]) == key]
    if len(descriptors) != 1:
        raise ValueError("N03 must have one typed artifact")
    descriptors[0]["sha256"] = "f" * 64
    result = evidence.validate_acceptance(evidence.canonical(changed), root, source)
    if result["status"] != "invalid":
        raise ValueError("N03 validator accepted modified artifact digest")
    changed = copy.deepcopy(acceptance)
    owner = next(case for case in changed["cases"]
                 if (case["case_id"], case["variant"]) == key)["owners"][0]
    owner["owner_id"] = "f" * 32
    result = evidence.validate_acceptance(evidence.canonical(changed), root, source)
    if result["status"] != "invalid":
        raise ValueError("N03 validator accepted modified owner identity")


def export_and_validate(incoming, output, image_file, head, attached, runtime, running, source_root):
    image_id = image_file.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("N03 image identity invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("N03 source head invalid")
    if not (attached.isdecimal() and runtime.isdecimal() and running in {"true", "false"}):
        raise ValueError("N03 container disposition invalid")
    receipts = {}
    for name in NAMES:
        try:
            raw, parsed = read_json(incoming / name)
        except FileNotFoundError:
            continue
        receipts[name] = parsed
        with (output / name).open("xb") as stream:
            stream.write(raw)
        (output / name).chmod(0o600)
    with (output / "container.json").open("x", encoding="utf-8") as stream:
        json.dump({
            "schema":1,"source_commit":head,"image_id":image_id,
            "attach_exit":int(attached),"container_exit":int(runtime),
            "external_rescue":running == "true","network":"none",
            "host_mounts":False,"formal_i6_passed":False,
        }, stream, sort_keys=True)
        stream.write("\n")
    if set(receipts) != set(NAMES):
        return False
    source = evidence.source_snapshot(source_root)
    if source["dirty"] or source["commit"] != head:
        return False
    report = receipts["n03-validation.json"]
    if (report.get("host_v3_acceptance_passed") is not False
            or report.get("n03_five_variant_smoke_passed") is not True
            or report.get("status") != "pass"
            or report.get("required_variants") != list(EXPECTED)
            or report.get("required_assertions") != [
                {"variant":v,"assertions":list(ASSERTIONS[v])} for v in EXPECTED
            ]
            or len(report.get("cases", [])) != len(EXPECTED)):
        return False
    if evidence.canonical(report["source"]) != evidence.canonical(source):
        return False
    if int(attached) != 0 or int(runtime) != 0 or running != "false":
        return False
    observed = set()
    for row in report["cases"]:
        if (row.get("case_id") != "N03"
                or row.get("variant") not in EXPECTED
                or row["variant"] in observed
                or row.get("environment_disposal") != "not_yet"):
            return False
        variant = row["variant"]
        observed.add(variant)
        native = receipts[f"n03-{variant}-native.json"]
        environment = receipts[f"n03-{variant}-environment.json"]
        try:
            actual = verify(native, environment, source, variant)
        except (ValueError, TypeError, KeyError, RecursionError):
            return False
        if (actual["status"] != "pass"
                or actual["verified_assertions"] != list(ASSERTIONS[variant])):
            return False
        reject_mutations(native, environment, source, variant)
        if (row.get("status") != "pass" or row.get("reason") != "I6_OK"
                or row.get("verified_assertions") != actual["verified_assertions"]):
            return False
        acceptance = receipts[f"n03-acceptance-{variant}.json"]
        claim = receipts[f"n03-acceptance-{variant}-validation.json"]
        checked = evidence.validate_acceptance(
            evidence.canonical(acceptance), output, source
        )
        if evidence.canonical(claim) != evidence.canonical(checked):
            return False
        if (acceptance.get("run_id") != native["run_id"]
                or acceptance.get("host_v3_acceptance_passed") is not False
                or checked["status"] != "incomplete" or checked["exit_code"] != 2
                or checked["host_v3_acceptance_passed"] is not False
                or checked["verified_case_oracles"] != [f"N03/{variant}"]
                or not any(m.startswith("N04/") for m in checked["missing"])):
            return False
        row_ = next(c for c in acceptance["cases"]
                    if (c["case_id"], c["variant"]) == ("N03", variant))
        if row_["cleanup"]["environment_disposal"] != "not_yet":
            return False
        reject_artifact_mutations(acceptance, output, source, variant)
    return observed == set(EXPECTED)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 8:
        return 2
    incoming, output, image_file = map(Path, args[:3])
    try:
        passed = export_and_validate(incoming, output, image_file, *args[3:7], Path(args[7]))
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        passed = False
    if not passed:
        print("N03 independent revalidation failed; formal I6 remains incomplete", file=sys.stderr)
        return 1
    print("N03 five Launch Admission variants passed; formal I6 remains incomplete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
