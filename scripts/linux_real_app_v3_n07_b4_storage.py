"""B4 supervisor-owned storage identity and fixed document, never a GUI driver.

The descriptor is created by the independent disposable supervisor. A producer
path is never accepted: the root is derived from its canonical StorageRecord
and the separately verified passwd HOME. Ordinary product Inspect creates C1;
this observer only opens it. All ancestor FDs live until both sessions finish.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import stat
import time

import linux_desktop_v3_contract as contract
import linux_real_app_v3_evidence as evidence
from linux_real_app_v3_n07_settings import StorageKeyfileProbe

DOCUMENT_NAME = "n07-eight-lines.txt"
DOCUMENT_BYTES = b"alpha\nbravo\ncharlie\ndelta\necho\nfoxtrot\ngolf\nhotel"
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def need(value, reason="N07_B4_STORAGE_INVALID"):
    if not value:
        raise ValueError(reason)


def storage_record(descriptor: dict) -> contract.StorageRecord:
    profile = descriptor["profile"]
    need(profile["installation"] == descriptor["installation"]
         and profile["profile_id"] == "remote-linux"
         and profile["backend"] == "linux_ssh"
         and profile["host"] == "127.0.0.1"
         and type(profile["port"]) is int and 1024 <= profile["port"] <= 65535)
    binding = evidence.sha("\0".join([
        "eidolon-profile-v2", profile["installation"], profile["profile_id"],
        profile["revision"], profile["backend"], profile["host"],
        str(profile["port"]), profile["username"], profile["host_key"],
    ]).encode("utf-8"))
    return contract.StorageRecord.parse(contract.canonical_json({
        "schema": 1, "legacy_installation": descriptor["installation"],
        "installation_id": descriptor["installation_id"],
        "profile_id": profile["profile_id"], "profile_binding": binding,
        "profile_storage_id": descriptor["profile_storage_id"],
        "host_user_binding": contract.host_user_binding(
            evidence.sha(profile["host_key"].encode("utf-8")), profile["username"]),
    }))


def directory_identity(info):
    return {"dev": info.st_dev, "ino": info.st_ino, "uid": info.st_uid,
            "mode": stat.S_IMODE(info.st_mode)}


def file_identity(info):
    return {**directory_identity(info), "nlink": info.st_nlink,
            "size": info.st_size, "mtime_ns": info.st_mtime_ns,
            "ctime_ns": info.st_ctime_ns}


class TrustedStorage:
    """A read-only C1 context observer with one explicit prelaunch fixture write."""

    def __init__(self, record: contract.StorageRecord, account: dict):
        need(type(record) is contract.StorageRecord)
        self.record, self.uid = record, account["uid"]
        self.home = Path(account["passwd_home"])
        need(self.home.is_absolute() and self.uid > 0
             and self.home == Path("/home/eidolon-app"))
        values = record.record()
        self.root = (self.home / ".local/share/eidolon/apps" /
                     values["installation_id"] / values["profile_storage_id"] /
                     contract.EDITOR)
        self.document_path = self.root / "Documents" / DOCUMENT_NAME
        self.chain, self.data_dirs = [], {}
        self.control_files = {}
        self.probe = None
        self.metadata = None
        self.document = None

    def _pin_chain(self):
        parent = os.open("/", DIR_FLAGS)
        self.chain.append((None, "/", parent, directory_identity(os.fstat(parent))))
        need(os.fstat(parent).st_uid == 0
             and not os.fstat(parent).st_mode & 0o022)
        accumulated = Path("/")
        for component in self.root.parts[1:]:
            child = os.open(component, DIR_FLAGS, dir_fd=parent)
            info = os.fstat(child)
            accumulated /= component
            expected_uid = self.uid if self.home == accumulated or self.home in accumulated.parents else 0
            self.chain.append((parent, component, child, directory_identity(info)))
            need(stat.S_ISDIR(info.st_mode) and info.st_uid == expected_uid
                 and not info.st_mode & 0o022 and info.st_nlink >= 2,
                 "N07_B4_UNSAFE_ANCESTOR")
            need(directory_identity(os.stat(component, dir_fd=parent, follow_symlinks=False))
                 == directory_identity(info), "N07_B4_ANCESTOR_REPLACED")
            parent = child
        for name in ("home", "config", "data", "state", "Documents"):
            fd = os.open(name, DIR_FLAGS, dir_fd=parent)
            info = os.fstat(fd)
            self.data_dirs[name] = (fd, directory_identity(info))
            need(info.st_uid == self.uid and stat.S_IMODE(info.st_mode) == 0o700,
                 "N07_B4_UNSAFE_C1_DIRECTORY")
        self._control_file("profile.lock")

    def _control_file(self, name):
        """Pin C1 control inodes without joining or releasing product locks."""
        need(name in ("owner.json", "profile.lock"), "N07_B4_CONTROL_FILE_INVALID")
        root = self.chain[-1][2]
        retained = self.control_files.get(name)
        if retained is None:
            fd = os.open(name, READ_FLAGS, dir_fd=root)
            try:
                info = os.fstat(fd)
                need(stat.S_ISREG(info.st_mode) and info.st_uid == self.uid
                     and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
                     and (info.st_size == 0 if name == "profile.lock"
                          else 0 < info.st_size <= contract.MAX_RECORD_BYTES),
                     "N07_B4_CONTROL_FILE_INVALID")
                original = file_identity(info)
                need(file_identity(os.stat(name, dir_fd=root, follow_symlinks=False)) == original,
                     "N07_B4_CONTROL_FILE_CHANGED")
            except BaseException:
                os.close(fd)
                raise
            self.control_files[name] = (fd, original)
        else:
            fd, original = retained
        need(file_identity(os.fstat(fd)) == original
             == file_identity(os.stat(name, dir_fd=root, follow_symlinks=False)),
             "N07_B4_CONTROL_FILE_CHANGED")
        return fd, original

    def assert_current(self):
        need(self.chain, "N07_B4_STORAGE_NOT_OPEN")
        for parent, name, fd, original in self.chain:
            need(directory_identity(os.fstat(fd)) == original,
                 "N07_B4_ANCESTOR_REPLACED")
            if parent is not None:
                need(directory_identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
                     == original, "N07_B4_ANCESTOR_REPLACED")
        root = self.chain[-1][2]
        for name, (fd, original) in self.data_dirs.items():
            need(directory_identity(os.fstat(fd)) == original
                 and directory_identity(os.stat(name, dir_fd=root, follow_symlinks=False))
                 == original, "N07_B4_C1_DIRECTORY_REPLACED")
        for name in self.control_files:
            self._control_file(name)
        if self.metadata is not None:
            raw = self._read_owner()
            need(evidence.sha(raw) == self.metadata["owner_sha256"],
                 "N07_B4_OWNER_CHANGED")
            self.metadata["control_files_final"] = {
                name: file_identity(os.fstat(fd))
                for name, (fd, _) in self.control_files.items()}

    def _read_owner(self):
        fd, before = self._control_file("owner.json")
        raw = os.pread(fd, contract.MAX_RECORD_BYTES + 1, 0)
        self._control_file("owner.json")
        need(len(raw) == before["size"], "N07_B4_CONTROL_FILE_CHANGED")
        return raw

    def open_after_inspect(self, context: dict):
        need(self.probe is None, "N07_B4_STORAGE_ALREADY_OPEN")
        trusted_context = contract.Context.parse(contract.canonical_json(context))
        self.record.matches_context(trusted_context)
        need(context["uid"] == self.uid, "N07_B4_STORAGE_UID_MISMATCH")
        try:
            self._pin_chain()
            raw = self._read_owner()
            owner = contract.StorageOwner.parse(raw)
            owner.matches_context(trusted_context)
            self.metadata = {
                "record": self.record.record(), "storage_digest": self.record.sha256(),
                "uid": self.uid, "passwd_home": str(self.home),
                "root_path": str(self.root), "root_identity": self.chain[-1][3],
                "owner": owner.record(), "owner_sha256": evidence.sha(raw),
                "control_files": {name: identity for name, (_, identity)
                                  in self.control_files.items()},
                "control_files_final": None,
                "directories": [{"relative": str(self.root if i == len(self.chain)-1 else name),
                                 **identity}
                                for i, (_, name, _, identity) in enumerate(self.chain)],
            }
            self.assert_current()
            self.probe = StorageKeyfileProbe(self.root, self.uid, self.record.sha256())
            return self.metadata
        except BaseException:
            self.close()
            raise

    def snapshot(self):
        self.assert_current()
        need(self.probe is not None, "N07_B4_STORAGE_NOT_OPEN")
        result = self.probe.snapshot()
        self.assert_current()
        return result

    def create_document_before_launch(self):
        """Only call from the first, pre-Mousepad observation barrier."""
        self.assert_current()
        need(self.document is None, "N07_B4_DOCUMENT_ALREADY_CREATED")
        parent = self.data_dirs["Documents"][0]
        fd = os.open(DOCUMENT_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
        try:
            os.fchown(fd, self.uid, self.uid)
            with os.fdopen(os.dup(fd), "wb") as stream:
                stream.write(DOCUMENT_BYTES)
                stream.flush()
                os.fsync(stream.fileno())
            info = os.fstat(fd)
            os.fsync(parent)
            self.document = {
                "relative_path": "Documents/" + DOCUMENT_NAME,
                "bytes_b64": base64.b64encode(DOCUMENT_BYTES).decode("ascii"),
                "size": len(DOCUMENT_BYTES), "sha256": evidence.sha(DOCUMENT_BYTES),
                "line_count": 8, "created_at_ns": time.monotonic_ns(),
                "identity": file_identity(info), "final": None,
            }
        finally:
            os.close(fd)
        self.verify_document()
        return self.document

    def verify_document(self):
        self.assert_current()
        need(self.document is not None, "N07_B4_DOCUMENT_MISSING")
        parent = self.data_dirs["Documents"][0]
        fd = os.open(DOCUMENT_NAME, READ_FLAGS, dir_fd=parent)
        try:
            before = os.fstat(fd)
            need(stat.S_ISREG(before.st_mode) and before.st_uid == self.uid
                 and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600
                 and before.st_size == len(DOCUMENT_BYTES), "N07_B4_DOCUMENT_CHANGED")
            raw = os.read(fd, len(DOCUMENT_BYTES) + 1)
            need(raw == DOCUMENT_BYTES and file_identity(before) == self.document["identity"]
                 == file_identity(os.fstat(fd))
                 == file_identity(os.stat(DOCUMENT_NAME, dir_fd=parent, follow_symlinks=False)),
                 "N07_B4_DOCUMENT_CHANGED")
            result = {**file_identity(before), "sha256": evidence.sha(raw)}
            self.document["final"] = result
            return result
        finally:
            os.close(fd)

    def close(self):
        if self.probe is not None:
            self.probe.close()
            self.probe = None
        for fd, _ in self.control_files.values():
            os.close(fd)
        self.control_files.clear()
        for fd, _ in self.data_dirs.values():
            os.close(fd)
        self.data_dirs.clear()
        for _, _, fd, _ in reversed(self.chain):
            os.close(fd)
        self.chain.clear()
