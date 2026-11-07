import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gtk_probe_run as gtk
from gui_probe_sdk import summarize_first_frame
from sdl_lease_capture import CaptureError

loader = importlib.machinery.SourceFileLoader('gtk_cli', str(Path(__file__).resolve().parents[1] / 'quest-run-gtk-probe'))
spec = importlib.util.spec_from_loader(loader.name, loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)

TRACE = ('xdg_wm_base@4.get_xdg_surface(new id xdg_surface@5, wl_surface@6)\n'
         'xdg_surface@5.get_toplevel(new id xdg_toplevel@7)\n'
         'wl_shm_pool@8.create_buffer(new id wl_buffer@9, 0, 640, 400, 2560, 0)\n'
         'wl_surface@6.attach(wl_buffer@9, 0, 0)\nwl_surface@6.commit()\n')


class GtkProbeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='eidolon-gtk-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.ready = self.root / 'ready'

    def command(self, *, trace=TRACE, exit_code=None, ignore_term=False, descendant=False, flood=False):
        source = ('import os,signal,sys,time\nfrom pathlib import Path\n'
                  + ('signal.signal(signal.SIGTERM,signal.SIG_IGN)\n' if ignore_term else '')
                  + f'os.write(2,{trace.encode()!r})\nos.write(1,b"PRIVATE GTK OUTPUT")\n'
                  + ('pid=os.fork()\nif pid==0:time.sleep(30);os._exit(0)\n' if descendant else '')
                  + f'Path({str(self.ready)!r}).write_text("ready")\n'
                  + (f'raise SystemExit({exit_code})\n' if exit_code is not None else '')
                  + ('while True:os.write(1,b"PRIVATE"*8192)\n' if flood else 'time.sleep(30)\n'))
        return [sys.executable, '-c', source]

    def observe(self, command, **kwargs):
        read_fd, write_fd = os.pipe()
        try:
            return gtk.observe_owned(command, dict(PATH=os.environ['PATH']), read_fd,
                                     kwargs.pop('checker', lambda: None), lambda _: None,
                                     observation_seconds=kwargs.pop('observation_seconds', .15),
                                     grace_seconds=.1, **kwargs)
        finally:
            os.close(read_fd);os.close(write_fd)

    def test_static_one_commit_is_observed_then_signal_is_recorded(self):
        result = self.observe(self.command())
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(result['trace']['content_shm_commits'], 1)
        self.assertEqual(result['stop_reason'], 'OBSERVATION_COMPLETE')
        self.assertEqual(result['termination'], 'signal')
        self.assertEqual(result['termination_signal'], signal.SIGTERM)
        self.assertEqual(result['child_returncode'], -signal.SIGTERM)
        self.assertTrue(result['term_sent'])
        self.assertFalse(result['kill_sent'])
        self.assertTrue(result['cleanup_confirmed'])
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_early_failure_keeps_original_exit_and_cannot_be_observed(self):
        result = self.observe(self.command(exit_code=7), observation_seconds=1)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['child_returncode'], 7)
        self.assertEqual(result['termination'], 'natural')
        self.assertEqual(result['stop_reason'], 'EARLY_EXIT')
        self.assertTrue(result['cleanup_confirmed'])

    def test_forced_kill_reclaims_owned_descendant_and_preserves_other_group(self):
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], start_new_session=True)
        try:
            result = self.observe(self.command(ignore_term=True, descendant=True))
            self.assertTrue(result['term_sent'] and result['kill_sent'])
            self.assertEqual(result['termination_signal'], signal.SIGKILL)
            self.assertTrue(result['cleanup_confirmed'])
            self.assertIsNone(unrelated.poll())
        finally:
            unrelated.terminate();unrelated.wait(timeout=2)

    def test_lease_change_and_stdout_limit_are_not_success(self):
        def changed():
            if self.ready.exists(): raise CaptureError('LEASE_CHANGED')
        result = self.observe(self.command(), checker=changed, observation_seconds=1)
        self.assertEqual(result['cancel_reason'], 'LEASE_CHANGED')
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['cleanup_confirmed'])
        result = self.observe(self.command(flood=True), observation_seconds=1)
        self.assertEqual(result['cancel_reason'], 'OUTPUT_LIMIT')
        self.assertEqual(result['shm_gate'], 'fail')
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_host_cancel_terminates_only_owned_program(self):
        read_fd, write_fd = os.pipe()
        def cancel():
            until = time.monotonic() + 2
            while not self.ready.exists() and time.monotonic() < until: time.sleep(.01)
            os.write(write_fd, b'{"type":"cancel"}\n')
        worker = threading.Thread(target=cancel);worker.start()
        try:
            result = gtk.observe_owned(self.command(), dict(PATH=os.environ['PATH']), read_fd, lambda: None, lambda _: None,
                                       observation_seconds=2, grace_seconds=.1)
            worker.join(timeout=3)
            self.assertEqual(result['cancel_reason'], 'HOST_CANCEL')
            self.assertEqual(result['status'], 'failed')
            self.assertTrue(result['cleanup_confirmed'])
        finally:
            os.close(read_fd);os.close(write_fd)

    def test_missing_or_non_shm_content_and_protocol_error_cannot_pass(self):
        for trace in ('', TRACE.replace('wl_shm_pool@8.create_buffer', 'unknown@8.create_buffer'),
                      TRACE + 'wl_display@1.error(wl_surface@6, 1, "PRIVATE")\n'):
            with self.subTest(trace=trace[:10]):
                result = self.observe(self.command(trace=trace))
                self.assertEqual(result['status'], 'failed')
                self.assertNotIn('PRIVATE', json.dumps(result))

    def test_invalid_control_fd_never_spawns(self):
        with patch.object(gtk.subprocess, 'Popen') as spawn, self.assertRaises(OSError):
            gtk.observe_owned(self.command(), {}, -1, lambda: None, lambda _: None)
        spawn.assert_not_called()

    def test_exec_identity_mismatch_cancels_spawned_program(self):
        result = self.observe(self.command(), executable_identity=(0, 0, 0, 0, 0))
        self.assertEqual(result['error'], 'GTK_EXEC_CHANGED')
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['cleanup_confirmed'])

    def test_hash_owner_mode_package_and_symlink_gates(self):
        home = self.root / 'files/home';home.mkdir(parents=True, mode=0o700)
        binary = self.root / 'files/usr/bin/gtk3-widget-factory';binary.parent.mkdir(parents=True)
        binary.write_bytes(b'fixed gtk fixture');binary.chmod(0o700)
        expected = hashlib.sha256(binary.read_bytes()).hexdigest()
        package = SimpleNamespace(returncode=0, stdout=b'installed:3.24.52', stderr=b'')
        with patch.multiple(gtk, GTK_UID=os.getuid(), GTK_BYTES=binary.stat().st_size, GTK_SHA256=expected), \
                patch.object(gtk.subprocess, 'run', return_value=package):
            self.assertEqual(gtk.validate_target(home)[0], binary)
            binary.write_bytes(b'changed gtk file!')
            with self.assertRaisesRegex(CaptureError, 'GTK_BINARY_CHANGED'): gtk.validate_target(home)
            binary.write_bytes(b'fixed gtk fixture');binary.chmod(0o770)
            with self.assertRaisesRegex(CaptureError, 'ARTIFACT_UNSAFE'): gtk.validate_target(home)
            binary.chmod(0o700);package.stdout = b'installed:other'
            with self.assertRaisesRegex(CaptureError, 'GTK_PACKAGE_CHANGED'): gtk.validate_target(home)
            binary.unlink();binary.symlink_to(home)
            with self.assertRaises(OSError): gtk.validate_target(home)

    def test_private_state_nonoverwrite_and_fixed_environment(self):
        home = self.root / 'home';home.mkdir(mode=0o700)
        directory = gtk.private_run(home, 'a' * 32)
        with self.assertRaises(FileExistsError): gtk.private_run(home, 'a' * 32)
        captured = dict(environment=dict(WAYLAND_DISPLAY='/owned/w', XDG_RUNTIME_DIR='/owned'))
        with patch.dict(os.environ, dict(PRIVATE_SECRET='value')):
            env = gtk.fixed_environment(home, directory, captured)
        self.assertEqual(env['GDK_BACKEND'], 'wayland')
        self.assertEqual(env['GDK_GL'], 'disable')
        self.assertEqual(env['GDK_RENDERING'], 'image')
        self.assertEqual(env['HOME'], str(directory / 'home'))
        self.assertNotIn('PRIVATE_SECRET', env)
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        captured['environment']['LD_PRELOAD'] = 'other'
        with self.assertRaisesRegex(CaptureError, 'GTK_ENV_INVALID'): gtk.fixed_environment(home, directory, captured)

    def test_operator_persists_only_new_report_and_keeps_fixed_scope(self):
        home = self.root / 'files/home';home.mkdir(parents=True, mode=0o700)
        target = self.root / 'files/usr/bin/gtk3-widget-factory'
        captured = dict(environment=dict(WAYLAND_DISPLAY='/owned/w', XDG_RUNTIME_DIR='/owned'))
        original = dict(status='observed', child_returncode=-15, cleanup_confirmed=True, shm_gate='pass')
        events = []
        request = dict(binding=dict(uid=os.getuid(), session='7'), probe_id='b' * 32)
        with patch.object(gtk, 'GTK_UID', os.getuid()), patch.object(gtk, 'capture_remote', return_value=captured), \
                patch.object(gtk, 'validate_target', return_value=(target, (1, 2, 3, 4, 5))) as validate, \
                patch.object(gtk, 'live_binding'), patch.object(gtk, 'observe_owned', return_value=original.copy()) as observe:
            result = gtk.run_operator(request, home, -1, events.append)
            self.assertEqual(validate.call_count, 2)
            self.assertEqual(observe.call_args.args[0], [str(target)])
            self.assertEqual(observe.call_args.args[1]['GDK_GL'], 'disable')
            self.assertEqual(result['child_returncode'], -15)
            self.assertEqual(result['input'], 'not_run')
            self.assertEqual(result['dialog'], 'not_run')
            self.assertEqual(result['resize'], 'not_run')
            path = home / '.local/state/eidolon/nx15' / request['probe_id'] / 'report.json'
            self.assertEqual(json.loads(path.read_text()), result)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError): gtk.run_operator(request, home, -1, events.append)
        self.assertEqual(events[0]['phase'], 'validated')
        self.assertEqual(events[-1]['phase'], 'finished')

    def test_arbitrary_request_fields_are_rejected_before_capture(self):
        with patch.object(gtk, 'capture_remote') as capture, self.assertRaisesRegex(CaptureError, 'GTK_REQUEST_INVALID'):
            gtk.run_operator(dict(binding={}, probe_id='a' * 32, executable='/other'), self.root, -1, lambda _: None)
        capture.assert_not_called()

    def test_first_frame_requires_own_new_key_retirement_and_continuous_foot(self):
        base = dict(sequence='1', mapped=['1'], targets=[])
        one = dict(sequence='2', mapped=['1', '2'], targets=[dict(window='2', scene='1', generation='2', frames=1, content_frame='1')])
        end = dict(sequence='3', mapped=['1'], targets=[])
        self.assertEqual(summarize_first_frame(base, [one, one, end])['status'], 'observed')
        self.assertEqual(summarize_first_frame(base, [one])['status'], 'not_verified')
        lost = dict(sequence='3', mapped=['2'], targets=[])
        self.assertEqual(summarize_first_frame(base, [one, lost, end])['status'], 'not_verified')
        self.assertEqual(summarize_first_frame({**base, 'targets': one['targets']}, [one, end])['status'], 'not_verified')
        black = {**one, 'targets': [{**one['targets'][0], 'content_frame': '0'}]}
        self.assertEqual(summarize_first_frame(base, [black, end])['status'], 'not_verified')

    def test_remote_composition_and_cli_expose_no_free_exec_or_env(self):
        source = cli.remote_source()
        compile(source, '<gtk-remote>', 'exec')
        self.assertLess(len(source.encode()), 120000)
        result = subprocess.run([sys.executable, cli.__file__, '--serial', 'unused', '--binary', '/other'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('unrecognized arguments', result.stderr)


if __name__ == '__main__':
    unittest.main()
