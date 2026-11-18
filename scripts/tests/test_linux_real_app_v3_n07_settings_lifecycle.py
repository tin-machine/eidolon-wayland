"""N07 v2 settings evidence: synthetic files, no GUI or Golden claim."""
from __future__ import annotations

import base64
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_real_app_v3_n07_settings as legacy
import linux_real_app_v3_n07_settings_lifecycle as lifecycle


PHASES = ("first_before_launch", "first_after_toggle", "first_retired",
          "second_before_launch", "second_before_open", "second_after_observation",
          "second_retired")
TARGET = b"[org/xfce/mousepad/preferences/view]\nshow-line-numbers=true\n"
WINDOW = (b"[org/xfce/mousepad/state/window]\nwidth=400\nheight=300\n"
          b"maximized=false\nfullscreen=false\n")
CHOOSER = (b"[org/gtk/settings/file-chooser]\nlocation-mode='filename-entry'\n"
           b"show-hidden=false\nshow-size-column=true\nshow-type-column=true\n"
           b"sort-directories-first=false\nsort-column='name'\nsort-order='ascending'\n"
           b"sidebar-width=164\ndate-format='regular'\ntype-format='category'\n"
           b"window-position=(3, 5)\nwindow-size=(600, 480)\n")
WITH_WINDOW = TARGET + b"\n" + WINDOW
WITH_CHOOSER = WITH_WINDOW + b"\n" + CHOOSER
GEOMETRIES = {
    "first": {"main": {"width": 512, "height": 384}, "chooser": None},
    "second": {"main": {"width": 512, "height": 384},
               "chooser": {"width": 704, "height": 552}},
}


def proof_for(raw, index=0):
    """Test evidence uses explicit synthetic metadata, never old GUI receipts."""
    present, value, others = (False, None, legacy.sha(b"{}")) if raw is None else legacy._parse(raw)
    return {
        "storage_digest": "a" * 64, "root_dev": 7, "root_ino": 11,
        "key_present": present, "value": value, "others_sha256": others,
        "file": None if raw is None else {
            "dev": 7, "inode": 100 + index, "uid": 1000, "nlink": 1,
            "size": len(raw), "mtime_ns": 1000 + index, "ctime_ns": 2000 + index,
            "sha256": legacy.sha(raw),
        },
    }


def raw_sequence():
    return dict(zip(PHASES, (None, TARGET, WITH_WINDOW, WITH_WINDOW,
                             WITH_WINDOW, WITH_CHOOSER, WITH_CHOOSER)))


def packet(raws=None):
    raws = raw_sequence() if raws is None else raws
    proofs, observed = {}, {}
    for index, (phase, raw) in enumerate(raws.items()):
        proof = proofs[phase] = proof_for(raw, index)
        observed[phase] = lifecycle.observe_snapshot(proof, raw)
    return proofs, observed


def rebind(proof, observation):
    """Adversarial producer can rehash metadata; semantic checks must still fail."""
    observation["proof_sha256"] = legacy.sha(lifecycle.canonical(proof))


