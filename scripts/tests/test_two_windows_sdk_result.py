"""実SDK二枚の片側loss・入力継続・第三window・表示切替・個別closeを別に検査する。"""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]))
import test_layout_result as layout_test
from quest_checks import two_windows_sdk_result
from theme_probe_checks import theme_sdk_result

class TwoWindowsSdkTests(unittest.TestCase):
    def fixture(self):
        f,package=layout_test.LayoutTests().sdk_fixture();p=f['check'];p.update(case='two-windows',surface_loss_trigger='sdk-entity-recreate',source_hold_released=True,old_surface_target_rejected=True,shift_held_before_switch=True)
        initial=p['cycles'][2]['runtime'];rows=[]
        labels=['pair','first_lost','loss_input','first_restored','third_hidden','shift_held','switched','third_input','third_closed','pair_restored']
        for i,label in enumerate(labels):
            s=copy.deepcopy(initial);mapped=['1','2','3'] if 4<=i<=7 else ['1','2'];visible=['2'] if i in (1,2) else (['1','3'] if i in (6,7) else (['1'] if i==8 else ['1','2']))
            window_template=copy.deepcopy(initial['app']['windows'][0]);placement_template=copy.deepcopy(initial['workspace']['windows'][0]);target_template=copy.deepcopy(initial['render']['targets'][0]);plan_template=copy.deepcopy(initial['panels'][0])
            s['app']['windows']=[];s['workspace']['windows']=[];s['render']['targets']=[];s['panels']=[]
            for w in mapped:
                window=copy.deepcopy(window_template);window['window']=w;window['frame']='1';s['app']['windows'].append(window)
                placement=copy.deepcopy(placement_template);placement['window']=w;s['workspace']['windows'].append(placement)
                if w not in visible:placement['placement']['applied_pose']=None;continue
                generation='2' if (w=='1' and i>=3) or (w=='2' and i==9) else '1'
                target=copy.deepcopy(target_template);target['key'].update(window=w,generation=generation);target.update(frames=100+i*20,content_frame='1');s['render']['targets'].append(target)
                placement['placement']['applied_pose'].update(target=copy.deepcopy(target['key']))
                plan=copy.deepcopy(plan_template);plan['target']=copy.deepcopy(target['key']);s['panels'].append(plan)
            s['app']['input'].update(pressed=1 if i==5 else 0,focus_window='2' if i in (0,2,3,4,5) else ('3' if i==7 else ('1' if i==8 else None)),delivered=[8,8,12,12,12,13,14,18,22,22][i])
            rows.append(dict(stage=label,runtime=s,adapter=dict(bindings=len(visible),listeners=len(visible),background_api=copy.deepcopy(p['cycles'][2]['adapter']['background_api']))))
        p['two_window_cycles']=rows
        return f,package

    def test_rejects_incomplete_sdk_loss_other_window_input_switch_and_close(self):
        f,package=self.fixture()
        def judge(value):return two_windows_sdk_result(value,'a'*32,dict(profile='debug'),4242,4343,package)
        self.assertTrue(judge(f))
        static=copy.deepcopy(f);static['check']['cycles'][0]['runtime']['render']['targets'][0]['frames']=1
        self.assertTrue(judge(static)) # current contentの一回のpresentで静止panelは準備済み。
        static['check']['cycles'][0]['runtime']['render']['targets'][0]['frames']=0
        with self.assertRaises(ValueError):judge(static)
        for path,value in [(['source_hold_released'],False),(['old_surface_target_rejected'],False),
                (['two_window_cycles',4,'runtime','app','windows',2,'life'],'AwaitingInitialCommit'),
                (['two_window_cycles',1,'adapter','bindings'],2),(['two_window_cycles',1,'runtime','render','targets',0,'key','generation'],'2'),
                (['two_window_cycles',2,'runtime','render','targets',0,'frames'],100),(['two_window_cycles',2,'runtime','app','input','delivered'],8),
                (['two_window_cycles',3,'runtime','render','targets',0,'key','generation'],'1'),(['two_window_cycles',5,'runtime','app','input','pressed'],0),
                (['two_window_cycles',5,'runtime','render','targets',1,'content_frame'],'0'),
                (['two_window_cycles',6,'runtime','app','input','pressed'],1),(['two_window_cycles',7,'runtime','app','input','focus_window'],'2'),
                (['two_window_cycles',8,'runtime','render','targets',0,'frames'],100),(['cycles',3,'runtime','render','targets',0,'key','generation'],'2')]:
            broken=copy.deepcopy(f);target=broken['check']
            for key in path[:-1]:target=target[key]
            target[path[-1]]=value
            with self.subTest(path=path),self.assertRaises(ValueError):judge(broken)

    def test_themed_sdk_requires_decorated_targets_through_resize_loss_and_restart(self):
        from test_theme_probe_result import ThemePerformanceTests
        f,package=self.fixture();f['check']['case']='theme-sdk'
        f['check']['performance']=ThemePerformanceTests().sdk_fixture()
        for row in f['check']['cycles']+f['check']['two_window_cycles']:
            s=row['runtime'];s['theme']=dict(selection='builtin',loading=False,error=None,fallback_reason=None)
            s['render'].update(theme_id='cyan-orbit',composition=dict(decoration_texture_bytes=1000))
            row['adapter'].update(decoration_entities=len(s['render']['targets']),targets=[
                dict(target=t['key'],visual_entity=str(100+i),visual_collision='NoCollision',input_collision='LineTest_IgnoreVisible')
                for i,t in enumerate(s['render']['targets'])])
            for t in s['render']['targets']:
                w=next(w for w in s['app']['windows'] if w['window']==t['key']['window'])
                logical=w['logical_size'];size=max(1280,min(2048,max(logical['width'],logical['height']+32)))
                p=next(p for p in s['panels'] if p['target']==t['key'])
                p['decoration']=dict(content=logical,canvas=dict(width=size,height=size),content_origin=[(size-logical['width'])//2,(size-logical['height'])//2])
                t.update(width=size,height=size,content_size=logical,decoration_frames=3,frame_timing=dict(samples=t['frames'],histogram_ms=[0,t['frames']]+[0]*30))
        def judge(value):return theme_sdk_result(value,'a'*32,dict(profile='debug'),4242,4343,package)
        self.assertTrue(judge(f))
        for path,value in [(['cycles',4,'runtime','theme','selection'],'off'),
                (['two_window_cycles',1,'runtime','render','targets',0,'decoration_frames'],0),
                (['cycles',1,'runtime','panels',0,'decoration','content_origin'],[0,0]),
                (['cycles',3,'runtime','render','targets',0,'width'],1024),
                (['cycles',2,'adapter','targets',0,'visual_collision'],'LineTest'),
                (['cycles',2,'adapter','targets',1,'input_collision'],'NoCollision'),
                (['two_window_cycles',1,'adapter','decoration_entities'],2),
                (['two_window_cycles',6,'runtime','app','input','pressed'],1)]:
            broken=copy.deepcopy(f);target=broken['check']
            for key in path[:-1]:target=target[key]
            target[path[-1]]=value
            with self.subTest(path=path),self.assertRaises(ValueError):judge(broken)

if __name__=='__main__':unittest.main()
