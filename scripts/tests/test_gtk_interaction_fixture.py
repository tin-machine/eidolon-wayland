import hashlib
import argparse
import contextlib
import io
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from gtk_xvfb_startup import StderrCount, wait_ready

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
from sdl_probe_install import InstallError, canonical, install_bundle, prepare_bundle, sha256

loader = importlib.machinery.SourceFileLoader("gtk_installer", str(SCRIPTS / "quest-install-gtk-fixture"))
spec = importlib.util.spec_from_loader(loader.name, loader)
installer = importlib.util.module_from_spec(spec);loader.exec_module(installer)
spec = importlib.util.spec_from_file_location("gtk_builder", SCRIPTS / "termux/build-gtk3-fixture.py")
builder = importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="gtk-build-");self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.files = self.root / "com.termux/files"
        self.prefix = self.files / "usr";(self.prefix / "bin").mkdir(parents=True)
        self.directory = self.files / "home/.local/state/eidolon/nx15-fixture" / ("d" * 32)
        self.directory.mkdir(mode=0o700, parents=True)
        for parent in self.directory.parents:
            if parent == self.files: break
            parent.chmod(0o700)
        for name, relative in installer.SOURCE_PATHS.items():
            target = self.directory / name;target.write_bytes((ROOT / relative).read_bytes());target.chmod(0o600)
        self.manifest = dict(schema=1, kind=installer.KIND, run_id=self.directory.name, source_commit="c" * 40, source_dirty=False,
                             files={name: dict(bytes=(self.directory/name).stat().st_size, sha256=sha256((self.directory/name).read_bytes()))
                                    for name in installer.SOURCE_PATHS})
        data = canonical(self.manifest) + b"\n";target = self.directory / "manifest.json";target.write_bytes(data);target.chmod(0o600)
        self.expected = sha256(data)
        self.python = self.prefix / "bin/python3";self.python.write_bytes(b"fixture prefix only");self.python.chmod(0o700)
        self.real_python = sys.executable
        self.tool("pkg-config", "import sys;print('3.24.52' if '--modversion' in sys.argv else '-I/fixed/include -lfixed')")
        self.compiler()

    def tool(self, name, source):
        path = self.prefix / "bin" / name;path.write_text(f"#!{self.real_python}\n" + source + "\n");path.chmod(0o700)

    def compiler(self, *, exit_code=0, sleep=False):
        elf = bytearray(64);elf[:6] = b"\x7fELF\x02\x01";elf[18:20] = (183).to_bytes(2, "little")
        self.tool("clang", "import sys,time,os\nfrom pathlib import Path\n" +
                  ("time.sleep(30)\n" if sleep else "") +
                  f"os.write(2,b'PRIVATE /compiler/path symbol')\nPath(sys.argv[sys.argv.index('-o')+1]).write_bytes({bytes(elf)!r})\n"
                  f"raise SystemExit({exit_code})")

    def run_build(self, expected=None):
        output = io.StringIO()
        with patch.object(builder, "__file__", str(self.directory / "build-gtk3-fixture.py")), \
             patch.object(builder, "UID", os.getuid()), patch.object(builder.sys, "executable", str(self.python)), \
             patch.object(builder.argparse.ArgumentParser, "parse_args", return_value=argparse.Namespace(manifest_sha256=expected or self.expected)), \
             contextlib.redirect_stdout(output):
            code = builder.main()
        return code, json.loads(output.getvalue())

    def test_only_verified_source_compiles_and_binary_has_fresh_host_known_pin(self):
        code, report = self.run_build()
        self.assertEqual(code, 0);self.assertEqual(report["status"], "built")
        self.assertFalse(report["executed"]);self.assertEqual(report["elf_machine"], 183)
        binary = self.directory / "build/gtk3-fixture"
        self.assertEqual(report["binary_sha256"], sha256(binary.read_bytes()))
        self.assertEqual(binary.stat().st_mode & 0o777, 0o700)
        self.assertNotIn("PRIVATE", json.dumps(report))
        self.assertFalse(list((self.directory / "build").glob("compiler.*")))
        code, report = self.run_build();self.assertEqual(code, 1)  # build directoryを上書きしない。

    def test_manifest_or_source_change_blocks_before_compile(self):
        code, report = self.run_build("0" * 64)
        self.assertEqual(report["reason"], "MANIFEST_CHANGED");self.assertFalse((self.directory / "build").exists())
        (self.directory / "gtk3-interaction-fixture.c").write_bytes(b"CHANGED")
        code, report = self.run_build()
        self.assertEqual(code, 1);self.assertEqual(report["reason"], "SOURCE_CHANGED")
        self.assertFalse((self.directory / "build").exists())

    def test_original_compiler_failure_or_timeout_is_retained_without_body(self):
        self.compiler(exit_code=7)
        code, report = self.run_build()
        self.assertEqual(code, 1);self.assertEqual(report["compiler_returncode"], 7)
        self.assertEqual(report["reason"], "COMPILE_FAILED");self.assertTrue(report["compiler_reaped"])
        self.assertNotIn("PRIVATE", json.dumps(report))

    def test_compile_timeout_reaps_spawned_compiler(self):
        self.compiler(sleep=True)
        with patch.object(builder, "COMPILE_TIMEOUT", .1), patch.object(builder, "TERM_GRACE", .1):
            code, report = self.run_build()
        self.assertEqual(code, 1);self.assertEqual(report["reason"], "COMPILE_TIMEOUT")
        self.assertTrue(report["compiler_timed_out"] and report["compiler_reaped"])


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.old_umask = os.umask(0o077);self.addCleanup(os.umask, self.old_umask)
        self.temporary = tempfile.TemporaryDirectory(prefix="gtk-install-");self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "home";self.home.mkdir(mode=0o700)
        self.payload, self.manifest = prepare_bundle(ROOT, "b" * 32, "c" * 40, False,
                                                     source_paths=installer.SOURCE_PATHS, kind=installer.KIND)

    def install(self, payload=None, *, kind=installer.KIND, scope=installer.SCOPE):
        payload = payload or self.payload
        return install_bundle(payload, sha256(payload), self.home, os.getuid(), source_paths=installer.SOURCE_PATHS,
                              kind=kind, scope=scope)

    def test_source_manifest_owner_modes_hash_and_no_overwrite(self):
        report = self.install();directory = Path(report["directory"])
        self.assertEqual(directory, self.home / ".local/state/eidolon/nx15-fixture" / ("b" * 32))
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(set(p.name for p in directory.iterdir()), set(installer.SOURCE_PATHS) | {"manifest.json"})
        for path in directory.iterdir(): self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(report["manifest_sha256"], sha256(canonical(self.manifest) + b"\n"))
        with self.assertRaisesRegex(InstallError, "RUN_ALREADY_EXISTS"): self.install()

    def test_tampered_source_wrong_kind_and_cross_scope_are_rejected(self):
        bundle = json.loads(self.payload)
        bundle["manifest"]["files"]["gtk3-interaction-fixture.c"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(InstallError, "SOURCE_HASH_MISMATCH"): self.install(canonical(bundle))
        with self.assertRaisesRegex(InstallError, "SOURCE_SCOPE_INVALID"): self.install(scope="nx11")
        with self.assertRaisesRegex(InstallError, "MANIFEST_INVALID"):
            self.install(kind="eidolon-sdl-source-v1", scope="nx11")
        self.assertFalse((self.home / ".local").exists())

    def test_owned_run_symlink_is_rejected(self):
        parent = self.home / ".local/state/eidolon/nx15-fixture";parent.mkdir(mode=0o700, parents=True)
        (parent / ("b" * 32)).symlink_to(self.home, target_is_directory=True)
        with self.assertRaisesRegex(InstallError, "RUN_ALREADY_EXISTS"): self.install()

    def test_remote_installer_is_compressed_bounded_and_never_executes_fixture(self):
        source = installer.remote_source(ROOT, sha256(self.payload));compile(source, "gtk-install-remote", "exec")
        self.assertLess(len(source.encode()), 49 * 1024)
        self.assertNotIn("clang", source)


class HostGtkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="gtk-host-")
        cls.root = Path(cls.temporary.name)
        flags = shlex.split(subprocess.check_output(["pkg-config", "--cflags", "--libs", "gtk+-3.0"], text=True))
        cls.binary, cls.production = cls.root / "selftest", cls.root / "production"
        for target, extra in ((cls.binary, ["-DEIDOLON_GTK_FIXTURE_SELFTEST"]), (cls.production, [])):
            compiled = subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2", *extra,
                                       str(SCRIPTS / "fixtures/gtk3-interaction-fixture.c"), "-o", str(target), *flags], capture_output=True)
            if compiled.returncode: raise RuntimeError("host C compile failed: " + str(compiled.returncode))
        read_fd, write_fd = os.pipe()
        cls.xvfb = subprocess.Popen(["Xvfb", "-displayfd", str(write_fd), "-screen", "0", "1024x768x24", "-nolisten", "tcp", "-noreset"],
                                    pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        os.close(write_fd)
        cls.xvfb_stderr = StderrCount(cls.xvfb.stderr)
        try:
            def probe(display, remaining):
                env = dict(os.environ, DISPLAY=display, GDK_BACKEND="x11", GDK_GL="disable", GDK_RENDERING="image",
                           HOME=str(cls.root), GTK_IM_MODULE="gtk-im-context-simple")
                env.pop("WAYLAND_DISPLAY", None)
                # Production rejects X11 immediately after gtk_init_check, before any widget/map.
                return subprocess.run([str(cls.production), "--case", "input", "--run-id", "f" * 32],
                                      stdin=subprocess.DEVNULL, capture_output=True, timeout=remaining, env=env)
            # CI can reach the displayfd deadline before Xvfb emits any readiness
            # or stderr. Give only server startup 15s; GTK still gets its own 5s.
            cls.display, cls.xvfb_ready = wait_ready(
                read_fd, cls.xvfb, probe, cls.xvfb_stderr, startup_timeout=15)
        except Exception:
            cls.stop_xvfb();cls.temporary.cleanup();raise
        finally: os.close(read_fd)

    @classmethod
    def tearDownClass(cls):
        try: cls.stop_xvfb()
        finally: cls.temporary.cleanup()

    @classmethod
    def stop_xvfb(cls):
        cls.xvfb_stderr.drain();cls.xvfb.terminate()
        try: cls.xvfb.wait(timeout=3)
        except subprocess.TimeoutExpired: cls.xvfb.kill();cls.xvfb.wait(timeout=3)
        cls.xvfb_stderr.drain();cls.xvfb.stderr.close()

    def run_case(self, case, *, production=False, run_id="a" * 32):
        self.xvfb_stderr.drain()
        env = dict(os.environ, DISPLAY=self.display, GDK_BACKEND="x11", GDK_GL="disable", GDK_RENDERING="image",
                   HOME=str(self.root), GTK_IM_MODULE="gtk-im-context-simple")
        env.pop("WAYLAND_DISPLAY", None)
        process = subprocess.run([str(self.production if production else self.binary), "--case", case, "--run-id", run_id],
                                 capture_output=True, timeout=8, env=env)
        self.xvfb_stderr.drain()
        return process, [json.loads(line) for line in process.stdout.splitlines()]

    def test_input_callbacks_count_fixed_token_delete_and_button_without_text(self):
        process, rows = self.run_case("input")
        self.assertEqual(process.returncode, 0)
        self.assertTrue(rows[-1]["complete"])
        self.assertTrue(rows[-1]["token_seen"] and rows[-1]["delete_seen"])
        self.assertEqual([rows[-1][k] for k in ("a_down", "a_up", "delete_down", "delete_up", "button_clicks")], [1] * 5)
        self.assertNotIn("text", rows[-1]);self.assertTrue(all(row["case"] == "input" for row in rows))
        self.assertEqual(rows[-1]["app_id"], "io.github.tinmachine.eidolon.gtkfixture.r" + "a" * 32)

    def test_dialog_opens_has_parent_and_closes_by_response(self):
        process, rows = self.run_case("dialog")
        self.assertEqual(process.returncode, 0)
        last = rows[-1]
        self.assertTrue(last["complete"] and last["dialog_parent_set"])
        self.assertEqual([last[k] for k in ("dialog_opened", "dialog_closed", "dialog_responses")], [1] * 3)
        self.assertFalse(last["dialog_visible"]);self.assertGreater(last["dialog_draws"], 0)

    def test_real_x11_configures_include_both_fixed_sizes(self):
        process, rows = self.run_case("resize")
        self.assertEqual(process.returncode, 0)
        self.assertTrue(rows[-1]["complete"] and rows[-1]["resize_large_seen"] and rows[-1]["resize_return_seen"])
        self.assertEqual(rows[-1]["logical_size"], [640, 360])

    @unittest.skipUnless(shutil.which("kwin_wayland") and shutil.which("dbus-run-session"),"isolated KWin virtual backend unavailable")
    def test_real_wayland_ids_precede_first_shm_and_dialog_keeps_parent(self):
        from gtk_wayland_identity_fixture import VirtualWayland
        with VirtualWayland() as display:
            for i,case in enumerate(("input","dialog","resize")):
                with self.subTest(case=case):
                    report=display.run(self.binary,case,format(i+1,"032x"));trace=report["trace"];identity=trace["identity"]
                    self.assertEqual(report["client_returncode"],0);self.assertFalse(report["timed_out"])
                    self.assertTrue(report["reports"][-1]["complete"]);self.assertEqual(trace["unknown_app_ids"],0)
                    self.assertTrue(identity["initial_ids_known"] and identity["first_shm_has_final_role"])
                    self.assertGreater(trace["fixture_shm_commits"]["main"],0)
                    if case=="dialog":
                        self.assertEqual(identity["final_toplevels"],dict(main=1,dialog=1))
                        self.assertTrue(identity["known_dialog_transition"] and trace["dialog_parent_observed"])
                        self.assertGreater(trace["fixture_shm_commits"]["dialog"],0)
                    else:self.assertEqual(identity["final_toplevels"],dict(main=1,dialog=0))
                    self.assertEqual(trace["protocol_errors"],0)

    def test_production_refuses_x11_and_arbitrary_cli_input(self):
        process, rows = self.run_case("input", production=True)
        self.assertEqual(process.returncode, 2);self.assertEqual(rows[-1]["reason"], "wayland_required")
        process, rows = self.run_case("OTHER", production=True)
        self.assertEqual(process.returncode, 64);self.assertFalse(rows)
        process, rows = self.run_case("input", production=True, run_id="PRIVATE/raw")
        self.assertEqual(process.returncode, 64);self.assertFalse(rows)


if __name__ == "__main__": unittest.main()
