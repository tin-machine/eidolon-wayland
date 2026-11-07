import copy
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from linux_shm_checks import CASES, cleaned, inspect, outcome

loader = importlib.machinery.SourceFileLoader("linux_shm_operator", str(Path(__file__).resolve().parents[1] / "quest-run-linux-shm"))
spec = importlib.util.spec_from_loader(loader.name, loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)

NONCE = "a" * 32
DIGEST = "b" * 64
BINDING = dict(uid=10159, pid=912, version=166, digest=DIGEST)


def check(case="virtual", result="pass"):
    value = dict(case=CASES[case], nonce=NONCE, result=result, apk_uid=BINDING["uid"], physical_hmd=False,
                 virtual_frame_clock=case == "virtual", probe_ready=True, disconnected=True, scope_cleared=True,
                 journal_cleared=True, remote_cleanup_confirmed=True, normal_runtime_restored=True)
    if case == "sdk":
        value.update(normal_gui_enabled=False, physical_input=False, runtime_adopted=True, sdk_pose_applied=True,
                     sdk_successful_draw_observed=True, local_native_sdk_cleaned=True, product_settings_untouched=True,
                     old_runtime_destroyed=True, normal_sdk_restored=True, owner_lease_released=True, stage="finished",
                     source_verified_hashes=100, target_driven_callbacks=100, first_draw_frames=3, last_draw_frames=80,
                     last_content_frame="99", sdk_entity="12", sdk_target=dict(session="7", window="1", scene="1", generation="1"))
    else:
        value.update(gui_enabled=False, gpu_verified=False, credential_bound=True, local_cleaned=True, destroyed=True,
                     termux_configuration_unchanged=True, probe_runtime="2")
        if case == "virtual":
            value.update(shm_verified_hashes=100, shm_copies=100, shm_callbacks=100, wayland_connections=2,
                         probe_capability_unchanged=True, journal_pending=False)
        else:
            absent = result == "not_required"
            value.update(recovery_not_required=absent, recovery_queried=not absent, receipt_verified=not absent,
                         remote_cleanup_confirmed=not absent)
    return value


def snapshot(value=None, seq=2, handle="3"):
    target = dict(session="1", window="1", scene="1", generation="9")
    runtime = dict(state="Ready", error=None, transport=dict(state="Idle", owned_connections=0, pending_cleanup=[]),
                   activity_visible=True, xr_state="FOCUSED", scene="1", shutdown=dict(sdk_bindings=1),
                   render=dict(targets=[dict(role="diagnostic", frames=4, key=target)]))
    return dict(schema=1, sequence=str(seq), process_id=BINDING["pid"],
                build_info=dict(profile="debug", target="aarch64-linux-android", apk_version_code=166, source_digest=DIGEST),
                runtime=dict(code=0, handle=handle, runtime=runtime, registry=dict(runtimes=1, workers=1)),
                adapter=dict(targets=[dict(role="diagnostic", target=target, retiring=False, sdk_disposed=False, disposal_failed=False)]), check=value)


def parsed(value, case="virtual"):
    return inspect(json.dumps(value), case=case, nonce=NONCE, **BINDING)


