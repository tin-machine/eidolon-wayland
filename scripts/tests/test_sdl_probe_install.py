import base64
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sdl_probe_install import (InstallError, MAX_PAYLOAD, MAX_SOURCE, SOURCE_PATHS,
                               canonical, decode_bundle, install_bundle, prepare_bundle, sha256, termux_home)


class SdlInstallTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="eidolon-sdl-install-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repository = self.root / "repository"
        for name, relative in SOURCE_PATHS.items():
            p = self.repository / relative
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(("source " + name + "\n").encode())
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.payload, self.manifest = prepare_bundle(self.repository, "a" * 32, "b" * 40, False)

    def deploy(self, payload=None, home=None, expected_hash=None, uid=None):
        data = self.payload if payload is None else payload
        return install_bundle(data, expected_hash or sha256(data), home or self.home, os.getuid() if uid is None else uid)

    def edited(self, edit):
        value = json.loads(self.payload)
        edit(value)
        return canonical(value)

    def test_new_run_contains_exact_sources_and_manifest_with_private_modes(self):
        unrelated = self.home / "unrelated"
        unrelated.write_bytes(b"keep")
        report = self.deploy()
        run = Path(report["directory"])
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["uid"], os.getuid())
        self.assertEqual(set(p.name for p in run.iterdir()), set(SOURCE_PATHS) | {"manifest.json"})
        for name, relative in SOURCE_PATHS.items():
            self.assertEqual((run / name).read_bytes(), (self.repository / relative).read_bytes())
            self.assertEqual(stat.S_IMODE((run / name).stat().st_mode), 0o600)
            self.assertEqual((run / name).stat().st_uid, os.getuid())
        self.assertEqual(json.loads((run / "manifest.json").read_bytes()), self.manifest)
        self.assertEqual(report["manifest_sha256"], sha256((run / "manifest.json").read_bytes()))
        self.assertEqual(stat.S_IMODE((run / "manifest.json").stat().st_mode), 0o600)
        for directory in [run, *list(run.parents)[:4]]:
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(unrelated.read_bytes(), b"keep")

    def test_existing_run_is_never_replaced_even_with_another_valid_bundle(self):
        report = self.deploy()
        source = self.repository / SOURCE_PATHS["sdl-wayland-probe.c"]
        source.write_bytes(b"different source")
        payload, _ = prepare_bundle(self.repository, "a" * 32, "b" * 40, True)
        before = (Path(report["directory"]) / "sdl-wayland-probe.c").read_bytes()
        with self.assertRaisesRegex(InstallError, "RUN_ALREADY_EXISTS"):
            self.deploy(payload)
        self.assertEqual((Path(report["directory"]) / "sdl-wayland-probe.c").read_bytes(), before)

    def test_invalid_payloads_fail_before_creating_directories(self):
        variants = [
            self.edited(lambda x: x["manifest"].update(run_id="../other")),
            self.edited(lambda x: x["files"].update({"../extra": ""})),
            self.edited(lambda x: x["manifest"]["files"]["sdl-wayland-probe.c"].update(bytes=True)),
            self.edited(lambda x: x["manifest"].update(schema=True)),
            self.edited(lambda x: x["manifest"].update(source_dirty=0)),
            self.edited(lambda x: x["files"].update({"sdl-wayland-probe.c": "not base64"})),
            self.edited(lambda x: x["manifest"]["files"]["sdl-wayland-probe.c"].update(sha256="0" * 64)),
            b'{"manifest":{},"manifest":{},"files":{}}',
            b"[]", b"\xff", b"x" * (MAX_PAYLOAD + 1),
        ]
        for payload in variants:
            with self.subTest(payload=payload[:40]), self.assertRaises((InstallError, ValueError)):
                self.deploy(payload)
            self.assertFalse((self.home / ".local").exists())
        with self.assertRaisesRegex(InstallError, "PAYLOAD_HASH_MISMATCH"):
            self.deploy(expected_hash="0" * 64)
        self.assertFalse((self.home / ".local").exists())

    def test_declared_size_and_decoded_size_are_bounded(self):
        payload = self.edited(lambda x: x["manifest"]["files"]["sdl-wayland-probe.c"].update(bytes=MAX_SOURCE + 1))
        with self.assertRaisesRegex(InstallError, "SOURCE_METADATA_INVALID"):
            self.deploy(payload)
        payload = self.edited(lambda x: x["files"].update({"sdl-wayland-probe.c": base64.b64encode(b"x" * (MAX_SOURCE + 1)).decode()}))
        with self.assertRaisesRegex(InstallError, "SOURCE_SIZE_INVALID"):
            self.deploy(payload)

    def test_symlink_home_or_each_parent_does_not_touch_outside(self):
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        link = self.root / "linked-home"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            self.deploy(home=link)
        self.assertEqual(list(outside.iterdir()), [])
        for index in range(4):
            home = self.root / f"home-{index}"
            home.mkdir(mode=0o700)
            parent = home
            for part in (".local", "state", "eidolon", "nx11")[:index]:
                parent = parent / part
                parent.mkdir(mode=0o700)
            (parent / (".local", "state", "eidolon", "nx11")[index]).symlink_to(outside, target_is_directory=True)
            with self.subTest(index=index), self.assertRaises(OSError):
                self.deploy(home=home)
            self.assertEqual(list(outside.iterdir()), [])

    def test_unsafe_mode_wrong_uid_and_non_directory_parent_are_rejected(self):
        self.home.chmod(0o777)
        with self.assertRaisesRegex(InstallError, "DIRECTORY_UNSAFE"):
            self.deploy()
        self.home.chmod(0o700)
        with self.assertRaisesRegex(InstallError, "UID_MISMATCH"):
            self.deploy(uid=os.getuid() + 1)
        (self.home / ".local").write_bytes(b"do not replace")
        with self.assertRaises(OSError):
            self.deploy()
        self.assertEqual((self.home / ".local").read_bytes(), b"do not replace")

    def test_source_symlink_or_oversize_is_rejected_before_transfer(self):
        source = self.repository / SOURCE_PATHS["sdl-wayland-probe.c"]
        source.unlink()
        source.symlink_to(self.repository / SOURCE_PATHS["eidolon-sdl-probe.py"])
        with self.assertRaisesRegex(InstallError, "SOURCE_NOT_REGULAR"):
            prepare_bundle(self.repository, "a" * 32, "b" * 40, False)
        source.unlink()
        source.write_bytes(b"x" * (MAX_SOURCE + 1))
        with self.assertRaisesRegex(InstallError, "SOURCE_SIZE_INVALID"):
            prepare_bundle(self.repository, "a" * 32, "b" * 40, False)

    def test_real_python_prefix_derives_termux_home_without_run_as_home(self):
        app = self.root / "com.termux"
        binary = app / "files/usr/bin/python3.14"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"fixture")
        home = app / "files/home"
        home.mkdir(mode=0o700)
        link = binary.with_name("python3")
        link.symlink_to(binary.name)
        self.assertEqual(termux_home(str(link)), home)
        self.assertNotEqual(home, app)  # run-as HOMEがapp dataでも保存先に使わない。
        with self.assertRaisesRegex(InstallError, "PYTHON_PREFIX_INVALID"):
            termux_home(sys.executable)

    def test_remote_entry_stdin_envelope_and_report_round_trip(self):
        app = self.root / "com.termux"
        binary = app / "files/usr/bin/python3.14"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"fixture")
        home = app / "files/home"
        home.mkdir(mode=0o700)
        module = Path(__file__).resolve().parents[1] / "sdl_probe_install.py"
        # 実機専用prefixを同じUIDのfixtureで模擬する。CLIにhome overrideは作らない。
        script = f"import sys;sys.executable={str(binary)!r};exec({module.read_text()!r})"
        process = subprocess.run([sys.executable, "-c", script, sha256(self.payload), str(os.getuid())],
                                 input=self.payload, capture_output=True, timeout=5,
                                 env={**os.environ, "HOME": str(app)})
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["home_source"], "python_prefix")
        self.assertEqual(Path(report["directory"]), home / ".local/state/eidolon/nx11" / ("a" * 32))
        self.assertEqual((Path(report["directory"]) / "sdl-wayland-probe.c").read_bytes(),
                         (self.repository / SOURCE_PATHS["sdl-wayland-probe.c"]).read_bytes())


if __name__ == "__main__":
    unittest.main()
