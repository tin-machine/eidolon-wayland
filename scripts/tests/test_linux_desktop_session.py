"""Normal helper protocol/lease/recovery tests; all homes and children are temporary fixtures."""
import importlib.util
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DESKTOP = load('desktop_session_adapter', ROOT / 'scripts/linux/eidolon_linux_desktop.py')
BASE = load('desktop_session_base', ROOT / 'scripts/linux/eidolon-linux-session.py')
OWNER = load('desktop_session_owner', ROOT / 'scripts/linux/eidolon_linux_process_owner.py')
SESSION = load('desktop_session_wire', ROOT / 'scripts/linux/eidolon_linux_desktop_session.py')
CONTEXT = ('1', '2', 'a' * 64, 'b' * 16)
BOOT_A = '11111111-2222-3333-4444-555555555555'
BOOT_B = '66666666-7777-8888-9999-aaaaaaaaaaaa'
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}


def fixture_bundle(home):
    return types.SimpleNamespace(home=home, base=BASE, base_hash='c' * 64, manifest_hash='d' * 64)


class DesktopStateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.bundle = fixture_bundle(self.home)
        self.context = DESKTOP.Context(*CONTEXT)
        self.state = SESSION.State(self.bundle, self.context, BOOT_A)
        self.state.prepare(dict(pid=os.getpid(), start=1))
        self.state.lease('prepared', 0, time.monotonic() + 15)

    def tearDown(self):
        self.temp.cleanup()

    def test_boot_and_context_are_durable_before_start(self):
        owner = BASE.decode(BASE.private_read(self.state.directory / 'owner.json'))
        self.assertEqual(owner['boot_id'], BOOT_A)
        self.assertEqual(owner['scope'], self.context.scope)
        self.assertEqual(owner['desktop_bundle_sha256'], self.bundle.manifest_hash)
        self.assertFalse(self.state.receipt.exists())
        self.assertTrue(SESSION.valid_boot(SESSION.boot_id()))
        self.assertFalse(SESSION.valid_boot('00000000-0000-0000-0000-000000000000'))

    def test_live_owner_receipt_proves_cleanup_on_same_boot(self):
        self.assertIsNone(self.state.finish('stop'))
        self.assertFalse(self.state.directory.exists())
        receipt = self.state.read_receipt(BOOT_A)
        self.assertTrue(receipt['remote_cleaned'])
        value = SESSION.recover(self.bundle, self.context, BOOT_A, current_boot=BOOT_A)
        self.assertEqual((value['status'], value['process_evidence']), ('complete', 'receipt'))
        self.assertTrue(value['process_complete'] and value['pipes_eof'] and value['receipt_written'])

    def test_same_boot_unknown_helper_never_signals_or_infers_exit_from_pid(self):
        owner = self.state.read_owner()
        owner['helper']['pid'] = 2147483647
        BASE.write_record(self.state.directory / 'owner.json', owner)
        with mock.patch.object(os, 'kill', side_effect=AssertionError('NUMERIC_PID_SIGNAL')), \
             mock.patch.object(os, 'pidfd_open', side_effect=AssertionError('RESTORED_PIDFD')):
            value = SESSION.recover(self.bundle, self.context, BOOT_A, current_boot=BOOT_A)
        self.assertEqual((value['status'], value['process_evidence']), ('pending', 'unknown'))
        self.assertFalse(value['process_complete'])
        self.assertTrue(self.state.directory.exists())
        self.assertFalse(self.state.receipt.exists())

    def test_boot_change_requires_record_then_cleans_files_without_claiming_observed_eof(self):
        private = self.state.directory / 'g'
        private.mkdir(mode=0o700)
        file = private / 'temporary'
        file.write_bytes(b'fixture')
        file.chmod(0o600)
        with mock.patch.object(os, 'kill', side_effect=AssertionError('NUMERIC_PID_SIGNAL')):
            value = SESSION.recover(self.bundle, self.context, BOOT_A, current_boot=BOOT_B)
        self.assertEqual((value['status'], value['process_evidence']), ('complete', 'boot_changed'))
        self.assertEqual(value['origin_boot_id'], BOOT_A)
        self.assertTrue(value['process_complete'] and value['remote_cleaned'] and value['receipt_written'])
        self.assertFalse(value['pipes_eof'])
        self.assertFalse(self.state.directory.exists())

    def test_lost_prepared_ack_can_recover_bound_remote_origin(self):
        value = SESSION.recover(self.bundle, self.context, '-', current_boot=BOOT_B)
        self.assertEqual((value['status'], value['origin_boot_id']), ('complete', BOOT_A))
        self.assertEqual(value['expected_boot_id'], '-')
        self.assertEqual(value['process_evidence'], 'boot_changed')

    def test_unknown_boot_without_records_remains_pending(self):
        different = DESKTOP.Context('1', '2', 'a' * 64, 'e' * 16)
        value = SESSION.recover(self.bundle, different, '-', current_boot=BOOT_B)
        self.assertEqual(value['status'], 'pending')
        self.assertIsNone(value['origin_boot_id'])
        self.assertFalse(value['process_complete'] or value['remote_cleaned'])

    def test_boot_change_does_not_hide_unknown_files_or_socket_substitution(self):
        foreign = self.state.directory / 'p'
        foreign.write_bytes(b'not a socket')
        foreign.chmod(0o600)
        value = SESSION.recover(self.bundle, self.context, BOOT_A, current_boot=BOOT_B)
        self.assertEqual(value['status'], 'pending')
        self.assertTrue(value['process_complete'])
        self.assertFalse(value['remote_cleaned'])
        self.assertEqual(foreign.read_bytes(), b'not a socket')
        self.assertFalse(self.state.read_receipt(BOOT_A)['remote_cleaned'])

    def test_partial_receipt_survives_failure_before_directory_cleanup(self):
        with mock.patch.object(self.state, 'clean_directory', side_effect=OSError('fixture')):
            self.assertEqual(self.state.finish('stop'), 'directory_cleanup')
        self.assertFalse(self.state.read_receipt(BOOT_A)['remote_cleaned'])
        value = SESSION.recover(self.bundle, self.context, BOOT_A, current_boot=BOOT_A)
        self.assertEqual((value['status'], value['process_evidence']), ('complete', 'receipt'))

    def test_crash_after_last_unlink_recovers_only_empty_directory_with_receipt(self):
        with mock.patch.object(self.state, 'clean_directory', side_effect=OSError('fixture')):
            self.state.finish('stop')
        (self.state.directory / 'lease.json').unlink()
        (self.state.directory / 'owner.json').unlink()
        value = SESSION.recover(self.bundle, self.context, BOOT_A, current_boot=BOOT_A)
        self.assertEqual(value['status'], 'complete')
        self.assertFalse(self.state.directory.exists())

    def test_missing_owner_with_nonempty_directory_remains_pending_even_with_receipt(self):
        with mock.patch.object(self.state, 'clean_directory', side_effect=OSError('fixture')):
            self.state.finish('stop')
        (self.state.directory / 'owner.json').unlink()
        value = SESSION.recover(self.bundle, self.context, BOOT_A, current_boot=BOOT_A)
        self.assertEqual(value['status'], 'pending')
        self.assertTrue((self.state.directory / 'lease.json').exists())

    def test_wrong_profile_pin_or_boot_cannot_recover_another_owner(self):
        before = (self.state.directory / 'owner.json').read_bytes()
        other_context = DESKTOP.Context('1', '3', 'a' * 64, 'b' * 16)
        other_bundle = fixture_bundle(self.home)
        other_bundle.manifest_hash = 'e' * 64
        for bundle, context, expected in [(self.bundle, other_context, BOOT_A),
                                           (other_bundle, self.context, BOOT_A), (self.bundle, self.context, BOOT_B)]:
            value = SESSION.recover(bundle, context, expected, current_boot=BOOT_B)
            self.assertEqual(value['status'], 'pending')
            self.assertFalse(value['process_complete'])
        self.assertEqual((self.state.directory / 'owner.json').read_bytes(), before)

    def test_symlink_or_hardlink_record_is_not_proof(self):
        path = self.state.directory / 'owner.json'
        target = self.home / 'outside'
        path.rename(target)
        path.symlink_to(target)
        self.assertEqual(SESSION.recover(self.bundle, self.context, BOOT_A, current_boot=BOOT_B)['status'], 'pending')
        path.unlink()
        os.link(target, path)
        self.assertEqual(SESSION.recover(self.bundle, self.context, BOOT_A, current_boot=BOOT_B)['status'], 'pending')
        self.assertTrue(target.exists())

    def test_failed_prepare_never_cleans_an_existing_live_session(self):
        control, sender = os.pipe()
        receiver, output = os.pipe()
        before = (self.state.directory / 'owner.json').read_bytes()
        try:
            def forbidden(*_):
                raise AssertionError('INSTANCE_REUSED')
            result = SESSION.serve(self.bundle, self.context, DESKTOP, OWNER, control, output,
                                   current_boot=BOOT_A, instance_factory=forbidden)
            self.assertTrue(result['pending'])
            self.assertEqual((self.state.directory / 'owner.json').read_bytes(), before)
            self.assertFalse(self.state.receipt.exists())
        finally:
            for descriptor in (control, sender, receiver, output):
                os.close(descriptor)


