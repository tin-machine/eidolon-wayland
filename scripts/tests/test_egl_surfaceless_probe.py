import contextlib
import ctypes
import argparse
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import egl_surfaceless_probe as egl
import egl_loaded_identity as loaded
from sdl_probe_install import InstallError

loader = importlib.machinery.SourceFileLoader("egl_cli", str(Path(__file__).resolve().parents[1] / "quest-probe-egl-surfaceless"))
spec = importlib.util.spec_from_loader(loader.name, loader)
cli = importlib.util.module_from_spec(spec);loader.exec_module(cli)


class Function:
    def __init__(self, call): self.call = call
    def __call__(self, *args): return self.call(*args)


class FakeEgl:
    def __init__(self, *, initialize=False, supported=True, display=True, core=True):
        self.error, self.calls = 0x3000, []
        self.eglGetError = Function(self.get_error)
        self.eglQueryString = Function(lambda *_: b"EGL_MESA_platform_surfaceless" if supported else b"OTHER")
        self.eglInitialize = Function(lambda *args: self.initialize(initialize, *args))
        self.eglTerminate = Function(self.terminate)
        if core:
            self.eglGetPlatformDisplay = Function(lambda *args: self.get_display(display, *args))
        else:
            self.callback = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                                            ctypes.POINTER(ctypes.c_int))(lambda *args: self.get_display(display, *args))
            self.eglGetProcAddress = Function(lambda _: ctypes.cast(self.callback, ctypes.c_void_p).value)

    def get_error(self):
        result = self.error;self.error = 0x3000;self.calls.append("error");return result

    def get_display(self, returned, platform, native, attributes):
        assert platform == 0x31DD and not native and not attributes
        self.calls.append("display");self.error = 0x3000 if returned else 0x3008
        return 1234 if returned else None

    def initialize(self, returned, display, major, minor):
        assert display == 1234
        self.calls.append("initialize");self.error = 0x3000 if returned else 0x3001
        ctypes.cast(major, ctypes.POINTER(ctypes.c_int))[0] = 1
        ctypes.cast(minor, ctypes.POINTER(ctypes.c_int))[0] = 5
        return int(returned)

    def terminate(self, display):
        assert display == 1234
        self.calls.append("terminate");self.error = 0x3008
        return 1


def loaded_event(verified=True):
    rows=[dict(name=n,canonical_path="/data/data/com.termux/files/usr/lib/"+n,device_major=1,device_minor=2,inode=3,bytes=100,sha256=egl.LIBRARIES[n],uid=egl.UID,mode=0o700) for n in loaded.NAMES]
    return dict(phase="loaded_libraries",result=dict(profile=loaded.PROFILE,status="verified" if verified else "not_verified",reason=None if verified else "LOADED_MAPS_MISMATCH",libraries=rows))


def api_events(*, initialize=False, verified=True):
    events = []
    egl.perform_api(FakeEgl(initialize=initialize), events.append,lambda:events.append(loaded_event(verified)))
    events.append(dict(phase="runtime", unchanged=True))
    return events


