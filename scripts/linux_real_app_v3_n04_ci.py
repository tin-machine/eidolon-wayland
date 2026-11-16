"""Export only bounded N04 receipts; independently revalidate saved bytes.

The Docker process return code, producer's pass flags, and formal-I6 report
claims have no authority. This exporter reruns the N04 oracle plus mutations.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import sys

import linux_real_app_v3_evidence as evidence
from linux_real_app_v3_n02_ci import read_json
from linux_real_app_v3_n04_oracle import ASSERTIONS, verify

VARIANT="idle_125s"
NAMES=(
    "n04-validation.json",
    "n04-idle_125s-native.json",
    "n04-idle_125s-environment.json",
    "n04-idle_125s-receipt.json",
    "n04-acceptance-idle_125s.json",
    "n04-acceptance-idle_125s-validation.json",
)


def reject_mutations(native, env, source):
    """Reject forged pass, stale activity, missing lease renewals and cleanup."""
    changes=(
        ("idle_short",lambda n,e: n["n04"]["idle"].update(
            end_ns=n["n04"]["idle"]["begin_ns"]+124_000_000_000)),
        ("native_idle_short",lambda n,e: next(o for o in n["observations"]
            if o["kind"]=="n04_idle_end")["data"].update(
                ns=next(o for o in n["observations"]
                    if o["kind"]=="n04_idle_begin")["data"]["ns"]+124_000_000_000)),
        ("supervisor_idle_short",lambda n,e: e["supervisor"]["markers"][1].update(
            at_ns=e["supervisor"]["markers"][0]["at_ns"]+124_000_000_000)),
        ("heartbeat_absent",lambda n,e: next(x for x in n["trace"]["events"]
            if x["kind"]=="protocol_event"
            and x["data"]["record"]["event"]["type"]=="heartbeat")["data"]["record"]
                ["event"].update(type="missing")),
        ("heartbeat_sequence",lambda n,e: next(x for x in n["trace"]["events"]
            if x["kind"]=="protocol_event"
            and x["data"]["record"]["event"]["type"]=="heartbeat")["data"]["record"]
                .update(request_sequence="999999")),
        ("heartbeat_reuse",lambda n,e: [
            x["data"]["record"].update(request_sequence="1") for x in n["trace"]["events"]
            if x["kind"]=="protocol_event"
            and x["data"]["record"]["event"]["type"]=="heartbeat"][:2]),
        ("heartbeat_foreign_capture",lambda n,e: next(x for x in n["trace"]["events"]
            if x["kind"]=="protocol_event"
            and x["data"]["record"]["event"]["type"]=="heartbeat")["capture"].update(
                capture_id="f"*64)),
        ("same_dialog_request",lambda n,e: n["n04"]["dialogs"][1].update(
            base_request="2000")),
        ("registry_only",lambda n,e: n["n04"]["dialogs"][1].update(
            delivered_open=n["n04"]["dialogs"][1]["delivered_before"])),
        ("esc_not_reset",lambda n,e: n["n04"]["dialogs"][1].update(
            escape_resets_after=n["n04"]["dialogs"][1]["escape_resets_before"])),
        ("esc_key_still_pressed",lambda n,e: n["n04"]["dialogs"][1].update(
            escape_pressed_after=1)),
        ("no_fresh_dialog",lambda n,e: n["n04"]["dialogs"][1]["window"].update(
            window=n["n04"]["dialogs"][0]["window"]["window"])),
        ("old_focus_token",lambda n,e: n["n04"]["dialogs"][1].update(
            main_focus_token="999999")),
        ("stale_target",lambda n,e: n["n04"]["dialogs"][1]["dialog_target"].update(
            window="999999")),
        ("stop_late",lambda n,e: next(o for o in n["observations"]
            if o["kind"]=="n04_stop_ack_seen")["data"].update(
                ns=next(o for o in n["observations"]
                    if o["kind"]=="n04_stop_send_seen")["data"]["ns"]+9_000_000_000)),
        ("finalizer_missing",lambda n,e: n["trace"]["events"][
            next(i for i,x in enumerate(n["trace"]["events"])
                 if x["kind"]=="finalizer_published")].update(kind="missing")),
        ("sdk_not_retired",lambda n,e: n.update(sdk_retired=False)),
        ("renderer_leaked",lambda n,e: n["last_snapshot"]["runtime"]["render"]
            ["composition"].update(texture_bytes=1)),
        ("source_digest",lambda n,e: n.update(source_sha256="f"*64)),
        ("owner_digest",lambda n,e: n.update(owner_id="f"*32)),
        ("target_digest",lambda n,e: n["n04"]["dialogs"][0]["dialog_readback"]
            ["target"].update(window="999999")),
        ("producer_pass_only",lambda n,e: n.update(status="pass")),
    )
    for label, mutate in changes:
        n,e=copy.deepcopy(native),copy.deepcopy(env)
        mutate(n,e)
        try:
            result=verify(n,e,source,VARIANT)
        except (ValueError,TypeError,KeyError,RecursionError,OverflowError):
            continue
        if result.get("status") == "pass":
            raise ValueError("N04 independent oracle accepted " + label)


def export_and_validate(incoming,output,image_file,head,attached,runtime,running,source_root,pr_head):
    image_id=image_file.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}",image_id):
        raise ValueError("N04 image digest invalid")
    if not re.fullmatch(r"[0-9a-f]{40}",head):
        raise ValueError("N04 source commit invalid")
    if pr_head and not re.fullmatch(r"[0-9a-f]{40}",pr_head):
        raise ValueError("N04 PR head digest invalid")
    if not (attached.isdecimal() and runtime.isdecimal() and running in ("true","false")):
        raise ValueError("N04 Docker disposition invalid")
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
    report=receipts["n04-validation.json"]
    if (report.get("status") != "pass" or
        report.get("host_v3_acceptance_passed") is not False or
        report.get("n04_single_variant_smoke_passed") is not True or
        report.get("required_variants") != [VARIANT] or
        report.get("required_assertions") != list(ASSERTIONS) or
        len(report.get("cases",[])) != 1 or
        evidence.canonical(report["source"]) != evidence.canonical(source) or
        int(attached) != 0 or int(runtime) != 0 or running != "false"):
        return False
    row=report["cases"][0]
    if (row.get("case_id") != "N04" or row.get("variant") != VARIANT or
        row.get("status") != "pass" or row.get("reason") != "I6_OK" or
        row.get("environment_disposal") != "not_yet" or
        row.get("verified_assertions") != list(ASSERTIONS)):
        return False
    native=receipts["n04-idle_125s-native.json"]
    environment=receipts["n04-idle_125s-environment.json"]
    try:
        result=verify(native,environment,source,VARIANT)
    except (ValueError,KeyError,TypeError,RecursionError):
        return False
    if result["status"] != "pass" or result["verified_assertions"] != list(ASSERTIONS):
        return False
    reject_mutations(native,environment,source)
    acceptance=receipts["n04-acceptance-idle_125s.json"]
    claim=receipts["n04-acceptance-idle_125s-validation.json"]
    verified=evidence.validate_acceptance(evidence.canonical(acceptance),output,source)
    if evidence.canonical(verified) != evidence.canonical(claim):
        return False
    return (verified["status"]=="incomplete" and
            verified["host_v3_acceptance_passed"] is False and
            verified["exit_code"]==2 and
            verified["verified_case_oracles"]==["N04/idle_125s"] and
            any(x.startswith("N05/") for x in verified["missing"]) and
            acceptance["run_id"]==native["run_id"] and
            acceptance["host_v3_acceptance_passed"] is False)


def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    if len(args)!=9:
        print("N04 exporter requires 9 arguments",file=sys.stderr)
        return 2
    incoming,output,image_file=map(Path,args[:3])
    try:
        passed=export_and_validate(incoming,output,image_file,*args[3:7],
                                   Path(args[7]),args[8])
    except (OSError,ValueError,TypeError,KeyError,RecursionError,OverflowError):
        passed=False
    if not passed:
        print("N04 independent revalidation failed; formal I6 remains incomplete",file=sys.stderr)
        return 1
    print("N04 125s idle and GUI smoke passed; formal I6 remains incomplete")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
