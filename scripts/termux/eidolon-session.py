#!/data/data/com.termux/files/usr/bin/python3
"""固定mode/nonceだけを扱い、prepare作成者がleaseと子processを所有する。"""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import stat
import sys
import threading
import time
import subprocess
import tempfile

SIZES = (1, 65536, *(8 * 1024 * 1024 for _ in range(4)))
BLOCK = bytes(range(256)) * 256
TRAILER = b"AFTER_EOF"
RING_BYTES = 65536


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
    receipt = root / ("r-" + nonce + ".json")
    if receipt.exists() or receipt.is_symlink():
        raise FileExistsError("NONCE_REUSED")
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


def cleanup(home, nonce, *, creator=False):
    root, directory = paths(home, nonce)
    checked(root)
    if not directory.exists() and not directory.is_symlink():
        result = {"schema": 1, "cleaned": True, "already_absent": True}
        receipt = root / ("r-" + nonce + ".json")
        if receipt.exists() or receipt.is_symlink():
            result["receipt"] = read_record(receipt, nonce)
            result["child_alive"] = same_process(result["receipt"].get("child"))
        return result
    directory = owned(home, nonce)
    lease = directory / "lease.json"
    if lease.exists() or lease.is_symlink():
        record = read_record(lease, nonce)
        if same_process(record.get("owner")) or same_process(record.get("child")):
            return {"schema": 1, "cleaned": False, "active": same_process(record.get("owner")), "orphaned": same_process(record.get("child"))}
        if not creator:
            return {"schema": 1, "cleaned": False, "pending": True, "orphaned": True}
        lease.unlink()  # stale recordからsignalしない。生存identity不一致を確認したfileだけ。
    if not creator:
        # 後日の照合では既存directoryの所有権を新たに取得しない。
        return {"schema": 1, "cleaned": False, "pending": True}
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
        # 制御ownerの60秒leaseより長くし、失効が子のsocket timeoutへ依存しないようにする。
        peer.settimeout(90)
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


def process_identity(pid):
    """Linuxのstarttimeを含め、再利用されたPIDを同じprocessと扱わない。"""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
        fields = text[text.rindex(")") + 2:].split()
        return {"pid": pid, "start": fields[19], "group": int(fields[2]), "state": fields[0]}
    except (FileNotFoundError, ProcessLookupError):
        return None


def same_process(identity):
    if not isinstance(identity, dict) or not isinstance(identity.get("pid"), int) or identity["pid"] <= 0:
        return False
    now = process_identity(identity["pid"])
    return now is not None and now["start"] == identity.get("start") and now["state"] != "Z"


