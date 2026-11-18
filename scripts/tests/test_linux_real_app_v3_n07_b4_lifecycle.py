"""Independent B4 v2 file/barrier joins with synthetic, explicit provenance.

These tests run the revision dispatcher, file oracle and barrier validator
unpatched. The existing fixture supplies synthetic product event identities;
real source-bound sessions, readbacks and retirement remain separate CI gates.
Mutations rehash the producer envelopes to reach the semantic checks.
"""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_real_app_v3_evidence as evidence
import linux_real_app_v3_n07_b4_probe as probe
import linux_real_app_v3_n07_settings_lifecycle as lifecycle
from test_linux_real_app_v3_n07_b4_probe import barrier_fixture
from test_linux_real_app_v3_n07_settings_lifecycle import packet, GEOMETRIES


def rebind_barrier(host, supervisor, natives, phase):
    """Rehash all outer wrappers, without changing the claimed observation."""
    left = next(row for row in host["barriers"] if row["request"]["phase"] == phase)
    right = next(row for row in supervisor["barriers"] if row["request"]["phase"] == phase)
    request_digest = evidence.sha(evidence.canonical(right["request"]))
    observation_digest = evidence.sha(evidence.canonical(right["observation"]))
    right["request_sha256"], right["observation_sha256"] = request_digest, observation_digest
    right["response"].update(request_sha256=request_digest, observation_sha256=observation_digest)
    left.update(request=copy.deepcopy(right["request"]), request_sha256=request_digest,
                response=copy.deepcopy(right["response"]))
    label = right["request"]["session"]
    for row in natives[label]["observations"]:
        if row["kind"] == "n07_b4_barrier" and row["data"]["request"]["phase"] == phase:
            row["data"] = copy.deepcopy(left)


def numbered(rows):
    for index, row in enumerate(rows, 1):
        row["sequence"] = str(index)


def joined_fixture():
    # This patch only asks the legacy fixture builder for one extra protocol
    # phase. No production validator or observation result is patched.
    with patch.object(probe, "PHASES", lifecycle.PHASES):
        host, supervisor, sessions, natives, environments = barrier_fixture()
    proofs, observations = packet()
    host.update(schema=1, revision=probe.NATIVE_REVISION_V2)
    supervisor.update(schema=1, revision=probe.SUPERVISOR_REVISION_V2,
        settings_revision=lifecycle.REVISION, settings_observations=observations,
        keyfile={"before_first_launch": proofs["first_before_launch"],
                 "after_first_toggle": proofs["first_after_toggle"],
                 "second_session": proofs["second_after_observation"]},
        supplemental_keyfile={phase: proofs[phase] for phase in (
            "first_retired", "second_before_launch", "second_before_open", "second_retired")})
    for phase in lifecycle.PHASES:
        right = next(row for row in supervisor["barriers"] if row["request"]["phase"] == phase)
        right["observation"].update(keyfile=copy.deepcopy(proofs[phase]),
            settings_observation_sha256=evidence.sha(evidence.canonical(observations[phase])))
        rebind_barrier(host, supervisor, natives, phase)
    rows = natives["second"]["observations"]
    at = next(index for index, row in enumerate(rows) if row["kind"] == "n07_b4_barrier"
              and row["data"]["request"]["phase"] == "second_before_open")
    rows.insert(at, {"kind": "n07_roi_readback_observed", "data": {"phase": "before_open"}})
    rows[at + 2:at + 2] = [
        {"kind": "n05_dialog_baseline", "data": {}},
        {"kind": "n05_step_delivered", "data": {"step": "open_control_o"}},
    ]
    numbered(rows)
    return host, supervisor, sessions, natives, environments


def geometry_natives():
    first = {"n07_probe": {"before_menu": {"readback": {"receipt": {
        "logical_size": copy.deepcopy(GEOMETRIES["first"]["main"])}}}}}
    second = {"n07_second": {
        "before_open": {"readback": {"receipt": {"logical_size":
            copy.deepcopy(GEOMETRIES["second"]["main"])}}},
        "dialogs": [{"readback": {"logical_size": copy.deepcopy(GEOMETRIES["second"]["chooser"])}}]}}
    return first, second


