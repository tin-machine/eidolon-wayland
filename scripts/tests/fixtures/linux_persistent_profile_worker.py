#!/usr/bin/env python3
"""C1-only real-I/O fixture. Never used or imported by a product helper.

The parent gives this fixture a newly-created private directory below the test
account's actual HOME. Only passwd lookup is redirected to that directory;
UIDs, openat, flock, descendants, pipes and filesystem mutations are real.
"""
from __future__ import annotations

from dataclasses import asdict
import errno
import fcntl
import json
import os
from pathlib import Path
import pwd
import select
import signal
import stat
import subprocess
import sys
import time
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "scripts/linux"), str(ROOT / "scripts")]
import eidolon_linux_persistent_profile as P
import eidolon_linux_process_owner as O
import linux_desktop_v3_contract as C

FIXTURE = json.loads((Path(__file__).parent / "linux-desktop-v3-contract.json").read_bytes())
RECORDS = {row["id"]: row for row in FIXTURE["records"]}
SELF = str(Path(__file__).resolve())


def emit(value):
    print(json.dumps(value, separators=(",", ":")), flush=True)


def require(value, message):
    if not value:
        raise AssertionError(message)


def read_line(stream, timeout=4):
    require(bool(select.select([stream], [], [], timeout)[0]), "fixture pipe timeout")
    value = stream.readline(8193)
    require(bool(value) and len(value) <= 8192, "fixture pipe contents")
    return value


def expect_error(call, code=None):
    try:
        call()
    except P.ProfileError as error:
        require(str(error) in {"APP_STATE_INVALID", "APP_STATE_BUSY", "APP_STATE_RECOVERY_REQUIRED"}, "unbounded error")
        if code is not None:
            require(str(error) == code, "unexpected profile error: " + str(error))
        return error
    raise AssertionError("unsafe operation succeeded")


def values(**changes):
    context = json.loads(RECORDS["context"]["raw"])
    context.update(uid=os.getuid(), **changes)
    context = C.Context.parse(C.canonical_json(context))
    owner = json.loads(RECORDS["owner"]["raw"])
    owner.update({key: context.record()[key] for key in ("uid", "installation_id", "profile_storage_id")})
    owner = C.StorageOwner.parse(C.canonical_json(owner))
    owner.matches_context(context)
    return context, owner


def identity_record(identity):
    # dataclasses.asdict preserves tuples, whereas the existing strict B1 JSON
    # domain deliberately accepts JSON arrays (lists), not Python tuples.
    return json.loads(json.dumps(asdict(identity)))


