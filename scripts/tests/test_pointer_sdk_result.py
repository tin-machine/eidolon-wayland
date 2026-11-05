"""SDKの捕捉解除・古held・合成scopeを検査する。"""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).parents[1]))
from quest_checks import pointer_result


class PointerSdkTests(unittest.TestCase):
    def fixture(self):
        target = dict(session='1',window='1',scene='1',generation='1')
        package = dict(dependencies=dict(keymaps=dict(us=dict(sha256='1'*64)),waypipe=dict(sha256='2'*64)))
        stages = ['before','hover','pressed','foreign','drag','released','scroll','scroll_stop','held','reset','old_held','refocused']
        rows = []
        for i,stage in enumerate(stages):
            grabbed, unfocused = i in [2,3,4,8], i in [0,1,9,10]
            pointer = dict(ready=True,pressed=int(grabbed),buttons=[0,0,1,1,1,2,2,2,3,4,4,6][i],
                           axis_frames=0 if i<6 else 1 if i==6 else 2,motions=i,
                           capture_window='1' if grabbed else None,capture_surface='3' if grabbed else None)
            inputs = dict(keyboard=True,layout='us',include_paths=0,keymap_sha256='1'*64,pointer=pointer,
                          focus_window=None if unfocused else '1',focus_token=None if unfocused else '1' if i<9 else '2',
                          pressed=int(i==8),delivered=0 if i<8 else 1 if i==8 else 2)
            state = dict(state='Ready',activity_visible=True,xr_state='FOCUSED',scene='1',input_resets=1 if i<9 else 2,
                         app=dict(session='1',mode='foot',callback_clock='target-driven',error=None,input=inputs,
                                  local=dict(uid=4242,executable_sha256='2'*64)),
                         transport=dict(state='Ready',session='1',credential_requests=1,
                                        app_forward=dict(uid=4343,mode='foot',state='running',error=None)),
                         render=dict(targets=[dict(key=target.copy(),role='windows',frames=3)]))
            rows.append(dict(stage=stage,runtime=state))
        points = [dict(expected=uv,uv=uv.copy(),entity='99',world=[(uv[0]-.5)*1.2,1.4+(.5-uv[1])*.9,1.5],
                       hit_point=[(uv[0]-.5)*1.2,1.4+(.5-uv[1])*.9,1.5]) for uv in [[.23,.31],[.73,.61]]]
        result = dict(case='pointer',nonce='a'*32,result='pass',consumer='SDK',dispatch='Scene.onInput',raycast='Scene.lineSegmentIntersect',
                      synthetic_input=True,synthetic_menu=True,product_host_path=True,sources_destroyed=True,
                      synthetic_visibility=False,physical_input_verified=False,os_menu_verified=False,
                      target=target,sources=['90','91'],points=points,cycles=rows,credential_base=0,
                      cancelled=dict(state='Ready',app=None,transport=dict(state='Disconnected',owned_connections=0,tokio_threads=0,pending_cleanup=[],credential_requests=0)),
                      old_handle='1',new_handle='2',stop=dict(old_runtime_destroyed=True,stop_ms=100),
                      restored=dict(state='Ready',activity_visible=True,xr_state='FOCUSED',transport=dict(state='Idle',pending_cleanup=[]),
                                    render=dict(targets=[dict(role='diagnostic',frames=3)])))
        return dict(schema=1,build_info={},check=result),package

    def test_capture_and_reset_are_required(self):
        snapshot,package = self.fixture()
        self.assertTrue(pointer_result(snapshot,'a'*32,{},4242,4343,package))
        for index,section,field,value in [(3,'pointer','capture_surface','4'),(4,'pointer','motions',2),
                (5,'pointer','pressed',1),(7,'pointer','axis_frames',1),(9,'input','pressed',1),
                (10,'pointer','buttons',5),(11,'input','focus_token','1')]:
            bad = copy.deepcopy(snapshot)
            inputs = bad['check']['cycles'][index]['runtime']['app']['input']
            (inputs if section=='input' else inputs['pointer'])[field] = value
            with self.subTest(index=index,field=field),self.assertRaises(ValueError):
                pointer_result(bad,'a'*32,{},4242,4343,package)

    def test_actual_sdk_hits_cancellation_and_scope_are_required(self):
        snapshot,package = self.fixture()
        for field in ['physical_input_verified','os_menu_verified','synthetic_visibility']:
            bad = copy.deepcopy(snapshot); bad['check'][field] = True
            with self.subTest(field=field),self.assertRaises(ValueError):
                pointer_result(bad,'a'*32,{},4242,4343,package)
        for field,value in [('uv',[.31,.23]),('hit_point',[0,0,0]),('entity','90')]:
            bad = copy.deepcopy(snapshot); bad['check']['points'][0][field] = value
            with self.subTest(field=field),self.assertRaises(ValueError):
                pointer_result(bad,'a'*32,{},4242,4343,package)
        bad = copy.deepcopy(snapshot); bad['check']['cancelled']['transport']['owned_connections'] = 1
        with self.assertRaises(ValueError):
            pointer_result(bad,'a'*32,{},4242,4343,package)
        snapshot['check']['restored']['transport']['pending_cleanup'] = [{}]
        with self.assertRaises(ValueError):
            pointer_result(snapshot,'a'*32,{},4242,4343,package)
