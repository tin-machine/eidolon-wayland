"""A inspection is static; all package, HOME and recovery data here are synthetic.

The schema fixture is unmodified Mousepad 0.6.3 source, SHA256 pinned by the
inspector. It is read as data; no Mousepad executable is run by these tests.
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("linux_real_app_inspect", ROOT / "scripts/linux_real_app_inspect.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
SCHEMA = (Path(__file__).parent / "fixtures/mousepad-0.6.3.gschema.xml").read_bytes()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def private_dirs(path):
    missing = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)


def write(path, data, mode=0o600):
    private_dirs(path.parent)
    path.write_bytes(data)
    path.chmod(mode)


class PackageFixture:
    """Synthetic Debian metadata with regular real files and no loader calls."""
    def __init__(self, root):
        self.root = root
        self.version = "0.6.3-0eidolon.a0.1"
        self.arch = "amd64"
        self.files = {}
        self.versions = {"mousepad": self.version, "libglib2.0-0": "2.80.5-1", "eidolon-test-deps": "1.0"}
        elf = bytearray(64)
        elf[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<HH", elf, 16, 3, 62)
        self.executable = bytes(elf)
        self.put(M.EXECUTABLE, self.executable, executable=True, package="mousepad")
        self.makefile = b"#am__append_1 = -DMOUSEPAD_SETTINGS_KEYFILE_BACKEND\nlibmousepad_la_CFLAGS = -Wall $(am__append_1)\n"
        self.put(M.BUILD_MAKEFILE_PATH, self.makefile, package="mousepad")
        self.evidence = {"schema": 1, "package": "mousepad", "version": self.version, "architecture": self.arch,
                         "upstream_version": M.UPSTREAM_VERSION, "source_commit": M.UPSTREAM_COMMIT,
                         "settings_backend": "default", "executable_sha256": sha(self.executable),
                         "makefile_sha256": sha(self.makefile), "source_sha256": dict(M.SOURCE_HASHES)}
        roles = {"gtk": "/usr/lib/libgtk-3.so.0.1", "glib": "/usr/lib/libglib-2.0.so.0.1",
                 "gio": "/usr/lib/libgio-2.0.so.0.1", "gtksourceview": "/usr/lib/libgtksourceview-4.so.0.1",
                 "dbus_daemon": "/usr/bin/dbus-daemon", "dbus_run_session": "/usr/bin/dbus-run-session",
                 "schema_xml": M.SCHEMA_PATH, "schema_compiled": M.COMPILED_SCHEMA_PATH}
        deps = []
        for role, path in sorted(roles.items()):
            package = "mousepad" if role == "schema_xml" else "libglib2.0-0" if role == "schema_compiled" else "eidolon-test-deps"
            content = SCHEMA if role == "schema_xml" else b"GVariant\x00synthetic-bounded-file" if role == "schema_compiled" else (role + " fixture").encode()
            self.put(path, content, executable=role.startswith("dbus_"), package=None if role == "schema_compiled" else package)
            deps.append({"role": role, "path": path, "sha256": sha(content), "package": package,
                         "version": self.versions[package], "architecture": self.arch})
        self.descriptor = {"schema": 1, "app_id": M.APP_ID, "package": "mousepad", "package_version": self.version,
                           "architecture": self.arch, "upstream_version": M.UPSTREAM_VERSION,
                           "executable_sha256": sha(self.executable), "source_evidence_sha256": "0" * 64,
                           "dependencies": deps}
        self.update_evidence()
        self.refresh_database()

    def path(self, path):
        return self.root / path.lstrip("/")

    def put(self, path, data, *, executable=False, package=None):
        write(self.path(path), data, 0o755 if executable else 0o644)
        if package:
            self.files.setdefault(package, set()).add(path)

    def update_evidence(self):
        data = M.canonical_json(self.evidence)
        self.put(M.BUILD_EVIDENCE_PATH, data, package="mousepad")
        self.descriptor["source_evidence_sha256"] = sha(data)

    def refresh_database(self):
        statuses = []
        for package, version in self.versions.items():
            statuses.append(f"Package: {package}\nStatus: install ok installed\nArchitecture: {self.arch}\nVersion: {version}\nDescription: synthetic only\n fixture continuation\n")
            paths = self.files.get(package, {"/usr/share/doc/unused"})
            self.put(f"/var/lib/dpkg/info/{package}:{self.arch}.list", ("\n".join(sorted(paths)) + "\n").encode())
        self.put("/var/lib/dpkg/status", ("\n".join(statuses) + "\n").encode())

    def inspect(self, descriptor=None):
        return M.inspect_host(self.descriptor if descriptor is None else descriptor, system_root=self.root)


class PackageInspectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        # Exercise the real stat/permission/no-follow checks as an unprivileged
        # CI user too. Production owner is always uid0, with no CLI override.
        self.owner = mock.patch.object(M, "SYSTEM_OWNER_UID", os.getuid())
        self.owner.start()
        self.fixture = PackageFixture(self.root)

    def tearDown(self):
        self.owner.stop()
        self.temp.cleanup()

    def test_reviewed_package_static_success_is_never_runtime_availability(self):
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("must not execute")), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("must not execute")), \
             mock.patch.object(os, "system", side_effect=AssertionError("must not execute")), \
             mock.patch.object(os, "execve", side_effect=AssertionError("must not execute")):
            receipt = self.fixture.inspect()
        self.assertTrue(receipt["inspection_passed"], receipt)
        self.assertEqual(receipt["reason"], "NONE")
        self.assertFalse(receipt["available"])
        self.assertFalse(receipt["runtime_verified"])
        M.validate_inspection_receipt(receipt)
        self.assertNotIn(str(self.root), json.dumps(receipt))
        record = M.validate_reviewed_package(self.fixture.descriptor, system_root=self.root)
        self.assertEqual(receipt["dependency_fingerprint"], M.digest(record))

    def test_no_descriptor_does_not_infer_support_from_installed_name(self):
        receipt = M.inspect_host(system_root=self.root)
        self.assertFalse(receipt["inspection_passed"])
        self.assertEqual(receipt["reason"], "APP_PACKAGE_UNSUPPORTED")
        self.assertTrue(receipt["checks"]["executable"])

    def test_missing_executable_is_not_installed(self):
        self.fixture.path(M.EXECUTABLE).unlink()
        self.assertEqual(self.fixture.inspect()["reason"], "APP_NOT_INSTALLED")

    def test_dependency_hash_changes_are_refused(self):
        dependency = self.fixture.descriptor["dependencies"][0]
        self.fixture.path(dependency["path"]).write_bytes(b"updated package")
        self.assertEqual(self.fixture.inspect()["reason"], "APP_DEPENDENCY_CHANGED")

    def test_package_version_and_file_ownership_are_checked(self):
        self.fixture.versions["mousepad"] = "0.6.4"
        self.fixture.refresh_database()
        self.assertEqual(self.fixture.inspect()["reason"], "APP_DEPENDENCY_CHANGED")
        self.fixture.versions["mousepad"] = self.fixture.version
        self.fixture.files["mousepad"].remove(M.EXECUTABLE)
        self.fixture.refresh_database()
        self.assertEqual(self.fixture.inspect()["reason"], "APP_DEPENDENCY_CHANGED")

    def test_merged_usr_package_list_alias_requires_exact_root_owned_lib_link(self):
        item = next(item for item in self.fixture.descriptor["dependencies"] if item["role"] == "gtk")
        paths = self.fixture.files[item["package"]]
        paths.remove(item["path"])
        paths.add(item["path"][len("/usr"):])
        self.fixture.refresh_database()
        alias = self.root / "lib"
        for target in ("usr/lib", "/usr/lib"):
            alias.symlink_to(target, target_is_directory=True)
            self.assertTrue(self.fixture.inspect()["inspection_passed"], target)
            alias.unlink()
        self.assertFalse(self.fixture.inspect()["inspection_passed"])
        alias.symlink_to("usr/../usr/lib", target_is_directory=True)
        self.assertEqual(self.fixture.inspect()["reason"], "APP_DEPENDENCY_CHANGED")
        alias.unlink()
        alias.mkdir(mode=0o755)
        self.assertEqual(self.fixture.inspect()["reason"], "APP_DEPENDENCY_CHANGED")

    def test_package_ownership_cannot_follow_other_library_symlinks(self):
        item = next(item for item in self.fixture.descriptor["dependencies"] if item["role"] == "gtk")
        alias = self.fixture.path(item["path"] + ".soname")
        alias.symlink_to(Path(item["path"]).name)
        paths = self.fixture.files[item["package"]]
        paths.remove(item["path"])
        paths.add(item["path"] + ".soname")
        self.fixture.refresh_database()
        self.assertEqual(self.fixture.inspect()["reason"], "APP_DEPENDENCY_CHANGED")

    def test_generated_compiled_schema_is_not_required_in_a_package_list(self):
        self.assertFalse(any(M.COMPILED_SCHEMA_PATH in paths for paths in self.fixture.files.values()))
        self.assertTrue(self.fixture.inspect()["inspection_passed"])
        self.fixture.path(M.COMPILED_SCHEMA_PATH).chmod(0o666)
        self.assertEqual(self.fixture.inspect()["reason"], "APP_DEPENDENCY_CHANGED")

    def test_makefile_default_backend_and_source_commit_are_evidence(self):
        self.fixture.evidence["source_commit"] = "0" * 40
        self.fixture.update_evidence()
        self.assertEqual(self.fixture.inspect()["reason"], "APP_PACKAGE_UNSUPPORTED")
        self.fixture.evidence["source_commit"] = M.UPSTREAM_COMMIT
        self.fixture.makefile = self.fixture.makefile.replace(b"#am__append_1", b"am__append_1")
        self.fixture.put(M.BUILD_MAKEFILE_PATH, self.fixture.makefile, package="mousepad")
        self.fixture.evidence["makefile_sha256"] = sha(self.fixture.makefile)
        self.fixture.update_evidence()
        self.assertEqual(self.fixture.inspect()["reason"], "APP_BACKEND_UNSUPPORTED")

    def test_declared_custom_backend_is_explicitly_unsupported(self):
        self.fixture.evidence["settings_backend"] = "keyfile"
        self.fixture.update_evidence()
        self.assertEqual(self.fixture.inspect()["reason"], "APP_BACKEND_UNSUPPORTED")

    def test_missing_build_evidence_and_incomplete_package_are_not_supported(self):
        self.fixture.path(M.BUILD_EVIDENCE_PATH).unlink()
        self.assertEqual(self.fixture.inspect()["reason"], "APP_PACKAGE_UNSUPPORTED")
        self.fixture.update_evidence()
        path = self.fixture.path("/var/lib/dpkg/status")
        path.write_bytes(path.read_bytes().replace(b"install ok installed", b"install ok unpacked", 1))
        self.assertEqual(self.fixture.inspect()["reason"], "APP_DEPENDENCY_CHANGED")

    def test_wrong_architecture_executable_is_unsupported(self):
        self.fixture.descriptor["architecture"] = "arm64"
        self.assertEqual(self.fixture.inspect()["reason"], "APP_PACKAGE_UNSUPPORTED")

    def test_schema_fixture_is_pinned_and_exact_policy_keys_are_verified(self):
        self.assertEqual(sha(SCHEMA), M.SOURCE_HASHES["mousepad/org.xfce.mousepad.gschema.xml"])
        M._check_schema(SCHEMA)
        for bad in (SCHEMA.replace(b'<key name="session" type="as">', b'<key name="session" type="s">'),
                    SCHEMA.replace(b'<key name="session"', b'<key name="not-session"'),
                    SCHEMA.replace(b"<default>'after-a-crash'</default>", b"<default>'never'</default>")):
            with self.subTest(case=sha(bad)), self.assertRaisesRegex(M.InspectionError, "APP_SETTINGS_UNVERIFIED"):
                M._check_schema(bad)

    def test_wrong_compiled_schema_magic_fails_even_when_descriptor_hash_matches(self):
        dependency = next(item for item in self.fixture.descriptor["dependencies"] if item["role"] == "schema_compiled")
        self.fixture.path(M.COMPILED_SCHEMA_PATH).write_bytes(b"not-a-compiled-schema")
        dependency["sha256"] = sha(b"not-a-compiled-schema")
        self.assertEqual(self.fixture.inspect()["reason"], "APP_SETTINGS_UNVERIFIED")

    def test_system_file_symlink_is_rejected_without_following(self):
        executable = self.fixture.path(M.EXECUTABLE)
        target = self.root / "other-executable"
        executable.rename(target)
        executable.symlink_to(target)
        self.assertEqual(self.fixture.inspect()["reason"], "APP_DEPENDENCY_CHANGED")

    def test_parent_symlink_and_writable_directory_are_rejected(self):
        directory = self.root / "usr/bin"
        moved = self.root / "other-bin"
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
        self.assertEqual(self.fixture.inspect()["reason"], "APP_DEPENDENCY_CHANGED")
        directory.unlink()
        moved.rename(directory)
        directory.chmod(0o777)
        self.assertEqual(self.fixture.inspect()["reason"], "APP_DEPENDENCY_CHANGED")

    def test_wrong_system_owner_and_hardlink_are_rejected(self):
        with mock.patch.object(M, "SYSTEM_OWNER_UID", os.getuid() + 1):
            self.assertEqual(self.fixture.inspect()["reason"], "APP_DEPENDENCY_CHANGED")
        os.link(self.fixture.path(M.EXECUTABLE), self.root / "second-link")
        self.assertEqual(self.fixture.inspect()["reason"], "APP_DEPENDENCY_CHANGED")

    def test_global_keyfile_defaults_and_locks_are_pinned_and_never_changed(self):
        initial = self.fixture.inspect()["dependency_fingerprint"]
        default_path = self.fixture.path(M.SYSTEM_SETTINGS_DIRECTORY + "/defaults")
        locks_path = self.fixture.path(M.SYSTEM_SETTINGS_DIRECTORY + "/locks")
        defaults = b"[org/xfce/mousepad/preferences/view]\nfont-name='Monospace 12'\n"
        write(default_path, defaults, 0o644)
        write(locks_path, b"", 0o644)
        receipt = self.fixture.inspect()
        self.assertTrue(receipt["inspection_passed"], receipt)
        self.assertNotEqual(receipt["dependency_fingerprint"], initial)
        self.assertEqual(default_path.read_bytes(), defaults)
        self.assertEqual(locks_path.read_bytes(), b"")
        for key in M.REQUIRED_WRITABLE_KEYS:
            content = b"# Lock a probe-required ordinary preference\n" + key + b"\n"
            locks_path.write_bytes(content)
            self.assertEqual(self.fixture.inspect()["reason"], "APP_SETTINGS_UNVERIFIED")
            self.assertEqual(locks_path.read_bytes(), content)
        locks_path.unlink()
        locks_path.symlink_to(default_path)
        self.assertEqual(self.fixture.inspect()["reason"], "APP_DEPENDENCY_CHANGED")

    def test_fingerprint_sort_is_independent_of_descriptor_list_order(self):
        first = self.fixture.inspect()["dependency_fingerprint"]
        self.fixture.descriptor["dependencies"].reverse()
        self.assertEqual(self.fixture.inspect()["dependency_fingerprint"], first)

    def test_strict_descriptor_rejects_extra_missing_duplicates_and_path_escape(self):
        mutations = [lambda v: v.update(command="mousepad --help"), lambda v: v.update(schema=True),
                     lambda v: v["dependencies"].pop(),
                     lambda v: v["dependencies"].append(copy.deepcopy(v["dependencies"][0])),
                     lambda v: v["dependencies"][0].update(path="/usr/../tmp/dependency"),
                     lambda v: v["dependencies"][0].update(role=[])]
        for mutate in mutations:
            descriptor = copy.deepcopy(self.fixture.descriptor)
            mutate(descriptor)
            with self.subTest(mutation=mutate), self.assertRaises(M.InspectionError):
                M.validate_reviewed_package(descriptor, system_root=self.root)
        for data in (b'{"schema":1,"schema":1}', b'{"bad":NaN}', b'{"bad":Infinity}', b'[]', b'{}' * M.MAX_JSON_BYTES):
            with self.assertRaises(M.InspectionError):
                M.decode_record(data)


class RecoveryGuardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "profile"
        self.root.mkdir(mode=0o700)
        self.keyfile = self.root / M.SETTINGS_RELATIVE_PATH
        self.recovery = self.root / M.RECOVERY_DIRECTORY
        self.document = self.root / "Documents" / "private-document.txt"
        write(self.document, b"Private document must remain byte-identical.\n")

    def tearDown(self):
        self.temp.cleanup()

    def assert_blocked_unchanged(self):
        paths = [path for path in self.root.rglob("*") if path.is_file() and not path.is_symlink()]
        snapshots = {path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode) for path in paths}
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("must not spawn")):
            result = M.guard_recovery_state(self.root)
        self.assertFalse(result["clear"], result)
        self.assertEqual(result["reason"], "APP_STATE_RECOVERY_REQUIRED")
        for path, before in snapshots.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode), before)
        serialized = json.dumps(result)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("private-document", serialized)
        return result

    def test_fresh_profile_is_clear_and_documents_are_ignored(self):
        result = M.guard_recovery_state(self.root)
        self.assertTrue(result["clear"], result)
        self.assertEqual(result["reason"], "NONE")
        self.assertEqual(self.document.read_bytes(), b"Private document must remain byte-identical.\n")

    def test_exact_session_group_handles_missing_and_explicit_empty_arrays(self):
        for data in (b"", b"# only comments\n", b"[org/xfce/mousepad/preferences/view]\nfont-name='Monospace 12'\n",
                     b"[org/xfce/mousepad/state/application]\nsession=[]\n",
                     b"[org/xfce/mousepad/state/application]\nsession=@as []\n"):
            with self.subTest(data=data):
                write(self.keyfile, data)
                self.assertTrue(M.guard_recovery_state(self.root)["clear"])
        write(self.keyfile, b"[org/xfce/mousepad/state/application]\nsession=['1;file:///autosave-1;+file:///private']\n")
        result = self.assert_blocked_unchanged()
        self.assertFalse(result["session_empty"])

    def test_malformed_duplicate_wrong_type_and_truncated_session_are_not_empty(self):
        cases = (b"[org/xfce/mousepad/state/application]\nsession=''\n",
                 b"[org/xfce/mousepad/state/application]\nsession=['unfinished\n",
                 b"[org/xfce/mousepad/state/application]\nsession=['saved']\nsession=[]\n",
                 b"[org/xfce/mousepad/state/application]\nsession=[]\n[org/xfce/mousepad/state/application]\n",
                 b"[org/xfce/mousepad/state/application]\nsession=@ai []\n",
                 b"[org/xfce/mousepad/state/application]\nsession=[]\x00\n",
                 b"[org/xfce/mousepad/state/application]\nmalformed\n",
                 b"\xff", b"[org/xfce/mousepad/state/application]" + "\u2028session=[]".encode())
        for data in cases:
            with self.subTest(data=data):
                write(self.keyfile, data)
                self.assert_blocked_unchanged()

    def test_autosave_and_unclassifiable_recovery_entries_are_preserved(self):
        private_dirs(self.recovery)
        for name in ("autosave-1", "autosave--1", "unknown-file"):
            path = self.recovery / name
            write(path, b"unsaved private text")
            self.assert_blocked_unchanged()
            path.unlink()
        self.assertTrue(M.guard_recovery_state(self.root)["clear"])
        (self.recovery / "unknown-dir").mkdir(mode=0o700)
        self.assert_blocked_unchanged()

    def test_keyfile_symlink_hardlink_and_permissions_fail_closed(self):
        write(self.keyfile, b"[org/xfce/mousepad/state/application]\nsession=[]\n")
        self.keyfile.chmod(0o644)
        self.assert_blocked_unchanged()
        self.keyfile.chmod(0o600)
        link = self.root / "second-link"
        os.link(self.keyfile, link)
        self.assert_blocked_unchanged()
        link.unlink()
        self.keyfile.unlink()
        self.keyfile.symlink_to(self.document)
        self.assert_blocked_unchanged()

    def test_intermediate_and_profile_root_symlinks_fail_closed(self):
        (self.root / "config").symlink_to(self.root / "Documents", target_is_directory=True)
        self.assert_blocked_unchanged()
        (self.root / "config").unlink()
        link = Path(self.temp.name) / "profile-link"
        link.symlink_to(self.root, target_is_directory=True)
        self.assertFalse(M.guard_recovery_state(link)["clear"])
        self.assertFalse(M.guard_recovery_state(self.root / "missing")["clear"])

    def test_wrong_owner_and_insecure_private_directory_fail_closed(self):
        self.assertFalse(M.guard_recovery_state(self.root, uid=os.getuid() + 1)["clear"])
        private_dirs(self.keyfile.parent)
        (self.root / "config").chmod(0o755)
        self.assert_blocked_unchanged()

    def test_bounded_size_and_special_file_never_block(self):
        write(self.keyfile, b"#" * (M.MAX_KEYFILE_BYTES + 1))
        self.assert_blocked_unchanged()
        self.keyfile.unlink()
        os.mkfifo(self.keyfile, 0o600)
        self.assertFalse(M.guard_recovery_state(self.root)["clear"])

    def test_recovery_entry_scan_has_a_bound(self):
        private_dirs(self.recovery)
        for index in range(M.MAX_RECOVERY_ENTRIES + 1):
            write(self.recovery / f"autosave-{index}", b"saved text")
        self.assert_blocked_unchanged()

    def test_file_replacement_during_inspection_is_detected_before_spawn(self):
        write(self.keyfile, b"[org/xfce/mousepad/state/application]\nsession=[]\n")
        original = M._empty_session
        def replace_after_read(data):
            self.keyfile.write_bytes(b"[org/xfce/mousepad/state/application]\nsession=['new recovery']\n")
            return original(data)
        with mock.patch.object(M, "_empty_session", side_effect=replace_after_read):
            self.assertFalse(M.guard_recovery_state(self.root)["clear"])
        self.assertIn(b"new recovery", self.keyfile.read_bytes())

    def test_nested_directory_replacement_during_scan_is_detected(self):
        write(self.keyfile, b"[org/xfce/mousepad/state/application]\nsession=[]\n")
        original = M._empty_session
        def replace_after_read(data):
            self.keyfile.parent.rename(self.keyfile.parent.with_name("old-settings"))
            write(self.keyfile, b"[org/xfce/mousepad/state/application]\nsession=['saved']\n")
            return original(data)
        with mock.patch.object(M, "_empty_session", side_effect=replace_after_read):
            self.assertFalse(M.guard_recovery_state(self.root)["clear"])


class ReceiptContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_receipt_budget_matches_frozen_helper_off_by_one_boundary(self):
        empty = M.inspect_receipt_capacity(self.home)
        self.assertTrue(empty["checked"])
        self.assertEqual(empty["capacity_remaining"], 81)
        root = self.home / ".eidolon-desktop"
        root.mkdir(mode=0o700)
        for index in range(80):
            write(root / f"r-{index:016x}.json", b"{}\n")
        before = {p.name: p.read_bytes() for p in root.iterdir()}
        eighty = M.inspect_receipt_capacity(self.home)
        self.assertEqual((eighty["reason"], eighty["entries"], eighty["receipts"], eighty["capacity_remaining"]), ("NONE", 80, 80, 1))
        write(root / f"r-{80:016x}.json", b"{}\n")
        full = M.inspect_receipt_capacity(self.home)
        self.assertEqual((full["reason"], full["capacity_remaining"]), ("STATE_LIMIT", 0))
        self.assertFalse(full["scan_complete"])
        for name, data in before.items():
            self.assertEqual((root / name).read_bytes(), data)

    def test_session_cap_unknown_entries_and_symlinks_are_not_empty_capacity(self):
        root = self.home / ".eidolon-desktop"
        root.mkdir(mode=0o700)
        for index in range(8):
            (root / f"s-{index:016x}").mkdir(mode=0o700)
        self.assertEqual(M.inspect_receipt_capacity(self.home)["reason"], "STATE_LIMIT")
        unknown = root / "unknown"
        write(unknown, b"do not remove")
        self.assertEqual(M.inspect_receipt_capacity(self.home)["reason"], "APP_STATE_INVALID")
        unknown.unlink()
        unknown.symlink_to(self.home)
        self.assertEqual(M.inspect_receipt_capacity(self.home)["reason"], "APP_STATE_INVALID")

    def test_manifest_has_fixed_policies_and_no_free_command_or_unknown_window_id(self):
        value = {"schema": 1, "app_id": M.APP_ID, "package": "mousepad", "package_version": "0.6.3-test", "architecture": "amd64",
                 "executable": M.EXECUTABLE, "executable_sha256": "a" * 64, "dependency_fingerprint": "b" * 64,
                 "settings_kind": M.SETTINGS_KIND, "settings_policy": M.SETTINGS_POLICY, "settings_module_sha256": "c" * 64,
                 "settings_relative_path": M.SETTINGS_RELATIVE_PATH, "window_app_ids": ["org.xfce.mousepad"],
                 "launch_policy": "mousepad-private-bus-v1", "state_policy": "persistent-keyfile-locked-v1"}
        self.assertIs(M.validate_manifest(value), value)
        for change in ({"schema": True}, {"argv": ["--help"]}, {"window_app_ids": []}, {"settings_kind": "keyfile"},
                       {"window_app_ids": ["unknown"]}, {"settings_relative_path": "../config"}):
            with self.subTest(change=change), self.assertRaises(M.InspectionError):
                M.validate_manifest(dict(value, **change))

    def test_receipt_no_false_availability_or_unknown_fields(self):
        value = M.inspect_host(system_root=self.home, home=self.home)
        M.validate_inspection_receipt(value)
        for change in ({"available": True}, {"runtime_verified": True}, {"package": "private package"},
                       {"schema": True}, {"inspection_passed": True}, {"reason": "free diagnostic"},
                       {"reason": "NONE"}, {"reason": []}):
            with self.subTest(change=change), self.assertRaises(M.InspectionError):
                M.validate_inspection_receipt(dict(value, **change))

    def test_receipt_rejects_contradictory_or_unbounded_capacity_fields(self):
        value = M.inspect_host(system_root=self.home, home=self.home)
        for change in ({"reason": []}, {"entries": 0, "receipts": 1}, {"sessions": 8},
                       {"capacity_remaining": 99}, {"checked": False}, {"scan_complete": False}):
            damaged = copy.deepcopy(value)
            damaged["receipt_capacity"].update(change)
            with self.subTest(change=change), self.assertRaises(M.InspectionError):
                M.validate_inspection_receipt(damaged)

    def test_artifact_is_private_new_and_cannot_follow_or_replace(self):
        receipt = M.inspect_host(system_root=self.home)
        directory = self.home / "artifacts"
        M.write_inspection_receipt(directory, receipt)
        path = directory / "inspection.json"
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(path.read_bytes()), receipt)
        with self.assertRaises(FileExistsError):
            M.write_inspection_receipt(directory, receipt)
        directory_link = self.home / "link"
        directory_link.symlink_to(directory, target_is_directory=True)
        with self.assertRaises(OSError):
            M.write_inspection_receipt(directory_link, receipt)
        (self.home / "insecure").mkdir(mode=0o755)
        with self.assertRaises(M.InspectionError):
            M.write_inspection_receipt(self.home / "insecure", receipt)


if __name__ == "__main__":
    unittest.main()
