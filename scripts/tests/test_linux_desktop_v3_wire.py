"""One frozen B2 fixture consumed independently by Python and Rust.

Synthetic captures test rejection/compatibility only. Product source closure,
live helper ownership and Quest acceptance are separate gates.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import linux_desktop_v3_contract as C
import linux_desktop_v3_wire as W

B1_PATH = ROOT / "scripts/tests/fixtures/linux-desktop-v3-contract.json"
B1 = json.loads(B1_PATH.read_bytes())
RECORDS = {r["id"]: r for r in B1["records"]}
F = json.loads((ROOT / "scripts/tests/fixtures/linux-desktop-v3-wire.json").read_bytes())


def inputs():
    context = C.Context.parse(RECORDS["context"]["raw"].encode())
    pins = C.Pins.parse(RECORDS["pins"]["raw"].encode())
    return W.Binding(context, pins), W.ExpectedEvidence(C.canonical_json(F["evidence"]))


def parse(case):
    binding, evidence = inputs()
    raw = case["raw"].encode()
    if case["kind"] == "event":
        return W.parse_event(raw, binding, evidence)
    if case["kind"] == "command":
        return W.parse_command(raw, binding)
    if case["kind"] == "receipt":
        return W.parse_receipt(raw, binding, F["source_hashes"], case["expected_boot"], case["exit_status"])
    raise AssertionError("unknown fixture kind")


class WireGoldenTests(unittest.TestCase):
    def test_fixed_b1_and_b2_golden_bytes(self):
        self.assertEqual(hashlib.sha256(B1_PATH.read_bytes()).hexdigest(), F["b1_fixture_sha256"])
        self.assertEqual(F["purpose"], "synthetic-wire-contract-only; never production pins or acceptance")
        self.assertEqual(len(F["records"]), 16)
        for case in F["records"]:
            with self.subTest(vector=case["id"]):
                value = parse(case)
                raw = C.canonical_json(value)
                self.assertEqual(raw.decode(), case["canonical_utf8"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), case["sha256"])

    def test_frozen_unknown_duplicate_extra_type_version_identity_evidence_rejection(self):
        self.assertEqual(len(F["invalid"]), 383)
        ids = set()
        for case in F["invalid"]:
            with self.subTest(vector=case["id"]):
                self.assertNotIn(case["id"], ids)
                ids.add(case["id"])
                with self.assertRaises(C.ContractError):
                    parse(case)

    def test_frozen_request_ack_and_terminal_traces(self):
        self.assertEqual(len(F["traces"]), 19)
        for trace in F["traces"]:
            state = W.Protocol(*inputs())
            for index, step in enumerate(trace["steps"]):
                with self.subTest(trace=trace["id"], step=index):
                    if "event_raw" in step:
                        run = lambda: state.accept(step["event_raw"].encode())
                    else:
                        selection = None if step["selection"] is None else C.Selection.parse(C.canonical_json(step["selection"]))
                        run = lambda: state.command(step["command"], selection)
                    if step.get("error"):
                        with self.assertRaises(C.ContractError):
                            run()
                    else:
                        result = run()
                        if "expected_raw" in step:
                            self.assertEqual(result.decode(), step["expected_raw"])
                        else:
                            self.assertEqual(state.phase, step["phase"])

    def test_catalog_has_both_fixed_selections_and_no_availability_attestation(self):
        state = W.Protocol(*inputs())
        for step in F["traces"][0]["steps"][:3]:
            if "event_raw" in step:
                state.accept(step["event_raw"].encode())
            else:
                state.command(step["command"])
        for app in (C.DEMO, C.EDITOR):
            self.assertEqual(state.selection(app).record()["app_id"], app)
        with self.assertRaises(C.ContractError):
            state.selection("shell")
        payload = W.catalog_payload(*inputs())
        self.assertNotIn("available", payload["descriptor"])
        self.assertNotIn("production_acceptance_passed", payload)
        self.assertNotIn("quest_verified", payload)

    def test_fragmented_frames_multiple_frames_limits_and_poisoned_decoder(self):
        raw = F["records"][0]["canonical_utf8"].encode()
        framing = W.FrameDecoder()
        for byte in raw:
            self.assertEqual(framing.feed(bytes((byte,))), [])
        self.assertEqual(framing.feed(b"\n"), [raw])
        framing.finish()
        self.assertEqual(framing.feed(b"{}\n{}\n"), [b"{}", b"{}"])
        for chunk in (b"\n", b"{}\r\n", b"x" * (W.MAX_FRAME_BYTES + 1), b"{}\n" * 17, b"x" * 65537):
            with self.subTest(size=len(chunk)):
                decoder = W.FrameDecoder()
                with self.assertRaises(C.ContractError):
                    decoder.feed(chunk)
                with self.assertRaises(C.ContractError):
                    decoder.feed(b"{}\n")
        decoder = W.FrameDecoder()
        decoder.feed(b"{")
        with self.assertRaises(C.ContractError):
            decoder.finish()
        decoder = W.FrameDecoder()
        self.assertEqual(decoder.feed(b"x" * W.MAX_FRAME_BYTES), [])
        self.assertEqual(len(decoder.feed(b"\n")[0]), W.MAX_FRAME_BYTES)

    def test_parser_domain_is_the_same_b1_domain(self):
        binding, evidence = inputs()
        prepared = F["records"][0]["raw"].encode()
        for bad in (b"\xef\xbb\xbf" + prepared, b"\xff", b"{}", prepared + b"{}",
                    prepared.replace(b'"lease_ms":15000', b'"lease_ms":-0'), b" " * 16385):
            with self.assertRaises(C.ContractError):
                W.parse_event(bad, binding, evidence)
        with self.assertRaises(C.ContractError):
            W.ExpectedEvidence(C.canonical_json(dict(F["evidence"], arbitrary=True)))

    def test_import_and_codec_do_not_spawn_or_touch_state(self):
        code = """
import os, socket, subprocess, sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
before = dict(os.environ)
def forbidden(*a, **k): raise AssertionError('unexpected I/O')
with patch.object(os, 'open', forbidden), patch.object(os, 'mkdir', forbidden), patch.object(subprocess, 'Popen', forbidden), patch.object(socket, 'socket', forbidden):
    import linux_desktop_v3_wire
assert dict(os.environ) == before
"""
        result = subprocess.run([sys.executable, "-B", "-c", code, str(ROOT / "scripts")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
