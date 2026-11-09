"""Adversarial GUI receipt tests; these specimens do not execute Mousepad."""
import copy
import hashlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import linux_real_app_gui_evidence as gui
from linux_real_app_build import BuildError


def gui_specimen(source):
    """A fixed, nonexecuting specimen shared with the aggregator gate tests."""
    identity = lambda number: format(number, "032x")
    digest = lambda text: hashlib.sha256(text.encode()).hexdigest()
    template = source["lifecycle"]["cases"]["close_restart"]
    first = copy.deepcopy(template["first"])
    first.update(run_id=identity(600), owner_scope_id=identity(1600), runtime_id=identity(2600),
                 profile_id=identity(900))
    first["profile_after"].update(settings_sha256=digest("line-numbers=true"),
                                  preferences_sha256=digest("line-numbers=true;geometry"),
                                  preferences_count=5, tree_sha256=digest("changed GUI profile"))
    restart = copy.deepcopy(first)
    restart.update(run_id=identity(601), owner_scope_id=identity(1601), runtime_id=identity(2601),
                   profile_before=copy.deepcopy(first["profile_after"]))
    isolation = copy.deepcopy(template["first"])
    isolation.update(run_id=identity(602), owner_scope_id=identity(1602), runtime_id=identity(2602),
                     profile_id=identity(901))

    def base(run=first, *, sequence=10, frame=10, keys=20, pointer=0):
        return {"run_id": run["run_id"], "main": "1", "prefs_window": "2", "parent": "1",
                "frame": str(frame), "observer_sequence": sequence, "input_delivered": keys,
                "pointer_buttons": pointer}

    def view(run=first, *, enabled=False, saved=False, **kwargs):
        return {**base(run, **kwargs), "line_numbers": enabled, "gui_checked": enabled,
                "saved_present": saved, "saved_value": enabled if saved else None}

    def seat(before, after):
        return {"window": before["prefs_window"], "key_before": before["input_delivered"],
                "key_after": after["input_delivered"], "pointer_before": before["pointer_buttons"],
                "pointer_after": after["pointer_buttons"], "keys_pressed": 0, "pointer_pressed": 0,
                "target_observed": True}

    initial = view()
    changed = view(enabled=True, saved=True, sequence=20, frame=11, pointer=2)

    def trial(sequence, frame, pointer, expected):
        def toggle(offset, active):
            return {**base(sequence=sequence + offset, frame=frame + offset, pointer=pointer + offset * 2),
                    "active": active, "sensitive": True, "mapped": True}
        before, changed, restored = toggle(0, False), toggle(1, True), toggle(2, False)
        negative_before = {**base(sequence=sequence + 3, frame=frame + 3, pointer=pointer + 4),
                           "present": True, "mapped": True, "sensitive": False,
                           "expected_value": True, "value_sha256": digest(expected)}
        negative_after = {**negative_before, "observer_sequence": sequence + 4,
                          "frame": str(frame + 4), "pointer_buttons": pointer + 6}
        return {"positive_before": before, "positive_changed": changed, "positive_restored": restored,
                "positive_input": seat(before, changed), "restore_input": seat(changed, restored),
                "before": negative_before, "after": negative_after, "input": seat(negative_before, negative_after)}

    before_tab = {"window": "1", "frame": "60", "app_id": "mousepad", "client_count": 1,
                  "observed_mapped_count": 2, "input_delivered": 30, "pressed": 0}
    after_tab = {**before_tab, "frame": "70", "observed_mapped_count": 3, "input_delivered": 60}
    input_change = seat(initial, changed)
    return {
        "schema": 1,
        "observer": {"kind": "gtk3-readonly-v1", "module_sha256": "d" * 64, "source_sha256": "e" * 64},
        "persistence": {"first": first, "restart": restart, "isolation": isolation,
                        "initial": initial, "changed": changed,
                        "restarted": view(restart, enabled=True, saved=True), "isolated": view(isolation),
                        "change_input": input_change},
        "protected": {"run_id": first["run_id"], "main": "1", "prefs_window": "2", "parent": "1",
                      "positive_before": copy.deepcopy(initial), "positive_after": copy.deepcopy(changed),
                      "positive_input": copy.deepcopy(input_change),
                      "widgets": {"opening-mode": trial(30, 20, 2, "tab"),
                                  "session-restore": trial(40, 30, 8, "never")},
                      "open_tab": {"run_id": first["run_id"], "main": "1", "before_tabs": 2, "after_tabs": 3,
                                   "before": before_tab, "after": after_tab, "observer_before": 50,
                                   "observer_after": 51, "open_dialog": "3", "parent": "1"}},
    }


