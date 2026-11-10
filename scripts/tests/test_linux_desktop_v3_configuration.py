"""Shared local configuration vectors and static exporter boundaries."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from linux_desktop_v3_configuration import ConnectionConfig
import linux_desktop_v3_build as build

FIXTURE = json.loads((Path(__file__).parent / "fixtures/linux-desktop-v3-configuration.json").read_bytes())


def raw(case):
    data = bytes.fromhex(case["raw_hex"]) if "raw_hex" in case else case["raw"].encode()
    return data.ljust(case.get("pad_to_bytes", len(data)), b" ")


class ConfigurationTests(unittest.TestCase):
    def test_shared_static_vectors_and_digest(self):
        for case in FIXTURE["valid"]:
            with self.subTest(case=case["id"]):
                config = ConnectionConfig.parse(raw(case))
                self.assertEqual(config.canonical(), case["canonical_utf8"].encode())
                self.assertEqual(config.sha256(), case["sha256"])
                self.assertEqual(ConnectionConfig.capture(config.pins(), config.manifest()), config)
                self.assertEqual(repr(config), "ConnectionConfig(<private>)")

    def test_raw_rejections_and_constructor(self):
        for case in FIXTURE["invalid"]:
            with self.subTest(case=case["id"]):
                for parse in (ConnectionConfig.parse, ConnectionConfig):
                    with self.assertRaises(ValueError):
                        parse(raw(case))

    def test_export_uses_verified_snapshot_without_running_any_application(self):
        config = ConnectionConfig.parse(raw(FIXTURE["valid"][0]))
        verified = mock.Mock(pins=config.pins(), manifest=config.manifest())
        with mock.patch.object(build, "verified_export", return_value=verified) as export, \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("export spawned app")):
            self.assertEqual(build.print_config("a" * 64), config.record())
            self.assertEqual(build.print_pins("a" * 64), config.pins().record())
            self.assertEqual(export.call_count, 2)

    def test_cli_requires_an_explicit_immutable_build_id(self):
        cli = ROOT / "scripts/build-linux-desktop-v3-bundle"
        for arguments in (["--print-config"], ["--print-config", "--print-pins"],
                          ["--print-config", "--build-id", "a" * 64, "--reviewed-package", "not-read"]):
            result = subprocess.run([sys.executable, str(cli), *arguments], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
