"""N07-B1 strict source-bound export of diagnostic-only Host receipts.

This module cannot validate or export an N07 typed acceptance receipt.
Independently reopening the saved bytes is mandatory even when Docker exits 0.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import sys

import linux_real_app_v3_evidence as evidence
from linux_real_app_v3_n02_ci import read_json
from linux_real_app_v3_n07_b1_probe import inspect, ProbeInvalid

NAMES = (
    "n07-b1-validation.json",
    "n07-b1-native.json",
    "n07-b1-environment.json",
)


def adversarial_checks(native: dict) -> None:
    mutations = (
        ("registry_only", lambda n: n["n07_probe"]["pointer"]["after"].update(
            delivered_buttons=n["n07_probe"]["pointer"]["before"]["delivered_buttons"])),
        ("old_epoch", lambda n: n["n07_probe"]["pointer"].update(epoch_after=999)),
        ("foreign_capture", lambda n: n["n07_probe"].update(capture_id="f"*64)),
        ("popup_role", lambda n: n["n07_probe"]["popup"].update(role="xdg_toplevel")),
        ("popup_parent", lambda n: n["n07_probe"]["popup"].update(
            parent_surface=n["n07_probe"]["popup"]["surface"])),
        ("popup_serial", lambda n: n["n07_probe"]["popup"].update(ack_serial=None)),
        ("foreign_grab_serial", lambda n: n["n07_probe"]["popup"].update(
            grab_serial=999999)),
        ("popup_layer", lambda n: n["n07_probe"]["popup"].update(layer=999)),
        ("popup_generation", lambda n: n["n07_probe"]["main_target"].update(
            generation="999")),
        ("no_gpu_readback", lambda n: n["n07_probe"]["menu_open"].update(readback=None)),
        ("stale_frame", lambda n: n["n07_probe"]["menu_open"]["readback"]
         ["receipt"].update(content_frame="1")),
        ("unreviewed_promotion", lambda n: n["n07_probe"].update(menu_identity_verified=True)),
        ("reused_observed_golden", lambda n: n.update(
            n07_assertions_verified=["pointer_toggled_setting"])),
        ("sdk_not_retired", lambda n: n.update(sdk_retired=False)),
        ("no_registry_destroy", lambda n: n.update(destroy_status=None)),
    )
    for label, mutate in mutations:
        altered = copy.deepcopy(native)
        mutate(altered)
        try:
            verdict = inspect(altered)
        except (ProbeInvalid, ValueError, KeyError, TypeError, OverflowError):
            continue
        raise ValueError(
            "N07-B1 diagnostic oracle accepted mutation " + label + ": " +
            str(verdict.get("status")))


def export_and_validate(
        incoming: Path, output: Path, image_file: Path, head: str,
        attached: str, runtime: str, running: str, source_root: Path,
        pr_head: str) -> bool:
    image_id = image_file.read_text(encoding="ascii").strip()
    if (re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None
            or re.fullmatch(r"[0-9a-f]{40}", head) is None
            or (pr_head and re.fullmatch(r"[0-9a-f]{40}", pr_head) is None)
            or attached not in ("0", "1", "2", "124", "125", "130", "137", "143")
            or not runtime.isdecimal()
            or running not in ("true", "false")):
        raise ValueError("N07-B1 invalid Docker/source disposition")
    received = {}
    for name in NAMES:
        try:
            raw, parsed = read_json(incoming / name)
        except FileNotFoundError:
            continue
        received[name] = parsed
        with (output / name).open("xb") as stream:
            stream.write(raw)
        (output / name).chmod(0o600)
    with (output / "container.json").open("x", encoding="utf-8") as stream:
        json.dump({
            "schema": 1, "source_commit": head, "pr_head_sha": pr_head or None,
            "image_id": image_id, "attach_exit": int(attached),
            "container_exit": int(runtime), "external_rescue": running == "true",
            "network": "none", "host_mounts": False,
            "formal_i6_passed": False, "n07_assertions_verified": [],
        }, stream, sort_keys=True)
        stream.write("\n")
    if set(received) != set(NAMES):
        return False
    source = evidence.source_snapshot(source_root)
    report = received["n07-b1-validation.json"]
    native = received["n07-b1-native.json"]
    environment = received["n07-b1-environment.json"]
    if (source["dirty"] or source["commit"] != head
            or evidence.canonical(report.get("source")) != evidence.canonical(source)
            or report.get("status") != "probe_only"
            or report.get("probe_status") != "probe_only"
            or report.get("host_v3_acceptance_passed") is not False
            or report.get("verified_n07_assertions") != []
            or report.get("exit_code") != 0
            or report.get("reason") != "N07_B1_DIAGNOSTIC_ONLY"
            or int(attached) != 0 or int(runtime) != 0
            or running != "false"
            or environment.get("run_id") != native.get("run_id")
            or environment.get("source_sha256") != source["tree_sha256"]
            or environment.get("runner", {}).get("source_sha256") != source["tree_sha256"]
            or native.get("source_sha256") != source["tree_sha256"]):
        return False
    outcome = inspect(native)
    if outcome != report.get("probe") or outcome["status"] != "probe_only":
        return False
    census = environment.get("final")
    if not isinstance(census, dict) or set(census) != {
            "app_process_count", "driver_child_count", "sshd_child_count",
            "journal_entries", "journal_lock_held", "profile_lock_held"}:
        return False
    if (any(type(census[field]) is not int or census[field] != 0
            for field in ("app_process_count", "driver_child_count",
                          "sshd_child_count", "journal_entries"))
            or census["journal_lock_held"] is not False
            or census["profile_lock_held"] is not False):
        return False
    adversarial_checks(native)
    return True


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 9:
        print("N07-B1 exporter requires 9 arguments", file=sys.stderr)
        return 2
    incoming, output, image_file = map(Path, args[:3])
    try:
        ok = export_and_validate(
            incoming, output, image_file, *args[3:7], Path(args[7]), args[8])
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        ok = False
    if not ok:
        print("N07-B1 diagnostic export/revalidation rejected; N07 0/13",
              file=sys.stderr)
        return 1
    print("N07-B1 actual pointer/popup/readback diagnostic revalidated; "
          "N07 assertions 0/13, formal I6 incomplete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
