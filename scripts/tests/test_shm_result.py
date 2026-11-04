"""SIGBUS回復・実pixel検証・通常UI復帰の証拠を混同しない。"""
import copy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
from quest_checks import shm_result


class ShmGateTests(unittest.TestCase):
    def fixture(self):
        nonce, build = "a" * 32, {"profile": "debug"}
        normal = dict(globals=[[name, version] for name, version in [("wl_compositor", 4), ("wl_subcompositor", 1),
            ("wl_shm", 1), ("wl_output", 3), ("wl_seat", 5), ("xdg_wm_base", 1), ("wl_data_device_manager", 3)]],
            frames=100, configures=1, acks=1, releases=100, callbacks=100, enters=1, leaves=1,
            cancellations=2, empty_selection=True, output_mode=[1024, 768, 60000], output_scale=1,
            output_geometry=True, output_done=True, seat_capabilities=0, protocol_error=False)
        report = dict(state="pass", error=None, mode="Truncate", fixture_revision="eidolon-shm-client-v1", uid=4242,
            nonce=hashlib.sha256(f"{nonce}:0".encode()).hexdigest()[:16], verified_hashes=100, bad_maps=1,
            server_copies=100, server_releases=100, server_callbacks=100, elapsed_ms=1000,
            client=dict(protocol_error=True), recovery=normal, **{field: 0 for field in ["surfaces_after", "clients_after",
                "objects_after", "pixel_bytes_after", "pool_bytes_after", "pool_count_after", "fixture_fds_after", "fixture_maps_after"]})
        result = dict(case="shm-truncate", nonce=nonce, result="pass", last_handle="1", accept_ms=1, stop_ms=20,
            cycles=[dict(cycle=1, scenario="truncate", report=report, fds=70, native_heap_bytes=123456)],
            after_stop=dict(state="Stopped", compositor_workers=0, dependency_workers=0, helper_workers=0,
                render=dict(workers=0), transport=dict(workers=0)))
        snapshot = dict(schema=1, build_info=build, check=result, runtime=dict(code=0, handle="2",
            registry=dict(runtimes=1, workers=1), runtime=dict(state="Ready", activity_visible=True,
                xr_state="FOCUSED", compositor_workers=0, transport=dict(state="Idle"))))
        return snapshot, nonce, build

    def test_missing_sigbus_recovery_hash_or_real_cleanup_never_passes(self):
        snapshot, nonce, build = self.fixture()
        self.assertTrue(shm_result(snapshot, nonce, build, 4242, "shm-truncate"))
        for key, value in [("bad_maps", 0), ("uid", 4243), ("verified_hashes", 99), ("fixture_maps_after", 1),
                           ("pool_bytes_after", 4096), ("objects_after", 1), ("nonce", "old")]:
            bad = copy.deepcopy(snapshot)
            bad["check"]["cycles"][0]["report"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                shm_result(bad, nonce, build, 4242, "shm-truncate")
        for key, value in [("frames", 99), ("seat_capabilities", 1), ("empty_selection", False), ("leaves", 0)]:
            bad = copy.deepcopy(snapshot)
            bad["check"]["cycles"][0]["report"]["recovery"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                shm_result(bad, nonce, build, 4242, "shm-truncate")

    def test_stale_native_only_or_unreaped_worker_cannot_be_ui_acceptance(self):
        snapshot, nonce, build = self.fixture()
        self.assertFalse(shm_result(snapshot, "old", build, 4242, "shm-truncate"))
        snapshot["runtime"]["runtime"]["xr_state"] = "IDLE"
        self.assertFalse(shm_result(snapshot, nonce, build, 4242, "shm-truncate"))
        snapshot["check"]["after_stop"]["compositor_workers"] = 1
        with self.assertRaises(ValueError):
            shm_result(snapshot, nonce, build, 4242, "shm-truncate")
