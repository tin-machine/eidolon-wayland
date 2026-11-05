"""EIDOLON_TEST_APKを指定すると、実APKを破損させて静的検査の拒否を確認する。"""
import copy
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).parents[1]))
loader = importlib.machinery.SourceFileLoader("package_check", str(Path(__file__).parents[1] / "check-native-package"))
spec = importlib.util.spec_from_loader(loader.name, loader)
checker = importlib.util.module_from_spec(spec)
loader.exec_module(checker)


@unittest.skipUnless(os.environ.get("EIDOLON_TEST_APK"), "実APKはEIDOLON_TEST_APKで明示する")
class PackageTests(unittest.TestCase):
    def reject(self, variant, message):
        lib = "lib/arm64-v8a/libeidolon_native.so"
        with tempfile.TemporaryDirectory(prefix="eidolon-apk-check-") as folder:
            apk = Path(folder) / "fixture.apk"
            with zipfile.ZipFile(os.environ["EIDOLON_TEST_APK"]) as source, zipfile.ZipFile(apk, "w") as out:
                for item in source.infolist():
                    if item.filename == "lib/arm64-v8a/libwaypipe_bin.so" and variant == "waypipe-absent":
                        continue
                    data = source.read(item)
                    if item.filename == lib and variant == "corrupt":
                        data += b"corrupt fixture"
                    if item.filename == "assets/native-build.json" and variant == "api":
                        data = data.replace(b'"api": 34', b'"api": 35')
                    if item.filename == "assets/keymaps/jis.xkb" and variant == "keymap":
                        data += b"invalid fixture"
                    if item.filename == "lib/arm64-v8a/libwaypipe_bin.so" and variant == "waypipe-corrupt":
                        data += b"invalid fixture"
                    # writestrはZipInfoを更新するので、元archiveのlookupを壊さない。
                    out.writestr(copy.copy(item), data)
                if variant == "duplicate":
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", UserWarning)
                        out.writestr(lib, source.read(lib))
            with self.assertRaisesRegex(ValueError, message):
                checker.inspect(apk)

    def test_corrupt_library_is_rejected(self):
        self.reject("corrupt", "hash")

    def test_duplicate_library_is_rejected(self):
        self.reject("duplicate", "重複")

    def test_incorrect_api_is_rejected(self):
        self.reject("api", "API")

    def test_corrupt_keymap_is_rejected(self):
        self.reject("keymap", "keymap hash")

    def test_corrupt_waypipe_is_rejected(self):
        self.reject("waypipe-corrupt", "Waypipe.*hash")

    def test_absent_waypipe_is_rejected(self):
        self.reject("waypipe-absent", "Waypipe.*数")
