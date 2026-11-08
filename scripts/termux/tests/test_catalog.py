"""実file/argv/制御pipeでNX10の読込原子性と非実行の照会契約を検査する。"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('catalog_launcher', Path(__file__).resolve().parents[1] / 'eidolon-session.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def toml(apps):
    # JSON stringのエスケープは、このfixtureで使うTOML basic stringと互換。
    chunks = ['schema = 1', 'apps = []'] if not apps else ['schema = 1']
    for app in apps:
        chunks.append('[[apps]]')
        chunks.extend(f'{key} = {json.dumps(value, ensure_ascii=False)}' for key, value in app.items())
    return ('\n'.join(chunks) + '\n').encode()


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='eidolon-catalog-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.prefix = self.home / 'prefix'
        (self.prefix / 'bin').mkdir(parents=True)
        (self.prefix / 'bin/bash').symlink_to('/bin/bash')
        self.catalog = launcher.LauncherCatalog(self.home, self.prefix)
        self.config = self.home / 'launcher.toml'
        self.script = self.home / '日本語 tool; name.py'
        self.script.write_text('import json,sys,os\nprint(json.dumps(dict(args=sys.argv[1:],cwd=os.getcwd())))\n')
        self.app = dict(id='script', label='日本語ツール', mode='terminal', executable=self.script.name,
                        interpreter=sys.executable, args=['one two', '日本語', '', 'line\nline', '$(touch sentinel)', ';touch sentinel'],
                        cwd='.', icon='builtin:script')

    def write(self, apps):
        self.config.write_bytes(toml(apps))
        self.config.chmod(0o600)
        return self.catalog.request(dict(kind='reload', path='launcher.toml'))

    def test_real_exec_preserves_empty_unicode_space_and_shell_characters(self):
        result = self.write([self.app])
        self.assertIsNone(result['error'])
        argv, cwd = self.catalog.selected_command(result['revision'], 'script')
        process = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=True, timeout=3)
        value = json.loads(process.stdout)
        self.assertEqual(value, dict(args=self.app['args'], cwd=str(self.home)))
        self.assertFalse((self.home / 'sentinel').exists())
        self.assertNotIn('one two', json.dumps(result))

    def test_configuration_and_inspection_do_not_execute(self):
        self.script.write_text('raise RuntimeError("must not execute on reload")\n')
        result = self.write([self.app])
        self.assertTrue(result['entries'][0]['available'])
        inspected = self.catalog.request(dict(kind='inspect', definition=self.app))
        self.assertTrue(inspected['inspected']['available'])
        self.assertEqual(inspected['entries'], [])
        self.assertEqual(self.catalog.apps[0]['args'], self.app['args'])

    def test_bad_reload_keeps_previous_revision_order_and_path(self):
        good = self.write([self.app])
        snapshot = copy.deepcopy(self.catalog.apps)
        for data in [b'[[apps', b'schema = 2\napps = []', b'schema = true\napps = []',
                     b'schema = 1\napps = []\nshell = "bad"', toml([self.app, self.app]),
                     toml([{**self.app, 'extra': 'bad'}]), b'\xff', b'x' * (launcher.CATALOG_BYTES + 1)]:
            with self.subTest(data=data[:35]):
                self.config.write_bytes(data)
                result = self.catalog.request(dict(kind='reload', path='launcher.toml'))
                self.assertIsNotNone(result['error'])
                self.assertEqual(result['revision'], good['revision'])
                self.assertEqual(self.catalog.apps, snapshot)
                self.assertEqual(self.catalog.path, self.config)

    def test_missing_parser_and_deeply_nested_toml_keep_the_current_catalog(self):
        before = self.write([self.app])
        with patch.object(launcher, 'tomllib', None):
            result = self.catalog.request(dict(kind='reload', path='launcher.toml'))
        self.assertEqual(result['error'], 'CATALOG_UNAVAILABLE')
        self.config.write_text('schema = 1\napps = ' + '[' * 4000 + ']' * 4000)
        result = self.catalog.request(dict(kind='reload', path='launcher.toml'))
        self.assertIsNotNone(result['error'])
        self.assertEqual(result['revision'], before['revision'])

    def test_deleted_executable_disables_only_that_entry_and_rechecks_before_launch(self):
        other = {**self.app, 'id': 'other', 'executable': str(self.prefix / 'bin/bash'), 'args': ['--version']}
        other.pop('interpreter')
        result = self.write([self.app, other])
        self.script.unlink()
        page = self.catalog.request(dict(kind='page', revision=result['revision'], offset=0))
        self.assertEqual(page['entries'][0]['error'], 'PATH_MISSING')
        self.assertTrue(page['entries'][1]['available'])
        with self.assertRaisesRegex(launcher.CatalogError, 'PATH_MISSING'):
            self.catalog.selected_command(result['revision'], 'script')

    def test_revision_prevents_using_stale_selection(self):
        result = self.write([self.app])
        self.config.write_bytes(toml([{**self.app, 'args': ['changed']}]))
        with self.assertRaisesRegex(launcher.CatalogError, 'CATALOG_CHANGED'):
            self.catalog.selected_command(result['revision'], 'script')
        self.catalog.reload('launcher.toml')
        page = self.catalog.request(dict(kind='page', revision=result['revision'], offset=0))
        self.assertEqual(page['error'], 'CATALOG_CHANGED')
        with self.assertRaisesRegex(launcher.CatalogError, 'APP_NOT_FOUND'):
            self.catalog.selected_command(self.catalog.revision, 'absent')

    def test_home_relative_paths_and_symlink_resolution_do_not_expand_shell_syntax(self):
        nested = self.home / 'config'
        nested.mkdir()
        config = nested / 'apps.toml'
        config.write_bytes(toml([self.app]))
        config.chmod(0o600)
        self.catalog.reload('config/apps.toml')
        argv, cwd = self.catalog.selected_command(self.catalog.revision, 'script')
        self.assertEqual(argv[1], str(self.script))
        link = self.home / 'linked'
        link.symlink_to(self.script)
        self.assertEqual(self.catalog.command({**self.app, 'executable': 'linked'}), (argv, cwd))
        for path in ['~/missing', '$HOME/missing', '*.py']:
            with self.subTest(path=path), self.assertRaisesRegex(launcher.CatalogError, 'PATH_MISSING'):
                self.catalog.command({**self.app, 'executable': path})

    def test_file_types_and_permissions_do_not_block_catalog_loop(self):
        fifo = self.home / 'pipe'
        os.mkfifo(fifo)
        server = socket.socket(socket.AF_UNIX)
        self.addCleanup(server.close)
        server.bind(str(self.home / 'socket'))
        link = self.home / 'link'
        link.symlink_to(self.config)
        self.write([self.app])
        for name in ['pipe', 'socket', 'link', 'prefix']:
            with self.subTest(name=name):
                result = self.catalog.request(dict(kind='reload', path=name))
                self.assertIsNotNone(result['error'])
        self.config.chmod(0o666)
        self.assertEqual(self.catalog.request(dict(kind='reload', path='launcher.toml'))['error'], 'CATALOG_FILE_UNSAFE')

    def test_entry_permission_and_directory_errors_are_isolated(self):
        definitions = [({**self.app, 'interpreter': str(self.script)}, 'INTERPRETER_UNAVAILABLE'),
                       ({**self.app, 'cwd': str(self.script)}, 'CWD_UNAVAILABLE'),
                       ({**self.app, 'executable': '.'}, 'EXECUTABLE_NOT_FILE')]
        direct = copy.deepcopy(self.app)
        direct.pop('interpreter')
        definitions.append((direct, 'EXECUTABLE_DENIED'))
        for app, error in definitions:
            with self.subTest(error=error):
                self.assertEqual(self.catalog.summary(launcher.catalog_definition(app))['error'], error)

    def test_paging_limits_order_and_builtin_fallback(self):
        before = self.catalog.request(dict(kind='reload', path='absent.toml'))
        self.assertTrue(before['builtin'])
        self.assertEqual([entry['id'] for entry in before['entries']], ['terminal', 'editor'])
        self.assertTrue(before['entries'][0]['available'])
        self.assertEqual(before['entries'][1]['error'], 'PATH_MISSING')
        apps = [{**self.app, 'id': f'app{i}', 'label': '界' * 53} for i in range(64)]
        result = self.write(apps)
        ids = []
        for offset in range(0, 64, 8):
            page = self.catalog.request(dict(kind='page', revision=result['revision'], offset=offset))
            self.assertLess(len(json.dumps(page).encode()), 15000)
            ids.extend(entry['id'] for entry in page['entries'])
        self.assertEqual(ids, [app['id'] for app in apps])
        self.assertEqual(self.write(apps + [{**self.app, 'id': 'overflow'}])['error'], 'CATALOG_LIMIT')
        for offset in [-1, 65, 1, True, '0']:
            self.assertEqual(self.catalog.request(dict(kind='page', revision=result['revision'], offset=offset))['error'], 'CATALOG_INVALID')

    def test_limits_unknown_fields_and_gui_gate(self):
        for change in [dict(id='../bad'), dict(label='\n'), dict(args=['\0']), dict(args='--help'),
                       dict(args=['x'] * 65), dict(args=['x' * 2049]), dict(args=['x' * 2048] * 5),
                       dict(executable='x' * 1025), dict(executable='\ud800'), dict(mode='shell'), dict(shell=True)]:
            with self.subTest(keys=list(change)):
                result = self.catalog.request(dict(kind='inspect', definition={**self.app, **change}))
                self.assertIsNotNone(result['error'])
        inspected = self.catalog.request(dict(kind='inspect', definition={**self.app, 'mode': 'gui', 'icon': 'https://invalid/image.png'}))
        self.assertEqual(inspected['inspected']['error'], 'GUI_NOT_VERIFIED')
        self.assertEqual(inspected['inspected']['icon'], 'builtin:script')

    def gui_definition(self):
        return dict(id='freedoom',label='Freedoom',mode='gui',executable='/data/data/com.termux/files/usr/bin/chocolate-doom',args=[],cwd='.',icon='builtin:game',
            execution_profile=dict(kind='freedoom_phase1_software',assets_run_id='a'*32,manifest_sha256='b'*64))

    def test_fixed_gui_profile_toml_roundtrip_and_save_preserve_other_terminal(self):
        app=launcher.catalog_definition(self.gui_definition())
        self.assertEqual(launcher.LauncherCatalog.parse(launcher.LauncherCatalog.encode([app])),[app])
        self.catalog.save('launcher.toml',None,self.app);revision=self.catalog.revision
        self.catalog.save('launcher.toml',revision,app)
        self.assertEqual([row['id'] for row in self.catalog.apps],['script','freedoom'])
        self.assertEqual(self.catalog.apps[1]['execution_profile'],app['execution_profile'])
        self.assertTrue(self.catalog.summary(self.catalog.apps[0])['available'])
        self.assertFalse(self.catalog.summary(app)['available'])

    def test_gui_profile_only_enables_fixed_policy_and_never_routes_through_terminal_command(self):
        from types import SimpleNamespace
        app=self.gui_definition()
        for change in (dict(mode='terminal'),dict(args=['-c','body']),dict(executable='/bin/sh'),dict(interpreter=sys.executable),dict(cwd='elsewhere'),
            dict(execution_profile={**app['execution_profile'],'env':{'LD_PRELOAD':'body'}})):
            with self.assertRaises(launcher.CatalogError):launcher.catalog_definition({**app,**change})
        with patch.object(launcher,'gui_policy',return_value=SimpleNamespace(inspect_assets=lambda *_:None)):
            summary=self.catalog.summary(launcher.catalog_definition(app));self.assertTrue(summary['available']);self.assertEqual(summary['execution_profile'],app['execution_profile'])
            selected=self.catalog.launch_definition(dict(kind='temporary',definition=app));self.assertEqual(selected['mode'],'gui')
            self.catalog.save('launcher.toml',None,app)
            with self.assertRaisesRegex(launcher.CatalogError,'GUI_NOT_VERIFIED'):self.catalog.selected_command(self.catalog.revision,'freedoom')
        with patch.object(launcher,'gui_policy',side_effect=ValueError('PRIVATE')):
            summary=self.catalog.summary(launcher.catalog_definition(app));self.assertEqual(summary['error'],'GUI_NOT_VERIFIED');self.assertNotIn('PRIVATE',json.dumps(summary))

    def test_real_supervisor_catalog_errors_do_not_stop_or_spawn_children(self):
        nonce = 'e' * 16
        read, write = os.pipe()
        events = []
        operations = [dict(kind='reload', path='missing.toml'), dict(kind='inspect', definition=self.app),
                      dict(kind='inspect', definition={**self.app, 'args': ['x' * 1900] * 3}),
                      dict(kind='inspect', definition={**self.app, 'args': 'invalid'})]
        sequence = 0

        def send(value):
            os.write(write, (json.dumps(value, ensure_ascii=False) + '\n').encode())

        def emit(event):
            nonlocal sequence
            events.append(event)
            if event['type'] in {'prepared', 'catalog_v1'}:
                if sequence < len(operations):
                    send(dict(type='catalog_v1', schema=1, nonce=nonce, request_id=str(sequence + 1), operation=operations[sequence]))
                    sequence += 1
                else:
                    send(dict(type='stop', nonce=nonce))

        try:
            report = launcher.supervise(self.home, nonce, read, emit, lease_seconds=2,
                                       factory=lambda *_: self.fail('catalog spawned child'))
        finally:
            os.close(write)
            os.close(read)
        replies = [event for event in events if event['type'] == 'catalog_v1']
        self.assertEqual(len(replies), 4)
        self.assertEqual(replies[-1]['error'], 'CATALOG_LIMIT')
        self.assertEqual(report['reason'], 'Stop')
        self.assertTrue(report['cleaned'])
        self.assertIsNone(report['child'])
        self.assertNotIn('one two', json.dumps(events))

    def test_save_new_default_path_and_update_keep_order_and_literal_arguments(self):
        path = '.config/eidolon/launcher.toml'
        app = {**self.app, 'args': [*self.app['args'], 'emoji 😀', '\x7f\x85']}
        first = self.catalog.request(dict(kind='save', path=path, expected_revision=None, definition=app))
        self.assertIsNone(first['error'])
        target = self.home / path
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(launcher.LauncherCatalog.parse(target.read_bytes())[0]['args'], app['args'])
        second = self.catalog.request(dict(kind='save', path=path, expected_revision=first['revision'],
                                           definition={**self.app, 'id': 'other'}))
        self.assertIsNone(second['error'])
        third = self.catalog.request(dict(kind='save', path=path, expected_revision=second['revision'],
                                          definition={**app, 'label': '更新'}))
        self.assertIsNone(third['error'])
        self.assertEqual([entry['id'] for entry in third['entries']], ['script', 'other'])
        self.assertEqual(third['entries'][0]['label'], '更新')
        self.assertNotIn('one two', json.dumps(third))
        self.assertFalse(list(target.parent.glob('*.tmp')))

    def test_save_stale_revision_and_creation_collision_do_not_overwrite(self):
        first = self.write([self.app])
        before = self.config.read_bytes()
        for revision in [None, '0' * 64, True]:
            result = self.catalog.request(dict(kind='save', path='launcher.toml', expected_revision=revision,
                                               definition={**self.app, 'label': 'not saved'}))
            self.assertIsNotNone(result['error'])
            self.assertEqual(self.config.read_bytes(), before)
        self.config.write_bytes(toml([{**self.app, 'label': 'external edit'}]))
        result = self.catalog.request(dict(kind='save', path='launcher.toml', expected_revision=first['revision'], definition=self.app))
        self.assertEqual(result['error'], 'CATALOG_CHANGED')
        self.assertIn(b'external edit', self.config.read_bytes())

    def test_external_edit_during_save_is_detected_before_exchange(self):
        first = self.write([self.app])
        original_sync = launcher.os.fsync
        changes = []
        def sync(fd):
            original_sync(fd)
            if not changes:
                changes.append(True)
                self.config.write_bytes(toml([{**self.app, 'label': 'external wins'}]))
        with patch.object(launcher.os, 'fsync', side_effect=sync):
            result = self.catalog.request(dict(kind='save', path='launcher.toml', expected_revision=first['revision'], definition=self.app))
        self.assertEqual(result['error'], 'CATALOG_CHANGED')
        self.assertIn(b'external wins', self.config.read_bytes())
        self.assertEqual(self.catalog.revision, first['revision'])
        self.assertFalse(list(self.home.glob('*.tmp')))

    def test_concurrent_savers_have_exactly_one_winner(self):
        first = self.write([self.app])
        barrier = threading.Barrier(2)
        results = []
        def save(label):
            catalog = launcher.LauncherCatalog(self.home, self.prefix)
            barrier.wait(timeout=2)
            results.append(catalog.request(dict(kind='save', path='launcher.toml', expected_revision=first['revision'],
                                               definition={**self.app, 'label': label})))
        threads = [threading.Thread(target=save, args=(label,)) for label in ['first', 'second']]
        for thread in threads: thread.start()
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertEqual([result['error'] for result in results].count(None), 1)
        self.assertEqual([result['error'] for result in results].count('CATALOG_CHANGED'), 1)
        self.assertIn(launcher.LauncherCatalog.parse(self.config.read_bytes())[0]['label'], ['first', 'second'])

    def test_save_preserves_existing_file_on_invalid_or_over_limit_update(self):
        first = self.write([{**self.app, 'id': f'app{i}'} for i in range(64)])
        before = self.config.read_bytes()
        result = self.catalog.request(dict(kind='save', path='launcher.toml', expected_revision=first['revision'], definition=self.app))
        self.assertEqual(result['error'], 'CATALOG_LIMIT')
        self.assertEqual(self.config.read_bytes(), before)
        (self.home / 'unsafe').mkdir(mode=0o777)
        (self.home / 'unsafe').chmod(0o777)
        result = self.catalog.request(dict(kind='save', path='unsafe/new.toml', expected_revision=None, definition=self.app))
        self.assertEqual(result['error'], 'CATALOG_FILE_UNSAFE')
        self.assertFalse((self.home / 'unsafe/new.toml').exists())


if __name__ == '__main__':
    unittest.main()
