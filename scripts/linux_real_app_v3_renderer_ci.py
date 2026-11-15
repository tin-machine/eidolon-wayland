#!/usr/bin/env python3
"""Copy only fixed bounded T receipts and revalidate against the actual checkout."""
import json
from pathlib import Path
import re
import subprocess
import sys

from linux_real_app_v3_evidence import parse, read_report, source_input_hashes, source_snapshot, write_report
from linux_real_app_v3_renderer import need, same_typed, validate

FILES = ("renderer-native.json", "renderer-unavailable.json", "renderer-environment.json", "renderer-validation.json")


def export(incoming, output, image_path, head, attached, exited, running, source_root, pr_head):
    source = source_snapshot(source_root)
    need(source["commit"] == head and source["dirty"] is False, "I6_SOURCE_MISMATCH")
    need((attached, exited, running) == ("0", "0", "false"), "I6_RENDERER_CONTAINER_FAILED")
    image = image_path.read_text(encoding="ascii").strip()
    need(re.fullmatch(r"sha256:[0-9a-f]{64}", image) is not None)
    if pr_head:
        need(re.fullmatch(r"[0-9a-f]{40}", pr_head) is not None)
        subprocess.run(["git", "-C", str(source_root), "merge-base", "--is-ancestor", pr_head, head],
                       check=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    values = {name: parse(read_report(incoming / name)) for name in FILES}
    inputs = source_input_hashes()
    environment = values["renderer-environment.json"]
    need(environment["source"] == source and environment["inputs"] == inputs
         and environment["uid"] == 12001 and len(environment["renderer_packages"]) == 4,
         "I6_SOURCE_MISMATCH")
    need(same_typed(environment["environment"], {
        "LIBGL_ALWAYS_SOFTWARE": "1", "GALLIUM_DRIVER": "llvmpipe",
        "MESA_LOADER_DRIVER_OVERRIDE": "llvmpipe", "EGL_PLATFORM": "surfaceless"}),
        "I6_RENDERER_ENVIRONMENT_MISMATCH")
    checked = validate(values["renderer-native.json"], source, inputs, values["renderer-unavailable.json"])
    need(same_typed(checked, values["renderer-validation.json"]), "I6_RENDERER_SAVED_VERDICT_MISMATCH")
    need(source_snapshot(source_root) == source, "I6_SOURCE_MISMATCH")
    for name, value in values.items():
        write_report(output / name, value)
    container = {"schema": 1, "image_id": image, "source": source,
                 "pr_head": pr_head or None, "checkout_commit": head,
                 "container_exit": 0, "fixture_rescue": "not_needed",
                 "renderer_t_passed": True, "host_v3_acceptance_passed": False,
                 "environment_disposal": "not_yet"}
    write_report(output / "renderer-container.json", container)
    print(json.dumps(container, sort_keys=True))


def main():
    if len(sys.argv) != 10:
        return 2
    incoming, output, image, head, attached, exited, running, source_root, pr_head = sys.argv[1:]
    try:
        export(Path(incoming), Path(output), Path(image), head, attached, exited, running, Path(source_root), pr_head)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        # Keep only bounded fixed-name diagnostics on failure. No saved success
        # verdict is exported unless it passed the outer independent validator.
        for name in FILES[:3]:
            try:
                value = parse(read_report(Path(incoming) / name))
                if not (Path(output) / name).exists():
                    write_report(Path(output) / name, value)
            except (OSError, ValueError, KeyError, TypeError):
                pass
        try:
            write_report(Path(output) / "renderer-container.json", {
                "schema": 1, "renderer_t_passed": False, "host_v3_acceptance_passed": False,
                "reason": "I6_RENDERER_CI_VALIDATION_FAILED", "environment_disposal": "not_yet"})
        except (OSError, ValueError):
            pass
        print("I6_RENDERER_CI_VALIDATION_FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
