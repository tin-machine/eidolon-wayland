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
MAX_DIAGNOSTIC_SECTIONS = 64
MAX_DIAGNOSTIC_ENTRIES = 256
MAX_DIAGNOSTIC_NAME_BYTES = 8192
DIAGNOSTIC_REVISION = "eidolon-i6-n07-keyfile-diagnostic-v1"
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
    # Match C1's directory contract: overlay/union filesystems can expose
    # st_nlink == 1 for a valid linked directory. This is separate from the
    # exact nlink == 1 requirement for regular keyfile/control files.
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == uid
         and not info.st_mode & 0o022 and info.st_nlink > 0,
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


def _diagnostic_inventory(entries: dict) -> dict:
    """Bounded names/hashes only; never serialize any parsed setting value."""
    count = sum(len(values) for values in entries.values())
    result = {"status": "observed", "reason": None, "section_count": len(entries),
              "entry_count": count, "sections": []}
    if len(entries) > MAX_DIAGNOSTIC_SECTIONS or count > MAX_DIAGNOSTIC_ENTRIES:
        result.update(status="unavailable", reason="N07_KEYFILE_DIAGNOSTIC_BOUNDS")
        return result
    names = [name for section, values in entries.items() for name in (section, *values)]
    if any(re.fullmatch(r"[A-Za-z0-9_./-]{1,256}", name) is None for name in names):
        result.update(status="unavailable", reason="N07_KEYFILE_DIAGNOSTIC_NAMES")
        return result
    if sum(len(name) for name in names) > MAX_DIAGNOSTIC_NAME_BYTES:
        result.update(status="unavailable", reason="N07_KEYFILE_DIAGNOSTIC_BOUNDS")
        return result
    result["sections"] = [{"section": section, "keys": [
        {"key": key, "value_sha256": sha(value.encode("utf-8"))}
        for key, value in sorted(values.items())]}
        for section, values in sorted(entries.items())]
    return result


def _parse(raw: bytes, *, diagnostic: dict | None = None) -> tuple[bool, bool | None, str]:
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
    if diagnostic is not None:
        diagnostic.update(_diagnostic_inventory(entries))
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

    def __init__(self, root: Path, uid: int, storage_digest: str, *, diagnostic: bool = False):
        need(isinstance(root, Path) and root.is_absolute()
             and type(uid) is int and uid >= 0
             and type(storage_digest) is str
             and re.fullmatch(r"[0-9a-f]{64}", storage_digest) is not None,
             "N07_STORAGE_BINDING_INVALID")
        need(type(diagnostic) is bool, "N07_STORAGE_BINDING_INVALID")
        self.path = root
        self.uid = uid
        self.storage_digest = storage_digest
        self.diagnostic_enabled = diagnostic
        self.last_diagnostic = None
        # Hold the first observed intermediate directory descriptions across
        # both GUI sessions. Comparing only within one read would allow an
        # entire settings directory to be replaced between observations.
        # Open FDs also prevent inode reuse from disguising that replacement.
        self.directory_fds: dict[tuple[str, ...], tuple[int, tuple]] = {}
        self.fd = os.open(root, DIR_FLAGS)
        try:
            self.root = _directory(self.fd, uid)
            self.root_stamp = _identity(self.root)
        except BaseException:
            os.close(self.fd)
            raise

    def close(self) -> None:
        for fd, _ in reversed(tuple(self.directory_fds.values())):
            os.close(fd)
        self.directory_fds.clear()
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
        self.last_diagnostic = None
        try:
            return self._snapshot()
        except BaseException:
            # A failed final path/FD check cannot leave a diagnostic that
            # looks bound to a successful snapshot.
            self.last_diagnostic = None
            raise

    def _snapshot(self) -> dict:
        self._assert_root()
        parent = self.fd
        opened = []
        try:
            for index, component in enumerate(COMPONENTS):
                chain = COMPONENTS[:index + 1]
                try:
                    child = _child(parent, component, self.uid)
                except FileNotFoundError:
                    need(chain not in self.directory_fds, "N07_DIRECTORY_REPLACED")
                    return self._absent()
                opened.append((parent, component, child))
                stamp = _identity(os.fstat(child))
                pinned = self.directory_fds.get(chain)
                if pinned is None:
                    # A missing directory may first appear when GLib writes
                    # the key. Once observed it may never be silently adopted
                    # again under a new identity. Keyfile replacement itself
                    # remains permitted for GLib's atomic Close flush.
                    self.directory_fds[chain] = (os.dup(child), stamp)
                else:
                    retained, expected = pinned
                    need(stamp == expected == _identity(os.fstat(retained)),
                         "N07_DIRECTORY_REPLACED")
                parent = child
            try:
                metadata, contents, _ = _read_keyfile(parent, self.uid)
            except FileNotFoundError:
                return self._absent()
            inventory = {} if self.diagnostic_enabled else None
            present, value, other = _parse(contents, diagnostic=inventory)
            self._assert_root()
            return self._proof(metadata, present, value, other, inventory=inventory)
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
               value: bool | None, others: str, *, inventory: dict | None = None) -> dict:
        proof = {"storage_digest": self.storage_digest,
                 "root_dev": self.root.st_dev, "root_ino": self.root.st_ino,
                 "file": file, "key_present": present, "value": value,
                 "others_sha256": others}
        if self.diagnostic_enabled:
            self.last_diagnostic = {
                "schema": 1, "revision": DIAGNOSTIC_REVISION,
                "source": "same_snapshot_keyfile_bytes",
                "proof_sha256": sha(json.dumps(proof, sort_keys=True, separators=(",", ":"),
                                               ensure_ascii=True).encode("utf-8")),
                "file_sha256": None if file is None else file["sha256"],
                "file_size": None if file is None else file["size"],
                "value_hash": "sha256_utf8_parsed_value",
                **(_diagnostic_inventory({}) if inventory is None else inventory),
            }
        return proof

    def _absent(self) -> dict:
        # Empty serialized dictionary is the reference for no other keys.
        self._assert_root()
        return self._proof(None, False, None, sha(b"{}"))


