"""実SDK二枚の片側loss・入力継続・第三window・表示切替・個別closeを別に検査する。"""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]))
import test_layout_result as layout_test
from quest_checks import two_windows_sdk_result

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
            s['app']['input'].update(pressed=1 if i==5 else 0,focus_window='2' if i in (0,2,3,4,5) else ('3' if i==7 else None),delivered=[8,8,12,12,12,13,14,18,18,18][i])
            rows.append(dict(stage=label,runtime=s,adapter=dict(bindings=len(visible),listeners=len(visible))))
        p['two_window_cycles']=rows
        return f,package

    def test_rejects_incomplete_sdk_loss_other_window_input_switch_and_close(self):
        f,package=self.fixture()
        def judge(value):return two_windows_sdk_result(value,'a'*32,dict(profile='debug'),4242,4343,package)
        self.assertTrue(judge(f))
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

if __name__=='__main__':unittest.main()
