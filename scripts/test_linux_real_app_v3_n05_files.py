"""Independent N05 file oracle contract and adversarial regressions.

The positive sample is a *file-only* proof; none of these tests assert that a
GUI driver or complete N05 product acceptance has run.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

import linux_real_app_v3_n05_files as oracle


class N05FileProofTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="eidolon-n05-")
        self.dir = Path(self.tmp.name)
        self.fd = oracle.trusted_directory(self.dir)
        self.uid = os.getuid()
        self.before = oracle.prepare_input(self.fd, self.uid)

    def tearDown(self):
        os.close(self.fd)
        self.tmp.cleanup()

    def put(self, name: str, contents: bytes):
        with os.fdopen(os.open(name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                              0o600, dir_fd=self.fd), "wb") as handle:
            handle.write(contents)

    def test_fixed_contract(self):
        oracle.fixed_contract()
        self.assertEqual(len(oracle.INPUT_BYTES), 78)
        self.assertEqual(len(oracle.INPUT_BYTES + oracle.APPEND_BYTES), 95)

    def test_failure_diagnostic_is_redacted_and_not_acceptance(self):
        missing = oracle.diagnostic_snapshot(
            self.fd,self.uid,"N05_MISSING_OR_UNSAFE_FILE")
        self.assertEqual(missing["output"]["state"],"missing_or_unsafe")
        self.assertEqual(missing["kind"],"eidolon-i6-n05-file-diagnostic-v1")
        self.assertNotIn("status",missing)
        self.assertNotIn("bytes",str(missing))
        self.put(oracle.OUTPUT_NAME,
                 bytes((0xef,0xbb,0xbf)) + oracle.INPUT_BYTES + oracle.APPEND_BYTES)
        mismatched=oracle.diagnostic_snapshot(self.fd,self.uid,"N05_OUTPUT_MISMATCH")
        self.assertEqual(mismatched["output"]["state"],"present")
        self.assertTrue(mismatched["output"]["bom"])
        self.assertFalse(mismatched["output"]["exact_bytes"])
        self.assertTrue(mismatched["output"]["append_suffix"])
        self.assertNotIn("bytes",mismatched["output"])
        self.assertEqual(mismatched["reason"],"N05_OUTPUT_MISMATCH")

    def test_missing_output_rejected(self):
        with self.assertRaises(oracle.FileEvidenceError):
            oracle.verify_after(self.fd, self.uid, self.before)

    def test_file_only_positive_does_not_claim_gui(self):
        self.put(oracle.OUTPUT_NAME, oracle.INPUT_BYTES + oracle.APPEND_BYTES)
        proof = oracle.verify_after(self.fd, self.uid, self.before)
        receipt = oracle.bounded_receipt(proof, "run1", "capture1", "supervisor1")
        self.assertEqual(receipt["output"]["sha256"], oracle.OUTPUT_SHA256)
        self.assertNotIn("bytes", receipt["output"])
        self.assertNotIn("status", receipt)

    def test_input_byte_mutation(self):
        self.put(oracle.INPUT_NAME, b"X" + oracle.INPUT_BYTES[1:])
        self.put(oracle.OUTPUT_NAME, oracle.INPUT_BYTES + oracle.APPEND_BYTES)
        with self.assertRaises(oracle.FileEvidenceError):
            oracle.verify_after(self.fd, self.uid, self.before)

    def test_output_byte_mutation(self):
        self.put(oracle.OUTPUT_NAME, b"X" + oracle.INPUT_BYTES[1:] + oracle.APPEND_BYTES)
        with self.assertRaises(oracle.FileEvidenceError):
            oracle.verify_after(self.fd, self.uid, self.before)

    def test_bom_rejected(self):
        self.put(oracle.OUTPUT_NAME, b"\xef\xbb\xbf" + oracle.INPUT_BYTES + oracle.APPEND_BYTES)
        with self.assertRaises(oracle.FileEvidenceError):
            oracle.verify_after(self.fd, self.uid, self.before)

    def test_crlf_rejected(self):
        self.put(oracle.OUTPUT_NAME,
                 (oracle.INPUT_BYTES + oracle.APPEND_BYTES).replace(b"\n", b"\r\n"))
        with self.assertRaises(oracle.FileEvidenceError):
            oracle.verify_after(self.fd, self.uid, self.before)

    def test_missing_final_newline(self):
        self.put(oracle.OUTPUT_NAME, (oracle.INPUT_BYTES + oracle.APPEND_BYTES)[:-1])
        with self.assertRaises(oracle.FileEvidenceError):
            oracle.verify_after(self.fd, self.uid, self.before)

    def test_input_overwritten_after_baseline(self):
        self.put(oracle.INPUT_NAME, oracle.INPUT_BYTES + oracle.APPEND_BYTES)
        self.put(oracle.OUTPUT_NAME, oracle.INPUT_BYTES + oracle.APPEND_BYTES)
        with self.assertRaises(oracle.FileEvidenceError):
            oracle.verify_after(self.fd, self.uid, self.before)

    def test_owner_mismatch_rejected(self):
        self.put(oracle.OUTPUT_NAME, oracle.INPUT_BYTES + oracle.APPEND_BYTES)
        with self.assertRaises(oracle.FileEvidenceError):
            oracle.verify_after(self.fd, self.uid + 1, self.before)

    def test_inode_replaced_rejected(self):
        self.put(oracle.OUTPUT_NAME, oracle.INPUT_BYTES + oracle.APPEND_BYTES)
        os.unlink(oracle.INPUT_NAME, dir_fd=self.fd)
        self.put(oracle.INPUT_NAME, oracle.INPUT_BYTES)
        with self.assertRaises(oracle.FileEvidenceError):
            oracle.verify_after(self.fd, self.uid, self.before)

    def test_symlink_rejected(self):
        os.symlink(oracle.INPUT_NAME, oracle.OUTPUT_NAME, dir_fd=self.fd)
        with self.assertRaises(oracle.FileEvidenceError):
            oracle.verify_after(self.fd, self.uid, self.before)

    def test_hardlink_rejected(self):
        os.link(oracle.INPUT_NAME, oracle.OUTPUT_NAME,
                src_dir_fd=self.fd, dst_dir_fd=self.fd)
        with self.assertRaises(oracle.FileEvidenceError):
            oracle.verify_after(self.fd, self.uid, self.before)

    def test_external_run_file_not_accepted(self):
        with tempfile.TemporaryDirectory(prefix="eidolon-n05-foreign-") as foreign:
            Path(foreign, oracle.OUTPUT_NAME).write_bytes(
                oracle.INPUT_BYTES + oracle.APPEND_BYTES)
            with self.assertRaises(oracle.FileEvidenceError):
                oracle.verify_after(self.fd, self.uid, self.before)

    def test_bad_binding_rejected(self):
        self.put(oracle.OUTPUT_NAME, oracle.INPUT_BYTES + oracle.APPEND_BYTES)
        proof = oracle.verify_after(self.fd, self.uid, self.before)
        with self.assertRaises(oracle.FileEvidenceError):
            oracle.bounded_receipt(proof, "run1", "", "supervisor1")

    def test_output_already_present_before_gui_rejected(self):
        self.put(oracle.OUTPUT_NAME, oracle.INPUT_BYTES + oracle.APPEND_BYTES)
        with self.assertRaises(oracle.FileEvidenceError):
            oracle.baseline(self.fd, self.uid)

    def test_input_symlink_before_prepare_rejected(self):
        os.unlink(oracle.INPUT_NAME, dir_fd=self.fd)
        os.symlink("other", oracle.INPUT_NAME, dir_fd=self.fd)
        with self.assertRaises(oracle.FileEvidenceError):
            oracle.baseline(self.fd, self.uid)


if __name__ == "__main__":
    unittest.main()
