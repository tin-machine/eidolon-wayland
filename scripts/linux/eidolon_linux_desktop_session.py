"""Pinned normal-desktop bootstrap target; stdout is bounded control JSON, never child output.

Only the authenticated SSH actor calls main with hash-checked modules. A lease limits
unattended ownership, not the lifetime of a healthy GUI. Saved PIDs are diagnostic
identity only: recovery never reconstructs pidfds or signals a numeric PID.
"""
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import time

LEASE_SECONDS = 15.0
MAX_LINE = 16384
MAX_QUEUE = 65536
BOOT_PATTERN = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
REASONS = {'stop', 'lease_expired', 'input_eof', 'process_exited', 'process_ownership', 'pipe_eof',
           'directory_cleanup', 'receipt_write', 'protocol', 'tool_inspection', 'start', 'reboot'}


def require(value, token):
    if not value:
        raise ValueError(token)


def valid_boot(value):
    return type(value) is str and BOOT_PATTERN.fullmatch(value) is not None and value != '00000000-0000-0000-0000-000000000000'


def boot_id():
    descriptor = os.open('/proc/sys/kernel/random/boot_id', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        raw = os.read(descriptor, 64)
        require(os.read(descriptor, 1) == b'', 'BOOT_ID_INVALID')
    finally:
        os.close(descriptor)
    value = raw.decode('ascii').rstrip('\n')
    require(raw == (value + '\n').encode() and valid_boot(value), 'BOOT_ID_INVALID')
    return value


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class State:
    """Normal-desktop records are separate from the unchanged B4/B6 namespace."""
    def __init__(self, bundle, context, current_boot):
        require(valid_boot(current_boot), 'BOOT_ID_INVALID')
        self.bundle, self.base, self.context, self.boot = bundle, bundle.base, context, current_boot
        self.root = bundle.home / '.eidolon-desktop'
        self.directory = self.root / ('s-' + context.nonce)
        self.receipt = self.root / ('r-' + context.nonce + '.json')
        require(len(os.fsencode(self.directory / 'p')) <= 103, 'SOCKET_PATH_LONG')
        self.pins = dict(base_bundle_sha256=bundle.base_hash, desktop_bundle_sha256=bundle.manifest_hash)

    def marker(self, origin):
        require(valid_boot(origin), 'BOOT_ID_INVALID')
        return dict(self.context.marker(), **self.pins, boot_id=origin)

    def checked_root(self):
        self.base.checked_directory(self.bundle.home, private=False)
        self.base.checked_directory(self.root)

    def read(self, path, fields, origin=None):
        self.checked_root()
        value = self.base.decode(self.base.private_read(path))
        require(type(value) is dict and valid_boot(value.get('boot_id')), 'RECORD_INVALID')
        expected = self.marker(value['boot_id'] if origin is None else origin)
        require(set(value) == set(expected) | fields and all(type(value.get(key)) is type(item) and value[key] == item
                for key, item in expected.items()), 'RECORD_SCOPE')
        return value

    def read_owner(self, origin=None):
        self.checked_root()
        self.base.checked_directory(self.directory)
        value = self.read(self.directory / 'owner.json', {'helper'}, origin)
        helper = value['helper']
        require(type(helper) is dict and set(helper) == {'pid', 'start', 'uid'} and
                type(helper['pid']) is int and 0 < helper['pid'] <= 2**31 - 1 and
                type(helper['start']) is int and helper['start'] > 0 and
                type(helper['uid']) is int and helper['uid'] == os.getuid(), 'RECORD_INVALID')
        return value

    def read_receipt(self, origin=None):
        value = self.read(self.receipt, {'observed_boot_id', 'process_evidence', 'reason', 'process_complete',
                                       'pipes_eof', 'remote_cleaned', 'receipt_written'}, origin)
        require(valid_boot(value['observed_boot_id']) and value['reason'] in REASONS and
                all(type(value[name]) is bool for name in ('process_complete', 'pipes_eof', 'remote_cleaned', 'receipt_written')) and
                value['process_complete'] and value['receipt_written'] and
                ((value['process_evidence'] == 'live_owner' and value['pipes_eof'] and value['boot_id'] == value['observed_boot_id']) or
                 (value['process_evidence'] == 'boot_changed' and not value['pipes_eof'] and value['boot_id'] != value['observed_boot_id'])),
                'RECEIPT_INVALID')
        return value

    def prepare(self, helper_identity):
        self.base.checked_directory(self.bundle.home, private=False)
        try:
            self.root.mkdir(mode=0o700)
            fsync_directory(self.bundle.home)
        except FileExistsError:
            pass
        self.checked_root()
        entries = []
        with os.scandir(self.root) as iterator:
            for entry in iterator:
                require(len(entries) < 80, 'STATE_LIMIT')
                entries.append(entry.name)
        require(sum(name.startswith('s-') for name in entries) < 8, 'STATE_LIMIT')
        require(not os.path.lexists(self.receipt), 'NONCE_REUSED')
        self.directory.mkdir(mode=0o700)
        self.base.checked_directory(self.directory)
        helper = dict(pid=helper_identity['pid'], start=helper_identity['start'], uid=os.getuid())
        self.base.write_record(self.directory / 'owner.json', dict(self.marker(self.boot), helper=helper), replace=False)
        fsync_directory(self.root)

    def lease(self, phase, sequence, expires):
        require(phase in {'prepared', 'catalog', 'started'} and type(sequence) is int and 0 <= sequence <= 2**63 - 1,
                'LEASE_INVALID')
        self.read_owner(self.boot)
        self.base.write_record(self.directory / 'lease.json', dict(self.marker(self.boot), phase=phase,
                               request_sequence=str(sequence), expires_monotonic_ms=int(expires * 1000)))

    def clean_directory(self, origin):
        """Called only after live-owner proof or a verified previous-boot record."""
        self.checked_root()
        if not os.path.lexists(self.directory):
            return
        self.base.checked_directory(self.directory)
        if not os.path.lexists(self.directory / 'owner.json'):
            # A crash after the last unlink but before rmdir is recoverable only
            # with durable process proof and an otherwise empty private directory.
            self.read_receipt(origin)
            with os.scandir(self.directory) as entries:
                require(next(entries, None) is None, 'DIRECTORY_NOT_EMPTY')
            self.directory.rmdir()
            fsync_directory(self.root)
            return
        self.read_owner(origin)
        names = set()
        with os.scandir(self.directory) as entries:
            for entry in entries:
                require(entry.name in {'owner.json', 'lease.json', 'p', 'w', 'g'}, 'DIRECTORY_NOT_EMPTY')
                names.add(entry.name)
        if 'lease.json' in names:
            lease = self.read(self.directory / 'lease.json', {'phase', 'request_sequence', 'expires_monotonic_ms'}, origin)
            require(lease['phase'] in {'prepared', 'catalog', 'started'} and
                    type(lease['request_sequence']) is str and (lease['request_sequence'] == '0' or
                    lease['request_sequence'].isascii() and lease['request_sequence'].isdecimal() and
                    not lease['request_sequence'].startswith('0') and len(lease['request_sequence']) <= 19 and
                    int(lease['request_sequence']) <= 2**63 - 1) and
                    type(lease['expires_monotonic_ms']) is int and lease['expires_monotonic_ms'] > 0, 'LEASE_INVALID')
        for name in ('p', 'w'):
            if name in names:
                info = (self.directory / name).lstat()
                require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid(), 'SOCKET_INVALID')
        if 'g' in names:
            self.base.remove_gtk_state(self.directory)
        for name in ('p', 'w', 'lease.json', 'owner.json'):
            if name in names:
                (self.directory / name).unlink()
        self.directory.rmdir()
        fsync_directory(self.root)

    def finish(self, reason, *, origin=None, evidence='live_owner', observed=None):
        """Publish process proof before unlinking, so a crash does not lose that proof."""
        origin = self.boot if origin is None else origin
        observed = self.boot if observed is None else observed
        require(reason in REASONS and valid_boot(observed), 'RECEIPT_INVALID')
        value = dict(self.marker(origin), observed_boot_id=observed, process_evidence=evidence, reason=reason,
                     process_complete=True, pipes_eof=evidence == 'live_owner', remote_cleaned=False, receipt_written=True)
        try:
            self.checked_root()
            if os.path.lexists(self.receipt):
                prior = self.read_receipt(origin)
                # Preserve the original proof and reason on retries/recovery.
                value = dict(prior)
                if prior['remote_cleaned']:
                    require(not os.path.lexists(self.directory), 'RECEIPT_CONFLICT')
                    return None
            else:
                self.base.write_record(self.receipt, value, replace=False)
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            return 'receipt_write'
        try:
            self.clean_directory(origin)
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            return 'directory_cleanup'
        try:
            self.base.write_record(self.receipt, dict(value, remote_cleaned=True))
            self.read_receipt(origin)
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            return 'receipt_write'
        return None


