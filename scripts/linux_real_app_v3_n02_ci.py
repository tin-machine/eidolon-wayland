"""Export bounded N02 artifacts and re-run the independent oracle after Docker copy.

Container/CLI status cannot override the actual Host R/S/T proof. This is a
partial I6 checkpoint. All 87 manifest variants remain required for formal I6.
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
from linux_real_app_v3_n02_oracle import CASES, ASSERTIONS, verify

NAMES = (
    "n02-validation.json",
    "n02-gtk3_desktop-native.json", "n02-gtk3_desktop-environment.json",
    "n02-mousepad_editor-native.json", "n02-mousepad_editor-environment.json",
    "n02-gtk3_desktop-receipt.json", "acceptance-gtk3_desktop.json",
    "acceptance-gtk3_desktop-validation.json",
    "n02-mousepad_editor-receipt.json", "acceptance-mousepad_editor.json",
    "acceptance-mousepad_editor-validation.json",
)


def read_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= 1024*1024:
            raise ValueError("N02 export is not a bounded regular receipt")
        with os.fdopen(fd,"rb",closefd=False) as stream:
            raw = stream.read(1024*1024+1)
        if len(raw) != info.st_size:
            raise ValueError("N02 export changed")
        return raw,evidence.parse(raw)
    finally:
        os.close(fd)


def reject_mutations(native, environment, source, variant):
    """Reopen real producer facts and demand every mutation fail independently.

    These are supplemental negative probes, not executions of formal N03/F12.
    """
    changes = (
        ("source", lambda n, e: n.update(source_sha256="f" * 64)),
        ("wrong_variant", lambda n, e: n.update(variant="another")),
        ("wrong_runtime", lambda n, e: n["readback"].update(runtime="90000")),
        ("wrong_create", lambda n, e: n["readback"].update(create="9999")),
        ("diagnostic", lambda n, e: n["readback"].update(layers=[])),
        ("old_frame", lambda n, e: n["readback"].update(content_frame="1",
             min_content_frame="99999")),
        ("wrong_adapter", lambda n, e: n["sdk_observations"][0].update(adapter="922")),
        ("not_current", lambda n, e: n["ready"]["sdk_target"].update(binding_present=False)),
        ("old_focus", lambda n, e: n["input"].update(focus_token="99999")),
        ("old_epoch", lambda n, e: n["input"].update(epoch=999999)),
        ("no_delivery", lambda n, e: n["input"].update(delivered_after=0)),
        ("unretired_sdk", lambda n, e: n.update(sdk_retired=False)),
        ("texture_leak", lambda n, e: n["last_snapshot"]["runtime"]["render"]["composition"].update(texture_bytes=1)),
        ("gl_error", lambda n, e: n["last_snapshot"]["runtime"]["render"]["composition"].update(gl_error=1282)),
        ("queued", lambda n, e: n["last_snapshot"]["runtime"]["render"].update(queued=1)),
        ("origin_pending", lambda n, e: n["last_snapshot"]["runtime"]["reclamation"].update(origin_retired=False)),
        ("destroy_failed", lambda n, e: n.update(destroy_status=1)),
        ("leaked_child", lambda n, e: e["final"].update(app_process_count=1)),
        ("trace_overflow", lambda n, e: n["trace"].update(overflow=True)),
        ("lost_start", lambda n, e: n.update(start_count=0)),
        ("lost_finalizer", lambda n, e: next(item for item in n["trace"]["events"]
            if item["kind"] == "finalizer_published")["data"].update(status=1)),
    )
    for label, mutate in changes:
        n, e = copy.deepcopy(native), copy.deepcopy(environment)
        mutate(n, e)
        try:
            result = verify(n, e, source, variant)
        except (ValueError, KeyError, TypeError, RecursionError):
            continue
        if result.get("status") == "pass":
            raise ValueError("N02 independent oracle accepted mutation " + label)


def export_and_validate(incoming, output, image_file, head, attached, runtime, running, source_root):
    image_id = image_file.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}",image_id):
        raise ValueError("N02 image identity invalid")
    if not re.fullmatch(r"[0-9a-f]{40}",head):
        raise ValueError("N02 source head invalid")
    if not (attached.isdecimal() and runtime.isdecimal() and running in {"true","false"}):
        raise ValueError("N02 disposition invalid")
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
    with (output / "container.json").open("x",encoding="utf-8") as stream:
        json.dump({"schema":1,"source_commit":head,"image_id":image_id,
                   "attach_exit":int(attached),"container_exit":int(runtime),
                   "external_rescue":running == "true","network":"none",
                   "host_mounts":False,"formal_i6_passed":False},stream,sort_keys=True)
        stream.write("\n")
    if set(receipts) != set(NAMES):
        return False
    source = evidence.source_snapshot(source_root)
    if source["dirty"] or source["commit"] != head:
        return False
    report = receipts["n02-validation.json"]
    if report.get("host_v3_acceptance_passed") is not False or report.get("n02_two_variant_smoke_passed") is not True:
        return False
    if report.get("status") != "pass" or report.get("required_variants") != list(CASES):
        return False
    if len(report.get("cases",[])) != len(CASES):
        return False
    if evidence.canonical(report["source"]) != evidence.canonical(source):
        return False
    if int(attached) != 0 or int(runtime) != 0 or running != "false":
        return False
    observed = set()
    for row in report["cases"]:
        if row.get("case_id") != "N02" or row.get("variant") not in CASES or row["variant"] in observed:
            return False
        variant = row["variant"]
        observed.add(variant)
        if row.get("environment_disposal") != "not_yet":
            return False
        native = receipts["n02-" + variant + "-native.json"]
        environment = receipts["n02-" + variant + "-environment.json"]
        try:
            actual = verify(native,environment,source,variant)
        except (ValueError,TypeError,KeyError,RecursionError):
            return False
        if actual["status"] != "pass" or actual["verified_assertions"] != list(ASSERTIONS):
            return False
        reject_mutations(native, environment, source, variant)
        if row.get("status") != "pass" or row.get("reason") != "I6_OK":
            return False
        if row.get("verified_assertions") != actual["verified_assertions"]:
            return False
        # The standard, frozen 87-variant I6 validator must independently
        # reopen the exact typed N02 R/S/T receipts from the exported bytes.
        # A standalone N02 pass is not itself formal Host acceptance.
        acceptance = receipts["acceptance-" + variant + ".json"]
        claim = receipts["acceptance-" + variant + "-validation.json"]
        expected_case = "N02/" + variant
        checked = evidence.validate_acceptance(
            evidence.canonical(acceptance), output, source
        )
        if evidence.canonical(claim) != evidence.canonical(checked):
            return False
        if (acceptance.get("run_id") != native["run_id"]
                or acceptance.get("host_v3_acceptance_passed") is not False
                or checked["status"] != "incomplete"
                or checked["exit_code"] != 2
                or checked["host_v3_acceptance_passed"] is not False
                or checked["verified_case_oracles"] != [expected_case]
                or not any(item.startswith("N03/") for item in checked["missing"])):
            return False
        matched = [case for case in acceptance["cases"]
                   if (case["case_id"], case["variant"]) == ("N02", variant)]
        if (len(matched) != 1 or matched[0]["status"] != "pass"
                or matched[0]["reason"] != "I6_OK"
                or matched[0]["cleanup"]["environment_disposal"] != "not_yet"):
            return False
    return observed == set(CASES)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 8:
        return 2
    incoming, output, image_file = map(Path,args[:3])
    try:
        passed = export_and_validate(incoming,output,image_file,*args[3:7],Path(args[7]))
    except (OSError,ValueError,TypeError,KeyError,RecursionError):
        passed = False
    if not passed:
        print("N02 independent revalidation failed; formal I6 remains incomplete",file=sys.stderr)
        return 1
    print("N02 GTK/Mousepad Launch smoke passed; formal I6 remains incomplete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
