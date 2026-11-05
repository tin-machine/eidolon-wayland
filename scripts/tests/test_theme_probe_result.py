import copy
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]))
from theme_probe_checks import theme_surface_result, theme_runtime_result


def fixture():
    state=dict(state="Ready",activity_visible=True,xr_state="VISIBLE",scene="1")
    rows=[]
    for mode in ("mesh","layer"):
        rows.append(dict(mode=mode,front="10",back="11",alpha_mode="PREMULTIPLIED",scene_texture=True,
                         default_center_hit="10",default_margin_hit="10",margin_blocks_back=True,
                         no_collision_center_hit="11",no_collision_margin_hit="11",removed_from_raycast=True,
                         content_input="12",content_margin_hit="11",content_uv=[.2,.35],
                         producer=dict(result="pass",owned_egl_released=True,thread=2,renderer="fixture",surfaces=[
                             dict(index=0,front=False,pixels=[[0,0,255,255]]*6),
                             dict(index=1,front=True,pixels=[[0,0,0,0],[128,0,0,128]]*3)])))
    return dict(schema=1,build_info={},runtime=dict(runtime=state),check=dict(case="theme-surface",nonce="n",result="pass",
        sdk_alpha_verified=False,hmd_verified=False,physical_input_verified=False,synthetic_visibility=False,synthetic_raycast=True,
        ui_thread=1,probe_entities_remaining=0,ready=state,after=state,cycles=rows))


class ThemeProbeTests(unittest.TestCase):
    def test_runtime_requires_recovery_proof_and_rejects_gpu_storage_or_scope_overclaims(self):
        rows=[]
        for name,selection,theme_id in [
            ('builtin','builtin','cyan-orbit'),('imported','imported','probe-theme'),
            ('invalid_preserved','imported','probe-theme'),('reduced','imported','probe-theme'),
            ('off','off',None),('restored_off','off',None),('restored_import','imported','probe-theme')]:
            rows.append(dict(name=name,runtime=dict(state='Ready',
                theme=dict(selection=selection,loading=False,workers=2,error='invalid' if name=='invalid_preserved' else None,
                    applied_revision=2,imported_available=True,reduced_motion=name in ('reduced','off','restored_off'),
                    storage=dict(pending=False,persistent=True,loaded=True,error=None)),
                render=dict(theme_id=theme_id,contexts=1,composition=dict(decoration_texture_bytes=100 if theme_id else 0)))))
        snapshot=dict(schema=1,build_info={},check=dict(case='theme-runtime',nonce='n',result='pass',
            sdk_verified=False,hmd_verified=False,physical_input_verified=False,private_fixture_removed=True,
            cycles=rows,stopped=dict(state='Stopped',theme=dict(workers=0),render=dict(workers=0,contexts=0))))
        self.assertFalse(theme_runtime_result(snapshot,'n',{},123)) # 再生成・回収proofがまだ無い。
        variants=[]
        s=copy.deepcopy(snapshot);s['check']['sdk_verified']=True;variants.append(s)
        s=copy.deepcopy(snapshot);s['check']['cycles'][0]['runtime']['render']['composition']['decoration_texture_bytes']=0;variants.append(s)
        s=copy.deepcopy(snapshot);s['check']['cycles'][2]['runtime']['theme']['applied_revision']=3;variants.append(s)
        s=copy.deepcopy(snapshot);s['check']['cycles'][5]['runtime']['theme']['storage']['loaded']=False;variants.append(s)
        s=copy.deepcopy(snapshot);s['check']['stopped']['theme']['workers']=1;variants.append(s)
        for s in variants:
            with self.assertRaises(ValueError):theme_runtime_result(s,'n',{},123)

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
        for key,value in [('content_input','10'),('content_margin_hit','12'),('content_uv',[.2,float('nan')]),('content_uv',[.8,.65])]:
            s=fixture();s['check']['cycles'][0][key]=value;variants.append(s)
        for s in variants:
            with self.assertRaises(ValueError):theme_surface_result(s,'n',{})
