"""C1: remote persistent profile I/O, without a normal-v3 helper or launch gate.

Only a dedicated, authenticated non-root ProcessOwner may acquire a profile.
C2 must supply a real, reserved journal barrier; no implementation/default is
provided here. Locks are advisory and are not proof that an old owner exited.
See docs/linux-real-app-c1-handoff-20261110.md for the lifetime and failure API.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import errno
import fcntl
import os
from pathlib import Path, PurePosixPath
import pwd
import select
import stat
import sys

import linux_desktop_v3_contract as contract
from eidolon_linux_process_owner import ProcessOwner, OwnershipError, descendant_snapshot


_DIRECTORIES = ("home", "config", "data", "state", "Documents")
_ENTRIES = frozenset((*_DIRECTORIES, "owner.json", "profile.lock"))
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_HANDLE_KEY = object()
# Keep a failed/live capability even if its caller drops the last reference.
# This is a lifetime guard, NOT remote exclusion (both flocks provide that).
_ACTIVE = {}


class ProfileError(RuntimeError):
    """Fixed B1 public reason only. A live capability remains in ``handle``."""

    def __init__(self, reason="APP_STATE_INVALID", *, handle=None):
        if reason not in {"APP_STATE_INVALID", "APP_STATE_BUSY", "APP_STATE_RECOVERY_REQUIRED"}:
            reason = "APP_STATE_INVALID"
        super().__init__(reason)
        self.handle = handle


def _require(value, reason="APP_STATE_INVALID"):
    if not value:
        raise ProfileError(reason)


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int

    def __post_init__(self):
        _require(type(self.device) is int and self.device >= 0 and
                 type(self.inode) is int and self.inode > 0)


def _identity(info):
    return FileIdentity(info.st_dev, info.st_ino)


@dataclass(frozen=True)
class ProfileIdentity:
    """Local I/O identity, not a new owner/wire/journal schema.

    A C2 journal adapter must compare a previously known incarnation. The six
    B1 owner fields alone cannot identify a copied tree on a cold reopen.
    """

    home: FileIdentity
    root: FileIdentity
    lock: FileIdentity
    owner: FileIdentity
    directories: tuple[tuple[str, FileIdentity], ...]

    def __post_init__(self):
        _require(all(type(value) is FileIdentity for value in (self.home, self.root, self.lock, self.owner)))
        _require(type(self.directories) is tuple and len(self.directories) == len(_DIRECTORIES))
        for entry, name in zip(self.directories, _DIRECTORIES):
            _require(type(entry) is tuple and len(entry) == 2 and entry[0] == name and
                     type(entry[1]) is FileIdentity)


@dataclass(frozen=True, repr=False)
class PersistentPaths:
    root: Path
    home: Path
    config: Path
    data: Path
    state: Path
    documents: Path


@dataclass(frozen=True)
class ReleaseEvidence:
    """Computed by release(), never accepted as a caller's cleanup assertion."""

    scope: str
    identity: ProfileIdentity
    helper_pid: int
    helper_start: int
    roots: int
    pipes: int


class JournalBarrier(ABC):
    """Required C2 integration boundary, deliberately without a clean adapter.

    Construct only after reserving the same captured journal/context and proving
    previous owners reclaimed. Preserve that reservation (and durable pending)
    until profile release and C2's own cleanup/receipt transaction completes.
    Hook methods raise on unknown/denied state, return None on verified success,
    and must not spawn processes or relinquish the reservation. A boolean is
    never accepted as proof. This local interface does not define a wire schema.
    """

    def __init__(self, context, owner):
        _require(type(context) is contract.Context and type(owner) is contract.StorageOwner)
        owner.matches_context(context)
        self._context, self._owner = context, owner
        self._pid, self._claim = os.getpid(), None

    @property
    def context(self):
        return self._context

    @property
    def owner(self):
        return self._owner

    @abstractmethod
    def before_acquire(self):
        """Recheck prior recovery/journal evidence before any profile mutation."""

    @abstractmethod
    def after_acquire(self, identity):
        """Recheck reservation and known incarnation after both locks/checks."""

    @abstractmethod
    def before_release(self, evidence):
        """Require C2's durable journal/receipt barrier for this exact evidence."""

    def _bind(self, context, owner):
        _require(self._pid == os.getpid() and self._claim is None and
                 self.context == context and self.owner == owner,
                 "APP_STATE_RECOVERY_REQUIRED")
        self._claim = object()
        return self._claim


