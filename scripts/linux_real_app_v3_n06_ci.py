"""N06 fixed-export and mutation revalidation; only real saved receipts count.

A Docker return code, a producer status, or a typed-formal claim alone can
never prove GUI input, preserved edits, process exit or product cleanup.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import sys

import linux_real_app_v3_evidence as evidence
from linux_real_app_v3_n02_ci import read_json
from linux_real_app_v3_n06_oracle import ASSERTIONS_BY_VARIANT, verify
from linux_real_app_v3_n06 import VERSIONS

NAMES=("n06-validation.json",)+tuple(
    f"n06-{v}-{kind}.json"
    for v in VERSIONS for kind in ("native","environment","receipt")
)+tuple(
    f"n06-acceptance-{v}{suffix}.json"
    for v in VERSIONS for suffix in ("","-validation")
)


def reject_mutations(native,env,source,variant):
    """Every positive premise must be necessary. Each mutation must be denied."""
    def observation(n,name):
        return next(x for x in n["observations"] if x["kind"]==name)
    changes=[
        ("stale_focus",lambda n,e:n["n06"]["steps"][0].update(token="999999")),
        ("registry_only",lambda n,e:n["n06"]["steps"][0].update(
            delivered_after=n["n06"]["steps"][0]["delivered_before"])),
        ("parent_mismatch",lambda n,e:n["n06"]["dialogs"][0]["window"].update(parent="999999")),
        ("foreign_capture",lambda n,e:n["n06"].update(capture_id="f"*64)),
        ("dialog_generation_reused",lambda n,e:n["n06"]["dialogs"][0]["target"].update(
            window=n["n06"]["main_target"]["window"])),
        ("source_changed",lambda n,e:n.update(source_sha256="f"*64)),
        ("sdk_retirement",lambda n,e:n.update(sdk_retired=False)),
        ("renderer_retirement",lambda n,e:n["last_snapshot"]["runtime"]["render"]
            ["composition"].update(texture_bytes=1)),
        ("journal_missing",lambda n,e:next(x for x in n["trace"]["events"]
            if x["kind"]=="finalizer_published").update(kind="tampered")),
        ("producer_pass_only",lambda n,e:n.update(status="pass")),
        ("file_input_replaced",lambda n,e:e["file_proof"]["input"].update(inode=999999)),
        ("input_overwritten",lambda n,e:e["file_proof"]["input"].update(sha256="f"*64)),
    ]
    if variant in ("open_cancel","unsaved_close_cancel"):
        changes.extend((
            ("not_cancelled",lambda n,e:n["n06"].update(cancel=None)),
            ("cancel_no_delivery",lambda n,e:n["n06"]["cancel"].update(
                delivered_after=n["n06"]["cancel"]["delivered_before"])),
            ("app_exited_after_cancel",lambda n,e:n["n06"]["continuation"].update(
                main_mapped=False)),
        ))
    if variant in ("save_as_confirm","unsaved_close_cancel"):
        changes.extend((
            ("output_missing",lambda n,e:e["file_proof"].update(output=None)),
            ("output_wrong_length",lambda n,e:e["file_proof"]["output"].update(size=96)),
            ("output_wrong_bytes",lambda n,e:e["file_proof"]["output"].update(sha256="f"*64)),
            ("output_hardlinked",lambda n,e:e["file_proof"]["output"].update(nlink=2)),
        ))
    if variant=="unsaved_close_cancel":
        changes.extend((
            ("edited_memory_lost",lambda n,e:e["file_proof"]["output"].update(
                sha256=e["file_proof"]["input"]["sha256"])),
            ("wrong_cancel_order",lambda n,e:observation(n,"n06_save_confirmed")
                .update(kind="deleted")),
        ))
    if variant=="close_confirm":
        changes.extend((
            ("no_close_intent",lambda n,e:observation(n,"n06_close_intent")
                .update(kind="missing")),
            ("app_not_exited",lambda n,e:n["n06"]["app_exit"].update(local_workers=1)),
            ("main_not_destroyed",lambda n,e:n["n06"]["main_destroyed"].update(main_mapped=True)),
            ("exit_before_intent",lambda n,e:n["observations"].reverse()),
            ("discard_no_delivery",lambda n,e:n["n06"]["discard"].update(
                delivered_after=n["n06"]["discard"]["delivered_before"])),
        ))
    for label,mutate in changes:
        n,e=copy.deepcopy(native),copy.deepcopy(env)
        mutate(n,e)
        try:
            checked=verify(n,e,source,variant)
        except (ValueError,TypeError,KeyError,OverflowError,RecursionError,IndexError):
            continue
        if checked["status"]=="pass":
            raise ValueError("N06 oracle accepted tampered "+variant+"/"+label)


def export_and_validate(incoming,output,image_file,head,attached,runtime,running,source_root,pr_head):
    image_id=image_file.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}",image_id):
        raise ValueError("N06 image digest invalid")
    if not re.fullmatch(r"[0-9a-f]{40}",head):
        raise ValueError("N06 source commit invalid")
    if pr_head and not re.fullmatch(r"[0-9a-f]{40}",pr_head):
        raise ValueError("N06 PR head digest invalid")
    if not (attached.isdecimal() and runtime.isdecimal() and running in ("true","false")):
        raise ValueError("N06 Docker disposition invalid")
    receipts={}
    for name in NAMES:
        try:
            raw,value=read_json(incoming/name)
        except FileNotFoundError:
            continue
        receipts[name]=value
        with (output/name).open("xb") as stream:
            stream.write(raw)
        (output/name).chmod(0o600)
    with (output/"container.json").open("x",encoding="utf-8") as stream:
        json.dump({"schema":1,"source_commit":head,"pr_head_sha":pr_head or None,
            "image_id":image_id,"attach_exit":int(attached),"container_exit":int(runtime),
            "external_rescue":running=="true","network":"none",
            "host_mounts":False,"formal_i6_passed":False},stream,sort_keys=True)
        stream.write("\n")
    if set(receipts)!=set(NAMES):
        return False
    source=evidence.source_snapshot(source_root)
    if source["dirty"] or source["commit"]!=head:
        return False
    report=receipts["n06-validation.json"]
    if (report.get("status")!="pass" or
        report.get("host_v3_acceptance_passed") is not False or
        report.get("n06_four_variant_smoke_passed") is not True or
        report.get("required_variants")!=list(VERSIONS) or
        report.get("required_assertions")!={v:list(ASSERTIONS_BY_VARIANT[v]) for v in VERSIONS} or
        len(report.get("cases",[]))!=4 or
        evidence.canonical(report.get("source"))!=evidence.canonical(source) or
        int(attached)!=0 or int(runtime)!=0 or running!="false"):
        return False
    runs=set()
    captures=set()
    owners=set()
    sessions=set()
    for case,variant in zip(report["cases"],VERSIONS):
        if (case.get("case_id")!="N06" or case.get("variant")!=variant or
            case.get("status")!="pass" or case.get("reason")!="I6_OK" or
            case.get("verified_assertions")!=list(ASSERTIONS_BY_VARIANT[variant]) or
            case.get("environment_disposal")!="not_yet"):
            return False
        native=receipts[f"n06-{variant}-native.json"]
        env=receipts[f"n06-{variant}-environment.json"]
        try:
            checked=verify(native,env,source,variant)
        except (ValueError,TypeError,KeyError,OverflowError,RecursionError):
            return False
        if checked.get("status")!="pass" or checked["verified_assertions"]!=list(ASSERTIONS_BY_VARIANT[variant]):
            return False
        reject_mutations(native,env,source,variant)
        runs.add(native["run_id"])
        captures.add(native["n06"]["capture_id"])
        owners.add(native["owner_id"])
        sessions.add(native["n06"]["main_target"]["session"])
        acceptance=receipts[f"n06-acceptance-{variant}.json"]
        claim=receipts[f"n06-acceptance-{variant}-validation.json"]
        actual=evidence.validate_acceptance(evidence.canonical(acceptance),output,source)
        if (evidence.canonical(actual)!=evidence.canonical(claim) or
            actual["status"]!="incomplete" or
            actual["host_v3_acceptance_passed"] is not False or
            actual["exit_code"]!=2 or
            actual["verified_case_oracles"]!=[f"N06/{variant}"] or
            not any(x.startswith("N07/") for x in actual["missing"]) or
            acceptance["run_id"]!=native["run_id"] or
            acceptance["host_v3_acceptance_passed"] is not False):
            return False
    return len(runs)==len(captures)==len(owners)==len(sessions)==4


def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    if len(args)!=9:
        print("N06 exporter requires 9 arguments",file=sys.stderr)
        return 2
    incoming,output,image_file=map(Path,args[:3])
    try:
        passed=export_and_validate(incoming,output,image_file,*args[3:7],
                                   Path(args[7]),args[8])
    except (OSError,ValueError,TypeError,KeyError,RecursionError,OverflowError):
        passed=False
    if not passed:
        print("N06 independent revalidation failed; formal I6 incomplete",file=sys.stderr)
        return 1
    print("N06 four real GTK dialog/close variants passed; formal I6 incomplete")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
