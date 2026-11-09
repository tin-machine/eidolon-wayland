"""Read-only GUI observation transport/schema adversaries, without a display.

These checks do not certify Mousepad GUI behavior. Dedicated real-app CI must
load the GTK module and obtain the independent seat-driven observations.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_real_app_gui_observer as observer
import linux_real_app_build as build

PROFILE = "a" * 32
RUN = "b" * 32


def snapshot(request=1):
    targets = {}
    for name in observer.TARGETS:
        protected = name in ("opening_mode", "session_restore")
        tab = name.startswith("tab_")
        targets[name] = {"present": True, "mapped": True, "sensitive": not protected,
                         "center": [150, 75], "size": [100, 20],
                         "active": None if protected or tab else False,
                         "expected_value": True if protected else None,
                         "value_digest": hashlib.sha256(("tab" if name == "opening_mode" else "never").encode()).hexdigest()
                         if protected else None}
    return {"schema": 1, "id": request, "run_id": RUN, "profile_id": PROFILE, "ok": True,
            "main_count": 1, "preferences_count": 1,
            "main": {"mapped": True, "view_count": 2, "mapped_view_count": 1, "line_numbers": False,
                     "document_tabs": 2, "toplevel_size": [650, 500]},
            "preferences": {"mapped": True, "transient_for_main": True, "page": 0, "pages": 5,
                            "toplevel_size": [600, 700], "targets": targets}}


class ObservationSchema(unittest.TestCase):
    def test_accepts_fixed_live_properties_but_does_not_certify_the_gui(self):
        value = snapshot()
        self.assertIs(observer.validate_snapshot(value, 1, PROFILE, RUN), value)
        # Sensitivity and changed values are observations, not transport errors;
        # the measured GUI oracle/receipt validator must fail their case.
        value["preferences"]["targets"]["opening_mode"]["sensitive"] = True
        value["preferences"]["targets"]["opening_mode"]["expected_value"] = False
        self.assertIs(observer.validate_snapshot(value, 1, PROFILE, RUN), value)

    def test_rejects_stale_request_and_other_run_or_profile(self):
        for field, bad in (("id", 0), ("id", True), ("run_id", "c" * 32), ("profile_id", "d" * 32)):
            with self.subTest(field=field, bad=bad):
                value = snapshot()
                value[field] = bad
                with self.assertRaisesRegex(observer.ObserverError, "APP_GUI_OBSERVER_CORRELATION"):
                    observer.validate_snapshot(value, 1, PROFILE, RUN)

    def test_rejects_free_form_text_pixel_and_property_fields_at_every_level(self):
        for where in ((), ("main",), ("preferences",), ("preferences", "targets", "opening_mode")):
            for field in ("title", "text", "pixels", "arbitrary_property"):
                with self.subTest(where=where, field=field):
                    value = snapshot()
                    target = value
                    for path in where:
                        target = target[path]
                    target[field] = "must never appear"
                    with self.assertRaises(observer.ObserverError):
                        observer.validate_snapshot(value, 1, PROFILE, RUN)

    def test_rejects_missing_and_third_protected_widget(self):
        for mutation in (lambda d: d.pop("session_restore"),
                         lambda d: d.update(autosave_timer=copy.deepcopy(d["session_restore"]))):
            value = snapshot()
            mutation(value["preferences"]["targets"])
            with self.assertRaises(observer.ObserverError):
                observer.validate_snapshot(value, 1, PROFILE, RUN)

    def test_rejects_ambiguous_main_and_visible_views(self):
        for path, bad in ((("main_count",), 2), (("preferences_count",), 2),
                          (("main", "mapped_view_count"), 2), (("main", "mapped_view_count"), True),
                          (("main", "line_numbers"), "false"), (("main", "document_tabs"), True)):
            with self.subTest(path=path):
                value = snapshot()
                target = value
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = bad
                with self.assertRaises(observer.ObserverError):
                    observer.validate_snapshot(value, 1, PROFILE, RUN)

    def test_rejects_absent_control_with_fabricated_observation(self):
        value = snapshot()
        value["preferences"]["targets"]["opening_mode"]["present"] = False
        with self.assertRaises(observer.ObserverError):
            observer.validate_snapshot(value, 1, PROFILE, RUN)

    def test_rejects_invalid_coordinates_and_enum_digest(self):
        for field, bad in (("center", [False, 10]), ("center", [0, 32769]), ("size", [-1, 20]),
                           ("value_digest", "tab"), ("expected_value", 1), ("active", True)):
            with self.subTest(field=field):
                value = snapshot()
                value["preferences"]["targets"]["opening_mode"][field] = bad
                with self.assertRaises(observer.ObserverError):
                    observer.validate_snapshot(value, 1, PROFILE, RUN)

    def test_rejects_false_topology_result(self):
        value = snapshot()
        value["ok"] = False
        with self.assertRaisesRegex(observer.ObserverError, "APP_GUI_OBSERVER_TOPOLOGY"):
            observer.validate_snapshot(value, 1, PROFILE, RUN)


class SyntheticScope(unittest.TestCase):
    def test_marker_binds_fresh_run_to_profile_identity_without_touching_settings(self):
        with tempfile.TemporaryDirectory(prefix="a0-gui-") as temporary:
            base = Path(temporary)
            runtime, profile = base / "runtime", base / "profile"
            runtime.mkdir(mode=0o700)
            profile.mkdir(mode=0o700)
            for name in ("home", "config", "data", "state"):
                (profile / name).mkdir(mode=0o700)
            env = observer.prepare(runtime, profile, PROFILE, RUN)
            identity = profile.stat()
            expected = f"eidolon-a0-gui-observer-v1\n{RUN}\n{PROFILE}\n{identity.st_dev}:{identity.st_ino}\n".encode()
            self.assertEqual((runtime / observer.MARKER_NAME).read_bytes(), expected)
            self.assertEqual(env, {"EIDOLON_A0_GUI_RUN_ID": RUN, "EIDOLON_A0_GUI_PROFILE_ID": PROFILE})
            self.assertEqual(list((profile / "config").iterdir()), [])
            with self.assertRaises(FileExistsError):
                observer.prepare(runtime, profile, PROFILE, RUN)

    def test_rejects_symlink_runtime_and_writable_profile(self):
        with tempfile.TemporaryDirectory(prefix="a0-gui-") as temporary:
            base = Path(temporary)
            actual = base / "actual"
            actual.mkdir(mode=0o700)
            link = base / "link"
            link.symlink_to(actual)
            with self.assertRaises((OSError, ValueError)):
                observer.Observer(link, PROFILE, RUN)
            actual.chmod(0o755)
            with self.assertRaises(observer.ObserverError):
                observer.Observer(actual, PROFILE, RUN)

    def test_gui_builder_does_not_overwrite_existing_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="a0-gui-") as temporary:
            path = Path(temporary)
            (path / "keep").write_bytes(b"keep")
            with self.assertRaises(FileExistsError):
                build.build_gui_observer(path)
            self.assertEqual((path / "keep").read_bytes(), b"keep")


class ObserverTransport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.close()
        except PermissionError as error:
            if os.environ.get("EIDOLON_REQUIRE_GUI_OBSERVER_TESTS") == "1":
                raise AssertionError("A0 GUI observer requires actual local UNIX sockets") from error
            raise unittest.SkipTest("local UNIX sockets are prohibited; dedicated CI requires these transport tests") from error

    def exchange(self, responses, checks, *, peer_check=None):
        with tempfile.TemporaryDirectory(prefix="a0-gui-") as temporary:
            runtime = Path(temporary)
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(server.close)
            path = runtime / observer.SOCKET_NAME
            server.bind(str(path))
            path.chmod(0o600)
            server.listen(2)
            server.settimeout(2)
            errors = []
            requests = []

            def serve():
                try:
                    for response in responses:
                        connection, _ = server.accept()
                        with connection:
                            connection.settimeout(2)
                            line = bytearray()
                            while not line.endswith(b"\n"):
                                chunk = connection.recv(64)
                                if not chunk:
                                    break
                                line.extend(chunk)
                            requests.append(bytes(line))
                            connection.sendall(response)
                except (OSError, AssertionError) as error:
                    errors.append(error)

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            pumps = []
            client = observer.Observer(runtime, PROFILE, RUN, pump=lambda: pumps.append(True), timeout=1,
                                       peer_check=peer_check)
            try:
                checks(client)
            finally:
                thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertTrue(pumps)
            return requests

    def test_fresh_requests_are_sent_and_owner_pump_continues(self):
        responses = [(json.dumps(snapshot(n)) + "\n").encode() for n in (1, 2)]
        peers = []
        def checks(client):
            self.assertEqual(client.snapshot()["id"], 1)
            self.assertEqual(client.snapshot()["id"], 2)
            self.assertEqual(client.peer_pid, os.getpid())
        self.assertEqual(self.exchange(responses, checks, peer_check=lambda pid: peers.append(pid)),
                         [b"OBSERVE 1\n", b"OBSERVE 2\n"])
        self.assertEqual(peers, [os.getpid(), os.getpid()])

    def test_owner_peer_rejection_precedes_any_observation_request(self):
        def reject(_pid):
            raise observer.ObserverError("APP_GUI_OBSERVER_WRONG_EXECUTABLE")
        def checks(client):
            with self.assertRaisesRegex(observer.ObserverError, "APP_GUI_OBSERVER_WRONG_EXECUTABLE"):
                client.snapshot()
        self.assertEqual(self.exchange([b""], checks, peer_check=reject), [b""])

    def test_duplicate_json_fields_are_rejected(self):
        data = json.dumps(snapshot()).replace('"id": 1', '"id": 1, "id": 1').encode() + b"\n"
        def checks(client):
            with self.assertRaises(observer.ObserverError):
                client.snapshot()
        self.exchange([data], checks)

    def test_stale_socket_reply_is_rejected(self):
        data = json.dumps(snapshot(2)).encode() + b"\n"
        def checks(client):
            with self.assertRaisesRegex(observer.ObserverError, "APP_GUI_OBSERVER_CORRELATION"):
                client.snapshot()
        self.exchange([data], checks)

    def test_incomplete_record_and_missing_observer_are_failures(self):
        def checks(client):
            with self.assertRaisesRegex(observer.ObserverError, "APP_GUI_OBSERVER_EOF"):
                client.snapshot()
        self.exchange([b'{"schema":1}'], checks)
        with tempfile.TemporaryDirectory(prefix="a0-gui-") as temporary:
            client = observer.Observer(Path(temporary), PROFILE, RUN, timeout=.02)
            with self.assertRaisesRegex(observer.ObserverError, "APP_GUI_OBSERVER_TIMEOUT"):
                client.snapshot()


if __name__ == "__main__":
    unittest.main()