class TestJournal(P.JournalBarrier):
    """Actual locked, durable test journal; no always-true success adapter.

    This deliberately models only the C1 barrier seam, not the C2 wire or
    receipt schema. A crash while preparing/active remains unknown on reopen.
    """
    def __init__(self, home, context, owner, name="journal", *, initial="quiet"):
        super().__init__(context, owner)
        self._test_path = home / (".c1-test-" + name + ".json")
        self._test_identity = None
        self._test_release_evidence = None
        self._test_release_calls = 0
        self._test_fd = None
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            descriptor = os.open(self._test_path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
        except FileExistsError:
            descriptor = os.open(self._test_path, flags)
            created = False
        self._test_fd = descriptor
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            info = os.fstat(descriptor)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and
                    stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1, "test journal ownership")
            self._test_inode = info.st_dev, info.st_ino
            if created:
                self._write({"context": context.record(), "owner": owner.record(), "phase": initial})
        except BaseException:
            self.close()
            raise

    def _read(self):
        info = os.stat(self._test_path, follow_symlinks=False)
        require((info.st_dev, info.st_ino) == self._test_inode and info.st_nlink == 1,
                "test journal replaced")
        value = C.parse_json(os.pread(self._test_fd, 16385, 0))
        if value.get("context") != self.context.record() or value.get("owner") != self.owner.record():
            raise P.ProfileError("APP_STATE_RECOVERY_REQUIRED")
        return value

    def _write(self, value):
        data = C.canonical_json(value)
        os.lseek(self._test_fd, 0, os.SEEK_SET)
        require(os.write(self._test_fd, data) == len(data), "test journal short write")
        os.ftruncate(self._test_fd, len(data))
        os.fsync(self._test_fd)
        parent = os.open(self._test_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)

    def before_acquire(self):
        value = self._read()
        if value["phase"] != "quiet":
            raise P.ProfileError("APP_STATE_RECOVERY_REQUIRED")
        value["phase"] = "preparing"
        self._write(value)

    def after_acquire(self, identity):
        value = self._read()
        if value["phase"] != "preparing":
            raise P.ProfileError("APP_STATE_RECOVERY_REQUIRED")
        self._test_identity = identity
        value.update(phase="active", identity=identity_record(identity))
        self._write(value)

    def before_release(self, evidence):
        self._test_release_calls += 1
        value = self._read()
        if (value["phase"] not in {"active", "release_authorized"} or evidence.scope != self.context.scope() or
                evidence.identity != self._test_identity or
                C.canonical_json(value["identity"]) != C.canonical_json(identity_record(evidence.identity)) or
                evidence.helper_pid != os.getpid() or evidence.helper_start <= 0 or
                type(evidence.roots) is not int or evidence.roots < 0 or evidence.pipes != 2 * evidence.roots):
            raise P.ProfileError("APP_STATE_RECOVERY_REQUIRED")
        self._test_release_evidence = evidence
        value["phase"] = "release_authorized"
        self._write(value)

    def confirm_released(self, evidence, handle):
        # Authorization is not completion: if C1's post-hook checks/close fail,
        # this method is never called and cold reopen remains recovery-blocked.
        value = self._read()
        require(value["phase"] == "release_authorized" and handle.closed is True and
                evidence == self._test_release_evidence and evidence.identity == handle.identity,
                "test journal cannot commit incomplete release")
        self._write({"context": self.context.record(), "owner": self.owner.record(), "phase": "quiet"})

    def close(self):
        if self._test_fd is not None:
            os.close(self._test_fd)
            self._test_fd = None


class Fixture:
    def __init__(self, home):
        self.home = Path(home)
        original = pwd.getpwuid(os.getuid())
        require(os.getuid() != 0 and os.getuid() == os.geteuid(), "nonroot fixture required")
        require(self.home.is_absolute() and self.home.parent == Path(original.pw_dir), "dedicated test HOME")
        require(self.home.name.startswith("eidolon-c1-test-"), "dedicated test HOME prefix")
        require(stat.S_IMODE(self.home.stat().st_mode) == 0o700 and
                self.home.stat().st_uid == os.getuid(), "private test HOME")
        row = list(original)
        row[5] = str(self.home)
        replacement = pwd.struct_passwd(row)
        actual_lookup = pwd.getpwuid
        self.patch = mock.patch.object(P.pwd, "getpwuid", side_effect=lambda uid:
                                       replacement if uid == os.getuid() else actual_lookup(uid))
        self.patch.start()
        self.context, self.storage_owner = values()
        self.journals = []
        self.process_owners = []
        self.counter = 0

    def start(self, *, context=None, journal_name=None, barrier_class=TestJournal, expected_identity=None, initial="quiet"):
        context = context or self.context
        owner_value = json.loads(RECORDS["owner"]["raw"])
        owner_value.update({key: context.record()[key] for key in ("uid", "installation_id", "profile_storage_id")})
        owner_value = C.StorageOwner.parse(C.canonical_json(owner_value))
        self.counter += 1
        barrier = barrier_class(self.home, context, owner_value, journal_name or str(self.counter), initial=initial)
        self.journals.append(barrier)
        process_owner = O.ProcessOwner()
        self.process_owners.append(process_owner)
        try:
            handle = P.PersistentProfileRepository().acquire(context, process_owner=process_owner,
                barrier=barrier, expected_identity=expected_identity)
        except BaseException:
            process_owner.finish()
            raise
        return process_owner, barrier, handle

    def finish(self, owner, handle):
        result = owner.finish()
        require(result["complete"] and not result["pending"] and result["open_pidfds"] == 0,
                "real process owner remains pending")
        evidence = handle.release()
        require(handle.closed, "released handle not closed")
        self.journals[-1].confirm_released(evidence, handle)
        require(self.journals[-1]._read()["phase"] == "quiet", "release skipped journal barrier")

    def close(self):
        try:
            # Assertions/fault injection cannot abandon a spawned fixture.
            # Process ownership is independent of a failed profile validation.
            for owner in reversed(self.process_owners):
                if owner.result is None:
                    require(owner.finish()["complete"], "fixture descendant cleanup incomplete")
        finally:
            for journal in self.journals:
                journal.close()
            self.patch.stop()


