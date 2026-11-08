"""通常Linux固定GTKのadapter。公開CLI/任意argvなし。SSH ownerの固定bootstrapから呼ぶ。"""
import hashlib
import json
import os
from pathlib import Path
import selectors
import stat
import time

REVISION = 'eidolon-linux-desktop-v2'
APP_ID = 'gtk3-desktop'
FILES = {'eidolon_linux_desktop.py', 'eidolon_linux_desktop_session.py', 'eidolon_linux_process_owner.py',
         'gtk3-desktop-demo.c', 'eidolon-gtk3-desktop'}
MAX_LINE = 16384


def require(value, token):
    if not value: raise ValueError(token)


def hexadecimal(value, count):
    return type(value) is str and len(value) == count and all(c in '0123456789abcdef' for c in value)


def decimal(value):
    return type(value) is str and value.isascii() and value.isdecimal() and not value.startswith('0') and len(value) <= 19 and int(value) <= 2**63 - 1


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate_selection(context, revision, selection):
    require(type(selection) is dict and set(selection) == {'scope', 'revision', 'app_id'} and
            selection == dict(scope=context.scope, revision=revision, app_id=APP_ID), 'CAPABILITY_STALE')


class Context:
    __slots__ = ('session','revision','nonce','scope','_sealed')
    def __setattr__(self, name, value):
        if getattr(self, '_sealed', False): raise AttributeError('CONTEXT_IMMUTABLE')
        object.__setattr__(self, name, value)
    def __init__(self, session, revision, binding, nonce):
        require(decimal(session) and decimal(revision) and hexadecimal(binding, 64) and hexadecimal(nonce, 16), 'CONTEXT_INVALID')
        self.session, self.revision, self.nonce = session, revision, nonce
        self.scope = digest(dict(session=session, revision=revision, binding=binding, nonce=nonce));self._sealed = True
    def __repr__(self): return 'LinuxDesktopContext(<private>)'
    def marker(self):
        return dict(schema=1, backend='linux_desktop', revision=REVISION, session=self.session,
                    profile_revision=self.revision, scope=self.scope, nonce=self.nonce, uid=os.getuid())


