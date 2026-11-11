"""Host-only tests for CI APT provisioning: no sudo, apt or network calls."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ci"))
import install_apt_deps as ci_apt  # noqa: E402


class AptDependencySetupTests(unittest.TestCase):
    def test_reads_runner_mirror_file_and_deb822_without_logging_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sources.list.d").mkdir()
            (root / "apt-mirrors.txt").write_text(
                "http://azure.archive.ubuntu.com/ubuntu\nhttps://archive.ubuntu.com/ubuntu\n"
            )
            (root / "sources.list.d" / "ubuntu.sources").write_text(
                "Types: deb\nURIs: mirror+file:/etc/apt/apt-mirrors.txt https://security.ubuntu.com/ubuntu\n"
            )
            self.assertEqual(ci_apt.mirror_hosts(root), [
                "azure.archive.ubuntu.com", "archive.ubuntu.com", "security.ubuntu.com"
            ])

    def test_dns_preflight_accepts_available_fallback_mirror(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv[-1])
            return SimpleNamespace(returncode=0 if argv[-1] == "archive.ubuntu.com" else 1)

        ci_apt.probe_dns(["azure.archive.ubuntu.com", "archive.ubuntu.com"], run=runner)
        self.assertEqual(calls, ["azure.archive.ubuntu.com", "archive.ubuntu.com"])

    def test_dns_outage_fails_before_any_apt_install(self):
        calls = []
        pauses = []

        def runner(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=1)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sources.list.d").mkdir()
            (root / "apt-mirrors.txt").write_text("http://azure.archive.ubuntu.com/ubuntu\n")
            with self.assertRaisesRegex(ci_apt.AptSetupError, "APT DNS preflight failed"):
                ci_apt.install(["libgtk-3-dev"], apt_root=root, run=runner, pause=pauses.append)
        self.assertEqual(len(calls), ci_apt.DNS_ATTEMPTS)
        self.assertTrue(all("getent" in c for c in calls))
        self.assertEqual(pauses, [2])

    def test_fail_closed_update_prevents_install(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=100)

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ci_apt.AptSetupError, "APT update failed"):
                ci_apt.install(["libgtk-3-dev"], apt_root=Path(tmp), run=runner)
        self.assertEqual(len(calls), 1)
        self.assertIn("APT::Update::Error-Mode=any", calls[0])
        self.assertNotIn("install", calls[0])

    def test_timeout_on_install_fails_and_never_marks_success(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=124 if "install" in argv else 0)

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ci_apt.AptSetupError, "240s deadline"):
                ci_apt.install(["libgtk-3-dev"], apt_root=Path(tmp), run=runner)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][3], "240s")

    def test_success_preserves_exact_package_names_and_update_before_install(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            ci_apt.install(["libgtk-3-dev", "xvfb"], apt_root=Path(tmp), run=runner)
        self.assertEqual(len(calls), 2)
        self.assertIn("APT::Update::Error-Mode=any", calls[0])
        self.assertEqual(calls[1][-4:], ["install", "-y", "libgtk-3-dev", "xvfb"])
        self.assertNotIn("--allow-unauthenticated", repr(calls))

    def test_rejects_arbitrary_apt_flags_and_blank_packages(self):
        for packages in [[], ["--fix-missing"], ["foo;touch /tmp/evil"], ["libgtk-3-dev", ""]]:
            with self.subTest(packages=packages), self.assertRaises(ci_apt.AptSetupError):
                ci_apt.install(packages, apt_root=Path("/tmp/irrelevant"), run=lambda *a, **kw: None)


if __name__ == "__main__":
    unittest.main()
