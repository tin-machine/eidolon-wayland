#!/usr/bin/env python3
"""新run内の固定GTK Cだけを検査して一回buildする。実行・catalog変更はしない。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import time

FILES = {"gtk3-interaction-fixture.c", "build-gtk3-fixture.py"}
UID = 10158
COMPILE_TIMEOUT, TERM_GRACE = 30.0, 2.0


def require(value, code):
    if not value: raise ValueError(code)


def checked(path, maximum=65536):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600 and
                0 < info.st_size <= maximum, "SOURCE_UNSAFE")
        data = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
        require((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) ==
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) and len(data) == info.st_size,
                "SOURCE_CHANGED")
        return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    result = dict(schema=1, status="blocked", probe="eidolon-gtk3-fixture-build-v1", executed=False)
    try:
        require(os.getuid() == UID and re.fullmatch(r"[0-9a-f]{64}", args.manifest_sha256), "IDENTITY_INVALID")
        directory = Path(__file__).absolute().parent
        require(re.fullmatch(r"[0-9a-f]{32}", directory.name) and directory.parent.name == "nx15-fixture", "RUN_INVALID")
        info = directory.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700, "RUN_UNSAFE")
        manifest_data = checked(directory / "manifest.json")
        require(hashlib.sha256(manifest_data).hexdigest() == args.manifest_sha256, "MANIFEST_CHANGED")
        manifest = json.loads(manifest_data)
        require(type(manifest) is dict and set(manifest) == {"schema", "kind", "run_id", "source_commit", "source_dirty", "files"} and
                manifest.get("kind") == "eidolon-gtk3-fixture-source-v1" and manifest.get("run_id") == directory.name and
                manifest.get("schema") == 1 and type(manifest.get("source_dirty")) is bool and
                re.fullmatch(r"[0-9a-f]{40}", manifest.get("source_commit", "")) and set(manifest.get("files", {})) == FILES,
                "MANIFEST_INVALID")
        for name in FILES:
            data = checked(directory / name)
            require(manifest["files"][name] == dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest()), "SOURCE_CHANGED")
        binary = Path(sys.executable).resolve(strict=True)
        prefix = binary.parent.parent
        require(binary.parent.name == "bin" and prefix.name == "usr" and prefix.parent.name == "files" and
                prefix.parent.parent.name == "com.termux", "PREFIX_INVALID")
        expected = prefix.parent / "home/.local/state/eidolon/nx15-fixture" / directory.name
        require(directory == expected and directory.resolve(strict=True) == directory, "RUN_INVALID")
        for parent in (directory.parent, *list(directory.parents)[1:5]):
            info = parent.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o022, "RUN_UNSAFE")
        environment = dict(PATH=str(prefix / "bin") + ":/system/bin", HOME=str(directory), TMPDIR=str(prefix / "tmp"), LANG="C.UTF-8")
        def pkg(*options):
            process = subprocess.run([str(prefix / "bin/pkg-config"), *options, "gtk+-3.0"], capture_output=True,
                                     stdin=subprocess.DEVNULL, timeout=5, env=environment)
            require(process.returncode == 0 and not process.stderr and len(process.stdout) <= 16384, "GTK_METADATA_UNAVAILABLE")
            return process.stdout.decode("ascii").strip()
        require(pkg("--modversion") == "3.24.52", "GTK_VERSION_CHANGED")
        flags = shlex.split(pkg("--cflags", "--libs"))
        require(flags and all(flag.startswith(("-I", "-L", "-l", "-D", "-pthread", "-Wl,")) for flag in flags), "GTK_FLAGS_INVALID")
        require(not any("EIDOLON_GTK_FIXTURE_SELFTEST" in flag for flag in flags), "GTK_FLAGS_INVALID")
        build = directory / "build";build.mkdir(mode=0o700)  # 既存buildを上書きしない。
        target = build / "gtk3-fixture"
        command = [str(prefix / "bin/clang"), "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                   str(directory / "gtk3-interaction-fixture.c"), "-o", str(target), *flags]
        # 出力は本文を保存せずbyte数だけ。固定Cの小compile、timeout時は生成groupだけを回収する。
        child = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True)
        selector = selectors.DefaultSelector();output = dict(stdout=0, stderr=0)
        started, stopped, timeout = time.monotonic(), None, False
        try:
            for name in output:
                pipe = getattr(child, name);os.set_blocking(pipe.fileno(), False);selector.register(pipe, selectors.EVENT_READ, name)
            while True:
                now = time.monotonic()
                exited = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
                if exited and not selector.get_map(): break
                if stopped is None and (now-started >= COMPILE_TIMEOUT or sum(output.values()) > 1024*1024):
                    stopped = now;timeout = True
                    try: os.killpg(child.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
                if stopped is not None and now-stopped >= TERM_GRACE:
                    try: os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                require(now-started < COMPILE_TIMEOUT + TERM_GRACE + 3, "COMPILE_TIMEOUT")
                for key, _ in selector.select(.02):
                    data = os.read(key.fd, 65536)
                    if data: output[key.data] += len(data)  # 本文は保持・保存しない。
                    else: selector.unregister(key.fd)
        finally:
            try: os.killpg(child.pid, signal.SIGKILL)  # leaderはWNOWAITで予約中、生成groupのみ。
            except ProcessLookupError: pass
            child.wait(timeout=2);selector.close();child.stdout.close();child.stderr.close()
        code = child.returncode
        result.update(compiler_returncode=code, compiler_output_bytes=output, compiler_timed_out=timeout,
                      compiler_reaped=child.returncode is not None)
        require(not timeout, "COMPILE_TIMEOUT")
        require(code == 0, "COMPILE_FAILED")
        with os.fdopen(os.open(target, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and 64 <= info.st_size <= 4*1024*1024, "BINARY_INVALID")
            os.fchmod(stream.fileno(), 0o700)
            data = stream.read(4*1024*1024 + 1)
            after = os.fstat(stream.fileno())
            require(info.st_size == len(data) == after.st_size and info.st_mtime_ns == after.st_mtime_ns, "BINARY_INVALID")
        require(len(data) >= 64 and data[:6] == b"\x7fELF\x02\x01" and int.from_bytes(data[18:20], "little") == 183, "BINARY_INVALID")
        # build中のsource変更を検出する。
        require(checked(directory / "manifest.json") == manifest_data and all(
            hashlib.sha256(checked(directory / name)).hexdigest() == manifest["files"][name]["sha256"] for name in FILES), "SOURCE_CHANGED")
        result.update(status="built", run_id=directory.name, manifest_sha256=args.manifest_sha256,
                      binary_sha256=hashlib.sha256(data).hexdigest(), binary_bytes=len(data), elf_machine=183,
                      source_commit=manifest["source_commit"], source_dirty=manifest["source_dirty"],
                      gtk_version="3.24.52", compiler_output_bytes=output, relative_binary="build/gtk3-fixture")
        report = os.open(build / "build.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(report, "w") as stream: json.dump(result, stream, indent=2);stream.write("\n")
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        fixed = {"IDENTITY_INVALID", "RUN_INVALID", "RUN_UNSAFE", "SOURCE_UNSAFE", "SOURCE_CHANGED", "MANIFEST_CHANGED",
                 "MANIFEST_INVALID", "PREFIX_INVALID", "GTK_METADATA_UNAVAILABLE", "GTK_VERSION_CHANGED", "GTK_FLAGS_INVALID",
                 "COMPILE_TIMEOUT", "COMPILE_FAILED", "BINARY_INVALID"}
        result["reason"] = str(error) if type(error) is ValueError and str(error) in fixed else "BUILD_UNAVAILABLE"
    print(json.dumps(result))
    return 0 if result["status"] == "built" else 1


if __name__ == "__main__": raise SystemExit(main())