class Bundle:
    """base is the separately hash-verified, unchanged B4/B6 helper module; owner is pinned C2a."""
    def __init__(self, base, home, manifest_hash, source_hash, owner_hash, demo_source_hash, wire_hash):
        require(all(hexadecimal(value, 64) for value in (manifest_hash, source_hash, owner_hash, demo_source_hash, wire_hash)), 'PIN_INVALID')
        self.base, self.home = base, home
        directory, base_hashes, base_hash = base.bundle(home)
        raw = base.private_read(directory / 'desktop-bundle.json')
        require(hashlib.sha256(raw).hexdigest() == manifest_hash, 'DESKTOP_BUNDLE_CHANGED')
        manifest = base.decode(raw)
        require(type(manifest) is dict and set(manifest) == {'schema', 'backend', 'revision', 'base_bundle_sha256', 'gtk_version', 'files'} and
                type(manifest['schema']) is int and manifest['schema'] == 1 and manifest['backend'] == 'linux_posix' and
                manifest['revision'] == REVISION and manifest['base_bundle_sha256'] == base_hash and
                type(manifest['gtk_version']) is str and manifest['gtk_version'].startswith('3.24.') and
                manifest['gtk_version'][5:].isascii() and manifest['gtk_version'][5:].isdecimal() and len(manifest['gtk_version'][5:]) <= 3 and
                type(manifest['files']) is dict and set(manifest['files']) == FILES, 'DESKTOP_BUNDLE_INVALID')
        hashes = {}
        for name in sorted(FILES):
            expected = manifest['files'][name];require(hexadecimal(expected, 64), 'DESKTOP_BUNDLE_INVALID')
            data = base.private_read(directory / name, 4 * 1024 * 1024, executable=name == 'eidolon-gtk3-desktop')
            hashes[name] = hashlib.sha256(data).hexdigest();require(hashes[name] == expected, 'DESKTOP_FILE_CHANGED')
        require(hashes['eidolon_linux_desktop.py'] == source_hash and hashes['eidolon_linux_process_owner.py'] == owner_hash and
                hashes['gtk3-desktop-demo.c'] == demo_source_hash and
                hashes['eidolon_linux_desktop_session.py'] == wire_hash, 'DESKTOP_SOURCE_CHANGED')
        self.directory, self.manifest_hash, self.hashes, self.gtk_version = directory, manifest_hash, hashes, manifest['gtk_version']
        self.base_hash, self.base_hashes = base_hash, base_hashes
    def recheck(self):
        current = Bundle(self.base, self.home, self.manifest_hash, self.hashes['eidolon_linux_desktop.py'],
                         self.hashes['eidolon_linux_process_owner.py'], self.hashes['gtk3-desktop-demo.c'],
                         self.hashes['eidolon_linux_desktop_session.py'])
        require(current.hashes == self.hashes, 'DESKTOP_BUNDLE_CHANGED')
    def _catalog(self, context):
        revision = digest(dict(scope=context.scope, bundle=self.manifest_hash, app=APP_ID))
        return dict(schema=1, namespace='linux-desktop', scope=context.scope, revision=revision,
                    entries=[dict(id=APP_ID, label='Linux アプリ', mode='gui', icon='builtin:script', available=True)],
                    arbitrary_exec=False, terminal=False, instance_limit=1)
    def catalog(self, context):
        return dict(self._catalog(context), base_bundle_sha256=self.base_hash, desktop_bundle_sha256=self.manifest_hash,
                    waypipe_sha256=self.base_hashes['waypipe'], shm_fixture_sha256=self.base_hashes['eidolon-shm-fixture'],
                    desktop_binary_sha256=self.hashes['eidolon-gtk3-desktop'],
                    helper_sha256=self.base_hashes['eidolon-linux-session.py'], adapter_sha256=self.hashes['eidolon_linux_desktop.py'],
                    owner_sha256=self.hashes['eidolon_linux_process_owner.py'], demo_source_sha256=self.hashes['gtk3-desktop-demo.c'],
                    wire_sha256=self.hashes['eidolon_linux_desktop_session.py'], waypipe_version='0.11.3')
    def command(self, directory, context, selection):
        catalog = self._catalog(context)
        validate_selection(context, catalog['revision'], selection)
        self.recheck()
        info = (directory / 'p').lstat()
        require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid() and not os.path.lexists(directory / 'w'), 'STREAMLOCAL_INVALID')
        return [str(self.directory / 'waypipe'), '--no-gpu', '--compress', 'none', '--threads', '1', '--socket', str(directory / 'p'),
                '--display', str(directory / 'w'), 'server', '--', str(self.directory / 'eidolon-gtk3-desktop'), '--run-id', context.nonce * 2]


