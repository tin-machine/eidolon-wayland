"""C2 normal-v3 helper ownership, recovery and B2 receipt regressions."""
from __future__ import annotations

import json
import ast
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts/linux"))

import linux_desktop_v3_contract as C
import linux_desktop_v3_wire as W
import eidolon_linux_desktop_v3 as core
import eidolon_linux_persistent_profile as profile_api
from eidolon_linux_process_owner import ProcessOwner

GOLDEN = json.loads((ROOT / "scripts/tests/fixtures/linux-desktop-v3-contract.json").read_bytes())
RECORDS = {item["id"]: item for item in GOLDEN["records"]}
ORIGIN = "11111111-1111-4111-8111-111111111111"
CURRENT = "22222222-2222-4222-8222-222222222222"
SOURCES = {"linux_desktop_v3_contract.py": "1" * 64}


def binding(uid=None):
    pins = C.Pins.parse(RECORDS["pins"]["raw"].encode())
    value = C.parse_json(RECORDS["context"]["raw"].encode())
    if uid is not None:
        value["uid"] = uid
    context = C.Context.parse(C.canonical_json(value))
    return W.Binding(context, pins)


class PureC2Tests(unittest.TestCase):
    def test_receipt_shapes_are_exact_b2_contract(self):
        item = binding()
        complete = core.receipt(item, SOURCES, "-", ORIGIN, CURRENT, "receipt", "complete",
                                True, True, True, True)
        raw = C.canonical_json(complete) + b"\n"
        self.assertEqual(W.parse_receipt(raw, item, SOURCES, "-", 0), complete)

        changed = core.receipt(item, SOURCES, ORIGIN, ORIGIN, CURRENT, "boot_changed",
                               "complete", True, False, True, True)
        raw = C.canonical_json(changed) + b"\n"
        self.assertEqual(W.parse_receipt(raw, item, SOURCES, ORIGIN, 0), changed)

        unknown = core.receipt(item, SOURCES, "-", None, CURRENT, "unknown", "pending",
                               False, False, False, False)
        raw = C.canonical_json(unknown) + b"\n"
        self.assertEqual(W.parse_receipt(raw, item, SOURCES, "-", 1), unknown)

    def test_product_source_has_no_saved_pid_signal_or_dynamic_import(self):
        source = (ROOT / "scripts/linux/eidolon_linux_desktop_v3.py").read_text()
        session = (ROOT / "scripts/linux/eidolon_linux_desktop_session_v3.py").read_text()
        for forbidden in ("__import__(", "os.kill(", "os.killpg(", "subprocess.Popen("):
            self.assertNotIn(forbidden, source + session)
        self.assertIn('FIXED_APP = ("/usr/bin/mousepad", "--disable-server")', source)
        self.assertIn("profile.track_process(child)", source)
        self.assertIn("profile.release()", source)

    def test_bus_template_has_one_fixed_socket_placeholder(self):
        raw = (ROOT / "scripts/linux/linux-desktop-v3-bus.conf.in").read_bytes()
        self.assertEqual(raw.count(b"@EIDOLON_BUS_SOCKET@"), 1)
        self.assertNotIn(b"<include", raw)
        self.assertNotIn(b"<includedir", raw)

    def test_dbus_printed_address_accepts_guid_and_binds_exact_path(self):
        path = "/home/test user/.eidolon-desktop-v3/s-" + "a" * 16 + "/b"
        escaped = core._dbus_escape_path(path)
        self.assertIn("%20", escaped)
        self.assertEqual(core._validate_bus_address("unix:path=" + escaped, path),
                         "unix:path=" + escaped)
        with_guid = "unix:path=" + escaped + ",guid=" + "a" * 32
        self.assertEqual(core._validate_bus_address(with_guid, path), with_guid)
        for bad in (
            "unix:path=" + escaped + ",guid=" + "a" * 31,
            "unix:path=/wrong,guid=" + "a" * 32,
            "unix:path=" + escaped + ";unix:path=/other",
        ):
            with self.assertRaises(core.HelperError):
                core._validate_bus_address(bad, path)

    def test_output_limiter_is_rate_based_not_cumulative(self):
        now = [0.0]
        drain = core.Drain(clock=lambda: now[0])
        stream = object()
        drain.rate[stream] = (float(core.OUTPUT_BURST_BYTES), now[0])
        drain._charge(stream, core.OUTPUT_BURST_BYTES)
        now[0] += 4.0
        drain._charge(stream, 4 * core.OUTPUT_RATE_BYTES_PER_SEC)
        now[0] += 1.0
        drain._charge(stream, core.OUTPUT_RATE_BYTES_PER_SEC)
        # More than the former cumulative 8 MiB is accepted when spread over time.
        self.assertGreater(core.OUTPUT_BURST_BYTES + 5 * core.OUTPUT_RATE_BYTES_PER_SEC,
                           8 * 1024 * 1024)
        with self.assertRaisesRegex(core.HelperError, "APP_OUTPUT_RATE_LIMIT"):
            drain._charge(stream, 1)
        drain.close()

    def test_protocol_write_is_bounded_when_reader_stops(self):
        import eidolon_linux_desktop_session_v3 as session
        reader, writer = os.pipe()
        try:
            os.set_blocking(writer, False)
            block = b"x" * 4096
            while True:
                try:
                    os.write(writer, block)
                except BlockingIOError:
                    break
            with self.assertRaisesRegex(core.HelperError, "APP_STATE_INVALID"):
                session._write_bounded(writer, b"{}\n", timeout=.01)
        finally:
            os.close(writer)
            os.close(reader)


@unittest.skipUnless(
    sys.platform == "linux" and os.getuid() != 0 and hasattr(os, "pidfd_open"),
    "C2 real ownership tests require a non-root Linux account with pidfd",
)
class RealC2Tests(unittest.TestCase):
    def run_case(self, name):
        library = ctypes.CDLL(None)
        def subreaper():
            status = ctypes.c_int()
            self.assertEqual(library.prctl(37, ctypes.byref(status), 0, 0, 0), 0)
            return status.value
        before_subreaper = subreaper()
        before_mask = os.umask(0o077)
        os.umask(before_mask)
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(CASE_WORKER), name],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(subreaper(), before_subreaper)
        after_mask = os.umask(before_mask)
        self.assertEqual(after_mask, before_mask)
        for reason in json.loads(result.stdout)["skipped"]:
            self.skipTest(reason)


CASE_WORKER = ROOT / "scripts/tests/fixtures/linux_desktop_v3_c2_cases.py"
# Only discover names here. Kernel ownership and filesystem fixtures execute
# inside the worker, so no prctl/umask/global owner escapes into this runner.
_CASE_AST = ast.parse(CASE_WORKER.read_text())
_CASE_CLASS = next(node for node in _CASE_AST.body
                   if isinstance(node, ast.ClassDef) and node.name == "RealC2Cases")


def _isolated_case(name):
    def test(self):
        self.run_case(name)
    test.__name__ = name
    return test


for _case in _CASE_CLASS.body:
    if isinstance(_case, ast.FunctionDef) and _case.name.startswith("test_"):
        setattr(RealC2Tests, _case.name, _isolated_case(_case.name))


if __name__ == "__main__":
    unittest.main()