class Checks(unittest.TestCase):
    def test_virtual_success_is_native_only(self):
        observed = parsed(snapshot(check()))
        self.assertTrue(cleaned(observed, "virtual", "1"))
        self.assertEqual(outcome(observed, "virtual", "1"), "pass")
        for key in ("shm_copies", "shm_callbacks", "shm_verified_hashes"):
            raw = snapshot(check()); raw["check"][key] = 99
            with self.assertRaisesRegex(ValueError, "SHM_PROOF_INCOMPLETE"):
                outcome(parsed(raw), "virtual", "1")

    def test_sdk_waits_for_current_display_and_lease_release(self):
        self.assertEqual(outcome(parsed(snapshot(check("sdk")), "sdk"), "sdk", "1"), "pass")
        variants = []
        for key in ("normal_sdk_restored", "owner_lease_released"):
            raw = snapshot(check("sdk")); raw["check"][key] = False; variants.append(raw)
        raw = snapshot(check("sdk")); raw["runtime"]["runtime"]["activity_visible"] = False; variants.append(raw)
        raw = snapshot(check("sdk")); raw["adapter"]["targets"][0]["retiring"] = True; variants.append(raw)
        raw = snapshot(check("sdk")); raw["adapter"]["targets"][0]["target"] = dict(session="1", window="1", scene="2", generation="9"); variants.append(raw)
        for raw in variants:
            observed = parsed(raw, "sdk")
            self.assertFalse(cleaned(observed, "sdk", "1"))
            self.assertIsNone(outcome(observed, "sdk", "1"))

    def test_recovery_not_required_is_never_pass(self):
        observed = parsed(snapshot(check("recover", "not_required")), "recover")
        self.assertEqual(outcome(observed, "recover", "1"), "not_required")
        raw = snapshot(check("recover", "not_required")); raw["check"]["result"] = "pass"
        with self.assertRaisesRegex(ValueError, "RECOVERY_PROOF_INCONSISTENT"):
            outcome(parsed(raw, "recover"), "recover", "1")
        self.assertEqual(outcome(parsed(snapshot(check("recover")), "recover"), "recover", "1"), "pass")

    def test_stale_owner_build_uid_and_coercions_rejected(self):
        for mutate in [lambda x: x.update(process_id=913), lambda x: x["build_info"].update(source_digest="c"*64),
                       lambda x: x["build_info"].update(apk_version_code=165), lambda x: x["check"].update(apk_uid=10160),
                       lambda x: x["check"].update(shm_copies=True), lambda x: x["check"].update(destroyed=1)]:
            raw = snapshot(check()); mutate(raw)
            with self.assertRaises(ValueError): parsed(raw)
        self.assertFalse(cleaned(parsed(snapshot(check(), handle="1")), "virtual", "1"))
        raw = snapshot(check()); raw["runtime"]["registry"]["runtimes"] = True
        self.assertFalse(cleaned(parsed(raw), "virtual", "1"))

    def test_private_unknown_payload_is_dropped_and_duplicate_json_rejected(self):
        raw = snapshot(check()); raw["check"]["private_key"] = "SECRET_CONTENT"
        raw["runtime"]["runtime"]["profiles"] = [{"pin": "SECRET_CONTENT"}]
        self.assertNotIn("SECRET_CONTENT", json.dumps(parsed(raw)))
        with self.assertRaisesRegex(ValueError, "METADATA_DUPLICATE_KEY"):
            inspect('{"schema":1,"schema":1}', case="virtual", nonce=NONCE, **BINDING)


class Clock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now
    def sleep(self, seconds): self.now += seconds


class Device:
    def __init__(self, receipt, after_stop=None, dispatch_error=False):
        self.receipt, self.after_stop = receipt, after_stop
        self.dispatch_error = dispatch_error
        self.launched = self.stopped = False
        self.commands = []
        self.sequence = 0
    def validate(self): return dict(model="Quest 3", api=34)
    def adb(self, *args, **kwargs):
        self.commands.append(args)
        if args[:4] == ("shell", "pm", "list", "packages"): return "package:io.github.tinmachine.eidolon uid:10159\n"
        if args[:4] == ("shell", "run-as", cli.PACKAGE, "id"): return "10159\n"
        if args[:3] == ("shell", "dumpsys", "package"): return "  versionCode=166 minSdk=34 targetSdk=36\n"
        if args[:2] == ("shell", "pidof"): return "912\n"
        if args[:2] == ("shell", "am"):
            if args[-4] == "linux-shm-probe-stop" or args[-4] == "linux-shm-sdk-stop": self.stopped = True
            else: self.launched = True
            if self.dispatch_error and not self.stopped: raise TimeoutError("untrusted adb output")
            return ""
        if args[0] == "exec-out":
            self.sequence += 1
            value = self.after_stop if self.stopped else self.receipt if self.launched else None
            return json.dumps(snapshot(copy.deepcopy(value), self.sequence, "3" if self.launched else "1"))
        raise AssertionError(args)