class PublicApiTests(unittest.TestCase):
    def test_first_initialize_error_is_flushed_before_terminate_and_retained(self):
        library, events = FakeEgl(), []
        def emit(event):
            if event["phase"] == "api": self.assertNotIn("terminate", library.calls)
            events.append(event)
        self.assertEqual(egl.perform_api(library, emit), 1)
        self.assertEqual(events[0]["result"]["initialize"]["error"], dict(value=0x3001, name="not_initialized"))
        self.assertEqual(events[1]["result"]["error"]["value"], 0x3008)
        self.assertEqual(library.eglInitialize.restype, ctypes.c_uint)
        self.assertEqual(library.eglGetError.restype, ctypes.c_int)
        self.assertEqual(library.eglGetPlatformDisplay.argtypes[-1], ctypes.POINTER(ctypes.c_ssize_t))
        self.assertEqual(library.calls[library.calls.index("initialize") + 1], "error")

    def test_loaded_capture_happens_after_first_error_flush_before_terminate(self):
        library=FakeEgl();events=[]
        def capture():
            self.assertEqual(events[-1]["phase"],"api");self.assertEqual(library.calls[-1],"error")
            self.assertNotIn("terminate",library.calls);events.append(loaded_event())
        self.assertEqual(egl.perform_api(library,events.append,capture),1)
        self.assertEqual([e["phase"] for e in events],["api","loaded_libraries","egl_cleanup"])
        self.assertEqual(events[0]["result"]["initialize"]["error"]["value"],0x3001)

    def test_ext_signature_and_core_success(self):
        for core in (True, False):
            with self.subTest(core=core):
                events = []
                self.assertEqual(egl.perform_api(FakeEgl(initialize=True, core=core), events.append), 0)
                self.assertEqual(events[0]["result"]["entrypoint"], "core" if core else "ext")
                self.assertEqual(events[0]["result"]["initialize"]["major"], 1)
                self.assertEqual(events[0]["result"]["initialize"]["minor"], 5)
                for event in events: self.assertEqual(egl.decode_event(json.dumps(event)), event)

    def test_unsupported_and_null_display_do_not_initialize_or_terminate(self):
        for supported, display, expected in ((False, True, 2), (True, False, 1)):
            library, events = FakeEgl(supported=supported, display=display), []
            self.assertEqual(egl.perform_api(library, events.append), expected)
            self.assertNotIn("initialize", library.calls);self.assertNotIn("terminate", library.calls)
            self.assertFalse(events[-1]["result"]["called"])
            for event in events: egl.decode_event(json.dumps(event))

    def test_missing_ext_symbol_does_not_leak_exception_text(self):
        library, events = FakeEgl(core=False), []
        library.eglGetProcAddress = Function(lambda _: None)
        with self.assertRaisesRegex(InstallError, "^EGL_SYMBOL_MISSING$"):
            egl.perform_api(library, events.append)
        self.assertEqual(events, [dict(phase="egl_cleanup", result=dict(called=False, returned=None, error=None))])
        with patch.object(egl, "termux_home", return_value=Path("/unused")), patch.object(egl, "validate_runtime", return_value={}), \
             patch.object(egl.ctypes, "CDLL", side_effect=OSError("PRIVATE /some/path symbol")):
            output = io.StringIO()
            with contextlib.redirect_stdout(output): self.assertEqual(egl.child_main(), 3)
        self.assertEqual(json.loads(output.getvalue()), dict(phase="blocked", reason="EGL_DLOPEN_FAILED"))

    def test_extra_or_raw_stdout_is_rejected(self):
        bad = api_events()[0]
        bad["result"]["private"] = "PRIVATE"
        for value in (b"PRIVATE /path", json.dumps(bad).encode(), b'{"phase":"blocked","reason":"PRIVATE"}'):
            with self.assertRaises(InstallError): egl.decode_event(value)


class LoadedIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix="egl-loaded-");self.addCleanup(self.temporary.cleanup)
        self.directory=Path(self.temporary.name);self.pins={};self.before={};self.paths={};self.maps=[]
        for i,name in enumerate(loaded.NAMES):
            path=self.directory/name;data=(name+" fixture").encode();path.write_bytes(data);path.chmod(0o600);info=path.stat()
            self.pins[name]=hashlib.sha256(data).hexdigest();self.before[name]=dict(identity=list(loaded.file_identity(info)))
            self.paths[name]=[os.fsencode(path)]
            self.maps.append(f"{65536+i*8192:x}-{69632+i*8192:x} r-xp 00000000 {os.major(info.st_dev):x}:{os.minor(info.st_dev):x} {info.st_ino} {path}\n".encode())
        self.maps.append(b"1234-5678 rw-p 0000 00:00 0 PRIVATE /unrelated\n")
    def capture(self,**kwargs):
        return loaded.capture(self.directory,self.before,self.pins,expected_uid=os.getuid(),enumerator=lambda _:self.paths,maps_reader=lambda:b"".join(self.maps),**kwargs)
    def test_fixed_images_match_linker_mapping_inode_owner_and_hash_without_addresses(self):
        report=self.capture();self.assertEqual(report["status"],"verified");self.assertEqual([r["name"] for r in report["libraries"]],list(loaded.NAMES))
        self.assertNotIn("PRIVATE",json.dumps(report));self.assertNotIn("address",json.dumps(report))
        self.assertEqual(report["libraries"][0]["inode"],(self.directory/loaded.NAMES[0]).stat().st_ino)
    def test_wrong_mapping_inode_and_duplicate_linker_image_are_not_verified(self):
        row=self.maps[0].split();row[4]=b"1";original=self.maps[0];self.maps[0]=b" ".join(row)+b"\n"
        self.assertEqual(self.capture()["reason"],"LOADED_MAPS_MISMATCH");self.maps[0]=original
        self.paths[loaded.NAMES[0]]*=2;self.assertEqual(self.capture()["reason"],"LOADED_LIBRARY_DUPLICATE")
    def test_deleted_relative_unknown_path_and_missing_image_never_use_expected_path_as_fallback(self):
        self.paths[loaded.NAMES[0]]=[b"PRIVATE/"+loaded.NAMES[0].encode()]
        report=self.capture();self.assertEqual(report["reason"],"LOADED_LIBRARY_PATH_CHANGED");self.assertNotIn("PRIVATE",json.dumps(report))
        self.paths[loaded.NAMES[0]]=[os.fsencode(self.directory/loaded.NAMES[0])];original=self.maps[0]
        self.maps[0]=original[:-1]+b" (deleted)\n";self.assertEqual(self.capture()["reason"],"LOADED_LIBRARY_DELETED")
        self.maps[0]=original;self.paths[loaded.NAMES[1]]=[];report=self.capture()
        self.assertEqual(report["reason"],"LOADED_LIBRARY_MISSING");self.assertEqual(len(report["libraries"]),1)
    def test_hash_identity_mode_uid_and_symlink_fail_closed(self):
        name=loaded.NAMES[0];target=self.directory/name
        self.pins[name]="f"*64;self.assertEqual(self.capture()["reason"],"LOADED_FILE_CHANGED")
        self.pins[name]=hashlib.sha256(target.read_bytes()).hexdigest();target.chmod(0o666)
        self.assertEqual(self.capture()["reason"],"LOADED_FILE_UNSAFE");target.chmod(0o600)
        report=loaded.capture(self.directory,self.before,self.pins,expected_uid=os.getuid()+1,enumerator=lambda _:self.paths,maps_reader=lambda:b"".join(self.maps))
        self.assertEqual(report["reason"],"LOADED_FILE_UNSAFE")
        target.unlink();target.symlink_to(self.directory/loaded.NAMES[1]);self.assertEqual(self.capture()["status"],"not_verified")
    def test_truncated_huge_and_invalid_maps_are_not_complete(self):
        for raw in (b"".join(self.maps)[:-1],b"x"*(loaded.MAX_MAPS+1),self.maps[0].replace(b"r-xp",b"INVALID")):
            with self.assertRaises(InstallError):loaded.select_maps(raw)
    def test_mapping_change_during_hash_cannot_pass(self):
        calls=[]
        def read():
            calls.append(True);return b"".join(self.maps) if len(calls)==1 else b""
        result=loaded.capture(self.directory,self.before,self.pins,expected_uid=os.getuid(),enumerator=lambda _:self.paths,maps_reader=read)
        self.assertEqual(result["reason"],"LOADED_MAPS_MISMATCH")
    def test_actual_public_phdr_prefix_enumeration_is_bounded_and_selective(self):
        result=loaded.enumerate_loaded(("libc.so.6",))
        self.assertEqual(set(result),{"libc.so.6"});self.assertEqual(len(result["libc.so.6"]),1)
    def test_wire_schema_rejects_arbitrary_path_address_hash_and_extra_library(self):
        for change in (lambda r:r.update(address=1234),lambda r:r.update(canonical_path="PRIVATE"),lambda r:r.update(sha256="f"*64),lambda r:r.update(name="PRIVATE")):
            value=loaded_event();change(value["result"]["libraries"][0])
            with self.assertRaises(InstallError):egl.decode_event(json.dumps(value))
        value=loaded_event();value["result"]["libraries"].pop()
        with self.assertRaises(InstallError):egl.decode_event(json.dumps(value))


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="egl-runtime-");self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home, self.lib = self.root / "home", self.root / "usr/lib"
        self.home.mkdir(mode=0o700);self.lib.mkdir(mode=0o700, parents=True)
        self.pins = {}
        for name in egl.LIBRARIES:
            data = (name + " fixed fixture").encode();target = self.lib / name;target.write_bytes(data);target.chmod(0o600)
            self.pins[name] = hashlib.sha256(data).hexdigest()
        for name, target in egl.ALIASES.items(): (self.lib / name).symlink_to(target)
        self.vendor = self.root / "usr" / egl.VENDOR;self.vendor.parent.mkdir(mode=0o700, parents=True)
        self.vendor.write_text('{"file_format_version":"1.0.0","ICD":{"library_path":"libEGL_mesa.so.0"}}');self.vendor.chmod(0o600)

    def validate(self): return egl.validate_runtime(self.home, expected_uid=os.getuid(), pins=self.pins)

    def test_kopper_disabled_variant_adds_exactly_one_env_and_baseline_does_not_inherit_it(self):
        directory=self.root/'probe'
        with patch.dict(os.environ,{'LIBGL_KOPPER_DISABLE':'PRIVATE','MESA_LOADER_DRIVER_OVERRIDE':'PRIVATE'}):
            baseline=egl.fixed_environment(self.home,directory)
            disabled=egl.fixed_environment(self.home,directory,'kopper-disabled')
        self.assertEqual(disabled,dict(baseline,LIBGL_KOPPER_DISABLE='1'))
        self.assertNotIn('LIBGL_KOPPER_DISABLE',baseline);self.assertNotIn('MESA_LOADER_DRIVER_OVERRIDE',baseline)
        for variant in ('PRIVATE','LIBGL_KOPPER_DISABLE=1',{'env':'PRIVATE'}):
            with self.assertRaises(InstallError):egl.fixed_environment(self.home,directory,variant)

    def test_pins_owner_and_exact_aliases(self):
        checked = self.validate()
        self.assertEqual(set(checked), set(egl.LIBRARIES) | {"vendor_json"})
        with self.assertRaisesRegex(InstallError, "UID_MISMATCH"):
            egl.validate_runtime(self.home, expected_uid=os.getuid() + 1, pins=self.pins)
        (self.lib / "libEGL.so.1").unlink();(self.lib / "libEGL.so.1").symlink_to("libEGL_mesa.so")
        with self.assertRaisesRegex(InstallError, "RUNTIME_ALIAS_CHANGED"): self.validate()

    def test_hash_mode_symlink_and_unsafe_directory_rejected(self):
        target = self.lib / "libEGL_mesa.so"
        original = target.read_bytes();target.write_bytes(b"CHANGED")
        with self.assertRaisesRegex(InstallError, "RUNTIME_LIBRARY_CHANGED"): self.validate()
        target.write_bytes(original);target.chmod(0o666)
        with self.assertRaisesRegex(InstallError, "RUNTIME_LIBRARY_UNSAFE"): self.validate()
        target.chmod(0o600);self.lib.chmod(0o770)
        with self.assertRaisesRegex(InstallError, "RUNTIME_DIRECTORY_UNSAFE"): self.validate()
        self.lib.chmod(0o700);target.unlink();target.symlink_to("libc++_shared.so")
        with self.assertRaises(OSError): self.validate()

    def test_vendor_cannot_select_another_library_or_duplicate_field(self):
        for content in ('{"file_format_version":"1.0.0","ICD":{"library_path":"PRIVATE"}}',
                        '{"file_format_version":"1.0.0","ICD":{"library_path":"PRIVATE","library_path":"libEGL_mesa.so.0"}}'):
            self.vendor.write_text(content)
            with self.assertRaises(InstallError): self.validate()

    def test_fixed_environment_has_no_native_display_or_inherited_env(self):
        directory = egl.private_run(self.home, "a" * 32)
        environment = egl.fixed_environment(self.home, directory)
        self.assertEqual(environment["GALLIUM_DRIVER"], "softpipe")
        self.assertEqual(environment["LIBGL_ALWAYS_SOFTWARE"], "1")
        self.assertEqual(environment["__EGL_VENDOR_LIBRARY_FILENAMES"], str(self.vendor))
        self.assertFalse(set(environment) & {"WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DISPLAY", "EGL_PLATFORM", "LD_PRELOAD"})
        with self.assertRaises(FileExistsError): egl.private_run(self.home, "a" * 32)


class OwnedProcessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="egl-owned-");self.addCleanup(self.temporary.cleanup)
        self.ready = Path(self.temporary.name) / "ready"

    def command(self, *, exit_code=1, sleep=False, ignore_term=False, descendant=False, raw=False):
        data = b"\n".join(json.dumps(v).encode() for v in api_events()) + b"\n"
        if raw: data = b"PRIVATE /raw/path\n"
        source = ("import os,signal,time\nfrom pathlib import Path\n"
                  + ("signal.signal(signal.SIGTERM,signal.SIG_IGN)\n" if ignore_term else "")
                  + f"os.write(1,{data!r})\nos.write(2,b'dri_fill_in_modes: driCreateConfigs failed\\nPRIVATE /raw/path symbol\\n')\n"
                  + ("pid=os.fork()\nif pid==0:time.sleep(30);os._exit(0)\n" if descendant else "")
                  + f"Path({str(self.ready)!r}).write_text('ready')\n"
                  + ("time.sleep(30)\n" if sleep else f"raise SystemExit({exit_code})\n"))
        return [sys.executable, "-c", source]

    def observe(self, command, **kwargs):
        read_fd, write_fd = os.pipe()
        try:
            return egl.observe_owned(command, dict(PATH=os.environ["PATH"]), read_fd, kwargs.pop("checker", lambda: None),
                                     lambda _: None, timeout=kwargs.pop("timeout", 2), grace=.1, **kwargs)
        finally: os.close(read_fd);os.close(write_fd)

    def test_negative_api_natural_exit_is_diagnosed_and_raw_stderr_discarded(self):
        result = self.observe(self.command())
        self.assertEqual(result["status"], "diagnosed")
        self.assertEqual(result["child_returncode"], 1)
        self.assertTrue(result["cleanup_confirmed"])
        self.assertFalse(result["term_sent"] or result["kill_sent"])
        self.assertEqual(result["api"]["initialize"]["error"]["value"], 0x3001)
        self.assertEqual(result["egl_log"]["known"], {"dri_config_generation_failed": 1})
        self.assertEqual(result["egl_log"]["unmatched_lines"], 1)
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_loaded_mismatch_keeps_original_egl_error_return_code_and_cleanup(self):
        data=b"\n".join(json.dumps(v).encode() for v in api_events(verified=False))+b"\n"
        result=self.observe([sys.executable,"-c",f"import os;os.write(1,{data!r});raise SystemExit(1)"])
        self.assertEqual(result["status"],"failed");self.assertEqual(result["child_returncode"],1)
        self.assertEqual(result["api"]["initialize"]["error"]["value"],0x3001)
        self.assertEqual(result["loaded_libraries"]["reason"],"LOADED_MAPS_MISMATCH");self.assertTrue(result["cleanup_confirmed"])

    def test_original_failure_and_invalid_stdout_are_not_hidden(self):
        result = self.observe(self.command(exit_code=7))
        self.assertEqual(result["child_returncode"], 7);self.assertEqual(result["status"], "failed")
        self.assertEqual(result["api"]["initialize"]["error"]["value"], 0x3001)
        result = self.observe(self.command(raw=True))
        self.assertEqual(result["status"], "failed");self.assertEqual(result["error"], "CHILD_OUTPUT_INVALID")
        self.assertTrue(result["cleanup_confirmed"]);self.assertNotIn("PRIVATE", json.dumps(result))

    def test_timeout_reclaims_only_actual_group_and_retains_first_api_error(self):
        unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], start_new_session=True)
        try:
            result = self.observe(self.command(sleep=True, ignore_term=True, descendant=True), timeout=.15)
            self.assertTrue(result["timed_out"] and result["term_sent"] and result["kill_sent"])
            self.assertTrue(result["cleanup_confirmed"])
            self.assertEqual(result["child_returncode"], -signal.SIGKILL)
            self.assertEqual(result["api"]["initialize"]["error"]["value"], 0x3001)
            self.assertIsNone(unrelated.poll())
        finally: unrelated.terminate();unrelated.wait(timeout=2)

    def test_signal_after_api_flush_preserves_error_without_faking_egl_cleanup(self):
        data = json.dumps(api_events()[0]).encode() + b"\n"
        result = self.observe([sys.executable, "-c", f"import os,signal;os.write(1,{data!r});os.kill(os.getpid(),signal.SIGTERM)"])
        self.assertEqual(result["child_returncode"], -signal.SIGTERM)
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["egl_cleanup"])
        self.assertEqual(result["api"]["initialize"]["error"]["value"], 0x3001)
        self.assertTrue(result["cleanup_confirmed"])

    def test_stdout_cap_discards_content_and_cancels_owned_group(self):
        result = self.observe([sys.executable, "-c", "import os,time;os.write(1,b'PRIVATE'*8192);time.sleep(30)"])
        self.assertEqual(result["cancel_reason"], "OUTPUT_LIMIT")
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["cleanup_confirmed"])
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_host_cancel_runtime_change_and_eof(self):
        read_fd, write_fd = os.pipe()
        def cancel():
            until = time.monotonic() + 2
            while not self.ready.exists() and time.monotonic() < until: time.sleep(.01)
            os.write(write_fd, b'{"type":"cancel"}\n')
        worker = threading.Thread(target=cancel);worker.start()
        try:
            result = egl.observe_owned(self.command(sleep=True), dict(PATH=os.environ["PATH"]), read_fd, lambda: None,
                                       lambda _: None, timeout=2, grace=.1)
            worker.join(timeout=2)
            self.assertEqual(result["cancel_reason"], "HOST_CANCEL");self.assertTrue(result["cleanup_confirmed"])
        finally: os.close(read_fd);os.close(write_fd)
        def changed():
            if self.ready.exists(): raise InstallError("RUNTIME_LIBRARY_CHANGED")
        result = self.observe(self.command(sleep=True), checker=changed)
        self.assertEqual(result["cancel_reason"], "RUNTIME_CHANGED");self.assertTrue(result["cleanup_confirmed"])
        read_fd, write_fd = os.pipe();os.close(write_fd)
        try:
            result = egl.observe_owned(self.command(sleep=True), dict(PATH=os.environ["PATH"]), read_fd, lambda: None,
                                       lambda _: None, timeout=2, grace=.1)
            self.assertEqual(result["cancel_reason"], "CONTROL_EOF");self.assertTrue(result["cleanup_confirmed"])
        finally: os.close(read_fd)


