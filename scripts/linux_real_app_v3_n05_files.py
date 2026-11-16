"""I6 N05 independent disposable file sub-oracle.

Only the fixed input is prepared. The GUI must create the output. Both paths
are opened relative to the trusted directory FD, with no symlink traversal.
This module intentionally cannot produce an N05 product acceptance verdict.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat

INPUT_BYTES = ("Eidolon I6\n日本語の固定入力\nline-3\nline-4\nline-5\n"
               "line-6\nline-7\nline-8\n").encode("utf-8")
APPEND_BYTES = b"eidolon-i6-saved\n"
INPUT_LENGTH = 78
OUTPUT_LENGTH = 95
INPUT_SHA256 = "884b4478282ae75f7f297783a415cb6d529b29b63fcf3b6e56c1110ed2af1262"
OUTPUT_SHA256 = "79a400c35081c5b9bc257e6d440512320c134a680b28c6c904e39b5d40e18337"
INPUT_NAME = "input.txt"
OUTPUT_NAME = "saved-as.txt"
FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)
MAX_BYTES = 4096


class FileEvidenceError(ValueError):
    """Fail-closed file proof error (never equivalent to N05 pass)."""


def need(condition: bool, reason: str) -> None:
    if not condition:
        raise FileEvidenceError(reason)


def hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fixed_contract() -> None:
    # The bytes and expected hashes are independent, immutable inputs; neither
    # digest is inferred from a measured result.
    need(len(INPUT_BYTES) == INPUT_LENGTH, "N05_FIXED_INPUT_LENGTH")
    need(hash_bytes(INPUT_BYTES) == INPUT_SHA256, "N05_FIXED_INPUT_DIGEST")
    need(len(INPUT_BYTES + APPEND_BYTES) == OUTPUT_LENGTH, "N05_FIXED_OUTPUT_LENGTH")
    need(hash_bytes(INPUT_BYTES + APPEND_BYTES) == OUTPUT_SHA256,
         "N05_FIXED_OUTPUT_DIGEST")


def check_component(name: str) -> None:
    need(name in (INPUT_NAME, OUTPUT_NAME), "N05_NAME_NOT_PINNED")


def trusted_directory(directory: Path) -> int:
    """Call after the disposable supervisor has made an exclusively owned dir."""
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    info = os.fstat(fd)
    need(stat.S_ISDIR(info.st_mode), "N05_NOT_DIRECTORY")
    need(info.st_nlink >= 2, "N05_DIRECTORY_LINK")
    return fd


def snapshot(dir_fd: int, name: str, uid: int) -> dict:
    check_component(name)
    try:
        fd = os.open(name, FLAGS, dir_fd=dir_fd)
    except OSError as exc:
        raise FileEvidenceError("N05_MISSING_OR_UNSAFE_FILE") from exc
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode), "N05_NOT_REGULAR")
        need(info.st_uid == uid, "N05_OWNER_MISMATCH")
        need(info.st_nlink == 1, "N05_LINK_COUNT")
        need(0 < info.st_size <= MAX_BYTES, "N05_FILE_SIZE_BOUNDS")
        chunks = bytearray()
        while len(chunks) <= MAX_BYTES:
            chunk = os.read(fd, min(4096, MAX_BYTES + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        need(len(chunks) <= MAX_BYTES, "N05_FILE_TOO_LARGE")
        need(len(chunks) == info.st_size, "N05_FILE_SIZE_RACE")
        after = os.fstat(fd)
        need((info.st_dev, info.st_ino, info.st_uid, info.st_size, info.st_mtime_ns,
              info.st_ctime_ns, info.st_nlink) ==
             (after.st_dev, after.st_ino, after.st_uid, after.st_size, after.st_mtime_ns,
              after.st_ctime_ns, after.st_nlink), "N05_FILE_CHANGED_DURING_READ")
        return {"dev": info.st_dev, "inode": info.st_ino, "uid": info.st_uid,
                "nlink": info.st_nlink, "size": info.st_size,
                "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
                "sha256": hash_bytes(bytes(chunks)), "bytes": bytes(chunks)}
    finally:
        os.close(fd)


def checked_absent(dir_fd: int, name: str) -> None:
    check_component(name)
    try:
        os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise FileEvidenceError("N05_OUTPUT_ALREADY_PRESENT")


def prepare_input(dir_fd: int, uid: int) -> dict:
    """Fresh trusted directory only; cannot replace any existing file.

    The supervisor can create INPUT_NAME and *never* creates OUTPUT_NAME.
    """
    fixed_contract()
    checked_absent(dir_fd, INPUT_NAME)
    checked_absent(dir_fd, OUTPUT_NAME)
    fd = os.open(INPUT_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dir_fd)
    try:
        os.write(fd, INPUT_BYTES)
        os.fsync(fd)
    finally:
        os.close(fd)
    return baseline(dir_fd, uid)


def baseline(dir_fd: int, uid: int) -> dict:
    fixed_contract()
    value = snapshot(dir_fd, INPUT_NAME, uid)
    need(value["size"] == INPUT_LENGTH and value["bytes"] == INPUT_BYTES
         and value["sha256"] == INPUT_SHA256, "N05_INPUT_MISMATCH")
    checked_absent(dir_fd, OUTPUT_NAME)
    return value


def verify_after(dir_fd: int, uid: int, before: dict) -> dict:
    """File-only predicate. Not proof that a GUI operation occurred."""
    fixed_contract()
    source = snapshot(dir_fd, INPUT_NAME, uid)
    target = snapshot(dir_fd, OUTPUT_NAME, uid)
    need(source["size"] == INPUT_LENGTH and source["bytes"] == INPUT_BYTES
         and source["sha256"] == INPUT_SHA256, "N05_INPUT_MISMATCH")
    need(target["size"] == OUTPUT_LENGTH and target["bytes"] == INPUT_BYTES + APPEND_BYTES
         and target["sha256"] == OUTPUT_SHA256, "N05_OUTPUT_MISMATCH")
    need((source["dev"], source["inode"], source["uid"], source["nlink"],
          source["ctime_ns"], source["mtime_ns"]) ==
         (before["dev"], before["inode"], before["uid"], before["nlink"],
          before["ctime_ns"], before["mtime_ns"]), "N05_INPUT_CHANGED")
    need((source["dev"], source["inode"]) != (target["dev"], target["inode"]),
         "N05_INPUT_OUTPUT_ALIAS")
    need(target["ctime_ns"] >= before["ctime_ns"], "N05_OUTPUT_PREDATES_INPUT")
    return {"input": source, "output": target}


def bounded_receipt(result: dict, run_id: str, capture_id: str, owner: str) -> dict:
    """Redact raw file bytes, bind an independently owned observation to run."""
    need(all(isinstance(v, str) and v and len(v) <= 128
             for v in (run_id, capture_id, owner)), "N05_OWNER_INVALID")
    need(result["input"]["sha256"] == INPUT_SHA256
         and result["output"]["sha256"] == OUTPUT_SHA256, "N05_DIGEST_INVALID")
    def public(v):
        return {key: v[key] for key in ("dev", "inode", "uid", "nlink",
                "size", "mtime_ns", "ctime_ns", "sha256")}
    return {"schema": 1, "kind": "eidolon-i6-n05-file-proof-v1",
            "run_id": run_id, "capture_id": capture_id, "owner": owner,
            "input": public(result["input"]), "output": public(result["output"])}
