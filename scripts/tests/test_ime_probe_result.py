import copy
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from ime_probe_checks import ACCEPTANCE, EXERCISE_STEPS, SDK_TAP_STEPS, TOUCH_KEYS, MAX_PROBE_BYTES, MAX_SNAPSHOT_BYTES, action_outcome, parse_receipt

loader = importlib.machinery.SourceFileLoader("quest_ime_probe", str(Path(__file__).parents[1] / "quest-ime-probe"))
spec = importlib.util.spec_from_loader(loader.name, loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)
NONCE = "1" * 32


def fixture(result="running"):
    check = dict(case="ime-editor-probe", nonce=NONCE, result=result, scope="android_editor_fixture",
                 linux_delivery_connected=False, acceptance=copy.deepcopy(ACCEPTANCE),
                 exercise=dict(result="not_run"), sdk_tap=dict(result="not_run"),
                 synthetic=dict(result="pass", input_source="direct_input_connection_fixture", actual_ime=False,
                                physical_keyboard=False, stock_fixed_text_match=True, custom_commit_once=True,
                                preedit_observed_and_cleared=True, stale_connection_rejected=True),
                 stage="awaiting_manual", elapsed_ms=1200, physical_key_events_observed=0,
                 editors=[dict(editor=name, display=2, attached=True, window_focus=False) for name in "ABC"],
                 panels=[dict(panel=i, window_flags=0, window_focus=False) for i in (0, 1)], counts={}, last_lengths_utf16={})
    if result == "completed":
        check.update(reason="operator_stop", cleanup_failed=False, panels_destroy_requested=True, views_detached_observed=False)
    return {"schema": 1, "runtime": {"private_unrelated_data": "NEVER_SAVE_THIS"}, "check": check}


def exercise_fixture(result="completed"):
    value = fixture()
    check = value["check"]
    for editor in check["editors"]:
        editor.update(window_token=True, view_focus=False, imm_active_view=False,
                      imm_accepting_text=False, ime_insets_visible=False)
    observation = dict(elapsed_ms=750, editors=copy.deepcopy(check["editors"]), panels=copy.deepcopy(check["panels"]),
                       activity=dict(display=0, window_focus=False, imm_accepting_text=False, window_flags=0),
                       framework_connection_callbacks=dict.fromkeys("ABC", 0))
    steps = [dict(copy.deepcopy(observation), step=index + 1, editor=editor, operation=operation, operation_returned=None)
             for index, (editor, operation) in enumerate(EXERCISE_STEPS)]
    check["exercise"] = dict(result=result, sequence="focus-show-hide-restart-clear-v1",
                             input_source="framework_focus_requests", text_injected=False, steps=steps,
                             cleanup_requested=True, cleanup_failed=False, cleanup_observation=copy.deepcopy(observation),
                             reason="sequence_finished" if result == "completed" else "exercise_timeout")
    return value


def encode(value):
    return json.dumps(value, ensure_ascii=False)


def sdk_tap_fixture(result="completed", connected=False):
    value = exercise_fixture()
    check = value["check"]
    observation = copy.deepcopy(check["exercise"]["cleanup_observation"])
    observation["panel_touch_callbacks"] = dict.fromkeys(TOUCH_KEYS, 0)
    check["exercise"] = dict(result="not_run")
    steps = []
    for index, (editor, operation) in enumerate(SDK_TAP_STEPS):
        before = copy.deepcopy(observation)
        if operation == "tap" and connected:
            for state in observation["editors"]:
                state["view_focus"] = state["window_focus"] = state["imm_active_view"] = state["editor"] == editor
            observation["framework_connection_callbacks"][editor] += 1
            for action in ("down", "up"):
                observation["panel_touch_callbacks"][f"panel_{1 if editor == 'C' else 0}_touch_{action}"] += 1
        step = dict(step=index + 1, editor=editor, operation=operation,
                    before=before, after=copy.deepcopy(observation),
                    route="sdk_panel_dispatch_event" if operation == "tap" else "framework",
                    down_submitted=operation == "tap", up_submitted=operation == "tap", show_returned=None)
        if operation == "tap":
            step.update(outcome="dispatch_submitted", geometry=dict(panel=1 if editor == "C" else 0, display=2,
                display_width=800, display_height=600, left=20, top=100, width=500, height=100, center_x=270, center_y=150))
        elif operation == "prepare": step["outcome"] = "cleanup_requested"
        elif connected: step.update(outcome="show_requested", show_returned=True)
        else: step["outcome"] = "show_skipped_no_focus"
        steps.append(step)
    check["sdk_tap"] = dict(result=result, sequence="prepare-sdk-tap-focused-show-v1",
        input_source="sdk_panel_dispatch_event", text_injected=False, steps=steps,
        cleanup_requested=True, cleanup_failed=False, cleanup_observation=copy.deepcopy(observation),
        reason="sequence_finished" if result == "completed" else "sdk_tap_timeout")
    return value


