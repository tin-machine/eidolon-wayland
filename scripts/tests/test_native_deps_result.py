"""UID/nonce/所有資源/自己完結keymapの欠落を受入成功にしない。"""
import copy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
from quest_checks import native_deps_result


class NativeDependenciesTests(unittest.TestCase):
    def fixture(self):
        nonce, build = "a" * 32, {"profile": "debug"}
        deps = {"xkeyboard-config": {"keymaps": {name: {"sha256": digit * 64, "bytes": 70000} for name, digit in [("us", "1"), ("jis", "2")]}}}
        rows = []
        for i in range(10):
            report = dict(state="pass", error=None, uid=4242, nonce=hashlib.sha256(f"{nonce}:{i}".encode()).hexdigest()[:16],
                          display_created=True, shm_global=True, sync_done=True, sync_delete_id=True, received_fds=1,
                          fd_cloexec=True, same_memfd=True, mapped_bytes=4096, mapped_regions_after=0, audited_fds=8,
                          owned_fds_closed=True, socket_cleaned=True, elapsed_ms=15,
                          keymaps=[dict(layout=name, sha256=deps["xkeyboard-config"]["keymaps"][name]["sha256"], bytes=70000,
                                        include_paths=0, base="a", shifted="A", shifted_two='"' if name == "jis" else "@") for name in ["us", "jis"]])
            rows.append(dict(cycle=i + 1, report=report, fds=100, native_heap_bytes=123456))
        check = dict(case="native-deps", nonce=nonce, result="pass", cycles=rows, last_handle="1", accept_ms=1, stop_ms=10,
                     after_stop=dict(state="Stopped", dependency_workers=0, helper_workers=0, render=dict(workers=0), transport=dict(workers=0, owned_connections=0)))
        snapshot = dict(schema=1, build_info=build, check=check,
                        runtime=dict(code=0, handle="2", registry=dict(runtimes=1, workers=1),
                                     runtime=dict(state="Ready", activity_visible=True, xr_state="FOCUSED", dependency_workers=0, transport=dict(state="Idle"))))
        return snapshot, nonce, build, deps

    def test_real_primitive_proof_is_required_for_every_cycle(self):
        snapshot, nonce, build, deps = self.fixture()
        self.assertTrue(native_deps_result(snapshot, nonce, build, 4242, deps))
        self.assertFalse(native_deps_result(snapshot, "old", build, 4242, deps))
        for key, value in [("uid", 4243), ("nonce", "old"), ("sync_done", False), ("sync_delete_id", False), ("fd_cloexec", False),
                           ("same_memfd", False), ("received_fds", 0), ("mapped_bytes", 0), ("mapped_regions_after", 1),
                           ("owned_fds_closed", False), ("socket_cleaned", False), ("audited_fds", 7)]:
            bad = copy.deepcopy(snapshot); bad["check"]["cycles"][5]["report"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                native_deps_result(bad, nonce, build, 4242, deps)

    def test_layout_source_fallback_and_stale_runtime_cannot_pass(self):
        snapshot, nonce, build, deps = self.fixture()
        for key, value in [("sha256", "0" * 64), ("bytes", 1), ("include_paths", 1), ("shifted_two", "@")]:
            bad = copy.deepcopy(snapshot); bad["check"]["cycles"][0]["report"]["keymaps"][1][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                native_deps_result(bad, nonce, build, 4242, deps)
        snapshot["runtime"]["handle"] = "1"
        self.assertFalse(native_deps_result(snapshot, nonce, build, 4242, deps))


if __name__ == "__main__":
    unittest.main()