class LifecycleSettings(unittest.TestCase):
    def test_valid_seven_phase_fixture_has_bounded_geometry_and_no_acceptance(self):
        self.assertEqual(lifecycle.PHASES, PHASES)
        proofs, observations = packet()
        result = lifecycle.validate_sequence(proofs, observations, geometries=GEOMETRIES)
        self.assertEqual(result["revision"], "eidolon-i6-n07-settings-lifecycle-v2")
        self.assertTrue(result["complete"])
        self.assertTrue(result["geometry_bounds_verified"])
        self.assertEqual(set(result["snapshots"]), set(PHASES))
        self.assertFalse(result["host_v3_acceptance_passed"])
        self.assertEqual(result["verified_n07_assertions"], [])
        self.assertTrue(all(row["bytes_unchanged"] for row in result["transitions"]
                            if row["phase"] in ("second_before_launch", "second_before_open",
                                                "second_retired")))

    def test_valid_v2_does_not_reinterpret_old_exact_bytes_contract(self):
        proofs, observations = packet()
        lifecycle.validate_sequence(proofs, observations)
        with self.assertRaisesRegex(ValueError, "N07_UNEXPECTED_SETTING_CHANGE"):
            legacy.validate_file_sequence(proofs["first_before_launch"],
                proofs["first_after_toggle"], proofs["second_after_observation"])

    def test_contiguous_prefixes_remain_explicitly_partial(self):
        proofs, observations = packet()
        for length in range(1, len(PHASES) + 1):
            with self.subTest(length=length):
                phases = PHASES[:length]
                result = lifecycle.validate_sequence({p: proofs[p] for p in phases},
                    {p: observations[p] for p in phases}, complete=False)
                self.assertFalse(result["complete"])
                self.assertFalse(result["geometry_bounds_verified"])
                self.assertFalse(result["host_v3_acceptance_passed"])
        with self.assertRaises(ValueError):
            lifecycle.validate_sequence({}, {}, complete=False)

    def test_missing_extra_and_noncontiguous_phases_fail(self):
        for phase in PHASES:
            proofs, observations = packet()
            proofs.pop(phase)
            observations.pop(phase)
            with self.subTest(missing=phase), self.assertRaisesRegex(ValueError, "PHASE_MISSING"):
                lifecycle.validate_sequence(proofs, observations)
        proofs, observations = packet()
        proofs["first_retired_later"] = proofs.pop("first_retired")
        observations["first_retired_later"] = observations.pop("first_retired")
        with self.assertRaisesRegex(ValueError, "PHASE_MISSING"):
            lifecycle.validate_sequence(proofs, observations, complete=False)
        proofs, observations = packet()
        observations.pop("second_before_open")
        with self.assertRaisesRegex(ValueError, "PHASE_MISSING"):
            lifecycle.validate_sequence(proofs, observations)

    def test_old_six_phase_collection_cannot_be_relabelled(self):
        proofs, observations = packet()
        proofs.pop("second_before_open")
        observations.pop("second_before_open")
        with self.assertRaisesRegex(ValueError, "PHASE_MISSING"):
            lifecycle.validate_sequence(proofs, observations)

    def test_initial_true_false_and_other_existing_keyfile_rejected(self):
        for initial in (TARGET, TARGET.replace(b"true", b"false"), WINDOW):
            raws = raw_sequence()
            raws["first_before_launch"] = initial
            with self.subTest(initial=initial), self.assertRaises(ValueError):
                lifecycle.validate_sequence(*packet(raws))

    def test_target_false_after_toggle_or_restart_rejected(self):
        for phase in PHASES[1:]:
            raws = raw_sequence()
            raws[phase] = raws[phase].replace(b"show-line-numbers=true", b"show-line-numbers=false")
            with self.subTest(phase=phase), self.assertRaisesRegex(ValueError, "KEY_NOT_PERSISTED"):
                lifecycle.validate_sequence(*packet(raws))

    def test_configure_timer_may_add_window_keys_before_close(self):
        lines = WINDOW.splitlines(keepends=True)
        for count in range(1, 5):
            raws = raw_sequence()
            raws["first_after_toggle"] = TARGET + b"\n" + b"".join(lines[:count + 1])
            with self.subTest(keys=count):
                lifecycle.validate_sequence(*packet(raws), geometries=GEOMETRIES)

    def test_all_window_keys_required_at_retirement(self):
        for line in WINDOW.splitlines(keepends=True)[1:]:
            raws = raw_sequence()
            raws["first_retired"] = WITH_WINDOW.replace(line, b"")
            with self.subTest(missing=line), self.assertRaisesRegex(ValueError, "WINDOW_INCOMPLETE"):
                lifecycle.validate_sequence(*packet(raws))

    def test_existing_window_values_cannot_change_during_close_or_open(self):
        for phase in PHASES[2:]:
            raws = raw_sequence()
            raws["first_after_toggle"] = WITH_WINDOW
            raws[phase] = raws[phase].replace(b"height=300", b"height=301")
            with self.subTest(phase=phase), self.assertRaisesRegex(ValueError, "UNEXPECTED_SETTING_CHANGE"):
                lifecycle.validate_sequence(*packet(raws))

    def test_chooser_is_forbidden_until_after_actual_open(self):
        for phase in PHASES[1:5]:
            raws = raw_sequence()
            raws[phase] = WITH_CHOOSER
            with self.subTest(phase=phase), self.assertRaisesRegex(ValueError, "CHOOSER_BEFORE_OPEN"):
                lifecycle.validate_sequence(*packet(raws))

    def test_exact_chooser_key_set_required(self):
        for line in CHOOSER.splitlines(keepends=True)[1:]:
            raws = raw_sequence()
            raws["second_after_observation"] = WITH_CHOOSER.replace(line, b"")
            with self.subTest(missing=line), self.assertRaisesRegex(ValueError, "CHOOSER_INCOMPLETE"):
                lifecycle.validate_sequence(*packet(raws))

    def test_late_stop_cannot_change_chooser_geometry(self):
        raws = raw_sequence()
        raws["second_retired"] = WITH_CHOOSER.replace(b"sidebar-width=164", b"sidebar-width=165")
        with self.assertRaisesRegex(ValueError, "UNEXPECTED_SETTING_CHANGE"):
            lifecycle.validate_sequence(*packet(raws))

    def test_reordering_old_keys_is_rejected_even_with_authorized_additions(self):
        for phase in ("first_retired", "second_after_observation", "second_retired"):
            raws = raw_sequence()
            raws["first_after_toggle"] = WITH_WINDOW
            raws[phase] = raws[phase].replace(b"width=400\nheight=300", b"height=300\nwidth=400")
            with self.subTest(phase=phase), self.assertRaisesRegex(ValueError, "SERIALIZATION_CHANGED"):
                lifecycle.validate_sequence(*packet(raws))

    def test_foreign_storage_root_or_owner_is_rejected_even_if_rehashed(self):
        for field, value in (("storage_digest", "b" * 64), ("root_dev", 9), ("root_ino", 12),
                             ("uid", 1001)):
            proofs, observations = packet()
            proof = proofs["second_before_launch"]
            (proof["file"] if field == "uid" else proof)[field] = value
            rebind(proof, observations["second_before_launch"])
            with self.subTest(field=field), self.assertRaises(ValueError):
                lifecycle.validate_sequence(proofs, observations)

    def test_inode_ctime_atomic_flush_changes_do_not_change_settings(self):
        proofs, observations = packet()
        for index, phase in enumerate(PHASES[1:]):
            proofs[phase]["file"].update(inode=900 + index, ctime_ns=5000 + index)
            rebind(proofs[phase], observations[phase])
        lifecycle.validate_sequence(proofs, observations)

    def test_missing_wrong_revision_or_foreign_observation_is_rejected(self):
        for field, value in (("revision", "eidolon-i6-n07-keyfile-diagnostic-v1"),
                             ("source", "producer_status"), ("schema", True),
                             ("proof_sha256", "f" * 64)):
            proofs, observations = packet()
            observations["first_after_toggle"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                lifecycle.validate_sequence(proofs, observations)
        proofs, observations = packet()
        observations["second_before_open"] = observations["second_after_observation"]
        with self.assertRaisesRegex(ValueError, "SNAPSHOT_BINDING"):
            lifecycle.validate_sequence(proofs, observations)
        proofs, observations = packet()
        observations["first_after_toggle"]["approved"] = True
        with self.assertRaisesRegex(ValueError, "SNAPSHOT_SHAPE"):
            lifecycle.validate_sequence(proofs, observations)

    def test_saved_validator_recomputes_file_content_not_producer_fields(self):
        for field, value in (("others_sha256", "b" * 64), ("value", False),
                             ("key_present", False), ("sha256", "c" * 64), ("size", 61)):
            proofs, observations = packet()
            proof = proofs["first_after_toggle"]
            (proof["file"] if field in ("sha256", "size") else proof)[field] = value
            if field == "key_present":
                proof["value"] = None
            rebind(proof, observations["first_after_toggle"])
            with self.subTest(field=field), self.assertRaises(ValueError):
                lifecycle.validate_sequence(proofs, observations)

    def test_base64_and_metadata_types_fail_closed(self):
        for encoded in (True, "!!!!", base64.b64encode(TARGET).decode() + "\n", "A" * 6000):
            proofs, observations = packet()
            observations["first_after_toggle"]["bytes_b64"] = encoded
            with self.subTest(encoded_type=type(encoded)), self.assertRaises(ValueError):
                lifecycle.validate_sequence(proofs, observations)
        for field in ("root_dev", "root_ino", "key_present", "value"):
            proofs, observations = packet()
            proofs["first_after_toggle"][field] = 1 if field in ("key_present", "value") else True
            rebind(proofs["first_after_toggle"], observations["first_after_toggle"])
            with self.subTest(field=field), self.assertRaises(ValueError):
                lifecycle.validate_sequence(proofs, observations)
        for value in (True, "1000", -1):
            proofs, observations = packet()
            proofs["first_after_toggle"]["file"]["uid"] = value
            rebind(proofs["first_after_toggle"], observations["first_after_toggle"])
            with self.subTest(uid=value), self.assertRaises(ValueError):
                lifecycle.validate_sequence(proofs, observations)

    def test_real_geometry_bounds_reject_oversize_and_non_integer_evidence(self):
        for label, which, field, value in (("first", "main", "width", 399),
                                          ("first", "main", "height", 299),
                                          ("second", "chooser", "width", 599),
                                          ("second", "chooser", "height", 479),
                                          ("second", "main", "width", True),
                                          ("second", "main", "height", "384")):
            geometries = copy.deepcopy(GEOMETRIES)
            geometries[label][which][field] = value
            raws = raw_sequence()
            raws["first_after_toggle"] = WITH_WINDOW
            with self.subTest(label=label, which=which, field=field), self.assertRaises(ValueError):
                lifecycle.validate_sequence(*packet(raws), geometries=geometries)
        raws = raw_sequence()
        raws["second_after_observation"] = WITH_CHOOSER.replace(b"sidebar-width=164", b"sidebar-width=601")
        with self.assertRaisesRegex(ValueError, "GEOMETRY"):
            lifecycle.validate_sequence(*packet(raws), geometries=GEOMETRIES)

    def test_first_retirement_geometry_uses_first_session_evidence(self):
        geometries = copy.deepcopy(GEOMETRIES)
        geometries["first"]["main"]["height"] = 299
        with self.assertRaisesRegex(ValueError, "GEOMETRY"):
            lifecycle.validate_sequence(*packet(), geometries=geometries)

    def test_malformed_geometry_rejected_without_keyerror(self):
        for geometries in ({}, {"first": {}}, {"first": None}, {"first": True},
                           {"first": GEOMETRIES["first"], "second": {"main": None}}):
            raws = raw_sequence()
            raws["first_after_toggle"] = WITH_WINDOW
            with self.subTest(geometries=geometries), self.assertRaises(ValueError):
                lifecycle.validate_sequence(*packet(raws), geometries=geometries)


class SafeProjection(unittest.TestCase):
    def test_exact_known_key_scope_rejects_other_preferences_and_subsections(self):
        extra = (
            b"[org/xfce/mousepad/preferences/window]\nremember-size=false\n",
            b"[org/xfce/mousepad/state/window/extra]\nwidth=400\n",
            b"[org/gtk/settings/file-chooser/extra]\nshow-hidden=false\n",
            b"[private]\nsecret=fixture-secret-not-for-export\n",
        )
        raw_values = [TARGET + b"\n" + item for item in extra] + [
            TARGET.replace(b"show-line-numbers=true", b"show-line-numbers=true\nfont-name=private-font"),
            WITH_WINDOW.replace(b"width=400", b"width=400\nleft=3"),
            WITH_CHOOSER.replace(b"show-hidden=false", b"show-hidden=false\nstartup-mode='cwd'"),
            WITH_CHOOSER.replace(b"date-format='regular'", b"date-format='regular'\nlast-folder-uri='file:///private'"),
        ]
        for raw in raw_values:
            with self.subTest(raw_sha=legacy.sha(raw)), self.assertRaises(ValueError) as error:
                lifecycle.observe_snapshot(proof_for(raw), raw)
            self.assertNotIn("fixture-secret-not-for-export", str(error.exception))
            self.assertNotIn("private-font", str(error.exception))
            self.assertNotIn("file:///private", str(error.exception))

    def test_target_boolean_lexical_type_is_strict(self):
        for value in (b"True", b"FALSE", b"1", b"'true'", b" true", b"true "):
            raw = TARGET.replace(b"=true", b"=" + value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                lifecycle.parse_safe(raw)

    def test_window_integer_and_boolean_schemas_are_strict(self):
        for value in (b"0", b"-1", b"+1", b"0400", b"1.0", b"true", b"'400'",
                      b"4294967296", b"uint32 400"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                lifecycle.parse_safe(WITH_WINDOW.replace(b"width=400", b"width=" + value))
        lifecycle.parse_safe(WITH_WINDOW.replace(b"width=400", b"width=4294967295"))
        for key in (b"maximized", b"fullscreen"):
            for value in (b"true", b"False", b"0", b"'false'"):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    lifecycle.parse_safe(WITH_WINDOW.replace(key + b"=false", key + b"=" + value))

    def test_chooser_defaults_and_location_input_policy_reject_other_valid_enums(self):
        for old, new in ((b"'filename-entry'", b"'path-bar'"), (b"'regular'", b"'with-time'"),
                         (b"'name'", b"'modified'"), (b"'ascending'", b"'descending'"),
                         (b"'category'", b"'mime'"), (b"show-hidden=false", b"show-hidden=true"),
                         (b"show-size-column=true", b"show-size-column=false"),
                         (b"sort-directories-first=false", b"sort-directories-first=true")):
            with self.subTest(new=new), self.assertRaisesRegex(ValueError, "CHOOSER_POLICY"):
                lifecycle.parse_safe(WITH_CHOOSER.replace(old, new))

    def test_chooser_dynamic_state_has_canonical_int32_syntax(self):
        for value in (b"0", b"-1", b"+164", b"0164", b"2147483648", b"164.0"):
            with self.subTest(sidebar=value), self.assertRaises(ValueError):
                lifecycle.parse_safe(WITH_CHOOSER.replace(b"sidebar-width=164", b"sidebar-width=" + value))
        for value in (b"(600,480)", b"(600, 0480)", b"(+600, 480)", b"(600, -0)",
                      b"(0, 480)", b"(600, -1)", b"(2147483648, 480)", b"[600, 480]",
                      b"(600, 480, 2)", b"(600.0, 480)"):
            with self.subTest(size=value), self.assertRaises(ValueError):
                lifecycle.parse_safe(WITH_CHOOSER.replace(b"window-size=(600, 480)", b"window-size=" + value))
        for value in (b"(-2147483649, 5)", b"(3, 2147483648)", b"(-0, 5)"):
            with self.subTest(position=value), self.assertRaises(ValueError):
                lifecycle.parse_safe(WITH_CHOOSER.replace(b"window-position=(3, 5)", b"window-position=" + value))
        lifecycle.parse_safe(WITH_CHOOSER.replace(b"window-position=(3, 5)",
                                                 b"window-position=(-2147483648, 2147483647)"))

    def test_comments_empty_sections_duplicate_keys_and_odd_whitespace_rejected(self):
        raws = (
            b"# comment\n" + TARGET, TARGET + b"; comment\n", TARGET + b"\n",
            TARGET[:-1], TARGET.replace(b"\n", b"\r\n"), b"\n" + TARGET,
            TARGET.replace(b"=true", b" =true"), TARGET.replace(b"=true", b"= true"),
            TARGET.replace(b"true\n", b"true\nshow-line-numbers=true\n"),
            TARGET + b"\n" + TARGET, TARGET + b"\n[org/xfce/mousepad/state/window]\n",
            TARGET.replace(b"true", b"true\n continuation"), TARGET + b"\x00",
            TARGET + "あ".encode("utf-8"), b"x" * (lifecycle.MAX_BYTES + 1),
        )
        for raw in raws:
            with self.subTest(raw_sha=legacy.sha(raw)), self.assertRaises(ValueError):
                lifecycle.parse_safe(raw)

    def test_projection_never_substitutes_later_bytes_or_wrong_file_digest(self):
        proof = proof_for(TARGET)
        with self.assertRaisesRegex(ValueError, "SNAPSHOT_BINDING"):
            lifecycle.observe_snapshot(proof, WITH_WINDOW)
        with self.assertRaisesRegex(ValueError, "SNAPSHOT_BINDING"):
            lifecycle.observe_snapshot(proof_for(None), TARGET)


class LiveReadOnlyProbe(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.directory = self.root.joinpath(*legacy.COMPONENTS)
        self.path = self.directory / legacy.LEAF

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, raw, atomic=False):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = self.path.with_name("test-atomic") if atomic else self.path
        destination.write_bytes(raw)
        destination.chmod(0o600)
        if atomic:
            destination.replace(self.path)

    def probe(self, **kwargs):
        return legacy.StorageKeyfileProbe(self.root, os.getuid(), "a" * 64, **kwargs)

    def test_same_root_fd_seven_observations_and_atomic_close_flush(self):
        proofs, observations = {}, {}
        with self.probe(lifecycle=True) as probe:
            for index, (phase, raw) in enumerate(raw_sequence().items()):
                if raw is not None:
                    self.write(raw, atomic=phase in ("first_retired", "second_retired"))
                proofs[phase] = probe.snapshot()
                observations[phase] = copy.deepcopy(probe.last_lifecycle)
                self.assertEqual(set(proofs[phase]), legacy.PROOF_KEYS)
            result = lifecycle.validate_sequence(proofs, observations, geometries=GEOMETRIES)
            self.assertTrue(result["complete"])
            self.assertNotEqual(proofs["first_after_toggle"]["file"]["inode"],
                                proofs["first_retired"]["file"]["inode"])
            retained_fds = [fd for fd, _ in probe.directory_fds.values()]
        for fd in retained_fds:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_projection_is_opt_in_and_cleared_after_unsafe_or_invalid_read(self):
        self.write(TARGET)
        with self.probe() as probe:
            probe.snapshot()
            self.assertIsNone(probe.last_lifecycle)
        with self.probe(lifecycle=True) as probe:
            probe.snapshot()
            self.assertIsNotNone(probe.last_lifecycle)
            self.write(TARGET + b"\n[private]\nsecret=fixture-private-do-not-export\n")
            with self.assertRaisesRegex(ValueError, "UNKNOWN_SECTION") as error:
                probe.snapshot()
            self.assertIsNone(probe.last_lifecycle)
            self.assertNotIn("fixture-private-do-not-export", str(error.exception))
            self.write(TARGET.replace(b"true\n", b"true\nshow-line-numbers=true\n"))
            with self.assertRaises(ValueError):
                probe.snapshot()
            self.assertIsNone(probe.last_lifecycle)

    def test_projection_uses_same_fd_read_not_a_second_path_read(self):
        self.write(TARGET)
        read = legacy._read_keyfile

        def read_then_replace(directory, uid):
            result = read(directory, uid)
            self.write(WITH_WINDOW, atomic=True)
            return result

        with self.probe(lifecycle=True) as probe:
            with patch.object(legacy, "_read_keyfile", side_effect=read_then_replace) as measured:
                proof = probe.snapshot()
            self.assertEqual(measured.call_count, 1)
            self.assertEqual(proof["file"]["sha256"], legacy.sha(TARGET))
            self.assertEqual(base64.b64decode(probe.last_lifecycle["bytes_b64"]), TARGET)
            lifecycle.validate_snapshot(proof, probe.last_lifecycle)


if __name__ == "__main__":
    unittest.main()