class Instance:
    """One dedicated helper owns one launch. Caller keeps this object when close returns pending."""
    def __init__(self, bundle, context, owner_module, directory):
        self.bundle, self.context, self.directory = bundle, context, directory
        self.owner_module = owner_module;self.owner = owner_module.ProcessOwner()
        self.tool_child = None;self.inspection_error = None;self.tool_bytes = dict(stdout=0,stderr=0)
        self.child = None; self.closing = False; self.closed = False; self.stdout = 0; self.stderr = 0
        self.selector = selectors.DefaultSelector();self.pipes = set();self.started = False;self.inspected = False;self.inspection_started = False
    def inspect(self):
        require(not self.inspection_started and not self.started and not self.closing, 'INSTANCE_CLOSED')
        self.inspection_started = True
        try:
            self.bundle.recheck()
            child = self.owner.spawn([str(self.bundle.directory / 'waypipe'), '--version'],
                                     env=self.bundle.base.environment(self.bundle.home, self.directory))
        except BaseException:
            self.closing = True;self.inspection_error = 'TOOL_INSPECTION_FAILED'
            raise
        selector = selectors.DefaultSelector();output = bytearray();error_bytes = 0
        try:
            for name in ('stdout', 'stderr'):
                pipe = getattr(child, name);os.set_blocking(pipe.fileno(), False);selector.register(pipe, selectors.EVENT_READ, name)
            deadline = time.monotonic() + 3
            while selector.get_map() or child.poll() is None:
                require(time.monotonic() < deadline, 'TOOL_TIMEOUT')
                for key, _ in selector.select(.01):
                    raw = os.read(key.fd, 4096)
                    if not raw: selector.unregister(key.fd)
                    elif key.data == 'stdout': output.extend(raw)
                    else: error_bytes += len(raw)
                    require(len(output) <= 4096 and error_bytes <= 4096, 'TOOL_OUTPUT_LIMIT')
            require(child.returncode == 0 and output == self.bundle.base.VERSION, 'WAYPIPE_VERSION_MISMATCH')
            require(not self.owner_module.descendant_snapshot(self.owner.owner), 'TOOL_CHILD_REMAINED')
            self.inspected = True
        except BaseException as error:
            self.closing = True;self.tool_child = child
            self.inspection_error = str(error) if str(error) in {'TOOL_TIMEOUT','TOOL_OUTPUT_LIMIT','WAYPIPE_VERSION_MISMATCH','TOOL_CHILD_REMAINED'} else 'TOOL_INSPECTION_FAILED'
            self.tool_bytes = dict(stdout=len(output),stderr=error_bytes)
            # Keep even a partially drained version pipe under this owner; explicit close is not EOF evidence.
            for name in ('stdout','stderr'):
                pipe = getattr(child,name);tag = 'tool_' + name
                self.selector.register(pipe,selectors.EVENT_READ,tag);self.pipes.add(tag)
            raise
        finally:
            selector.close()
            if self.tool_child is None:child.stdout.close();child.stderr.close()
        return self.bundle._catalog(self.context)
    def start(self, selection, *, deadline=None):
        require(self.inspected and not self.started and not self.closing and not self.closed, 'INSTANCE_CLOSED')
        command = self.bundle.command(self.directory, self.context, selection)
        require(deadline is None or time.monotonic() < deadline, 'LEASE_EXPIRED')
        self.started = True # No second attempt, including a spawn failure.
        self.child = self.owner.spawn(command, env=self.bundle.base.gtk_environment(self.bundle.home, self.directory))
        for name in ('stdout', 'stderr'):
            pipe = getattr(self.child, name);os.set_blocking(pipe.fileno(), False)
            self.selector.register(pipe, selectors.EVENT_READ, name);self.pipes.add(name)
    def drain(self, *, enforce=True):
        require(not self.closed, 'INSTANCE_CLOSED')
        for key, _ in self.selector.select(0):
            raw = os.read(key.fd, 4096)
            if not raw: self.selector.unregister(key.fd);self.pipes.discard(key.data)
            elif key.data.startswith('tool_'):self.tool_bytes[key.data[5:]] += len(raw)
            elif key.data == 'stdout': self.stdout += len(raw)
            else: self.stderr += len(raw)
        if enforce: require(self.stdout <= 65536 and self.stderr <= 65536, 'OUTPUT_LIMIT')
        return dict(running=self.child is not None and self.child.poll() is None, stdout_bytes=self.stdout,
                    stderr_bytes=self.stderr, pipes_eof=not self.pipes)
    def close(self, timeout=3.0):
        self.closing = True
        require(0 < timeout <= 5, 'DEADLINE_INVALID')
        if self.closed: return dict(complete=True, pending=False, process_complete=True, pipes_eof=True, open_pidfds=0, inspection_error=self.inspection_error)
        report = self.owner.finish(timeout=timeout, grace=min(.25, timeout / 2))
        # Exit and pipe EOF are separate; close() alone does not prove remote socket/receipt cleanup.
        deadline = time.monotonic() + .25
        while self.pipes and time.monotonic() < deadline:
            self.drain(enforce=False);time.sleep(.005)
        if report['complete'] and not self.pipes:
            for child in (self.child,self.tool_child):
                if child is not None:child.stdout.close();child.stderr.close()
            self.selector.close();self.closed = True
        return dict(complete=self.closed, pending=not self.closed, process_complete=report['complete'],
                    pipes_eof=not self.pipes, open_pidfds=report['open_pidfds'], inspection_error=self.inspection_error)