class GuiEvidenceTests(unittest.TestCase):
    def setUp(self):
        # Import locally so the aggregator can reuse gui_specimen without a
        # module import cycle or inheritance that reruns its existing suite.
        from test_linux_real_app_check import FoundationAggregationTests
        self.source = FoundationAggregationTests().application()
        self.value = gui_specimen(self.source)

    def rejects(self, mutate):
        value = copy.deepcopy(self.value)
        mutate(value)
        with self.assertRaises(BuildError):
            gui.validate_gui(value, self.source)

    def test_complete_related_gui_evidence_is_accepted(self):
        self.assertIs(gui.validate_gui(self.value, self.source), self.value)
        self.assertIs(gui.validate_gui(self.value, self.source, self.source["lifecycle"]), self.value)

    def test_fresh_runs_may_reuse_numeric_window_ids(self):
        points = self.value["persistence"]
        self.assertEqual({points[key]["prefs_window"] for key in ("initial", "changed", "restarted", "isolated")}, {"2"})
        gui.validate_gui(self.value, self.source)

    def test_missing_evidence_or_unknown_payload_is_rejected(self):
        for key in tuple(self.value):
            with self.subTest(missing=key):
                self.rejects(lambda value: value.pop(key))
        for path in ((), ("persistence",), ("protected",), ("observer",), ("persistence", "changed")):
            def inject(value):
                for key in path:
                    value = value[key]
                value["title"] = "private payload"
            with self.subTest(path=path):
                self.rejects(inject)

    def test_prewritten_keyfile_cannot_replace_new_profile_gui_default(self):
        for phase in ("first", "isolation"):
            def prewrite(value):
                value["persistence"][phase]["profile_before"] = copy.deepcopy(value["persistence"][phase]["profile_after"])
            with self.subTest(phase=phase):
                self.rejects(prewrite)
        for field, replacement in (("saved_present", True), ("saved_value", False), ("line_numbers", True), ("gui_checked", True)):
            with self.subTest(field=field):
                self.rejects(lambda value: value["persistence"]["initial"].update({field: replacement}))

    def test_api_or_geometry_evidence_cannot_replace_actual_changed_widget(self):
        for phase in ("changed", "restarted"):
            for field, replacement in (("line_numbers", False), ("gui_checked", False),
                                       ("saved_present", False), ("saved_value", False), ("saved_value", 1)):
                with self.subTest(phase=phase, field=field):
                    self.rejects(lambda value: value["persistence"][phase].update({field: replacement}))

    def test_configuration_leaking_to_another_profile_is_rejected(self):
        self.rejects(lambda value: value["persistence"]["isolation"].update(profile_id=value["persistence"]["first"]["profile_id"]))
        for field, replacement in (("line_numbers", True), ("gui_checked", True), ("saved_present", True), ("saved_value", True)):
            with self.subTest(field=field):
                self.rejects(lambda value: value["persistence"]["isolated"].update({field: replacement}))

    def test_restart_requires_same_profile_fresh_ownership_and_preserved_bytes(self):
        self.rejects(lambda value: value["persistence"]["restart"].update(profile_id="f" * 32))
        for field in ("run_id", "owner_scope_id", "runtime_id"):
            with self.subTest(field=field):
                self.rejects(lambda value: value["persistence"]["restart"].update({field: value["persistence"]["first"][field]}))
        self.rejects(lambda value: value["persistence"]["restart"]["profile_before"].update(tree_sha256="f" * 64))
        self.rejects(lambda value: value["persistence"]["restart"]["profile_after"].update(documents_sha256="f" * 64))

    def test_prior_lifecycle_receipt_cannot_be_reused_as_gui_run(self):
        for field in ("run_id", "owner_scope_id", "runtime_id", "profile_id"):
            old = self.source["lifecycle"]["cases"]["close_restart"]["first"][field]
            with self.subTest(field=field):
                self.rejects(lambda value: value["persistence"]["first"].update({field: old}))

    def test_every_gui_run_needs_normal_close_and_actual_cleanup(self):
        for phase in ("first", "restart", "isolation"):
            with self.subTest(phase=phase):
                self.rejects(lambda value: value["persistence"][phase]["cleanup"].update(output_pipes_eof=False))
                self.rejects(lambda value: value["persistence"][phase]["termination"].update(server_returncode=1))
                self.rejects(lambda value: value["persistence"][phase].update(termination_kind="stop"))

    def test_input_omission_wrong_target_unreleased_button_or_counter_forgery_is_rejected(self):
        for field, replacement in (("window", "99"), ("target_observed", False), ("keys_pressed", 1),
                                   ("pointer_pressed", 1), ("pointer_after", 0), ("pointer_after", True),
                                   ("key_before", 0), ("key_after", 19), ("pointer_before", 1)):
            with self.subTest(field=field):
                self.rejects(lambda value: value["persistence"]["change_input"].update({field: replacement}))

    def test_changed_state_without_delivered_pointer_input_is_rejected(self):
        def no_delivery(value):
            persistence = value["persistence"]
            persistence["changed"]["pointer_buttons"] = persistence["initial"]["pointer_buttons"]
            persistence["change_input"]["pointer_after"] = persistence["change_input"]["pointer_before"]
        self.rejects(no_delivery)

    def test_gui_observation_replay_and_frame_or_input_rollback_are_rejected(self):
        for field, replacement in (("observer_sequence", 10), ("observer_sequence", True),
                                   ("frame", "9"), ("input_delivered", 19), ("pointer_buttons", 0)):
            with self.subTest(field=field):
                self.rejects(lambda value: value["persistence"]["changed"].update({field: replacement}))

    def test_preferences_must_have_actual_original_main_parent(self):
        for field, replacement in (("parent", None), ("parent", "99"), ("prefs_window", "1"), ("main", "99")):
            with self.subTest(field=field):
                self.rejects(lambda value: value["persistence"]["changed"].update({field: replacement}))

    def test_exactly_two_protected_preferences_controls_exist(self):
        self.rejects(lambda value: value["protected"]["widgets"].update({"autosave-timer": {}}))
        self.rejects(lambda value: value["protected"]["widgets"].pop("opening-mode"))
        self.rejects(lambda value: value["protected"]["widgets"].pop("session-restore"))

    def test_each_protected_control_must_be_present_mapped_insensitive_and_expected(self):
        for name in ("opening-mode", "session-restore"):
            for phase in ("before", "after"):
                for field, replacement in (("present", False), ("mapped", False), ("sensitive", True),
                                           ("sensitive", 0), ("expected_value", False), ("value_sha256", None)):
                    with self.subTest(name=name, phase=phase, field=field):
                        self.rejects(lambda value: value["protected"]["widgets"][name][phase].update({field: replacement}))

    def test_protected_control_needs_same_page_positive_change_and_restore(self):
        for name in ("opening-mode", "session-restore"):
            for phase, field, replacement in (("positive_changed", "active", False),
                                               ("positive_restored", "active", True),
                                               ("positive_before", "sensitive", False),
                                               ("positive_changed", "mapped", False),
                                               ("positive_input", "target_observed", False),
                                               ("restore_input", "pointer_pressed", 1)):
                with self.subTest(name=name, phase=phase, field=field):
                    self.rejects(lambda value: value["protected"]["widgets"][name][phase].update({field: replacement}))

    def test_unchanged_value_alone_without_targeted_negative_input_is_rejected(self):
        for name in ("opening-mode", "session-restore"):
            self.rejects(lambda value: value["protected"]["widgets"][name]["input"].update(target_observed=False))
            self.rejects(lambda value: value["protected"]["widgets"][name]["after"].update(value_sha256="0" * 64))
            self.rejects(lambda value: value["protected"]["widgets"][name].update(
                after=copy.deepcopy(value["protected"]["widgets"][name]["before"])))

    def test_expected_true_and_matching_arbitrary_digest_cannot_certify_protected_value(self):
        for name in ("opening-mode", "session-restore"):
            def wrong_value(value):
                for phase in ("before", "after"):
                    value["protected"]["widgets"][name][phase].update(expected_value=True, value_sha256="f" * 64)
            with self.subTest(name=name):
                self.rejects(wrong_value)

    def test_swapped_protected_value_digests_are_rejected_even_when_both_stay_unchanged(self):
        def swap(value):
            for name, other in (("opening-mode", "session-restore"), ("session-restore", "opening-mode")):
                for phase in ("before", "after"):
                    value["protected"]["widgets"][name][phase]["value_sha256"] = gui.PROTECTED_VALUE_DIGESTS[other]
        self.rejects(swap)

    def test_protected_hashes_are_bound_to_the_exact_active_id_bytes(self):
        for name, wrong in (("opening-mode", b"'tab'"), ("session-restore", b"never\n")):
            def decorated(value):
                for phase in ("before", "after"):
                    value["protected"]["widgets"][name][phase]["value_sha256"] = hashlib.sha256(wrong).hexdigest()
            with self.subTest(name=name):
                self.rejects(decorated)

    def test_positive_witness_cannot_be_a_different_experiment(self):
        for field in ("positive_before", "positive_after", "positive_input"):
            with self.subTest(field=field):
                self.rejects(lambda value: value["protected"].update({field: {}}))

    def test_tab_open_needs_actual_new_tab_dialog_response_and_same_main(self):
        for field, replacement in (("run_id", "f" * 32), ("main", "99"), ("after_tabs", 2),
                                   ("after_tabs", True), ("observer_after", 50), ("observer_before", 44),
                                   ("open_dialog", "2"), ("parent", None)):
            with self.subTest(field=field):
                self.rejects(lambda value: value["protected"]["open_tab"].update({field: replacement}))
        for field, replacement in (("window", "99"), ("client_count", 2), ("frame", "60"),
                                   ("observed_mapped_count", 2), ("input_delivered", 30), ("pressed", 1)):
            with self.subTest(observation_field=field):
                self.rejects(lambda value: value["protected"]["open_tab"]["after"].update({field: replacement}))

    def test_same_snapshot_projection_may_share_restored_and_negative_before_sequence(self):
        for trial in self.value["protected"]["widgets"].values():
            restored = trial["positive_restored"]
            trial["before"].update({key: restored[key] for key in gui.BASE_FIELDS})
        gui.validate_gui(self.value, self.source)

    def test_observer_kind_and_hashes_are_bounded_fixed_metadata(self):
        for field, replacement in (("kind", "api-probe"), ("module_sha256", "unknown"), ("source_sha256", True)):
            with self.subTest(field=field):
                self.rejects(lambda value: value["observer"].update({field: replacement}))


if __name__ == "__main__":
    unittest.main()