def lock_probe(path, *, busy):
    code = """import fcntl,json,os,sys
fd=os.open(sys.argv[1],os.O_RDWR|os.O_NOFOLLOW)
info=os.fstat(fd)
try:
 fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 locked=False
except BlockingIOError:
 locked=True
print(json.dumps({'busy':locked,'device':info.st_dev,'inode':info.st_ino}))
"""
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(path)],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=4)
    require(result.returncode == 0, "independent lock probe failed: " + result.stderr)
    value = json.loads(result.stdout)
    require(value["busy"] is busy, "independent process observed wrong lock state")
    return value


def replace_from_process(path, *, restore=False):
    code = """import os,pathlib,stat,sys
path=pathlib.Path(sys.argv[1]); old=path.with_name(path.name+'.c1-detached')
if sys.argv[2]=='restore':
 path.rename(path.with_name(path.name+'.c1-replacement'))
 old.rename(path)
else:
 info=path.lstat(); data=path.read_bytes() if stat.S_ISREG(info.st_mode) else None
 path.rename(old)
 if data is None: path.mkdir(mode=0o700)
 else:
  fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  os.write(fd,data);os.close(fd)
"""
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(path),
                             "restore" if restore else "replace"], capture_output=True, text=True, timeout=4)
    require(result.returncode == 0, "replacement subprocess failed: " + result.stderr)


def fixed_child(mode="plain"):
    return [sys.executable, "-I", "-S", "-B", SELF, "--child", mode]


def child(mode):
    # Every descendant has a finite lifetime independent of the tested owner.
    if mode == "tree":
        if os.fork() != 0:
            os._exit(0)
        os.setsid()
        if os.fork() != 0:
            os._exit(0)
    emit({"ready": True})
    time.sleep(8)
    os._exit(0)


def basic(f):
    with mock.patch.dict(os.environ, {"HOME": "/ignored-c1-home", "XDG_DATA_HOME": "/ignored-c1-data",
                                      "XDG_CONFIG_HOME": "/ignored-c1-config"}):
        owner, barrier, handle = f.start()
    expected = f.home / ".local/share/eidolon/apps" / f.context.record()["installation_id"] / \
        f.context.record()["profile_storage_id"] / C.EDITOR
    require(Path(handle.paths.root) == expected, "fixed data key changed")
    require(set(os.listdir(expected)) == {"owner.json", "profile.lock", "home", "config", "data", "state", "Documents"},
            "persistent/ephemeral layout mixed")
    cursor = expected
    while cursor != f.home:
        require(stat.S_IMODE(cursor.stat().st_mode) == 0o700, "private namespace mode")
        cursor = cursor.parent
    for name in ("home", "config", "data", "state", "documents"):
        path = Path(getattr(handle.paths, name))
        require(path.parent == expected and stat.S_IMODE(path.stat().st_mode) == 0o700, "private persistent directory")
    for name in ("owner.json", "profile.lock"):
        info = (expected / name).stat()
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
                "private persistent record")
    written = C.StorageOwner.parse((expected / "owner.json").read_bytes())
    require(written == f.storage_owner and set(written.record()) == set(C.OWNER_FIELDS), "B1 owner contract changed")
    require(handle.environment() == {"HOME": str(handle.paths.home), "XDG_CONFIG_HOME": str(handle.paths.config),
            "XDG_DATA_HOME": str(handle.paths.data), "XDG_STATE_HOME": str(handle.paths.state)}, "persistent environment")
    require(os.umask(0o077) == 0o077, "umask was not retained")
    before = lock_probe(expected / "profile.lock", busy=True)
    handle.validate()
    f.finish(owner, handle)
    after = lock_probe(expected / "profile.lock", busy=False)
    require((before["device"], before["inode"]) == (after["device"], after["inode"]), "lock inode recreated")


