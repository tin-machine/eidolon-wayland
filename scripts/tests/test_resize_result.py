"""ackだけの成功、producerとmeterの混同、resize後の入力欠落を拒否する。"""
import copy
import hashlib
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]))
from quest_checks import resize_result,wayland_state_result


class ResizeSdkTests(unittest.TestCase):
    def fixture(self):
        target=dict(session='1',window='1',scene='1',generation='1');rows=[]
        package=dict(dependencies=dict(keymaps=dict(us=dict(sha256='1'*64)),waypipe=dict(sha256='2'*64)))
        for i,stage in enumerate(['before','small','wide','physical','clicked','typed']):
            size=dict(width=1024,height=768) if i==0 else dict(width=800,height=500) if i==1 else dict(width=1280,height=720)
            physical=dict(width_m=1.2,height_m=.9) if i<3 else dict(width_m=1.44,height_m=1.08)
            window=dict(window='1',life='Mapped',logical_size=size,frame=str(i+1),
                        resize=dict(committed_size=size,requested_logical_size=None if i==0 else size,phase='idle' if i==0 else 'applied',error=None,configured_serial=None if i==0 else 10 if i==1 else 11),
                        sizing=dict(physical_size_m=physical,applied_physical_size_m=physical.copy(),producer_size_px=dict(width=1024,height=768)))
            inputs=dict(keyboard=True,layout='us',include_paths=0,keymap_sha256='1'*64,focus_window='1' if i>=4 else None,
                        delivered=4 if i==5 else 0,pressed=0,pointer=dict(buttons=2 if i>=4 else 0,pressed=0))
            app=dict(session='1',mode='foot',callback_clock='target-driven',error=None,input=inputs,windows=[window],copies=i+1,configures=[1,2,4,4,5,5][i],local=dict(uid=4242,executable_sha256='2'*64))
            state=dict(state='Ready',activity_visible=True,xr_state='FOCUSED',scene='1',app=app,
                       transport=dict(state='Ready',session='1',credential_requests=1,app_forward=dict(uid=4343,state='running',mode='foot',error=None)),
                       render=dict(targets=[dict(role='windows',key=target.copy(),frames=3,width=1024,height=768,content_frame=str(i+1))]))
            rows.append(dict(stage=stage,runtime=state))
        result=dict(case='resize',nonce='a'*32,result='pass',consumer='SDK',product_host_path=True,synthetic_input=True,
                    synthetic_visibility=False,physical_input_verified=False,text_verified=False,source_destroyed=True,
                    target=target,cycles=rows,hit=dict(uv=[.53,.47],world=[.03*1.44,1.4+.03*1.08,1.5]),
                    old_handle='1',new_handle='2',stop=dict(old_runtime_destroyed=True,stop_ms=100),
                    restored=dict(state='Ready',activity_visible=True,xr_state='FOCUSED',transport=dict(state='Idle',pending_cleanup=[]),render=dict(targets=[dict(role='diagnostic',frames=3)])))
        return dict(schema=1,build_info={},check=result),package

    def test_old_image_ack_only_and_meter_pixel_confusion_are_rejected(self):
        snapshot,package=self.fixture();self.assertTrue(resize_result(snapshot,'a'*32,{},4242,4343,package))
        for section,field,value in [('resize','phase','awaiting_commit'),('resize','committed_size',dict(width=1024,height=768)),
                ('sizing','producer_size_px',dict(width=1280,height=720)),('sizing','applied_physical_size_m',dict(width_m=1.2,height_m=.9))]:
            bad=copy.deepcopy(snapshot);bad['check']['cycles'][3]['runtime']['app']['windows'][0][section][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):resize_result(bad,'a'*32,{},4242,4343,package)
        for i,field,value in [(3,'configures',5),(5,'copies',5)]:
            bad=copy.deepcopy(snapshot);bad['check']['cycles'][i]['runtime']['app'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):resize_result(bad,'a'*32,{},4242,4343,package)
        bad=copy.deepcopy(snapshot)
        bad['check']['cycles'][5]['runtime']['render']['targets'][0]['content_frame']='4'
        with self.assertRaises(ValueError):resize_result(bad,'a'*32,{},4242,4343,package)
        snapshot['check']['cycles'][1]['runtime']['render']['targets'][0]['content_frame']='1'
        with self.assertRaises(ValueError):resize_result(snapshot,'a'*32,{},4242,4343,package)

    def test_physical_claim_bad_ray_and_stop_failure_are_rejected(self):
        snapshot,package=self.fixture()
        for field,value in [('physical_input_verified',True),('synthetic_visibility',True),('source_destroyed',False),('new_handle','1')]:
            bad=copy.deepcopy(snapshot);bad['check'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):resize_result(bad,'a'*32,{},4242,4343,package)
        bad=copy.deepcopy(snapshot);bad['check']['hit']['world']=[0,0,0]
        with self.assertRaises(ValueError):resize_result(bad,'a'*32,{},4242,4343,package)
        snapshot['check']['cycles'][5]['runtime']['app']['input']['delivered']=3
        with self.assertRaises(ValueError):resize_result(snapshot,'a'*32,{},4242,4343,package)


class ResizeWireTests(unittest.TestCase):
    def fixture(self):
        nonce='a'*32
        proof=dict(logical_resize_flags=1023,checkpoints=11,resize_serials=[2**32-1,0,1],cadence_checks=4,immutable_checks=2,window_ids=['1'],
                   client=dict(protocol_error=False,checkpoints=11,protocol=dict(configures=4)))
        report=dict(state='pass',error=None,fixture_revision='eidolon-wayland-state-v1',callback_clock='virtual',scenario='LogicalResize',
                    uid=4242,nonce=hashlib.sha256(f'{nonce}:0'.encode()).hexdigest()[:16],elapsed_ms=20,proof=proof)
        report.update({k:0 for k in ['surfaces_after','clients_after','objects_after','windows_after','pixel_bytes_after','pool_bytes_after','pool_count_after','fixture_fds_after','fixture_maps_after']})
        result=dict(case='resize-wire',nonce=nonce,result='pass',last_handle='1',accept_ms=1,stop_ms=100,
                    cycles=[dict(cycle=1,scenario='logical_resize',report=report,fds=20,native_heap_bytes=100)],
                    after_stop=dict(state='Stopped',compositor_workers=0,dependency_workers=0,helper_workers=0,render=dict(workers=0),transport=dict(workers=0)))
        return dict(schema=1,build_info={},check=result,runtime=dict(code=0,handle='2',registry=dict(runtimes=1,workers=1),runtime=dict(state='Ready',activity_visible=True,xr_state='FOCUSED',compositor_workers=0,transport=dict(state='Idle'))))

    def test_resize_clock_scope_flags_and_opaque_serials_are_checked(self):
        snapshot=self.fixture();self.assertTrue(wayland_state_result(snapshot,'a'*32,{},4242,case='resize-wire'))
        for field,value in [('logical_resize_flags',511),('checkpoints',10),('resize_serials',[1,1,2]),('immutable_checks',1)]:
            bad=copy.deepcopy(snapshot);bad['check']['cycles'][0]['report']['proof'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):wayland_state_result(bad,'a'*32,{},4242,case='resize-wire')
