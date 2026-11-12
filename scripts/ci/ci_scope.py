#!/usr/bin/env python3
"""Select expensive CI jobs using the actual Git diff, without third-party Actions.

Unknown inputs fail open to full testing. Intentionally inert Markdown documents
are the only broad exclusion; these are NOT a general "*.md" ignore rule.
"""
import json
import os
import re
import subprocess
import sys

JOBS = ("host", "android", "settings", "mousepad")
ALL = frozenset(JOBS)
SHA = re.compile(r"[0-9a-f]{40}\Z")


def classify(paths):
    """Return impacted jobs for all changed filenames (including rename sources)."""
    changed = list(paths)
    if not changed:
        return set(ALL)
    jobs = set()
    for path in changed:
        if not path or path.startswith("/") or ".." in path.split("/"):
            return set(ALL)
        if path in ("README.md", ".github/SECURITY.md"):
            continue
        if path.startswith("docs/design/"):
            jobs.add("host")  # scripts/check-design reads these files.
        elif path.startswith("docs/") and path.endswith(".md"):
            continue  # other Markdown is not an input; JSON fixtures are.
        elif path == ".github/workflows/check.yml":
            jobs.update(("host", "android"))
        elif path == ".github/workflows/linux-real-app.yml":
            jobs.update(("host", "settings", "mousepad"))  # actionlint in host
        elif path.startswith("android/"):
            jobs.add("android")
            if path.startswith("android/gradle/") or path in ("android/gradlew", "android/gradlew.bat"):
                jobs.add("host")  # scripts/check-build-tools verifies wrapper.
        else:
            return set(ALL)  # native, scripts, CI, Cargo, notices, unknown paths.
    return jobs


def decode_name_status(raw):
    """Parse git --name-status -z and include old+new rename/copy paths."""
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    paths = []
    i = 0
    while i < len(fields):
        status = fields[i].decode("ascii")
        i += 1
        if not status or status[0] not in "ACDMRTUXB":
            raise ValueError("unexpected git diff status")
        count = 2 if status[0] in "RC" else 1
        if i + count > len(fields):
            raise ValueError("truncated git diff record")
        paths.extend(os.fsdecode(item) for item in fields[i:i + count])
        i += count
    return paths


def diff_paths(event, before, after):
    if event not in ("push", "pull_request"):
        return None  # manual dispatch or unknown events => full testing
    if not SHA.fullmatch(before) or not SHA.fullmatch(after) or before == "0" * 40:
        return None
    rev = before + ("..." if event == "pull_request" else "..") + after
    try:
        raw = subprocess.check_output(
            ["git", "diff", "--name-status", "-z", "--find-renames", rev],
            stderr=subprocess.DEVNULL, timeout=30)
        return decode_name_status(raw) or None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, UnicodeError):
        return None  # missing objects / ambiguous diff => full testing


def main():
    event = os.environ.get("CI_EVENT", "")
    before = os.environ.get("CI_BASE_SHA", "")
    after = os.environ.get("CI_HEAD_SHA", "")
    changed = diff_paths(event, before, after)
    selected = classify(changed) if changed is not None else set(ALL)
    outputs = {job: str(job in selected).lower() for job in JOBS}
    print(json.dumps({"event": event, "paths": changed, "jobs": outputs}, ensure_ascii=False))
    out_file = os.environ.get("GITHUB_OUTPUT")
    if out_file:
        with open(out_file, "a", encoding="utf-8") as out:
            for job, value in outputs.items():
                out.write(f"{job}={value}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