def reopen(f):
    owner, _, handle = f.start()
    identity = handle.identity
    root = Path(handle.paths.root)
    retained = {root / "Documents/saved.txt": b"C1 test document\n", root / "config/preferences": b"font=12\n",
                root / "data/autosave": b"do not adopt or delete\n", root / "state/session": b"ambiguous recovery\n"}
    for path, value in retained.items():
        path.write_bytes(value)
    f.finish(owner, handle)
    changed, _ = values(session="4", profile_revision="8", profile_binding="d" * 64, nonce="e" * 16)
    owner, _, reopened = f.start(context=changed, expected_identity=identity)
    require(reopened.identity == identity and Path(reopened.paths.root) == root, "session/revision changed data key")
    # Product cleanup must not recursively inspect/delete saved app data.
    import shutil
    with mock.patch.object(shutil, "rmtree", side_effect=AssertionError("recursive cleanup")), \
            mock.patch.object(Path, "rglob", side_effect=AssertionError("recursive traversal")):
        f.finish(owner, reopened)
    new_context, _ = values(profile_storage_id="d" * 32)
    owner, _, new = f.start(context=new_context)
    require(Path(new.paths.root) != root and not (Path(new.paths.documents) / "saved.txt").exists(),
            "new ID adopted/copied old data")
    f.finish(owner, new)
    require(all(path.read_bytes() == value for path, value in retained.items()), "persistent bytes changed")


def invalid_owner(f):
    owner, _, handle = f.start()
    path = Path(handle.paths.root) / "owner.json"
    original = path.read_bytes()
    identity = handle.identity
    f.finish(owner, handle)
    cases = [row for row in FIXTURE["invalid"] if row["kind"] == "owner"]
    for row in cases:
        raw = bytes.fromhex(row["raw_hex"]) if "raw_hex" in row else row["raw"].encode()
        path.write_bytes(raw)
        expect_error(lambda: f.start(expected_identity=identity))
        require(path.read_bytes() == raw, "invalid owner was overwritten")
    for key, replacement in (("uid", os.getuid() + 1), ("installation_id", "e" * 32),
                             ("profile_storage_id", "f" * 32)):
        value = f.storage_owner.record()
        value[key] = replacement
        raw = C.canonical_json(value)
        path.write_bytes(raw)
        expect_error(lambda: f.start(expected_identity=identity))
        require(path.read_bytes() == raw, "foreign owner was overwritten")
    path.write_bytes(original)
    owner, _, reopened = f.start(expected_identity=identity)
    f.finish(owner, reopened)
    return {"raw_rejections": len(cases), "owner_mismatches": 3}


def replacement(f, name):
    owner, _, handle = f.start()
    root = Path(handle.paths.root)
    path = root if name == "root" else root / name
    replace_from_process(path)
    caught = expect_error(handle.validate)
    require(getattr(caught, "handle", None) is handle and not handle.closed, "validation lost live owner")
    old_lock = path.with_name(path.name + ".c1-detached") / "profile.lock" if name == "root" else \
        root / ("profile.lock.c1-detached" if name == "profile.lock" else "profile.lock")
    lock_probe(old_lock, busy=True)
    require(owner.finish()["complete"], "owner finish failed")
    expect_error(handle.release)
    require(not handle.closed, "replacement released lock")
    # Do not auto-adopt or restore a changed incarnation. The worker exits with
    # a pending journal, held original lock, and no surviving descendants.
    lock_probe(old_lock, busy=True)