class Runner(unittest.TestCase):
    def run_case(self, device, case="virtual", timeout=1):
        clock, saved = Clock(), []
        result = cli.execute(device, case, NONCE, timeout, saved.append, clock=clock, sleep=clock.sleep, digest_fn=lambda: DIGEST)
        return result, saved

    def test_success_does_not_stop_or_claim_sdk(self):
        device = Device(check()); result, _ = self.run_case(device)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["sdk_restoration"], "not_checked")
        self.assertFalse(device.stopped)

    def test_failure_restored_stays_failure(self):
        device = Device(check(result="fail")); result, _ = self.run_case(device)
        self.assertEqual(result["status"], "fail")
        self.assertTrue(result["cleanup_confirmed"])

    def test_configuration_failure_is_distinct_from_owner_cleanup(self):
        failed = check(result="fail"); failed["termux_configuration_unchanged"] = False
        result, _ = self.run_case(Device(failed))
        self.assertEqual(result["status"], "fail")
        self.assertTrue(result["cleanup_confirmed"])
        self.assertFalse(result["configuration_preserved"])

    def test_late_same_nonce_result_before_start_does_not_launch(self):
        class OldDevice(Device):
            def adb(self, *args, **kwargs):
                if args[0] == "exec-out": return json.dumps(snapshot(check(), 8, "9"))
                return super().adb(*args, **kwargs)
        device = OldDevice(check()); result, _ = self.run_case(device)
        self.assertEqual(result["failure_code"], "NONCE_ALREADY_OBSERVED")
        self.assertFalse(result["launch_attempted"] or result["stop_attempted"])

    def test_final_digest_change_never_passes_or_claims_cleanup(self):
        device, clock = Device(check(), check(result="cancelled")), Clock()
        calls = 0
        def digest():
            nonlocal calls
            calls += 1
            return DIGEST if calls == 1 else "c" * 64
        result = cli.execute(device, "virtual", NONCE, 1, lambda _: None, clock=clock, sleep=clock.sleep, digest_fn=digest)
        self.assertEqual(result["status"], "cleanup_unconfirmed")
        self.assertFalse(result["cleanup_confirmed"])

    def test_timeout_same_nonce_stop_and_late_pass_not_promoted(self):
        device = Device(check(result="running"), check()); result, _ = self.run_case(device)
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["cleanup_case_result"], "pass")
        self.assertTrue(result["cleanup_confirmed"])
        sends = [c for c in device.commands if c[:2] == ("shell", "am")]
        self.assertEqual(len(sends), 2)
        self.assertTrue(all(c[-2:] == ("nonce", NONCE) for c in sends))
        self.assertTrue(all(c[5:] == ("--es", "case", c[7], "--es", "nonce", NONCE) for c in sends))

    def test_timeout_without_cleanup_retains_unconfirmed(self):
        device = Device(check(result="running"), check(result="cleanup_unconfirmed")); result, _ = self.run_case(device)
        self.assertEqual(result["status"], "cleanup_unconfirmed")
        self.assertEqual(result["operation_status"], "timeout")
        self.assertFalse(result["cleanup_confirmed"])

    def test_sdk_timeout_uses_sdk_stop_and_requires_sdk_restoration(self):
        closing = check("sdk", "cancelled"); closing["owner_lease_released"] = False
        device = Device(check("sdk", "running"), closing); result, _ = self.run_case(device, "sdk")
        self.assertEqual(result["status"], "cleanup_unconfirmed")
        stop = [c for c in device.commands if c[:2] == ("shell", "am")][-1]
        self.assertEqual(stop[2], "start")
        self.assertEqual(stop[7], "linux-shm-sdk-stop")

    def test_regressed_sequence_cannot_complete(self):
        class Regressed(Device):
            def adb(self, *args, **kwargs):
                if args[0] == "exec-out" and self.launched:
                    return json.dumps(snapshot(check(result="cancelled"), 1))
                raw = super().adb(*args, **kwargs)
                if args[0] == "exec-out":
                    value = json.loads(raw); value["sequence"] = "2"; return json.dumps(value)
                return raw
        result, _ = self.run_case(Regressed(check()))
        self.assertEqual(result["failure_code"], "SNAPSHOT_SEQUENCE_REGRESSED")
        self.assertEqual(result["status"], "cleanup_unconfirmed")

    def test_uncertain_am_delivery_still_sends_stop(self):
        device = Device(check(result="running"), check(result="cancelled"), dispatch_error=True)
        result, _ = self.run_case(device)
        self.assertTrue(device.stopped and result["cleanup_confirmed"])
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("untrusted adb output", json.dumps(result))

    def test_recovery_and_sdk_use_correct_fixed_components(self):
        for case in ("recover", "sdk"):
            device = Device(check(case, "not_required" if case == "recover" else "pass"))
            result, _ = self.run_case(device, case)
            self.assertEqual(result["status"], "not_required" if case == "recover" else "pass")
            sent = next(c for c in device.commands if c[:2] == ("shell", "am"))
            self.assertEqual(sent[2], "start" if case == "sdk" else "broadcast")
            self.assertEqual(sent[4], cli.PACKAGE + ("/.DebugCommandActivity" if case == "sdk" else "/.LinuxSshProbeReceiver"))


if __name__ == "__main__": unittest.main()
