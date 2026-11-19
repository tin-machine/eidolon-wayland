"""B4 negative contracts; synthetic test pixels never count as live evidence."""
from __future__ import annotations

import copy
from pathlib import Path, PurePosixPath
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_desktop_v3_contract as contract
import linux_real_app_v3_evidence as evidence
import linux_real_app_v3_n07_b4_probe as probe
import linux_real_app_v3_n07_fingerprint as fingerprint
import linux_real_app_v3_n07_pixel as pixel
import linux_real_app_v3_n07_review as b3
from test_linux_real_app_v3_n07_b2 import pointer, readback
from test_linux_real_app_v3_n07_b3 import inputs as b3_inputs, complete_ticket, add_context
from test_linux_real_app_v3_n07_fingerprint import fixture as fingerprint_fixture


def second_fixture():
    data = b3_inputs()
    add_context(data)
    first = data["native"]
    native = copy.deepcopy(first)
    native.pop("n07_probe")
    native.pop("review_context")
    native.update(schema=probe.SECOND_SCHEMA, variant=probe.SECOND_VARIANT,
                  runtime="2", owner_id="e" * 32)
    target = {"session": "2", "window": "17", "scene": "1", "generation": "1"}
    native["readback"].update(target=target, runtime="2", create="2")
    stages = {}
    for name, frame, roi in (("before_open", 2, probe.NUMBERS),
                             ("before_open_confirm", 3, probe.NUMBERS),
                             (pixel.STATES[4], 6, probe.NUMBERS),
                             (pixel.STATES[5], 9, probe.CHECK)):
        uid, x, y, w, h = roi
        stage = readback(first, name, frame, uid, (x, y, w, h), change=8,
                         popup_surface="25")
        rb, receipt = stage["readback"], stage["readback"]["receipt"]
        rb.update(target=target, create_seq="2", sdk_adapter="2")
        receipt.update(target=target, runtime="2", create="2", renderer_owner=2, context_epoch=2)
        if name != pixel.STATES[5]:
            receipt["layers"] = receipt["layers"][:1]
        complete_ticket(rb)
        stages[name] = stage
    proof = {"schema": 1, "source": "normal_sdk_renderer_main_content_sequence",
             "parent_surface": "1", "baseline_content_frame": "3", "baseline_main_content_seq": "3",
             "quiet_ms": 200, "budget_ms": 12000, "status": "settled",
             "content_frame": "6", "main_content_seq": "6", "samples": [
                 {"content_frame": "6", "main_content_seq": "6", "request_seq": 59, "elapsed_ms": 10},
                 {"content_frame": "6", "main_content_seq": "6", "request_seq": 60, "elapsed_ms": 210}]}
    native["n07_second"] = {
        "schema": 1, "run_id": native["run_id"], "capture_id": "e" * 64,
        "main_target": target, "menu_coordinate_reviewed": False, "menu_identity_verified": False,
        **stages, "post_open_settling": proof,
        "pointer": pointer(target, 0, 81, "1", [160, 63]),
        "popup": {"role": "xdg_popup", "root_window": "17", "captured_origin": True,
                  "dismissed": False, "surface": "25", "parent_surface": "1",
                  "ack_serial": 96, "grab_serial": 81, "layer": 1},
        "steps": [], "dialogs": [], "input": "synthetic-not-an-open-proof", "document": "synthetic-not-an-open-proof",
    }
    extra = {}
    for index, name in enumerate(probe.SECOND_CONTEXT_STATES):
        original = evidence.parse(data["context_raw"], limit=b3.MAX_INPUT_BYTES)["states"][b3.CONTEXT_STATES[2 + index]]
        stage = copy.deepcopy(original)
        stage["phase"] = name
        rb = stage["readback"]
        anchor = stages[pixel.STATES[4 + index]]["readback"]["receipt"]
        rb.update(target=target, create_seq="2", sdk_adapter="2")
        rb["receipt"].update(copy.deepcopy(anchor))
        rb["receipt"]["pixel_sha256"] = [p["sha256"] for p in rb["pixels"]]
        complete_ticket(rb)
        rb["receipt"]["request_seq"] += 1
        rb["receipt"]["present_seq"] += 1
        extra[name] = stage
    context = {"schema": 1, "revision": "eidolon-i6-n07-b4-second-review-context-v1",
               "source": "host_renderer_review_context", **{k: native[k] for k in (
                   "run_id", "runtime", "owner_id", "source_sha256")},
               "capture_id": native["n07_second"]["capture_id"], "target": target,
               "complete": True, "states": extra, "error": None, "cleanup_error": None}
    raw = evidence.canonical(context)
    native["review_context"] = {"schema": 1, "sha256": evidence.sha(raw), "bytes": len(raw),
                                "states": list(probe.SECOND_CONTEXT_STATES), "complete": True}
    return native, raw, data


