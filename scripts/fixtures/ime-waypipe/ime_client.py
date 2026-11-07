#!/usr/bin/env python3
"""固定語のみを使う独立Wayland v1 client。本文はartifact/標準出力へ出さない。"""
import array
import json
import os
from pathlib import Path
import socket
import struct
import sys
import time

def words(*values):
    return struct.pack('=' + 'I' * len(values), *values)

def string(text):
    data = text.encode('utf-8') + b'\0'
    return words(len(data)) + data + bytes(-len(data) % 4)

def read_string(body, offset=0):
    size, = struct.unpack_from('=I', body, offset)
    if size == 0:
        return '', offset + 4
    value = body[offset + 4:offset + 4 + size]
    if len(value) != size or not value.endswith(b'\0'):
        raise RuntimeError('WIRE_STRING')
    return value[:-1].decode('utf-8'), offset + 4 + (size + 3) // 4 * 4

class Client:
    def __init__(self, path, control):
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.settimeout(4)
        self.socket.connect(path)
        self.control = socket.socket(socket.AF_UNIX)
        self.control.settimeout(4)
        self.control.connect(control)
        self.buffer = bytearray()
        self.next_id = 2
        self.kinds = {1: 'wl_display'}
        self.globals = {}
        self.callbacks = set()
        self.configured = set()
        self.windows = []
        self.focus = None
        self.enters = []
        self.leaves = 0
        self.serial = 0
        self.pending = {}
        self.applied = []
        self.done_serials = []
        self.registry = self.new('wl_registry')
        self.send(1, 1, words(self.registry))
        self.sync()
        self.bound = {}
        for interface, version in [('wl_compositor', 4), ('wl_shm', 1), ('wl_seat', 5), ('xdg_wm_base', 1), ('zwp_text_input_manager_v3', 1)]:
            name, advertised = self.globals[interface]
            if advertised < version or (interface == 'zwp_text_input_manager_v3' and advertised != 1):
                raise RuntimeError('GLOBAL_VERSION')
            obj = self.new(interface)
            self.send(self.registry, 0, words(name) + string(interface) + words(version, obj))
            self.bound[interface] = obj
        self.text_input = self.new('zwp_text_input_v3')
        self.send(self.bound['zwp_text_input_manager_v3'], 1, words(self.text_input, self.bound['wl_seat']))
        self.sync()

    def new(self, kind):
        obj = self.next_id
        self.next_id += 1
        self.kinds[obj] = kind
        return obj

    def send(self, obj, opcode, payload=b'', fd=None):
        message = words(obj, ((len(payload) + 8) << 16) | opcode) + payload
        if fd is None:
            self.socket.sendall(message)
        else:
            count = self.socket.sendmsg([message], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [fd]))])
            if count != len(message):
                raise RuntimeError('PARTIAL_FD_MESSAGE')

    def dispatch(self, obj, opcode, body):
        kind = self.kinds.get(obj)
        if obj == 1 and opcode == 0:
            raise RuntimeError('WAYLAND_PROTOCOL_ERROR')
        if kind == 'wl_registry' and opcode == 0:
            name, = struct.unpack_from('=I', body)
            interface, offset = read_string(body, 4)
            version, = struct.unpack_from('=I', body, offset)
            self.globals[interface] = name, version
        elif kind == 'wl_callback' and opcode == 0:
            self.callbacks.add(obj)
        elif kind == 'xdg_wm_base' and opcode == 0:
            self.send(obj, 3, body)
        elif kind == 'xdg_surface' and opcode == 0:
            self.send(obj, 4, body)
            self.configured.add(obj)
        elif kind == 'zwp_text_input_v3':
            if opcode == 0:
                self.focus, = struct.unpack('=I', body)
                self.enters.append(self.focus)
                self.send(obj, 1)
                self.send(obj, 5, words(0, 13))
                self.send(obj, 6, words(1, 1, 1, 1))
                self.send(obj, 7)
                self.serial += 1
            elif opcode == 1:
                self.focus = None
                self.pending.clear()
                self.leaves += 1
            elif opcode == 2:
                self.pending['preedit'] = read_string(body)[0]
            elif opcode == 3:
                self.pending['commit'] = read_string(body)[0]
            elif opcode == 4:
                raise RuntimeError('UNEXPECTED_SURROUNDING_DELETE')
            elif opcode == 5:
                serial, = struct.unpack('=I', body)
                if serial != self.serial:
                    raise RuntimeError('DONE_SERIAL')
                self.applied.append((self.focus, self.pending.get('preedit', ''), self.pending.get('commit', '')))
                self.done_serials.append(serial)
                self.pending.clear()

    def pump(self):
        chunk = self.socket.recv(16384)
        if not chunk:
            raise RuntimeError('WAYLAND_EOF')
        self.buffer.extend(chunk)
        while len(self.buffer) >= 8:
            obj, header = struct.unpack_from('=II', self.buffer)
            size, opcode = header >> 16, header & 65535
            if size < 8 or size % 4:
                raise RuntimeError('WIRE_SIZE')
            if len(self.buffer) < size:
                return
            body = bytes(self.buffer[8:size])
            del self.buffer[:size]
            self.dispatch(obj, opcode, body)

    def sync(self):
        callback = self.new('wl_callback')
        self.send(1, 0, words(callback))
        while callback not in self.callbacks:
            self.pump()
        self.callbacks.remove(callback)

    def step(self, stage):
        self.control.sendall((stage + '\n').encode('ascii'))
        reply = bytearray()
        while not reply.endswith(b'\n'):
            chunk = self.control.recv(128)
            if not chunk:
                raise RuntimeError('CONTROL_EOF')
            reply.extend(chunk)
            if len(reply) > 256:
                raise RuntimeError('CONTROL_LIMIT')
        if reply != b'ok\n':
            raise RuntimeError('CONTROL_REJECTED')
        # enter callbackから送ったenable/commitまでserverへ反映させる。
        self.sync()
        self.sync()

    def map_window(self, label):
        surface = self.new('wl_surface')
        xdg = self.new('xdg_surface')
        top = self.new('xdg_toplevel')
        self.send(self.bound['wl_compositor'], 0, words(surface))
        self.send(self.bound['xdg_wm_base'], 2, words(xdg, surface))
        self.send(xdg, 1, words(top))
        self.send(top, 2, string(label))
        self.send(surface, 6)
        self.sync()
        if xdg not in self.configured:
            raise RuntimeError('NOT_CONFIGURED')
        fd = os.memfd_create('eidolon-ime-waypipe-fixture', os.MFD_CLOEXEC)
        pool = self.new('wl_shm_pool')
        buffer = self.new('wl_buffer')
        try:
            os.ftruncate(fd, 16 * 8 * 4)
            os.write(fd, bytes([0, 64, 128, 255]) * (16 * 8))
            self.send(self.bound['wl_shm'], 0, words(pool, 16 * 8 * 4), fd=fd)
            self.send(pool, 0, words(buffer, 0, 16, 8, 16 * 4, 0))
            self.send(surface, 1, words(buffer, 0, 0))
            self.send(surface, 2, words(0, 0, 16, 8))
            self.send(surface, 6)
            self.send(pool, 1)
            self.sync()
        finally:
            os.close(fd)
        self.windows.append((surface, xdg, top, buffer))
        return surface

    def destroy(self, index):
        surface, xdg, top, buffer = self.windows[index]
        self.send(top, 0)
        self.send(xdg, 0)
        self.send(surface, 0)
        self.send(buffer, 0)
        self.sync()