def _barrier_call(callback, *args):
    try:
        result = callback(*args)
    except ProfileError:
        raise
    except Exception:
        # Do not publish private journal/path/exception text from an adapter.
        raise ProfileError("APP_STATE_RECOVERY_REQUIRED") from None
    _require(result is None, "APP_STATE_RECOVERY_REQUIRED")


def storage_owner(context):
    """Build and validate the existing B1 six-field record, without new fields."""
    _require(type(context) is contract.Context)
    value = context.record()
    owner = contract.StorageOwner.parse(contract.canonical_json({
        "schema": 1, "uid": value["uid"], "installation_id": value["installation_id"],
        "profile_storage_id": value["profile_storage_id"], "app_id": contract.EDITOR,
        "state_policy": "persistent-keyfile-locked-v1",
    }))
    owner.matches_context(context)
    return owner


def _live_owner(owner, uid):
    _require(type(owner) is ProcessOwner, "APP_STATE_RECOVERY_REQUIRED")
    owner._current()
    _require(owner.owner["uid"] == (uid,) * 4 and owner.owner["pid"] == os.getpid(),
             "APP_STATE_RECOVERY_REQUIRED")


def _fresh_owner(owner, uid):
    _live_owner(owner, uid)
    _require(owner.result is None and not owner.closing and not owner.roots and not owner.handles and
             not descendant_snapshot(owner.owner), "APP_STATE_RECOVERY_REQUIRED")


def _account_home(context):
    uid = context.record()["uid"]
    _require(sys.platform == "linux" and uid != 0 and os.getresuid() == (uid,) * 3)
    account = pwd.getpwuid(uid)
    _require(account.pw_uid == uid and type(account.pw_dir) is str)
    home = PurePosixPath(account.pw_dir)
    _require(home.is_absolute() and home.root == "/" and str(home) == account.pw_dir and
             home != PurePosixPath("/") and "\0" not in account.pw_dir and
             all(part not in (".", "..") for part in home.parts) and
             len(os.fsencode(home)) <= 4096 and len(home.parts) <= 64)
    return uid, Path(home)


def _umask_is_private():
    # Reading proc avoids temporarily changing the process-wide mask to inspect
    # it. ProcessOwner enforces the dedicated single-thread helper assumption.
    descriptor = os.open("/proc/self/status", os.O_RDONLY | os.O_CLOEXEC)
    try:
        raw = os.read(descriptor, 32769)
        _require(len(raw) <= 32768)
    finally:
        os.close(descriptor)
    rows = [row.split() for row in raw.splitlines() if row.startswith(b"Umask:")]
    _require(rows == [[b"Umask:", b"0077"]])


def _directory_info(info, uid, *, private, owned):
    _require(stat.S_ISDIR(info.st_mode) and info.st_nlink > 0)
    _require(info.st_uid == uid if owned else info.st_uid in (0, uid))
    _require(stat.S_IMODE(info.st_mode) == 0o700 if private else not info.st_mode & 0o022)


@dataclass
class _Directory:
    fd: int
    parent: _Directory | None
    name: str
    identity: FileIdentity
    uid: int
    mode: int
    private: bool
    owned: bool


def _directory(fd, parent, name, uid, *, private, owned):
    actual = os.fstat(fd)
    _directory_info(actual, uid, private=private, owned=owned)
    named = os.stat(name, dir_fd=None if parent is None else parent.fd, follow_symlinks=False)
    _require(_identity(named) == _identity(actual))
    return _Directory(fd, parent, name, _identity(actual), actual.st_uid,
                      stat.S_IMODE(actual.st_mode), private, owned)