def storage_fixture():
    record = contract.StorageRecord.parse(contract.canonical_json({
        "schema": 1, "legacy_installation": "1" * 32, "installation_id": "2" * 32,
        "profile_id": "remote-linux", "profile_binding": "3" * 64,
        "profile_storage_id": "4" * 32, "host_user_binding": "5" * 64}))
    context = {"schema": 1, "backend": "linux_desktop", "protocol_revision": contract.REVISION,
        "session": "1", "profile_revision": "1", "profile_binding": "3" * 64,
        "nonce": "6" * 16, "uid": 12002, "installation_id": "2" * 32, "profile_storage_id": "4" * 32}
    owner = contract.StorageOwner.parse(contract.canonical_json({"schema": 1, "uid": 12002,
        "installation_id": "2" * 32, "profile_storage_id": "4" * 32,
        "app_id": contract.EDITOR, "state_policy": "persistent-keyfile-locked-v1"}))
    path = "/home/eidolon-app/.local/share/eidolon/apps/" + "2" * 32 + "/" + "4" * 32 + "/" + contract.EDITOR
    names = list(PurePosixPath(path).parts)
    names[-1] = path
    directories = [{"relative": name, "dev": 1, "ino": i + 1,
                    "uid": 0 if i < 2 else 12002, "mode": 0o755 if i < 2 else 0o700}
                   for i, name in enumerate(names)]
    control = {name: {"dev": 1, "ino": 100 + i, "uid": 12002, "mode": 0o600,
                     "nlink": 1, "size": len(owner.canonical()) if name == "owner.json" else 0,
                     "mtime_ns": 123456, "ctime_ns": 123456}
               for i, name in enumerate(("owner.json", "profile.lock"))}
    storage = {"record": record.record(), "storage_digest": record.sha256(), "uid": 12002,
        "passwd_home": "/home/eidolon-app", "root_path": path,
        "root_identity": {k: directories[-1][k] for k in ("dev", "ino", "uid", "mode")},
        "owner": owner.record(), "owner_sha256": owner.sha256(), "directories": directories,
        "control_files": control, "control_files_final": copy.deepcopy(control)}
    sessions = {"first": {"context": context}, "second": {"context": {**context, "nonce": "7" * 16}}}
    environments = {label: {"app": {"uid": 12002, "passwd_home": "/home/eidolon-app"}}
                    for label in ("first", "second")}
    return storage, sessions, environments


