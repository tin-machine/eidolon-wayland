"""wire releaseの行き先、axis符号、reset・資源回収を省略した記録を拒否する。"""
import copy
import hashlib
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]))
from quest_checks import pointer_wire_result


class PointerWireTests(unittest.TestCase):
    def fixture(self):
        nonce='a'*32
        buttons=[('child',272,1),('child',272,0),('root_b',273,1),('root_b',273,0),('root_b',272,1),('root_b',274,1),('root_b',272,0),('root_b',274,0)]
        events=[dict(event='enter',surface='child',position=[8,8])]
        events.extend(dict(event='button',surface=s,code=k,state=v,time=124) for s,k,v in buttons)
        events.extend([dict(event='motion',surface='child',position=[-12,8]),dict(event='axis_source',source=2),
                       dict(event='axis',surface='root_b',axis=1,value=2.5),dict(event='axis',surface='root_b',axis=0,value=-15),
                       dict(event='axis_stop',axis=1),dict(event='axis_stop',axis=0),dict(event='axis',surface='root_b',axis=0,value=3),dict(event='axis_stop',axis=0),dict(event='frame')])
        normal=len(events)
        events.extend([dict(event='button',surface='root_b',code=272,state=1,time=124),dict(event='button',surface='root_b',code=272,state=0,time=124)])
        phases=[]
        for index,stage in enumerate(['Mapped','HoverChild','Hole','Pressed','Released','FocusedB','Reset','UnmappedB','DestroyedChild']):
            phases.append(dict(stage=stage,events=[0,1,2,3,4,5,normal,normal+2,normal+2][index],
                               keyboard_enters=[10,20],keyboard_leaves=2,keys=2))
        keyboard=dict(keys=[[42,1,123],[42,0,124]],keymap_sha256='3'*64)
        client=dict(root_a=10,root_b=20,child=30,events=events,checkpoints=phases,keyboard=keyboard,error=None)
        input_report=dict(keyboard=True,layout='us',keymap_sha256='1'*64,wire_keymap_sha256='3'*64,include_paths=0,
                          focus_window=None,focus_token=None,pressed=0,pointer=dict(ready=True,pressed=0,
                          hover_window=None,hover_surface=None,capture_window=None,capture_surface=None))
        report=dict(state='pass',error=None,uid=4242,nonce=hashlib.sha256(f'{nonce}:0'.encode()).hexdigest()[:16],
                    fixture_revision='eidolon-pointer-wire-fixture-v1',checkpoints=9,input_time_ms=123,synthetic_input=True,physical_input_verified=False,
                    input=input_report,client=client)
        report.update({k:True for k in ['letterbox_rejected','region_checked','source_capture_checked','off_panel_release','hover_keeps_keyboard',
                                      'focus_changed','duplicate_reset','late_source_ignored','unmap_released','destroy_released']})
        report.update({k:0 for k in ['clients_after','objects_after','surfaces_after','windows_after','pixel_bytes_after','pool_bytes_after',
                                   'fixture_fds_after','fixture_maps_after']})
        check=dict(case='pointer-wire',nonce=nonce,result='pass',cycles=[dict(cycle=1,scenario='pointer',report=report)],
                   last_handle='1',accept_ms=1,stop_ms=20,after_stop=dict(state='Stopped',compositor_workers=0,dependency_workers=0,
                   helper_workers=0,render=dict(workers=0),transport=dict(workers=0)))
        snapshot=dict(schema=1,build_info={},check=check,runtime=dict(code=0,handle='2',registry=dict(runtimes=1,workers=1),
                      runtime=dict(state='Ready',activity_visible=True,xr_state='FOCUSED',transport=dict(state='Idle'))))
        return snapshot,dict(dependencies=dict(keymaps=dict(us=dict(sha256='1'*64))))

    def test_wrong_surface_offset_scroll_and_frame_are_rejected(self):
        snapshot,package=self.fixture()
        self.assertTrue(pointer_wire_result(snapshot,'a'*32,{},4242,package))
        for event,field,value in [(1,'code',280),(2,'surface','root_b'),(9,'position',[12,8]),(12,'value',15),(14,'axis',1)]:
            bad=copy.deepcopy(snapshot);bad['check']['cycles'][0]['report']['client']['events'][event][field]=value
            with self.subTest(event=event,field=field),self.assertRaises(ValueError):
                pointer_wire_result(bad,'a'*32,{},4242,package)
        bad=copy.deepcopy(snapshot);bad['check']['cycles'][0]['report']['client']['events'][17]=dict(event='motion')
        with self.assertRaises(ValueError):
            pointer_wire_result(bad,'a'*32,{},4242,package)

    def test_reset_identity_and_cleanup_are_required(self):
        snapshot,package=self.fixture()
        for field,value in [('source_capture_checked',False),('destroy_released',False),('fixture_fds_after',1),('nonce','old'),('physical_input_verified',True)]:
            bad=copy.deepcopy(snapshot);bad['check']['cycles'][0]['report'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):
                pointer_wire_result(bad,'a'*32,{},4242,package)
        bad=copy.deepcopy(snapshot);bad['check']['cycles'][0]['report']['input']['pointer']['pressed']=1
        with self.assertRaises(ValueError):
            pointer_wire_result(bad,'a'*32,{},4242,package)
        snapshot['runtime']['handle']='1'
        self.assertFalse(pointer_wire_result(snapshot,'a'*32,{},4242,package))
