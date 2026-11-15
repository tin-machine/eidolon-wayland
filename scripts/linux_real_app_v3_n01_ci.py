"""Export fixed N01 receipts and independently revalidate them after Docker copy."""
from pathlib import Path
import json
import os
import re
import stat
import sys

import linux_real_app_v3_evidence as evidence

NAMES = ("acceptance.json", "acceptance-validation.json", "n01-validation.json", "n01-receipt.json")
DIAGNOSTIC_NAMES = ("n01-native.json",)


def read_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                not 0 < info.st_size <= 1024 * 1024):
            raise ValueError("N01 exported receipt is invalid")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) != info.st_size:
            raise ValueError("N01 exported receipt changed")
        return raw, evidence.parse(raw)
    finally:
        os.close(fd)


def export_and_validate(incoming, output, image_file, head, attached, runtime, running, source_root):
    image_id = image_file.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("N01 image identity is invalid")
    if not (attached.isdecimal() and runtime.isdecimal() and running in {"true", "false"}):
        raise ValueError("N01 container disposition is invalid")
    receipts = {}
    for name in NAMES + DIAGNOSTIC_NAMES:
        try:
            raw, parsed = read_json(incoming / name)
        except FileNotFoundError:
            continue
        except (OSError, ValueError, TypeError):
            if name in DIAGNOSTIC_NAMES:
                # Diagnostics are optional, untrusted observations. Omit an
                # unsafe file without promoting it to an acceptance gate.
                continue
            raise
        if name in NAMES:
            receipts[name] = parsed
        with (output / name).open("xb") as target:
            target.write(raw)
        (output / name).chmod(0o600)
    envelope = {"schema": 1, "source_commit": head, "image_id": image_id,
                "attach_exit": int(attached), "container_exit": int(runtime),
                "external_rescue": running == "true", "network": "none",
                "host_mounts": False, "formal_i6_passed": False}
    with (output / "container.json").open("x", encoding="utf-8") as target:
        json.dump(envelope, target, sort_keys=True)
        target.write("\n")
    if set(receipts) != set(NAMES):
        return False
    # Docker copy changes filesystem ownership. Re-open the exported bytes with
    # the same strict no-follow validator against the caller's actual checkout.
    source = evidence.source_snapshot(source_root)
    if source["dirty"] or source["commit"] != head:
        return False
    checked = evidence.validate_acceptance(evidence.read_report(output / "acceptance.json"), output, source)
    n01 = receipts["n01-validation.json"]
    return (int(attached) == 0 and int(runtime) == 0 and running == "false"
            and evidence.canonical(receipts["acceptance-validation.json"]) == evidence.canonical(checked)
            and n01.get("status") == "pass" and n01.get("n01_inspect_only_passed") is True
            and n01.get("source") == source and checked.get("n01_inspect_only_passed") is True
            and checked.get("host_v3_acceptance_passed") is False
            and type(checked.get("exit_code")) is int and checked["exit_code"] == 2)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 8:
        return 2
    incoming, output, image_file = map(Path, args[:3])
    try:
        passed = export_and_validate(incoming, output, image_file, *args[3:7], Path(args[7]))
    except (OSError, ValueError, TypeError, KeyError):
        passed = False
    if not passed:
        print("N01 smoke did not complete with independently revalidated receipts; formal I6 remains incomplete", file=sys.stderr)
        return 1
    print("N01 Inspect-only passed; formal I6 remains incomplete (exit 2)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