def open_fixture():
    """Synthetic admitted Open traces for target/Create join rejection."""
    main = {"scene": "1", "session": "2", "window": "17", "generation": "1"}
    child = {**main, "window": "18"}
    path = storage_fixture()[0]["root_path"] + "/Documents/n07-eight-lines.txt"

    def ticket(target, create):
        return {"runtime": "2", "target": target, "create": create,
                "content_frame": "1", "min_content_frame": "1", "layers": [{"surface": "1"}],
                "present_seq": 1, "roi_revision": 1,
                "rois": [{"roi": {"id": 1, "x": 0, "y": 0, "width": 4, "height": 4}}],
                "pixel_sha256": ["a" * 64], "roi_sha256": "b" * 64,
                "backend": {"readback": True, "renderer": "synthetic", "vendor": "synthetic"}}

    main_rb, child_rb = ticket(main, "2"), ticket(child, "3")
    dialog = {"name": "open", "window": {"window": "18", "parent": "17", "life": "Mapped",
              "frame": "1", "app_id": "synthetic"}, "target": child, "readback": child_rb}
    native = {"runtime": "2", "readback": main_rb, "observations": [], "sdk_observations": [],
              "n07_second": {"main_target": main, "dialogs": [dialog], "steps": [], "input": path}}

    def observe(kind, data):
        rows = native["observations"]
        rows.append({"sequence": str(len(rows) + 1), "kind": kind, "data": copy.deepcopy(data)})

    observe("n05_dialog_baseline", {"phase": "open", "main_target": main, "mapped_windows": ["17"]})
    request, delivered = 8000, 0
    for index, phase in enumerate(("open_shortcut", "open_location", "open_path")):
        target = main if index == 0 else child
        token = str(100 + index)
        count = (4, 4, 2 * len(path) + 2)[index]
        observe("n05_focus_current", {"target": target, "request_id": str(request), "token": token, "epoch": 0})
        request += 1
        step = {"phase": phase, "target": target, "epoch": 0, "token": token,
                "expected_delivery": count, "delivered_before": delivered,
                "delivered_after": delivered + count, "first_key_request": str(request),
                "next_key_request": str(request + count)}
        native["n07_second"]["steps"].append(step)
        observe("n05_step_delivered", step)
        request += count
        delivered += count
        if index == 0:
            observe("n05_dialog_created", {"name": "open", "window": "18", "parent": "17", "target": child})
    observe("n07_roi_readback_observed", {"phase": "before_open_confirm"})
    observe("n05_focus_current", {"target": child, "request_id": str(request), "token": "103", "epoch": 0})
    observe("n05_dialog_closed", {"name": "open", "window": "18", "target": child})
    observe("n05_confirm_delivered", {"name": "open", "target": child, "epoch": 0, "token": "103",
            "attempts": 1, "expected_delivery": 1, "delivered_before": delivered, "delivered_after": delivered + 1})
    observe("n07_b4_open_confirmed", {"target": child, "document": path})
    native["n07_second"]["final_key_request"] = str(request + 10)
    for target, readback in ((main, main_rb), (child, child_rb)):
        for kind in probe.n02.SDK_KINDS:
            rows = native["sdk_observations"]
            data = {"target": target, "create_seq": readback["create"]}
            if kind == "real_readback":
                data["readback"] = readback
            rows.append({"sequence": str(len(rows) + 1), "runtime": "2", "adapter": "2",
                         "kind": kind, "data": copy.deepcopy(data)})
            if kind == "create_applied" and target == main:
                rows.append({"sequence": str(len(rows) + 1), "runtime": "2", "adapter": "2",
                             "kind": "n07_renderer_pixel_readback", "data": {
                                 "target": main, "create_seq": "2", "sdk_adapter": "2",
                                 "content_frame": "1", "roi_count": 1}})
    return native


