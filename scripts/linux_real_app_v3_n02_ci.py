"""Export bounded N02 artifacts and re-run the independent oracle after Docker copy.

Container/CLI status cannot override the actual Host R/S/T proof. This is a
partial I6 checkpoint. All 87 manifest variants remain required for formal I6.
"""
from __future__ import annotations

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
        if row.get("status") != "pass" or row.get("reason") != "I6_OK":
            return False
        if row.get("verified_assertions") != actual["verified_assertions"]:
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
