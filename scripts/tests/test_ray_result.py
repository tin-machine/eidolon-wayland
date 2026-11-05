"""SDK rayの実交差/UV/changed/state/threadを欠いた自己申告を拒否する。"""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]))
from quest_checks import ray_result


class RayTests(unittest.TestCase):
    def fixture(self):
        key=dict(session='1',window='1',scene='1',generation='1')
        ready=dict(state='Ready',activity_visible=True,xr_state='FOCUSED',scene='1',
            render=dict(targets=[dict(key=key,role='diagnostic',frames=3)]))
        points=[('top_left',.01,.01),('top_right',.99,.01),('bottom_left',.01,.99),('bottom_right',.99,.99),('center',.5,.5)]
        rows,events=[],[]
        for label,u,v in points:
            uv=[u,1-v]
            world=[(u-.5)*1.2,1.4+(.5-v)*.9,1.6]
            rows.append(dict(label=label,uv=uv,world=world,hit_point=list(world),ray_start=[.3,1.7,.2],
                ray_end=[.3+(world[0]-.3)*2.3/1.4,1.7+(world[1]-1.7)*2.3/1.4,2.5],entity='4503599627370514'))
            for down in [True,False]:
                events.append(dict(event='input',target=copy.deepcopy(key),source='4503599627370515',uv=uv,
                    changed=['trigger_right'],state=['trigger_right'] if down else [],callback_thread=100,delivery_thread=100))
        events.append(dict(event='stop',target=copy.deepcopy(key),source='4503599627370515',callback_thread=100,delivery_thread=100))
        return dict(schema=1,build_info=dict(profile='debug'),check=dict(case='ray',nonce='a'*32,result='pass',
            target=key,source='4503599627370515',uv_origin='bottom_left',ui_thread=100,
            synthetic_input=True,synthetic_visibility=False,physical_input_verified=False,
            dispatch='Scene.onInput',raycast='Scene.lineSegmentIntersect',capture_detached=True,source_destroyed=True,
            cycles=rows,inputs=events,ready=ready,after=dict(code=0,runtime=ready)))

    def test_origin_generations_finite_coordinates_buttons_threads_and_scope_are_required(self):
        fixture=self.fixture()
        def judge(value):return ray_result(value,'a'*32,dict(profile='debug'))
        self.assertTrue(judge(fixture))
        for path,value in [
            (['uv_origin'],'top_left'),(['physical_input_verified'],True),(['synthetic_visibility'],True),
            (['cycles',0,'uv'],[.01,.01]),(['cycles',0,'world',0],0),(['cycles',4,'uv'],[float('nan'),.5]),
            (['cycles',0,'hit_point',0],0),(['cycles',0,'ray_end',0],0),
            (['inputs',0,'state'],[]),(['inputs',1,'state'],['trigger_right']),
            (['inputs',0,'target','generation'],'2'),(['inputs',0,'delivery_thread'],101),
            (['inputs',10,'event'],'input'),(['source_destroyed'],False),(['after','runtime','xr_state'],'IDLE'),
        ]:
            bad=copy.deepcopy(fixture);current=bad['check']
            for key in path[:-1]:current=current[key]
            current[path[-1]]=value
            with self.subTest(path=path),self.assertRaises(ValueError):judge(bad)