def barrier_fixture():
    run_id = "a" * 32
    host = {"run_id": run_id, "barriers": []}
    supervisor = {"barriers": [], "document": {"sha256": "d" * 64}}
    sessions, natives, environments = {}, {}, {}
    zero = {"app_process_count": 0, "driver_child_count": 0, "sshd_child_count": 0,
            "journal_entries": 0, "profile_lock_held": False, "journal_lock_held": False}
    for index, label in enumerate(("first", "second"), 1):
        cap = {"capture_id": str(index) * 64, "nonce": str(index) * 16}
        sessions[label] = {"capture": cap, "context": {"synthetic": label}}
        natives[label] = {"runtime": str(index), "observations": [], "trace": {"events": [
            {"sequence": "1", "kind": "capture_created"},
            {"sequence": "2", "kind": "control_send_attempt", "data": {"action": "start"}},
            {"sequence": "3", "kind": "other"}, {"sequence": "4", "kind": "finalizer_published"}]}}
        environments[label] = {"final": zero, "driver_control": {"synthetic": "retained-control"}}
    def observe(label, kind, data):
        rows = natives[label]["observations"]
        rows.append({"sequence": str(len(rows) + 1), "kind": kind, "data": data})
    for index, phase in enumerate(probe.PHASES):
        label = "first" if index < 3 else "second"
        runtime = natives[label]["runtime"]
        if phase.endswith("before_launch"):
            observe(label, "n07_b4_runtime_created", {"session": label, "runtime": runtime,
                "started_at_ns": 800 if label == "first" else 3500,
                "completed_at_ns": 900 if label == "first" else 3600})
        request = {"schema": 1, "run_id": run_id, "session": label, "phase": phase, "runtime": runtime,
            "capture_id": sessions[label]["capture"]["capture_id"], **sessions[label],
            "transport_watermark": 1 if phase.endswith("before_launch") else 4,
            "monotonic_ns": (index + 1) * 1000}
        observed = {"phase": phase, "session": label, "observed_at_ns": request["monotonic_ns"] + 20,
            "keyfile": None, "census": zero if phase.endswith("retired") else None,
            "driver_control": environments[label]["driver_control"] if phase.endswith("retired") else None,
            "product_cleanup_verified": phase.endswith("retired"), "document_sha256": "d" * 64}
        rq, ob = evidence.sha(evidence.canonical(request)), evidence.sha(evidence.canonical(observed))
        response = {"schema": 1, "phase": phase, "request_sha256": rq, "allow": True, "observation_sha256": ob}
        external = {"request": request, "request_sha256": rq,
            "received_at_ns": request["monotonic_ns"] + 10, "observation": observed,
            "observation_sha256": ob, "response": response, "sent_at_ns": request["monotonic_ns"] + 30}
        internal = {"request": request, "request_sha256": rq, "response": response,
            "response_monotonic_ns": request["monotonic_ns"] + 50, "error": None}
        if phase == "first_after_toggle":
            observe(label, "n07_b2_candidate_gui_transition", {})
        if phase == "second_after_observation":
            observe(label, "n07_roi_readback_observed", {"phase": "second_on_menu_context"})
        if not phase.endswith("retired"):
            observe(label, "n07_b4_barrier", internal)
        if phase.endswith("before_launch"):
            observe(label, "n02_command_accepted", {"command": "linux_desktop_v3_launch"})
        if phase == "first_after_toggle":
            observe(label, "n07_b4_close_intent", {})
        if phase.endswith("retired"):
            observe(label, "origin_retired", {})
            observe(label, "registry_destroy_return", {})
        host["barriers"].append(internal)
        supervisor["barriers"].append(external)
    return host, supervisor, sessions, natives, environments