def write_record(path, value):
    fd, temporary = tempfile.mkstemp(prefix=".record-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(value, out, separators=(",", ":"))
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_record(path, nonce):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 16384:
        raise ValueError("RECORD_INVALID")
    value = json.loads(path.read_text())
    if any(value.get(key) != expected for key, expected in marker(nonce).items()):
        raise ValueError("RECORD_MISMATCH")
    return value


class Tail:
    def __init__(self):
        self.data, self.total = bytearray(), 0

    def push(self, block):
        self.total += len(block)
        self.data.extend(block)
        del self.data[:-RING_BYTES]

    def metrics(self):
        return {"bytes": self.total, "truncated": self.total > RING_BYTES, "retained": len(self.data)}


def child_command(mode, nonce):
    if mode != "echo":
        # W04で固定Waypipe/fixture/footの実binaryへ接続する。
        raise ValueError("DEPENDENCY_NOT_READY")
    return [sys.executable, str(Path(__file__).resolve()), "echo", nonce]


def group_live(pid):
    # leaderをWNOWAITで保持している期間だけ調べる。後日のrecordからgroupへsignalしない。
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            identity = process_identity(int(entry.name))
        except PermissionError:
            continue
        if identity is not None and identity["group"] == pid and identity["state"] != "Z":
            return True
    return False


def waitable(child):
    # Popen.pollはreapするため使わない。group終了までPIDを予約して再利用を防ぐ。
    return os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None


def echo_summary(data):
    value = json.loads(data)
    if (set(value) != {"schema", "result", "uid", "channels", "bytes", "copies"}
            or value["schema"] != 1 or value["result"] != "pass" or value["uid"] != os.getuid()
            or value["channels"] != 6 or value["bytes"] != sum(SIZES) or len(value["copies"]) != 6):
        raise ValueError("ECHO_SUMMARY_INVALID")
    for copy, size in zip(value["copies"], SIZES):
        if (set(copy) != {"size", "bytes", "sha256", "tail_matched"} or copy["size"] != size or copy["bytes"] != size
                or copy["tail_matched"] is not True or not isinstance(copy["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", copy["sha256"])):
            raise ValueError("ECHO_SUMMARY_INVALID")
    return value


def supervise(home, nonce, control_fd, emit, *, lease_seconds=60.0, grace_seconds=3.0, factory=child_command):
    """prepareの作成者がlease/子/FD/directoryを所有する。factoryの差替えはhost内部testのみ。"""
    prepared = prepare(home, nonce)
    directory = Path(prepared["socket"]).parent
    owner = process_identity(os.getpid())
    record = {**marker(nonce), "owner": owner, "child": None}
    write_record(directory / "lease.json", record)
    report = {**marker(nonce), "type": "finished", "reason": None, "child": None,
              "exit_status": None, "exit_signal": None, "term_sent": False, "kill_sent": False,
              "cleaned": False, "group_alive": False, "pipes_closed": False}
    child, child_reaped = None, False
    started_mode = None
    output = {"stdout": Tail(), "stderr": Tail()}
    opened = set()
    pending = bytearray()
    last_heartbeat = time.monotonic()
    stopping_at = None
    interrupted = [False]
    saved_signals = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
            saved_signals[sig] = signal.getsignal(sig)
            signal.signal(sig, lambda number, frame: interrupted.__setitem__(0, True))
    selector = selectors.DefaultSelector()
    original_blocking = os.get_blocking(control_fd)
    os.set_blocking(control_fd, False)

    def signal_owned(sig):
        if child is not None and not child_reaped:
            try:
                os.killpg(child.pid, sig)
                report["term_sent" if sig == signal.SIGTERM else "kill_sent"] = True
            except ProcessLookupError:
                pass

    def stop(reason):
        nonlocal stopping_at
        if stopping_at is None:
            report["reason"] = reason
            stopping_at = time.monotonic()
            if child is not None and group_live(child.pid):
                signal_owned(signal.SIGTERM)

    def publish(value):
        try:
            emit(value)
        except OSError:
            # SSH stdoutが先に閉じても所有者は停止・回収を最後まで進める。
            stop("IOFailed")

    try:
        selector.register(control_fd, selectors.EVENT_READ, "control")
        publish({**marker(nonce), **prepared, "type": "prepared"})
        while True:
            now = time.monotonic()
            if interrupted[0]:
                stop("Signal")
            if stopping_at is None and now - last_heartbeat >= lease_seconds:
                stop("LeaseExpired")
            if child is not None and not child_reaped and waitable(child):
                stop("Completed")
                if not group_live(child.pid):
                    status = child.wait()
                    child_reaped = True
                    report["exit_status" if status >= 0 else "exit_signal"] = status if status >= 0 else -status
            if stopping_at is not None:
                if child is None or child_reaped:
                    if not opened:
                        break
                elif now - stopping_at >= grace_seconds and not report["kill_sent"]:
                    signal_owned(signal.SIGKILL)
                if now - stopping_at > grace_seconds + 3:
                    report["reason"] = "CleanupTimeout"
                    break
            for key, _ in selector.select(timeout=0.05):
                try:
                    block = os.read(key.fd, 4096 if key.data == "control" else 65536)
                except BlockingIOError:
                    continue
                if not block:
                    selector.unregister(key.fd)
                    if key.data == "control":
                        stop("EOF")
                    else:
                        opened.discard(key.data)
                    continue
                if key.data != "control":
                    output[key.data].push(block)
                    continue
                if stopping_at is not None:
                    continue
                pending.extend(block)
                if len(pending) > 4096:
                    stop("ControlInvalid")
                    continue
                while b"\n" in pending and stopping_at is None:
                    line, _, rest = pending.partition(b"\n")
                    pending = bytearray(rest)
                    try:
                        value = json.loads(line)
                        kind = value["type"]
                        expected = {"type", "nonce", "mode"} if kind == "start" else {"type", "nonce"}
                        if len(line) > 1024 or set(value) != expected or value["nonce"] != nonce:
                            raise ValueError("CONTROL_INVALID")
                        if kind == "heartbeat":
                            last_heartbeat = time.monotonic()
                        elif kind == "stop":
                            stop("Stop")
                        elif kind == "start" and child is None and value["mode"] in {"echo", "pattern", "foot"}:
                            started_mode = value["mode"]
                            command = factory(value["mode"], nonce)
                            child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                     start_new_session=True, close_fds=True)
                            identity = process_identity(child.pid)
                            if identity is None or identity["group"] != child.pid:
                                raise ValueError("CHILD_IDENTITY_INVALID")
                            report["child"] = record["child"] = identity
                            write_record(directory / "lease.json", record)
                            for name in ("stdout", "stderr"):
                                pipe = getattr(child, name)
                                os.set_blocking(pipe.fileno(), False)
                                selector.register(pipe, selectors.EVENT_READ, name)
                                opened.add(name)
                            publish({**marker(nonce), "type": "child_started", "child": identity})
                        else:
                            raise ValueError("CONTROL_INVALID")
                    except (KeyError, TypeError, ValueError, OSError) as error:
                        report["error"] = "DEPENDENCY_NOT_READY" if error.args == ("DEPENDENCY_NOT_READY",) else "CONTROL_INVALID"
                        stop("ControlInvalid")
        report["group_alive"] = child is not None and not child_reaped and group_live(child.pid)
    finally:
        if child is not None and not child_reaped:
            signal_owned(signal.SIGKILL)
            try:
                child.wait(timeout=2)
                child_reaped = True
            except subprocess.TimeoutExpired:
                report["group_alive"] = True
        selector.close()
        if child is not None:
            child.stdout.close()
            child.stderr.close()
        report["pipes_closed"] = not opened
        os.set_blocking(control_fd, original_blocking)
        for name, ring in output.items():
            report[name] = ring.metrics()
        # echoの固定JSON以外のstdout/stderr本文は診断/receiptへ公開しない。
        try:
            if started_mode == "echo":
                report["echo"] = echo_summary(output["stdout"].data)
        except (ValueError, TypeError, KeyError):
            pass
        if (child is None or child_reaped) and not report["group_alive"] and not opened:
            (directory / "lease.json").unlink()
            report["cleaned"] = cleanup(home, nonce, creator=True)["cleaned"]
        if report["cleaned"]:
            write_record(directory.parent / ("r-" + nonce + ".json"), report)
            receipts = []
            for path in directory.parent.glob("r-*.json"):
                token = path.name[2:-5]
                if not re.fullmatch(r"[0-9a-f]{16}", token):
                    continue
                try:
                    saved = read_record(path, token)
                    if saved.get("type") == "finished" and saved.get("cleaned") is True:
                        receipts.append(path)
                except (ValueError, OSError):
                    continue
            for path in sorted(receipts, key=lambda p: p.stat().st_mtime_ns)[:-64]:
                path.unlink()
        publish(report)
        for sig, previous in saved_signals.items():
            signal.signal(sig, previous)
    return report


def main(argv):
    os.umask(0o077)
    if len(argv) != 2 or argv[0] not in {"prepare", "echo", "pattern", "foot", "cleanup"}:
        raise ValueError("MODE_INVALID")
    mode, nonce = argv
    if mode == "prepare":
        supervise(Path.home(), nonce, sys.stdin.fileno(), lambda value: print(json.dumps(value, separators=(",", ":")), flush=True))
        return
    if mode in {"pattern", "foot"}:
        raise ValueError("DEPENDENCY_NOT_READY")
    operation = {"prepare": prepare, "echo": echo, "cleanup": cleanup}[mode]
    print(json.dumps(operation(Path.home(), nonce), separators=(",", ":")), flush=True)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception:
        print('{"schema":1,"result":"fail","error":"LAUNCHER_FAILED"}', flush=True)
        sys.exit(1)
