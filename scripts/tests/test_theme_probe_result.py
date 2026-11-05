import copy
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]))
from theme_probe_checks import theme_surface_result, theme_runtime_result, theme_performance, theme_sdk_performance


def fixture():
    state=dict(state="Ready",activity_visible=True,xr_state="VISIBLE",scene="1")
    rows=[]
    for mode in ("mesh","layer"):
        rows.append(dict(mode=mode,front="10",back="11",alpha_mode="PREMULTIPLIED" if mode=='mesh' else "TRANSLUCENT_HOLE_PUNCH",scene_texture=True,
                         layer_enabled=mode=='layer',layer_blend_type='ALPHA_BLEND',layer_blend=None if mode=='mesh' else ['ONE','ONE_MINUS_SOURCE_ALPHA']*2,
                         default_center_hit="10",default_margin_hit="10",margin_blocks_back=True,
                         no_collision_center_hit="11",no_collision_margin_hit="11",removed_from_raycast=True,
                         content_input="12",content_margin_hit="11",content_uv=[.2,.35],content_hit_point=[0.84,1.535,1.4],content_collision="LineTest_IgnoreVisible",
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
        for key,value in [('alpha_mode','PREMULTIPLIED'),('layer_enabled',False),('layer_blend_type','OPAQUE'),
                          ('layer_blend',['SOURCE_ALPHA','ONE_MINUS_SOURCE_ALPHA']*2)]:
            s=fixture();s['check']['cycles'][1][key]=value;variants.append(s)
        for key,value in [('content_input','10'),('content_margin_hit','12'),('content_collision','LineTest'),('content_uv',[.2,float('nan')]),('content_uv',[.8,.65]),
                          ('content_hit_point',[0.84,1.535,1.6]),('content_hit_point',[float('nan'),1.535,1.4])]:
            s=fixture();s['check']['cycles'][0][key]=value;variants.append(s)
        for s in variants:
            with self.assertRaises(ValueError):theme_surface_result(s,'n',{})


class ThemePerformanceTests(unittest.TestCase):
    def fixture(self):
        rows=[]
        for mode in ('off','reduced','animated','animated_metrics'):
            targets=[]
            for window in ('1','2'):
                targets.append(dict(key=dict(window=window,generation='1'),frames=10,decoration_frames=0 if mode=='off' else 10,
                    content_frame='5',frame_timing=dict(samples=10,total_us=20000,histogram_ms=[0,10]+[0]*30)))
            before=dict(state='Ready',render=dict(targets=targets))
            after=copy.deepcopy(before)
            after['theme']=dict(selection='off' if mode=='off' else 'builtin',reduced_motion=mode=='reduced',loading=False,error=None)
            after['render'].update(theme_id=None if mode=='off' else 'cyan-orbit',composition=dict(decoration_texture_bytes=0 if mode=='off' else 100))
            after['workspace']=dict(workload_observation=mode=='animated_metrics')
            after['transport']=dict(app_forward=dict(remote=dict(workload=dict(capability='unavailable',scan_seconds=0))))
            after['app']=dict(workloads=[dict(window=w,terminal_id=w,state='registered',display=dict(coverage='complete',stale=mode!='animated_metrics',scope='shell_tree',rss_bytes=1000)) for w in ('1','2')])
            for t in after['render']['targets']:
                t['frames']+=20;t['decoration_frames']+=0 if mode=='off' else 20
                t['frame_timing']=dict(samples=30,total_us=60000,histogram_ms=[0,30]+[0]*30)
            rows.append(dict(mode=mode,elapsed_ms=6000,process_cpu_ms=300,before=before,after=after))
        return rows

    def sdk_fixture(self):
        rows=self.fixture()
        for row in rows:
            row.update(rss_before=1000000,rss_after=1000000)
            for state in (row['before'],row['after']):
                state.update(state='Ready',activity_visible=True,xr_state='FOCUSED',scene='1')
                state.setdefault('app',{})['session']='1'
                state['render'].update(contexts=1)
                state['render'].setdefault('composition',{}).update(uploads=2,decoration_uploads=4)
        return rows

    def test_sdk_measurement_rejects_visibility_loss_content_updates_and_reuploads(self):
        rows=self.sdk_fixture();self.assertEqual(len(theme_sdk_performance(rows)),4)
        for path,value in [(('after','xr_state'),'IDLE'),(('after','scene'),'2'),(('rss_after',),0),
                (('after','render','composition','decoration_uploads'),5),
                (('after','render','targets',0,'content_frame'),'6')]:
            r=copy.deepcopy(rows);target=r[2]
            for key in path[:-1]:target=target[key]
            target[path[-1]]=value
            with self.subTest(path=path),self.assertRaises(ValueError):theme_sdk_performance(r)

    def test_interval_deltas_and_incomplete_or_stale_proofs(self):
        rows=self.fixture()
        reports=theme_performance(rows)
        self.assertEqual(reports[2]['targets'][0]['draw_p95_upper_ms'],2)
        self.assertAlmostEqual(reports[0]['process_cpu_cores'],.05)
        variants=[]
        r=copy.deepcopy(rows);r[0]['after']['render']['targets'][0]['frame_timing']['histogram_ms'][1]+=1;variants.append(r)
        r=copy.deepcopy(rows);r[0]['after']['render']['targets'][0]['key']['generation']='2';variants.append(r)
        r=copy.deepcopy(rows);r[3]['after']['app']['workloads'][0]['display']['stale']=True;variants.append(r)
        r=copy.deepcopy(rows);r[2]['process_cpu_ms']=-1;variants.append(r)
        r=copy.deepcopy(rows);r[0]['after']['render']['composition']['decoration_texture_bytes']=100;variants.append(r)
        for r in variants:
            with self.assertRaises(ValueError):theme_performance(r)
