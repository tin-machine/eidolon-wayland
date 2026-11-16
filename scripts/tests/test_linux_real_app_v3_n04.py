"""N04 independent temporal verifier mutation tests (not a product smoke)."""
from __future__ import annotations
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import linux_real_app_v3_n04_oracle as n04
import linux_real_app_v3_evidence as evidence


def fixture():
    cap = {"capture_id":"b"*64}
    rows = [
        {"sequence":"1","kind":"capture_created","capture":cap,"data":{}},
        {"sequence":"2","kind":"protocol_event","capture":cap,"data":{
            "record":{"event":{"type":"started","payload":{}}}}},
    ]
    a = 1_000_000_000_000
    observations = [{"sequence":"1","kind":"n04_idle_begin",
                     "transport_sequence":"2","data":{"ns":a}}]
    for i in range(1,26):
        rows.append({"sequence":str(len(rows)+1),"kind":"control_send_attempt",
                     "capture":cap,"data":{"action":"heartbeat"}})
        rows.append({"sequence":str(len(rows)+1),"kind":"protocol_event",
                     "capture":cap,"data":{"record":{
                         "request_sequence":str(10+i),
                         "event":{"type":"heartbeat","payload":{"phase":"started"}}}}})
        observations.append({"sequence":str(len(observations)+1),
                             "kind":"n04_lease_sample",
                             "transport_sequence":str(len(rows)),
                             "data":{"ns":a+i*5_000_000_000}})
    b = a+127_000_000_000
    observations.append({"sequence":str(len(observations)+1),
                         "kind":"n04_idle_end",
                         "transport_sequence":str(len(rows)),"data":{"ns":b}})
    native = {"run_id":"a"*32,"trace":{"overflow":False,"events":rows},
              "observations":observations,"n04":{"capture_id":cap["capture_id"]}}
    external = {"run_id":"a"*32,"markers":[
        {"kind":"begin","run_id":"a"*32,"at_ns":a+100_000_000},
        {"kind":"end","run_id":"a"*32,"at_ns":b+100_000_000},
    ]}
    return native, external


class N04TemporalTests(unittest.TestCase):
    def test_manifest_is_frozen(self):
        cases = evidence.load_manifest()["cases"]
        row, = [r for r in cases if (r["case_id"],r["variant"]) == ("N04","idle_125s")]
        self.assertEqual(tuple(row["assertions"]),n04.ASSERTIONS)
        self.assertEqual(len(cases),87)
        self.assertEqual(len({r["case_id"] for r in cases}),28)
        self.assertEqual(sum(len(r["assertions"]) for r in cases),533)

    def test_unique_heartbeat_ack_plus_independent_monotonic(self):
        native, outside = fixture()
        proof = n04.verify_temporal(native,outside)
        self.assertEqual(proof["heartbeat_acks"],25)
        self.assertEqual(proof["duration_ns"],127_000_000_000)

    def test_rejects_adversarial_time_sequence_and_provenance(self):
        changes = [
            ("short_native",lambda n,o: n["observations"][-1]["data"].update(
                ns=n["observations"][0]["data"]["ns"]+124_000_000_000)),
            ("short_outer",lambda n,o: o["markers"][1].update(
                at_ns=o["markers"][0]["at_ns"]+124_000_000_000)),
            ("missing_ack",lambda n,o: n["trace"]["events"][3].update(kind="corrupt")),
            ("wrong_sequence",lambda n,o: n["trace"]["events"][3]["data"]["record"].update(
                request_sequence="9999")),
            ("reused_ack",lambda n,o: n["trace"]["events"][5]["data"]["record"].update(
                request_sequence="11")),
            ("other_capture",lambda n,o: n["trace"]["events"][2]["capture"].update(
                capture_id="f"*64)),
            ("replay_watermark",lambda n,o: n["observations"][4].update(
                transport_sequence=n["observations"][3]["transport_sequence"])),
            ("no_samples",lambda n,o: n.update(observations=[
                r for r in n["observations"] if r["kind"] != "n04_lease_sample"])),
            ("lease_gap",lambda n,o: n["observations"][7]["data"].update(
                ns=n["observations"][6]["data"]["ns"]+16_000_000_000)),
            ("wrong_run",lambda n,o: o.update(run_id="d"*32)),
            ("not_started",lambda n,o: n["trace"]["events"][3]["data"]["record"]
                ["event"]["payload"].update(phase="catalog")),
            ("premature_stop",lambda n,o: n["trace"]["events"][4].update(
                kind="protocol_event",data={"record":{"request_sequence":"500",
                    "event":{"type":"stopped","payload":{}}}})),
            ("trust_producer_pass",lambda n,o: n.update(
                observations=[],status="pass")),
        ]
        for label, mutate in changes:
            with self.subTest(label=label):
                native, outside = fixture()
                mutate(native,outside)
                with self.assertRaises((ValueError,KeyError,TypeError)):
                    n04.verify_temporal(native,outside)


if __name__=="__main__":
    unittest.main()