def tamper(f, variant):
    owner, _, handle = f.start()
    root, identity = Path(handle.paths.root), handle.identity
    f.finish(owner, handle)
    if variant.startswith("symlink:"):
        relative = variant.split(":", 1)[1]
        path = f.home / relative if relative.startswith(".local") else root / relative
        detached = path.with_name(path.name + ".detached")
        path.rename(detached)
        path.symlink_to(detached, target_is_directory=detached.is_dir())
    elif variant.startswith("hardlink:"):
        path = root / variant.split(":", 1)[1]
        os.link(path, f.home / ".c1-hardlink-alias")
    elif variant.startswith("mode:"):
        path = root / variant.split(":", 1)[1]
        path.chmod(0o755 if path.is_dir() else 0o644)
    elif variant == "missing":
        (root / "owner.json").rename(root / "owner.detached")
    elif variant == "fifo":
        (root / "profile.lock").unlink()
        os.mkfifo(root / "profile.lock", 0o600)
    elif variant == "lock_bytes":
        (root / "profile.lock").write_bytes(b"unowned lock content")
    elif variant == "foreign_uid":
        path = root / "owner.json"
        result = subprocess.run(["sudo", "-n", "chown", "0:0", str(path)], capture_output=True, timeout=3)
        require(result.returncode == 0, "isolated foreign-UID fixture unavailable")
    else:
        raise AssertionError("unknown tamper fixture")
    try:
        expect_error(lambda: f.start(expected_identity=identity))
        require(root.exists(), "invalid persistent root deleted")
    finally:
        if variant == "foreign_uid":
            subprocess.run(["sudo", "-n", "chown", str(os.getuid()) + ":" + str(os.getgid()), str(path)],
                           capture_output=True, timeout=3, check=True)


def partial(f, variant):
    root = f.home / ".local/share/eidolon/apps" / f.context.record()["installation_id"] / \
        f.context.record()["profile_storage_id"] / C.EDITOR
    if variant == "preexisting":
        root.mkdir(mode=0o700, parents=True)
        # pathlib parents use the process umask; make the intentionally owned
        # fixed ancestors private without changing any real account directory.
        cursor = root
        while cursor != f.home:
            cursor.chmod(0o700)
            cursor = cursor.parent
        (root / "do-not-delete").write_bytes(b"unknown original bytes")
        before = tree_snapshot(root)
        expect_error(lambda: f.start())
        require((root / "do-not-delete").read_bytes() == b"unknown original bytes", "unknown root adopted/deleted")
        require(tree_snapshot(root) == before, "unknown tree mutated")
        return
    actual_write, actual_mkdir, actual_open, actual_fsync = os.write, os.mkdir, os.open, os.fsync
    hit = []

    def fail_write(fd, data):
        if str(os.readlink("/proc/self/fd/" + str(fd))).endswith("/owner.json"):
            hit.append(True)
            actual_write(fd, data[:7])
            raise OSError(errno.ENOSPC, "synthetic disk full")
        return actual_write(fd, data)

    def fail_mkdir(path, *args, **kwargs):
        result = actual_mkdir(path, *args, **kwargs)
        if path == "home":
            hit.append(True)
            raise OSError(errno.EIO, "synthetic mkdir handoff failure")
        return result

    def fail_open(path, *args, **kwargs):
        descriptor = actual_open(path, *args, **kwargs)
        if path == "profile.lock":
            hit.append(True)
            os.close(descriptor)
            raise OSError(errno.EIO, "synthetic lock creation handoff failure")
        return descriptor

    def fail_fsync(fd):
        if (Path(os.readlink("/proc/self/fd/" + str(fd))) == root and
                set(os.listdir(fd)) == {"profile.lock", "home", "config", "data", "state", "Documents"}):
            hit.append(True)
            raise OSError(errno.EIO, "synthetic fsync before owner commit")
        return actual_fsync(fd)

    injected = {"write_failure": ("write", fail_write), "mkdir_failure": ("mkdir", fail_mkdir),
                "lock_creation_failure": ("open", fail_open), "fsync_before_owner": ("fsync", fail_fsync)}
    method, failing = injected[variant]
    with mock.patch.object(P.os, method, side_effect=failing):
        expect_error(lambda: f.start())
    require(hit and root.is_dir(), "partial filesystem failure was not injected")
    if variant == "write_failure":
        require((root / "owner.json").read_bytes(), "partial real write was not injected")
    else:
        require(not (root / "owner.json").exists(), "failure occurred after owner commit")
    before = tree_snapshot(root)
    expect_error(lambda: f.start())
    require(tree_snapshot(root) == before, "partial tree repaired/deleted")


