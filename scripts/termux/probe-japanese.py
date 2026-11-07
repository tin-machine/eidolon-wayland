#!/usr/bin/env python3
"""専用PTY/専用辞書状態で固定文字だけを入力する。通常端末の入力を採取しない。"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import signal
import struct
import sys
import tempfile
import termios
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--binary", type=Path, required=True)
parser.add_argument("--dictionary", type=Path, required=True)
args = parser.parse_args()
os.umask(0o077)
with tempfile.TemporaryDirectory(prefix="eidolon-japanese-", dir=os.environ.get("TMPDIR")) as directory:
    root = Path(directory)
    config = root / "config.toml"
    config.write_text('[behavior]\nmode_marker = "letter"\n[candidates]\nlayout = "float"\n')
    capture = root / "child-input"
    child = root / "child.py"
    child.write_text('''import os,sys,tty
tty.setraw(0)
os.write(1,b"probe> ")
with open(sys.argv[1],"wb",buffering=0) as out:
 while True:
  value=os.read(0,1)
  if value in (b"",b"\\x04"): break
  out.write(value)
  os.write(1,b"\\r\\n" if value==b"\\r" else value)
''')
    pid, fd = pty.fork()
    if pid == 0:
        env = os.environ.copy()
        env.update(TERM="xterm-256color", TTYSKK_CONFIG=str(config), TTYSKK_JISYO=str(args.dictionary.resolve()),
                   TTYSKK_USER_JISYO=str(root / "user-jisyo"))
        for key in ["TTYSKK_ACTIVE", "TTYSKK_DEBUG", "TTYSKK_HANDOVER"]:
            env.pop(key, None)
        os.execve(str(args.binary.resolve()), [str(args.binary), "--", sys.executable, str(child), str(capture)], env)
    output = bytearray()
    collected = False
    stage = "start"

    def pump(seconds=0.15):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            readable, _, _ = select.select([fd], [], [], min(0.05, max(0, end - time.monotonic())))
            if readable:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    return
                output.extend(data)
                if b"\x1b[6n" in data:
                    os.write(fd, b"\x1b[1;8R")
                if len(output) > 256 * 1024:
                    raise RuntimeError("fixture output limit")

    def expect(text):
        end = time.monotonic() + 5
        while text.encode() not in output:
            if time.monotonic() >= end:
                raise RuntimeError("expected fixture display missing")
            pump()

    def send(value):
        output.clear()
        os.write(fd, value)
        pump(0.2)

    def received():
        return capture.read_bytes() if capture.exists() else b""

    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        expect("probe>")
        stage = "conversion"
        send(b"\x0aKanji ")
        expect("漢字")
        assert received() == b"", "preedit leaked to child"
        stage = "candidate"
        send(b" ")
        expect("幹事")
        assert received() == b""
        send(b"x")
        expect("漢字")
        stage = "commit"
        send(b"\r")
        assert received() == "漢字".encode(), "commit Enter must not execute child command"
        stage = "cancel"
        send(b"Kanji \x07\x07")
        assert received() == "漢字".encode(), "cancel leaked preedit"
        stage = "backspace"
        send(b"Kanjix\x7f ")
        expect("漢字")
        send(b"\x0a")
        assert received() == "漢字漢字".encode()
        stage = "ascii-resize-exit"
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 32, 100, 0, 0))
        os.kill(pid, signal.SIGWINCH)
        send(b"lABC\x7f\x1b[D\x1b[C\r")
        assert received() == "漢字漢字".encode() + b"ABC\x7f\x1b[D\x1b[C\r"
        send(b"\x04")
        end = time.monotonic() + 5
        while time.monotonic() < end:
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                collected = True
                assert os.waitstatus_to_exitcode(status) == 0
                break
            pump()
        assert collected, "wrapper failed to exit"
        print(json.dumps({"result":"pass", "conversion":True, "candidate":True, "cancel":True,
                          "commit_enter_does_not_execute":True, "backspace":True, "ascii":True,
                          "arrow_keys":True, "resize_signal":True, "exit":True,
                          "scope":"dedicated-pty", "foot_render":False, "physical_input":False}))
    except Exception as error:
        print(json.dumps({"result":"fail", "stage":stage, "error":type(error).__name__}))
        raise
    finally:
        os.close(fd)
        if not collected:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)
