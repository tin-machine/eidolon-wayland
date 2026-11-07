"""NX13固定probeのparser、identity失効、専用processの観測/回収を検査。"""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'eidolon-io-probe.py'
spec = importlib.util.spec_from_file_location('io_probe', SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def io_text(**changes):
    return ''.join(f'{key}: {changes.get(key, 0)}\n' for key in probe.FIELDS)


def identity(pid=12, start=34, uid=1000, comm='name ) with ( spaces'):
    fields = ['S'] + ['0'] * 49
    fields[1:4] = ['1', '12', '12']
    fields[19] = str(start)
    return f'{pid} ({comm}) ' + ' '.join(fields), f'Name:\tprivate\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n'


class IoProbeTests(unittest.TestCase):
    def test_io_fields_are_separate_and_zero_is_valid(self):
        value = probe.parse_io(io_text(rchar=1024, wchar=2048))
        self.assertEqual(value['read_bytes'], 0)
        self.assertEqual(value['write_bytes'], 0)
        self.assertEqual(value['rchar'], 1024)
        self.assertEqual(value['wchar'], 2048)

    def test_io_missing_duplicate_unknown_negative_non_ascii_and_overflow_rejected(self):
        cases = [io_text().replace('wchar: 0\n', ''), io_text() + 'rchar: 1\n',
                 io_text() + 'private: 0\n', io_text(rchar=-1), io_text(rchar='１２'),
                 io_text(rchar=2**64), io_text(rchar='0' * 100)]
        for text in cases:
            with self.subTest(text_length=len(text)), self.assertRaises(probe.ProbeError):
                probe.parse_io(text)

    def test_stat_comm_parentheses_and_uid_fields_are_not_pid_identity(self):
        stat_text, status = identity()
        self.assertEqual(probe.parse_identity(stat_text, status, 12), (12, 34, (1000,) * 4, 1, 12, 12))
        with self.assertRaises(probe.ProbeError):
            probe.parse_identity(stat_text, status, 13)
        with self.assertRaises(probe.ProbeError):
            probe.parse_identity(stat_text, 'Uid: 1000\n', 12)

    def test_pid_reuse_uid_change_and_between_read_mutation_fail_closed(self):
        base = (12, 34, (os.getuid(),) * 4, 1, 12, 12)
        variants = [(12, 35, base[2], 1, 12, 12), (12, 34, (os.getuid() + 1,) * 4, 1, 12, 12)]
        for current in variants:
            with patch.object(probe.os, 'open', return_value=99), patch.object(probe.os, 'close'), \
                    patch.object(probe, 'identity_at', return_value=current):
                with self.assertRaises(probe.ProbeError):
                    probe.checked_sample(12, base)
        with patch.object(probe.os, 'open', return_value=99), patch.object(probe.os, 'close'), \
                patch.object(probe, 'identity_at', side_effect=[base, variants[0]]), \
                patch.object(probe, 'read_at', return_value=io_text()):
            with self.assertRaises(probe.ProbeError):
                probe.checked_sample(12, base)

    def test_denied_missing_regression_never_become_zero(self):
        for error, reason in [(PermissionError(13, 'private path'), 'ACCESS_DENIED'),
                              (FileNotFoundError(2, 'private path'), 'PROCESS_GONE')]:
            with patch.object(probe, 'checked_sample', side_effect=error):
                _, value = probe.sample(12, ('known',))
                self.assertEqual(value['reason'], reason)
                self.assertNotIn('counters', value)
                self.assertNotIn('private', json.dumps(value))
        before = dict(status='available', counters=probe.parse_io(io_text(wchar=10)))
        after = dict(status='available', counters=probe.parse_io(io_text(wchar=9)))
        self.assertEqual(probe.delta(before, after)['reason'], 'COUNTER_REGRESSED')
        self.assertEqual(probe.delta(before, dict(status='unavailable'))['reason'], 'COUNTER_UNAVAILABLE')

    def test_oversize_proc_and_output_are_bounded(self):
        with patch.object(probe.os, 'open', return_value=99), patch.object(probe.os, 'close'), \
                patch.object(probe.os, 'read', return_value=b'x' * (probe.LIMIT + 1)):
            with self.assertRaises(probe.ProbeError):
                probe.read_at(99, 'io')
        with self.assertRaises(probe.ProbeError):
            probe.send(-1, dict(value='x' * probe.LIMIT))

    def test_private_fixed_file_has_expected_modes_and_never_follows_a_symlink(self):
        with tempfile.TemporaryDirectory(prefix='nx13-file-') as folder:
            directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
            try:
                self.assertEqual(probe.file_operation(directory, True)['bytes'], probe.FILE_BYTES)
                self.assertEqual((Path(folder) / 'fixed.bin').stat().st_mode & 0o777, 0o600)
                self.assertEqual(probe.file_operation(directory, False)['bytes'], probe.FILE_BYTES)
                (Path(folder) / 'fixed.bin').unlink()
                outside = Path(folder) / 'untouched'
                outside.write_bytes(b'private fixture')
                (Path(folder) / 'fixed.bin').symlink_to(outside)
                with self.assertRaises(OSError):
                    probe.file_operation(directory, False)
                self.assertEqual(outside.read_bytes(), b'private fixture')
            finally:
                os.close(directory)

    def test_arbitrary_pid_path_and_commands_are_not_cli_options(self):
        for argument in ('--pid=1', '--path=/etc/passwd', '--command=sh'):
            result = subprocess.run([sys.executable, str(SCRIPT), argument], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)['reason'], 'NO_ARGUMENTS_ALLOWED')
            self.assertEqual(result.stderr, '')

    @unittest.skipUnless(sys.platform.startswith('linux') and hasattr(os, 'WNOWAIT'), 'Linux proc/waitid fixture')
    def test_real_children_read_write_wait_and_only_owned_cleanup(self):
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'])
        try:
            with tempfile.TemporaryDirectory(prefix='nx13-test-') as folder:
                env = dict(os.environ, TMPDIR=folder, PYTHONDONTWRITEBYTECODE='1')
                result = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=15)
                self.assertIn(result.returncode, (0, 1))
                self.assertEqual(result.stderr, '')
                value = json.loads(result.stdout)
                self.assertIn(value['status'], ('observed', 'partial'), value)
                self.assertEqual(value['cleanup'], 'completed')
                self.assertEqual(value['uid'], os.getuid())
                self.assertFalse(value['physical_disk_guaranteed'])
                self.assertFalse(value['nx13_complete'])
                for role in ('writer', 'reader'):
                    self.assertEqual(value['observations'][role + '_operation']['bytes'], probe.FILE_BYTES)
                    self.assertEqual(value['observations'][role + '_parent_live']['status'], 'available')
                self.assertGreaterEqual(value['writer_delta']['counters']['wchar'], probe.FILE_BYTES)
                self.assertEqual(value['naive_sum_double_count_risk']['character_io'], 'observed')
                self.assertEqual(list(Path(folder).iterdir()), [])
                self.assertNotIn(folder, result.stdout)
                self.assertIsNone(unrelated.poll())
        finally:
            unrelated.terminate(); unrelated.wait(timeout=5)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux process fixture')
    def test_signal_during_blocked_owned_fixture_returns_sanitized_failure(self):
        # Inject only a fixed host test stall; production CLI has no fault/path/process controls.
        source = """import importlib.util,os,sys,time
spec=importlib.util.spec_from_file_location('p',sys.argv[1]);p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
def stalled(_):
 os.write(2,b'fixture_started\\n');time.sleep(30)
p.scenario=stalled
sys.argv=[sys.argv[1]]
p.main()
"""
        with tempfile.TemporaryDirectory(prefix='nx13-signal-') as folder:
            process = subprocess.Popen([sys.executable, '-c', source, str(SCRIPT)],
                env=dict(os.environ, TMPDIR=folder), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                import select
                self.assertTrue(select.select([process.stderr], [], [], 5)[0])
                self.assertEqual(process.stderr.readline(), 'fixture_started\n')
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=5)
                value = json.loads(stdout)
                self.assertEqual(value['reason'], 'INTERRUPTED')
                self.assertEqual(value['cleanup'], 'completed')
                self.assertEqual(stderr, '')
                self.assertEqual(list(Path(folder).iterdir()), [])
            finally:
                if process.poll() is None:
                    process.terminate(); process.wait(timeout=5)
                process.stdout.close(); process.stderr.close()



if __name__ == '__main__':
    unittest.main()
