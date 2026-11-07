import argparse
import contextlib
import copy
import hashlib
import importlib.machinery
import importlib.util
import io
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
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sdl_lease_capture import CaptureError, canonical
from sdl_probe_run import group_live, run_operator, validate_artifacts

loader = importlib.machinery.SourceFileLoader("sdl_operator_cli", str(Path(__file__).resolve().parents[1] / "quest-run-sdl-probe"))
spec = importlib.util.spec_from_loader(loader.name, loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)


class HostCommandBudgetTests(unittest.TestCase):
    def test_oversized_fixed_source_is_rejected_before_adb_spawn(self):
        class Device:
            def __init__(self,*_):pass
            def validate(self):return dict(model="Quest 3",api=34)
            def adb(self,*args):
                if args[:2]==("shell","pidof"):return "77"
                if args[:3]==("shell","pm","list"):return "package:com.termux uid:10158"
                return "fixture"
        with tempfile.TemporaryDirectory(prefix="sdl-budget-") as td:
            root=Path(td);sources=root/"source";artifacts=root/"artifacts"
            for name,data in (("scripts/sdl_probe_install.py",b"x"*(50*1024)),("scripts/sdl_lease_capture.py",b"fixed"),
                              ("scripts/sdl_probe_run.py",b"fixed"),("scripts/fixtures/sdl-wayland-probe.c",b"fixed"),("scripts/termux/eidolon-sdl-probe.py",b"fixed"),
                              ("scripts/quest-run-sdl-probe",b"fixed"),("scripts/gui_probe_sdk.py",b"fixed"),("scripts/gtk_interaction_evidence.py",b"fixed")):
                target=sources/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            args=argparse.Namespace(serial="fixture",run_id="a"*32,binary_sha256="b"*64,manifest_sha256="c"*64,
                variant="egl-wayland-softpipe-kopper-disabled-debug",stage="gl-load",artifact_root=artifacts)
            with patch.object(cli.argparse.ArgumentParser,"parse_args",return_value=args),patch.object(cli,"ROOT",sources),patch.object(cli,"QuestDevice",Device), \
                 patch.object(cli,"digest_sources",return_value="fixed"),patch.object(cli,"select_snapshot",return_value=dict(uid=10158,process_id=77,session="17")), \
                 patch.object(cli,"sdk_sample",return_value=dict(mapped=[1])),patch.object(cli.subprocess,"run",return_value=subprocess.CompletedProcess([],0,b"{}",b"")), \
                 patch.object(cli.subprocess,"Popen") as spawn,contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(),1);spawn.assert_not_called()
            result=json.loads(next(artifacts.glob("*/result.json")).read_text())
            self.assertEqual(result["status"],"blocked");self.assertEqual(result["reason"],"REMOTE_COMMAND_LIMIT")


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

    def prepare(self, *, original_failure=False, hold=False, stage="render"):
        probe = dict(schema=1, probe="eidolon-sdl-observation-v1", binary_sha256=self.binary_hash,
                     reaped=True, group_reclaimed=True, graphics_client="failed" if original_failure else "completed",
                     shm_gate="fail" if original_failure else "pass", error="ORIGINAL_RENDERER" if original_failure else None,
                     returncode=7 if original_failure else 0, quest_sdk="not_run", stage=stage)
        if stage != "render":
            probe.update(graphics_client="not_run", shm_gate="not_run", diagnostic_gate="observed", returncode=1,
                         client=dict(api=dict(completed=False, stage="load", error_class="egl_initialize_failed"),
                                     cleanup=dict(completed=True)))
        program = ("import argparse,json,os,signal,subprocess,sys,time\nfrom pathlib import Path\n"
                   "os.umask(0o077)\np=argparse.ArgumentParser()\n"
                   "p.add_argument('--binary');p.add_argument('--result');p.add_argument('--driver');p.add_argument('--frames');p.add_argument('--timeout');p.add_argument('--stage')\n"
                   "a=p.parse_args()\nstop=[False]\nsignal.signal(signal.SIGTERM,lambda *_:stop.__setitem__(0,True))\n"
                   f"probe={probe!r}\n"
                   "owned=None\n"
                   + ("owned=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)\n" if hold else "")
                   + f"Path({str(self.directory / 'ready.json')!r}).write_text(json.dumps(dict(argv=sys.argv[1:],environment_keys=sorted(os.environ),egl_log_level=os.environ.get('EGL_LOG_LEVEL'),gallium_driver=os.environ.get('GALLIUM_DRIVER'),kopper_disable=os.environ.get('LIBGL_KOPPER_DISABLE'),graphics={{key:os.environ.get(key) for key in ['EGL_PLATFORM','LIBGL_ALWAYS_SOFTWARE']}},owned=owned.pid if owned else None)))\n"
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
                    binary_sha256=self.binary_hash, manifest_sha256=hashlib.sha256(data).hexdigest(), sources=sources,
                    variant="baseline", stage=stage)

    def execute(self, request, **kwargs):
        read_fd, write_fd = os.pipe()
        try:
            return run_operator(request, self.home, read_fd, self.events.append,
                                capture=lambda *_: self.proof, checker=kwargs.pop("checker", lambda *_: None), **kwargs)
        finally:
            os.close(read_fd);os.close(write_fd)

    def test_fixed_command_success_and_separate_sdk_status(self):
        request = self.prepare()
        with patch.dict(os.environ, {"EGL_PLATFORM": "parent-must-not-leak"}):
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
        self.assertEqual(ready["graphics"], dict(EGL_PLATFORM=None, LIBGL_ALWAYS_SOFTWARE="1"))
        self.assertEqual(report["variant"], "baseline")
        self.assertEqual(report["graphics_environment"], dict(LIBGL_ALWAYS_SOFTWARE="1"))
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
        request = self.prepare(original_failure=True)
        request["variant"] = "egl-wayland"
        report = self.execute(request)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["runner_returncode"], 1)
        self.assertEqual(report["probe_result"]["returncode"], 7)
        self.assertEqual(report["probe_result"]["error"], "ORIGINAL_RENDERER")
        self.assertTrue(report["cleanup_confirmed"])
        self.assertEqual(report["variant"], "egl-wayland")

    def test_egl_variant_changes_only_fixed_graphics_condition(self):
        request = self.prepare()
        baseline = self.execute(request)
        first = json.loads((self.directory / "ready.json").read_text())
        original_result = (self.directory / baseline["result_file"]).read_bytes()
        request.update(probe_id="d" * 32, variant="egl-wayland", environment={"LD_PRELOAD": "not-accepted"})
        with patch.dict(os.environ, {"EGL_PLATFORM": "parent-must-not-leak", "PRIVATE_SECRET": "private"}):
            report = self.execute(request)
        second = json.loads((self.directory / "ready.json").read_text())
        self.assertEqual(report["status"], "completed")
        self.assertTrue(report["cleanup_confirmed"])
        self.assertEqual(second["graphics"], dict(EGL_PLATFORM="wayland", LIBGL_ALWAYS_SOFTWARE="1"))
        self.assertEqual(set(second["environment_keys"]) - set(first["environment_keys"]), {"EGL_PLATFORM"})
        self.assertNotIn("LD_PRELOAD", second["environment_keys"])
        self.assertNotIn("PRIVATE_SECRET", second["environment_keys"])
        self.assertEqual(first["argv"][:3] + first["argv"][4:], second["argv"][:3] + second["argv"][4:])
        self.assertEqual((self.directory / baseline["result_file"]).read_bytes(), original_result)
        self.assertEqual(report["graphics_environment"], dict(LIBGL_ALWAYS_SOFTWARE="1", EGL_PLATFORM="wayland"))
        self.assertEqual(self.events[-2]["variant"], "egl-wayland")

    def test_invalid_or_missing_variant_is_rejected_before_spawn(self):
        request = self.prepare()
        for value in ("EGL_PLATFORM=x11", "wayland", {}, None):
            with self.subTest(value=value), self.assertRaisesRegex(CaptureError, "VARIANT_INVALID"):
                self.execute(dict(request, variant=value))
        request.pop("variant")
        with self.assertRaisesRegex(CaptureError, "VARIANT_INVALID"):
            self.execute(request)
        self.assertFalse((self.directory / "ready.json").exists())

    def test_stage_failure_is_diagnosed_without_becoming_graphics_success(self):
        request = self.prepare(stage="gl-load")
        report = self.execute(request)
        ready = json.loads((self.directory / "ready.json").read_text())
        self.assertEqual(ready["argv"][-2:], ["--stage", "gl-load"])
        self.assertEqual(report["status"], "diagnosed")
        self.assertEqual(report["stage"], "gl-load")
        self.assertEqual(report["probe_result"]["returncode"], 1)
        self.assertEqual(report["probe_result"]["shm_gate"], "not_run")
        self.assertFalse(report["probe_result"]["client"]["api"]["completed"])
        self.assertTrue(report["cleanup_confirmed"])

    def test_debug_variant_adds_only_log_level_and_keeps_original_stage_failure(self):
        request = self.prepare(stage="gl-load")
        request["variant"] = "egl-wayland"
        before = self.execute(request)
        ready_before = json.loads((self.directory / "ready.json").read_text())
        original = (self.directory / before["result_file"]).read_bytes()
        request.update(probe_id="e" * 32, variant="egl-wayland-debug")
        result = self.execute(request)
        ready_after = json.loads((self.directory / "ready.json").read_text())
        self.assertEqual(result["status"], "diagnosed")
        self.assertEqual(result["probe_result"]["client"], before["probe_result"]["client"])
        self.assertEqual(result["probe_result"]["returncode"], 1)
        self.assertEqual(ready_after["egl_log_level"], "debug")
        self.assertEqual(set(ready_after["environment_keys"]) - set(ready_before["environment_keys"]), {"EGL_LOG_LEVEL"})
        self.assertEqual(result["graphics_environment"], dict(LIBGL_ALWAYS_SOFTWARE="1", EGL_PLATFORM="wayland", EGL_LOG_LEVEL="debug"))
        self.assertEqual((self.directory / before["result_file"]).read_bytes(), original)

    def test_debug_requires_non_render_stage_before_spawn(self):
        request = self.prepare()
        for variant in ("egl-wayland-debug", "egl-wayland-softpipe-debug", "egl-wayland-softpipe-kopper-disabled-debug"):
            request["variant"] = variant
            with self.subTest(variant=variant), self.assertRaisesRegex(CaptureError, "DEBUG_STAGE_REQUIRED"):
                self.execute(request)
        self.assertFalse((self.directory / "ready.json").exists())

    def test_softpipe_debug_adds_only_driver_and_keeps_prior_failure(self):
        request = self.prepare(stage="gl-load")
        request["variant"] = "egl-wayland-debug"
        before = self.execute(request)
        ready_before = json.loads((self.directory / "ready.json").read_text())
        original = (self.directory / before["result_file"]).read_bytes()
        request.update(probe_id="f" * 32, variant="egl-wayland-softpipe-debug")
        result = self.execute(request)
        ready_after = json.loads((self.directory / "ready.json").read_text())
        self.assertEqual(result["status"], "diagnosed")
        self.assertEqual(result["probe_result"], before["probe_result"])
        self.assertEqual(ready_after["gallium_driver"], "softpipe")
        self.assertEqual(ready_after["egl_log_level"], "debug")
        self.assertEqual(set(ready_after["environment_keys"]) - set(ready_before["environment_keys"]), {"GALLIUM_DRIVER"})
        self.assertEqual((self.directory / before["result_file"]).read_bytes(), original)
        self.assertEqual(result["graphics_environment"], dict(LIBGL_ALWAYS_SOFTWARE="1", EGL_PLATFORM="wayland",
                                                             EGL_LOG_LEVEL="debug", GALLIUM_DRIVER="softpipe"))

    def test_softpipe_non_debug_twin_keeps_fixed_driver_without_log_level(self):
        request = self.prepare()
        request["variant"] = "egl-wayland-softpipe"
        result = self.execute(request)
        ready = json.loads((self.directory / "ready.json").read_text())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(ready["gallium_driver"], "softpipe")
        self.assertIsNone(ready["egl_log_level"])
        self.assertEqual(result["graphics_environment"], dict(LIBGL_ALWAYS_SOFTWARE="1", EGL_PLATFORM="wayland", GALLIUM_DRIVER="softpipe"))

    def test_kopper_disable_debug_is_exactly_one_env_and_keeps_original_stage_failure(self):
        request=self.prepare(stage="gl-load");request["variant"]="egl-wayland-softpipe-debug"
        before=self.execute(request);first=json.loads((self.directory/"ready.json").read_text());saved=(self.directory/before["result_file"]).read_bytes()
        request.update(probe_id="9"*32,variant="egl-wayland-softpipe-kopper-disabled-debug")
        with patch.dict(os.environ,{"LIBGL_KOPPER_DISABLE":"PRIVATE","MESA_LOADER_DRIVER_OVERRIDE":"PRIVATE"}):result=self.execute(request)
        ready=json.loads((self.directory/"ready.json").read_text())
        self.assertEqual(result["status"],"diagnosed");self.assertEqual(result["probe_result"],before["probe_result"])
        self.assertEqual(result["graphics_environment"],dict(before["graphics_environment"],LIBGL_KOPPER_DISABLE="1"))
        self.assertEqual(set(ready["environment_keys"])-set(first["environment_keys"]),{"LIBGL_KOPPER_DISABLE"})
        self.assertEqual(ready["kopper_disable"],"1");self.assertEqual(ready["gallium_driver"],"softpipe");self.assertEqual(ready["egl_log_level"],"debug")
        self.assertNotIn("MESA_LOADER_DRIVER_OVERRIDE",ready["environment_keys"]);self.assertEqual((self.directory/before["result_file"]).read_bytes(),saved)
        self.assertTrue(result["cleanup_confirmed"]);self.assertEqual(result["probe_result"]["returncode"],1)

    def test_kopper_disable_non_debug_twin_allows_render_without_new_argv_or_pins(self):
        request=self.prepare();request["variant"]="egl-wayland-softpipe-kopper-disabled"
        result=self.execute(request);ready=json.loads((self.directory/"ready.json").read_text())
        self.assertEqual(result["status"],"completed");self.assertEqual(ready["kopper_disable"],"1");self.assertIsNone(ready["egl_log_level"])
        self.assertNotIn("--stage",ready["argv"]);self.assertEqual(result["graphics_environment"],dict(LIBGL_ALWAYS_SOFTWARE="1",EGL_PLATFORM="wayland",GALLIUM_DRIVER="softpipe",LIBGL_KOPPER_DISABLE="1"))
        self.assertTrue(result["cleanup_confirmed"])

    def test_free_or_missing_stage_is_rejected_before_spawn(self):
        request = self.prepare()
        for value in ("gl-load --arbitrary", {}, None):
            with self.subTest(value=value), self.assertRaisesRegex(CaptureError, "STAGE_INVALID"):
                self.execute(dict(request, stage=value))
        request.pop("stage")
        with self.assertRaisesRegex(CaptureError, "STAGE_INVALID"):
            self.execute(request)
        self.assertFalse((self.directory / "ready.json").exists())

    def test_cli_rejects_free_environment_variant(self):
        completed = subprocess.run([sys.executable, cli.__file__, "--serial", "unused", "--run-id", self.run_id,
                                    "--binary-sha256", "1" * 64, "--manifest-sha256", "2" * 64,
                                    "--variant", "EGL_PLATFORM=x11"], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("invalid choice", completed.stderr)

    def test_cancel_reclaims_only_owned_runner_and_child(self):
        request = self.prepare(hold=True)
        request["variant"] = "egl-wayland"
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
        # fake runnerのhandler/owned child起動に余裕を持たせ、終了判定の期限超過を検査する。
        report = self.execute(self.prepare(hold=True), timeout=1.0)
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

    def test_sdk_retirement_requires_every_full_target_and_preserves_original_owners(self):
        # GTKと共通の実所有台帳fixture。SDLのframe gateとは独立して回収を検査する。
        from test_gtk_interaction_live import sdk
        def row(value):
            return dict(mapped=[w["window"] for w in value["windows"]], resources=value["resources"],
                        native_targets=[t["key"] for t in value["targets"]])
        baseline, live, end = row(sdk(main=False)), row(sdk()), row(sdk(retired=True))
        self.assertTrue(cli.sdk_resources_retired(baseline,[live,end]))
        self.assertFalse(cli.sdk_resources_retired(dict(mapped=["1"]),[live,end]))
        for mutate in (lambda r:r["retired"].clear(),
                       lambda r:r["retired"][0].update(sdk_binding_released=False),
                       lambda r:r["board"].update(cleanup_required=True),
                       lambda r:r["owners"].update(mode_texture_owners=2)):
            changed=copy.deepcopy(end);mutate(changed["resources"])
            self.assertFalse(cli.sdk_resources_retired(baseline,[live,changed]))
        replacement=copy.deepcopy(live)
        replacement["resources"]["targets"][-1]["key"]["generation"]="8"
        self.assertFalse(cli.sdk_resources_retired(baseline,[live,replacement,end]))

    def test_invalid_control_fd_is_rejected_before_spawn(self):
        request = self.prepare()
        with self.assertRaises(OSError):
            run_operator(request, self.home, -1, self.events.append,
                         capture=lambda *_: self.proof, checker=lambda *_: None)
        self.assertFalse((self.directory / "ready.json").exists())


if __name__ == "__main__":
    unittest.main()
