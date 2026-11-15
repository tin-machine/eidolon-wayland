"""N01 disposable-container supervisor, separate from the normal acceptance CLI.

Only an explicitly selected, fresh Docker fixture may create accounts or start
sshd. Product execution is unprivileged and uses the existing immutable builder
and normal Registry. The root supervisor records independent filesystem/process
facts; it never supplies a product reclamation proof or modifies C2.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import selectors
import signal
import socket
import stat
import subprocess
import sys
import time

import linux_desktop_v3_contract as contract
from linux_desktop_v3_configuration import ConnectionConfig
import linux_real_app_inspect as inspect
import linux_real_app_v3_evidence as evidence

ROOT = Path(__file__).resolve().parents[1]
ACCOUNTS = {"driver": (12001, "/home/eidolon-driver"), "app": (12002, "/home/eidolon-app")}
MAX_OUTPUT = 1024 * 1024
FIXTURE_REASONS = frozenset({
    "I6_ENVIRONMENT_UNAVAILABLE", "I6_SOURCE_DIRTY", "I6_SOURCE_UNAVAILABLE",
    "I6_SOURCE_MISMATCH", "I6_WATCHDOG", "I6_DRIVER_DIED", "I6_EVIDENCE_MISSING",
    "I6_ASSERTION_FAILED", "I6_CLEANUP_FAILED", "I6_OPERATOR_CANCELLED",
})


class FixtureError(ValueError):
    """A fixed public reason; command output and private paths never escape."""


def need(condition, reason="I6_ENVIRONMENT_UNAVAILABLE"):
    if not condition:
        raise FixtureError(reason)


def clean_environment(home):
    return {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1", "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": str(ROOT)}


def read_owned(path, uid, *, limit=MAX_OUTPUT, private=True):
    """Hold no-follow ancestors and reject links, devices and changing files."""
    path = Path(path)
    parent = inspect._open_absolute_directory(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            need(stat.S_ISREG(before.st_mode) and before.st_uid == uid and before.st_nlink == 1
                 and not before.st_mode & (0o077 if private else 0o022)
                 and 0 <= before.st_size <= limit, "I6_EVIDENCE_MISSING")
            data = stream.read(limit + 1)
            after = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            need(len(data) == before.st_size and inspect._identity(before) == inspect._identity(after)
                 == inspect._identity(os.fstat(stream.fileno())), "I6_EVIDENCE_MISSING")
            return data
    finally:
        os.close(parent)


def exclusive(path, data, *, uid=0, gid=0, mode=0o600):
    parent = inspect._open_absolute_directory(Path(path).parent)
    try:
        fd = os.open(Path(path).name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     mode, dir_fd=parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fchown(stream.fileno(), uid, gid)
            os.fsync(stream.fileno())
        os.fsync(parent)
    finally:
        os.close(parent)


def fresh_directory(path, *, uid=0, gid=0):
    path = Path(path)
    need(path.is_absolute() and path.resolve() == path)
    parent = inspect._open_absolute_directory(path.parent)
    try:
        os.mkdir(path.name, 0o700, dir_fd=parent)
        fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        try:
            os.fchown(fd, uid, gid)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(parent)
    finally:
        os.close(parent)
    return path


def run_capture(argv, *, uid=None, home="/", seconds=30, pass_fds=(), input_bytes=None, diagnostic=None):
    """Bound retained bytes and time, with a live pidfd for the owned child.

    Unexpected children are never promoted to product cleanup. The disposable
    container owns any provisioning rescue; process counts are taken separately.
    """
    options = dict(stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT,
                   env=clean_environment(home), pass_fds=pass_fds, start_new_session=True)
    if uid is not None:
        options.update(user=uid, group=uid, extra_groups=[])
    child = subprocess.Popen([str(a) for a in argv], **options)
    try:
        pidfd = os.pidfd_open(child.pid)
    except OSError:
        # The Popen child is still owned and unreaped; no PID from a saved file
        # or census is used. Refuse a fixture without live pidfd support.
        child.kill()
        child.wait(timeout=2)
        child.stdout.close()
        child.stderr.close()
        if child.stdin is not None:
            child.stdin.close()
        raise FixtureError("I6_ENVIRONMENT_UNAVAILABLE") from None
    selector = selectors.DefaultSelector()
    stdout = bytearray()
    total = 0
    try:
        if input_bytes is not None:
            need(len(input_bytes) <= 16384)
            child.stdin.write(input_bytes)
            child.stdin.close()
        for name in ("stdout", "stderr"):
            stream = getattr(child, name)
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        deadline = time.monotonic() + seconds
        while selector.get_map() or child.poll() is None:
            need(time.monotonic() < deadline, "I6_WATCHDOG")
            for key, _ in selector.select(0.05):
                data = os.read(key.fd, 16384)
                if not data:
                    selector.unregister(key.fd)
                    continue
                total += len(data)
                need(total <= MAX_OUTPUT, "I6_EVIDENCE_MISSING")
                if key.data == "stdout":
                    stdout.extend(data)
                elif diagnostic is not None:
                    diagnostic.extend(data)
                    del diagnostic[:-2048]
        return child.wait(timeout=1), bytes(stdout)
    finally:
        if child.poll() is None:
            signal.pidfd_send_signal(pidfd, signal.SIGTERM)
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                child.wait(timeout=2)
        selector.close()
        os.close(pidfd)
        child.stdout.close()
        child.stderr.close()


def command(argv, **kwargs):
    code, output = run_capture(argv, **kwargs)
    need(code == 0)
    return output


def account_observation(role, *, fresh):
    uid, expected_home = ACCOUNTS[role]
    row = pwd.getpwnam("eidolon-" + role)
    need(row.pw_uid == uid and row.pw_gid == uid and row.pw_dir == expected_home)
    ancestors = []
    for path in (Path("/"), Path("/home")):
        item = path.lstat()
        need(stat.S_ISDIR(item.st_mode) and item.st_uid == 0 and not item.st_mode & 0o022)
        ancestors.append({"uid": item.st_uid, "mode": stat.S_IMODE(item.st_mode)})
    home = Path(row.pw_dir)
    fd = inspect._open_absolute_directory(home)
    try:
        info = os.fstat(fd)
        need(info.st_uid == uid and stat.S_IMODE(info.st_mode) == 0o700)
    finally:
        os.close(fd)
    return {"uid": uid, "passwd_home": row.pw_dir, "home_uid": info.st_uid,
            "home_mode": stat.S_IMODE(info.st_mode), "ancestors": ancestors, "fresh_home": fresh}


def disposable_preflight(explicit):
    need(explicit and sys.platform == "linux" and os.getresuid() == (0, 0, 0))
    marker = Path("/.dockerenv").lstat()
    need(stat.S_ISREG(marker.st_mode) and marker.st_uid == 0 and not marker.st_mode & 0o022)
    need(not os.path.lexists("/var/run/docker.sock") and not os.path.lexists("/run/docker.sock"))
    for role, (uid, home) in ACCOUNTS.items():
        try:
            pwd.getpwnam("eidolon-" + role)
        except KeyError:
            pass
        else:
            raise FixtureError("I6_ENVIRONMENT_UNAVAILABLE")
        try:
            pwd.getpwuid(uid)
        except KeyError:
            pass
        else:
            raise FixtureError("I6_ENVIRONMENT_UNAVAILABLE")
        need(not os.path.lexists(home))
    return {"kind": "docker", "pid_namespace": str(Path("/proc/self/ns/pid").stat().st_ino),
            "mount_namespace": str(Path("/proc/self/ns/mnt").stat().st_ino),
            "docker_marker": True, "docker_socket_present": False}


def prepare_accounts():
    result = {}
    for role, (uid, home) in ACCOUNTS.items():
        # A random, unusable-by-disclosure password hash keeps public-key account
        # checks normal. Password and keyboard-interactive auth are disabled.
        password_hash = command(["/usr/bin/openssl", "passwd", "-6", "-stdin"],
                                input_bytes=secrets.token_hex(32).encode() + b"\n").decode().strip()
        need(re.fullmatch(r"\$6\$[./A-Za-z0-9$]+", password_hash) is not None)
        command(["/usr/sbin/useradd", "--uid", str(uid), "--user-group", "--home-dir", home,
                 "--no-create-home", "--no-log-init", "--shell", "/bin/sh", "--password", password_hash,
                 "eidolon-" + role])
        fresh_directory(Path(home), uid=uid, gid=uid)
        need(not list(Path(home).iterdir()))
        result[role] = account_observation(role, fresh=True)
    return result


def prepare_base_bundle(app_home):
    """Install only verified source-built inputs into this fresh account."""
    uid = ACCOUNTS["app"][0]
    metadata = evidence.parse((ROOT / "native/generated/waypipe-host.json").read_bytes())
    need(metadata["target"] == "host" and metadata["version"] == "0.11.3")
    waypipe = Path(metadata["executable"])
    need(waypipe.is_absolute() and waypipe.is_relative_to(ROOT))
    waypipe_bytes = read_owned(waypipe, 0, limit=64 * 1024 * 1024, private=False)
    need(evidence.sha(waypipe_bytes) == metadata["sha256"], "I6_SOURCE_MISMATCH")
    for suffix in (".local", ".local/lib", ".local/lib/eidolon-linux"):
        fresh_directory(app_home / suffix, uid=uid, gid=uid)
    directory = app_home / ".local/lib/eidolon-linux"
    contents = {
        "eidolon-linux-session.py": read_owned(ROOT / "scripts/linux/eidolon-linux-session.py", 0, private=False),
        "waypipe": waypipe_bytes,
        "eidolon-shm-fixture": read_owned(ROOT / "target/debug/eidolon-shm-fixture", 0,
                                          limit=64 * 1024 * 1024, private=False),
    }
    for name, data in contents.items():
        exclusive(directory / name, data, uid=uid, gid=uid, mode=0o600 if name.endswith(".py") else 0o700)
    manifest = {"schema": 1, "backend": "linux_posix", "revision": "eidolon-linux-shm-v1",
                "files": {name: evidence.sha(data) for name, data in contents.items()}}
    exclusive(directory / "bundle.json", evidence.canonical(manifest), uid=uid, gid=uid)
    return waypipe_bytes


def export_bundle(build_id, app_home):
    raw = command(["/usr/bin/python3", ROOT / "scripts/build-linux-desktop-v3-bundle",
                   "--print-config", "--build-id", build_id], uid=ACCOUNTS["app"][0], home=app_home, seconds=60)
    config = ConnectionConfig.parse(raw)
    pins = config.pins()
    need(pins.build_id() == build_id, "I6_SOURCE_MISMATCH")
    inputs = {**evidence.source_input_hashes(), "bundle_build_id": build_id,
              "bundle_sha256": pins.record()["desktop_bundle_sha256"], "pins": pins.record()}
    return config.record(), inputs


def prepare_sshd(fixture, app_home):
    for name in ("host-key", "client-key"):
        command(["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", fixture / name])
    uid = ACCOUNTS["app"][0]
    fresh_directory(app_home / ".ssh", uid=uid, gid=uid)
    exclusive(app_home / ".ssh/authorized_keys", (fixture / "client-key.pub").read_bytes(), uid=uid, gid=uid)
    parts = (fixture / "host-key.pub").read_text().split()
    need(len(parts) >= 2 and parts[0] == "ssh-ed25519")
    host_key = " ".join(parts[:2])
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    rows = ["Port " + str(port), "ListenAddress 127.0.0.1", "AddressFamily inet",
            "HostKey " + str(fixture / "host-key"), "PidFile " + str(fixture / "sshd.pid"),
            "AuthorizedKeysFile .ssh/authorized_keys", "UsePAM no", "PasswordAuthentication no",
            "KbdInteractiveAuthentication no", "PubkeyAuthentication yes", "StrictModes yes",
            "PermitRootLogin no", "AllowUsers eidolon-app", "PermitUserEnvironment no",
            "AllowAgentForwarding no", "X11Forwarding no", "PermitTTY no", "AllowTcpForwarding no",
            "AllowStreamLocalForwarding yes", "StreamLocalBindUnlink no", "StreamLocalBindMask 0177",
            "LogLevel ERROR", "MaxStartups 4", "MaxSessions 4"]
    exclusive(fixture / "sshd_config", ("\n".join(rows) + "\n").encode())
    Path("/run/sshd").mkdir(mode=0o755, exist_ok=True)
    command(["/usr/sbin/sshd", "-t", "-f", fixture / "sshd_config"])
    child = subprocess.Popen(["/usr/sbin/sshd", "-D", "-e", "-f", str(fixture / "sshd_config")],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             env=clean_environment("/"), start_new_session=True)
    pidfd = os.pidfd_open(child.pid)
    try:
        deadline = time.monotonic() + 5
        while True:
            need(child.poll() is None, "I6_ENVIRONMENT_UNAVAILABLE")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                need(time.monotonic() < deadline, "I6_WATCHDOG")
                time.sleep(0.02)
    except BaseException:
        stop_owned(child, pidfd)
        raise
    fingerprint = evidence.sha(host_key.encode("ascii"))
    return child, pidfd, host_key, {"listen_host": "127.0.0.1", "port": port,
            "host_key_sha256": fingerprint, "pinned_host_key_sha256": fingerprint, "dedicated": True}


def stop_owned(child, pidfd):
    try:
        if child.poll() is None:
            signal.pidfd_send_signal(pidfd, signal.SIGTERM)
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                child.wait(timeout=3)
    finally:
        os.close(pidfd)


def process_census(sshd_pid):
    """Read only: never signal a PID obtained from this scan."""
    processes = {}
    candidates = [p for p in Path("/proc").iterdir() if p.name.isdecimal()]
    need(len(candidates) <= 4096, "I6_EVIDENCE_MISSING")
    for directory in candidates:
        try:
            raw = (directory / "stat").read_bytes()
            tail = raw[raw.rfind(b")") + 2:].split()
            uid_lines = [line.split()[1:] for line in (directory / "status").read_bytes().splitlines()
                         if line.startswith(b"Uid:")]
            need(len(uid_lines) == 1 and len(uid_lines[0]) == 4, "I6_EVIDENCE_MISSING")
            processes[int(directory.name)] = (int(tail[1]), tuple(map(int, uid_lines[0])))
        except (FileNotFoundError, ProcessLookupError):
            continue
    descendants = {sshd_pid}
    for _ in range(len(processes) + 1):
        expanded = descendants | {pid for pid, (ppid, _) in processes.items() if ppid in descendants}
        if expanded == descendants:
            break
        descendants = expanded
    return {"app_process_count": sum(ACCOUNTS["app"][0] in ids for _, ids in processes.values()),
            "driver_child_count": sum(ACCOUNTS["driver"][0] in ids for _, ids in processes.values()),
            "sshd_child_count": len(descendants - {sshd_pid})}


def settled_census(sshd):
    """Allow only OS reaping to settle; no signals or product retry/deadline change."""
    deadline = time.monotonic() + 3
    while True:
        need(sshd.poll() is None, "I6_EVIDENCE_MISSING")
        counts = process_census(sshd.pid)
        if not any(counts.values()) or time.monotonic() >= deadline:
            return counts
        time.sleep(0.02)


def filesystem_census(files, app_home, installation_id, storage_id):
    # Existence alone does not prove finalization. These independent observations
    # supplement (and never replace) the same-capture native finalizer receipt.
    state = files / "linux-desktop-v3-state"
    fd = inspect._open_absolute_directory(state)
    try:
        entries = os.listdir(fd)
        need({"io.lock", "owner.lock"} <= set(entries) <= {"io.lock", "owner.lock", "pending.json"},
             "I6_EVIDENCE_MISSING")
        journal_entries = sum(name == "pending.json" for name in entries)
        journal_held = False
        for name in ("io.lock", "owner.lock"):
            observed = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
            try:
                info = os.fstat(observed)
                need(stat.S_ISREG(info.st_mode) and info.st_uid == ACCOUNTS["driver"][0] and info.st_nlink == 1
                     and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size == 0, "I6_EVIDENCE_MISSING")
                try:
                    fcntl.flock(observed, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    journal_held = True
                else:
                    fcntl.flock(observed, fcntl.LOCK_UN)
            finally:
                os.close(observed)
    finally:
        os.close(fd)
    profile = app_home / ".local/share/eidolon/apps" / installation_id / storage_id / contract.EDITOR
    parent = inspect._open_absolute_directory(profile)
    lock_fd = os.open("profile.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
    home_fd = inspect._open_absolute_directory(app_home)
    held = False
    try:
        info = os.fstat(lock_fd)
        need(stat.S_ISREG(info.st_mode) and info.st_uid == ACCOUNTS["app"][0] and info.st_nlink == 1
             and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size == 0, "I6_EVIDENCE_MISSING")
        for observed in (home_fd, lock_fd):
            try:
                fcntl.flock(observed, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                held = True
            else:
                fcntl.flock(observed, fcntl.LOCK_UN)
    finally:
        os.close(home_fd)
        os.close(lock_fd)
        os.close(parent)
    return {"journal_entries": journal_entries, "profile_lock_held": held, "journal_lock_held": journal_held}


def owner_from_native(native, owner_id):
    """Map raw product identifiers without inventing a local Session/origin."""
    capture = next((event["capture"] for event in native["trace"]["events"] if event["capture"] is not None), None)
    connect = next((item["data"]["request_id"] for item in native["observations"]
                   if item["kind"] == "command_accepted" and item["data"]["command"] == "connect"), None)
    need(capture is not None and connect is not None, "I6_EVIDENCE_MISSING")
    need(native["owner_id"] == owner_id, "I6_EVIDENCE_MISSING")
    return {"owner_id": owner_id, "runtime": native["runtime"],
            # Host fixture ordinal. Android's admission epoch is outside N01.
            "admission_epoch": "1", "connect_request": connect,
            "launch_request": None, "session": capture["session"], "capture_sha256": capture["capture_id"],
            "context_sha256": capture["context_digest"], "io_generation": capture["io_generation"],
            "nonce": capture["nonce"], "scope": capture["scope"], "native_origin": None}


def assemble_report(source, after, native, environment, artifacts):
    """The producer only references facts; the independent oracle decides N01."""
    from linux_real_app_v3_n01_oracle import REVISION, verify_receipt

    owner = owner_from_native(native, environment["owner_id"])
    report = evidence.incomplete_report(source, run_id=environment["run_id"], reason="I6_NOT_RUN")
    report["source"]["after"] = after
    receipt = {"schema": 1, "kind": REVISION, "run_id": report["run_id"], "case_id": "N01",
               "variant": "inspect_only", "source_sha256": source["tree_sha256"], "owner": owner,
               "native": native, "environment": environment}
    oracle = verify_receipt(receipt, expected_run_id=report["run_id"],
                            expected_source_sha256=source["tree_sha256"], expected_owner=owner,
                            expected_source=source)
    evidence.write_report(artifacts / "n01-receipt.json", receipt)
    raw = evidence.read_report(artifacts / "n01-receipt.json")
    reference = {"artifact": "n01-receipt.json", "owner_id": owner["owner_id"],
                 "target_id": None, "event_seq": None}
    report["artifacts"].append({"path": reference["artifact"], "size": len(raw), "sha256": evidence.sha(raw),
        "evidence_class": "R", "kind": "n01_receipt", "run_id": report["run_id"], "case_id": "N01",
        "variant": "inspect_only", "owner_id": owner["owner_id"], "target_id": None,
        "source_sha256": source["tree_sha256"]})
    case = next(row for row in report["cases"] if (row["case_id"], row["variant"]) == ("N01", "inspect_only"))
    case.update(status=oracle["status"], reason=oracle["reason"], owners=[owner])
    if oracle["status"] == "pass":
        case["observed_product_outcome"] = "complete"
        case["cleanup"]["product"] = "complete"
    case["assertions"] = [{"assertion_id": name, "status": "pass", "reason": "I6_OK",
                           "evidence_refs": [dict(reference)]} for name in oracle["verified_assertions"]]
    case["cleanup"]["census"] = [{"resource": resource, "owner_id": owner["owner_id"], "target_id": None,
        "state": state, "evidence_refs": [dict(reference)], "missing_reasons": []}
        for resource, state in sorted(oracle["census"].items())]
    # Fixture disposal has not happened yet. The outer Docker owner records it.
    case["cleanup"]["environment_disposal"] = "not_yet"
    return report, oracle


def run_fixture(args, artifacts, source, progress):
    isolation = disposable_preflight(args.disposable_container)
    need(args.source_root == ROOT and not source["dirty"], "I6_SOURCE_DIRTY")
    fixture = fresh_directory(args.fixture_root)
    accounts = prepare_accounts()
    app_home = Path(accounts["app"]["passwd_home"])
    driver_home = Path(accounts["driver"]["passwd_home"])
    driver_uid = accounts["driver"]["uid"]
    waypipe = prepare_base_bundle(app_home)
    build_result = evidence.parse(command(["/usr/bin/python3", ROOT / "scripts/build-linux-desktop-v3-bundle",
        "--reviewed-package", args.reviewed_package], uid=ACCOUNTS["app"][0], home=app_home, seconds=180))
    need(build_result.get("status") == "built")
    configuration, before = export_bundle(build_result["build_id"], app_home)
    driver = fresh_directory(driver_home / "fixture", uid=driver_uid, gid=driver_uid)
    files = fresh_directory(driver / "files", uid=driver_uid, gid=driver_uid)
    libraries = fresh_directory(driver / "lib", uid=driver_uid, gid=driver_uid)
    exclusive(libraries / "libwaypipe_bin.so", waypipe, uid=driver_uid, gid=driver_uid, mode=0o700)
    runner_hash = evidence.sha(read_owned(args.runner, 0, limit=128 * 1024 * 1024, private=False))
    sshd = None
    try:
        sshd, sshd_pidfd, host_key, ssh = prepare_sshd(fixture, app_home)
        run_id = secrets.token_hex(16)
        progress["run_id"] = run_id
        installation, installation_id, storage_id = (secrets.token_hex(16) for _ in range(3))
        descriptor = {"schema": 1, "run_id": run_id, "installation": installation,
            "installation_id": installation_id, "profile_storage_id": storage_id,
            "profile": {"schema": 2, "profile_id": "remote-linux", "installation": installation,
                        "revision": "1", "display_name": "I6 fixture", "backend": "linux_ssh",
                        "host": "127.0.0.1", "port": ssh["port"], "username": "eidolon-app", "host_key": host_key},
            "configuration": configuration, "files_dir": str(files), "native_library_dir": str(libraries)}
        descriptor_path, output_path = driver / "descriptor.json", driver / "native.json"
        exclusive(descriptor_path, evidence.canonical(descriptor), uid=driver_uid, gid=driver_uid)
        credential = os.open(fixture / "client-key", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        diagnostic = bytearray()
        try:
            code, _ = run_capture([args.runner, "--descriptor", descriptor_path, "--credential-fd", str(credential),
                "--output", output_path], uid=driver_uid, home=driver_home, seconds=180,
                pass_fds=(credential,), diagnostic=diagnostic)
        finally:
            os.close(credential)
        if code != 0:
            progress["first_reason"] = "I6_DRIVER_DIED"
            reasons = [line.decode("ascii") for line in diagnostic.splitlines()
                       if re.fullmatch(rb"I6_[A-Z0-9_]{1,64}", line)]
            progress["runner_reason"] = reasons[-1] if reasons else None
        native = evidence.parse(read_owned(output_path, driver_uid))
        # Save the fixed, typed native observations before any postcondition can
        # fail. This diagnostic is opaque unless the full independent receipt
        # is available; it cannot fill an assertion or a missing cleanup proof.
        evidence.write_report(artifacts / "n01-native.json", native)
        progress["native_saved"] = True
        if native.get("error") is not None:
            progress["first_reason"] = "I6_ASSERTION_FAILED"
            reason = native["error"]
            progress["runner_reason"] = reason if type(reason) is str and re.fullmatch(r"I6_[A-Z0-9_]{1,64}", reason) else None
        if native.get("cleanup_error") is not None:
            progress["cleanup_reason"] = "I6_CLEANUP_FAILED"
        owner_id = native["owner_id"]
        # This census precedes fixture sshd termination. A later kill cannot
        # manufacture a clean product observation.
        counts = settled_census(sshd)
        counts.update(filesystem_census(files, app_home, installation_id, storage_id))
        _, after_inputs = export_bundle(build_result["build_id"], app_home)
        need(runner_hash == evidence.sha(read_owned(args.runner, 0, limit=128 * 1024 * 1024, private=False)),
             "I6_SOURCE_MISMATCH")
        environment = {"schema": 1, "revision": "eidolon-i6-n01-environment-v1", "run_id": run_id,
            "owner_id": owner_id, "source_sha256": source["tree_sha256"], **accounts, "ssh": ssh,
            "before": before, "after": after_inputs, "runner": {"source_sha256": source["tree_sha256"],
            "binary_sha256": runner_hash}, "isolation": isolation, "final": counts}
        after_source = evidence.source_snapshot(ROOT)
        report, oracle = assemble_report(source, after_source, native, environment, artifacts)
        if progress["first_reason"] is not None:
            case = report["cases"][0]
            case["status"], case["reason"] = "fail", progress["first_reason"]
            case["first_case_failure"] = {"reason": progress["first_reason"], "owner_id": owner_id, "sequence": None}
        if progress["cleanup_reason"] is not None:
            report["cases"][0]["cleanup_failures"].append(
                {"reason": "I6_CLEANUP_FAILED", "owner_id": owner_id, "sequence": None})
        return report, oracle
    finally:
        # These are fixture-owned only; all product facts above were fixed first.
        if sshd is not None:
            stop_owned(sshd, sshd_pidfd)
        for name in ("client-key", "host-key"):
            path = fixture / name
            if path.is_file() and not path.is_symlink():
                path.unlink()


def execute(args):
    from linux_real_app_check import private_artifact_root

    os.umask(0o077)
    artifacts = private_artifact_root(args.artifact_root)
    source = None
    oracle = None
    progress = {"run_id": None, "first_reason": None, "cleanup_reason": None,
                "runner_reason": None, "native_saved": False}
    fixture_failure = None
    try:
        source = evidence.source_snapshot(ROOT)
        report, oracle = run_fixture(args, artifacts, source, progress)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        reason = str(error) if str(error) in FIXTURE_REASONS else "I6_ENVIRONMENT_UNAVAILABLE"
        fixture_failure = reason
        report = evidence.incomplete_report(source, run_id=progress["run_id"], reason=reason)
        case = report["cases"][0]
        if progress["first_reason"] is not None:
            case.update(status="fail", reason=progress["first_reason"])
            case["first_case_failure"] = {"reason": progress["first_reason"], "owner_id": None, "sequence": None}
        if progress["cleanup_reason"] is not None:
            case["cleanup_failures"].append({"reason": "I6_CLEANUP_FAILED", "owner_id": None, "sequence": None})
    except KeyboardInterrupt:
        report = evidence.incomplete_report(source, run_id=progress["run_id"], reason="I6_OPERATOR_CANCELLED")
        report["status"] = "cancelled"
    if progress["native_saved"] and not report["artifacts"] and source is not None:
        raw = evidence.read_report(artifacts / "n01-native.json")
        report["artifacts"].append({"path": "n01-native.json", "size": len(raw), "sha256": evidence.sha(raw),
            "evidence_class": "R", "kind": "opaque", "run_id": report["run_id"], "case_id": "N01",
            "variant": "inspect_only", "owner_id": None, "target_id": None,
            "source_sha256": source["tree_sha256"]})
    try:
        report["source"]["after"] = evidence.source_snapshot(ROOT)
    except (OSError, ValueError):
        report["source"]["after"] = None
    result = evidence.validate_acceptance(evidence.canonical(report), artifacts, source)
    evidence.write_report(artifacts / "acceptance.json", report)
    evidence.write_report(artifacts / "acceptance-validation.json", result)
    n01_passed = result.get("n01_inspect_only_passed", False)
    n01_result = {"schema": 1, "case_id": "N01", "variant": "inspect_only",
        "status": "pass" if n01_passed else ("fail" if result["exit_code"] == 1 else result["status"]),
        "reason": "I6_OK" if n01_passed else result["reason"], "n01_inspect_only_passed": n01_passed,
        "host_v3_acceptance_passed": False, "source": source,
        "required_families": 28, "required_variants": 87, "required_assertions": 533,
        "oracle": oracle, "acceptance_exit_code": result["exit_code"],
        "fixture_failure": fixture_failure, "runner_reason": progress["runner_reason"],
        "environment_disposal": "not_yet"}
    evidence.write_report(artifacts / "n01-validation.json", n01_result)
    print(json.dumps({k: v for k, v in n01_result.items() if k not in {"oracle", "source"}}, sort_keys=True))
    return 0 if n01_passed else result["exit_code"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disposable-container", action="store_true")
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--fixture-root", type=Path, default=Path("/run/eidolon-i6"))
    parser.add_argument("--reviewed-package", type=Path, default=Path("/opt/i6/reviewed-package.json"))
    parser.add_argument("--runner", type=Path, default=Path("/opt/i6/eidolon-i6-host"))
    parser.add_argument("--artifact-root", type=Path, required=True)
    args = parser.parse_args(argv)
    def cancelled(_signum, _frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, cancelled)
    try:
        return execute(args)
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"schema": 1, "status": "incomplete", "reason": "I6_ENVIRONMENT_UNAVAILABLE",
                          "n01_inspect_only_passed": False, "host_v3_acceptance_passed": False}))
        return 2
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
