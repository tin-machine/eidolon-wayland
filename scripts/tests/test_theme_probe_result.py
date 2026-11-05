import copy
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]))
from theme_probe_checks import theme_surface_result


def fixture():
    state=dict(state="Ready",activity_visible=True,xr_state="VISIBLE",scene="1")
    rows=[]
    for mode in ("mesh","layer"):
        rows.append(dict(mode=mode,front="10",back="11",alpha_mode="PREMULTIPLIED",scene_texture=True,
                         default_center_hit="10",default_margin_hit="10",margin_blocks_back=True,
                         no_collision_center_hit="11",no_collision_margin_hit="11",removed_from_raycast=True,
                         producer=dict(result="pass",owned_egl_released=True,thread=2,renderer="fixture",surfaces=[
                             dict(index=0,front=False,pixels=[[0,0,255,255]]*6),
                             dict(index=1,front=True,pixels=[[0,0,0,0],[128,0,0,128]]*3)])))
    return dict(schema=1,build_info={},runtime=dict(runtime=state),check=dict(case="theme-surface",nonce="n",result="pass",
        sdk_alpha_verified=False,hmd_verified=False,physical_input_verified=False,synthetic_visibility=False,synthetic_raycast=True,
        ui_thread=1,probe_entities_remaining=0,ready=state,after=state,cycles=rows))


class ThemeProbeTests(unittest.TestCase):
    def test_subprobe_distinguishes_producer_alpha_from_sdk_and_accepts_observed_default_policy(self):
        s=fixture();self.assertTrue(theme_surface_result(s,"n",{}))
        row=s['check']['cycles'][0];row['default_margin_hit']='11';row['margin_blocks_back']=False
        self.assertTrue(theme_surface_result(s,"n",{}))
        self.assertFalse(theme_surface_result(s,"wrong",{}))

    def test_overclaims_alpha_corruption_hidden_scene_and_unreleased_egl_are_rejected(self):
        variants=[]
        for key in ('sdk_alpha_verified','hmd_verified','physical_input_verified','synthetic_visibility'):
            s=fixture();s['check'][key]=True;variants.append(s)
        s=fixture();s['check']['cycles'][0]['producer']['surfaces'][1]['pixels']=[[0,0,0,255]]*6;variants.append(s)
        s=fixture();s['check']['cycles'][0]['producer']['owned_egl_released']=False;variants.append(s)
        s=fixture();s['check']['cycles'][0]['producer']['thread']=1;variants.append(s)
        s=copy.deepcopy(fixture());s['check']['after']['xr_state']='IDLE';variants.append(s)
        s=fixture();s['check']['cycles'][0]['no_collision_margin_hit']='10';variants.append(s)
        s=fixture();s['check']['cycles'][0]['removed_from_raycast']=False;variants.append(s)
        for s in variants:
            with self.assertRaises(ValueError):theme_surface_result(s,'n',{})
