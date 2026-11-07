import copy
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).parents[1]))
from quest_checks import runtime_result, surface_result, credentials_result, ssh_result, product_ssh_result, forward_result, remote_cleanup_result


class ResultTests(unittest.TestCase):
    def test_remote_cleanup_requires_real_lease_new_epochs_and_verified_receipts(self):
        info, nonce = {"profile": "debug"}, "a" * 32
        rows = []
        modes = ["cancel_before_exec", "prepare_eof", "stop", "control_eof", "ssh_drop", "ssh_drop_recreate", "lease_expire"]
        def stopped(handle):
            return {"handle": str(handle), "accept_ms": 0, "stop_ms": 10,
                    "after": {"workers": 0, "tokio_threads": 0, "owned_connections": 0, "forward": {"active": 0}, "pending_cleanup": []}}
        for i, mode in enumerate(modes, 1):
            scenario = "ssh_drop" if mode == "ssh_drop_recreate" else mode
            handle, session, token = i * 10, i * 10, f"{i:016x}"
            child = None if mode in {"cancel_before_exec", "prepare_eof"} else {"pid": 42, "group": 42, "start": "123", "state": "S"}
            remote = {"phase": "finished", "cleanup_pending": False, "eof": True, "exit_status": 0, "exit_signal": False, "child": child,
                      "finished": {"nonce": token, "uid": 4242, "reason": {"stop": "Stop", "lease_expire": "LeaseExpired"}.get(mode, "EOF"),
                                   "child": child, "cleaned": True, "group_alive": False, "pipes_closed": True}}
            report = {"nonce": token, "uid": 4242, "scenario": scenario, "state": "pass", "error": None, "local_cleaned": True,
                      "forwarding": {"active": 0}, "remote_cleaned": True, "remote": remote, "elapsed_ms": 60100, "pending_cleanup": []}
            row = {"mode": mode, "scenario": scenario, "nonce": token, "first_handle": str(handle), "first_session": str(session),
                   "report": report, "stop": stopped(handle), "result": "pass", "observed_live": True}
            if mode == "cancel_before_exec":
                report.update(remote=None, remote_not_started=True, remote_cleaned=False)
            elif mode.startswith("ssh_drop"):
                report.update(error="SSH_DROPPED", remote_cleaned=False)
                remote["cleanup_pending"] = True
                row.update(next_session=str(session + 1), next_handle=str(handle), after_loss={"pending_cleanup": [{}]},
                           recovered={"state": "Ready", "pending_cleanup": [], "cleanup_audits": [
                               {"nonce": token, "origin_session": str(session), "current_session": str(session + 1), "verified": True, "child": child,
                                "active": False, "orphaned": False, "child_alive": False, "error": None}]})
                if mode == "ssh_drop_recreate":
                    row.update(next_handle=str(handle + 1), stop=stopped(handle + 1), old_stop=stopped(handle))
                    row["old_stop"]["after"]["pending_cleanup"] = [{}]
            rows.append(row)
        snapshot = {"schema": 1, "build_info": info, "check": {"case": "remote-cleanup", "nonce": nonce, "result": "pass", "rows": rows, "last_handle": "70"},
                    "runtime": {"handle": "71", "runtime": {"state": "Ready", "activity_visible": True, "xr_state": "FOCUSED", "transport": {"state": "Idle", "pending_cleanup": []}}}}
        self.assertTrue(remote_cleanup_result(snapshot, nonce, info, 4242))
        # 同じ7故障のnative証拠でも、描画を伴わないinstrumentationをUI復帰として扱わない。
        native = copy.deepcopy(snapshot)
        native["process_id"] = 1234
        destroyed = {"schema": 1, "code": 1, "registry": {"runtimes": 0, "workers": 0}}
        native["runtime"] = destroyed
        native["check"]["instrumentation"] = {
            "mode": "instrumentation", "case": "remote-cleanup", "nonce": nonce, "uid": 10159, "pid": 1234, "result": "pass",
            "recovery_handle": "71", "accept_ms": 0, "stop_ms": 10,
            "ready": {"code": 0, "handle": "71", "registry": {"runtimes": 1, "workers": 1}, "runtime": {
                "state": "Ready", "error": None, "activity_visible": False, "xr_state": "IDLE",
                "transport": {"state": "Idle", "journal_ready": True, "pending_cleanup": []}}},
            "stopped": {"code": 0, "handle": "71", "runtime": {"state": "Stopped", "helper_workers": 0, "render": {"workers": 0},
                "transport": {"workers": 0, "tokio_threads": 0, "owned_connections": 0, "forward": {"active": 0}}}},
            "after_destroy": destroyed,
        }
        self.assertTrue(remote_cleanup_result(native, nonce, info, 4242, instrumentation=True, apk_uid=10159))
        self.assertFalse(remote_cleanup_result(native, nonce, info, 4242))
        with self.assertRaises(ValueError):
            remote_cleanup_result(native, nonce, info, 4242, instrumentation=True, apk_uid=10160)
        bad = copy.deepcopy(native); bad["check"]["rows"][-1]["report"]["elapsed_ms"] = 1000
        with self.assertRaises(ValueError):
            remote_cleanup_result(bad, nonce, info, 4242, instrumentation=True, apk_uid=10159)
        bad = copy.deepcopy(native); bad["check"]["instrumentation"]["after_destroy"]["registry"]["runtimes"] = 1
        with self.assertRaises(ValueError):
            remote_cleanup_result(bad, nonce, info, 4242, instrumentation=True, apk_uid=10159)
        self.assertFalse(remote_cleanup_result(snapshot, "old", info, 4242))
        with self.assertRaises(ValueError): remote_cleanup_result(snapshot, nonce, info, 4243)
        for change in ["short_lease", "false_verified", "lost_pending", "old_epoch", "changed_pid", "leaked_worker"]:
            bad = copy.deepcopy(snapshot)
            if change == "short_lease": bad["check"]["rows"][6]["report"]["elapsed_ms"] = 100
            elif change == "false_verified": bad["check"]["rows"][4]["recovered"]["cleanup_audits"][0]["verified"] = False
            elif change == "lost_pending": bad["check"]["rows"][5]["old_stop"]["after"]["pending_cleanup"] = []
            elif change == "old_epoch": bad["check"]["rows"][4]["next_session"] = "1"
            elif change == "changed_pid": bad["check"]["rows"][4]["recovered"]["cleanup_audits"][0]["child"] = {"pid": 43}
            else: bad["check"]["rows"][2]["stop"]["after"]["workers"] = 1
            with self.assertRaises(ValueError): remote_cleanup_result(bad, nonce, info, 4242)

    def test_forward_needs_bytes_hash_half_close_bounded_channels_cleanup_and_new_runtime(self):
        info, nonce = {"profile": "debug"}, "a" * 32
        total = 1 + 65536 + 4 * 8 * 1024 * 1024
        report = {"state": "pass", "nonce": nonce[:16], "uid": 4242, "channels": 6, "bytes": total, "error": None,
                  "hashes_matched": True, "half_close": True, "forward_cancelled": True, "local_cleaned": True, "remote_cleaned": True,
                  "forwarding": {"accepted": 6, "active": 0, "peak": 4, "failed": 0, "remote_to_local": total, "local_to_remote": total + 54, "copy_buffer_limit": 512 * 1024}}
        child = {"pid": 123, "group": 123, "start": "456", "state": "R"}
        report["remote"] = {"phase": "finished", "cleanup_pending": False, "eof": True, "exit_status": 0, "exit_signal": False, "error": None, "child": child,
                            "finished": {"schema": 1, "nonce": nonce[:16], "uid": 4242, "reason": "Completed", "exit_status": 0, "exit_signal": None,
                                         "cleaned": True, "pipes_closed": True, "group_alive": False, "child": copy.deepcopy(child)}}
        snapshot = {"schema": 1, "build_info": info, "check": {"case": "forward", "nonce": nonce, "result": "pass", "last_handle": "1", "credential_requests": 1,
                    "transport": {"forward_probe": report}, "after_stop": {"workers": 0, "tokio_threads": 0, "owned_connections": 0, "forward": {"active": 0}}},
                    "runtime": {"handle": "2", "runtime": {"state": "Ready", "activity_visible": True, "xr_state": "FOCUSED", "transport": {"state": "Idle"}}}}
        self.assertTrue(forward_result(snapshot, nonce, info, 4242))
        extended = copy.deepcopy(snapshot)
        extended["check"]["transport"]["forward_probe"]["forwarding"]["copy_buffer_limit"] = 768 * 1024
        self.assertTrue(forward_result(extended, nonce, info, 4242))
        for limit in [640 * 1024, 768 * 1024 + 1, float(768 * 1024), {}, []]:
            bad = copy.deepcopy(extended)
            bad["check"]["transport"]["forward_probe"]["forwarding"]["copy_buffer_limit"] = limit
            with self.assertRaises(ValueError): forward_result(bad, nonce, info, 4242)
        for peak in [7, 12, 4.0]:
            bad = copy.deepcopy(extended)
            bad["check"]["transport"]["forward_probe"]["forwarding"]["peak"] = peak
            with self.assertRaises(ValueError): forward_result(bad, nonce, info, 4242)
        self.assertFalse(forward_result(snapshot, "old", info, 4242))
        with self.assertRaises(ValueError): forward_result(snapshot, nonce, info, 4243)
        for key in ["hashes_matched", "half_close", "forward_cancelled", "local_cleaned", "remote_cleaned"]:
            bad = copy.deepcopy(snapshot); bad["check"]["transport"]["forward_probe"][key] = False
            with self.assertRaises(ValueError): forward_result(bad, nonce, info, 4242)
        for key, value in [("bytes", 1), ("peak", 9), ("active", 1), ("copy_buffer_limit", 0)]:
            bad = copy.deepcopy(snapshot)
            target = bad["check"]["transport"]["forward_probe"]
            (target if key == "bytes" else target["forwarding"])[key] = value
            with self.assertRaises(ValueError): forward_result(bad, nonce, info, 4242)
        for change in ["pending", "child_reuse", "missing_eof", "pipes", "uid"]:
            bad = copy.deepcopy(snapshot); remote = bad["check"]["transport"]["forward_probe"]["remote"]
            if change == "pending": remote["cleanup_pending"] = True
            elif change == "child_reuse": remote["finished"]["child"]["start"] = "0"
            elif change == "missing_eof": remote["eof"] = False
            elif change == "pipes": remote["finished"]["pipes_closed"] = False
            else: remote["finished"]["uid"] = 4243
            with self.assertRaises(ValueError): forward_result(bad, nonce, info, 4242)
        snapshot["runtime"]["handle"] = "1"
        self.assertFalse(forward_result(snapshot, nonce, info, 4242))

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
                 "no_silent_regeneration", "production_unchanged", "save_loss_rejected", "profile_roundtrip", "profile_mode_0600",
                 "unconfirmed_pin_preserved", "stale_form_rejected", "explicit_pin_change", "production_profile_unchanged")
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
