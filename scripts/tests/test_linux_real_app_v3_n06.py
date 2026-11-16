"""N06 contract and hostile evidence tests. Not a claim of live GUI coverage."""
from __future__ import annotations
import copy
import os
import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import linux_real_app_v3_evidence as evidence
import linux_real_app_v3_n05_files as n05
import linux_real_app_v3_n06_files as files
import linux_real_app_v3_n06_oracle as n06
from linux_real_app_v3_n06 import VERSIONS


class N06ManifestTests(unittest.TestCase):
    def test_frozen_four_variants_and_34_assertions(self):
        import json
        raw=json.loads(evidence.MANIFEST_PATH.read_text())
        rows={row["variant"]:row for row in raw["cases"] if row["case_id"]=="N06"}
        self.assertEqual(tuple(rows),VERSIONS)
        self.assertEqual(len(rows),4)
        self.assertEqual(sum(len(a) for a in n06.ASSERTIONS_BY_VARIANT.values()),34)
        for name,wanted in n06.ASSERTIONS_BY_VARIANT.items():
            self.assertEqual(tuple(rows[name]["assertions"]),wanted)
            self.assertEqual(rows[name]["evidence_classes"],["R","S"])
            self.assertEqual(rows[name]["expected_product_outcome"],"complete")

    def test_main_and_dialog_phases_are_contractually_distinct(self):
        self.assertEqual(n06.DIALOG_NAMES["open_cancel"],("open",))
        self.assertEqual(n06.DIALOG_NAMES["close_confirm"],("open","unsaved_close"))
        self.assertEqual(n06.DIALOG_NAMES["unsaved_close_cancel"],
                         ("open","unsaved_close","save_as"))
        self.assertIn("close_intent_recorded",n06.ASSERTIONS_BY_VARIANT["close_confirm"])
        self.assertIn("unsaved_bytes_preserved",
                      n06.ASSERTIONS_BY_VARIANT["unsaved_close_cancel"])
        self.assertEqual(n06.expected_delivery("close_shortcut",{}),6)
        self.assertEqual(n06.expected_delivery("edit_append",{}),34)

    def test_missing_product_evidence_never_becomes_pass(self):
        for variant in VERSIONS:
            with self.subTest(variant=variant),self.assertRaises(
                    (ValueError,TypeError,KeyError)):
                n06.verify({"status":"pass","variant":variant},
                           {"file_proof":None},{},variant)


class N06IndependentFileProofTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.dir=Path(self.temp.name)
        self.fd=n05.trusted_directory(self.dir)
        self.before=n05.prepare_input(self.fd,os.getuid())

    def tearDown(self):
        os.close(self.fd)
        self.temp.cleanup()

    def test_no_output_variants_require_original_and_absence(self):
        for variant in ("open_cancel","close_confirm"):
            result=files.verify_after(self.fd,os.getuid(),self.before,variant)
            proof=files.bounded_receipt(result,"a"*32,"b"*64)
            self.assertTrue(proof["output_absent"])
            self.assertIsNone(proof["output"])
            self.assertEqual(proof["input"]["sha256"],n05.INPUT_SHA256)
            self.assertNotIn("bytes",proof["input"])
        with open(self.dir/"saved-as.txt","xb") as fp:
            fp.write(n05.INPUT_BYTES+n05.APPEND_BYTES)
        for variant in ("open_cancel","close_confirm"):
            with self.subTest(variant=variant),self.assertRaises(ValueError):
                files.verify_after(self.fd,os.getuid(),self.before,variant)

    def test_save_as_requires_exact_bytes_and_keeps_inode(self):
        for variant in ("save_as_confirm","unsaved_close_cancel"):
            with self.subTest(variant=variant),self.assertRaises(ValueError):
                files.verify_after(self.fd,os.getuid(),self.before,variant)
        with open(self.dir/"saved-as.txt","xb") as fp:
            fp.write(n05.INPUT_BYTES+n05.APPEND_BYTES)
        for variant in ("save_as_confirm","unsaved_close_cancel"):
            proof=files.bounded_receipt(files.verify_after(
                self.fd,os.getuid(),self.before,variant),"a"*32,"b"*64)
            self.assertFalse(proof["output_absent"])
            self.assertEqual(proof["output"]["size"],95)
            self.assertEqual(proof["output"]["sha256"],n05.OUTPUT_SHA256)
            self.assertNotEqual(proof["input"]["inode"],proof["output"]["inode"])

    def test_file_receipt_rejects_cross_capture_and_overwritten_source(self):
        proof=files.bounded_receipt(files.verify_after(
            self.fd,os.getuid(),self.before,"open_cancel"),"a"*32,"b"*64)
        owner={"capture_sha256":"b"*64}
        native={"run_id":"a"*32,"n06":{"capture_id":"b"*64,
            "directory":"/home/eidolon-app/n06-"+"a"*32,
            "input":"/home/eidolon-app/n06-"+"a"*32+"/input.txt",
            "output":"/home/eidolon-app/n06-"+"a"*32+"/saved-as.txt"}}
        env={"schema":1,"run_id":"a"*32,
             "app":{"uid":os.getuid()},
             "driver":{"uid":os.getuid()+1},"file_proof":proof}
        self.assertTrue(n06.verify_files(env,native,owner,"open_cancel"))
        for name,mutation in (
            ("foreign_capture",lambda p:p.update(capture_id="f"*64)),
            ("input_digest",lambda p:p["input"].update(sha256="f"*64)),
            ("changed_inode",lambda p:p["input"].update(inode=999)),
            ("wrong_absence",lambda p:p.update(output_absent=False)),
        ):
            changed=copy.deepcopy(env)
            mutation(changed["file_proof"])
            with self.subTest(name=name),self.assertRaises(ValueError):
                n06.verify_files(changed,native,owner,"open_cancel")


if __name__=="__main__":
    unittest.main()