class DesktopWireTest(unittest.TestCase):
    def setUp(self):
        self.control, self.sender = os.pipe()
        self.receiver, self.output = os.pipe()
        self.context = DESKTOP.Context(*CONTEXT)
        self.wire = SESSION.Wire(self.context, BASE, self.control, self.output)

    def tearDown(self):
        self.wire.close()
        for descriptor in (self.control, self.sender, self.receiver, self.output):
            os.close(descriptor)

    def command(self, sequence=1, **changes):
        return dict(self.context.marker(), sequence=str(sequence), command=dict(type='heartbeat'), **changes)

    def test_partial_frame_and_separate_causal_event_sequence(self):
        raw = json.dumps(self.command()).encode() + b'\n'
        os.write(self.sender, raw[:10])
        self.assertIsNone(self.wire.poll(0))
        self.assertEqual(self.wire.request_sequence, 0)
        os.write(self.sender, raw[10:])
        self.assertEqual(self.wire.poll(0), {'type': 'heartbeat'})
        self.wire.emit('heartbeat', {'phase': 'started'})
        # A later request is waiting in stdin; asynchronous exit must retain the
        # last processed request rather than fabricating an ACK for queued bytes.
        os.write(self.sender, json.dumps(self.command(2)).encode() + b'\n')
        self.wire.emit('stopped', {'reason': 'process_exited'})
        self.wire.flush()
        rows = [json.loads(line) for line in os.read(self.receiver, 4096).splitlines()]
        self.assertEqual([(row['sequence'], row['request_sequence']) for row in rows], [('1', '1'), ('2', '1')])

    def test_duplicate_json_and_wrong_sequence_are_rejected(self):
        raw = json.dumps(self.command()).encode().replace(b'"schema": 1', b'"schema": 1,"schema":1')
        os.write(self.sender, raw + b'\n')
        with self.assertRaisesRegex(ValueError, 'JSON_INVALID'):
            self.wire.poll(0)
        os.write(self.sender, json.dumps(self.command(2)).encode() + b'\n')
        with self.assertRaisesRegex(ValueError, 'COMMAND_SEQUENCE'):
            self.wire.poll(0)
        self.assertEqual(self.wire.request_sequence, 0)

    def test_input_and_event_buffers_have_finite_limits(self):
        self.wire.input.extend(b'x' * SESSION.MAX_LINE)
        os.write(self.sender, b'x')
        with self.assertRaisesRegex(ValueError, 'INPUT_LIMIT'):
            self.wire.poll(0)
        self.wire.output.extend(b'x' * SESSION.MAX_QUEUE)
        with self.assertRaisesRegex(ValueError, 'OUTPUT_QUEUE_LIMIT'):
            self.wire.emit('heartbeat', {'phase': 'started'})
        self.assertEqual(self.wire.event_sequence, 0)