def main():
    os.umask(0o077)
    display, control, artifact = sys.argv[1:]
    deadline = time.monotonic() + 8
    while not Path(display).exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('REMOTE_DISPLAY_TIMEOUT')
        time.sleep(.01)
    client = Client(display, control)
    a = client.map_window('ime-fixture-a')
    b = client.map_window('ime-fixture-b')
    client.step('focus_a')
    if client.focus != a:
        raise RuntimeError('FOCUS_A')
    client.step('normal_a')
    if client.applied[-2:] != [(a, 'にほん', ''), (a, '', '日本語')]:
        raise RuntimeError('NORMAL_A')
    count = len(client.applied)
    client.step('stale_a')
    if client.applied[count:] != [(a, '', '')]:
        raise RuntimeError('STALE_A_LEAK')
    client.step('capture_geometry')
    if client.applied[-1] != (a, '編集中', ''):
        raise RuntimeError('GEOMETRY_PREEDIT')
    count = len(client.applied)
    client.send(client.text_input, 6, words(7, 7, 1, 1))
    client.send(client.text_input, 7)
    client.serial += 1
    client.sync()
    client.step('apply_geometry')
    if client.applied[count:] != [(a, '継続', '幾何確定')]:
        raise RuntimeError('GEOMETRY_LOST_OR_DUPLICATED')
    client.step('focus_b')
    if client.focus != b:
        raise RuntimeError('FOCUS_B')
    count = len(client.applied)
    client.step('late_a')
    if client.applied[count:] != [(b, 'べつ', '別窓')]:
        raise RuntimeError('LATE_A_LEAK')
    client.step('reset')
    if client.focus is not None:
        raise RuntimeError('RESET_FOCUS')
    client.step('focus_a_again')
    client.step('recover')
    if client.applied[-1] != (a, '', '復帰'):
        raise RuntimeError('RECOVERY')
    client.destroy(0)
    client.step('destroyed')
    client.destroy(1)
    client.send(client.text_input, 0)
    client.sync()
    proof = dict(schema=1, state='pass', fixture_revision='eidolon-ime-waypipe-client-v2', manager_version=client.globals['zwp_text_input_manager_v3'][1], mapped=2, enters=len(client.enters), leaves=client.leaves, done=len(client.applied), commits=sum(bool(x[2]) for x in client.applied), preedits=sum(bool(x[1]) for x in client.applied), done_serials=client.done_serials, fixed_payload_only=True, payload_logged=False)
    Path(artifact).write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n')
    client.socket.close()
    client.control.sendall(b'complete\n')
    if client.control.recv(32) != b'ok\n':
        raise RuntimeError('COMPLETE')
    client.control.close()

if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, KeyError, ValueError, struct.error) as error:
        # 固定error tagだけを出す。受信したwire本文を例外へ埋め込まない。
        print(type(error).__name__, file=sys.stderr)
        sys.exit(1)
