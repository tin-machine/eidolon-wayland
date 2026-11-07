"""GTK自身/SHMの成功とは分離した、固定report・PID・group lifecycleのhost fixture。"""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import test_linux_helper as base

helper, SOURCE = base.helper, base.SOURCE


NONCE = '1' * 16


def row(case='input', sequence=1, final=False):
    app_id = 'io.github.tinmachine.eidolon.gtkfixture.r' + NONCE * 2
    value = {**dict.fromkeys(helper.GTK_COUNTERS, 0), **dict.fromkeys(helper.GTK_BOOLS, False),
             'schema': 1, 'probe': 'eidolon-gtk3-interaction-v1', 'run_id': NONCE * 2,
             'case': case, 'app_id': app_id, 'dialog_app_id': app_id + '.dialog', 'pid': os.getpid(),
             'uid': os.getuid(), 'sequence': sequence, 'phase': 'final' if final else 'ready' if sequence == 1 else 'state',
             'reason': 'completed' if final else 'running', 'logical_size': [640, 360],
             'entry_rect': [20, 20, 100, 40], 'button_rect': [20, 100, 100, 40]}
    if final:
        value.update(complete=True, main_draws=1)
        if case == 'input':
            value.update(a_down=1, a_up=1, delete_down=1, delete_up=1, button_clicks=1,
                         entry_changes=2, token_seen=True, delete_seen=True)
        elif case == 'dialog':
            value.update(dialog_opened=1, dialog_closed=1, dialog_responses=1, dialog_parent_set=True, dialog_draws=1)
        else: value.update(resize_large_seen=True, resize_return_seen=True)
    return value


def encoded(value): return json.dumps(value, separators=(',', ':')).encode() + b'\n'


class LinuxGtkParserTest(unittest.TestCase):
    def parser(self, case='input'):
        return helper.GtkReports(NONCE, 'gtk_' + case, lambda pid, saved: saved or {'pid': pid}, lambda value: None)

    def test_all_fixed_cases_fragmented_and_trailing_partial_not_complete(self):
        for case in ('input', 'dialog', 'resize'):
            parser = self.parser(case); raw = encoded(row(case)) + encoded(row(case, 2, True))
            for offset in range(0, len(raw), 7): parser.feed(raw[offset:offset + 7])
            self.assertTrue(parser.complete()); self.assertEqual(parser.count, 2)
            parser.feed(b'{'); self.assertFalse(parser.complete())

    def test_identity_types_selftest_geometry_and_unknown_fields_rejected(self):
        mutations = [('schema', True), ('uid', True), ('pid', True), ('uid', os.getuid() + 1),
                     ('run_id', '2' * 32), ('case', 'dialog'), ('app_id', 'other'),
                     ('host_selftest', True), ('physical_input_verified', True), ('a_down', True),
                     ('entry_rect', [0, 0, 4097, 20]), ('unexpected', 'private')]
        for key, value in mutations:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.parser().feed(encoded({**row(), key: value}))
        with self.assertRaisesRegex(ValueError, 'JSON_INVALID'): self.parser().feed(b'{"schema":1,"schema":1}\n')

    def test_sequences_counter_regression_and_after_final(self):
        with self.assertRaisesRegex(ValueError, 'GTK_REPORT_PHASE'): self.parser().feed(encoded(row(sequence=2)))
        parser = self.parser(); parser.feed(encoded({**row(), 'a_down': 1}))
        with self.assertRaisesRegex(ValueError, 'COUNTER_REGRESSED'): parser.feed(encoded(row(sequence=2)))
        parser = self.parser(); parser.feed(encoded(row(final=True)))
        with self.assertRaisesRegex(ValueError, 'AFTER_FINAL'): parser.feed(encoded(row(sequence=2)))
        with self.assertRaisesRegex(ValueError, 'GTK_REPORT_PHASE'): self.parser().feed(encoded({**row(), 'reason': 'completed'}))

    def test_output_and_row_bounds_no_success_from_partial_or_claim_only(self):
        with self.assertRaisesRegex(ValueError, 'GTK_REPORT_LIMIT'): self.parser().feed(b'x' * 16385)
        with self.assertRaisesRegex(ValueError, 'GTK_REPORT_LIMIT'): self.parser().feed(b'x' * 524289)
        parser = self.parser()
        for index in range(256): parser.feed(encoded(row(sequence=index + 1)))
        with self.assertRaisesRegex(ValueError, 'GTK_REPORT_PHASE'): parser.feed(encoded(row(sequence=257)))
        parser = self.parser(); parser.feed(encoded({**row(final=True), 'a_up': 0})); self.assertFalse(parser.complete())
        parser = self.parser(); parser.feed(encoded(row(final=True))); self.assertFalse(parser.complete())

    def test_live_process_executable_group_start_and_uid_binding(self):
        current = helper.identity(os.getpid()); executable = Path('/proc/self/exe').stat()
        saved = helper.gtk_process(os.getpid(), current['group'], (executable.st_dev, executable.st_ino))
        self.assertEqual(saved['start'], current['start'])
        for group, inode in [(current['group'] + 1, executable.st_ino), (current['group'], executable.st_ino + 1)]:
            with self.assertRaises(ValueError): helper.gtk_process(os.getpid(), group, (executable.st_dev, inode))
        for key, value in [('pid', current['pid'] + 1), ('start', '0'), ('group', current['group'] + 1), ('uid', current['uid'] + 1)]:
            with self.assertRaisesRegex(ValueError, 'GTK_PROCESS_CHANGED'):
                helper.gtk_process(os.getpid(), current['group'], (0, 0), {**saved, key: value})
        with mock.patch.object(helper, 'identity', return_value=None):
            self.assertEqual(helper.gtk_process(os.getpid(), current['group'], (0, 0), saved), saved)
            with self.assertRaisesRegex(ValueError, 'GTK_PROCESS_INVALID'): helper.gtk_process(os.getpid(), current['group'], (0, 0))
    def test_group_scan_tolerates_short_scheduler_pause_but_remains_bounded(self):
        group = helper.Group(['/usr/bin/true'], {'PATH': '/usr/bin:/bin'})
        original = os.scandir
        class Delayed:
            def __init__(self, path, delay): self.entries, self.delay = original(path), delay
            def __enter__(self): return self
            def __exit__(self, *_): self.entries.close()
            def __iter__(self): time.sleep(self.delay); yield from self.entries
        try:
            time.sleep(.02)
            with mock.patch.object(helper.os, 'scandir', side_effect=lambda path: Delayed(path, .06)):
                self.assertFalse(group.live())
            with mock.patch.object(helper.os, 'scandir', side_effect=lambda path: Delayed(path, .3)):
                with self.assertRaisesRegex(ValueError, 'GROUP_SCAN_LIMIT'): group.live()
        finally:
            self.assertTrue(group.finish()); group.child.stdout.close(); group.child.stderr.close()


class LinuxGtkFixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='elg-'); self.home = Path(self.temporary.name)
        self.bundle_path = base.LinuxHelperTest.bundle(self)
        self.make_extension()
    def tearDown(self): self.temporary.cleanup()
    def make_extension(self, binary=b'fixed host fixture'):
        for name, data in zip(helper.GTK_FILES, (b'fixed source', binary)):
            path = self.bundle_path / name; path.write_bytes(data); path.chmod(0o700 if name == helper.GTK_FILES[1] else 0o600)
        value = dict(schema=1, backend='linux_posix', revision=helper.GTK_REVISION,
                     base_bundle_sha256=hashlib.sha256((self.bundle_path / 'bundle.json').read_bytes()).hexdigest(),
                     gtk_version='3.24.52', files={name: hashlib.sha256((self.bundle_path / name).read_bytes()).hexdigest() for name in helper.GTK_FILES})
        (self.bundle_path / 'gtk-bundle.json').write_bytes(encoded(value)); (self.bundle_path / 'gtk-bundle.json').chmod(0o600)


class LinuxGtkBundleTest(LinuxGtkFixture, unittest.TestCase):
    def test_extension_is_optional_for_pattern_and_binds_base_and_source(self):
        self.assertEqual(helper.gtk_bundle(self.home)[3]['gtk_version'], '3.24.52')
        manifest = json.loads((self.bundle_path / 'gtk-bundle.json').read_text()); manifest['base_bundle_sha256'] = '0' * 64
        (self.bundle_path / 'gtk-bundle.json').write_bytes(encoded(manifest))
        with self.assertRaisesRegex(ValueError, 'GTK_BUNDLE_INVALID'): helper.gtk_bundle(self.home)
        helper.bundle(self.home)  # patternはGTK拡張の内容へ依存しない。
        self.make_extension(); (self.bundle_path / helper.GTK_FILES[0]).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'GTK_BUNDLE_HASH_MISMATCH'): helper.gtk_bundle(self.home)
    def test_extension_rejects_unknown_field_symlink_and_writable_binary(self):
        manifest = json.loads((self.bundle_path / 'gtk-bundle.json').read_text()); manifest['argv'] = ['/bin/sh']
        (self.bundle_path / 'gtk-bundle.json').write_bytes(encoded(manifest))
        with self.assertRaises(ValueError): helper.gtk_bundle(self.home)
        self.make_extension(); binary = self.bundle_path / helper.GTK_FILES[1]; binary.chmod(0o777)
        with self.assertRaisesRegex(ValueError, 'FILE_INVALID'): helper.gtk_bundle(self.home)
        binary.unlink(); binary.symlink_to('/usr/bin/true')
        with self.assertRaises(OSError): helper.gtk_bundle(self.home)
    def test_fixed_command_and_private_environment_never_inherit_desktop(self):
        directory = helper.prepare(self.home, NONCE)
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(str(directory / 'p'))
            for mode, case in helper.GTK_MODES.items():
                command = helper.gtk_child_command(self.home, directory, NONCE, mode)
                self.assertEqual(command[-5:], [str(self.bundle_path / helper.GTK_FILES[1]), '--case', case, '--run-id', NONCE * 2])
                self.assertIn('--no-gpu', command); self.assertNotIn('/bin/sh', command)
        with mock.patch.dict(os.environ, DBUS_SESSION_BUS_ADDRESS='secret', SSH_AUTH_SOCK='secret', WAYLAND_DISPLAY='secret', LD_PRELOAD='secret'):
            env = helper.gtk_environment(self.home, directory)
        self.assertEqual(env['HOME'], str(directory / 'g/home')); self.assertEqual(env['GDK_BACKEND'], 'wayland')
        self.assertFalse({'SSH_AUTH_SOCK', 'LD_PRELOAD', 'WAYLAND_DISPLAY'} & set(env))
        self.assertEqual(env['DBUS_SESSION_BUS_ADDRESS'], 'unix:path=' + str(directory / 'g/no-session-bus'))
        self.assertFalse((self.home / '.config').exists())
    def state(self):
        directory = helper.prepare(self.home, NONCE); creator = helper.identity(os.getpid())
        helper.write_record(directory / 'lease.json', {**helper.marker(NONCE), 'mode': 'gtk_input', 'owner': creator, 'child': None})
        helper.gtk_environment(self.home, directory); return directory, creator
    def test_private_state_cleanup_is_owned_bounded_and_never_follows_link(self):
        directory, creator = self.state(); outside = self.home / 'keep'; outside.write_bytes(b'keep')
        link = directory / 'g/cache/escape'; link.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'GTK_STATE_INVALID'): helper.cleanup(self.home, NONCE, creator)
        self.assertEqual(outside.read_bytes(), b'keep'); self.assertTrue((directory / 'owner.json').exists())
        link.unlink(); self.assertTrue(helper.cleanup(self.home, NONCE, creator)['cleaned'])
    def test_live_or_unknown_creator_keeps_private_state(self):
        directory, creator = self.state()
        self.assertFalse(helper.cleanup(self.home, NONCE)['cleaned']); self.assertTrue((directory / 'g').exists())
        lease = helper.record(directory / 'lease.json', NONCE); lease['child'] = creator
        helper.write_record(directory / 'lease.json', lease)
        self.assertFalse(helper.cleanup(self.home, NONCE, creator)['cleaned']); self.assertTrue((directory / 'g').exists())
    def test_cleanup_depth_limit_retains_owner(self):
        directory, creator = self.state(); nested = directory / 'g'
        for _ in range(7): nested /= 'x'; nested.mkdir(mode=0o700)
        with self.assertRaisesRegex(ValueError, 'GTK_STATE_LIMIT'): helper.cleanup(self.home, NONCE, creator)
        self.assertTrue((directory / 'owner.json').exists())