def fixed_child(mode, proof):
    if mode == 'long_logs':
        for _ in range(24):
            os.write(1, b'x' * 4096)
            os.write(2, b'y' * 4096)
    if os.fork() == 0:
        os.setsid()
        row = OWNER.identity(os.getpid())
        proof.write_text(json.dumps(dict(pid=row['pid'], start=row['start'])))
        print('child output is never a protocol event', flush=True)
        time.sleep(10)
        os._exit(0)
    if mode == 'exit':
        return
    time.sleep(10)


def worker(mode, home, *, simulated=False):
    tool = home / 'waypipe'
    tool.write_text('#!/usr/bin/python3\nimport sys\nsys.stdout.buffer.write(' + repr(BASE.VERSION) + ')\n')
    tool.chmod(0o700)
    class FixedBundle:
        base = BASE
        base_hash = 'c' * 64
        manifest_hash = 'd' * 64
        def recheck(self):
            pass
        def _catalog(self, context):
            return DESKTOP.Bundle._catalog(self, context)
        def catalog(self, context):
            return self._catalog(context)
        def command(self, *_):
            return [sys.executable, '-I', '-S', '-B', str(Path(__file__).resolve()), '--child', mode, str(home / 'child.json')]
    bundle = FixedBundle()
    bundle.home = bundle.directory = home
    class FixtureInstance(DESKTOP.Instance):
        def start(self, selection, *, deadline=None):
            super().start(selection, deadline=deadline)
            if mode == 'exit':
                self.child.wait(timeout=2)  # force natural exit to race the next client command
        def close(self, timeout=3):
            if mode == 'pending' and not getattr(self, 'injected', False):
                self.injected = True
                def unavailable(*_):
                    raise OWNER.OwnershipError('PROCESS_SCAN_LIMIT')
                with mock.patch.object(OWNER, 'descendant_snapshot', unavailable):
                    return super().close(timeout=.03)
            return super().close(timeout)
    class LoopFixture:
        """No process ownership mock is used by the real child tests below."""
        def __init__(self, *_):
            self.running = False
        def inspect(self):
            return bundle._catalog(DESKTOP.Context(*CONTEXT))
        def start(self, _, *, deadline=None):
            (home / 'started').touch()
            self.running = mode != 'exit'
        def drain(self, *, enforce=True):
            assert not enforce
            return dict(running=self.running)
        def close(self, timeout=3):
            self.running = False
            return dict(complete=True, process_complete=True, pipes_eof=True)
    owner_module = types.SimpleNamespace(identity=lambda pid: dict(pid=pid, start=1)) if simulated else OWNER
    SESSION.serve(bundle, DESKTOP.Context(*CONTEXT), DESKTOP, owner_module, current_boot=BOOT_A,
                  lease_seconds=.4, cleanup_seconds=.5, instance_factory=LoopFixture if simulated else FixtureInstance)