def _open_directory(parent, name, uid, nodes, *, private, owned, create=False):
    created = False
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent.fd)
        except FileExistsError:
            pass
        else:
            created = True
            os.fsync(parent.fd)
    fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent.fd)
    try:
        node = _directory(fd, parent, name, uid, private=private, owned=owned)
        nodes.append(node)
        if created:
            os.fsync(fd)
        return node, created
    except BaseException:
        if not nodes or nodes[-1].fd != fd:
            os.close(fd)
        raise


def _open_home(home, uid, nodes):
    fd = os.open("/", _DIRECTORY_FLAGS)
    try:
        node = _directory(fd, None, "/", uid, private=False, owned=False)
        nodes.append(node)
    except BaseException:
        os.close(fd)
        raise
    anchor = None
    for index, name in enumerate(home.parts[1:], 1):
        node, _ = _open_directory(node, name, uid, nodes, private=False,
                                  owned=index == len(home.parts) - 1)
        # The first UID-owned component has a root-owned, non-writable parent
        # chain. Unlike a nested HOME, the UID cannot rename this anchor itself.
        if anchor is None and node.uid == uid:
            anchor = node
    _require(anchor is not None)
    return node, anchor


def _validate_directories(nodes, uid):
    for node in nodes:
        actual = os.fstat(node.fd)
        named = os.stat(node.name, dir_fd=None if node.parent is None else node.parent.fd,
                        follow_symlinks=False)
        _directory_info(actual, uid, private=node.private, owned=node.owned)
        _directory_info(named, uid, private=node.private, owned=node.owned)
        _require(node.identity == _identity(actual) == _identity(named) and
                 actual.st_uid == named.st_uid == node.uid and
                 stat.S_IMODE(actual.st_mode) == stat.S_IMODE(named.st_mode) == node.mode)


def _file_info(info, uid, *, empty=False):
    _require(stat.S_ISREG(info.st_mode) and info.st_uid == uid and info.st_nlink == 1 and
             stat.S_IMODE(info.st_mode) == 0o600)
    _require(info.st_size == 0 if empty else 0 < info.st_size <= contract.MAX_RECORD_BYTES)


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _file_at(root, name, fd, uid, *, empty=False, expected=None):
    actual = os.fstat(fd)
    named = os.stat(name, dir_fd=root.fd, follow_symlinks=False)
    _file_info(actual, uid, empty=empty)
    _file_info(named, uid, empty=empty)
    _require(_stamp(actual) == _stamp(named) and (expected is None or _stamp(actual) == expected))
    return actual


def _read_owner(root, fd, uid, expected_owner, *, expected=None):
    before = _file_at(root, "owner.json", fd, uid, expected=expected)
    raw = os.pread(fd, contract.MAX_RECORD_BYTES + 1, 0)
    after = _file_at(root, "owner.json", fd, uid, expected=_stamp(before))
    _require(len(raw) == after.st_size)
    parsed = contract.StorageOwner.parse(raw)
    _require(parsed == expected_owner)
    return _stamp(after)


def _fixed_entries(root):
    names = set()
    # Only the fixed envelope, never traversal of settings, Documents or state.
    with os.scandir(root.fd) as entries:
        for entry in entries:
            _require(entry.name in _ENTRIES and entry.name not in names)
            names.add(entry.name)
    _require(names == _ENTRIES)


def _flock(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EAGAIN):
            raise ProfileError("APP_STATE_BUSY") from None
        raise


def _write_all(fd, raw):
    remaining = memoryview(raw)
    while remaining:
        count = os.write(fd, remaining)
        _require(count > 0)
        remaining = remaining[count:]


