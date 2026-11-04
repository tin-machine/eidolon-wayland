import copy
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).parents[1]))
from quest_checks import runtime_result


class ResultTests(unittest.TestCase):
    def setUp(self):
        self.info = {"apk_version_code": 5, "source_digest": "fixture"}
        self.snapshot = {"schema": 1, "build_info": self.info,
                         "check": {"case": "runtime", "nonce": "new", "result": "pass", "panic_converted": True,
                                   "cycles": [{"cycle": i, "handle": str(i), "owned_workers": 0} for i in range(1, 11)]},
                         "runtime": {"code": 0, "handle": "11", "runtime": {"state": "Ready", "activity_visible": True, "xr_state": "FOCUSED"}, "registry": {"runtimes": 1, "workers": 1}}}

    def test_old_result_or_different_apk_never_passes(self):
        self.assertTrue(runtime_result(self.snapshot, "new", self.info))
        self.assertFalse(runtime_result(self.snapshot, "old", self.info))
        self.assertFalse(runtime_result(self.snapshot, "new", {"apk_version_code": 6}))
        self.snapshot["check"]["result"] = "running"
        self.assertFalse(runtime_result(self.snapshot, "new", self.info))

    def test_incomplete_recovery_or_resource_leak_never_passes(self):
        for target, value in [("handle", "10"), ("registry", {"runtimes": 2, "workers": 2}), ("runtime", {"state": "Failed"})]:
            bad = copy.deepcopy(self.snapshot)
            bad["runtime"][target] = value
            self.assertFalse(runtime_result(bad, "new", self.info))
        bad = copy.deepcopy(self.snapshot)
        bad["runtime"]["runtime"]["xr_state"] = "IDLE"
        self.assertFalse(runtime_result(bad, "new", self.info))
        self.snapshot["check"]["cycles"][0]["owned_workers"] = 1
        with self.assertRaises(ValueError):
            runtime_result(self.snapshot, "new", self.info)

    def test_explicit_failure_is_terminal(self):
        self.snapshot["check"]["result"] = "fail"
        with self.assertRaises(ValueError):
            runtime_result(self.snapshot, "new", self.info)
