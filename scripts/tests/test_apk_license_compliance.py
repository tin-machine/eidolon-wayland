import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import apk_license_compliance as compliance


def theme():
    return {
        "schema": 1,
        "packages": [{
            "name": "cssparser", "version": "0.38.0", "license": "MPL-2.0",
            "archive_sha256": "a" * 64,
            "source": "https://static.crates.io/crates/cssparser/cssparser-0.38.0.crate",
            "modified": False,
            "licenses": {"licenses/theme/cssparser-0.38.0/LICENSE": "b" * 64},
        }],
    }


def runtime(modules=None):
    modules = modules or [
        {"group": "androidx.core", "name": "core-ktx", "version": "1.13.1"},
        *[{"group": "com.meta.spatial", "name": name, "version": "0.15.0"}
          for name in sorted(compliance.META_MODULES)],
    ]
    return compliance.canonical({"schema": 1, "variant": "debug", "modules": modules})


def archive(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as writer:
        for name, content in entries.items():
            writer.writestr(name, content)
    stream.seek(0)
    return zipfile.ZipFile(stream)


class APKComplianceTests(unittest.TestCase):
    def prepare(self):
        with tempfile.TemporaryDirectory() as tmp:
            metadata = compliance.write(Path(tmp), theme())
            files = {compliance.ASSET_PREFIX + name: data
                     for name, data in compliance.assets(theme())[0].items()}
            files[compliance.ANDROID_MANIFEST] = runtime()
            return metadata, files

    def test_first_party_license_and_mpl_offer_are_bundled(self):
        metadata, files = self.prepare()
        with archive(files) as apk:
            self.assertEqual(compliance.validate(apk, metadata, theme()), 1)
            self.assertEqual(compliance.validate_android(apk, "debug"), 5)
            body = apk.read(compliance.ASSET_PREFIX + "MPL-SOURCES.md").decode()
            self.assertIn("cssparser-0.38.0.crate", body)
            self.assertIn("Archive SHA-256", body)
            self.assertIn("Copyright (c) 2026 tin-machine",
                          apk.read(compliance.ASSET_PREFIX + "LICENSE-MIT.txt").decode())

    def test_reject_modified_mpl_source(self):
        bad = theme()
        bad["packages"][0]["modified"] = True
        with self.assertRaisesRegex(ValueError, "MPL source unavailable or modified"):
            compliance.assets(bad)

    def test_reject_missing_license_from_apk(self):
        metadata, files = self.prepare()
        del files[compliance.ASSET_PREFIX + "LICENSE-MIT.txt"]
        with archive(files) as apk, self.assertRaisesRegex(ValueError, "APK license assets"):
            compliance.validate(apk, metadata, theme())

    def test_reject_changed_license_text(self):
        metadata, files = self.prepare()
        files[compliance.ASSET_PREFIX + "Apache-2.0.txt"] = b"corrupted"
        with archive(files) as apk, self.assertRaisesRegex(ValueError, "altered"):
            compliance.validate(apk, metadata, theme())

    def test_reject_unknown_android_dependency(self):
        metadata, files = self.prepare()
        entries = json.loads(runtime())
        entries["modules"].append({"group": "example.unknown", "name": "rogue", "version": "1.0"})
        entries["modules"].sort(key=lambda x: f"{x['group']}:{x['name']}:{x['version']}")
        files[compliance.ANDROID_MANIFEST] = compliance.canonical(entries)
        with archive(files) as apk, self.assertRaisesRegex(ValueError, "requires review"):
            compliance.validate_android(apk, "debug")

    def test_reject_missing_meta_module(self):
        metadata, files = self.prepare()
        entries = json.loads(runtime())
        entries["modules"] = [m for m in entries["modules"] if m["name"] != "meta-spatial-sdk-vr"]
        files[compliance.ANDROID_MANIFEST] = compliance.canonical(entries)
        with archive(files) as apk, self.assertRaisesRegex(ValueError, "Meta Spatial SDK"):
            compliance.validate_android(apk, "debug")

    def test_reject_wrong_variant_or_duplicate(self):
        metadata, files = self.prepare()
        with archive(files) as apk, self.assertRaisesRegex(ValueError, "variant"):
            compliance.validate_android(apk, "release")
        entries = json.loads(runtime())
        entries["modules"].append(entries["modules"][-1])
        files[compliance.ANDROID_MANIFEST] = compliance.canonical(entries)
        with archive(files) as apk, self.assertRaisesRegex(ValueError, "unique and sorted"):
            compliance.validate_android(apk, "debug")


if __name__ == "__main__":
    unittest.main()