class SecondRendererTests(unittest.TestCase):
    def test_actual_second_schema_geometry_and_local_frame_order(self):
        n, context, first = second_fixture()
        actual = probe.second_readbacks(n)
        self.assertEqual(actual[pixel.STATES[4]]["pixels"][0]["rect"],
                         {"x": 0, "y": 64, "width": 64, "height": 192})
        # Genuine per-session sequence is allowed to restart below first.
        self.assertLess(int(actual[pixel.STATES[4]]["receipt"]["content_frame"]),
                        int(first["native"]["n07_probe"]["b2"]["first_on_gutter"]["readback"]["receipt"]["content_frame"]))
        self.assertEqual(set(probe.second_context(n, context, actual)), set(probe.SECOND_CONTEXT_STATES))

    def test_relabel_foreign_identity_stale_frame_and_wrong_types_rejected(self):
        mutations = [
            lambda n, f: n["n07_second"].pop(pixel.STATES[5]),
            lambda n, f: n["n07_second"].update(second_on_checkmark=copy.deepcopy(
                f["native"]["n07_probe"]["b2"]["first_on_checkmark"])),
            lambda n, f: n["n07_second"][pixel.STATES[4]]["readback"]["receipt"].update(create="1"),
            lambda n, f: n["n07_second"][pixel.STATES[4]]["readback"].update(sdk_adapter="1"),
            lambda n, f: n["n07_second"][pixel.STATES[5]]["readback"]["receipt"].update(renderer_owner=1),
            lambda n, f: n["n07_second"][pixel.STATES[5]]["readback"]["receipt"].update(context_epoch=True),
            lambda n, f: n["n07_second"][pixel.STATES[5]]["readback"]["receipt"].update(content_frame=9),
            lambda n, f: n["n07_second"][pixel.STATES[5]]["readback"]["receipt"].update(content_frame="6"),
            lambda n, f: n["n07_second"]["post_open_settling"].update(main_content_seq="3"),
            lambda n, f: n["n07_second"]["post_open_settling"]["samples"][0].update(elapsed_ms=-1),
            lambda n, f: n["n07_second"]["post_open_settling"]["samples"][-1].update(elapsed_ms=100),
            lambda n, f: n["n07_second"]["pointer"]["after"].update(delivered_buttons=0),
            lambda n, f: n["n07_second"]["pointer"].update(input_epoch=False),
            lambda n, f: n["n07_second"]["popup"].update(grab_serial=999),
            lambda n, f: n["n07_second"].update(item_pointer={"status": "pass"}),
        ]
        for index, mutate in enumerate(mutations):
            n, _, first = second_fixture()
            mutate(n, first)
            with self.subTest(index=index), self.assertRaises((ValueError, KeyError, TypeError)):
                probe.second_readbacks(n)

    def test_saved_context_digest_and_foreign_capture_rejected(self):
        n, raw, _ = second_fixture()
        values = probe.second_readbacks(n)
        with self.assertRaises(ValueError):
            probe.second_context(n, raw + b" ", values)
        changed = evidence.parse(raw, limit=probe.MAX_CONTEXT_BYTES)
        changed["capture_id"] = "f" * 64
        raw = evidence.canonical(changed)
        n["review_context"].update(bytes=len(raw), sha256=evidence.sha(raw))
        with self.assertRaises(ValueError):
            probe.second_context(n, raw, values)

    def test_second_fingerprint_has_its_own_process_and_capture_binding(self):
        for schema in (probe.SECOND_SCHEMA, probe.SECOND_SCHEMA_V2):
            with self.subTest(schema=schema):
                record, _, _, source = fingerprint_fixture()
                second, _, _ = second_fixture()
                second["schema"] = schema
                environment = {
                    "run_id": second["run_id"], "source_sha256": source["tree_sha256"],
                    "app": {"uid": 12002}, "before": {"pins": {"waypipe_sha256": "4" * 64}},
                    "after": {"pins": {"waypipe_sha256": "4" * 64}}}
                record["binding"] = fingerprint._binding(second, environment, source)
                record["evidence"]["renderer"] = fingerprint._renderer(second)
                record["values"] = fingerprint._derive(record, second, environment, source)
                fingerprint.validate_fingerprint(record, second, environment, source)
                self.assertEqual(record["binding"]["runtime"], "2")
                record["binding"]["capture_id"] = "b" * 64
                with self.assertRaises(ValueError):
                    fingerprint.validate_fingerprint(record, second, environment, source)

    def test_second_fingerprint_rejects_unknown_schema_and_variant(self):
        second, _, _ = second_fixture()
        for schema in (None, "eidolon-i6-n07-b4-second-native-v3"):
            with self.subTest(schema=schema):
                second["schema"] = schema
                with self.assertRaisesRegex(ValueError, "N07_FINGERPRINT_SESSION_SCHEMA"):
                    fingerprint._session_readbacks(second)
        for schema in (probe.SECOND_SCHEMA, probe.SECOND_SCHEMA_V2):
            with self.subTest(schema=schema, variant="foreign"):
                second.update(schema=schema, variant="foreign")
                with self.assertRaisesRegex(ValueError, "N07_FINGERPRINT_SESSION_SCHEMA"):
                    fingerprint._session_readbacks(second)


