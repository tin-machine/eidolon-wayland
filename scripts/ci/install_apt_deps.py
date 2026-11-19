#!/usr/bin/env python3
"""Install fixed CI packages with bounded APT/DNS retries and fail-closed updates.

This is for ephemeral Ubuntu GitHub-hosted runners. It keeps the configured
repositories, package identities and signature verification. A download-only
timeout may temporarily prefer an already configured official Ubuntu archive;
the exact mirror-list bytes are restored before installing cached packages.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

DNS_ATTEMPTS = 2
DNS_PROBE_SECONDS = 5
DNS_HOST_LIMIT = 4
APT_UPDATE_SECONDS = 90
APT_INSTALL_SECONDS = 240
APT_FIRST_DOWNLOAD_SECONDS = 120
APT_INSTALL_RESERVE_SECONDS = 45
APT_KILL_GRACE_SECONDS = 5
MIRROR_CONTROL_SECONDS = 5
MIRROR_LIST_LIMIT = 16 * 1024
RUNNER_MIRROR_PATH = Path("/etc/apt/apt-mirrors.txt")
APT_OPTIONS = (
    "-o", "Acquire::Retries=1",
    "-o", "Acquire::http::Timeout=10",
    "-o", "Acquire::https::Timeout=10",
)
PACKAGE_RE = re.compile(r"[a-z0-9][a-z0-9+.-]*\Z")


class AptSetupError(Exception):
    pass


class AptCommandError(AptSetupError):
    def __init__(self, message: str, returncode: int):
        super().__init__(message)
        self.returncode = returncode


@dataclass(frozen=True)
class MirrorFallback:
    path: Path
    original: bytes
    fallback: bytes


def _mirror_lines(contents: bytes) -> list[tuple[str, int | None]] | None:
    """Accept only the small official runner mirror-list format, never new URLs.

    Unknown metadata may constrain suites/architectures. Leave such lists alone
    rather than silently remove those restrictions while changing priorities.
    """
    if len(contents) > MIRROR_LIST_LIMIT:
        return None
    try:
        lines = contents.decode("ascii").splitlines()
    except UnicodeError:
        return None
    entries = []
    for line in lines:
        active = line.strip()
        if not active or active.startswith("#"):
            continue
        if "#" in active:
            return None
        fields = active.split()
        if len(fields) not in {1, 2}:
            return None
        try:
            uri = urlsplit(fields[0])
            host = uri.hostname or ""
            if (uri.scheme not in {"http", "https"}
                    or uri.username is not None or uri.password is not None
                    or uri.port is not None or uri.query or uri.fragment
                    or uri.path not in {"/ubuntu", "/ubuntu/"}
                    or not re.fullmatch(r"[a-z0-9.-]+", host)
                    or not (host in {"archive.ubuntu.com", "security.ubuntu.com"}
                            or host.endswith(".archive.ubuntu.com"))):
                return None
        except ValueError:
            return None
        priority = None
        if len(fields) == 2:
            match = re.fullmatch(r"priority:([0-9]{1,9})", fields[1])
            if match is None:
                return None
            priority = int(match[1])
        if any(existing[0] == fields[0] for existing in entries):
            return None
        entries.append((fields[0], priority))
    return entries if 1 <= len(entries) <= DNS_HOST_LIMIT else None


def fallback_mirror_bytes(contents: bytes) -> bytes | None:
    entries = _mirror_lines(contents)
    if entries is None or len(entries) < 2:
        return None
    archive = [uri for uri, _ in entries if urlsplit(uri).hostname == "archive.ubuntu.com"]
    if len(archive) != 1:
        return None
    # Only reprioritize the entries that were already present. Other mirrors
    # retain their relative order, including unprioritized entries coming last.
    ranks = sorted({p for _, p in entries if p is not None})
    output = []
    for uri, priority in entries:
        rank = 0 if uri == archive[0] else (
            ranks.index(priority) + 1 if priority is not None else len(ranks) + 1
        )
        output.append(f"{uri}\tpriority:{rank}\n")
    result = "".join(output).encode("ascii")
    if result == contents:
        return None
    return result


def _read_mirror_file(path: Path, *, owner_uid: int = 0) -> tuple[bytes, os.stat_result]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != owner_uid or info.st_mode & 0o022
                or info.st_size > MIRROR_LIST_LIMIT):
            raise AptSetupError("Unsafe runner mirror-list identity.")
        data = os.read(fd, MIRROR_LIST_LIMIT + 1)
        if len(data) != info.st_size or _mirror_identity(os.fstat(fd)) != _mirror_identity(info):
            raise AptSetupError("Runner mirror list changed while reading.")
        return data, info
    finally:
        os.close(fd)


def _mirror_identity(info: os.stat_result) -> tuple[int, ...]:
    # A read can advance atime. Content/ownership changes must still be refused.
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def configured_mirror_fallback(apt_root: Path) -> MirrorFallback | None:
    path = apt_root / "apt-mirrors.txt"
    source_files = [apt_root / "sources.list"]
    source_files.extend(sorted((apt_root / "sources.list.d").glob("*.sources")))
    source_files.extend(sorted((apt_root / "sources.list.d").glob("*.list")))
    referenced = False
    for source in source_files:
        try:
            contents = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        referenced |= any(
            f"mirror+file:{path}" in line.split("#", 1)[0].split()
            for line in contents.splitlines()
        )
    if not referenced:
        return None
    try:
        original, _ = _read_mirror_file(path)
    except (OSError, AptSetupError):
        return None
    fallback = fallback_mirror_bytes(original)
    if fallback is None:
        return None
    # Only safe hostnames/priority numbers and an integrity digest are logged.
    summary = ", ".join(f"{urlsplit(uri).hostname}={priority}"
                        for uri, priority in _mirror_lines(original))
    print(f"APT configured mirror priorities: {summary}; "
          f"bytes={len(original)}, sha256={hashlib.sha256(original).hexdigest()}", flush=True)
    return MirrorFallback(path, original, fallback)


def replace_mirror_file(path: Path, expected_sha256: str, replacement: bytes,
                        *, owner_uid: int = 0) -> None:
    """Privileged, compare-before-replace mutation of one known mirror list."""
    original, before = _read_mirror_file(path, owner_uid=owner_uid)
    if original == replacement:
        return  # A failed preference change may already leave the original intact.
    old_entries, new_entries = _mirror_lines(original), _mirror_lines(replacement)
    if (hashlib.sha256(original).hexdigest() != expected_sha256
            or old_entries is None or new_entries is None
            or {uri for uri, _ in old_entries} != {uri for uri, _ in new_entries}):
        raise AptSetupError("Runner mirror list differs from the validated plan.")
    temporary_fd, temporary_name = tempfile.mkstemp(prefix=".eidolon-apt-", dir=path.parent)
    try:
        with os.fdopen(temporary_fd, "wb") as stream:
            os.fchown(stream.fileno(), before.st_uid, before.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(before.st_mode))
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        current, identity = _read_mirror_file(path, owner_uid=owner_uid)
        if _mirror_identity(identity) != _mirror_identity(before) or current != original:
            raise AptSetupError("Runner mirror list changed before replacement.")
        os.replace(temporary_name, path)
        observed, _ = _read_mirror_file(path, owner_uid=owner_uid)
        if observed != replacement:
            raise AptSetupError("Runner mirror-list replacement was not observed.")
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def _mirror_control(plan: MirrorFallback, *, restore: bool, run=subprocess.run) -> None:
    if plan.path != RUNNER_MIRROR_PATH:
        raise AptSetupError("Mirror mutation is restricted to the configured runner path.")
    expected, replacement = ((plan.fallback, plan.original) if restore
                             else (plan.original, plan.fallback))
    payload = json.dumps({
        "expected_sha256": hashlib.sha256(expected).hexdigest(),
        "replacement_b64": base64.b64encode(replacement).decode("ascii"),
    }).encode("ascii")
    result = run([
        "timeout", "--signal=TERM", "--kill-after=1s", f"{MIRROR_CONTROL_SECONDS}s",
        "sudo", "-n", "python3", str(Path(__file__).resolve()), "--_replace-mirrorlist",
    ], input=payload, check=False)
    if result.returncode:
        action = "restore" if restore else "reprioritize"
        raise AptSetupError(f"Failed to {action} the runner mirror list (exit {result.returncode}).")


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
    if operation not in {"update", "install", "download", "install_cached"}:
        raise ValueError("Unsupported APT operation")
    args = [
        "sudo", "-n", "env", "DEBIAN_FRONTEND=noninteractive",
        "apt-get", *APT_OPTIONS,
    ]
    if operation == "update":
        return [*args, "-o", "APT::Update::Error-Mode=any", "update"]
    if operation == "download":
        return [*args, "install", "--download-only", "-y", *packages]
    if operation == "install_cached":
        return [*args, "install", "--no-download", "-y", *packages]
    return [*args, "install", "-y", *packages]


def bounded_apt(operation: str, packages: list[str] = [], *, run=subprocess.run,
                seconds: int | None = None) -> None:
    if seconds is None:
        seconds = APT_UPDATE_SECONDS if operation == "update" else APT_INSTALL_SECONDS
    command = ["timeout", "--signal=TERM", "--kill-after=5s", f"{seconds}s",
               *apt_command(operation, packages)]
    print(f"APT {operation}: deadline={seconds}s, Acquire::Retries=1", flush=True)
    outcome = run(command, check=False)
    if outcome.returncode:
        if outcome.returncode in {124, 137}:
            raise AptCommandError(
                f"APT {operation} exceeded its {seconds}s deadline (exit {outcome.returncode}).",
                outcome.returncode)
        raise AptCommandError(
            f"APT {operation} failed (exit {outcome.returncode}); see the APT output above.",
            outcome.returncode)


def _remaining_seconds(deadline: float, *, reserve: int = APT_KILL_GRACE_SECONDS,
                       clock=time.monotonic) -> int:
    seconds = math.floor(deadline - clock() - reserve)
    if seconds < 1:
        raise AptSetupError(f"APT install exhausted its {APT_INSTALL_SECONDS}s overall deadline.")
    return seconds


def install_with_mirror_fallback(packages: list[str], plan: MirrorFallback,
                                 *, run=subprocess.run, clock=time.monotonic) -> None:
    """One total install budget; retry downloads only, never a partial dpkg run."""
    deadline = clock() + APT_INSTALL_SECONDS
    print(f"APT install total deadline={APT_INSTALL_SECONDS}s; "
          "download-only timeout permits one configured archive fallback.", flush=True)
    try:
        bounded_apt("download", packages, run=run,
                    seconds=min(APT_FIRST_DOWNLOAD_SECONDS,
                                _remaining_seconds(deadline, clock=clock)))
    except AptCommandError as error:
        # Do not turn a package/signature/permission failure into a retry success.
        if error.returncode != 124:
            # 137 can also be an unrelated SIGKILL/OOM, not a proven timeout.
            raise
        _remaining_seconds(deadline,
                           reserve=APT_INSTALL_RESERVE_SECONDS + 2 * (MIRROR_CONTROL_SECONDS + 1)
                           + APT_KILL_GRACE_SECONDS, clock=clock)
        print("APT download-only timed out; temporarily preferring the already configured "
              "archive.ubuntu.com, with the original install deadline unchanged.", flush=True)
        fallback_error = None
        try:
            _mirror_control(plan, restore=False, run=run)
            bounded_apt("download", packages, run=run,
                        seconds=_remaining_seconds(
                            deadline, reserve=APT_INSTALL_RESERVE_SECONDS
                            + MIRROR_CONTROL_SECONDS + 1 + APT_KILL_GRACE_SECONDS,
                            clock=clock))
        except AptSetupError as failure:
            fallback_error = failure
            raise
        finally:
            # Even a failed/timed-out mutation may already have replaced the file.
            # Restoration is bounded and compare-before-replace; never overwrite a
            # foreign edit. A restoration failure keeps the whole setup failed.
            try:
                _mirror_control(plan, restore=True, run=run)
            except AptSetupError as restore_error:
                if fallback_error is not None:
                    raise AptSetupError(f"{fallback_error} Restoration also failed: {restore_error}") from fallback_error
                raise
            print("APT configured mirror-list bytes restored.", flush=True)
    bounded_apt("install_cached", packages, run=run,
                seconds=_remaining_seconds(deadline, clock=clock))


def install(packages: list[str], *, apt_root: Path = Path("/etc/apt"),
            run=subprocess.run, pause=time.sleep, clock=time.monotonic) -> None:
    if not packages or any(not PACKAGE_RE.fullmatch(package) for package in packages):
        raise AptSetupError("Expected a non-empty list of literal Ubuntu package names.")
    probe_dns(mirror_hosts(apt_root), run=run, pause=pause)
    bounded_apt("update", run=run)
    fallback = configured_mirror_fallback(apt_root)
    if fallback is None:
        bounded_apt("install", packages, run=run)
    else:
        install_with_mirror_fallback(packages, fallback, run=run, clock=clock)


def main() -> int:
    if sys.argv[1:] == ["--_replace-mirrorlist"]:
        try:
            payload = json.loads(sys.stdin.buffer.read(4 * MIRROR_LIST_LIMIT + 1))
            if (not isinstance(payload, dict)
                    or set(payload) != {"expected_sha256", "replacement_b64"}
                    or not isinstance(payload["expected_sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", payload["expected_sha256"])
                    or not isinstance(payload["replacement_b64"], str)):
                raise ValueError("Invalid mirror-list request")
            replacement = base64.b64decode(payload["replacement_b64"], validate=True)
            replace_mirror_file(RUNNER_MIRROR_PATH, payload["expected_sha256"], replacement)
        except (AptSetupError, OSError, ValueError, TypeError):
            print("CI runner mirror-list mutation refused.", file=sys.stderr)
            return 1
        return 0
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
