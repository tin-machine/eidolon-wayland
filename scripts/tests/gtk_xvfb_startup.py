"""Private host-test display readiness. Keep diagnostic counts, never stderr contents."""
import json
import os
import re
import select
import subprocess
import time


class StderrCount:
    def __init__(self, stream):
        self.stream, self.bytes = stream, 0
        os.set_blocking(stream.fileno(), False)

    def drain(self):
        for _ in range(16):
            try: block = os.read(self.stream.fileno(), 4096)
            except BlockingIOError: break
            if not block: break
            self.bytes += len(block)
        return self.bytes


def wait_ready(read_fd, child, probe, stderr, *, timeout=5, clock=time.monotonic, pause=time.sleep):
    deadline = clock() + timeout
    data, display, attempts, probe_bytes = bytearray(), None, 0, 0

    def fail(reason, probe_code=None):
        raise RuntimeError(json.dumps(dict(reason=reason, xvfb_returncode=child.poll(), stderr_bytes=stderr.drain(),
                                           probe_returncode=probe_code, probe_stderr_bytes=probe_bytes, probe_attempts=attempts)))

    while True:
        stderr.drain()
        if child.poll() is not None: fail("XVFB_EXITED")
        remaining = deadline - clock()
        if remaining <= 0: fail("XVFB_DISPLAYFD_TIMEOUT" if display is None else "XVFB_GTK_DISPLAY_TIMEOUT")
        if display is None:
            if not select.select([read_fd], [], [], min(.05, remaining))[0]: continue
            block = os.read(read_fd, 32)
            if not block: fail("XVFB_DISPLAYFD_EOF")
            data.extend(block)
            if len(data) > 32 or any(c not in b"0123456789\n" for c in data): fail("XVFB_DISPLAYFD_INVALID")
            if b"\n" not in data: continue
            if not re.fullmatch(rb"[0-9]{1,5}\n", data) or int(data) > 65535: fail("XVFB_DISPLAYFD_INVALID")
            display = ":" + data[:-1].decode("ascii")
        attempts += 1
        try: completed = probe(display, remaining)
        except subprocess.TimeoutExpired as error:
            probe_bytes += len(error.stderr or b"");fail("XVFB_GTK_PROBE_TIMEOUT")
        probe_bytes += len(completed.stderr)
        try:
            rows = [json.loads(line) for line in completed.stdout.splitlines()]
            if len(completed.stdout) > 16384 or len(rows) != 1: raise ValueError()
            row = rows[0]
            if not isinstance(row, dict) or row.get("phase") != "final" or row.get("host_selftest") is not False: raise ValueError()
            reason = row.get("reason")
        except (ValueError, TypeError): fail("XVFB_GTK_PROBE_INVALID", completed.returncode)
        if child.poll() is not None: fail("XVFB_EXITED", completed.returncode)
        if clock() >= deadline: fail("XVFB_GTK_DISPLAY_TIMEOUT", completed.returncode)
        if completed.returncode != 2: fail("XVFB_GTK_PROBE_FAILED", completed.returncode)
        if reason == "wayland_required":
            return display, dict(probe_attempts=attempts, stderr_bytes=stderr.drain(), probe_stderr_bytes=probe_bytes)
        if reason != "display_unavailable": fail("XVFB_GTK_PROBE_FAILED", completed.returncode)
        pause(min(.02, max(0, deadline - clock())))
