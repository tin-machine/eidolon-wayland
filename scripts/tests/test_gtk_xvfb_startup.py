import json
import os
import subprocess
import unittest
from unittest.mock import patch

from gtk_xvfb_startup import wait_ready


class Child:
    def __init__(self, codes=(None,)): self.codes = list(codes)
    def poll(self):
        value = self.codes[0]
        if len(self.codes) > 1: self.codes.pop(0)
        return value


class Counter:
    def drain(self): return 17


class XvfbStartupTests(unittest.TestCase):
    def run_ready(self, payload=b"123\n", *, codes=(None,), reports=None, timeout=.1, probe_delay=0):
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, payload);os.close(write_fd);write_fd = None
            now = [0.0]
            reports = iter(reports or [(2, "wayland_required")])
            def probe(display, remaining):
                self.assertEqual(display, ":123");self.assertGreater(remaining, 0)
                now[0] += probe_delay
                code, reason = next(reports)
                return subprocess.CompletedProcess([], code,
                    json.dumps(dict(phase="final", reason=reason, host_selftest=False)).encode(), b"PRIVATE stderr body")
            return wait_ready(read_fd, Child(codes), probe, Counter(), timeout=timeout,
                              clock=lambda: now[0], pause=lambda seconds: now.__setitem__(0, now[0] + seconds))
        finally:
            os.close(read_fd)
            if write_fd is not None: os.close(write_fd)

    def test_digit_display_and_production_wayland_refusal_prove_gtk_display_open(self):
        display, metadata = self.run_ready()
        self.assertEqual(display, ":123");self.assertEqual(metadata["probe_attempts"], 1)
        self.assertEqual(metadata["stderr_bytes"], 17);self.assertEqual(metadata["probe_stderr_bytes"], len(b"PRIVATE stderr body"))
        self.assertNotIn("PRIVATE", json.dumps(metadata))

    def test_eof_is_not_accepted_as_empty_display(self):
        with self.assertRaisesRegex(RuntimeError, "XVFB_DISPLAYFD_EOF"): self.run_ready(b"")

    def test_non_ascii_prefix_multiple_lines_and_overflow_are_rejected(self):
        for data in (b":123\n", b"\xff\n", b"1\n2\n", b"99999\n", b" " * 33):
            with self.subTest(data=data), self.assertRaisesRegex(RuntimeError, "XVFB_DISPLAYFD_INVALID"): self.run_ready(data)

    def test_exited_child_is_rejected_before_or_after_gtk_probe(self):
        for codes in ((1,), (None, 1)):
            with self.subTest(codes=codes), self.assertRaisesRegex(RuntimeError, "XVFB_EXITED"): self.run_ready(codes=codes)

    def test_only_display_unavailable_is_retried_until_finite_deadline(self):
        _, metadata = self.run_ready(reports=[(2, "display_unavailable"), (2, "wayland_required")])
        self.assertEqual(metadata["probe_attempts"], 2)
        with self.assertRaisesRegex(RuntimeError, "XVFB_GTK_DISPLAY_TIMEOUT"):
            self.run_ready(reports=[(2, "display_unavailable")] * 8, timeout=.03)

    def test_unexpected_exit_or_reason_is_failure_and_stderr_body_is_not_exposed(self):
        for report in ((0, "wayland_required"), (2, "running")):
            with self.subTest(report=report), self.assertRaisesRegex(RuntimeError, "XVFB_GTK_PROBE_FAILED") as caught:
                self.run_ready(reports=[report])
            self.assertNotIn("PRIVATE", str(caught.exception))

    def test_gtk_success_at_deadline_is_not_ready(self):
        with self.assertRaisesRegex(RuntimeError, "XVFB_GTK_DISPLAY_TIMEOUT"): self.run_ready(probe_delay=.1)

    def test_slow_displayfd_does_not_leave_only_milliseconds_for_first_gtk_connection(self):
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd,b"123\n");os.close(write_fd);write_fd=None
            now=[0.0];budgets=[]
            def select_ready(*_):
                now[0]=4.965
                return [read_fd],[],[]
            def probe(display, remaining):
                budgets.append(remaining);now[0]+=1.0
                return subprocess.CompletedProcess([],2,json.dumps(dict(phase="final",reason="wayland_required",host_selftest=False)).encode(),b"")
            with patch("gtk_xvfb_startup.select.select",side_effect=select_ready):
                display,_=wait_ready(read_fd,Child(),probe,Counter(),timeout=5,clock=lambda:now[0])
            self.assertEqual(display,":123");self.assertEqual(budgets,[5.0])
            self.assertLess(now[0],10)
        finally:
            os.close(read_fd)
            if write_fd is not None:os.close(write_fd)


if __name__ == "__main__": unittest.main()