class Wire:
    """At most one frame plus one bounded output queue, with nonblocking I/O."""
    def __init__(self, context, base, control_fd, output_fd):
        self.context, self.base = context, base
        self.control_fd, self.output_fd = control_fd, output_fd
        self.input = bytearray()
        self.output = bytearray()
        self.event_sequence = self.request_sequence = 0
        self.eof = self.output_lost = False
        self.selector = selectors.DefaultSelector()
        os.set_blocking(control_fd, False)
        os.set_blocking(output_fd, False)
        self.selector.register(control_fd, selectors.EVENT_READ)

    def emit(self, name, payload):
        next_sequence = self.event_sequence + 1
        require(next_sequence <= 2**63 - 1, 'SEQUENCE_EXHAUSTED')
        row = dict(self.context.marker(), sequence=str(next_sequence), request_sequence=str(self.request_sequence),
                   event=dict(type=name, payload=payload))
        raw = json.dumps(row, separators=(',', ':'), ensure_ascii=True).encode() + b'\n'
        require(len(raw) <= MAX_LINE and len(self.output) + len(raw) <= MAX_QUEUE, 'OUTPUT_QUEUE_LIMIT')
        self.output.extend(raw)
        self.event_sequence = next_sequence

    def flush(self):
        if not self.output or self.output_lost:
            return
        try:
            count = os.write(self.output_fd, self.output[:4096])
            if count:
                del self.output[:count]
        except BlockingIOError:
            pass
        except (BrokenPipeError, OSError):
            self.output_lost = True
            self.output.clear()

    def poll(self, timeout=.025, *, commands=True):
        self.flush()
        for key, _ in self.selector.select(timeout):
            try:
                raw = os.read(key.fd, 4096)
            except BlockingIOError:
                continue
            if not raw:
                self.eof = True
                self.selector.unregister(key.fd)
            elif commands:
                self.input.extend(raw)
                require(len(self.input) <= MAX_LINE, 'INPUT_LIMIT')
        if not commands or b'\n' not in self.input:
            return None
        raw, _, remaining = self.input.partition(b'\n')
        self.input = bytearray(remaining)
        value = self.base.decode(bytes(raw))
        marker = self.context.marker()
        require(type(value) is dict and set(value) == set(marker) | {'sequence', 'command'} and
                all(type(value.get(key)) is type(item) and value[key] == item for key, item in marker.items()), 'COMMAND_SCOPE')
        require(type(value['sequence']) is str and value['sequence'] == str(self.request_sequence + 1) and
                self.request_sequence < 2**63 - 1, 'COMMAND_SEQUENCE')
        command = value['command']
        require(type(command) is dict and type(command.get('type')) is str and
                ((command['type'] in {'inspect', 'heartbeat', 'stop'} and set(command) == {'type'}) or
                 (command['type'] == 'start' and set(command) == {'type', 'selection'})), 'COMMAND_INVALID')
        self.request_sequence += 1
        return command

    def finish_output(self, timeout=.5):
        deadline = time.monotonic() + timeout
        while self.output and not self.output_lost and time.monotonic() < deadline:
            self.flush()
            if self.output:
                time.sleep(.005)

    def close(self):
        self.selector.close()