class B4LifecycleRevisionTests(unittest.TestCase):
    def test_explicit_revision_pair_has_no_implicit_upgrade(self):
        host, supervisor, *_ = joined_fixture()
        self.assertEqual(probe.collection_revision(host, supervisor), probe.COLLECTION_REVISION_V2)
        legacy_host = {"schema": 1, "revision": probe.NATIVE_REVISION}
        legacy_supervisor = {"schema": 1, "revision": probe.SUPERVISOR_REVISION}
        self.assertEqual(probe.collection_revision(legacy_host, legacy_supervisor), probe.COLLECTION_REVISION)
        for h, s in ((host, legacy_supervisor), (legacy_host, supervisor)):
            with self.subTest(pair=(h["revision"], s["revision"])), self.assertRaisesRegex(
                    ValueError, "N07_B4_COLLECTION_REVISION"):
                probe.collection_revision(h, s)
        for field in ("settings_revision", "settings_observations"):
            changed = copy.deepcopy(legacy_supervisor)
            changed[field] = supervisor[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                probe.collection_revision(legacy_host, changed)

    def test_missing_or_malformed_revision_and_schema_are_refused(self):
        mutations = (
            lambda h, s: h.pop("revision"),
            lambda h, s: s.pop("revision"),
            lambda h, s: h.update(schema=True),
            lambda h, s: s.update(schema="1"),
            lambda h, s: h.update(revision="eidolon-i6-n07-b4-host-v999"),
            lambda h, s: s.pop("settings_revision"),
            lambda h, s: s.update(settings_revision="eidolon-i6-n07-settings-v1"),
            lambda h, s: s.update(settings_observations=[]),
        )
        for index, mutate in enumerate(mutations):
            host, supervisor, *_ = joined_fixture()
            mutate(host, supervisor)
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, "N07_B4_COLLECTION_REVISION"):
                probe.collection_revision(host, supervisor)

    def test_three_canonical_and_four_supplemental_snapshots_join_all_barriers(self):
        fixture = joined_fixture()
        host, supervisor, *_ = fixture
        phases = probe._barriers(*fixture)
        self.assertEqual(tuple(phases), lifecycle.PHASES)
        first, second = geometry_natives()
        result = probe.validate_settings(supervisor, first=first, second=second)
        self.assertTrue(result["complete"])
        self.assertTrue(result["geometry_bounds_verified"])
        self.assertFalse(result["host_v3_acceptance_passed"])
        self.assertEqual(result["verified_n07_assertions"], [])
        self.assertEqual(len(supervisor["keyfile"]), 3)
        self.assertEqual(len(supervisor["supplemental_keyfile"]), 4)
        self.assertTrue(phases["first_retired"]["observation"]["product_cleanup_verified"])

    def test_missing_observation_or_relabelled_supplemental_proof_refused(self):
        for phase in lifecycle.PHASES:
            _, supervisor, *_ = joined_fixture()
            supervisor["settings_observations"].pop(phase)
            with self.subTest(missing=phase), self.assertRaises(ValueError):
                probe.validate_settings(supervisor)
        _, supervisor, *_ = joined_fixture()
        supervisor["supplemental_keyfile"]["second_before_open"] = copy.deepcopy(
            supervisor["keyfile"]["second_session"])
        with self.assertRaisesRegex(ValueError, "N07_SETTINGS_SNAPSHOT_BINDING"):
            probe.validate_settings(supervisor)

    def test_geometry_is_taken_from_bound_native_readbacks(self):
        _, supervisor, *_ = joined_fixture()
        for label in ("main", "chooser"):
            first, second = geometry_natives()
            logical = (second["n07_second"]["before_open"]["readback"]["receipt"]["logical_size"]
                       if label == "main" else second["n07_second"]["dialogs"][0]["readback"]["logical_size"])
            logical["width"] = 1
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, "N07_SETTINGS_GEOMETRY"):
                probe.validate_settings(supervisor, first=first, second=second)


