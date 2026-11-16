"""N05 file and independent-proof negative tests, never a live GUI claim."""
from __future__ import annotations
import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_linux_real_app_v3_n05_files import N05FileProofTests
import linux_real_app_v3_evidence as evidence
import linux_real_app_v3_n05_files as files
import linux_real_app_v3_n05_oracle as n05


def proof_fixture():
    run="a"*32
    cap="b"*64
    directory="/home/eidolon-app/n05-"+run
    src={"dev":42,"inode":101,"uid":12002,"nlink":1,"size":78,
         "mtime_ns":100,"ctime_ns":100,"sha256":files.INPUT_SHA256}
    dst={"dev":42,"inode":102,"uid":12002,"nlink":1,"size":95,
         "mtime_ns":200,"ctime_ns":200,"sha256":files.OUTPUT_SHA256}
    env={"run_id":run,"app":{"uid":12002},"driver":{"uid":12001},
         "file_proof":{"schema":1,"kind":"eidolon-i6-n05-file-proof-v1",
             "run_id":run,"capture_id":cap,"owner":"disposable-supervisor",
             "baseline":src,"input":copy.deepcopy(src),"output":dst}}
    native={"run_id":run,"n05":{"capture_id":cap,"directory":directory,
            "input":directory+"/input.txt","output":directory+"/saved-as.txt"}}
    owner={"capture_sha256":cap}
    return env,native,owner


def dialog_baseline_fixture():
    main={"session":"1","window":"1","scene":"1","generation":"1"}
    dialogs=[{"window":{"window":"2"}},{"window":{"window":"3"}}]
    def observed(pos,phase,windows):
        return (pos,{"data":{"phase":phase,"main_target":copy.deepcopy(main),
                             "mapped_windows":windows}})
    baseline=[observed(1,"open",["1"]),observed(8,"save_as",["1"])]
    delivery_positions=(2,4,5,6,7,9,11,12)
    delivered=[(pos,{}) for pos in delivery_positions]
    created=[(3,{}),(10,{})]
    return {"n05_dialog_baseline":baseline},main,dialogs,delivered,created


class N05OwnerOracleTests(unittest.TestCase):
    def test_dialog_baseline_precedes_shortcut_and_excludes_new_child(self):
        args=dialog_baseline_fixture()
        self.assertTrue(n05.verify_dialog_baselines(*args))
        # This reproduces the fast GTK creation race: taking the set after
        # Ctrl+Shift+S includes the newly created child and must be rejected.
        cases=[
            ("save_dialog_already_in_previous",lambda a:
             a[0]["n05_dialog_baseline"][1][1]["data"]
               ["mapped_windows"].append("3")),
            ("open_dialog_already_in_previous",lambda a:
             a[0]["n05_dialog_baseline"][0][1]["data"]
               ["mapped_windows"].append("2")),
            ("late_save_baseline",lambda a:
             a[0]["n05_dialog_baseline"].__setitem__(
                1,(11,a[0]["n05_dialog_baseline"][1][1]))),
            ("baseline_before_edit",lambda a:
             a[0]["n05_dialog_baseline"].__setitem__(
                1,(6,a[0]["n05_dialog_baseline"][1][1]))),
            ("missing_open_baseline",lambda a:
             a[0]["n05_dialog_baseline"].pop(0)),
            ("wrong_phase",lambda a:
             a[0]["n05_dialog_baseline"][1][1]["data"].update(phase="open")),
            ("foreign_main",lambda a:
             a[0]["n05_dialog_baseline"][1][1]["data"]
               ["main_target"].update(window="999")),
            ("duplicate_id",lambda a:
             a[0]["n05_dialog_baseline"][1][1]["data"]
               ["mapped_windows"].append("1")),
            ("main_missing",lambda a:
             a[0]["n05_dialog_baseline"][0][1]["data"].update(
                mapped_windows=["5"])),
        ]
        for label,mutate in cases:
            with self.subTest(mutation=label):
                testcase=copy.deepcopy(dialog_baseline_fixture())
                mutate(testcase)
                with self.assertRaises((ValueError,TypeError,KeyError)):
                    n05.verify_dialog_baselines(*testcase)

    def test_frozen_manifest(self):
        rows=evidence.load_manifest()["cases"]
        row,=[x for x in rows if x["case_id"]=="N05"]
        self.assertEqual(row["variant"],"open_edit_save_as")
        self.assertEqual(tuple(row["assertions"]),n05.ASSERTIONS)
        self.assertEqual(row["expected_product_outcome"],"complete")
        self.assertEqual(row["evidence_classes"],["R","S"])
        self.assertEqual(len(rows),87)
        self.assertEqual(len({r["case_id"] for r in rows}),28)
        self.assertEqual(sum(len(r["assertions"]) for r in rows),533)

    def test_positive_file_suboracle_not_product_verdict(self):
        env,native,owner=proof_fixture()
        self.assertTrue(n05.verify_files(env,native,owner))
        self.assertNotIn("status",env["file_proof"])
        self.assertNotIn("assertions",env["file_proof"])

    def test_mutated_supervisor_proof_rejected(self):
        mutations=[
            ("missing",lambda e,n: e.update(file_proof=None)),
            ("output_hash",lambda e,n: e["file_proof"]["output"].update(sha256="f"*64)),
            ("input_hash",lambda e,n: e["file_proof"]["input"].update(sha256="f"*64)),
            ("output_size",lambda e,n: e["file_proof"]["output"].update(size=94)),
            ("input_inode",lambda e,n: e["file_proof"]["input"].update(inode=103)),
            ("output_inode",lambda e,n: e["file_proof"]["output"].update(inode=101)),
            ("output_uid",lambda e,n: e["file_proof"]["output"].update(uid=12001)),
            ("hardlink",lambda e,n: e["file_proof"]["output"].update(nlink=2)),
            ("foreign_run",lambda e,n: e["file_proof"].update(run_id="c"*32)),
            ("foreign_capture",lambda e,n: e["file_proof"].update(capture_id="d"*64)),
            ("foreign_dir",lambda e,n: n["n05"].update(directory="/tmp/forged")),
            ("foreign_path",lambda e,n: n["n05"].update(output="/tmp/saved-as.txt")),
        ]
        for label,mutation in mutations:
            with self.subTest(label=label):
                env,native,owner=proof_fixture()
                mutation(env,native)
                with self.assertRaises((ValueError,TypeError,KeyError)):
                    n05.verify_files(env,native,owner)

    def test_product_pass_never_inferred_from_file(self):
        env,native,_=proof_fixture()
        native["status"]="pass"
        # No real GUI trace, SDK/renderer, or product census: fail closed.
        with self.assertRaises((ValueError,TypeError,KeyError)):
            n05.verify(native,env,{})


if __name__=="__main__":
    unittest.main()