class LinuxGtkProcessTest(LinuxGtkFixture, unittest.TestCase):
    # 追加の小CはGTKではない。report/lifecycleを固定stdioで検証するprocess fixture。
    @classmethod
    def setUpClass(cls):
        if not Path('/usr/bin/cc').exists(): raise unittest.SkipTest('host process fixture requires /usr/bin/cc')
    def install_process(self, final=True, invalid=False, lingering_child=False):
        values = [row(), row(sequence=2, final=True)] if final else [row()]
        if invalid: values[0]['uid'] += 1
        output = []
        for value in values:
            value['pid'] = 987654321
            raw = encoded(value).decode().replace('987654321', '%ld')
            output.append('printf(' + json.dumps(raw) + ', (long)getpid()); fflush(stdout); usleep(200000);')
        linger = 'if(fork()==0){sleep(5);return 0;}' if lingering_child else ''
        code = '#include <stdio.h>\n#include <unistd.h>\nint main(void){' + ''.join(output) + linger + ('return 0;' if final else 'sleep(5);return 0;') + '}\n'
        source = self.home / 'fixed-process.c'; source.write_text(code)
        target = self.home / 'fixed-process'
        subprocess.run(['/usr/bin/cc', '-O0', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(target)], check=True, capture_output=True)
        self.make_extension(target.read_bytes())
    def process(self, *, start='gtk_input', stop=False, close=False, lease=2):
        wrapper = '''import importlib.util,json,sys
from pathlib import Path
s=importlib.util.spec_from_file_location('helper',sys.argv[1]);h=importlib.util.module_from_spec(s);s.loader.exec_module(h)
home=Path(sys.argv[2])
h.serve(home,'1111111111111111',0,lambda v:print(json.dumps(v),flush=True),mode='gtk_input',lease_seconds=float(sys.argv[3]),maximum_seconds=4,inspect=lambda *_:{'synthetic_tools':True},factory=lambda *_:['/usr/bin/python3','-c','import subprocess,sys;sys.exit(subprocess.call([sys.argv[1]]))',str(home/'.local/lib/eidolon-linux/eidolon-gtk3-fixture')])
'''
        process = subprocess.Popen(['/usr/bin/python3', '-c', wrapper, str(SOURCE), str(self.home), str(lease)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            prepared = json.loads(process.stdout.readline()); self.assertEqual(prepared['type'], 'prepared')
            for command in ({'type': 'inspect_tools'}, {'type': 'start', 'mode': start}):
                process.stdin.write(encoded({'schema': 1, 'nonce': NONCE, **command})); process.stdin.flush()
            values = [prepared]
            if stop or close:
                while True:
                    item = json.loads(process.stdout.readline()); values.append(item)
                    if item['type'] == 'gtk_report': break
                if stop: process.stdin.write(encoded({'schema': 1, 'nonce': NONCE, 'type': 'stop'})); process.stdin.flush()
                else: process.stdin.close(); process.stdin = None
            process.wait(timeout=8)
            values.extend(json.loads(line) for line in process.stdout.read().splitlines())
            self.assertEqual(process.returncode, 0, process.stderr.read().decode())
            self.assertEqual(values[-1]['type'], 'finished'); return values[-1], values
        finally:
            if process.poll() is None: os.killpg(process.pid, 9); process.wait(timeout=2)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None: pipe.close()
    def test_reports_bind_actual_non_helper_executable_and_complete_after_cleanup(self):
        self.install_process(); result, values = self.process()
        self.assertTrue(result['gtk_verified']); self.assertFalse(result['pattern_verified']); self.assertTrue(result['cleaned'])
        self.assertEqual(result['gtk_reports'], 2); self.assertEqual(result['reason'], 'Completed')
        self.assertFalse(result['gtk_forced_stop'])
        report = next(value['report'] for value in values if value['type'] == 'gtk_report')
        leader = next(value['child']['pid'] for value in values if value['type'] == 'child_started')
        self.assertNotEqual(report['pid'], leader)  # Waypipe leaderとGTK PIDを混同しない。
        self.assertNotEqual(report['pid'], os.getpid()); self.assertFalse((self.home / '.eidolon-linux' / ('s-' + NONCE)).exists())
    def test_mode_cannot_change_after_capture(self):
        self.install_process(); result, values = self.process(start='pattern')
        self.assertEqual(result['reason'], 'ControlInvalid'); self.assertFalse(result['gtk_verified'])
        self.assertFalse(any(value['type'] == 'child_started' for value in values)); self.assertTrue(result['cleaned'])
    def test_invalid_report_cancels_group_without_success(self):
        self.install_process(final=False, invalid=True); result, _ = self.process()
        self.assertEqual(result['reason'], 'ReportInvalid'); self.assertEqual(result['gtk_report_error'], 'GTK_REPORT_IDENTITY')
        self.assertFalse(result['gtk_verified']); self.assertTrue(result['child_reaped']); self.assertTrue(result['cleaned'])
    def test_final_and_exit_zero_with_lingering_group_is_not_natural_success(self):
        self.install_process(lingering_child=True); result, _ = self.process(lease=3)
        self.assertEqual(result['exit_status'], 0); self.assertEqual(result['gtk_reports'], 2)
        self.assertTrue(result['gtk_forced_stop']); self.assertFalse(result['gtk_verified']); self.assertTrue(result['cleaned'])
    def test_stop_eof_and_lease_reap_and_remove_private_state(self):
        for stop, close, reason in [(True, False, 'Stop'), (False, True, 'EOF'), (False, False, 'LeaseExpired')]:
            with self.subTest(reason=reason):
                self.install_process(final=False); result, _ = self.process(stop=stop, close=close, lease=.4)
                self.assertEqual(result['reason'], reason); self.assertTrue(result['child_reaped']); self.assertTrue(result['cleaned'])
                self.assertFalse(result['gtk_verified']); self.assertFalse(result['group_alive'])
                (self.home / '.eidolon-linux' / ('r-' + NONCE + '.json')).unlink()


if __name__ == '__main__': unittest.main()