def validate_file_sequence(before: dict, enabled: dict, restarted: dict) -> None:
    """Compare *independent* snapshots; never certify that a GUI caused them.

    Full N07 success additionally requires native pointer/readback, two distinct
    product sessions, complete cleanup, and reviewed pixel golden acceptance.
    """
    def integer(value: object, minimum: int = 0) -> bool:
        return type(value) is int and value >= minimum

    def digest(value: object) -> bool:
        return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None

    for item in (before, enabled, restarted):
        need(type(item) is dict and set(item) == PROOF_KEYS, "N07_KEYFILE_PROOF_INVALID")
        need(digest(item["storage_digest"]) and digest(item["others_sha256"])
             and integer(item["root_dev"]) and integer(item["root_ino"], 1)
             and type(item["key_present"]) is bool,
             "N07_KEYFILE_PROOF_INVALID")
        need((item["key_present"] and type(item["value"]) is bool)
             or (not item["key_present"] and item["value"] is None),
             "N07_KEYFILE_PROOF_INVALID")
        file = item["file"]
        if file is None:
            need(item["key_present"] is False, "N07_KEYFILE_PROOF_INVALID")
            continue
        need(type(file) is dict and set(file) == {
            "dev", "inode", "uid", "nlink", "size", "mtime_ns", "ctime_ns", "sha256"},
            "N07_KEYFILE_PROOF_INVALID")
        need(all(integer(file[field]) for field in (
                 "dev", "uid", "mtime_ns", "ctime_ns"))
             and integer(file["inode"], 1) and integer(file["nlink"], 1)
             and file["nlink"] == 1 and integer(file["size"], 1)
             and file["size"] <= MAX_BYTES and digest(file["sha256"]),
             "N07_UNSAFE_KEYFILE")
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
    need(enabled["file"] is not None and restarted["file"] is not None,
         "N07_UNSAFE_KEYFILE")
    # GLib can atomically replace the keyfile when flushing on normal Close:
    # a changed inode/ctime is not itself a changed user setting. Require exact
    # saved bytes and owner instead of comparing inode/timestamp metadata.
    need(enabled["file"]["sha256"] == restarted["file"]["sha256"]
         and enabled["file"]["size"] == restarted["file"]["size"]
         and enabled["file"]["uid"] == restarted["file"]["uid"],
         "N07_RESTART_KEYFILE_CHANGED")