def serve(bundle, context, desktop, owner_module, control_fd=0, output_fd=1, *, lease_seconds=LEASE_SECONDS,
          current_boot=None, instance_factory=None, cleanup_seconds=3.0):
    """Test injection is Python-only; the fixed bootstrap always uses production defaults."""
    require(0 < lease_seconds <= LEASE_SECONDS and 0 < cleanup_seconds <= 5, 'DEADLINE_INVALID')
    wire = Wire(context, bundle.base, control_fd, output_fd)
    state = instance = None
    prepared = False
    phase, reason, failure = 'prepared', None, None
    interrupted = []
    handlers = {number: signal.getsignal(number) for number in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)}
    for number in handlers:
        signal.signal(number, lambda *_: interrupted.append(True) if not interrupted else None)
    try:
        try:
            state = State(bundle, context, boot_id() if current_boot is None else current_boot)
            state.prepare(owner_module.identity(os.getpid()))
            prepared = True
            instance = (desktop.Instance if instance_factory is None else instance_factory)(bundle, context, owner_module, state.directory)
            expires = time.monotonic() + lease_seconds
            state.lease(phase, 0, expires)
            wire.emit('prepared', dict(lease_ms=int(lease_seconds * 1000), boot_id=state.boot, remote_socket=str(state.directory / 'p')))
            while reason is None and failure is None:
                if interrupted:
                    reason = 'input_eof'
                    break
                if time.monotonic() >= expires:
                    reason = 'lease_expired'
                    break
                try:
                    # Logs have a per-iteration drain budget, not a lifetime byte
                    # limit. Only framed control traffic has cumulative buffering.
                    status = instance.drain(enforce=False)
                except Exception:
                    failure = 'protocol'
                    break
                if phase == 'started' and not status['running']:
                    reason = 'process_exited'
                    break
                command = wire.poll(min(.025, max(0, expires - time.monotonic())))
                if time.monotonic() >= expires:
                    reason = 'lease_expired'
                    break
                if wire.eof or wire.output_lost:
                    reason = 'input_eof'
                    break
                if command is None:
                    continue
                action = command['type']
                if action == 'stop':
                    reason = 'stop'
                elif action == 'heartbeat':
                    expires = time.monotonic() + lease_seconds
                    state.lease(phase, wire.request_sequence, expires)
                    wire.emit('heartbeat', dict(phase=phase))
                elif action == 'inspect':
                    require(phase == 'prepared', 'COMMAND_PHASE')
                    expires = time.monotonic() + lease_seconds
                    state.lease(phase, wire.request_sequence, expires)
                    try:
                        instance.inspect()
                        catalog = bundle.catalog(context)
                    except Exception:
                        failure = 'tool_inspection'
                        continue
                    phase = 'catalog'
                    state.lease(phase, wire.request_sequence, expires)
                    wire.emit('catalog', catalog)
                elif action == 'start':
                    require(phase == 'catalog', 'COMMAND_PHASE')
                    try:
                        desktop.validate_selection(context, bundle._catalog(context)['revision'], command['selection'])
                        expires = time.monotonic() + lease_seconds
                        # owner.json and the observed boot are already durable before any spawn.
                        state.lease(phase, wire.request_sequence, expires)
                        instance.start(command['selection'], deadline=expires)
                    except Exception:
                        failure = 'start'
                        continue
                    phase = 'started'
                    state.lease(phase, wire.request_sequence, expires)
                    wire.emit('started', dict(app_id=desktop.APP_ID, mode='gui'))
        except Exception:
            failure = 'protocol'

        pending_sent = False
        while True:
            report = dict(process_complete=True, pipes_eof=True, complete=True)
            if instance is not None:
                try:
                    report = instance.close(timeout=cleanup_seconds)
                except Exception:
                    report = dict(process_complete=False, pipes_eof=False, complete=False)
            cleanup_error = None
            if report['process_complete'] and report['pipes_eof']:
                cleanup_error = 'directory_cleanup' if not prepared else state.finish(reason or failure or 'protocol')
            else:
                cleanup_error = 'process_ownership' if not report['process_complete'] else 'pipe_eof'
            if cleanup_error is None:
                if not pending_sent:
                    try:
                        if failure is None:
                            wire.emit('stopped', dict(reason=reason, process_complete=True, pipes_eof=True,
                                                     remote_cleaned=True, receipt_written=True))
                        else:
                            wire.emit('pending', dict(reason=failure, process_complete=True, pipes_eof=True))
                    except Exception:
                        pass
                wire.finish_output()
                return dict(complete=True, pending=False, reason=failure or reason)
            if not pending_sent:
                try:
                    wire.emit('pending', dict(reason=failure or cleanup_error, process_complete=report['process_complete'],
                                             pipes_eof=report['pipes_eof']))
                except Exception:
                    pass
                pending_sent = True
            wire.finish_output()
            # No new launch/command after terminal Pending. Keep the same live owner even
            # after SSH EOF; a later successful retry publishes the private receipt.
            if instance is None or report['complete']:
                return dict(complete=False, pending=True, reason=cleanup_error)
            retry = time.monotonic() + .25
            while time.monotonic() < retry:
                try:
                    wire.poll(.025, commands=False)
                except Exception:
                    pass
    finally:
        wire.close()
        for number, handler in handlers.items():
            signal.signal(number, handler)