class CliTests(unittest.TestCase):
    def test_fixed_remote_code_is_bounded_and_contains_no_wayland_runner(self):
        import shlex
        source = cli.remote_source()
        compile(source, "remote-egl", "exec")
        self.assertLessEqual(len(shlex.join(["run-as", "com.termux", "/data/data/com.termux/files/usr/bin/python3", "-c", source]).encode()), 49 * 1024)
        compile(cli.remote_program(), "remote-program", "exec")
        self.assertNotIn("class WaylandTrace:", cli.remote_program())
        self.assertNotIn("sdk_slice", cli.remote_program())
        self.assertEqual(set(cli.source_hashes()), set(cli.SOURCES))

    def test_remote_early_exit_and_broken_stdin_preserve_result_file(self):
        class Device:
            def __init__(self, *args): pass
            def validate(self): return dict(model="Quest 3", api=34)
            def adb(self, *args): return "package:com.termux uid:10158\n"
        with tempfile.TemporaryDirectory(prefix="egl-host-") as directory:
            original = subprocess.Popen
            def spawn(*args, **kwargs):
                return original([sys.executable, "-c", "import os;os.close(0);raise SystemExit(1)"], **kwargs)
            with patch.object(cli.argparse.ArgumentParser, "parse_args", return_value=argparse.Namespace(serial="fixture", artifact_root=Path(directory), variant="baseline")), \
                 patch.object(cli, "QuestDevice", Device), patch.object(cli.subprocess, "Popen", side_effect=spawn), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(), 1)
            reports = list(Path(directory).glob("*/result.json"))
            self.assertEqual(len(reports), 1)
            report = json.loads(reports[0].read_text())
            self.assertEqual(report["remote_returncode"], 1)
            self.assertEqual(report["status"], "failed")
            self.assertNotIn("remote_cleanup_unconfirmed", report)


if __name__ == "__main__": unittest.main()
