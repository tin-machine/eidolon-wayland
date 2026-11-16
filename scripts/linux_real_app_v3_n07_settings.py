"""N07-A: read-only, directory-FD-bound independent GSettings keyfile probe.

This is FILE evidence only. It cannot attest to input delivery, GUI state,
C2 lease retirement or N07 acceptance; those require the future live oracle.
No writer, GSettings API or Mousepad process is invoked by this module.
"""
from __future__ import annotations

import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import stat

SECTION = "org/xfce/mousepad/preferences/view"
KEY = "show-line-numbers"
COMPONENTS = ("config", "glib-2.0", "settings")
LEAF = "keyfile"
MAX_BYTES = 65536
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
PROOF_KEYS = frozenset(("storage_digest", "root_dev", "root_ino", "file",
                        "key_present", "value", "others_sha256"))


class KeyfileEvidenceError(ValueError):
    pass


def need(ok: bool, reason: str = "N07_KEYFILE_INVALID") -> None:
    if not ok:
        raise KeyfileEvidenceError(reason)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode & 0o7777)


def _directory(fd: int, uid: int) -> os.stat_result:
    info = os.fstat(fd)
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == uid
         and not info.st_mode & 0o022 and info.st_nlink >= 2,
         "N07_UNSAFE_DIRECTORY")
    return info


def _child(parent: int, name: str, uid: int) -> int:
    need(name in COMPONENTS, "N07_UNSAFE_COMPONENT")
    fd = os.open(name, DIR_FLAGS, dir_fd=parent)
    try:
        opened = _directory(fd, uid)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        need(_identity(named) == _identity(opened), "N07_DIRECTORY_REPLACED")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_keyfile(fd: int, uid: int) -> tuple[dict, bytes, dict]:
    handle = os.open(LEAF, READ_FLAGS, dir_fd=fd)
    try:
        before = os.fstat(handle)
        named = os.stat(LEAF, dir_fd=fd, follow_symlinks=False)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == uid
             and before.st_nlink == 1 and 0 < before.st_size <= MAX_BYTES
             and not before.st_mode & 0o022 and _identity(before) == _identity(named),
             "N07_UNSAFE_KEYFILE")
        chunks = bytearray()
        while len(chunks) <= MAX_BYTES:
            piece = os.read(handle, min(8192, MAX_BYTES + 1 - len(chunks)))
            if not piece:
                break
            chunks.extend(piece)
        after = os.fstat(handle)
        renamed = os.stat(LEAF, dir_fd=fd, follow_symlinks=False)
        stamp = lambda s: (_identity(s), s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        need(len(chunks) == before.st_size and len(chunks) <= MAX_BYTES
             and stamp(before) == stamp(after) == stamp(renamed),
             "N07_KEYFILE_RACE")
        return ({
            "dev": before.st_dev, "inode": before.st_ino, "uid": before.st_uid,
            "nlink": before.st_nlink, "size": before.st_size,
            "mtime_ns": before.st_mtime_ns, "ctime_ns": before.st_ctime_ns,
            "sha256": sha(chunks),
        }, bytes(chunks), {})
    finally:
        os.close(handle)


def _parse(raw: bytes) -> tuple[bool, bool | None, str]:
    parser = configparser.ConfigParser(interpolation=None, strict=True,
                                       delimiters=("=",), empty_lines_in_values=False)
    parser.optionxform = str
    try:
        parser.read_string(raw.decode("utf-8"))
    except (UnicodeError, configparser.Error) as error:
        raise KeyfileEvidenceError("N07_KEYFILE_SYNTAX") from error
    need(not parser.defaults(), "N07_KEYFILE_DEFAULTS")
    entries = {section: dict(parser.items(section, raw=True))
               for section in parser.sections()}
    values = entries.get(SECTION, {})
    present = KEY in values
    value = values.get(KEY)
    need(not present or value in ("true", "false"), "N07_KEYFILE_TYPE")
    if SECTION in entries:
        entries[SECTION].pop(KEY, None)
        if not entries[SECTION]:
            entries.pop(SECTION)
    remainder = json.dumps(entries, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True).encode("utf-8")
    return present, (value == "true") if present else None, sha(remainder)


class StorageKeyfileProbe:
    """An independent supervisor holds one C1-owned storage root FD.

    The caller must obtain both path and storage digest from a trusted C1
    capture, not from the GUI producer's self-reported status. The persistent
    root, not the per-nonce temporary C2 directory, is used for both sessions.
    """

    def __init__(self, root: Path, uid: int, storage_digest: str):
        need(isinstance(root, Path) and root.is_absolute()
             and type(uid) is int and uid >= 0
             and type(storage_digest) is str
             and re.fullmatch(r"[0-9a-f]{64}", storage_digest) is not None,
             "N07_STORAGE_BINDING_INVALID")
        self.path = root
        self.uid = uid
        self.storage_digest = storage_digest
        self.fd = os.open(root, DIR_FLAGS)
        try:
            self.root = _directory(self.fd, uid)
            self.root_stamp = _identity(self.root)
        except BaseException:
            os.close(self.fd)
            raise

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self) -> "StorageKeyfileProbe":
        return self

    def __exit__(self, *_unused: object) -> None:
        self.close()

    def _assert_root(self) -> None:
        need(self.fd is not None, "N07_PROBE_CLOSED")
        need(_identity(os.fstat(self.fd)) == self.root_stamp, "N07_ROOT_CHANGED")
        # A renamed root cannot be treated as the same storage by pathname.
        need(_identity(os.stat(self.path, follow_symlinks=False)) == self.root_stamp,
             "N07_ROOT_REPLACED")

    def snapshot(self) -> dict:
        self._assert_root()
        parent = self.fd
        opened = []
        try:
            for component in COMPONENTS:
                try:
                    child = _child(parent, component, self.uid)
                except FileNotFoundError:
                    return self._absent()
                opened.append((parent, component, child))
                parent = child
            try:
                metadata, contents, _ = _read_keyfile(parent, self.uid)
            except FileNotFoundError:
                return self._absent()
            present, value, other = _parse(contents)
            self._assert_root()
            return self._proof(metadata, present, value, other)
        finally:
            try:
                # The name chain must still identify the same open directories.
                # C1 persistent root alone is not sufficient if an intermediate
                # config/settings directory was replaced during a GUI run.
                for ancestor, name, child in opened:
                    named = os.stat(name, dir_fd=ancestor, follow_symlinks=False)
                    need(_identity(named) == _identity(os.fstat(child)),
                         "N07_DIRECTORY_REPLACED")
                self._assert_root()
            finally:
                for _, _, child in reversed(opened):
                    os.close(child)

    def _proof(self, file: dict | None, present: bool,
               value: bool | None, others: str) -> dict:
        return {"storage_digest": self.storage_digest,
                "root_dev": self.root.st_dev, "root_ino": self.root.st_ino,
                "file": file, "key_present": present, "value": value,
                "others_sha256": others}

    def _absent(self) -> dict:
        # Empty serialized dictionary is the reference for no other keys.
        self._assert_root()
        return self._proof(None, False, None, sha(b"{}"))