def recover(bundle, context, expected_boot_id, *, current_boot=None):
    """No PID lookup or signal. Unknown same-boot ownership always remains pending."""
    require(expected_boot_id == '-' or valid_boot(expected_boot_id), 'BOOT_ID_INVALID')
    state = State(bundle, context, boot_id() if current_boot is None else current_boot)
    result = dict(context.marker(), expected_boot_id=expected_boot_id, origin_boot_id=None, boot_id=state.boot,
                  status='pending', process_evidence='unknown', process_complete=False, pipes_eof=False,
                  remote_cleaned=False, receipt_written=False)
    try:
        state.checked_root()
        receipt = state.read_receipt() if os.path.lexists(state.receipt) else None
        owner = None
        if os.path.lexists(state.directory):
            if os.path.lexists(state.directory / 'owner.json') or receipt is None:
                owner = state.read_owner()
            else:
                state.base.checked_directory(state.directory)
                with os.scandir(state.directory) as entries:
                    require(next(entries, None) is None, 'DIRECTORY_NOT_EMPTY')
        origin = receipt['boot_id'] if receipt is not None else owner['boot_id'] if owner is not None else None
        require(origin is not None and (expected_boot_id == '-' or expected_boot_id == origin) and
                (owner is None or owner['boot_id'] == origin), 'RECORD_SCOPE')
        result['origin_boot_id'] = origin
        if receipt is not None:
            evidence = 'receipt' if receipt['process_evidence'] == 'live_owner' else 'boot_changed'
            result.update(process_evidence=evidence, process_complete=True, pipes_eof=receipt['pipes_eof'], receipt_written=True)
            if receipt['remote_cleaned']:
                require(not os.path.lexists(state.directory), 'RECEIPT_CONFLICT')
                result.update(status='complete', remote_cleaned=True)
                return result
            require(state.finish(receipt['reason'], origin=origin, evidence=receipt['process_evidence'],
                                 observed=receipt['observed_boot_id']) is None, 'CLEANUP_PENDING')
        elif state.boot != origin:
            require(owner is not None, 'RECORD_SCOPE')
            result.update(process_evidence='boot_changed', process_complete=True)
            require(state.finish('reboot', origin=origin, evidence='boot_changed') is None, 'CLEANUP_PENDING')
        else:
            return result
        final = state.read_receipt(origin)
        require(final['remote_cleaned'], 'CLEANUP_PENDING')
        result.update(status='complete', remote_cleaned=True, receipt_written=True)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        pass
    return result


