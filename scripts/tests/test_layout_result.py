"""配置検査で保存値・scope・再生成と全owner回収を欠いた証明を拒否する。"""
import copy
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]))
import test_foot_result as foot_test
from quest_checks import compositor_layer_stats, layout_native_result, layout_sdk_result

class LayoutTests(unittest.TestCase):
    def fixture(self):
        source=foot_test.FootTests().fixture();p=source['check'];base=copy.deepcopy(p['cycles'][0]['runtime']);after=copy.deepcopy(p['after_stop'])
        pose=dict(position_m=[.2,1.65,1.9],rotation_xyzw=[0,math.sin(math.pi/24),0,math.cos(math.pi/24)])
        p.update(case='layout',first_session='1',second_session='2',synthetic_input=True,synthetic_pose_ack=True,synthetic_scene=True,
            physical_input_verified=False,sdk_pose_verified=False,product_settings_untouched=True,saved_pose=pose,
            settings=dict(schema=1,background='virtual',keyboard_layout='jis',placements=[dict(app_id='terminal',slot=0,pose=pose,physical_size_m=dict(width_m=1.44,height_m=1.08),logical_size=dict(width=800,height=500))]))
        rows=[]
        for i,label in enumerate(['before','pose','size','background_virtual','background_passthrough','background_virtual','keyboard','duplicate','restored','new_scene']):
            s=copy.deepcopy(base);session='2' if i>=8 else '1';scene='2' if i==9 else '1';generation='3' if i==9 else '1';s.update(activity_visible=True,xr_state='FOCUSED')
            s['app']['session']=session;s['transport']['session']=session;s['app']['windows']=[];s['app']['input'].update(delivered=6,pressed=0)
            if i>=6:s['app']['input'].update(layout='jis')
            s['render']['targets']=[];s['panels']=[];placements=[];pixels={}
            for w in (['1','2'] if i==7 else ['1']):
                key=dict(session=session,window=w,scene=scene,generation=generation)
                s['app']['windows'].append(dict(window=w,life='Mapped',frame='12',logical_size=dict(width=800,height=500),resize=dict(phase='applied'),sizing=dict(physical_size_m=dict(width_m=1.44,height_m=1.08))))
                s['render']['targets'].append(dict(key=key,frames=3,content_frame='12'))
                s['panels'].append(dict(target=key,action='create',applied=True,error=None))
                placement=dict(pose=pose,pose_command='8',applied_pose=dict(target=key,pose=pose,pose_command='8') if i==1 else None)
                placements.append(dict(window=w,session=session,ambiguous=i==7,placement=placement))
                pixels[w]=dict(distinct_images=3,non_uniform_images=1)
            mode='passthrough' if i in (0,1,2,4) else 'virtual'
            s['workspace']=dict(windows=placements,storage=dict(persistent=True,error=None,loaded=i>=8),background=mode,keyboard_layout='jis' if i>=6 else 'us',background_command='7',applied_background=dict(scene='1',background_command='7',background=mode) if 3<=i<=5 else None)
            rows.append(dict(stage=label,runtime=s,pixels=pixels))
        p['cycles']=rows;p['corruption']=[dict(stage=key,workspace=dict(background='passthrough',keyboard_layout='us',windows=[],storage=dict(error='LAYOUT_SCHEMA' if key=='unknown_schema' else 'LAYOUT_INVALID'))) for key in ('unknown_schema','invalid_pose','truncated')]
        p['stops']=[dict(stage=key,runtime=copy.deepcopy(after),accept_ms=0,stop_ms=150) for key in ('first','restored','unknown_schema','invalid_pose','truncated')]
        return source
    def test_missing_persistence_stale_pose_synthetic_scope_and_leaks_are_rejected(self):
        f=self.fixture();package=dict(dependencies=dict(waypipe=dict(sha256='0'*64),keymaps=dict(jis=dict(sha256='1'*64))))
        def judge(value):return layout_native_result(value,'a'*32,dict(profile='debug'),4242,4343,package)
        with patch('quest_checks.instrumentation_result',return_value=True):
            self.assertTrue(judge(f))
            for path,value in [(['sdk_pose_verified'],True),(['settings','placements',0,'window'],'1'),(['settings','placements',0,'logical_size'],dict(width=1024,height=768)),
                    (['cycles',1,'runtime','workspace','windows',0,'placement','applied_pose','pose_command'],'9'),
                    (['cycles',8,'runtime','workspace','storage','loaded'],False),(['cycles',9,'runtime','workspace','applied_background'],dict(scene='1')),
                    (['cycles',6,'runtime','app','input','pressed'],1),(['stops',0,'runtime','transport','workers'],1)]:
                bad=copy.deepcopy(f);target=bad['check']
                for key in path[:-1]:target=target[key]
                target[path[-1]]=value
                with self.subTest(path=path),self.assertRaises(ValueError):judge(bad)

    def sdk_fixture(self):
        f=self.fixture();p=f['check'];pose=dict(position_m=[.2,1.5,1.75],rotation_xyzw=[0,math.sin(math.pi/24),0,math.cos(math.pi/24)])
        p.update(consumer='SDK',uid=4242,pid=44,synthetic_visibility=False,synthetic_pose_ack=False,synthetic_scene=False,sdk_pose_verified=True,
            physical_input_verified=False,hmd_verified=False,text_verified=False,background_optical_verified=False,synthetic_stale_callback=True,activity_recreated=True,source_destroyed=True,product_settings_untouched=True,
            original_handle='1',fixture_handle='2',last_handle='3',new_handle='4',saved_pose=pose,layer_window=dict(start_wall_ms=10000,end_wall_ms=12200,scene='1'))
        rows=[]
        for i,(original,label) in enumerate(zip([0,6,7,7,8],['before','changed','two','recreated','restored'])):
            row=copy.deepcopy(p['cycles'][original]);s=row['runtime'];row['stage']=label
            s['scene']='2' if i>=3 else '1';s['app']['mode']='foot'
            s['app']['input']['delivered']=8 if i>=2 else 4
            for target in s['render']['targets']:
                target['key']['scene']=s['scene'];target['key']['generation']='3' if i>=3 else '1'
            for j,placement in enumerate(s['workspace']['windows']):
                key=s['render']['targets'][j]['key'];placement['placement']['pose']=pose;placement['placement']['applied_pose']=dict(target=copy.deepcopy(key),pose=pose,pose_command=placement['placement']['pose_command'])
                s['panels'][j]['target']=copy.deepcopy(key)
                size=s['app']['windows'][j]['sizing'];size['applied_physical_size_m']=copy.deepcopy(size['physical_size_m'])
            w=s['workspace'];w['applied_background']=dict(scene=s['scene'],background=w['background'],background_command=w['background_command'])
            row['adapter']=dict(bindings=len(s['app']['windows']),listeners=len(s['app']['windows']))
            rows.append(row)
        p['cycles']=rows;p['product_restored']=dict(state='Ready',activity_visible=True,xr_state='FOCUSED',transport=dict(state='Idle',pending_cleanup=[]),render=dict(mode='diagnostic',targets=[dict(role='diagnostic',frames=3)]))
        package=dict(dependencies=dict(waypipe=dict(sha256='0'*64),keymaps=dict(jis=dict(sha256='1'*64))))
        return f,package

    def test_sdk_scope_requires_actual_pose_ack_two_panels_recreated_scene_and_product_restore(self):
        f,package=self.sdk_fixture()
        def judge(value):return layout_sdk_result(value,'a'*32,dict(profile='debug'),4242,4343,package)
        self.assertTrue(judge(f))
        for path,value in [(['synthetic_visibility'],True),(['synthetic_pose_ack'],True),(['activity_recreated'],False),
                (['cycles',3,'runtime','scene'],'1'),(['cycles',2,'adapter','bindings'],1),
                (['cycles',4,'runtime','workspace','storage','loaded'],False),(['product_restored','transport','pending_cleanup'],[dict(nonce='pending')])]:
            bad=copy.deepcopy(f);target=bad['check']
            for key in path[:-1]:target=target[key]
            target[path[-1]]=value
            with self.subTest(path=path),self.assertRaises(ValueError):judge(bad)

    def test_layer_measurement_excludes_other_process_time_window_and_suffix_counters(self):
        p=dict(uid=4242,pid=44,layer_window=dict(start_wall_ms=10000,end_wall_ms=12200))
        logs="10.100 44 9 I VrApi : LCnt=5(DR0),ShrpLCnt=1,SSLCnt=1/2\n10.200 45 9 I VrApi : LCnt=16\n10.300 44 9 I VrApi : ShrpLCnt=8,SSLCnt=7/8\n12.300 44 9 I VrApi : LCnt=12\n"
        r=compositor_layer_stats(p,logs,4242)
        self.assertEqual((r['status'],r['minimum'],r['maximum'],len(r['samples'])),('observed',5,5,1))
        self.assertEqual(compositor_layer_stats(p,logs,4343)['status'],'not_observed')