class DesktopSessionHarness(unittest.TestCase):
    worker_flag = '--worker'
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.child = None
        self.buffer = bytearray()
        self.sequence = 0
        self.context = DESKTOP.Context(*CONTEXT)
    def tearDown(self):
        if self.child is not None:
            if self.child.poll() is None:
                self.child.stdin.close()
                try:
                    self.child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.child.kill()
                    self.child.wait()
            self.child.stdout.close()
            self.child.stderr.close()
            if not self.child.stdin.closed:
                self.child.stdin.close()
        self.temp.cleanup()
    def prepare_helper(self, mode='plain'):
        self.child = subprocess.Popen([sys.executable, '-I', '-S', '-B', str(Path(__file__).resolve()), self.worker_flag, mode, str(self.home)],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=ENV)
        prepared = self.event()
        self.assertEqual(prepared['event']['type'], 'prepared')
        self.assertEqual(prepared['event']['payload']['boot_id'], BOOT_A)
    def launch(self, mode='plain'):
        self.prepare_helper(mode)
        self.send('inspect')
        catalog = self.event()
        self.assertEqual(catalog['event']['type'], 'catalog')
        selection = dict(scope=self.context.scope, revision=catalog['event']['payload']['revision'], app_id=DESKTOP.APP_ID)
        self.send('start', selection=selection)
        self.assertEqual(self.event()['event']['type'], 'started')
    def send(self, name, **fields):
        self.sequence += 1
        value = dict(self.context.marker(), sequence=str(self.sequence), command=dict(type=name, **fields))
        self.child.stdin.write(json.dumps(value).encode() + b'\n')
        self.child.stdin.flush()
    def event(self, timeout=4):
        deadline = time.monotonic() + timeout
        while b'\n' not in self.buffer and time.monotonic() < deadline:
            ready, _, _ = select.select([self.child.stdout], [], [], max(0, deadline - time.monotonic()))
            if ready:
                data = os.read(self.child.stdout.fileno(), 4096)
                if not data:
                    break
                self.buffer.extend(data)
        self.assertIn(b'\n', self.buffer, 'helper event missing')
        raw, _, self.buffer = self.buffer.partition(b'\n')
        return json.loads(raw)
    def finished(self, reason):
        event = self.event()
        self.assertEqual(event['event']['type'], 'stopped')
        self.assertEqual(event['event']['payload']['reason'], reason)
        self.assertEqual(event['request_sequence'], str(self.sequence))
        self.assertEqual(self.child.wait(timeout=4), 0)
        self.assertEqual(self.child.stderr.read(), b'')
        result = SESSION.recover(fixture_bundle(self.home), self.context, BOOT_A, current_boot=BOOT_A)
        self.assertEqual(result['status'], 'complete')
        proof = self.home / 'child.json'
        if proof.exists():
            row = json.loads(proof.read_text())
            try:
                current = OWNER.identity(row['pid'])
            except FileNotFoundError:
                current = None
            self.assertTrue(current is None or current['start'] != row['start'], 'owned setsid child survived cleanup')


