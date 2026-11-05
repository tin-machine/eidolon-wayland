"""wireの+8混入、押下残存、keymap不一致を成功扱いしない。"""
import copy
import hashlib
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).parents[1]))
from quest_checks import keyboard_result, keyboard_wire_result


class KeyboardWireTests(unittest.TestCase):
    def fixture(self):
        nonce, build = 'a'*32, dict(profile='debug')
        package = dict(dependencies=dict(keymaps={layout: dict(sha256=digit*64) for layout,digit in [('us','1'),('jis','2')]}))
        rows = []
        for index, layout in enumerate(['us','jis']):
            keyboard = dict(keyboard=True, layout=layout, include_paths=0, keymap_sha256=package['dependencies']['keymaps'][layout]['sha256'],
                            wire_keymap_sha256='3'*64, focus_window=None, pressed=0, delivered=12, resets=2)
            client = dict(keymap_format=1, keymap_sha256='3'*64, keymap_bytes=50000, repeat=[25,600], enters=[[],[]], leaves=2,
                          keys=[[42,1,123],[3,1,123],[3,0,123],[42,0,123],[30,1,123],[30,0,123],
                                [29,1,123],[42,1,123],[30,1,123],[29,0,124],[30,0,124],[42,0,124]],
                          modifiers=[[1,0,0,0],[0,0,0,0]], order=['keymap','repeat','enter']+['key']*12+['leave','enter','leave'], error=None)
            report = dict(state='pass', error=None, uid=4242, layout=layout, fixture_revision='eidolon-keyboard-wire-fixture-v1',
                          nonce=hashlib.sha256(f'{nonce}:{index}'.encode()).hexdigest()[:16], synthetic_input=True, input_time_ms=123,
                          physical_input_verified=False, old_focus_rejected=True, checkpoints=5, shifted_two=ord('@' if layout=='us' else '"'), keyboard=keyboard, client=client)
            report.update({k:0 for k in ['clients_after','objects_after','surfaces_after','windows_after','pixel_bytes_after',
                                        'pool_bytes_after','fixture_fds_after','fixture_maps_after']})
            rows.append(dict(cycle=index+1, scenario='keyboard_'+layout, report=report))
        result = dict(case='keyboard-wire', nonce=nonce, result='pass', cycles=rows, last_handle='1', accept_ms=1, stop_ms=10,
                      after_stop=dict(state='Stopped', compositor_workers=0, dependency_workers=0, helper_workers=0,
                                      render=dict(workers=0), transport=dict(workers=0, owned_connections=0)))
        snapshot = dict(schema=1, build_info=build, check=result, runtime=dict(code=0, handle='2', registry=dict(runtimes=1,workers=1),
                        runtime=dict(state='Ready',activity_visible=True,xr_state='FOCUSED',transport=dict(state='Idle'))))
        return snapshot, nonce, build, package

    def test_wire_offset_modifier_repeat_and_layout_must_match(self):
        snapshot, nonce, build, package = self.fixture()
        self.assertTrue(keyboard_wire_result(snapshot,nonce,build,4242,package))
        self.assertFalse(keyboard_wire_result(snapshot,'old',build,4242,package))
        for section, field, value in [('client','keys',[[50,1,123]]), ('client','modifiers',[[1,0,0,0]]),
                ('client','enters',[[42],[]]), ('client','repeat',[0,0]), ('client','keymap_sha256','4'*64),
                ('keyboard','include_paths',1), ('keyboard','pressed',1), ('keyboard','keymap_sha256','4'*64),
                ('keyboard','focus_window','1')]:
            bad = copy.deepcopy(snapshot); bad['check']['cycles'][0]['report'][section][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                keyboard_wire_result(bad,nonce,build,4242,package)
        for field,value in [('uid',4243),('physical_input_verified',True),('synthetic_input',False),('fixture_fds_after',1),('nonce','old')]:
            bad = copy.deepcopy(snapshot); bad['check']['cycles'][1]['report'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                keyboard_wire_result(bad,nonce,build,4242,package)

    def test_old_runtime_and_cleanup_failure_cannot_pass(self):
        snapshot, nonce, build, package = self.fixture()
        snapshot['runtime']['handle'] = '1'
        self.assertFalse(keyboard_wire_result(snapshot,nonce,build,4242,package))
        snapshot['check']['after_stop']['compositor_workers'] = 1
        with self.assertRaises(ValueError):
            keyboard_wire_result(snapshot,nonce,build,4242,package)

    def test_monotonic_wire_clock_wrap_and_zero_reset_are_distinguished(self):
        snapshot,nonce,build,package=self.fixture()
        for row in snapshot['check']['cycles']:
            report=row['report'];report['input_time_ms']=2**32-2
            for index,key in enumerate(report['client']['keys']): key[2]=2**32-2 if index<9 else 3
        self.assertTrue(keyboard_wire_result(snapshot,nonce,build,4242,package))
        snapshot,nonce,build,package=self.fixture()
        snapshot['check']['cycles'][0]['report']['client']['keys'][-1][2]=0
        with self.assertRaises(ValueError):
            keyboard_wire_result(snapshot,nonce,build,4242,package)


class ProductKeyboardTests(unittest.TestCase):
    def fixture(self):
        target = dict(session='1',window='1',scene='1',generation='1')
        package = dict(dependencies=dict(keymaps=dict(us=dict(sha256='1'*64)),waypipe=dict(sha256='2'*64)))
        rows=[]
        for index, stage in enumerate(['before','focused','typed','held','reset','late','refocused','final']):
            keyboard=dict(keyboard=True,layout='us',include_paths=0,keymap_sha256='1'*64,
                          focus_token=None if index in [0,4,5] else ('1' if index<4 else '2'),
                          focus_window=None if index in [0,4,5] else '1', pressed=2 if index==3 else 0,
                          delivered=[0,0,4,6,8,8,8,10][index])
            state=dict(state='Ready',activity_visible=True,xr_state='FOCUSED',scene='1',input_resets=1 if index<4 else 2,
                       app=dict(session='1',mode='foot',callback_clock='target-driven',error=None,input=keyboard,copies=index+1,
                                local=dict(uid=4242,executable_sha256='2'*64)),
                       transport=dict(state='Ready',session='1',credential_requests=1,
                                      app_forward=dict(uid=4343,mode='foot',state='running',error=None)),
                       render=dict(targets=[dict(key=copy.deepcopy(target),role='windows',frames=3)]))
            rows.append(dict(stage=stage,runtime=state))
        restored=dict(state='Ready',activity_visible=True,xr_state='FOCUSED',transport=dict(state='Idle',pending_cleanup=[]),
                      render=dict(targets=[dict(role='diagnostic',frames=3)]))
        result=dict(case='keyboard',nonce='a'*32,result='pass',consumer='SDK',synthetic_input=True,synthetic_visibility=False,
                    physical_input_verified=False,physical_entry_verified=False,text_verified=False,product_host_path=True,
                    target=target,cycles=rows,old_handle='1',new_handle='2',restored=restored,
                    stop=dict(old_runtime_destroyed=True,stop_ms=100))
        return dict(schema=1,build_info={},check=result),package

    def test_production_focus_repeat_release_epoch_and_foot_must_match(self):
        snapshot,package=self.fixture()
        self.assertTrue(keyboard_result(snapshot,'a'*32,{},4242,4343,package))
        for index,section,field,value in [(2,'input','delivered',6),(4,'input','pressed',1),(5,'input','delivered',9),
                (6,'input','focus_window',None),(7,'input','keymap_sha256','4'*64)]:
            bad=copy.deepcopy(snapshot); bad['check']['cycles'][index]['runtime']['app'][section][field]=value
            with self.subTest(index=index,field=field),self.assertRaises(ValueError):
                keyboard_result(bad,'a'*32,{},4242,4343,package)
        bad=copy.deepcopy(snapshot);bad['check']['cycles'][4]['runtime']['input_resets']=1
        with self.assertRaises(ValueError):
            keyboard_result(bad,'a'*32,{},4242,4343,package)

    def test_synthetic_scope_and_stop_are_required(self):
        snapshot,package=self.fixture()
        for field,value in [('physical_input_verified',True),('physical_entry_verified',True),('text_verified',True),('synthetic_visibility',True)]:
            bad=copy.deepcopy(snapshot);bad['check'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):
                keyboard_result(bad,'a'*32,{},4242,4343,package)
        snapshot['check']['stop']['old_runtime_destroyed']=False
        with self.assertRaises(ValueError):
            keyboard_result(snapshot,'a'*32,{},4242,4343,package)
