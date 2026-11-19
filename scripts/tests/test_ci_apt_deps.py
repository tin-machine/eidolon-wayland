"""Host-only tests for CI APT provisioning: no sudo, apt or network calls."""

import base64
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ci"))
import install_apt_deps as ci_apt  # noqa: E402


# Current upstream runner-images configure-apt-sources.sh emits this 152 B AWS
# configuration. This is a source-backed fixture, not a reconstructed CI receipt.
RUNNER_MIRRORS = (
    b"http://us-east-2.ec2.archive.ubuntu.com/ubuntu/\tpriority:1\n"
    b"https://archive.ubuntu.com/ubuntu/\tpriority:2\n"
    b"https://security.ubuntu.com/ubuntu/\tpriority:3\n"
)


class DownloadHarness:
    def __init__(self, downloads, *, install=(0, 12), prefer_error=0, restore_error=0):
        self.plan = ci_apt.MirrorFallback(
            ci_apt.RUNNER_MIRROR_PATH, RUNNER_MIRRORS,
            ci_apt.fallback_mirror_bytes(RUNNER_MIRRORS))
        self.downloads = list(downloads)
        self.install = install
        self.prefer_error = prefer_error
        self.restore_error = restore_error
        self.now = 1000.0
        self.events = []
        self.mirrors = RUNNER_MIRRORS

    def run(self, argv, **kwargs):
        seconds = int(argv[3][:-1])
        if "--_replace-mirrorlist" in argv:
            request = json.loads(kwargs["input"])
            replacement = base64.b64decode(request["replacement_b64"])
            assert request["expected_sha256"] == hashlib.sha256(self.mirrors).hexdigest()
            phase = "restore" if replacement == RUNNER_MIRRORS else "prefer"
            self.events.append((phase, seconds, argv))
            # A failed mutation can already have replaced the mirror list.
            self.mirrors = replacement
            self.now += 0.2
            return SimpleNamespace(returncode=(self.restore_error if phase == "restore"
                                               else self.prefer_error))
        phase = "download" if "--download-only" in argv else "install_cached"
        self.events.append((phase, seconds, argv))
        if phase == "install_cached":
            assert "--no-download" in argv
            assert self.mirrors == RUNNER_MIRRORS
            code, elapsed = self.install
        else:
            code, elapsed = self.downloads.pop(0)
        self.now += elapsed
        return SimpleNamespace(returncode=code)

    def execute(self):
        ci_apt.install_with_mirror_fallback(
            ["libgtk-3-dev", "cmake"], self.plan,
            run=self.run, clock=lambda: self.now)

    @property
    def phases(self):
        return [event[0] for event in self.events]


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

    def test_unrelated_third_party_dns_cannot_mask_ubuntu_outage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = root / "sources.list.d"
            sources.mkdir()
            (sources / "microsoft-prod.list").write_text(
                "deb https://packages.microsoft.com/ubuntu/24.04/prod noble main\n"
            )
            (sources / "vendor.list").write_text(
                "deb https://packages.example.org/apt noble main\n"
            )
            (sources / "ubuntu.sources").write_text(
                "Types: deb\nURIs: mirror+file:/etc/apt/apt-mirrors.txt https://security.ubuntu.com/ubuntu\n"
            )
            (root / "apt-mirrors.txt").write_text(
                "http://azure.archive.ubuntu.com/ubuntu\n"
            )
            self.assertEqual(ci_apt.mirror_hosts(root),
                             ["azure.archive.ubuntu.com", "security.ubuntu.com"])

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

    def test_fallback_reprioritizes_only_existing_official_uris(self):
        self.assertEqual(len(RUNNER_MIRRORS), 152)
        fallback = ci_apt.fallback_mirror_bytes(RUNNER_MIRRORS)
        self.assertEqual(fallback, (
            b"http://us-east-2.ec2.archive.ubuntu.com/ubuntu/\tpriority:1\n"
            b"https://archive.ubuntu.com/ubuntu/\tpriority:0\n"
            b"https://security.ubuntu.com/ubuntu/\tpriority:3\n"
        ))
        self.assertEqual({u for u, _ in ci_apt._mirror_lines(fallback)},
                         {u for u, _ in ci_apt._mirror_lines(RUNNER_MIRRORS)})

    def test_unsupported_or_unsafe_mirror_lists_keep_original_path(self):
        for data in [
            RUNNER_MIRRORS.replace(b"ec2.archive.ubuntu.com", b"evil.example.org"),
            RUNNER_MIRRORS.replace(b"https://archive", b"https://user:secret@archive"),
            RUNNER_MIRRORS.replace(b"https://archive", b"ftp://archive"),
            RUNNER_MIRRORS.replace(b"/ubuntu/\tpriority:2", b"/ubuntu/?token=secret\tpriority:2"),
            RUNNER_MIRRORS.replace(b"/ubuntu/\tpriority:2", b"/ubuntu/#fragment\tpriority:2"),
            RUNNER_MIRRORS.replace(b"priority:2", b"priority:2 arch:amd64"),
            RUNNER_MIRRORS + b"https://archive.ubuntu.com/ubuntu/\tpriority:4\n",
            RUNNER_MIRRORS.replace(b"priority:2", b"priority:-1"),
            RUNNER_MIRRORS.replace(b"archive.ubuntu.com/ubuntu/\tpriority:2", b"archive.ubuntu.com:443/ubuntu/\tpriority:2"),
            RUNNER_MIRRORS.replace(b"https://archive.ubuntu.com/ubuntu/\tpriority:2\n", b""),
            b"https://archive.ubuntu.com/ubuntu/\n",
            b"x" * (ci_apt.MIRROR_LIST_LIMIT + 1),
            RUNNER_MIRRORS + b"\x00\n",
        ]:
            with self.subTest(data=data[:80]):
                self.assertIsNone(ci_apt.fallback_mirror_bytes(data))

    def test_unreferenced_mirror_list_is_not_mutated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "apt-mirrors.txt").write_bytes(RUNNER_MIRRORS)
            (root / "sources.list").write_text("deb https://archive.ubuntu.com/ubuntu noble main\n")
            with mock.patch.object(ci_apt, "_read_mirror_file") as reader:
                self.assertIsNone(ci_apt.configured_mirror_fallback(root))
                reader.assert_not_called()

    def test_configured_mirror_discovery_requires_exact_file_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sources.list.d").mkdir()
            source = root / "sources.list.d/ubuntu.sources"
            for suffix, expected in [(".foreign", False), ("", True)]:
                source.write_text(f"URIs: mirror+file:{root}/apt-mirrors.txt{suffix}\n")
                with mock.patch.object(ci_apt, "_read_mirror_file", return_value=(RUNNER_MIRRORS, None)):
                    plan = ci_apt.configured_mirror_fallback(root)
                self.assertEqual(plan is not None, expected)

    def test_mirror_file_read_ignores_atime_but_checks_ns_content_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mirrors"
            path.write_bytes(RUNNER_MIRRORS)
            before = path.stat()
            original = SimpleNamespace(**{k: getattr(before, k) for k in (
                "st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size",
                "st_mtime_ns", "st_ctime_ns", "st_atime_ns")})
            after = SimpleNamespace(**vars(original))
            after.st_atime_ns += 1
            with mock.patch.object(ci_apt.os, "fstat", side_effect=[original, after]):
                data, _ = ci_apt._read_mirror_file(path, owner_uid=os.getuid())
                self.assertEqual(data, RUNNER_MIRRORS)
            after.st_mtime_ns += 1
            with mock.patch.object(ci_apt.os, "fstat", side_effect=[original, after]):
                with self.assertRaisesRegex(ci_apt.AptSetupError, "changed while reading"):
                    ci_apt._read_mirror_file(path, owner_uid=os.getuid())

    def test_mirror_file_refuses_links_owner_and_writable_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mirrors"
            path.write_bytes(RUNNER_MIRRORS)
            with self.assertRaises(ci_apt.AptSetupError):
                ci_apt._read_mirror_file(path, owner_uid=os.getuid() + 1)
            path.chmod(0o666)
            with self.assertRaises(ci_apt.AptSetupError):
                ci_apt._read_mirror_file(path, owner_uid=os.getuid())
            path.chmod(0o600)
            link = Path(tmp) / "link"
            link.symlink_to(path)
            with self.assertRaises(OSError):
                ci_apt._read_mirror_file(link, owner_uid=os.getuid())
            link.unlink()
            os.link(path, link)
            with self.assertRaises(ci_apt.AptSetupError):
                ci_apt._read_mirror_file(path, owner_uid=os.getuid())

    def test_real_atomic_priority_change_restores_exact_original_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mirrors"
            original = b"# runner-controlled\n" + RUNNER_MIRRORS
            path.write_bytes(original)
            fallback = ci_apt.fallback_mirror_bytes(original)
            ci_apt.replace_mirror_file(path, hashlib.sha256(original).hexdigest(), fallback,
                                      owner_uid=os.getuid())
            self.assertEqual(path.read_bytes(), fallback)
            ci_apt.replace_mirror_file(path, hashlib.sha256(fallback).hexdigest(), original,
                                      owner_uid=os.getuid())
            self.assertEqual(path.read_bytes(), original)
            # A failed preference change that left original bytes needs no rewrite.
            ci_apt.replace_mirror_file(path, hashlib.sha256(fallback).hexdigest(), original,
                                      owner_uid=os.getuid())

    def test_atomic_mutation_rejects_foreign_edit_or_new_mirror(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mirrors"
            path.write_bytes(RUNNER_MIRRORS)
            fallback = ci_apt.fallback_mirror_bytes(RUNNER_MIRRORS)
            for digest, contents in [
                ("0" * 64, fallback),
                (hashlib.sha256(RUNNER_MIRRORS).hexdigest(), fallback.replace(
                    b"us-east-2.ec2.archive.ubuntu.com", b"azure.archive.ubuntu.com")),
            ]:
                with self.assertRaises(ci_apt.AptSetupError):
                    ci_apt.replace_mirror_file(path, digest, contents, owner_uid=os.getuid())
                self.assertEqual(path.read_bytes(), RUNNER_MIRRORS)

    def test_download_success_does_not_change_mirrors_and_installs_cached_only(self):
        runner = DownloadHarness([(0, 8)])
        runner.execute()
        self.assertEqual(runner.phases, ["download", "install_cached"])
        self.assertEqual(runner.events[0][1], 120)
        self.assertLess(runner.events[1][1], ci_apt.APT_INSTALL_SECONDS)
        for _, _, argv in runner.events:
            self.assertEqual(argv[-2:], ["libgtk-3-dev", "cmake"])
            self.assertNotIn("--fix-missing", argv)
            self.assertNotIn("--allow-unauthenticated", argv)

    def test_timeout_fallback_uses_shared_budget_and_restores_before_dpkg(self):
        runner = DownloadHarness([(124, 120), (0, 10)])
        runner.execute()
        self.assertEqual(runner.phases, ["download", "prefer", "download", "restore", "install_cached"])
        self.assertLessEqual(runner.events[2][1], 64)
        self.assertLess(runner.events[-1][1], 110)
        self.assertEqual(runner.mirrors, RUNNER_MIRRORS)
        self.assertLess(runner.now - 1000, 240)

    def test_non_timeout_download_failure_never_retries_or_installs(self):
        for code in [100, 1, 137]:
            with self.subTest(code=code):
                runner = DownloadHarness([(code, 5)])
                with self.assertRaises(ci_apt.AptCommandError):
                    runner.execute()
                self.assertEqual(runner.phases, ["download"])

    def test_fallback_download_failure_restores_and_never_starts_dpkg(self):
        for code in [100, 124]:
            with self.subTest(code=code):
                runner = DownloadHarness([(124, 120), (code, 20)])
                with self.assertRaises(ci_apt.AptCommandError):
                    runner.execute()
                self.assertEqual(runner.phases, ["download", "prefer", "download", "restore"])
                self.assertEqual(runner.mirrors, RUNNER_MIRRORS)

    def test_exhausted_budget_prevents_fallback_and_dpkg(self):
        runner = DownloadHarness([(124, 239)])
        with self.assertRaisesRegex(ci_apt.AptSetupError, "overall deadline"):
            runner.execute()
        self.assertEqual(runner.phases, ["download"])

    def test_exhausted_budget_after_download_prevents_dpkg(self):
        runner = DownloadHarness([(0, 239)])
        with self.assertRaisesRegex(ci_apt.AptSetupError, "overall deadline"):
            runner.execute()
        self.assertEqual(runner.phases, ["download"])

    def test_failed_preference_change_is_still_restored(self):
        runner = DownloadHarness([(124, 120)], prefer_error=1)
        with self.assertRaisesRegex(ci_apt.AptSetupError, "reprioritize"):
            runner.execute()
        self.assertEqual(runner.phases, ["download", "prefer", "restore"])
        self.assertEqual(runner.mirrors, RUNNER_MIRRORS)

    def test_restore_failure_keeps_setup_failed_before_dpkg(self):
        runner = DownloadHarness([(124, 120), (0, 10)], restore_error=1)
        with self.assertRaisesRegex(ci_apt.AptSetupError, "restore"):
            runner.execute()
        self.assertNotIn("install_cached", runner.phases)

    def test_original_download_failure_is_retained_when_restore_also_fails(self):
        runner = DownloadHarness([(124, 120), (100, 5)], restore_error=1)
        with self.assertRaisesRegex(ci_apt.AptSetupError, "download failed.*Restoration also failed"):
            runner.execute()
        self.assertNotIn("install_cached", runner.phases)

    def test_cached_install_failure_is_not_retried(self):
        runner = DownloadHarness([(124, 120), (0, 10)], install=(100, 12))
        with self.assertRaises(ci_apt.AptCommandError):
            runner.execute()
        self.assertEqual(runner.phases.count("install_cached"), 1)
        self.assertEqual(runner.mirrors, RUNNER_MIRRORS)

    def test_privileged_mutation_cannot_target_an_arbitrary_file(self):
        plan = ci_apt.MirrorFallback(Path("/tmp/foreign"), RUNNER_MIRRORS,
                                    ci_apt.fallback_mirror_bytes(RUNNER_MIRRORS))
        with self.assertRaisesRegex(ci_apt.AptSetupError, "restricted"):
            ci_apt._mirror_control(plan, restore=False, run=lambda *a, **k: self.fail("called sudo"))


if __name__ == "__main__":
    unittest.main()
