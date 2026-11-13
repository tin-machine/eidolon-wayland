#!/usr/bin/env python3
"""Install fixed CI packages with bounded APT/DNS retries and fail-closed updates.

This is for ephemeral Ubuntu GitHub-hosted runners. It does not change APT
sources, disable signature verification, or declare missing packages optional.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

DNS_ATTEMPTS = 2
DNS_PROBE_SECONDS = 5
DNS_HOST_LIMIT = 4
APT_UPDATE_SECONDS = 90
APT_INSTALL_SECONDS = 240
APT_OPTIONS = (
    "-o", "Acquire::Retries=1",
    "-o", "Acquire::http::Timeout=10",
    "-o", "Acquire::https::Timeout=10",
)
PACKAGE_RE = re.compile(r"[a-z0-9][a-z0-9+.-]*\Z")


class AptSetupError(Exception):
    pass


def mirror_hosts(apt_root: Path = Path("/etc/apt")) -> list[str]:
    """Read Ubuntu mirror hostnames, including runner's mirror+file list.

    Do not count unrelated third-party APT sources as an Ubuntu DNS success.
    Only hostnames are returned; no URLs, auth or tokens are logged.
    If sources cannot be inspected, bounded APT commands remain authoritative.
    """
    files = [apt_root / "apt-mirrors.txt", apt_root / "sources.list"]
    files.extend(sorted((apt_root / "sources.list.d").glob("*.sources")))
    files.extend(sorted((apt_root / "sources.list.d").glob("*.list")))
    hosts: list[str] = []
    for path in files:
        try:
            contents = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for line in contents.splitlines():
            # Ignore full-line and inline comments, never include URLs in logs.
            line = line.split("#", 1)[0]
            for match in re.finditer(r"https?://[^\s]+", line):
                try:
                    parsed = urlsplit(match.group(0))
                    host = parsed.hostname
                    # Ubuntu runner images also list third-party package
                    # repositories (e.g. packages.microsoft.com). Resolving
                    # those cannot establish that Ubuntu APT mirrors work.
                    # Only standard Ubuntu mirror domains are preflight
                    # candidates. Unknown custom mirrors fall through to
                    # bounded APT rather than being declared healthy.
                    is_ubuntu_mirror = bool(host) and (
                        host == "ubuntu.com" or host.endswith(".ubuntu.com")
                    )
                except ValueError:
                    host, is_ubuntu_mirror = None, False
                if is_ubuntu_mirror and host not in hosts:
                    hosts.append(host)
    return hosts[:DNS_HOST_LIMIT]


def probe_dns(hosts: list[str], *, run=subprocess.run, pause=time.sleep) -> None:
    """Fail quickly on full mirror DNS outage; partial mirror outage is left to APT."""
    if not hosts:
        print("APT mirror host discovery unavailable; bounded APT checks remain enabled.", flush=True)
        return
    for attempt in range(1, DNS_ATTEMPTS + 1):
        for host in hosts:
            # GNU timeout kills getent even if the runner's resolver hangs.
            result = run(
                ["timeout", "--signal=TERM", "--kill-after=1s", f"{DNS_PROBE_SECONDS}s",
                 "getent", "ahosts", host],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if result.returncode == 0:
                print(f"APT DNS preflight: mirror hostname resolved ({host}).", flush=True)
                return
        if attempt < DNS_ATTEMPTS:
            pause(2)
    raise AptSetupError(
        "APT DNS preflight failed: none of the configured mirror hosts resolved "
        f"after {DNS_ATTEMPTS} attempts ({', '.join(hosts)}). "
        "Check runner DNS/network rather than increasing the test timeout."
    )


def apt_command(operation: str, packages: list[str] = []) -> list[str]:
    if operation not in {"update", "install"}:
        raise ValueError("Unsupported APT operation")
    args = [
        "sudo", "-n", "env", "DEBIAN_FRONTEND=noninteractive",
        "apt-get", *APT_OPTIONS,
    ]
    if operation == "update":
        return [*args, "-o", "APT::Update::Error-Mode=any", "update"]
    return [*args, "install", "-y", *packages]


def bounded_apt(operation: str, packages: list[str] = [], *, run=subprocess.run) -> None:
    seconds = APT_UPDATE_SECONDS if operation == "update" else APT_INSTALL_SECONDS
    command = ["timeout", "--signal=TERM", "--kill-after=5s", f"{seconds}s",
               *apt_command(operation, packages)]
    print(f"APT {operation}: deadline={seconds}s, Acquire::Retries=1", flush=True)
    outcome = run(command, check=False)
    if outcome.returncode:
        if outcome.returncode in {124, 137}:
            raise AptSetupError(f"APT {operation} exceeded its {seconds}s deadline (exit {outcome.returncode}).")
        raise AptSetupError(f"APT {operation} failed (exit {outcome.returncode}); see the APT output above.")


def install(packages: list[str], *, apt_root: Path = Path("/etc/apt"),
            run=subprocess.run, pause=time.sleep) -> None:
    if not packages or any(not PACKAGE_RE.fullmatch(package) for package in packages):
        raise AptSetupError("Expected a non-empty list of literal Ubuntu package names.")
    probe_dns(mirror_hosts(apt_root), run=run, pause=pause)
    bounded_apt("update", run=run)
    bounded_apt("install", packages, run=run)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packages", nargs="+", help="Literal package names, no apt options")
    args = parser.parse_args()
    try:
        install(args.packages)
    except AptSetupError as error:
        print(f"::error title=CI APT dependency setup::{error}", file=sys.stderr)
        return 1
    print(f"APT dependency setup complete ({len(args.packages)} package requests).", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
