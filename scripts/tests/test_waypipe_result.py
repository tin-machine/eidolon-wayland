"""版・listen pathだけの偽成功と、停止前のWaypipe残存を拒否する。"""
import copy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
from quest_checks import waypipe_result


class WaypipeTests(unittest.TestCase):
    def test_elf_socket_wait_cancel_and_scope_are_required(self):
        nonce, build, dependency = "a" * 32, {"profile": "debug"}, {"sha256": "b" * 64}
        def report(index, cancel=False):
            return dict(uid=4242, version="waypipe 0.11.3", error=None, cancelled=cancel, directory_removed=True, owned_children=0, cycles=3,
                waypipe=dict(nonce=hashlib.sha256(f"{nonce}:{index}".encode()).hexdigest()[:16], executable_sha256=dependency["sha256"],
                    child_uid=4242, child_context="fixture", features=["lz4", "zstd"], unavailable=["dmabuf", "video", "vaapi"], owned_groups=0, clients_after=0, objects_after=0,
                    socket_cleaned=True, stop_ms=6, waited=3, compositor_connections=3, transfer_connections=3))
        result = dict(case="waypipe", nonce=nonce, result="pass", last_handle="1", accept_ms=1, stop_ms=25,
            cycles=[dict(cycle=i+1, report=report(i), fds=70) for i in range(3)],
            after_stop=dict(state="Stopped", helper=report(3, True), helper_workers=0, dependency_workers=0, compositor_workers=0,
                render=dict(workers=0), transport=dict(workers=0)))
        snapshot = dict(schema=1, build_info=build, check=result, runtime=dict(code=0, handle="2", registry=dict(runtimes=1, workers=1),
            runtime=dict(state="Ready", activity_visible=True, xr_state="FOCUSED", helper_workers=0, transport=dict(state="Idle"))))
        self.assertTrue(waypipe_result(snapshot, nonce, build, 4242, dependency))
        for key, value in [("executable_sha256", "c"*64), ("waited", 0), ("compositor_connections", 0), ("transfer_connections", 0),
                           ("owned_groups", 1), ("clients_after", 1), ("socket_cleaned", False), ("features", []), ("child_uid", 4243), ("child_context", "")]:
            bad = copy.deepcopy(snapshot)
            bad["check"]["cycles"][0]["report"]["waypipe"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                waypipe_result(bad, nonce, build, 4242, dependency)
        bad = copy.deepcopy(snapshot)
        bad["check"]["after_stop"]["helper"]["cancelled"] = False
        with self.assertRaises(ValueError):
            waypipe_result(bad, nonce, build, 4242, dependency)
        with self.assertRaises(ValueError):
            waypipe_result(snapshot, nonce, build, 4243, dependency)
        snapshot["runtime"]["runtime"]["xr_state"] = "IDLE"
        self.assertFalse(waypipe_result(snapshot, nonce, build, 4242, dependency))
