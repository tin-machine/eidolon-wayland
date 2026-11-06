import copy
from pathlib import Path
import sys
import json
import runpy
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gpu_fd_checks import validate


class GpuFdChecksTests(unittest.TestCase):
    def fixture(self, mode):
        def sample(time, tids=(), alive=False):
            return dict(elapsed_ms=time, fds=100+len(tids), sdk_loaded=mode == 'sdk', product_native_loaded=False,
                        gpu=[dict(fd=100+i, tid=tid, alive=alive) for i,tid in enumerate(tids)])
        def row(tid, i):
            return dict(tid=tid, draws=40, unbound=True, surface_destroyed=True, context_destroyed=True,
                        thread_released=True, during=sample(100+i, [tid] if mode == 'sdk' else [], True),
                        after_join=sample(100+i, [tid] if mode == 'sdk' else []))
        return dict(schema=1, case='gpu-fds', nonce='a'*32, mode=mode, status='observed', acceptance='not_run',
                    uid=100, pid=50, build=130, build_info=dict(source_digest='digest', profile='debug', apk_version_code=130),
                    cycles=10, hold_ms=2000, baseline=sample(0), after_empty=sample(90),
                    empty_threads=[dict(tid=x) for x in range(1,4)],
                    fresh=[row(x, x-10) for x in range(10,20)], reused=[row(20, i) for i in range(10)],
                    after_fresh_wait=sample(30109, list(range(10,20)) if mode == 'sdk' else []),
                    after_reuse_join=sample(40000, list(range(10,21)) if mode == 'sdk' else []),
                    final=sample(70000, list(range(10,21)) if mode == 'sdk' else []))

    def test_reports_observation_without_turning_growth_into_acceptance(self):
        for mode, expected in [('platform', 0), ('sdk', 10)]:
            result=validate(self.fixture(mode), 'a'*32, mode, {'source_digest':'digest'}, 100)
            self.assertEqual(result['fresh_retained'], expected)
            self.assertEqual(result['acceptance'], 'not_run')
            self.assertEqual(result['status'], 'observed')

    def test_rejects_product_contamination_incomplete_cleanup_and_wrong_control(self):
        original=self.fixture('sdk')
        changes=[(['baseline','product_native_loaded'],True), (['final','sdk_loaded'],False),
                 (['fresh',0,'thread_released'],False), (['reused',0,'tid'],999),
                 (['fresh',0,'after_join','gpu',0,'alive'],True), (['final','elapsed_ms'],40001),
                 (['nonce'],'b'*32), (['uid'],101), (['build_info','source_digest'],'old'),
                 (['status'],'running')]
        for path,value in changes:
            proof=copy.deepcopy(original);target=proof
            for key in path[:-1]:target=target[key]
            target[path[-1]]=value
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate(proof,'a'*32,'sdk',{'source_digest':'digest'},100)

    def test_busy_product_is_rejected_without_force_stop_or_settings_changes(self):
        calls=[]
        class Device:
            def __init__(self, serial, run): pass
            def validate(self): return dict(model='Quest 3', api=34)
            def adb(self, *command, **kwargs):
                calls.append(command)
                if command[:3] == ('shell','pm','list'): return 'package:io.github.tinmachine.eidolon uid:100\n'
                if command[0] == 'shell' and command[1].startswith('pidof'): return '123\n'
                if command[:2] == ('exec-out','run-as'):
                    return json.dumps(dict(process_id=123, runtime=dict(runtime=dict(transport=dict(owned_connections=1)))))
                raise AssertionError('接続中に変更を実行: '+repr(command))
        with tempfile.TemporaryDirectory() as tmp, mock.patch('quest_device.QuestDevice', Device), \
                mock.patch.object(sys,'argv',['quest-gpu-fds','--serial','fixture','--artifact-root',tmp,'--pause-boundary']):
            with self.assertRaisesRegex(RuntimeError, 'SSH切断'):
                runpy.run_path(str(Path(__file__).resolve().parents[1]/'quest-gpu-fds'),run_name='__main__')
            result=json.loads(next(Path(tmp).glob('*/result.json')).read_text())
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['cleanup'],[])
        self.assertEqual(len(calls),3)

    def test_transient_empty_adb_read_waits_for_nonce_report(self):
        owner=self
        class Device:
            def __init__(self, serial, run): self.mode='platform';self.reads=0
            def validate(self): return dict(model='Quest 3',api=34)
            def start_log(self, *args, **kwargs):
                return mock.Mock()
            def adb(self, *command, **kwargs):
                if command[:3] == ('shell','pm','list'): return 'package:io.github.tinmachine.eidolon uid:100\n'
                if command[0] == 'shell' and command[1].startswith('pidof'): return ''
                if any('GpuFdSdkActivity' in x for x in command): self.mode='sdk';self.reads=0
                if command[:2] == ('exec-out','run-as'):
                    self.reads+=1
                    return '' if self.reads==1 else json.dumps(owner.fixture(self.mode))
                return ''
        class Instrument:
            def __init__(self, *args, **kwargs):
                kwargs['stdout'].write('INSTRUMENTATION_CODE: -1\n');kwargs['stdout'].flush()
            def poll(self): return None
            def wait(self, **kwargs): return 0
        with tempfile.TemporaryDirectory() as tmp, mock.patch('quest_device.QuestDevice',Device), \
                mock.patch('subprocess.Popen',Instrument), mock.patch('secrets.token_hex',lambda n:'a'*(n*2)), \
                mock.patch('native_build.identity',return_value={'source_digest':'digest'}), \
                mock.patch('time.sleep'), mock.patch.object(sys,'argv',['quest-gpu-fds','--serial','fixture','--artifact-root',tmp]):
            runpy.run_path(str(Path(__file__).resolve().parents[1]/'quest-gpu-fds'),run_name='__main__')
            result=json.loads(next(Path(tmp).glob('*/result.json')).read_text())
            self.assertEqual(result['status'],'observed')
            self.assertEqual(len(result['modes']),2)


if __name__ == '__main__': unittest.main()