class Clock:
    def __init__(self):
        self.now = 0

    def time(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


class Device:
    def __init__(self, receipts):
        self.receipts = list(receipts)
        self.calls = []

    def validate(self):
        self.calls.append(("validate",))
        return dict(model="Quest 3", api=34)

    def adb(self, *command, **kwargs):
        self.calls.append(command)
        if command[0] == "exec-out":
            value = self.receipts.pop(0) if len(self.receipts) > 1 else self.receipts[0]
            return value
        return "AM_OUTPUT_MUST_NOT_BE_SAVED"


class ImeProbeResultTests(unittest.TestCase):
    def test_sdk_tap_completion_is_observation_even_with_no_delivered_touch_or_connection(self):
        for connected in (False, True):
            with self.subTest(connected=connected):
                check = parse_receipt(encode(sdk_tap_fixture(connected=connected)), NONCE)
                self.assertEqual(action_outcome("sdk-tap", check), "sdk_tap_sequence_observed")
                self.assertEqual(check["acceptance"], ACCEPTANCE)
                self.assertEqual(check["sdk_tap"]["cleanup_observation"]["framework_connection_callbacks"]["A"], int(connected))
        self.assertNotEqual(action_outcome("status", check), action_outcome("sdk-tap", check))

    def test_sdk_tap_rejects_false_pass_incomplete_steps_or_missing_cleanup(self):
        variants = []
        for key, item in (("result", "pass"), ("text_injected", True), ("cleanup_requested", False),
                          ("cleanup_failed", True), ("reason", "sdk_tap_timeout"), ("input_source", "physical_keyboard")):
            value = sdk_tap_fixture(); value["check"]["sdk_tap"][key] = item; variants.append(value)
        value = sdk_tap_fixture(); value["check"]["sdk_tap"]["steps"].pop(); variants.append(value)
        value = sdk_tap_fixture(); value["check"]["sdk_tap"].pop("cleanup_observation"); variants.append(value)
        value = sdk_tap_fixture(); value["check"]["sdk_tap"]["steps"][0]["editor"] = "C"; variants.append(value)
        for value in variants:
            with self.subTest(proof=value["check"]["sdk_tap"]), self.assertRaises(ValueError):
                parse_receipt(encode(value), NONCE)

    def test_sdk_tap_requires_both_dispatch_requests_matching_display_and_bounded_center(self):
        variants = []
        for key, item in (("down_submitted", False), ("up_submitted", False), ("route", "framework"), ("show_returned", True)):
            value = sdk_tap_fixture(); value["check"]["sdk_tap"]["steps"][1][key] = item; variants.append(value)
        for key, item in (("center_x", 0), ("display_width", 200), ("display", 0), ("panel", 1), ("height", 0)):
            value = sdk_tap_fixture(); value["check"]["sdk_tap"]["steps"][1]["geometry"][key] = item; variants.append(value)
        value = sdk_tap_fixture(); value["check"]["sdk_tap"]["steps"][1].pop("geometry"); variants.append(value)
        for value in variants:
            with self.assertRaises(ValueError): parse_receipt(encode(value), NONCE)

    def test_sdk_tap_only_requests_show_after_observed_window_and_view_focus(self):
        for connected in (False, True):
            value = sdk_tap_fixture(connected=connected)
            step = value["check"]["sdk_tap"]["steps"][2]
            step["outcome"] = "show_skipped_no_focus" if connected else "show_requested"
            step["show_returned"] = None if connected else True
            with self.assertRaises(ValueError): parse_receipt(encode(value), NONCE)

    def test_sdk_tap_nested_text_unknown_counters_and_regressing_observations_are_rejected(self):
        variants = []
        for path in (("text",), ("steps", 1, "geometry", "text"), ("steps", 0, "before", "editors", 0, "text"),
                     ("steps", 0, "before", "panel_touch_callbacks", "arbitrary_input")):
            value = sdk_tap_fixture(); item = value["check"]["sdk_tap"]
            for part in path[:-1]: item = item[part]
            item[path[-1]] = "PRIVATE_INPUT_MUST_NOT_APPEAR"
            variants.append(value)
        for field, key in (("framework_connection_callbacks", "A"), ("panel_touch_callbacks", "panel_0_touch_up")):
            value = sdk_tap_fixture(); value["check"]["sdk_tap"]["steps"][0]["before"][field][key] = 1; variants.append(value)
        value = sdk_tap_fixture(); value["check"]["sdk_tap"]["steps"][0]["after"]["elapsed_ms"] = 1; variants.append(value)
        for value in variants:
            with self.assertRaises(ValueError) as error: parse_receipt(encode(value), NONCE)
            self.assertNotIn("PRIVATE_INPUT", str(error.exception))

    def test_sdk_tap_failed_or_stopped_partial_steps_are_recorded_without_success(self):
        for reason, outcome in (("sdk_tap_step_failed", "bounds_unavailable"), ("sdk_tap_step_failed", "operation_exception")):
            value = sdk_tap_fixture("fail"); proof = value["check"]["sdk_tap"]
            proof["steps"] = proof["steps"][:2]; proof["reason"] = reason
            step = proof["steps"][-1]; step["outcome"] = outcome
            step["down_submitted"] = step["up_submitted"] = False; step.pop("geometry")
            if outcome == "operation_exception": step["failure_class"] = "IllegalStateException"
            check = parse_receipt(encode(value), NONCE)
            with self.assertRaisesRegex(ValueError, "SDK_TAP_NOT_COMPLETED"): action_outcome("sdk-tap", check)
            self.assertEqual(action_outcome("status", check), "metadata_observed")
        value = sdk_tap_fixture("blocked"); proof = value["check"]["sdk_tap"]
        proof["steps"] = []; proof["reason"] = "probe_stopped"
        with self.assertRaisesRegex(ValueError, "SDK_TAP_NOT_COMPLETED"):
            action_outcome("sdk-tap", parse_receipt(encode(value), NONCE))

    def test_sdk_tap_running_receipt_times_out_and_cannot_overlap_exercise(self):
        value = sdk_tap_fixture("running"); value["check"]["sdk_tap"].pop("reason")
        device = Device([encode(value)]); clock = Clock()
        with self.assertRaises(TimeoutError):
            cli.observe(device, NONCE, "sdk-tap", .5, clock=clock.time, sleep=clock.sleep)
        value["check"]["exercise"] = exercise_fixture("running")["check"]["exercise"]
        with self.assertRaisesRegex(ValueError, "CONCURRENT_PROBE_SEQUENCE"): parse_receipt(encode(value), NONCE)

    def test_cli_sdk_tap_is_fixed_case_same_nonce_and_metadata_only(self):
        device = Device([encode(fixture()), encode(sdk_tap_fixture())])
        old_umask = os.umask(0o077)
        try:
            with tempfile.TemporaryDirectory() as directory:
                argv = ["quest-ime-probe", "sdk-tap", "--serial", "fixture", "--nonce", NONCE, "--artifact-root", directory]
                with patch.object(sys, "argv", argv), patch.object(cli, "QuestDevice", return_value=device), patch("sys.stdout", new_callable=io.StringIO):
                    self.assertEqual(cli.main(), 0)
                launch = next(call for call in device.calls if call[0] == "shell")
                self.assertEqual(launch[-6:], ("--es", "case", "ime-editor-probe-sdk-tap", "--es", "nonce", NONCE))
                report = json.loads(next(Path(directory).rglob("result.json")).read_text())
                self.assertEqual(report["action_status"], "sdk_tap_sequence_observed")
                self.assertEqual(report["acceptance"], "not_verified")
                saved = "".join(p.read_text() for p in Path(directory).rglob("*") if p.is_file())
                self.assertNotIn("NEVER_SAVE_THIS", saved)
                self.assertNotIn("AM_OUTPUT_MUST_NOT_BE_SAVED", saved)
        finally: os.umask(old_umask)

    def test_cli_sdk_tap_rejects_wrong_nonce_replay_or_concurrent_exercise_before_launch(self):
        variants = [sdk_tap_fixture(), exercise_fixture("running")]
        value = fixture(); value["check"]["nonce"] = "2" * 32; variants.append(value)
        for value in variants:
            device = Device([encode(value)])
            old_umask = os.umask(0o077)
            try:
                with tempfile.TemporaryDirectory() as directory:
                    argv = ["quest-ime-probe", "sdk-tap", "--serial", "fixture", "--nonce", NONCE, "--artifact-root", directory]
                    with patch.object(sys, "argv", argv), patch.object(cli, "QuestDevice", return_value=device), patch("sys.stdout", new_callable=io.StringIO):
                        self.assertEqual(cli.main(), 1)
                    self.assertFalse(any(call[0] == "shell" for call in device.calls))
            finally: os.umask(old_umask)

    def test_matching_metadata_excludes_unrelated_snapshot_and_keeps_acceptance_unverified(self):
        check = parse_receipt(encode(fixture()), NONCE)
        self.assertNotIn("NEVER_SAVE_THIS", encode(check))
        self.assertEqual(check["acceptance"], ACCEPTANCE)
        self.assertEqual(action_outcome("start", check), "panel_fixture_ready")
        self.assertEqual(action_outcome("status", check), "metadata_observed")

    def test_known_key_limit_failure_is_metadata_without_expanding_unknown_values(self):
        value = fixture(); value["check"]["editors"][2]["last_failure"] = "key_limit"
        self.assertEqual(parse_receipt(encode(value), NONCE)["editors"][2]["last_failure"], "key_limit")
        value["check"]["editors"][2]["last_failure"] = "PRIVATE_TEXT"
        with self.assertRaises(ValueError): parse_receipt(encode(value), NONCE)

    def test_wrong_nonce_and_case_are_ignored(self):
        self.assertIsNone(parse_receipt(encode(fixture()), "2" * 32))
        value = fixture(); value["check"]["case"] = "keyboard"
        self.assertIsNone(parse_receipt(encode(value), NONCE))

    def test_size_limits_apply_to_snapshot_and_probe_separately(self):
        with self.assertRaisesRegex(ValueError, "RECEIPT_LIMIT"):
            parse_receipt(" " * (MAX_SNAPSHOT_BYTES + 1), NONCE)
        value = fixture(); value["check"]["padding"] = "x" * MAX_PROBE_BYTES
        with self.assertRaisesRegex(ValueError, "PROBE_LIMIT"):
            parse_receipt(encode(value), NONCE)

    def test_fake_pass_delivery_and_acceptance_are_rejected(self):
        variants = []
        value = fixture(); value["check"]["result"] = "pass"; variants.append(value)
        value = fixture(); value["check"]["linux_delivery_connected"] = True; variants.append(value)
        for key in ACCEPTANCE:
            value = fixture(); value["check"]["acceptance"][key] = "pass"; variants.append(value)
        for key in ("actual_ime", "physical_keyboard"):
            value = fixture(); value["check"]["synthetic"][key] = True; variants.append(value)
        value = fixture(); value["check"]["synthetic"].pop("stale_connection_rejected"); variants.append(value)
        for value in variants:
            with self.subTest(value=value["check"]), self.assertRaises(ValueError):
                parse_receipt(encode(value), NONCE)

    def test_only_known_metadata_fields_and_callback_names_may_be_saved(self):
        variants = []
        for path in (("text",), ("editors", 0, "text"), ("synthetic", "commit"), ("counts", "private_input")):
            value = fixture(); item = value["check"]
            for part in path[:-1]: item = item[part]
            item[path[-1]] = "PRIVATE_TEXT_DO_NOT_INCLUDE_IN_ERROR"
            variants.append(value)
        for value in variants:
            with self.assertRaises(ValueError) as error:
                parse_receipt(encode(value), NONCE)
            self.assertNotIn("PRIVATE_TEXT", str(error.exception))

    def test_duplicate_json_keys_nan_bad_types_and_duplicate_editors_are_rejected(self):
        raw = encode(fixture()).replace('"linux_delivery_connected": false', '"linux_delivery_connected": true, "linux_delivery_connected": false')
        with self.assertRaisesRegex(ValueError, "DUPLICATE_JSON_KEY"):
            parse_receipt(raw, NONCE)
        for value in (float("nan"), True, -2, "12"):
            data = fixture(); data["check"]["elapsed_ms"] = value
            with self.assertRaises(ValueError): parse_receipt(encode(data), NONCE)
        data = fixture(); data["check"]["editors"][1]["editor"] = "A"
        with self.assertRaises(ValueError): parse_receipt(encode(data), NONCE)

    def test_initial_receipt_is_valid_but_does_not_claim_ready(self):
        data = fixture(); check = data["check"]
        check["synthetic"] = dict(result="not_run")
        for key in ("stage", "editors", "panels"): check.pop(key)
        parsed = parse_receipt(encode(data), NONCE)
        self.assertIsNone(action_outcome("start", parsed))
        self.assertEqual(action_outcome("status", parsed), "metadata_observed")

    def test_still_running_stop_times_out_without_a_success(self):
        device = Device([encode(fixture())]); clock = Clock(); observations = []
        with self.assertRaises(TimeoutError):
            cli.observe(device, NONCE, "stop", .5, clock=clock.time, sleep=clock.sleep, record=observations.append)
        self.assertGreater(len(observations), 0)
        self.assertTrue(all(item["result"] == "running" for item in observations))

    def test_stop_acknowledgment_does_not_claim_async_detach_or_input_acceptance(self):
        device = Device([encode(fixture()), encode(fixture("completed"))]); clock = Clock()
        check, outcome = cli.observe(device, NONCE, "stop", 1, clock=clock.time, sleep=clock.sleep)
        self.assertEqual(outcome, "stop_acknowledged")
        self.assertFalse(check["views_detached_observed"])
        self.assertEqual(check["acceptance"], ACCEPTANCE)

    def test_incomplete_or_failed_cleanup_and_blocked_stops_are_rejected(self):
        for key, value in (("cleanup_failed", True), ("panels_destroy_requested", False)):
            data = fixture("completed"); data["check"][key] = value
            with self.assertRaisesRegex(ValueError, "INCOMPLETE_STOP_PROOF"):
                parse_receipt(encode(data), NONCE)
        for status in ("blocked", "fail"):
            check = parse_receipt(encode(fixture(status)), NONCE)
            with self.assertRaisesRegex(ValueError, "STOP_NOT_ACKNOWLEDGED"):
                action_outcome("stop", check)
            self.assertEqual(action_outcome("status", check), "metadata_observed")

    def test_fixture_failure_is_recorded_without_start_success(self):
        data = fixture(); data["check"]["synthetic"]["result"] = "fail"
        device = Device([encode(data)]); clock = Clock(); observations = []
        with self.assertRaisesRegex(ValueError, "SYNTHETIC_FIXTURE_FAILED"):
            cli.observe(device, NONCE, "start", 1, clock=clock.time, sleep=clock.sleep, record=observations.append)
        self.assertEqual(observations[0]["synthetic"]["result"], "fail")

    def test_cli_writes_metadata_only_and_uses_fixed_adb_arguments(self):
        previous = fixture(); previous["check"]["nonce"] = "2" * 32
        device = Device([encode(previous), encode(fixture())])
        old_umask = os.umask(0o077)
        try:
            with tempfile.TemporaryDirectory() as directory:
                argv = ["quest-ime-probe", "start", "--serial", "fixture", "--nonce", NONCE, "--artifact-root", directory]
                with patch.object(sys, "argv", argv), patch.object(cli, "QuestDevice", return_value=device), patch("sys.stdout", new_callable=io.StringIO):
                    self.assertEqual(cli.main(), 0)
                files = list(Path(directory).rglob("*"))
                saved = "".join(path.read_text() for path in files if path.is_file())
                self.assertNotIn("NEVER_SAVE_THIS", saved)
                self.assertNotIn("AM_OUTPUT_MUST_NOT_BE_SAVED", saved)
                report = json.loads(next(Path(directory).rglob("result.json")).read_text())
                self.assertEqual(report["action_status"], "panel_fixture_ready")
                self.assertTrue(report["observation_only"])
                self.assertEqual(report["acceptance"], "not_verified")
                self.assertEqual(device.calls[0], ("validate",))
                launch = next(call for call in device.calls if call[0] == "shell")
                self.assertEqual(launch[-6:], ("--es", "case", "ime-editor-probe", "--es", "nonce", NONCE))
                for path in files:
                    self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)
        finally:
            os.umask(old_umask)

    def test_start_rejects_existing_nonce_and_shell_metacharacters_before_launch(self):
        device = Device([encode(fixture())])
        old_umask = os.umask(0o077)
        try:
            with tempfile.TemporaryDirectory() as directory:
                argv = ["quest-ime-probe", "start", "--serial", "fixture", "--nonce", NONCE, "--artifact-root", directory]
                with patch.object(sys, "argv", argv), patch.object(cli, "QuestDevice", return_value=device), patch("sys.stdout", new_callable=io.StringIO):
                    self.assertEqual(cli.main(), 1)
                self.assertFalse(any(call[0] == "shell" for call in device.calls))
                argv[5] = "a;echo PRIVATE"
                with patch.object(sys, "argv", argv), patch.object(cli, "QuestDevice") as constructor, patch("sys.stderr", new_callable=io.StringIO):
                    with self.assertRaises(SystemExit): cli.main()
                    constructor.assert_not_called()
        finally:
            os.umask(old_umask)

    def test_exercise_sequence_can_complete_without_claiming_ime_connection_or_acceptance(self):
        value = parse_receipt(encode(exercise_fixture()), NONCE)
        self.assertEqual(action_outcome("exercise", value), "exercise_sequence_observed")
        self.assertEqual(value["exercise"]["cleanup_observation"]["framework_connection_callbacks"], dict.fromkeys("ABC", 0))
        self.assertEqual(value["acceptance"], ACCEPTANCE)

    def test_exercise_rejects_incomplete_reordered_or_forged_success(self):
        variants = []
        for key, item in (("result", "pass"), ("text_injected", True), ("cleanup_requested", False),
                          ("cleanup_failed", True), ("reason", "exercise_timeout")):
            value = exercise_fixture(); value["check"]["exercise"][key] = item; variants.append(value)
        value = exercise_fixture(); value["check"]["exercise"]["steps"].pop(); variants.append(value)
        value = exercise_fixture(); value["check"]["exercise"]["steps"][0]["editor"] = "C"; variants.append(value)
        value = exercise_fixture(); value["check"]["exercise"].pop("cleanup_observation"); variants.append(value)
        value = exercise_fixture(); value["check"]["exercise"]["steps"][0]["editors"][0].pop("imm_active_view"); variants.append(value)
        value = exercise_fixture(); value["check"]["exercise"]["steps"][0]["framework_connection_callbacks"]["A"] = 1; variants.append(value)
        for value in variants:
            with self.subTest(proof=value["check"]["exercise"]), self.assertRaises(ValueError):
                parse_receipt(encode(value), NONCE)

    def test_exercise_nested_text_and_acceptance_are_not_saved(self):
        for path in (("text",), ("actual_ime",), ("steps", 0, "text"), ("cleanup_observation", "editors", 0, "text")):
            value = exercise_fixture(); item = value["check"]["exercise"]
            for part in path[:-1]: item = item[part]
            item[path[-1]] = "PRIVATE_INPUT"
            with self.assertRaises(ValueError) as error:
                parse_receipt(encode(value), NONCE)
            self.assertNotIn("PRIVATE_INPUT", str(error.exception))

    def test_exercise_running_timeout_or_stop_cannot_succeed(self):
        value = exercise_fixture("running")
        device = Device([encode(value)]); clock = Clock()
        with self.assertRaises(TimeoutError):
            cli.observe(device, NONCE, "exercise", .5, clock=clock.time, sleep=clock.sleep)
        for state in ("blocked", "fail"):
            value = parse_receipt(encode(exercise_fixture(state)), NONCE)
            with self.assertRaisesRegex(ValueError, "EXERCISE_NOT_COMPLETED"):
                action_outcome("exercise", value)
        value = fixture("completed"); value["check"]["exercise"] = exercise_fixture("blocked")["check"]["exercise"]
        with self.assertRaisesRegex(ValueError, "EXERCISE_NOT_COMPLETED"):
            action_outcome("exercise", parse_receipt(encode(value), NONCE))

    def test_cli_exercise_uses_same_nonce_fixed_case_and_refuses_replay(self):
        old_umask = os.umask(0o077)
        try:
            for initial, expected in ((fixture(), 0), (exercise_fixture(), 1)):
                device = Device([encode(initial), encode(exercise_fixture())])
                with tempfile.TemporaryDirectory() as directory:
                    argv = ["quest-ime-probe", "exercise", "--serial", "fixture", "--nonce", NONCE, "--artifact-root", directory]
                    with patch.object(sys, "argv", argv), patch.object(cli, "QuestDevice", return_value=device), patch("sys.stdout", new_callable=io.StringIO):
                        self.assertEqual(cli.main(), expected)
                    launches = [call for call in device.calls if call[0] == "shell"]
                    if expected == 0:
                        self.assertEqual(launches[0][-6:], ("--es", "case", "ime-editor-probe-exercise", "--es", "nonce", NONCE))
                        report = json.loads(next(Path(directory).rglob("result.json")).read_text())
                        self.assertEqual(report["action_status"], "exercise_sequence_observed")
                        self.assertEqual(report["acceptance"], "not_verified")
                    else:
                        self.assertEqual(launches, [])
        finally:
            os.umask(old_umask)


if __name__ == "__main__":
    unittest.main()