class PersistentProfileRepository:
    """Fixed passwd-HOME repository. No configurable root, migration or erase."""

    def acquire(self, context, *, process_owner, barrier, expected_identity=None):
        nodes, lock_fd, owner_fd, handle = [], None, None, None
        try:
            _require(type(context) is contract.Context)
            _require(expected_identity is None or type(expected_identity) is ProfileIdentity)
            uid, home = _account_home(context)
            _fresh_owner(process_owner, uid)
            _require(os.getpid() not in _ACTIVE, "APP_STATE_BUSY")
            expected_owner = storage_owner(context)
            _require(isinstance(barrier, JournalBarrier), "APP_STATE_RECOVERY_REQUIRED")
            claim = JournalBarrier._bind(barrier, context, expected_owner)
            _barrier_call(barrier.before_acquire)
            _fresh_owner(process_owner, uid)
            # Do not restore a weaker mask after returning a live capability.
            os.umask(0o077)
            _umask_is_private()
            home_node, anchor = _open_home(home, uid, nodes)
            # A nested HOME is itself replaceable by its UID. Hold the first
            # UID-owned ancestor below the root-owned trusted chain as well.
            # Unsupported directory flock/fsync filesystems fail closed.
            _flock(anchor.fd)
            if anchor is not home_node:
                _flock(home_node.fd)
            _validate_directories(nodes, uid)
            _require(expected_identity is None or home_node.identity == expected_identity.home)
            parent = home_node
            value = context.record()
            components = (".local", "share", "eidolon", "apps", value["installation_id"],
                          value["profile_storage_id"])
            for index, name in enumerate(components):
                parent, _ = _open_directory(parent, name, uid, nodes, private=index >= 2,
                                            owned=True, create=expected_identity is None)
            root, created = _open_directory(parent, contract.EDITOR, uid, nodes,
                                            private=True, owned=True, create=expected_identity is None)
            _require(expected_identity is None or root.identity == expected_identity.root)
            flags = os.O_RDWR | _FILE_FLAGS
            if created:
                flags |= os.O_CREAT | os.O_EXCL
            else:
                _fixed_entries(root)  # no repair of an existing partial root
            lock_fd = os.open("profile.lock", flags, 0o600, dir_fd=root.fd)
            lock_stamp = _stamp(_file_at(root, "profile.lock", lock_fd, uid, empty=True))
            _require(expected_identity is None or _identity(os.fstat(lock_fd)) == expected_identity.lock)
            _flock(lock_fd)
            _file_at(root, "profile.lock", lock_fd, uid, empty=True, expected=lock_stamp)
            if created:
                os.fsync(lock_fd)
            children = {}
            for name in _DIRECTORIES:
                child, child_created = _open_directory(root, name, uid, nodes, private=True,
                                                       owned=True, create=created)
                _require(not created or child_created)
                children[name] = child
            if created:
                # All directories and the stable lock precede the owner commit.
                os.fsync(root.fd)
                owner_fd = os.open("owner.json", os.O_RDWR | _FILE_FLAGS | os.O_CREAT | os.O_EXCL,
                                   0o600, dir_fd=root.fd)
                _write_all(owner_fd, expected_owner.canonical())
                os.fsync(owner_fd)
            else:
                owner_fd = os.open("owner.json", os.O_RDONLY | _FILE_FLAGS, dir_fd=root.fd)
            owner_stamp = _read_owner(root, owner_fd, uid, expected_owner)
            if created:
                os.fsync(root.fd)
            identity = ProfileIdentity(home_node.identity, root.identity, _identity(os.fstat(lock_fd)),
                                       _identity(os.fstat(owner_fd)),
                                       tuple((name, children[name].identity) for name in _DIRECTORIES))
            _require(expected_identity is None or identity == expected_identity)
            root_path = home.joinpath(*components, contract.EDITOR)
            paths = PersistentPaths(root_path, *(root_path / name for name in _DIRECTORIES))
            handle = ProfileHandle(_HANDLE_KEY, context, process_owner, barrier, claim, uid, nodes,
                                   home_node, anchor, root, lock_fd, owner_fd, lock_stamp, owner_stamp,
                                   expected_owner, identity, paths, created)
            _ACTIVE[os.getpid()] = handle
            # Transfer before invoking another C2 hook. Even a hook failure (or
            # a forbidden child spawn there) must not drop the live locks.
            nodes, lock_fd, owner_fd = [], None, None
            handle.validate()
            _barrier_call(barrier.after_acquire, identity)
            _fresh_owner(process_owner, uid)
            handle.validate()
            return handle
        except ProfileError as error:
            # Before transfer there are no children; after transfer the caller
            # must keep this capability even if acquire itself did not return.
            error.handle = handle
            raise
        except (OSError, KeyError, ValueError, OwnershipError):
            raise ProfileError(handle=handle) from None
        finally:
            for fd in (owner_fd, lock_fd):
                if fd is not None:
                    os.close(fd)
            for node in reversed(nodes):
                os.close(node.fd)


