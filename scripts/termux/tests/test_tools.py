"""remote app起動前の版照合と、version子の出力上限・回収を検査する。"""
import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import time
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("tools_launcher", Path(__file__).resolve().parents[1] / "eidolon-session.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class ToolTests(unittest.TestCase):
    def test_direct_exec_probe_uses_a_second_exec_and_caches_success(self):
        with tempfile.TemporaryDirectory(prefix="eidolon-direct-exec-") as folder:
            root = Path(folder)
            (root / 'bin').mkdir()
            (root / 'bin/python3').symlink_to(sys.executable)
            with patch.object(launcher, 'TERMUX_PREFIX', root), patch.object(launcher, '_direct_exec_supported', None):
                self.assertTrue(launcher.termux_direct_exec_available(os.environ.copy()))
                with patch.object(launcher.subprocess, 'run', side_effect=AssertionError('probe repeated')):
                    self.assertTrue(launcher.termux_direct_exec_available(os.environ.copy()))

    def test_failed_or_timed_out_probe_does_not_disable_required_linker_exec(self):
        with tempfile.TemporaryDirectory(prefix="eidolon-direct-exec-fail-") as folder:
            root = Path(folder)
            (root / 'bin').mkdir()
            (root / 'bin/python3').symlink_to(sys.executable)
            for failure in [subprocess.CompletedProcess([], 1), subprocess.TimeoutExpired('probe', 1), OSError('denied')]:
                options = {'return_value': failure} if isinstance(failure, subprocess.CompletedProcess) else {'side_effect': failure}
                with self.subTest(failure=type(failure).__name__), patch.object(launcher, 'TERMUX_PREFIX', root), \
                        patch.object(launcher, '_direct_exec_supported', None), patch.dict(os.environ, {}, clear=True), \
                        patch.object(launcher.subprocess, 'run', **options):
                    env = launcher.tool_environment(root / 'runtime')
                    self.assertNotIn('TERMUX_EXEC__SYSTEM_LINKER_EXEC__MODE', env)

    def test_direct_exec_environment_preserves_preload_and_explicit_operator_modes(self):
        original = {'LD_PRELOAD': '/termux/libtermux-exec-ld-preload.so', 'TERMUX_EXEC__PROC_SELF_EXE': '/old/bash'}
        with patch.dict(os.environ, original, clear=True), patch.object(launcher, 'termux_direct_exec_available', return_value=True):
            env = launcher.tool_environment(Path('/runtime'))
            self.assertEqual(env['TERMUX_EXEC__SYSTEM_LINKER_EXEC__MODE'], 'disable')
            self.assertEqual(env['LD_PRELOAD'], original['LD_PRELOAD'])
            self.assertNotIn('TERMUX_EXEC__PROC_SELF_EXE', env)
        for mode in ['force', 'disable']:
            with self.subTest(mode=mode), patch.dict(os.environ, {**original, 'TERMUX_EXEC__SYSTEM_LINKER_EXEC__MODE': mode}, clear=True), \
                    patch.object(launcher, 'termux_direct_exec_available', side_effect=AssertionError('explicit mode probed')):
                env = launcher.tool_environment(Path('/runtime'))
                self.assertEqual(env['TERMUX_EXEC__SYSTEM_LINKER_EXEC__MODE'], mode)
                self.assertEqual(env['LD_PRELOAD'], original['LD_PRELOAD'])

    def test_terminal_request_is_single_bounded_and_owned_child_is_reaped(self):
        with tempfile.TemporaryDirectory(prefix="eidolon-terminal-") as folder:
            directory=Path(folder)
            server=socket.socket(socket.AF_UNIX)
            try:
                server.bind(str(directory/'f'))
                starter=launcher.TerminalStarter(directory,'foot')
                with patch.object(launcher,'terminal_command',return_value=[sys.executable,'-c','pass']):
                    starter.begin('17')
                    with self.assertRaisesRegex(ValueError,'CONTROL_INVALID'):starter.begin('18')
                    deadline=time.monotonic()+3
                    result=None
                    while result is None:
                        result=starter.tick()
                        self.assertLess(time.monotonic(),deadline)
                        time.sleep(.005)
                    self.assertEqual(result,dict(request_id='17',success=True,ordinal=1))
                    self.assertIsNone(starter.child)
                with patch.object(launcher,'terminal_command',return_value=[sys.executable,'-c','import time;time.sleep(30)']):
                    starter.begin('19');self.assertIsNone(starter.tick())
                    pid=starter.child.pid
                    starter.close()
                    with self.assertRaises(ProcessLookupError):os.kill(pid,0)
            finally:server.close()

    def test_version_mismatch_and_nonregular_producer_cannot_start_app(self):
        with tempfile.TemporaryDirectory(prefix="eidolon-tools-") as folder:
            root = Path(folder)
            directory = root / '.local/lib/eidolon'
            directory.mkdir(parents=True)
            producer = directory / 'eidolon-shm-fixture'
            producer.write_bytes(b"fixture ELF")
            def version(path):
                if path.name == 'waypipe':
                    return 'waypipe 0.11.3\nfeatures: lz4 zstd\nunavailable: dmabuf video vaapi\n'
                return json.dumps({'schema': 1, 'fixture_revision': 'eidolon-shm-client-v1', 'frames': 100})
            with patch.object(launcher, 'TERMUX_HOME', root), patch.object(launcher, 'tool_version', side_effect=version):
                result = launcher.inspect_tools('pattern')
                self.assertEqual(result['fixture']['sha256'], hashlib.sha256(producer.read_bytes()).hexdigest())
                producer.unlink()
                producer.symlink_to(root / 'absent')
                with self.assertRaisesRegex(ValueError, 'DEPENDENCY_NOT_READY'):
                    launcher.inspect_tools('pattern')
            with patch.object(launcher, 'tool_version', return_value='waypipe 0.12.0\n'):
                with self.assertRaisesRegex(ValueError, 'DEPENDENCY_NOT_READY'):
                    launcher.inspect_tools('foot')

    def test_version_stdout_flood_is_bounded_and_child_is_reaped(self):
        with tempfile.TemporaryDirectory(prefix="eidolon-tool-flood-") as folder:
            root = Path(folder)
            program = root / 'tool'
            record = root / 'pid'
            program.write_text(f'#!{sys.executable}\nimport os\nopen({str(record)!r},"w").write(str(os.getpid()))\nwhile True:os.write(1,b"x"*4096)\n')
            program.chmod(0o700)
            with self.assertRaisesRegex(ValueError, 'DEPENDENCY_NOT_READY'):
                launcher.tool_version(program)
            pid = int(record.read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
