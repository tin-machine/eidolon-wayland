#!/usr/bin/env python3
"""専用SDL子のWayland traceを集計する。入力本文・生traceは保存しない。"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import threading
import time

STDOUT_LIMIT = 16384
TRACE_LIMIT = 8 * 1024 * 1024
LINE_LIMIT = 8192
OBJECT_LIMIT = 8192
STAGES = ('render', 'gl-load', 'gl-window', 'gl-context', 'gles-load', 'gles-window', 'gles-context')
ERROR_CLASSES = {'none', 'gl_library_unavailable', 'egl_library_unavailable', 'egl_symbol_missing',
                 'egl_display_unavailable', 'egl_initialize_failed', 'egl_config_unavailable',
                 'egl_context_failed', 'egl_surface_failed', 'unclassified_sdl_error'}


class EglLogSummary:
    """Mesa26.2.6の既知messageを固定分類へ変換し、本文・path・symbolを結果へ出さない。"""
    # egl/main/eglapi.c, drivers/dri2/{egl_dri2,platform_wayland}.c, loader/loader.c。
    PATTERNS = {
        'forced_software': b"Found 'LIBGL_ALWAYS_SOFTWARE' set, will use a CPU renderer",
        'dri2_screen_failed': b'egl: failed to create dri2 screen',
        'egl_device_setup_failed': b'DRI2: failed to setup EGLDevice',
        'egl_configs_missing': b'failed to add any EGLConfigs',
        'dri_format_missing': b'No DRI config supports native format ',
        'dri_config_validation_failed': b'DRI2: failed to validate config ',
        'driver_load_failed': b'MESA-LOADER: failed to open ',
        'driver_opened': b'MESA-LOADER: dlopen(',
        'device_info_failed': b'MESA-LOADER: failed to retrieve device information',
        'wayland_format_map_failed': b'wayland-egl: we could not map the format table ',
        'wayland_device_open_failed': b'wayland-egl: could not open ',
        'wayland_drm_magic_failed': b'wayland-egl: drmGetMagic failed',
        'wayland_prime_missing': b'wayland-egl: display does not support prime',
    }
    INFORMATION = {'forced_software', 'driver_opened'}

    def __init__(self, enabled):
        self.enabled = enabled
        self.pending = bytearray()
        self.bytes = self.lines = self.unmatched = self.truncated = 0
        self.complete, self.dropping = True, False
        self.known, self.first_failure = {}, None

    def feed(self, data):
        remaining = max(0, TRACE_LIMIT - self.bytes)
        self.bytes += len(data)
        if len(data) > remaining:
            self.complete = False
        data = data[:remaining]
        if self.dropping:
            end = data.find(b'\n')
            if end < 0:
                return
            self.lines += 1
            data, self.dropping = data[end + 1:], False
        self.pending.extend(data)
        while b'\n' in self.pending:
            line, _, rest = self.pending.partition(b'\n')
            self.pending = bytearray(rest)
            self.lines += 1
            if len(line) > LINE_LIMIT:
                self.complete = False
                self.truncated += 1
                continue
            matched = False
            for key, pattern in self.PATTERNS.items():
                if pattern in line:
                    self.known[key] = self.known.get(key, 0) + 1
                    matched = True
                    if self.first_failure is None and key not in self.INFORMATION:
                        self.first_failure = key
            if not matched:
                self.unmatched += 1
        if len(self.pending) > LINE_LIMIT:
            self.pending.clear()
            self.dropping, self.complete = True, False
            self.truncated += 1

    def summary(self):
        return dict(schema=1, profile='mesa-26.2.6-known-v1', enabled=self.enabled, bytes=self.bytes, lines=self.lines,
                    complete=self.complete and not self.pending and not self.dropping,
                    known=dict(sorted(self.known.items())), first_failure=self.first_failure,
                    unmatched_lines=self.unmatched, truncated_lines=self.truncated)


class WaylandTrace:
    """xdg_toplevelへattachされたbufferだけを数える。cursor用SHMは合格根拠にしない。"""
    def __init__(self):
        self.pending = bytearray()
        self.bytes = 0
        self.complete = True
        self.buffers, self.xdg_surfaces, self.attached, self.callbacks = {}, {}, {}, {}
        self.buffer_formats, self.attached_formats, self.content_formats = {}, {}, set()
        self.toplevels, self.content_buffers = set(), set()
        self.globals = {}
        self.shm = self.non_shm = self.unknown = self.releases = self.frames = 0
        self.protocol_errors = 0

    def feed(self, data):
        self.bytes += len(data)
        if self.bytes > TRACE_LIMIT:
            self.complete = False
            return
        self.pending.extend(data)
        while b'\n' in self.pending:
            line, _, rest = self.pending.partition(b'\n')
            self.pending = bytearray(rest)
            if len(line) <= LINE_LIMIT:
                self.line(line.decode('utf-8', errors='replace'))
            else:
                self.complete = False
        if len(self.pending) > LINE_LIMIT:
            self.pending.clear()
            self.complete = False

    def line(self, line):
        if sum(map(len, (self.buffers, self.xdg_surfaces, self.attached, self.callbacks, self.content_buffers, self.toplevels,
                         self.buffer_formats, self.attached_formats))) > OBJECT_LIMIT:
            self.complete = False
            return
        obj = r'[@#](\d+)'
        match = re.search(r'wl_registry' + obj + r'\.bind\(\d+, "([a-z0-9_]+)", (\d+),', line)
        if match and len(self.globals) < 128:
            self.globals[match[2]] = int(match[3])
        match = re.search(r'get_xdg_surface\(new id xdg_surface' + obj + r', wl_surface' + obj, line)
        if match:
            self.xdg_surfaces[int(match[1])] = int(match[2])
        match = re.search(r'xdg_surface' + obj + r'\.get_toplevel\(', line)
        if match and int(match[1]) in self.xdg_surfaces:
            self.toplevels.add(self.xdg_surfaces[int(match[1])])
        match = re.search(r'wl_shm_pool' + obj + r'\.create_buffer\(new id wl_buffer' + obj + r', (\d+), (\d+), (\d+), (\d+), (\d+)\)', line)
        if match:
            self.buffers[int(match[2])] = 'shm'
            self.buffer_formats[int(match[2])] = int(match[7])
        # EGL/Mesaが作る非SHM bufferを既知として記録。未知の作成経路も合格扱いしない。
        match = re.search(r'(?:wl_drm[@#]\d+\.create_(?:prime_)?buffer|zwp_linux_buffer_params_v1[@#]\d+\.create_immed)\(new id wl_buffer' + obj, line)
        if not match:
            match = re.search(r'zwp_linux_buffer_params_v1[@#]\d+\.created\(wl_buffer' + obj, line)
        if match:
            self.buffers[int(match[1])] = 'non_shm'
        match = re.search(r'wl_surface' + obj + r'\.attach\(wl_buffer' + obj, line)
        if match:
            surface, buffer = int(match[1]), int(match[2])
            self.attached[surface] = self.buffers.get(buffer, 'unknown')
            self.attached_formats[surface] = self.buffer_formats.get(buffer)
            if surface in self.toplevels:
                self.content_buffers.add(buffer)
        match = re.search(r'wl_surface' + obj + r'\.attach\(nil', line)
        if match:
            self.attached.pop(int(match[1]), None)
            self.attached_formats.pop(int(match[1]), None)
        match = re.search(r'wl_surface' + obj + r'\.commit\(', line)
        if match and int(match[1]) in self.toplevels:
            kind = self.attached.get(int(match[1]))
            if kind == 'shm':
                self.shm += 1
                self.content_formats.add(self.attached_formats[int(match[1])])
            elif kind == 'non_shm': self.non_shm += 1
            elif kind == 'unknown': self.unknown += 1
        match = re.search(r'wl_surface' + obj + r'\.frame\(new id wl_callback' + obj, line)
        if match and int(match[1]) in self.toplevels:
            self.callbacks[int(match[2])] = int(match[1])
        match = re.search(r'wl_callback' + obj + r'\.done\(', line)
        if match and self.callbacks.pop(int(match[1]), None) is not None:
            self.frames += 1
        match = re.search(r'wl_buffer' + obj + r'\.release\(', line)
        if match and int(match[1]) in self.content_buffers:
            self.releases += 1
        match = re.search(r'wl_buffer' + obj + r'\.destroy\(', line)
        if match:
            self.buffers.pop(int(match[1]), None)
            self.buffer_formats.pop(int(match[1]), None)
            self.content_buffers.discard(int(match[1]))
        match = re.search(r'wl_surface' + obj + r'\.destroy\(', line)
        if match:
            self.attached.pop(int(match[1]), None)
            self.attached_formats.pop(int(match[1]), None)
            self.toplevels.discard(int(match[1]))
        if re.search(r'wl_display[@#]\d+\.error\(', line):
            self.protocol_errors += 1

    def summary(self):
        return dict(bytes=self.bytes, complete=self.complete and not self.pending,
                    content_shm_formats=sorted(self.content_formats),
                    content_shm_commits=self.shm, content_non_shm_commits=self.non_shm,
                    content_unknown_commits=self.unknown, content_buffer_releases=self.releases,
                    content_frame_callbacks=self.frames, protocol_errors=self.protocol_errors,
                    bound_globals=self.globals)


def probe_report(data):
    if len(data) > STDOUT_LIMIT:
        raise ValueError('OUTPUT_LIMIT')
    value = json.loads(data)
    fields = {'schema', 'probe', 'completed', 'stage', 'exit_reason', 'video_driver', 'renderer',
              'renderer_flags', 'compiled_sdl', 'linked_sdl', 'requested_frames', 'frames', 'pixel_checks',
              'key_down', 'key_up', 'focus_lost', 'close_events', 'synthetic_events',
              'fd_before', 'fd_after', 'error'}
    if (not isinstance(value, dict) or set(value) != fields or type(value['schema']) is not int or value['schema'] != 1
            or value['probe'] != 'eidolon-sdl-v1' or type(value['completed']) is not bool
            or type(value['synthetic_events']) is not bool):
        raise ValueError('PROBE_REPORT_INVALID')
    for name in ['frames', 'requested_frames', 'pixel_checks', 'key_down', 'key_up', 'focus_lost', 'close_events', 'renderer_flags']:
        if type(value[name]) is not int or not 0 <= value[name] <= 1000000:
            raise ValueError('PROBE_REPORT_INVALID')
    if not 2 <= value['requested_frames'] <= 600 or value['frames'] > value['requested_frames']:
        raise ValueError('PROBE_REPORT_INVALID')
    for name in ['fd_before', 'fd_after']:
        if type(value[name]) is not int or not -1 <= value[name] <= 1000000:
            raise ValueError('PROBE_REPORT_INVALID')
    for name in ['compiled_sdl', 'linked_sdl']:
        if not isinstance(value[name], str) or not re.fullmatch(r'\d{1,3}\.\d{1,3}\.\d{1,3}', value[name]):
            raise ValueError('PROBE_REPORT_INVALID')
    for name in ['stage', 'exit_reason', 'video_driver', 'renderer', 'error']:
        if not isinstance(value[name], str) or len(value[name]) > (384 if name == 'error' else 64):
            raise ValueError('PROBE_REPORT_INVALID')
    if value['completed']:
        if (value['stage'] != 'complete' or value['error'] or value['exit_reason'] not in {'frames', 'escape', 'window_close', 'signal'}
                or (value['exit_reason'] == 'frames' and value['frames'] != value['requested_frames'])):
            raise ValueError('PROBE_REPORT_INVALID')
    elif value['stage'] not in {'init', 'window', 'renderer', 'render'} or value['exit_reason'] != 'error':
        raise ValueError('PROBE_REPORT_INVALID')
    return value


def stage_report(data, requested):
    """API outcomeをcleanupより先に保持する。任意本文/未知fieldは受け入れない。"""
    if len(data) > STDOUT_LIMIT or requested not in STAGES[1:]:
        raise ValueError('STAGE_REPORT_INVALID')
    lines = data.splitlines()
    if not lines:
        raise ValueError('STAGE_REPORT_INVALID')
    value = json.loads(lines[0])
    fields = {'schema', 'probe', 'requested_stage', 'stage', 'completed', 'error_class', 'fd_before'}
    reached = ['init', 'attributes', 'load', 'window', 'context']
    target = requested.split('-', 1)[1]
    if (type(value) is not dict or set(value) != fields or type(value['schema']) is not int or value['schema'] != 1
            or value['probe'] != 'eidolon-sdl-egl-stage-v1' or value['requested_stage'] != requested
            or type(value['stage']) is not str or value['stage'] not in reached[:reached.index(target) + 1]
            or type(value['completed']) is not bool or type(value['error_class']) is not str
            or value['error_class'] not in ERROR_CLASSES
            or (value['completed'] and (value['stage'] != target or value['error_class'] != 'none'))
            or (not value['completed'] and value['error_class'] == 'none')
            or type(value['fd_before']) is not int or not -1 <= value['fd_before'] <= 1000000):
        raise ValueError('STAGE_REPORT_INVALID')
    cleanup = None
    cleanup_status = 'missing' if len(lines) == 1 else 'invalid'
    if len(lines) == 2:
        try:
            candidate = json.loads(lines[1])
            if (type(candidate) is dict and set(candidate) == {'schema', 'probe', 'requested_stage', 'completed', 'fd_after'}
                    and type(candidate['schema']) is int and candidate['schema'] == 1
                    and candidate['probe'] == 'eidolon-sdl-egl-cleanup-v1' and candidate['requested_stage'] == requested
                    and candidate['completed'] is True and type(candidate['fd_after']) is int
                    and -1 <= candidate['fd_after'] <= 1000000):
                cleanup, cleanup_status = candidate, 'observed'
        except (ValueError, TypeError):
            pass
    return dict(api=value, cleanup=cleanup, cleanup_status=cleanup_status)


def assess(client, trace, returncode, *, driver, timed_out=False, reclaimed=False, cancelled=False):
    graphics = (client is not None and client['completed'] and returncode == 0 and not timed_out and not cancelled and reclaimed
                and client['exit_reason'] != 'signal' and client['video_driver'] == driver
                and client['renderer_flags'] & 1 and client['frames'] >= 2 and client['pixel_checks'] == client['frames'])
    shm = (graphics and driver == 'wayland' and trace['complete'] and trace['protocol_errors'] == 0
           and trace['content_shm_commits'] >= 2 and trace['content_non_shm_commits'] == 0
           and trace['content_unknown_commits'] == 0 and bool(trace['content_shm_formats'])
           and set(trace['content_shm_formats']) <= {0, 1})  # 現行EidolonのARGB8888/XRGB8888。
    input_result = 'not_run'
    if client is not None and client['key_down'] > 0 and client['key_up'] > 0:
        input_result = 'synthetic' if client['synthetic_events'] else 'observed'
    return dict(graphics_client='completed' if graphics else 'failed',
                shm_gate=('pass' if shm else 'fail') if driver == 'wayland' else 'not_run',
                input_gate=input_result, quest_sdk='not_run')


def group_live(group):
    for path in Path('/proc').iterdir():
        if not path.name.isdecimal():
            continue
        try:
            text = (path / 'stat').read_text()
            fields = text[text.rindex(')') + 2:].split()
            if int(fields[2]) == group and fields[0] != 'Z':
                return True
        except (OSError, ValueError, IndexError):
            continue
    return False


@contextmanager
def cancellation_signals():
    """所有子のspawn前から結果保存まで取消を保持し、signal handlerではI/Oしない。"""
    if threading.current_thread() is not threading.main_thread():
        raise ValueError('PROBE_REQUIRES_MAIN_THREAD')
    cancellation, previous = {'signal': None}, {}

    def interrupted(number, frame):
        if cancellation['signal'] is None:
            cancellation['signal'] = number

    try:
        for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            previous[number] = signal.signal(number, interrupted)
        yield cancellation
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def run(binary, *, driver='wayland', frames=120, timeout=15, inject=False, disable_acceleration=False, stage='render',
        _cancellation=None):
    if stage not in STAGES or (stage != 'render' and (inject or disable_acceleration)):
        raise ValueError('STAGE_INVALID')
    if _cancellation is None:
        with cancellation_signals() as cancellation:
            return run(binary, driver=driver, frames=frames, timeout=timeout, inject=inject,
                       disable_acceleration=disable_acceleration, stage=stage, _cancellation=cancellation)
    binary_hash = hashlib.sha256(binary.read_bytes()).hexdigest()
    environment = os.environ.copy()
    environment.update(SDL_VIDEODRIVER=driver, SDL_RENDER_DRIVER='software')
    if driver == 'wayland' and stage == 'render': environment['WAYLAND_DEBUG'] = 'client'
    else: environment.pop('WAYLAND_DEBUG', None)
    if disable_acceleration: environment['SDL_FRAMEBUFFER_ACCELERATION'] = '0'
    command = [str(binary), '--frames', str(frames)] + (['--inject-events'] if inject else [])
    if stage != 'render': command += ['--stage', stage]
    trace, output, overflow = WaylandTrace(), bytearray(), False
    egl_log = EglLogSummary(environment.get('EGL_LOG_LEVEL') == 'debug')
    started, deadline, timed_out = time.monotonic(), time.monotonic() + timeout, False
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=environment, start_new_session=True, close_fds=True)
    selector = selectors.DefaultSelector()
    for stream, name in [(child.stdout, 'stdout'), (child.stderr, 'stderr')]:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)
    reaped, reclaimed, term_sent, kill_sent = False, False, False, False
    stopping_at = None

    def signal_group(number):
        nonlocal term_sent, kill_sent
        try:
            os.killpg(child.pid, number)
            if number == signal.SIGTERM: term_sent = True
            if number == signal.SIGKILL: kill_sent = True
        except ProcessLookupError:
            pass

    try:
        while True:
            now = time.monotonic()
            if _cancellation['signal'] is not None or now >= deadline or overflow or trace.bytes > TRACE_LIMIT:
                timed_out |= now >= deadline
                if stopping_at is None:
                    stopping_at = now
                if not term_sent:
                    signal_group(signal.SIGTERM)
                if now >= stopping_at + 1 or overflow or trace.bytes > TRACE_LIMIT:
                    signal_group(signal.SIGKILL)
            exited = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
            if exited and not selector.get_map():
                break
            if stopping_at is not None and now >= stopping_at + 2:
                break
            for key, _ in selector.select(0.05):
                data = os.read(key.fd, 65536)
                if not data:
                    selector.unregister(key.fileobj)
                elif key.data == 'stdout':
                    output.extend(data[:max(0, STDOUT_LIMIT + 1 - len(output))])
                    overflow |= len(output) > STDOUT_LIMIT
                else:
                    trace.feed(data)
                    egl_log.feed(data)
    finally:
        # WNOWAITでleaderを予約している間だけsignalし、子孫を含む所有groupを回収する。
        signal_group(signal.SIGKILL)
        until = time.monotonic() + 2
        while group_live(child.pid) and time.monotonic() < until:
            time.sleep(0.01)
        reclaimed = not group_live(child.pid)
        try:
            child.wait(timeout=2)
            reaped = True
        except subprocess.TimeoutExpired:
            reclaimed = False
        pipes_drained = not selector.get_map()
        selector.close()
        child.stdout.close()
        child.stderr.close()
    try: client, error = (probe_report(output) if stage == 'render' else stage_report(output, stage)), None
    except (ValueError, TypeError, UnicodeError): client, error = None, 'PROBE_REPORT_INVALID'
    observation = trace.summary()
    observation['complete'] &= pipes_drained
    cancelled = _cancellation['signal'] is not None
    if stage == 'render':
        gates = assess(client, observation, child.returncode, driver=driver, timed_out=timed_out,
                       reclaimed=reclaimed and reaped, cancelled=cancelled)
    else:
        valid = (client is not None and client['cleanup'] is not None and
                 child.returncode == (0 if client['api']['completed'] else 1) and
                 not timed_out and not cancelled and not overflow and trace.bytes <= TRACE_LIMIT and
                 reclaimed and reaped and pipes_drained)
        gates = dict(graphics_client='not_run', shm_gate='not_run', input_gate='not_run', quest_sdk='not_run',
                     diagnostic_gate='observed' if valid else 'partial' if client is not None else 'failed')
    return dict(schema=1, probe='eidolon-sdl-observation-v1', driver=driver,
                binary_sha256=binary_hash, stage=stage,
                disable_framebuffer_acceleration=disable_acceleration, elapsed_ms=int((time.monotonic() - started) * 1000),
                client=client, trace=observation, egl_log=egl_log.summary(), error=error, returncode=child.returncode,
                timed_out=timed_out, cancelled=cancelled, cancel_signal=_cancellation['signal'],
                output_overflow=overflow, term_sent=term_sent, kill_sent=kill_sent,
                reaped=reaped, group_reclaimed=reclaimed, pipes_drained=pipes_drained, **gates)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--result', type=Path, required=True)
    parser.add_argument('--driver', choices=['wayland', 'dummy'], default='wayland')
    parser.add_argument('--frames', type=int, default=120)
    parser.add_argument('--timeout', type=int, default=15)
    parser.add_argument('--inject-events', action='store_true')
    parser.add_argument('--disable-framebuffer-acceleration', action='store_true')
    parser.add_argument('--stage', choices=STAGES, default='render')
    args = parser.parse_args()
    if not args.binary.is_absolute() or not args.result.is_absolute() or not 2 <= args.frames <= 600 or not 2 <= args.timeout <= 60:
        parser.error('absolute paths / frames 2..600 / timeout 2..60 required')
    if args.stage != 'render' and (args.inject_events or args.disable_framebuffer_acceleration):
        parser.error('diagnostic stages do not accept event injection or framebuffer overrides')
    info = args.binary.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 8 * 1024 * 1024:
        parser.error('bounded regular probe binary required')
    if args.result.exists() or args.result.is_symlink():
        parser.error('result path already exists; choose a new run path')
    os.umask(0o077)
    with cancellation_signals() as cancellation:
        result = run(args.binary, driver=args.driver, frames=args.frames, timeout=args.timeout,
                     inject=args.inject_events, disable_acceleration=args.disable_framebuffer_acceleration,
                     stage=args.stage, _cancellation=cancellation)
        # 元のclient/error/timeout証拠を保持する。取消は独立metadataであり成功へ昇格しない。
        if cancellation['signal'] is not None:
            result.update(cancelled=True, cancel_signal=cancellation['signal'])
            if args.stage == 'render':
                result['graphics_client'] = 'failed'
                if args.driver == 'wayland': result['shm_gate'] = 'fail'
            else: result['diagnostic_gate'] = 'partial' if result['client'] is not None else 'failed'
        with args.result.open('x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
        print(json.dumps({key: result[key] for key in ['stage', 'graphics_client', 'shm_gate', 'input_gate', 'quest_sdk',
                                                      'group_reclaimed', 'error', 'cancelled', 'cancel_signal']}))
        if args.stage != 'render':
            return 0 if cancellation['signal'] is None and result['diagnostic_gate'] == 'observed' else 1
        return 0 if (cancellation['signal'] is None and result['graphics_client'] == 'completed'
                     and (args.driver == 'dummy' or result['shm_gate'] == 'pass')) else 1


if __name__ == '__main__':
    raise SystemExit(main())
