"""serial/snapshot/treeの個別証拠と実機scopeを欠く結果を受け入れない。"""
import copy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
from quest_checks import wayland_state_result


class WaylandStateTests(unittest.TestCase):
    def fixture(self):
        nonce, build = "a" * 32, {"profile": "debug"}
        transform = dict(client=dict(protocol_error=False, checkpoints=16, withheld=0), checkpoints=16,
            immutable_checks=15, transforms=255, scales=3, cadence_checks=16, window_ids=["1"])
        modes = [("lifecycle", "Lifecycle"), ("transforms", "Transforms"), ("frame_gates", "FrameGates"), ("tree", "Tree"),
            ("bad_serial", "BadSerial"), ("stale_configure", "StaleConfigure"), ("premature_buffer", "PrematureBuffer"),
            ("unconfigured_remap", "UnconfiguredRemap"), ("bad_scale", "BadScale"), ("positioner_overflow", "PositionerOverflow")]
        rows = []
        for i, (scenario, mode) in enumerate(modes):
            proof = copy.deepcopy(transform)
            if i == 0:
                proof.update({key: True for key in ["mapped", "unmapped", "remapped", "resize_commit_only", "consecutive_configures", "recreated", "rejected_resize"]})
                proof["window_ids"] = ["1", "2"]
                proof["client"]["withheld"] = 1
            elif i == 2:
                proof["withheld"] = 3
                proof["client"]["withheld"] = 3
            elif i == 3:
                proof.update(tree_flags=127, tree_immutable_checks=5, clip_checks=2, content_reused=True)
                proof["client"].update(popup_done=3, popup_positions=[[10, 10, 8, 8], [6, 6, 6, 6], [24, 26, 8, 8]])
            recovery = None
            if i >= 4:
                recovery, proof = proof, dict(client=dict(protocol_error=True))
            report = dict(state="pass", error=None, fixture_revision="eidolon-wayland-state-v1", callback_clock="virtual", scenario=mode,
                nonce=hashlib.sha256(f"{nonce}:{i}".encode()).hexdigest()[:16], uid=4242,
                proof=proof, recovery=recovery, elapsed_ms=100, **{field: 0 for field in ["surfaces_after", "clients_after", "objects_after",
                    "windows_after", "pixel_bytes_after", "pool_bytes_after", "pool_count_after", "fixture_fds_after", "fixture_maps_after"]})
            rows.append(dict(cycle=i+1, scenario=scenario, report=report, fds=70, native_heap_bytes=123456))
        result = dict(case="wayland-state", nonce=nonce, result="pass", last_handle="1", cycles=rows, accept_ms=1, stop_ms=20,
            after_stop=dict(state="Stopped", compositor_workers=0, dependency_workers=0, helper_workers=0,
                render=dict(workers=0), transport=dict(workers=0)))
        snapshot = dict(schema=1, build_info=build, check=result, runtime=dict(code=0, handle="2", registry=dict(runtimes=1, workers=1),
            runtime=dict(state="Ready", activity_visible=True, xr_state="FOCUSED", compositor_workers=0, transport=dict(state="Idle"))))
        return snapshot, nonce, build

    def test_reused_ids_missing_immutable_transform_tree_or_recovery_proof_never_pass(self):
        snapshot, nonce, build = self.fixture()
        self.assertTrue(wayland_state_result(snapshot, nonce, build, 4242))
        for index, key, value in [(0, "window_ids", ["1", "1"]), (0, "resize_commit_only", False), (0, "immutable_checks", 0),
                                 (1, "transforms", 127), (2, "withheld", 2), (3, "tree_flags", 63), (3, "clip_checks", 1),
                                 (3, "content_reused", False), (3, "tree_immutable_checks", 0)]:
            bad = copy.deepcopy(snapshot)
            bad["check"]["cycles"][index]["report"]["proof"][key] = value
            with self.subTest(index=index, key=key), self.assertRaises(ValueError):
                wayland_state_result(bad, nonce, build, 4242)
        bad = copy.deepcopy(snapshot)
        bad["check"]["cycles"][6]["report"]["recovery"] = None
        with self.assertRaises(ValueError):
            wayland_state_result(bad, nonce, build, 4242)

    def test_wrong_uid_resource_leak_and_native_only_result_never_pass_as_ui(self):
        snapshot, nonce, build = self.fixture()
        self.assertFalse(wayland_state_result(snapshot, "old", build, 4242))
        with self.assertRaises(ValueError):
            wayland_state_result(snapshot, nonce, build, 4243)
        bad = copy.deepcopy(snapshot)
        bad["check"]["cycles"][4]["report"]["pool_bytes_after"] = 4096
        with self.assertRaises(ValueError):
            wayland_state_result(bad, nonce, build, 4242)
        snapshot["runtime"]["runtime"]["xr_state"] = "IDLE"
        self.assertFalse(wayland_state_result(snapshot, nonce, build, 4242))