class B4LifecycleBarrierTests(unittest.TestCase):
    def test_missing_seventh_barrier_and_wrong_phase_order_refused(self):
        for mutate in (
            lambda h, s: (h["barriers"].pop(4), s["barriers"].pop(4)),
            lambda h, s: (h["barriers"].reverse(), s["barriers"].reverse()),
        ):
            fixture = joined_fixture()
            mutate(*fixture[:2])
            with self.assertRaises(ValueError):
                probe._barriers(*fixture)

    def test_rehashed_foreign_settings_snapshot_or_keyfile_moment_refused(self):
        for field, value, reason in (
            ("settings_observation_sha256", "f" * 64, "N07_SETTINGS_SNAPSHOT_BINDING"),
            ("keyfile", "foreign_phase", "N07_B4_SNAPSHOT_MOMENT"),
        ):
            fixture = joined_fixture()
            host, supervisor, _, natives, _ = fixture
            row = supervisor["barriers"][4]
            row["observation"][field] = (copy.deepcopy(supervisor["keyfile"]["second_session"])
                                          if value == "foreign_phase" else value)
            rebind_barrier(host, supervisor, natives, "second_before_open")
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, reason):
                probe._barriers(*fixture)

    def test_preopen_observation_after_keyboard_delivery_is_not_preopen(self):
        fixture = joined_fixture()
        rows = fixture[3]["second"]["observations"]
        barrier = next(row for row in rows if row["kind"] == "n07_b4_barrier"
                       and row["data"]["request"]["phase"] == "second_before_open")
        rows.remove(barrier)
        at = next(index for index, row in enumerate(rows) if row["kind"] == "n05_step_delivered")
        rows.insert(at + 1, barrier)
        numbered(rows)
        with self.assertRaisesRegex(ValueError, "N07_B4_PREOPEN_TOO_LATE"):
            probe._barriers(*fixture)

    def test_preopen_observation_before_actual_main_readback_refused(self):
        fixture = joined_fixture()
        rows = fixture[3]["second"]["observations"]
        readback = next(row for row in rows if row["kind"] == "n07_roi_readback_observed"
                        and row["data"]["phase"] == "before_open")
        rows.remove(readback)
        at = next(index for index, row in enumerate(rows) if row["kind"] == "n07_b4_barrier"
                  and row["data"]["request"]["phase"] == "second_before_open")
        rows.insert(at + 1, readback)
        numbered(rows)
        with self.assertRaisesRegex(ValueError, "N07_B4_PREOPEN_TOO_LATE"):
            probe._barriers(*fixture)

    def test_rehashed_false_retirement_or_late_prelaunch_refused(self):
        for phase in ("first_retired", "first_before_launch"):
            fixture = joined_fixture()
            host, supervisor, _, natives, _ = fixture
            row = next(row for row in supervisor["barriers"] if row["request"]["phase"] == phase)
            if phase == "first_retired":
                row["observation"]["product_cleanup_verified"] = False
            else:
                row["request"]["transport_watermark"] = 2
            rebind_barrier(host, supervisor, natives, phase)
            with self.subTest(phase=phase), self.assertRaisesRegex(
                    ValueError, "N07_B4_(RETIREMENT_GATE|PRELAUNCH_TOO_LATE)"):
                probe._barriers(*fixture)

    def test_second_runtime_cannot_start_at_or_before_first_retirement_ack(self):
        for offset in (-1, 0):
            fixture = joined_fixture()
            host, _, _, natives, _ = fixture
            row = next(row for row in natives["second"]["observations"]
                       if row["kind"] == "n07_b4_runtime_created")
            row["data"]["started_at_ns"] = host["barriers"][2]["response_monotonic_ns"] + offset
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, "N07_B4_SECOND_BEFORE_CLEANUP"):
                probe._barriers(*fixture)


if __name__ == "__main__":
    unittest.main()
