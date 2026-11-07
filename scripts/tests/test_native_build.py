"""古い成果物の再利用と、Gitなしsourceの版識別を検査する。"""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("native_build", Path(__file__).parents[1] / "native_build.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="eidolon-build-test-")
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        (self.root / "Cargo.toml").write_text('[workspace.package]\nversion = "0.1.0"\n')
        (self.root / "android/app").mkdir(parents=True)
        (self.root / "android/app/build.gradle.kts").write_text("// fixture\n")
        (self.root / "native/model/src").mkdir(parents=True)
        self.source = self.root / "native/model/src/lib.rs"
        self.source.write_text("pub const VERSION: &str = \"one\";\n")
        self.environment = patch.dict(os.environ, {"EIDOLON_SOURCE_COMMIT": "a" * 40, "EIDOLON_SOURCE_DIRTY": "true"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def info(self, version=3, profile="debug"):
        return native.identity(version, profile, root=self.root)

    def test_source_change_while_already_dirty_changes_digest(self):
        first = self.info()
        self.source.write_text("pub const VERSION: &str = \"two\";\n")
        self.assertNotEqual(first["source_digest"], self.info()["source_digest"])
        self.assertEqual(first["source_dirty"], self.info()["source_dirty"])

    def test_cache_and_generated_artifacts_do_not_change_identity(self):
        first = self.info()
        cache = self.root / "native/build/cache"
        cache.mkdir(parents=True)
        (cache / "output.so").write_bytes(b"fixture")
        self.assertEqual(first, self.info())
        self.assertNotEqual(native.canonical(first), native.canonical(self.info(version=4)))
        self.assertNotEqual(native.canonical(first), native.canonical(self.info(profile="release")))

    def test_embedded_linux_helper_changes_build_identity(self):
        helper = self.root / "scripts/linux/eidolon-linux-session.py"
        helper.parent.mkdir(parents=True)
        helper.write_text("# fixed Linux helper version one\n")
        first = self.info()
        self.assertIn(helper, native.source_files(self.root))
        helper.write_text("# fixed Linux helper version two\n")
        self.assertNotEqual(first["source_digest"], self.info()["source_digest"])

    def test_gitless_archive_accepts_explicit_identity_and_rejects_bad_input(self):
        with patch.dict(os.environ, {"EIDOLON_SOURCE_DIGEST": "b" * 64, "EIDOLON_SOURCE_DIRTY": "false"}):
            first = self.info()
            self.assertEqual(first["source_digest"], "b" * 64)
            self.assertFalse(first["source_dirty"])
            self.assertEqual(json.loads(native.canonical(first)), first)
        with patch.dict(os.environ, {"EIDOLON_SOURCE_DIGEST": "bad"}):
            with self.assertRaises(ValueError):
                self.info()
        for invalid in [0, -1, 2_100_000_001]:
            with self.assertRaises(ValueError):
                self.info(version=invalid)


if __name__ == "__main__":
    unittest.main()