class DesktopSessionLoopTest(DesktopSessionHarness):
    worker_flag = '--loop-worker'
    def test_lease_expiry_of_running_loop(self):
        self.launch()
        self.finished('lease_expired')
    def test_renewal_and_stop_have_no_total_session_timeout(self):
        self.launch()
        for _ in range(7):
            time.sleep(.1)
            self.send('heartbeat')
            self.assertEqual(self.event()['event']['type'], 'heartbeat')
        self.send('stop')
        self.finished('stop')
    def test_control_eof_reaches_cleanup_and_receipt(self):
        self.launch()
        self.child.stdin.close()
        self.finished('input_eof')
    def test_inspect_and_valid_start_renew_lease_during_slow_setup(self):
        self.prepare_helper()
        time.sleep(.25)
        self.send('inspect')
        catalog = self.event()
        self.assertEqual(catalog['event']['type'], 'catalog')
        time.sleep(.25)
        self.send('start', selection=dict(scope=self.context.scope, revision=catalog['event']['payload']['revision'],
                                          app_id=DESKTOP.APP_ID))
        self.assertEqual(self.event()['event']['type'], 'started')
        time.sleep(.25)
        self.send('stop')
        self.finished('stop')
    def test_malformed_command_is_terminal_and_does_not_renew_lease(self):
        self.launch()
        self.child.stdin.write(b'{"wrong":"scope"}\n')
        self.child.stdin.flush()
        value = self.event()
        self.assertEqual((value['event']['type'], value['event']['payload']['reason']), ('pending', 'protocol'))
        self.assertEqual(value['request_sequence'], '2')
        self.assertEqual(self.child.wait(timeout=3), 0)
        self.assertEqual(SESSION.recover(fixture_bundle(self.home), self.context, BOOT_A, current_boot=BOOT_A)['status'], 'complete')
    def test_wrong_selection_cannot_reach_application_spawn(self):
        self.prepare_helper()
        self.send('inspect')
        catalog = self.event()
        self.send('start', selection=dict(scope='f' * 64, revision=catalog['event']['payload']['revision'], app_id=DESKTOP.APP_ID))
        event = self.event()
        self.assertEqual((event['event']['type'], event['event']['payload']['reason']), ('pending', 'start'))
        self.assertEqual(self.child.wait(timeout=3), 0)
        self.assertFalse((self.home / 'started').exists())