class IndependentStorageAndGateTests(unittest.TestCase):
    def test_open_dialog_is_joined_to_actual_sdk_create_and_lifecycle(self):
        native = open_fixture()
        probe._open(native, probe.n02.ordinal_events(native["observations"], limit=128))

        def event(data, kind):
            return next(row["data"] for row in data["observations"] if row["kind"] == kind)

        def sdk_readback(data):
            return next(row["data"]["readback"] for row in data["sdk_observations"]
                        if row["kind"] == "real_readback" and row["data"]["target"]["window"] == "18")

        mutations = (
            lambda d: d["n07_second"]["dialogs"][0]["readback"].update(create="9999"),
            lambda d: event(d, "n05_dialog_created").update(window="99"),
            lambda d: event(d, "n05_dialog_closed")["target"].update(generation="2"),
            lambda d: event(d, "n07_b4_open_confirmed").update(document="foreign"),
            lambda d: sdk_readback(d).update(content_frame=1),
            lambda d: d["n07_second"]["dialogs"][0]["window"].update(frame=True),
            lambda d: d["n07_second"].update(final_key_request="8000"),
        )
        for index, mutate in enumerate(mutations):
            changed = copy.deepcopy(native)
            mutate(changed)
            with self.subTest(index=index), self.assertRaises((ValueError, KeyError, TypeError)):
                probe._open(changed, probe.n02.ordinal_events(changed["observations"], limit=128))

    def test_canonical_storage_root_owner_and_control_files(self):
        storage, sessions, env = storage_fixture()
        self.assertEqual(probe._storage(storage, sessions, env), storage["record"])
        mutations = (
            lambda s: s.update(root_path="/home/eidolon-app/arbitrary-root"),
            lambda s: s["record"].update(profile_storage_id="f" * 32),
            lambda s: s["root_identity"].update(ino=True),
            lambda s: s["directories"][4].update(uid=0),
            lambda s: s["directories"][4].update(mode=0o777),
            lambda s: s["control_files_final"]["profile.lock"].update(ino=999),
            lambda s: s["control_files"]["owner.json"].update(nlink=2),
            lambda s: s.update(owner_sha256="f" * 64),
        )
        for index, mutate in enumerate(mutations):
            changed = copy.deepcopy(storage)
            mutate(changed)
            with self.subTest(index=index), self.assertRaises(ValueError):
                probe._storage(changed, sessions, env)

    def test_live_retained_control_is_only_count_exclusion(self):
        count = {"driver_child_count": 0}
        control = {"pid": 101, "start_ticks": 201, "uid": 12001,
                   "raw_driver_process_count": 1, "excluded_live_control": 1}
        probe._driver_control(control, 12001, count)
        for key, value in (("excluded_live_control", True), ("excluded_live_control", 2),
                           ("raw_driver_process_count", 2), ("uid", 12002), ("start_ticks", 0)):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                probe._driver_control({**control, key: value}, 12001, count)

    def test_barriers_recompute_hashes_clock_order_and_runtime_gate(self):
        data = barrier_fixture()
        self.assertEqual(tuple(probe._barriers(*data)), probe.PHASES)
        mutations = (
            lambda d: d[1]["barriers"][0]["observation"].update(observed_at_ns=999),
            lambda d: d[1]["barriers"][2]["observation"].update(product_cleanup_verified=False),
            lambda d: d[1]["barriers"][3]["response"].update(allow=False),
            lambda d: d[0]["barriers"][2].update(response_monotonic_ns=4500),
            lambda d: d[3]["second"]["observations"][0]["data"].update(started_at_ns=2000),
            lambda d: d[0]["barriers"].pop(2),
        )
        for index, mutate in enumerate(mutations):
            changed = copy.deepcopy(data)
            mutate(changed)
            with self.subTest(index=index), self.assertRaises((ValueError, KeyError, TypeError)):
                probe._barriers(*changed)

    def test_missing_failed_partial_and_rescue_never_become_collection(self):
        for bad in ({}, {"status": "pass"}, {"status": "pass", "external_rescue": True},
                    {"sessions": ["first"], "second": None}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                probe.validate_collection(bad, {}, None, None, {}, {}, {}, {})

    def test_formal_pin_and_manifest_are_unchanged(self):
        self.assertIsNone(pixel.APPROVED_SHA256)
        with self.assertRaisesRegex(ValueError, "N07_GOLDEN_UNREVIEWED"):
            pixel.load_reviewed()
        self.assertEqual(evidence.MANIFEST_SHA256,
                         "e2d0d80c35fc5494415a627a7a11d162b127fecc38646535afacf9f08ff40f33")


if __name__ == "__main__":
    unittest.main()
