"""NX10: 任意argvの実PTY実行、起動失敗の分離、leaseに属する一時定義の回収。"""
import errno
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import pty
import select
import socket
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'eidolon-session.py'
spec = importlib.util.spec_from_file_location('application_launcher', SCRIPT)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class ApplicationLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='eidolon-app-')
        self.addCleanup(self.temp.cleanup)
        self.home, self.nonce = Path(self.temp.name), 'a' * 16
        self.script = self.home / '日本語 app; name.py'
        self.script.write_text('import json,os,sys\nprint(json.dumps(dict(args=sys.argv[1:], cwd=os.getcwd(), tty=os.isatty(0), pid=os.getpid(), terminal=os.environ.get("EIDOLON_TERMINAL_ID"))))\nsys.exit(7)\n')
        self.app = dict(id='custom', label='Custom', mode='terminal', executable=self.script.name,
                        interpreter=sys.executable, args=['space arg', '日本語', '', '$(touch sentinel)', ';touch sentinel'], cwd='.')
        self.catalog = launcher.LauncherCatalog(self.home)

    def prepare(self):
        return Path(launcher.prepare(self.home, self.nonce)['socket']).parent

    def test_selected_target_checks_revision_and_temporary_keeps_argv(self):
        selected = self.catalog.launch_definition(dict(kind='temporary', definition=self.app))
        self.assertEqual(selected['args'], self.app['args'])
        self.assertIsNone(self.catalog.path)
        self.catalog.save('launcher.toml', None, self.app)
        self.assertEqual(self.catalog.launch_definition(dict(kind='registered', revision=self.catalog.revision, id='custom')), selected)
        (self.home / 'launcher.toml').write_text('schema = 1\napps = []\n')
        with self.assertRaisesRegex(launcher.CatalogError, 'CATALOG_CHANGED'):
            self.catalog.launch_definition(dict(kind='registered', revision=self.catalog.revision, id='custom'))
        with self.assertRaisesRegex(launcher.CatalogError, 'GUI_NOT_VERIFIED'):
            self.catalog.launch_definition(dict(kind='temporary', definition={**self.app, 'mode': 'gui'}))

    def test_stage_is_private_exclusive_and_creator_cleanup_removes_it(self):
        directory = self.prepare()
        launcher.terminal_application_write(directory, '1', self.app)
        path = directory / 'a-1.json'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            launcher.terminal_application_write(directory, '1', self.app)
        self.assertEqual(launcher.terminal_application_read(directory, '1')['args'], self.app['args'])
        self.assertFalse(launcher.cleanup(self.home, self.nonce)['cleaned'])
        self.assertTrue(launcher.cleanup(self.home, self.nonce, creator=True)['cleaned'])
        self.assertFalse(directory.exists())

    def test_stage_symlink_and_mismatched_nonce_are_never_consumed(self):
        directory = self.prepare()
        path = directory / 'a-1.json'
        path.symlink_to(self.script)
        with self.assertRaises(OSError): launcher.terminal_application_read(directory, '1', consume=True)
        self.assertTrue(self.script.exists())
        path.unlink()
        launcher.terminal_application_write(directory, '1', self.app)
        value = json.loads(path.read_bytes())
        value['nonce'] = 'b' * 16
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'TERMINAL_INVALID'):
            launcher.cleanup(self.home, self.nonce, creator=True)
        self.assertTrue(path.exists())
        self.assertTrue((directory / 'owner.json').exists())

    def test_real_pty_exec_consumes_definition_keeps_identity_and_exit_status(self):
        directory = self.prepare()
        launcher.terminal_application_write(directory, '12', self.app)
        channel = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        channel.settimeout(3)
        channel.bind(str(directory / 'm'))
        master, slave = pty.openpty()
        source = (f'import importlib.util,pathlib; s=importlib.util.spec_from_file_location("launcher",{str(SCRIPT)!r});'
                  'm=importlib.util.module_from_spec(s);s.loader.exec_module(m);'
                  f'm.TERMUX_HOME=pathlib.Path({str(self.home)!r});m._direct_exec_supported=True;'
                  f'm.terminal_shell(pathlib.Path({str(self.home)!r}),{self.nonce!r},"12","application")')
        def terminal():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        child = subprocess.Popen([sys.executable, '-c', source], stdin=slave, stdout=slave, stderr=slave,
                                 preexec_fn=terminal, close_fds=True)
        os.close(slave)
        output = bytearray()
        try:
            registration = json.loads(channel.recv(2048))
            self.assertEqual(registration['pid'], child.pid)
            self.assertEqual(registration['terminal_id'], '12')
            deadline = time.monotonic() + 4
            while True:
                self.assertLess(time.monotonic(), deadline)
                if select.select([master], [], [], .05)[0]:
                    try: data = os.read(master, 8192)
                    except OSError as error:
                        if error.errno == errno.EIO: break
                        raise
                    if not data: break
                    output.extend(data)
            self.assertEqual(child.wait(timeout=2), 7)
            result = json.loads(output.decode().strip())
            self.assertEqual(result, dict(args=self.app['args'], cwd=str(self.home), tty=True, pid=child.pid, terminal='12'))
            self.assertFalse((directory / 'a-12.json').exists())
            self.assertFalse((self.home / 'sentinel').exists())
            command = launcher.terminal_command(directory, 'foot', '12', application=True)
            self.assertIn('--hold', command)
            self.assertIn('--no-wait', command)
            self.assertIn('terminal-app', command)
            self.assertNotIn('space arg', command)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=2)
            os.close(master)
            channel.close()
        self.assertTrue(launcher.cleanup(self.home, self.nonce, creator=True)['cleaned'])

    def supervise(self, *, stop_pending=False, spawn_fails=False):
        read, write = os.pipe()
        events, threads = [], []
        directory = self.home / '.eidolon' / ('s-' + self.nonce)
        def send(kind, **fields):
            os.write(write, (json.dumps(dict(type=kind, nonce=self.nonce, **fields), ensure_ascii=False) + '\n').encode())
        def application(terminal, request, definition):
            send('open_terminal_v2', schema=1, session='1', terminal_id=terminal, request_id=request,
                 target=dict(kind='temporary', definition=definition))
        def emit(value):
            events.append(value)
            if value['type'] == 'prepared': send('inspect_tools', mode='foot')
            elif value['type'] == 'tools': send('start_terminal_v1', schema=1, session='1', terminal_id='1', mode='foot')
            elif value['type'] == 'terminal_opened':
                if value['request_id'] is None:
                    if stop_pending:
                        application('2', '100', self.app)
                        def stop():
                            deadline = time.monotonic() + 2
                            while not (self.home / 'client-pid').exists() and time.monotonic() < deadline: time.sleep(.005)
                            send('stop')
                        thread = threading.Thread(target=stop)
                        threads.append(thread)
                        thread.start()
                    else:
                        application('2', '100', {**self.app, 'executable': 'absent'})
                elif value['request_id'] == '100':
                    self.assertEqual(value['error'], 'PATH_MISSING')
                    self.assertEqual(value['ordinal'], 1)
                    leader = next(event['child']['pid'] for event in events if event['type'] == 'child_started')
                    os.kill(leader, 0)
                    application('3', '101', self.app)
                else: send('stop')
        def factory(mode, nonce):
            return [sys.executable, '-c', f'import socket,time;s=socket.socket(socket.AF_UNIX);s.bind({str(directory / "f")!r});time.sleep(30)']
        def command(directory, mode, terminal, **options):
            if options.get('application') and spawn_fails: return ['/nonexistent/eidolon-test-footclient']
            source = (f'import os,time;open({str(self.home / "client-pid")!r},"w").write(str(os.getpid()));time.sleep(30)'
                      if options.get('application') and stop_pending else 'pass')
            return [sys.executable, '-c', source]
        try:
            with patch.object(launcher, 'inspect_tools', return_value={'mode': 'foot'}), patch.object(launcher, 'terminal_command', side_effect=command):
                result = launcher.supervise(self.home, self.nonce, read, emit, lease_seconds=3, grace_seconds=.1, factory=factory)
            for thread in threads:
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
            self.assertEqual(result['reason'], 'Stop')
            self.assertTrue(result['cleaned'], result)
            self.assertFalse(result['group_alive'])
            self.assertFalse(directory.exists())
            if stop_pending:
                client_pid = int((self.home / 'client-pid').read_text())
                with self.assertRaises(ProcessLookupError): os.kill(client_pid, 0)
            self.assertNotIn('space arg', json.dumps(events))
            return [event for event in events if event['type'] == 'terminal_opened']
        finally:
            os.close(read)
            os.close(write)

    def test_bad_definition_does_not_stop_existing_terminal_then_valid_request_is_accepted(self):
        results = self.supervise()
        self.assertEqual([result['success'] for result in results], [True, False, True])
        self.assertEqual([result['ordinal'] for result in results], [1, 1, 2])
        self.assertIsNone(results[-1]['error'])

    def test_footclient_spawn_failure_is_nonfatal_and_reclaims_definition(self):
        results = self.supervise(spawn_fails=True)
        self.assertFalse(results[-1]['success'])
        self.assertEqual(results[-1]['error'], 'PATH_UNAVAILABLE')

    def test_stop_during_pending_start_reaps_child_and_stage_but_not_unrelated_process(self):
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'])
        try:
            self.supervise(stop_pending=True)
            self.assertIsNone(unrelated.poll())
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=2)


if __name__ == '__main__':
    unittest.main()