def tree_snapshot(root):
    """Fixture oracle only; product cleanup must never traverse this tree."""
    result = {}
    for directory, directories, files in os.walk(root):
        for path in [Path(directory), *(Path(directory) / name for name in files)]:
            info = path.lstat()
            result[str(path.relative_to(root))] = (info.st_dev, info.st_ino, info.st_uid, info.st_mode,
                info.st_nlink, info.st_size, info.st_mtime_ns,
                path.read_bytes() if stat.S_ISREG(info.st_mode) else None)
    return result


def release_conditions(f, variant):
    class FailAfterAuthorization(TestJournal):
        def before_release(self, evidence):
            super().before_release(evidence)
            target = f.home / ".local/share/eidolon/apps" / self.context.record()["installation_id"] / \
                self.context.record()["profile_storage_id"] / C.EDITOR / "profile.lock"
            target.chmod(0o644)

    owner, barrier, handle = f.start(barrier_class=FailAfterAuthorization if variant == "posthook" else TestJournal)
    root = Path(handle.paths.root)
    if variant == "unfinished":
        expect_error(handle.release, "APP_STATE_RECOVERY_REQUIRED")
        lock_probe(root / "profile.lock", busy=True)
        f.finish(owner, handle)
        return
    mode = "tree" if variant == "descendant" else "plain"
    process = owner.spawn(fixed_child(mode), env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
    if variant != "untracked":
        handle.track_process(process)
    require(json.loads(read_line(process.stdout)) == {"ready": True}, "child not ready")
    writer = None
    if variant == "pipe_writer":
        writer = os.open("/proc/" + str(process.pid) + "/fd/1", os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        expect_error(handle.release, "APP_STATE_RECOVERY_REQUIRED")
        lock_probe(root / "profile.lock", busy=True)
        completed = owner.finish()
        require(completed["complete"] and completed["open_pidfds"] == 0, "descendants not reaped")
        if variant == "descendant":
            require(completed["reaped"] >= 2, "escaped child was not observed/reaped")
        if variant == "posthook":
            denied = expect_error(handle.release, "APP_STATE_INVALID")
            require(getattr(denied, "handle", None) is handle and not handle.closed and
                    barrier._read()["phase"] == "release_authorized", "post-hook failure cleared journal")
            lock_probe(root / "profile.lock", busy=True)
            return {"release_denied": True, "journal": "release_authorized"}
        if variant == "barrier":
            record = barrier._read()
            record["phase"] = "receipt_pending"
            barrier._write(record)
            before_calls = barrier._test_release_calls
            denied = expect_error(handle.release, "APP_STATE_RECOVERY_REQUIRED")
            require(barrier._test_release_calls == before_calls + 1 and
                    getattr(denied, "handle", None) is handle and not handle.closed,
                    "release was not denied by the actual journal after EOF checks")
            lock_probe(root / "profile.lock", busy=True)
            record = barrier._read()
            require(record["phase"] == "receipt_pending", "pending receipt was cleared")
            record["phase"] = "active"
            barrier._write(record)
        if variant in {"pipe_writer", "untracked"}:
            expect_error(handle.release, "APP_STATE_RECOVERY_REQUIRED")
            require(not handle.closed, "unproven pipe handoff released lock")
            lock_probe(root / "profile.lock", busy=True)
        if variant == "untracked":
            # The fixture exits with the denied handle; there are no surviving
            # descendants. This is not reported as a successful release.
            return {"release_denied": True, "descendants_complete": True}
        if writer is not None:
            os.close(writer)
            writer = None
        evidence = handle.release()
        require(handle.closed, "EOF-proven handle not released")
        barrier.confirm_released(evidence, handle)
        lock_probe(root / "profile.lock", busy=False)
    finally:
        if writer is not None:
            os.close(writer)
        if owner.result is None:
            owner.finish()


def barrier_denial(f, variant):
    if variant == "unknown":
        expect_error(lambda: f.start(initial="unknown"), "APP_STATE_RECOVERY_REQUIRED")
        require(not (f.home / ".local").exists(), "unknown journal mutated profile")
        return

    class WrongResult(TestJournal):
        def before_acquire(self):
            super().before_acquire()
            return True

    class AfterDenial(TestJournal):
        def after_acquire(self, identity):
            super().after_acquire(identity)
            raise P.ProfileError("APP_STATE_RECOVERY_REQUIRED")

    if variant == "boolean":
        expect_error(lambda: f.start(barrier_class=WrongResult), "APP_STATE_RECOVERY_REQUIRED")
    else:
        error = expect_error(lambda: f.start(barrier_class=AfterDenial), "APP_STATE_RECOVERY_REQUIRED")
        handle = getattr(error, "handle", None)
        require(handle is not None and not handle.closed, "after-acquire denial lost owner")
        lock_probe(Path(handle.paths.root) / "profile.lock", busy=True)


def missing_known(f):
    owner, _, handle = f.start()
    root, identity = Path(handle.paths.root), handle.identity
    (root / "Documents/original").write_bytes(b"preserve original tree")
    f.finish(owner, handle)
    detached = root.with_name(root.name + ".detached")
    root.rename(detached)
    expect_error(lambda: f.start(expected_identity=identity))
    require(not root.exists(), "known missing incarnation recreated")
    require((detached / "Documents/original").read_bytes() == b"preserve original tree", "old incarnation changed")


def hold(f, name, compete=False):
    if compete:
        emit({"event": "prepared"})
        require(read_line(sys.stdin) == "go\n", "missing creation barrier")
    try:
        owner, _, handle = f.start(journal_name=name)
    except P.ProfileError as error:
        emit({"event": "denied", "reason": str(error)})
        return
    emit({"event": "acquired", "root": str(handle.paths.root), "identity": asdict(handle.identity)})
    request = read_line(sys.stdin, timeout=10)
    if request == "release\n":
        f.finish(owner, handle)
        emit({"event": "released"})
    else:
        require(request == "pending\n", "missing release request")
        expect_error(handle.validate)
        require(owner.finish()["complete"], "replacement owner cleanup pending")
        expect_error(handle.release)
        require(not handle.closed, "replacement unlocked owner")
        emit({"event": "pending"})


def borrowed(f):
    owner, _, handle = f.start()
    read, write = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read)
        try:
            expect_error(handle.validate)
            expect_error(handle.release)
            os.write(write, b"rejected")
        finally:
            os._exit(0)
    os.close(write)
    require(os.read(read, 16) == b"rejected", "forked process used borrowed owner handle")
    os.close(read)
    require(os.waitpid(pid, 0)[1] == 0, "fork fixture failed")
    handle.validate()
    lock_probe(Path(handle.paths.root) / "profile.lock", busy=True)
    f.finish(owner, handle)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        child(sys.argv[2])
        return
    case, home = sys.argv[1:3]
    f = Fixture(home)
    try:
        result = None
        if case == "basic":
            basic(f)
        elif case == "reopen":
            reopen(f)
        elif case == "invalid_owner":
            result = invalid_owner(f)
        elif case.startswith("replace:"):
            replacement(f, case.split(":", 1)[1])
        elif case.startswith("tamper:"):
            tamper(f, case.split(":", 1)[1])
        elif case.startswith("partial:"):
            partial(f, case.split(":", 1)[1])
        elif case.startswith("release:"):
            result = release_conditions(f, case.split(":", 1)[1])
        elif case.startswith("barrier:"):
            barrier_denial(f, case.split(":", 1)[1])
        elif case == "borrowed":
            borrowed(f)
        elif case == "missing_known":
            missing_known(f)
        elif case in {"hold", "compete"}:
            hold(f, sys.argv[3], compete=case == "compete")
        else:
            raise AssertionError("unknown fixture case")
        emit({"case": case, "result": "passed", "details": result})
    finally:
        f.close()


if __name__ == "__main__":
    main()