@unittest.skipIf(os.getuid() == 0, 'ProcessOwner requires an unprivileged dedicated Linux helper')
class DesktopSessionProcessTest(DesktopSessionHarness):
    def ready_child(self):
        deadline = time.monotonic() + 2
        while not (self.home / 'child.json').exists() and time.monotonic() < deadline:
            time.sleep(.05)
            self.send('heartbeat')
            self.assertEqual(self.event()['event']['type'], 'heartbeat')
        self.assertTrue((self.home / 'child.json').exists(), 'setsid descendant did not start')
    def test_eof_cleans_owned_descendants_and_leaves_unrelated_process_alive(self):
        unrelated = subprocess.Popen([sys.executable, '-I', '-S', '-c', 'import time;time.sleep(10)'], env=ENV)
        try:
            self.launch()
            self.ready_child()
            self.child.stdin.close()
            self.finished('input_eof')
            self.assertIsNone(unrelated.poll())
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=3)
    def test_expired_lease_stops_without_a_client_stop_request(self):
        self.launch()
        self.ready_child()
        self.finished('lease_expired')
    def test_renewal_allows_lifetime_logs_over_64k_and_multiple_lease_periods(self):
        self.launch('long_logs')
        for _ in range(10):
            time.sleep(.1)
            self.send('heartbeat')
            self.assertEqual(self.event()['event']['type'], 'heartbeat')
        self.assertIsNone(self.child.poll())
        self.assertTrue((self.home / 'child.json').exists(), 'fixture did not drain more than 64KiB per stream')
        self.send('stop')
        self.finished('stop')
    def test_natural_exit_emits_terminal_cause_before_any_later_request_ack(self):
        self.launch('exit')
        # A client may already serialize/enqueue heartbeat 3 after seeing Started.
        # The helper's terminal event is still causally tied to accepted Start 2.
        try:
            self.send('heartbeat')
        except BrokenPipeError:
            pass
        event = self.event()
        self.assertEqual(event['event']['type'], 'stopped')
        self.assertEqual(event['event']['payload']['reason'], 'process_exited')
        self.assertEqual(event['request_sequence'], '2')
        self.assertEqual(self.child.wait(timeout=4), 0)
    def test_pending_retains_original_owner_until_retry_receipt_is_complete(self):
        self.launch('pending')
        self.ready_child()
        self.send('stop')
        event = self.event()
        self.assertEqual(event['event']['type'], 'pending')
        self.assertFalse(event['event']['payload']['process_complete'])
        self.child.stdin.close()
        self.assertEqual(self.child.wait(timeout=4), 0)
        self.assertEqual(self.child.stdout.read(), b'')  # terminal Pending is emitted only once
        result = SESSION.recover(fixture_bundle(self.home), self.context, BOOT_A, current_boot=BOOT_A)
        self.assertEqual(result['status'], 'complete')


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--worker':
        worker(sys.argv[2], Path(sys.argv[3]))
    elif len(sys.argv) == 4 and sys.argv[1] == '--loop-worker':
        worker(sys.argv[2], Path(sys.argv[3]), simulated=True)
    elif len(sys.argv) == 4 and sys.argv[1] == '--child':
        fixed_child(sys.argv[2], Path(sys.argv[3]))
    else:
        unittest.main()
