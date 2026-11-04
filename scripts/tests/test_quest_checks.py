import copy
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).parents[1]))
from quest_checks import runtime_result, surface_result, credentials_result, ssh_result, product_ssh_result


class ResultTests(unittest.TestCase):
    def test_product_ssh_needs_same_host_path_live_render_stop_and_fresh_return(self):
        info = {"profile": "debug"}
        snapshot = {"schema": 1, "build_info": info, "check": {"case": "ssh-product", "nonce": "new", "result": "pass",
                    "product_host_path": True, "render_frames_advanced": True, "stop": {"old_runtime_destroyed": True}, "old_handle": "1", "new_handle": "2",
                    "transport": {"probe": {"uid": 4242, "token_matched": True, "eof": True, "exit_status": 0, "exit_signal": False}}},
                    "runtime": {"handle": "2", "runtime": {"state": "Ready", "activity_visible": True, "xr_state": "FOCUSED",
                                 "transport": {"state": "Idle", "owned_connections": 0}, "render": {"targets": [{}]}}}}
        self.assertTrue(product_ssh_result(snapshot, "new", info, 4242))
        self.assertFalse(product_ssh_result(snapshot, "old", info, 4242))
        with self.assertRaises(ValueError): product_ssh_result(snapshot, "new", info, 4243)
        for field in ["product_host_path", "render_frames_advanced"]:
            bad = copy.deepcopy(snapshot); bad["check"][field] = False
            with self.assertRaises(ValueError): product_ssh_result(bad, "new", info, 4242)
        snapshot["runtime"]["handle"] = "1"
        self.assertFalse(product_ssh_result(snapshot, "new", info, 4242))

    def test_ssh_gate_checks_remote_uid_pin_before_secret_owned_stop_and_new_runtime(self):
        rows = []
        for index, mode in enumerate(["normal", "mismatch", "auth_failed", "cancel_credential"], 1):
            report = {"probe": {"uid": 4242, "token_matched": True, "eof": True, "exit_status": 0, "exit_signal": False}}
            if mode in {"mismatch", "auth_failed"}: report["error"] = "HOST_KEY_MISMATCH" if mode == "mismatch" else "AUTH_FAILED"
            rows.append({"mode": mode, "handle": str(index), "result": "pass", "transport": report,
                         "credential_requests": 0 if mode == "mismatch" else 1,
                         "credentials_provided": 0 if mode in {"mismatch", "cancel_credential"} else 1,
                         "stale_disconnect_safe": mode == "normal", "after_stop": {"workers": 0, "tokio_threads": 0, "owned_connections": 0}})
        info = {"profile": "debug"}
        snapshot = {"schema": 1, "build_info": info, "check": {"case": "ssh", "nonce": "new", "result": "pass", "last_handle": "4", "profile_unchanged": True, "cases": rows},
                    "runtime": {"handle": "5", "runtime": {"state": "Ready", "activity_visible": True, "xr_state": "FOCUSED", "transport": {"state": "Idle"}}}}
        self.assertTrue(ssh_result(snapshot, "new", info, 4242))
        self.assertFalse(ssh_result(snapshot, "old", info, 4242))
        with self.assertRaises(ValueError): ssh_result(snapshot, "new", info, 4243)
        for change in ["before_pin", "leak", "reuse"]:
            bad = copy.deepcopy(snapshot)
            if change == "before_pin": bad["check"]["cases"][1]["credentials_provided"] = 1
            elif change == "leak": bad["check"]["cases"][0]["after_stop"]["owned_connections"] = 1
            else: bad["check"]["cases"][1]["handle"] = "1"
            with self.assertRaises(ValueError): ssh_result(bad, "new", info, 4242)
        snapshot["runtime"]["handle"] = "4"
        self.assertFalse(ssh_result(snapshot, "new", info, 4242))

    def test_credentials_gate_needs_all_protections_and_no_secret_in_diagnostics(self):
        flags = ("idempotent", "public_only", "file_mode_0600", "aes_nonexportable", "known_pin", "unknown_pin_rejected",
                 "mismatch_pin_rejected", "unsolicited_credential_rejected", "tamper_rejected", "key_loss_rejected",
                 "no_silent_regeneration", "production_unchanged", "save_loss_rejected")
        info = {"profile": "debug"}
        snapshot = {"schema": 1, "build_info": info, "check": {"case": "credentials", "nonce": "new", "result": "pass", "stopped_handle": "1", **dict.fromkeys(flags, True)},
                    "runtime": {"handle": "2", "runtime": {"state": "Ready", "activity_visible": True, "xr_state": "FOCUSED", "render": {"workers": 1}}}}
        self.assertTrue(credentials_result(snapshot, "new", info))
        self.assertFalse(credentials_result(snapshot, "old", info))
        for field in flags:
            bad = copy.deepcopy(snapshot); bad["check"][field] = False
            with self.assertRaises(ValueError): credentials_result(bad, "new", info)
        snapshot["runtime"]["invalid_fixture"] = "-----BEGIN OPENSSH PRIVATE KEY-----"
        with self.assertRaises(ValueError): credentials_result(snapshot, "new", info)

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

    def test_surface_gate_requires_fresh_generation_readback_listener_cleanup_and_resumed_frames(self):
        key = {"session": "1", "window": "1", "scene": "1", "generation": "1"}
        target = {"key": key, "frames": 3, "readback": True, "width": 1024, "height": 768,
                  "vendor": "fixture", "renderer": "fixture", "version": "fixture"}
        snapshot = {"schema": 1, "build_info": self.info,
                    "check": {"case": "pattern", "nonce": "new", "result": "pass", "cycles": [target],
                              "paused_frames_stable": True, "stale_detach_safe": True, "duplicate_safe": True,
                              "adapter": {"created": 1, "destroyed": 0, "bindings": 1, "listeners": 1, "pending": 0}},
                    "runtime": {"runtime": {"state": "Ready", "activity_visible": True, "xr_state": "FOCUSED",
                                            "render": {"workers": 1, "error": None, "paused": False, "targets": [dict(target, frames=6)]}}}}
        self.assertTrue(surface_result(snapshot, "new", self.info, "pattern"))
        self.assertFalse(surface_result(snapshot, "old", self.info, "pattern"))
        for path, value in [("readback", False), ("generation", "0"), ("listeners", 2), ("paused_frames_stable", False)]:
            bad = copy.deepcopy(snapshot)
            check = bad["check"]
            if path == "readback": check["cycles"][0][path] = value
            elif path == "generation": check["cycles"][0]["key"][path] = value
            elif path == "listeners": check["adapter"][path] = value
            else: check[path] = value
            with self.assertRaises(ValueError): surface_result(bad, "new", self.info, "pattern")
        snapshot["runtime"]["runtime"]["render"]["targets"][0]["frames"] = 3
        self.assertFalse(surface_result(snapshot, "new", self.info, "pattern"))