def main(argv, *, base, desktop, owner, source_hashes):
    """Fixed argv: operation + immutable context + pins (+ prior boot for recovery)."""
    os.umask(0o077)
    try:
        require(type(argv) is list and len(argv) in {11, 12} and argv[0] in {'serve', 'recover'} and
                len(argv) == (11 if argv[0] == 'serve' else 12), 'BOOTSTRAP_INVALID')
        require(type(source_hashes) is dict and set(source_hashes) == {'base', 'adapter', 'owner', 'wire', 'demo'} and
                all(desktop.hexadecimal(value, 64) for value in source_hashes.values()), 'PIN_INVALID')
        base.system_python()
        home = base.account_home()
        context = desktop.Context(*argv[1:5])
        require(argv[5] == str(os.getuid()) and os.getuid() != 0 and
                all(desktop.hexadecimal(value, 64) for value in argv[6:11]), 'BOOTSTRAP_INVALID')
        bundle = desktop.Bundle(base, home, argv[7], source_hashes['adapter'], source_hashes['owner'],
                                source_hashes['demo'], source_hashes['wire'])
        require(bundle.base_hash == argv[6] and bundle.base_hashes['waypipe'] == argv[8] and
                bundle.base_hashes['eidolon-shm-fixture'] == argv[9] and bundle.hashes['eidolon-gtk3-desktop'] == argv[10] and
                bundle.base_hashes['eidolon-linux-session.py'] == source_hashes['base'], 'PIN_MISMATCH')
        if argv[0] == 'serve':
            result = serve(bundle, context, desktop, owner)
            return 0 if result['complete'] else 1
        result = recover(bundle, context, argv[11])
        raw = json.dumps(result, separators=(',', ':'), ensure_ascii=True).encode() + b'\n'
        require(len(raw) <= MAX_LINE, 'RECORD_LIMIT')
        os.set_blocking(1, False)
        deadline = time.monotonic() + 2
        while raw and time.monotonic() < deadline:
            try:
                count = os.write(1, raw)
                raw = raw[count:]
            except BlockingIOError:
                time.sleep(.005)
        require(not raw, 'OUTPUT_UNAVAILABLE')
        return 0 if result['status'] == 'complete' else 1
    except Exception:
        try:
            os.write(2, b'DESKTOP_HELPER_FAILED\n')
        except OSError:
            pass
        return 1
