"""上限・serial・popup focus・本文非出力・回収を欠く成功reportを拒否する。"""
import copy
import hashlib
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]))
from quest_checks import popup_clipboard_result

class PopupClipboardTests(unittest.TestCase):
    def fixture(self):
        nonce='a'*32;info={'profile':'debug'};uid=12345
        flags=[['denied_serial','denied_parent','popup_keyboard','nested_keyboard','outside_dismissed','other_window_focused','parent_destroyed'],
               ['copied','at_limit','rejected_serial','rejected_focus','stale_offer','invalid_utf8','oversized','stalled','stalled_receiver','cancelled','owner_lost','mime_filtered']]
        rows=[]
        for i,scenario in enumerate(['popup','clipboard']):
            r=dict(state='pass',fixture_revision=f'eidolon-{scenario}-wire-fixture-v1',uid=uid,synthetic_input=True,physical_input_verified=False,error=None,client_error=None,
                nonce=hashlib.sha256(f'{nonce}:{i}'.encode()).hexdigest()[:16],client={k:True for k in flags[i]})
            r.update({k:0 for k in ['clients_after','objects_after','surfaces_after','windows_after','pixel_bytes_after','pool_bytes_after','fixture_fds_after','fixture_maps_after']})
            if i==0:r.update(clipped=True,nested_clipped=True,outside_consumed=True);r['client'].update(popup_done=5,popup_keys=2)
            else:r.update(elapsed_ms=10400,input=dict(clipboard=dict(selection=False,active=0,completed=2,oversized=1,invalid_utf8=1,timed_out=2,cancelled=1)))
            rows.append(dict(cycle=i+1,scenario=scenario,report=r))
        check=dict(case='popup-clipboard',nonce=nonce,result='pass',cycles=rows,last_handle='1',accept_ms=0,stop_ms=20,
            after_stop=dict(state='Stopped',compositor_workers=0,dependency_workers=0,helper_workers=0,render=dict(workers=0),transport=dict(workers=0)))
        snapshot=dict(schema=1,build_info=info,check=check,runtime=dict(code=0,registry=dict(runtimes=1,workers=1),handle='2',
            runtime=dict(state='Ready',activity_visible=True,xr_state='FOCUSED',transport=dict(state='Idle'))))
        return snapshot,nonce,info,uid

    def test_rejects_incomplete_scope_clip_timeout_and_resource_evidence(self):
        args=self.fixture();self.assertTrue(popup_clipboard_result(*args))
        for row,path,value in [(0,['clipped'],False),(0,['client','denied_serial'],False),(0,['client','popup_done'],3),
                (0,['physical_input_verified'],True),(0,['objects_after'],1),(1,['input','clipboard','timed_out'],0),
                (1,['input','clipboard','completed'],1),(1,['input','clipboard','active'],1),(1,['elapsed_ms'],1),
                (1,['client','payload'],'unexpected body'),(1,['client','owner_lost'],False),(1,['uid'],999)]:
            broken=copy.deepcopy(args);obj=broken[0]['check']['cycles'][row]['report']
            for key in path[:-1]:obj=obj[key]
            obj[path[-1]]=value
            with self.subTest(row=row,path=path):
                with self.assertRaises(ValueError):popup_clipboard_result(*broken)
        broken=copy.deepcopy(args);broken[0]['runtime']['runtime']['xr_state']='IDLE'
        self.assertFalse(popup_clipboard_result(*broken))

if __name__=='__main__':unittest.main()
