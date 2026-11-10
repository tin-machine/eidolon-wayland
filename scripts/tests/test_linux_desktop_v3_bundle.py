"""B2 source integration tests. Publication fixtures are not product bundles.

No empty/fake C helper is used to make product build/print-pins succeed. The
existing B1 manifest vector remains read-only; no golden value is regenerated.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import linux_desktop_v3_bundle as B
import linux_desktop_v3_build as D
from linux_desktop_v3_contract import ContractError, Manifest, Pins, canonical_json

RAW = (ROOT / "scripts" / B.INVENTORY_NAME).read_bytes()
SPEC = B.inventory(RAW)
GOLDEN = json.loads((Path(__file__).parent / "fixtures/linux-desktop-v3-contract.json").read_bytes())
RECORDS = {item["id"]: item for item in GOLDEN["records"]}


def pure_bundle_manifest():
    """A schema record only; no helper files, install, product pins or launch."""
    hashes = {name: "1" * 64 for name in SPEC["sources"] | dict.fromkeys(B.GENERATED)}
    hashes["app-manifest.json"] = RECORDS["manifest"]["sha256"]
    return {"schema": 1, "backend": "linux_desktop", "protocol_revision": B.REVISION,
            "base_bundle_sha256": "2" * 64, "app_manifest_sha256": RECORDS["manifest"]["sha256"], "files": hashes}


class SourceClosureTests(unittest.TestCase):
    def test_dependency_status_is_explicit_and_not_product_pins(self):
        status = B.source_status(ROOT, RAW)
        self.assertEqual(status["missing_sources"], [])
        for commit in status["integration"].values():
            self.assertRegex(commit, r"^[0-9a-f]{40}$")
        self.assertTrue(status["integration_complete"])
        self.assertTrue(all(status[k] is False for k in ("available", "production_acceptance_passed", "quest_verified")))
        self.assertNotIn("pins", status)

    def test_public_builder_and_export_refuse_before_tools_or_writes(self):
        incomplete = copy.deepcopy(SPEC)
        incomplete["integration"]["c2_commit"] = None
        with mock.patch.object(D, "_build_tools", side_effect=AssertionError("compiler or base helper imported")), \
             mock.patch.object(D, "account_home", side_effect=AssertionError("product filesystem touched")), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process spawned")), \
             mock.patch.object(os, "execve", side_effect=AssertionError("app executed")), \
             mock.patch.object(B, "read_regular", return_value=canonical_json(incomplete)):
            for call in (lambda: D.build(Path("/not-read.json")), lambda: D.print_pins("a" * 64),
                         lambda: D.print_config("a" * 64)):
                with self.assertRaisesRegex(B.BundleError, "BUNDLE_INTEGRATION_INCOMPLETE"):
                    call()
        with self.assertRaisesRegex(B.BundleError, "BUNDLE_INTEGRATION_INCOMPLETE"):
            B.require_integrated(incomplete)

    def test_complete_sources_still_require_final_integration_pins(self):
        self.assertEqual(set(B.source_snapshot(ROOT, RAW, require_complete=False)), B.REQUIRED_SOURCES)
        self.assertEqual(set(B.source_snapshot(ROOT, RAW)), B.REQUIRED_SOURCES)
        for key in ("c1_commit", "c2_commit"):
            incomplete = copy.deepcopy(SPEC)
            incomplete["integration"][key] = None
            with self.assertRaisesRegex(B.BundleError, "BUNDLE_INTEGRATION_INCOMPLETE"):
                B.source_snapshot(ROOT, canonical_json(incomplete))

    def test_known_real_source_graph_includes_transitive_b1_manifest(self):
        contents = {name: (ROOT / path).read_bytes() for name, path in SPEC["sources"].items()
                    if name.endswith(".py") and (ROOT / path).is_file()}
        edges = B.check_python_closure(contents)
        self.assertEqual(edges["linux_desktop_v3_contract.py"], ["linux_real_app_manifest.py"])
        self.assertEqual(edges["linux_real_app_inspect.py"], ["linux_real_app_manifest.py"])
        self.assertIn("linux_real_app_inspect.py", edges["linux_desktop_v3_bundle.py"])
        self.assertNotIn("linux_real_app_build.py", contents)
        self.assertNotIn("linux-real-app-gui-observer.c", SPEC["sources"])

    def test_missing_transitive_import_is_rejected_even_if_never_executed(self):
        code = b"if False:\n import missing_policy\n"
        with self.assertRaisesRegex(B.BundleError, "BUNDLE_IMPORT_UNPINNED"):
            B.check_python_closure({"caller.py": code})
        with self.assertRaisesRegex(B.BundleError, "BUNDLE_IMPORT_UNPINNED"):
            B.check_python_closure({"caller.py": b"from .policy import value\n"})

    def test_dynamic_import_and_path_aliases_are_rejected(self):
        cases = [b"__import__('policy')", b"import importlib; importlib.import_module('policy')",
                 b"from importlib import import_module as load\nf = load\nf('policy')",
                 b"import sys; sys.path.append('/tmp')", b"import sys as s; s.path.append('/tmp')",
                 b"from sys import path as p\np.append('/tmp')", b"import sys as s; getattr(s, 'path').append('/tmp')",
                 b"import builtins as b\nf = b.__import__\nf('policy')", b"from policy import *",
                 b"import importlib as il\nf=il.import_module\nf('unpinned')", b"f=__import__\nf('unpinned')",
                 b"__builtins__['__import__']('unpinned')",
                 b"import importlib\nget=getattr\nfn=get(importlib,'import_module')\nfn('unpinned')"]
        for code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(B.BundleError, "BUNDLE_IMPORT_UNPINNED"):
                B.check_python_closure({"caller.py": code})

    def test_fixed_base_alias_reuses_existing_bytes_without_dynamic_loader(self):
        data = (ROOT / SPEC["sources"]["eidolon-linux-session.py"]).read_bytes()
        edges = B.check_python_closure({"caller.py": b"import eidolon_linux_session as base\n", "eidolon-linux-session.py": data})
        self.assertEqual(edges["caller.py"], ["eidolon-linux-session.py"])

    def test_inspector_loader_exception_requires_exact_expression_and_dependency(self):
        source = (ROOT / "scripts/linux_real_app_inspect.py").read_bytes()
        manifest = (ROOT / "scripts/linux_real_app_manifest.py").read_bytes()
        for contents in ({"linux_real_app_inspect.py": source},
                         {"linux_real_app_inspect.py": source.replace(b'with_name("linux_real_app_manifest.py")', b'with_name("evil.py")'),
                          "linux_real_app_manifest.py": manifest},
                         {"another.py": source, "linux_real_app_manifest.py": manifest}):
            with self.assertRaisesRegex(B.BundleError, "BUNDLE_IMPORT_UNPINNED"):
                B.check_python_closure(contents)

    def test_real_source_modification_missing_and_extra_hash_reject(self):
        real = (ROOT / "scripts/linux_desktop_v3_contract.py").read_bytes()
        expected = {"linux_desktop_v3_contract.py": hashlib.sha256(real).hexdigest()}
        B.check_source_hashes({"linux_desktop_v3_contract.py": real}, expected)
        for contents in ({}, {"linux_desktop_v3_contract.py": real + b"\n"},
                         {"linux_desktop_v3_contract.py": real, "unexpected.py": b"# extra\n"}):
            with self.assertRaisesRegex(B.BundleError, "BUNDLE_SOURCE_CHANGED"):
                B.check_source_hashes(contents, expected)

    def test_inventory_unknown_missing_duplicate_and_wrong_types_reject(self):
        variants = []
        for key, value in (("schema", True), ("protocol_revision", "eidolon-linux-desktop-v2"), ("extra", 1)):
            record = copy.deepcopy(SPEC); record[key] = value; variants.append(canonical_json(record))
        record = copy.deepcopy(SPEC); record["sources"]["unknown.py"] = "scripts/unknown.py"; variants.append(canonical_json(record))
        record = copy.deepcopy(SPEC); del record["sources"]["linux_real_app_manifest.py"]; variants.append(canonical_json(record))
        record = copy.deepcopy(SPEC); record["generated"].append(record["generated"][0]); variants.append(canonical_json(record))
        record = copy.deepcopy(SPEC); record["integration"]["c2_commit"] = "wrong"; variants.append(canonical_json(record))
        record = copy.deepcopy(SPEC); record["sources"]["linux_real_app_manifest.py"] = "scripts/../evil.py"; variants.append(canonical_json(record))
        variants.append(RAW.replace(b'"schema": 1,', b'"schema": 1, "schema": 1,'))
        for raw in variants:
            with self.subTest(raw=raw[:80]), self.assertRaises(ValueError):
                B.inventory(raw)


class BundleSchemaTests(unittest.TestCase):
    def test_b1_manifest_and_hash_stay_the_literal_golden(self):
        value = Manifest.parse(RECORDS["manifest"]["raw"].encode())
        self.assertEqual(value.canonical(), RECORDS["manifest"]["canonical_utf8"].encode())
        self.assertEqual(value.sha256(), RECORDS["manifest"]["sha256"])

    def test_manifest_has_exact_files_and_no_self_referential_hash(self):
        record = pure_bundle_manifest()
        self.assertEqual(B.parse_bundle_manifest(canonical_json(record), SPEC), record)
        self.assertNotIn(B.MANIFEST_NAME, record["files"])
        self.assertNotIn("build_id", record)
        self.assertNotIn("desktop_bundle_sha256", record)

    def test_unknown_duplicate_extra_missing_fields_wrong_revision_and_types_reject(self):
        good = pure_bundle_manifest()
        variants = []
        for key, value in (("schema", True), ("protocol_revision", "eidolon-linux-desktop-v2"),
                           ("build_id", "a" * 64), ("desktop_bundle_sha256", "b" * 64),
                           ("base_bundle_sha256", "A" * 64)):
            record = copy.deepcopy(good); record[key] = value; variants.append(canonical_json(record))
        record = copy.deepcopy(good); del record["files"]["linux_real_app_manifest.py"]; variants.append(canonical_json(record))
        record = copy.deepcopy(good); record["files"]["unknown.py"] = "a" * 64; variants.append(canonical_json(record))
        record = copy.deepcopy(good); record["files"][B.MANIFEST_NAME] = "a" * 64; variants.append(canonical_json(record))
        record = copy.deepcopy(good); record["files"]["app-manifest.json"] = "a" * 64; variants.append(canonical_json(record))
        variants.extend([canonical_json(good) + b"\n", canonical_json(good).replace(b'"schema":1', b'"schema":1,"schema":1')])
        for raw in variants:
            with self.subTest(raw=raw[:80]), self.assertRaises(ValueError):
                B.parse_bundle_manifest(raw, SPEC)

    def test_recover_gate_cannot_succeed_with_missing_integration_or_touch_package(self):
        pins = Pins.parse(RECORDS["pins"]["raw"].encode())
        incomplete = copy.deepcopy(SPEC)
        incomplete["integration"]["c1_commit"] = None
        with mock.patch.object(B.inspector, "validate_reviewed_package", side_effect=AssertionError("current package inspected")), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("app spawned")):
            with self.assertRaisesRegex(B.BundleError, "BUNDLE_INTEGRATION_INCOMPLETE"):
                B.validate_bundle(Path("/not-read"), pins, {}, canonical_json(incomplete))


class ImmutablePublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.parent = Path(self.temp.name)
        self.parent.chmod(0o700)
        self.build_id = "a" * 64

    def tearDown(self):
        for path in sorted(self.parent.rglob("*"), key=lambda p: len(p.parts)):
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o700)
        self.temp.cleanup()

    def test_atomic_low_level_byte_publication_has_readonly_modes(self):
        # Generic bytes exercise only the publisher, never the product builder.
        target = D.publish_immutable(self.parent, self.build_id, {"payload.txt": b"published", "gio/policy.txt": b"fixed"})
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o500)
        self.assertEqual(stat.S_IMODE((target / "gio").stat().st_mode), 0o500)
        self.assertEqual(B.read_regular(target / "payload.txt", mode=0o400), b"published")
        self.assertEqual(set(self.parent.iterdir()), {target})

    def test_existing_same_build_id_is_never_overwritten_even_empty(self):
        target = self.parent / self.build_id
        target.mkdir(mode=0o700)
        with self.assertRaisesRegex(B.BundleError, "BUILD_ALREADY_EXISTS"):
            D.publish_immutable(self.parent, self.build_id, {"payload.txt": b"bytes"})
        self.assertEqual(list(target.iterdir()), [])

    def test_existing_other_build_is_unchanged(self):
        first = D.publish_immutable(self.parent, "b" * 64, {"payload.txt": b"old"})
        second = D.publish_immutable(self.parent, self.build_id, {"payload.txt": b"new"})
        self.assertEqual((first / "payload.txt").read_bytes(), b"old")
        self.assertEqual((second / "payload.txt").read_bytes(), b"new")

    def test_target_created_after_precheck_is_not_replaced(self):
        original = D._rename_noreplace
        def race(fd, source, target):
            os.mkdir(target, 0o700, dir_fd=fd)
            original(fd, source, target)
        with mock.patch.object(D, "_rename_noreplace", race), self.assertRaisesRegex(B.BundleError, "BUILD_ALREADY_EXISTS"):
            D.publish_immutable(self.parent, self.build_id, {"payload.txt": b"bytes"})
        self.assertEqual(set(p.name for p in self.parent.iterdir()), {self.build_id})
        self.assertEqual(list((self.parent / self.build_id).iterdir()), [])

    def test_unknown_files_and_directories_are_rejected(self):
        target = D.publish_immutable(self.parent, self.build_id, {"payload.txt": b"bytes"})
        B._directory_names(target, {"payload.txt"}, os.getuid())
        target.chmod(0o700); (target / "unknown.py").write_bytes(b"# unpinned\n"); target.chmod(0o500)
        with self.assertRaisesRegex(B.BundleError, "BUNDLE_FILES_INVALID"):
            B._directory_names(target, {"payload.txt"}, os.getuid())

    def test_symlink_hardlink_owner_mode_and_ancestor_symlink_refuse(self):
        real = self.parent / "real"; real.write_bytes(b"data"); real.chmod(0o400)
        link = self.parent / "link"; link.symlink_to(real)
        with self.assertRaises((OSError, B.BundleError)):
            B.read_regular(link)
        hard = self.parent / "hard"; os.link(real, hard)
        with self.assertRaisesRegex(B.BundleError, "BUNDLE_FILE_INVALID"):
            B.read_regular(real)
        hard.unlink()
        with self.assertRaisesRegex(B.BundleError, "BUNDLE_FILE_INVALID"):
            B.read_regular(real, mode=0o600)
        with self.assertRaisesRegex(B.BundleError, "BUNDLE_FILE_INVALID"):
            B.read_regular(real, uid=os.getuid() + 1)
        alias = self.parent / "alias"; alias.symlink_to(self.parent, target_is_directory=True)
        with self.assertRaises((OSError, B.BundleError)):
            B.read_regular(alias / "real")

    def test_traversal_absolute_paths_and_empty_bytes_refuse_before_write(self):
        for contents in ({"../outside": b"x"}, {"/absolute": b"x"}, {"a/../b": b"x"}, {"a": b""}):
            with self.subTest(contents=contents), self.assertRaisesRegex(B.BundleError, "BUILD_INPUT_INVALID"):
                D.publish_immutable(self.parent, self.build_id, contents)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_source_and_manifest_caps_apply_before_reading_payload(self):
        self.assertEqual(B.file_limit("linux_desktop_v3_contract.py", SPEC), 256 * 1024)
        self.assertEqual(B.file_limit(B.MANIFEST_NAME, SPEC), 64 * 1024)
        self.assertEqual(B.file_limit("app-manifest.json", SPEC), 16384)
        self.assertEqual(B.file_limit("reviewed-package.json", SPEC), 128 * 1024)
        self.assertEqual(B.file_limit("eidolon-gtk3-desktop", SPEC), 4 * 1024 * 1024)
        for name in ("linux_desktop_v3_contract.py", B.MANIFEST_NAME, "app-manifest.json"):
            path = self.parent / name
            path.write_bytes(b"x" * (B.file_limit(name, SPEC) + 1)); path.chmod(0o400)
            with self.subTest(name=name), self.assertRaisesRegex(B.BundleError, "BUNDLE_FILE_INVALID"):
                B.read_regular(path, mode=0o400, limit=B.file_limit(name, SPEC))


if __name__ == "__main__":
    unittest.main()
