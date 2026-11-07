import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sdl_lease_capture import CaptureError, canonical
from sdl_probe_run import group_live, run_operator, validate_artifacts

loader = importlib.machinery.SourceFileLoader("sdl_operator_cli", str(Path(__file__).resolve().parents[1] / "quest-run-sdl-probe"))
spec = importlib.util.spec_from_loader(loader.name, loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)


class SdlOperatorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="eidolon-run-")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "h"
        self.home.mkdir(mode=0o700)
        self.run_id = "a" * 32
        self.directory = self.home / ".local/state/eidolon/nx11" / self.run_id
        self.directory.mkdir(parents=True, mode=0o700)
        for parent in self.directory.parents:
            if parent == self.home.parent:
                break
            parent.chmod(0o700)
        binary = bytearray(64)
        binary[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<H", binary, 18, 183)
        self.binary_hash = hashlib.sha256(binary).hexdigest()
        (self.directory / "eidolon-sdl-probe").write_bytes(binary)
        (self.directory / "eidolon-sdl-probe").chmod(0o700)
        self.binding = dict(uid=os.getuid(), session="17", nonce="b" * 16)
        self.proof = dict(identities={}, environment=dict(WAYLAND_DISPLAY="w", XDG_RUNTIME_DIR=str(self.home)))
        self.events = []

    def prepare(self, *, original_failure=False, hold=False):
        probe = dict(schema=1, probe="eidolon-sdl-observation-v1", binary_sha256=self.binary_hash,
                     reaped=True, group_reclaimed=True, graphics_client="failed" if original_failure else "completed",
                     shm_gate="fail" if original_failure else "pass", error="ORIGINAL_RENDERER" if original_failure else None,
                     returncode=7 if original_failure else 0, quest_sdk="not_run")
        program = ("import argparse,json,os,signal,subprocess,sys,time\nfrom pathlib import Path\n"
                   "os.umask(0o077)\np=argparse.ArgumentParser()\n"
                   "p.add_argument('--binary');p.add_argument('--result');p.add_argument('--driver');p.add_argument('--frames');p.add_argument('--timeout')\n"
                   "a=p.parse_args()\nstop=[False]\nsignal.signal(signal.SIGTERM,lambda *_:stop.__setitem__(0,True))\n"
                   f"probe={probe!r}\n"
                   "owned=None\n"
                   + ("owned=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)\n" if hold else "")
                   + f"Path({str(self.directory / 'ready.json')!r}).write_text(json.dumps(dict(argv=sys.argv[1:],environment_keys=sorted(os.environ),owned=owned.pid if owned else None)))\n"
                   + ("until=time.monotonic()+10\nwhile not stop[0] and time.monotonic()<until:time.sleep(.01)\n" if hold else "time.sleep(.05)\n")
                   + "if owned is not None:\n owned.terminate();owned.wait(timeout=2)\n"
                   "if stop[0]:probe.update(graphics_client='failed',shm_gate='fail')\n"
                   "Path(a.result).write_text(json.dumps(probe))\n"
                   + f"raise SystemExit(1 if stop[0] or {original_failure!r} else 0)\n")
        (self.directory / "sdl-wayland-probe.c").write_bytes(b"fixed C source")
        (self.directory / "eidolon-sdl-probe.py").write_text(program)
        sources = {}
        for name in ("sdl-wayland-probe.c", "eidolon-sdl-probe.py"):
            path = self.directory / name
            path.chmod(0o600)
            data = path.read_bytes();sources[name] = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        manifest = dict(schema=1, kind="eidolon-sdl-source-v1", run_id=self.run_id, files=sources)
        data = canonical(manifest) + b"\n"
        (self.directory / "manifest.json").write_bytes(data)
        (self.directory / "manifest.json").chmod(0o600)
        return dict(binding=self.binding, probe_id="c" * 32, run_id=self.run_id,
                    binary_sha256=self.binary_hash, manifest_sha256=hashlib.sha256(data).hexdigest(), sources=sources)

    def execute(self, request, **kwargs):
        read_fd, write_fd = os.pipe()
        try:
            return run_operator(request, self.home, read_fd, self.events.append,
                                capture=lambda *_: self.proof, checker=kwargs.pop("checker", lambda *_: None), **kwargs)
        finally:
            os.close(read_fd);os.close(write_fd)

    def test_fixed_command_success_and_separate_sdk_status(self):
        request = self.prepare()
        report = self.execute(request)
        self.assertEqual(report["status"], "completed")
        self.assertTrue(report["cleanup_confirmed"])
        self.assertTrue(report["runner_group_reclaimed"])
        self.assertTrue(report["pipes_drained"])
        self.assertEqual(report["probe_result"]["quest_sdk"], "not_run")
        ready = json.loads((self.directory / "ready.json").read_text())
        self.assertEqual(ready["argv"], ["--binary", str(self.directory / "eidolon-sdl-probe"), "--result",
                         str(self.directory / report["result_file"]), "--driver", "wayland", "--frames", "120", "--timeout", "15"])
        self.assertNotIn("PRIVATE_SECRET", ready["environment_keys"])
        saved = self.directory / ("operator-" + request["probe_id"] + "-report.json")
        self.assertEqual(json.loads(saved.read_text()), report)
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)

    def test_changed_manifest_binary_source_or_symlink_is_rejected_before_spawn(self):
        request = self.prepare()
        for name, expected in (("manifest.json", "MANIFEST_CHANGED"), ("eidolon-sdl-probe", "BINARY_CHANGED"),
                               ("sdl-wayland-probe.c", "SOURCE_CHANGED")):
            path = self.directory / name;original = path.read_bytes();path.write_bytes(original + b"changed")
            with self.subTest(name=name), self.assertRaisesRegex(CaptureError, expected):
                self.execute(request)
            self.assertFalse((self.directory / "ready.json").exists())
            path.write_bytes(original)
        binary = self.directory / "eidolon-sdl-probe"
        binary.unlink();binary.symlink_to("sdl-wayland-probe.c")
        with self.assertRaises(OSError):
            validate_artifacts(request, self.home)

    def test_renderer_error_and_original_exit_are_preserved(self):
        report = self.execute(self.prepare(original_failure=True))
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["runner_returncode"], 1)
        self.assertEqual(report["probe_result"]["returncode"], 7)
        self.assertEqual(report["probe_result"]["error"], "ORIGINAL_RENDERER")
        self.assertTrue(report["cleanup_confirmed"])

    def test_cancel_reclaims_only_owned_runner_and_child(self):
        request = self.prepare(hold=True)
        unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], start_new_session=True)
        read_fd, write_fd = os.pipe()
        failures = []

        def cancelled():
            deadline = time.monotonic() + 3
            while not (self.directory / "ready.json").exists() and time.monotonic() < deadline:
                time.sleep(.01)
            if not (self.directory / "ready.json").exists():
                failures.append("runner not ready")
            os.write(write_fd, canonical(dict(type="cancel", run_id=self.run_id)) + b"\n")

        writer = threading.Thread(target=cancelled);writer.start()
        try:
            report = run_operator(request, self.home, read_fd, self.events.append,
                                  capture=lambda *_: self.proof, checker=lambda *_: None)
            writer.join(timeout=4)
            self.assertEqual(failures, [])
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["cancel_reason"], "HOST_CANCEL")
            self.assertTrue(report["cleanup_confirmed"])
            self.assertIsNone(unrelated.poll())
            owned = json.loads((self.directory / "ready.json").read_text())["owned"]
            self.assertFalse(group_live(owned))
        finally:
            os.close(read_fd);os.close(write_fd)
            unrelated.terminate();unrelated.wait(timeout=3)

    def test_old_result_and_noncanonical_run_id_are_rejected(self):
        request = self.prepare()
        request["run_id"] = "../other"
        with self.assertRaisesRegex(CaptureError, "PIN_INVALID"):
            self.execute(request)
        request["run_id"] = self.run_id
        (self.directory / ("operator-" + request["probe_id"] + ".json")).write_bytes(b"existing")
        with self.assertRaisesRegex(CaptureError, "RESULT_EXISTS"):
            self.execute(request)

    def test_lease_change_and_deadline_cancel_cooperatively(self):
        request = self.prepare(hold=True)
        def check(*_):
            if (self.directory / "ready.json").exists():
                raise CaptureError("LEASE_CHANGED")
        report = self.execute(request, checker=check)
        self.assertEqual(report["cancel_reason"], "LEASE_CHANGED")
        self.assertTrue(report["cleanup_confirmed"])
        self.assertEqual(report["status"], "failed")

    def test_deadline_is_not_reported_as_success(self):
        report = self.execute(self.prepare(hold=True), timeout=.2)
        self.assertTrue(report["timed_out"])
        self.assertEqual(report["cancel_reason"], "OPERATOR_TIMEOUT")
        self.assertTrue(report["cleanup_confirmed"])
        self.assertEqual(report["status"], "failed")

    def test_sdk_metadata_requires_content_advance_retirement_and_baseline(self):
        base = dict(sequence="1", mapped=["1"], targets=[])
        def sample(sequence, content):
            return dict(sequence=sequence, mapped=["1", "2"], targets=[dict(window="2", frames=10, content_frame=content)])
        a, b = sample("2", "5"), sample("3", "6")
        end = dict(sequence="4", mapped=["1"], targets=[])
        self.assertEqual(cli.summarize_sdk(base, [a, b, end])["status"], "observed")
        self.assertEqual(cli.summarize_sdk(base, [a, a, end])["status"], "not_verified")
        self.assertEqual(cli.summarize_sdk(base, [a, b])["status"], "not_verified")
        self.assertEqual(cli.summarize_sdk(base, [a, b, dict(sequence="4", mapped=[], targets=[])])["status"], "not_verified")

    def test_invalid_control_fd_is_rejected_before_spawn(self):
        request = self.prepare()
        with self.assertRaises(OSError):
            run_operator(request, self.home, -1, self.events.append,
                         capture=lambda *_: self.proof, checker=lambda *_: None)
        self.assertFalse((self.directory / "ready.json").exists())


if __name__ == "__main__":
    unittest.main()
