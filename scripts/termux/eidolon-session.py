#!/data/data/com.termux/files/usr/bin/python3
"""固定mode/nonceだけを扱うTermux launcher。T03はprepare/echo/cleanupを実装する。"""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import threading

SIZES = (1, 65536, *(8 * 1024 * 1024 for _ in range(4)))
BLOCK = bytes(range(256)) * 256
TRAILER = b"AFTER_EOF"


def checked(directory):
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("DIRECTORY_INVALID")


def paths(home, nonce):
    if not re.fullmatch(r"[0-9a-f]{16}", nonce):
        raise ValueError("NONCE_INVALID")
    root = home / ".eidolon"
    directory = root / ("s-" + nonce)
    if len(os.fsencode(directory / "p")) > 103:
        raise ValueError("SOCKET_PATH_LONG")
    return root, directory


def marker(nonce):
    return {"schema": 1, "nonce": nonce, "uid": os.getuid()}


def prepare(home, nonce):
    root, directory = paths(home, nonce)
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    checked(root)
    directory.mkdir(mode=0o700)  # 再利用・既存socketの上書きをしない。
    checked(directory)
    with (directory / "owner.json").open("x") as out:
        json.dump(marker(nonce), out)
    (directory / "owner.json").chmod(0o600)
    return {"schema": 1, "socket": str(directory / "p"), "launcher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def owned(home, nonce):
    root, directory = paths(home, nonce)
    checked(root)
    checked(directory)
    owner = directory / "owner.json"
    info = owner.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 1024:
        raise ValueError("OWNER_INVALID")
    if json.loads(owner.read_text()) != marker(nonce):
        raise ValueError("OWNER_MISMATCH")
    return directory


def cleanup(home, nonce):
    root, directory = paths(home, nonce)
    checked(root)
    if not directory.exists() and not directory.is_symlink():
        return {"schema": 1, "cleaned": True, "already_absent": True}
    directory = owned(home, nonce)
    sock = directory / "p"
    try:
        info = sock.lstat()
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("SOCKET_INVALID")
        sock.unlink()
    # 予期しないfileがあればmarkerを消さず失敗させ、他の内容を消去しない。
    if {entry.name for entry in directory.iterdir()} != {"owner.json"}:
        raise ValueError("DIRECTORY_NOT_EMPTY")
    (directory / "owner.json").unlink()
    directory.rmdir()
    return {"schema": 1, "cleaned": True, "already_absent": False}


def transfer(path, size, barrier=None):
    if barrier:
        barrier.wait(timeout=10)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
        peer.settimeout(30)
        peer.connect(str(path))
        expected = hashlib.sha256()
        failure = []

        def send():
            try:
                left = size
                while left:
                    block = BLOCK[:min(left, len(BLOCK))]
                    expected.update(block)
                    peer.sendall(block)
                    left -= len(block)
                peer.shutdown(socket.SHUT_WR)
            except Exception as error:
                failure.append(error)
                peer.close()

        writer = threading.Thread(target=send)
        writer.start()
        received, digest, tail = 0, hashlib.sha256(), b""
        try:
            while True:
                block = peer.recv(65536)
                if not block:
                    break
                received += len(block)
                pending = tail + block
                digest.update(pending[:-len(TRAILER)])
                tail = pending[-len(TRAILER):]
        finally:
            writer.join(timeout=35)
        if writer.is_alive() or failure or received != size + len(TRAILER) or tail != TRAILER or digest.digest() != expected.digest():
            raise ValueError("ECHO_MISMATCH")
        return {"size": size, "bytes": received - len(TRAILER), "sha256": digest.hexdigest(), "tail_matched": True}


def echo(home, nonce):
    directory = owned(home, nonce)
    path = directory / "p"
    reports = [transfer(path, size) for size in SIZES[:2]]
    barrier = threading.Barrier(4)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(transfer, path, size, barrier) for size in SIZES[2:]]
        reports.extend(future.result(timeout=45) for future in futures)
    return {"schema": 1, "result": "pass", "uid": os.getuid(), "channels": len(reports), "bytes": sum(SIZES), "copies": reports}


def main(argv):
    os.umask(0o077)
    if len(argv) != 2 or argv[0] not in {"prepare", "echo", "cleanup"}:
        raise ValueError("MODE_INVALID")
    mode, nonce = argv
    operation = {"prepare": prepare, "echo": echo, "cleanup": cleanup}[mode]
    print(json.dumps(operation(Path.home(), nonce), separators=(",", ":")), flush=True)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception:
        print('{"schema":1,"result":"fail","error":"LAUNCHER_FAILED"}', flush=True)
        sys.exit(1)
