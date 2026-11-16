"""Export only bounded N05 receipts; independently revalidate saved bytes.

The Docker process return code, producer's pass flags, and formal-I6 report
claims have no authority. This exporter reruns the N05 oracle plus mutations.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import sys

import linux_real_app_v3_evidence as evidence
from linux_real_app_v3_n02_ci import read_json
from linux_real_app_v3_n05_oracle import ASSERTIONS, verify

VARIANT="open_edit_save_as"
NAMES=(
    "n05-validation.json",
    "n05-open_edit_save_as-native.json",
    "n05-open_edit_save_as-environment.json",
    "n05-open_edit_save_as-receipt.json",
    "n05-acceptance-open_edit_save_as.json",
    "n05-acceptance-open_edit_save_as-validation.json",
)


def reject_mutations(native, env, source):
    """All recorded positive proofs must be necessary for N05 PASS."""
    changes=(
        ("input_digest",lambda n,e: e["file_proof"]["input"].update(sha256="f"*64)),
        ("output_digest",lambda n,e: e["file_proof"]["output"].update(sha256="f"*64)),
        ("output_size",lambda n,e: e["file_proof"]["output"].update(size=96)),
        ("input_changed",lambda n,e: e["file_proof"]["input"].update(inode=999999)),
        ("output_absent",lambda n,e: e.update(file_proof=None)),
        ("uid_changed",lambda n,e: e["file_proof"]["output"].update(uid=0)),
        ("input_inode",lambda n,e: e["file_proof"]["baseline"].update(inode=999999)),
        ("hardlink",lambda n,e: e["file_proof"]["output"].update(nlink=2)),
        ("alias",lambda n,e: e["file_proof"]["output"].update(
            inode=e["file_proof"]["input"]["inode"],
            dev=e["file_proof"]["input"]["dev"])),
        ("foreign_run",lambda n,e: e["file_proof"].update(run_id="f"*32)),
        ("foreign_capture",lambda n,e: e["file_proof"].update(capture_id="f"*64)),
        ("registry_only",lambda n,e: n["n05"]["steps"][0].update(
            delivered_after=n["n05"]["steps"][0]["delivered_before"])),
        ("input_delivery",lambda n,e: n["n05"]["steps"][4].update(expected_delivery=0)),
        ("stale_focus",lambda n,e: n["n05"]["steps"][2].update(token="999999")),
        ("main_parent",lambda n,e: n["n05"]["dialogs"][0]["window"].update(parent="999999")),
        ("dialog_target",lambda n,e: n["n05"]["dialogs"][1]["target"].update(window="999999")),
        ("dialog_reuse",lambda n,e: n["n05"]["dialogs"][1]["window"].update(
            window=n["n05"]["dialogs"][0]["window"]["window"])),
        ("missing_dialog_baseline",lambda n,e: n.update(observations=[
            row for row in n["observations"]
            if row["kind"]!="n05_dialog_baseline"])),
        ("post_shortcut_baseline",lambda n,e: next(row for row in
            n["observations"] if row["kind"]=="n05_dialog_baseline"
            and row["data"]["phase"]=="save_as")["data"]["mapped_windows"].append(
                n["n05"]["dialogs"][1]["window"]["window"])),

        ("missing_sdk",lambda n,e: n.update(sdk_observations=[
            x for x in n["sdk_observations"] if x["data"]["target"] !=
            n["n05"]["dialogs"][1]["target"]])),
        ("renderer_leak",lambda n,e: n["last_snapshot"]["runtime"]["render"]
            ["composition"].update(texture_bytes=1)),
        ("finalizer_missing",lambda n,e: next(x for x in n["trace"]["events"]
             if x["kind"]=="finalizer_published").update(kind="missing")),
        ("sdk_retirement",lambda n,e: n.update(sdk_retired=False)),
        ("owner",lambda n,e: n.update(owner_id="f"*32)),
        ("source",lambda n,e: n.update(source_sha256="f"*64)),
        ("producer_pass_only",lambda n,e: n.update(status="pass")),
    )
    for label, mutate in changes:
        n,e=copy.deepcopy(native),copy.deepcopy(env)
        mutate(n,e)
        try:
            result=verify(n,e,source,VARIANT)
        except (ValueError,TypeError,KeyError,RecursionError,OverflowError):
            continue
        if result.get("status")=="pass":
            raise ValueError("N05 independent oracle accepted "+label)



def export_and_validate(incoming,output,image_file,head,attached,runtime,running,source_root,pr_head):
    image_id=image_file.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}",image_id):
        raise ValueError("N05 image digest invalid")
    if not re.fullmatch(r"[0-9a-f]{40}",head):
        raise ValueError("N05 source commit invalid")
    if pr_head and not re.fullmatch(r"[0-9a-f]{40}",pr_head):
        raise ValueError("N05 PR head digest invalid")
    if not (attached.isdecimal() and runtime.isdecimal() and running in ("true","false")):
        raise ValueError("N05 Docker disposition invalid")
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
        json.dump({"schema":1,"source_commit":head,
            "pr_head_sha":pr_head or None,"image_id":image_id,
            "attach_exit":int(attached),"container_exit":int(runtime),
            "external_rescue":running=="true","network":"none",
            "host_mounts":False,"formal_i6_passed":False},stream,sort_keys=True)
        stream.write("\n")
    if set(receipts) != set(NAMES):
        return False
    source=evidence.source_snapshot(source_root)
    if source["dirty"] or source["commit"] != head:
        return False
    report=receipts["n05-validation.json"]
    if (report.get("status") != "pass" or
        report.get("host_v3_acceptance_passed") is not False or
        report.get("n05_single_variant_smoke_passed") is not True or
        report.get("required_variants") != [VARIANT] or
        report.get("required_assertions") != list(ASSERTIONS) or
        len(report.get("cases",[])) != 1 or
        evidence.canonical(report["source"]) != evidence.canonical(source) or
        int(attached) != 0 or int(runtime) != 0 or running != "false"):
        return False
    row=report["cases"][0]
    if (row.get("case_id") != "N05" or row.get("variant") != VARIANT or
        row.get("status") != "pass" or row.get("reason") != "I6_OK" or
        row.get("environment_disposal") != "not_yet" or
        row.get("verified_assertions") != list(ASSERTIONS)):
        return False
    native=receipts["n05-open_edit_save_as-native.json"]
    environment=receipts["n05-open_edit_save_as-environment.json"]
    try:
        result=verify(native,environment,source,VARIANT)
    except (ValueError,KeyError,TypeError,RecursionError):
        return False
    if result["status"] != "pass" or result["verified_assertions"] != list(ASSERTIONS):
        return False
    reject_mutations(native,environment,source)
    acceptance=receipts["n05-acceptance-open_edit_save_as.json"]
    claim=receipts["n05-acceptance-open_edit_save_as-validation.json"]
    verified=evidence.validate_acceptance(evidence.canonical(acceptance),output,source)
    if evidence.canonical(verified) != evidence.canonical(claim):
        return False
    return (verified["status"]=="incomplete" and
            verified["host_v3_acceptance_passed"] is False and
            verified["exit_code"]==2 and
            verified["verified_case_oracles"]==["N05/open_edit_save_as"] and
            any(x.startswith("N06/") for x in verified["missing"]) and
            acceptance["run_id"]==native["run_id"] and
            acceptance["host_v3_acceptance_passed"] is False)


def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    if len(args)!=9:
        print("N05 exporter requires 9 arguments",file=sys.stderr)
        return 2
    incoming,output,image_file=map(Path,args[:3])
    try:
        passed=export_and_validate(incoming,output,image_file,*args[3:7],
                                   Path(args[7]),args[8])
    except (OSError,ValueError,TypeError,KeyError,RecursionError,OverflowError):
        passed=False
    if not passed:
        print("N05 independent revalidation failed; formal I6 remains incomplete",file=sys.stderr)
        return 1
    print("N05 live Open/Edit/Save As smoke passed; formal I6 remains incomplete")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