def _retains_handle(operation):
    def call(self, *args, **kwargs):
        try:
            return operation(self, *args, **kwargs)
        except ProfileError as error:
            error.handle = self if not self.closed else None
            raise
        except (OSError, ValueError, OwnershipError):
            raise ProfileError(handle=self if not self.closed else None) from None
    return call


@dataclass
class _Pipe:
    fd: int
    identity: FileIdentity
    uid: int


class ProfileHandle:
    """A live capability: explicit release only, no __exit__/__del__ unlock.

    Call track_process immediately after every spawn by the bound ProcessOwner.
    C2 drains output and calls ProcessOwner.finish itself. A refused release
    retains the capability and both locks; it never deletes persistent files.
    """

    def __init__(self, key, context, process_owner, barrier, claim, uid, nodes, home_node, anchor, root,
                 lock_fd, owner_fd, lock_stamp, owner_stamp, owner, identity, paths, created):
        _require(key is _HANDLE_KEY)
        self._context, self._process_owner, self._barrier = context, process_owner, barrier
        self._claim, self._uid, self._nodes = claim, uid, nodes
        self._home_node, self._root, self._lock_fd, self._owner_fd = home_node, root, lock_fd, owner_fd
        self._anchor = anchor
        self._lock_stamp, self._owner_stamp, self._owner = lock_stamp, owner_stamp, owner
        self._identity, self._paths, self.created = identity, paths, created
        self._pid, self._closed, self._closing = os.getpid(), False, False
        self._children, self._pipes = {}, []

    @property
    def identity(self):
        return self._identity

    @property
    def closed(self):
        return self._closed

    @property
    def paths(self):
        self.validate()
        return self._paths

    @_retains_handle
    def validate(self):
        _require(not self.closed and not self._closing and self._pid == os.getpid())
        _require(_ACTIVE.get(self._pid) in (None, self))
        _live_owner(self._process_owner, self._uid)
        _require(self._barrier._pid == self._pid and self._barrier._claim is self._claim and
                 self._barrier.context == self._context and self._barrier.owner == self._owner,
                 "APP_STATE_RECOVERY_REQUIRED")
        _umask_is_private()
        _validate_directories(self._nodes, self._uid)
        _file_at(self._root, "profile.lock", self._lock_fd, self._uid, empty=True,
                 expected=self._lock_stamp)
        _read_owner(self._root, self._owner_fd, self._uid, self._owner, expected=self._owner_stamp)
        _fixed_entries(self._root)

    def environment(self):
        """Persistent variables only. C2 must add verified ephemeral paths.

        This is NOT a complete launch environment. Never inherit the caller's
        env or place runtime/cache/tmp/sockets under this persistent root.
        """
        paths = self.paths
        return {"HOME": str(paths.home), "XDG_CONFIG_HOME": str(paths.config),
                "XDG_DATA_HOME": str(paths.data), "XDG_STATE_HOME": str(paths.state)}

    @_retains_handle
    def track_process(self, child):
        self.validate()
        owner = self._process_owner
        _require(not owner.closing and owner.result is None and
                 owner.roots.get(child.pid) is child and child.pid not in self._children,
                 "APP_STATE_RECOVERY_REQUIRED")
        pipes, descriptors = [], []
        try:
            for stream in (child.stdout, child.stderr):
                _require(stream is not None and not stream.closed, "APP_STATE_RECOVERY_REQUIRED")
                fd = os.dup(stream.fileno())
                descriptors.append(fd)
                pipe = _Pipe(fd, _identity(os.fstat(fd)), self._uid)
                pipes.append(pipe)
                os.set_inheritable(fd, False)
                _check_pipe(pipe)
            self._children[child.pid] = child
            self._pipes.extend(pipes)
            descriptors = []
        finally:
            for fd in descriptors:
                os.close(fd)

    def _release_evidence(self):
        owner = self._process_owner
        _live_owner(owner, self._uid)
        result = owner.result
        _require(type(result) is dict and result.get("complete") is True and
                 result.get("pending") is False and type(result.get("open_pidfds")) is int and
                 result["open_pidfds"] == 0 and owner.closing and not owner.handles,
                 "APP_STATE_RECOVERY_REQUIRED")
        _require(not descendant_snapshot(owner.owner), "APP_STATE_RECOVERY_REQUIRED")
        _require(set(owner.roots) == set(self._children) and
                 all(owner.roots[pid] is child and child.poll() is not None
                     for pid, child in self._children.items()), "APP_STATE_RECOVERY_REQUIRED")
        for pipe in self._pipes:
            _pipe_eof(pipe)
        return ReleaseEvidence(self._context.scope(), self.identity, owner.owner["pid"],
                               owner.owner["start"], len(self._children), len(self._pipes))

    @_retains_handle
    def release(self):
        """Return computed evidence only after C2's barrier and actual release.

        No boolean/receipt supplied by the caller substitutes for live descendant
        checks or EOF. All fallible checks precede descriptor close. A close(2)
        failure is terminal/uncertain (never retry an ambiguous fd number); C2
        must preserve pending rather than reporting successful cleanup.
        """
        self.validate()
        evidence = self._release_evidence()
        _barrier_call(self._barrier.before_release, evidence)
        self.validate()
        _require(self._release_evidence() == evidence, "APP_STATE_RECOVERY_REQUIRED")
        self._closing = True
        # Keep the stable ancestor lock last. No LOCK_UN through shared fork/dup fds.
        fds = ([pipe.fd for pipe in self._pipes] + [self._owner_fd] +
               [node.fd for node in reversed(self._nodes)
                if node is not self._home_node and node is not self._anchor] +
               [self._lock_fd] + ([] if self._anchor is self._home_node else [self._home_node.fd]) +
               [self._anchor.fd])
        for fd in fds:
            os.close(fd)
        self._closed = True
        del _ACTIVE[self._pid]
        return evidence


def _check_pipe(pipe):
    info = os.fstat(pipe.fd)
    _require(stat.S_ISFIFO(info.st_mode) and info.st_uid == pipe.uid and
             _identity(info) == pipe.identity and
             fcntl.fcntl(pipe.fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY,
             "APP_STATE_RECOVERY_REQUIRED")


def _pipe_eof(pipe):
    _check_pipe(pipe)
    poller = select.poll()
    poller.register(pipe.fd, select.POLLIN | select.POLLHUP | select.POLLERR)
    events = poller.poll(0)
    # A writer still open, unread buffered output, closed fd, or poll error is
    # not EOF. Do not consume one byte and disguise an unfinished drain.
    _require(events == [(pipe.fd, select.POLLHUP)], "APP_STATE_RECOVERY_REQUIRED")
    flags = fcntl.fcntl(pipe.fd, fcntl.F_GETFL)
    try:
        fcntl.fcntl(pipe.fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        _require(os.read(pipe.fd, 1) == b"", "APP_STATE_RECOVERY_REQUIRED")
    finally:
        fcntl.fcntl(pipe.fd, fcntl.F_SETFL, flags)