def validate_file_sequence(before: dict, enabled: dict, restarted: dict) -> None:
    """Compare *independent* snapshots; never certify that a GUI caused them.

    Full N07 success additionally requires native pointer/readback, two distinct
    product sessions, complete cleanup, and reviewed pixel golden acceptance.
    """
    for item in (before, enabled, restarted):
        need(type(item) is dict and set(item) == PROOF_KEYS, "N07_KEYFILE_PROOF_INVALID")
        need(type(item["storage_digest"]) is str
             and re.fullmatch(r"[0-9a-f]{64}", item["storage_digest"]) is not None)
    need(len({p["storage_digest"] for p in (before, enabled, restarted)}) == 1,
         "N07_DIFFERENT_STORAGE")
    need(len({(p["root_dev"], p["root_ino"]) for p in (before, enabled, restarted)}) == 1,
         "N07_DIFFERENT_STORAGE_ROOT")
    need(before["key_present"] is False and before["value"] is None,
         "N07_INITIAL_KEY_PRESENT")
    need(enabled["key_present"] is True and enabled["value"] is True
         and restarted["key_present"] is True and restarted["value"] is True,
         "N07_KEY_NOT_PERSISTED")
    need(len({p["others_sha256"] for p in (before, enabled, restarted)}) == 1,
         "N07_UNEXPECTED_SETTING_CHANGE")
    for item in (enabled, restarted):
        file = item["file"]
        need(type(file) is dict and set(file) == {
            "dev", "inode", "uid", "nlink", "size", "mtime_ns", "ctime_ns", "sha256"})
        need(file["nlink"] == 1 and file["size"] > 0
             and re.fullmatch(r"[0-9a-f]{64}", file["sha256"]) is not None,
             "N07_UNSAFE_KEYFILE")
    # The second session is read-only with respect to this setting; a rewritten
    # keyfile is not accepted merely because the boolean remains true.
    need(enabled["file"] == restarted["file"], "N07_RESTART_KEYFILE_CHANGED")
