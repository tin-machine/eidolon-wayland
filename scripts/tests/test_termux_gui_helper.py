import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import test_termux_gui_worker as fixture

SCRIPTS=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('gui_helper',SCRIPTS/'termux/eidolon-session.py');helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)


class HelperGuiIntegrationTests(unittest.TestCase):
    setUp=fixture.GuiWorkerTests.setUp
    fixed_worker_source=fixture.GuiWorkerTests.fixed_worker_source
    def definition(self):
        return dict(id='freedoom',label='Freedoom',mode='gui',executable=fixture.gui.EXECUTABLE,args=[],cwd='.',icon='builtin:game',execution_profile=self.profile)
    def supervise(self,*,initial_gui=False,stop_game=False,reject_runtime=False,eof=False):
        read,write=os.pipe();events=[];nonce=fixture.NONCE;directory=self.home/'.eidolon'/('s-'+nonce)
        sent_gui=sent_after=False;owner=None;closed=[]
        body='import time;time.sleep(4)' if stop_game or eof else 'import time;time.sleep(.12)'
        source_bytes=self.fixed_worker_source(body)
        def send(kind,**fields):os.write(write,fixture.gui.canonical(dict(type=kind,nonce=nonce,**fields))+b'\n')
        def target():return dict(kind='temporary',definition=self.definition())
        def emit(value):
            nonlocal sent_gui,sent_after,owner,write
            events.append(copy.deepcopy(value));kind=value['type']
            if kind=='prepared':send('inspect_tools',mode='foot')
            elif kind=='tools':
                send('start_application_v1' if initial_gui else 'start_terminal_v1',schema=1,mode='foot',session='17',terminal_id='1',**({'target':target()} if initial_gui else {}))
            elif kind=='child_started':owner=value['child']
            elif kind=='terminal_opened':
                if value['request_id'] is None and not initial_gui and not sent_gui:
                    sent_gui=True
                    if reject_runtime:(self.home.parent/'usr/lib/libSDL2-2.0.so').write_bytes(b'changed runtime')
                    send('open_terminal_v2',schema=1,session='17',terminal_id='2',request_id='10',target=target())
                elif value.get('mode')=='gui' and value['success'] and (stop_game or eof):
                    if eof:os.close(write);write=None
                    else:send('stop')
                elif reject_runtime and value['request_id']=='10':
                    self.assertFalse(value['success']);self.assertTrue(helper.same_process(owner));send('open_terminal_v1',schema=1,session='17',terminal_id='3',request_id='20')
                elif value['request_id']=='20':send('stop')
            elif kind=='gui_exited_v1':
                closed.append(value);self.assertTrue(value['cleanup_complete']);self.assertTrue(helper.same_process(owner))
                self.assertFalse((self.home/'anchor-stopped').exists())
                if not stop_game and not eof and not sent_after:
                    sent_after=True;send('open_terminal_v1',schema=1,session='17',terminal_id='2' if initial_gui else '3',request_id='20')
        def factory(mode,token):
            code=("import os,pathlib,signal,socket,time\n"+f"root=pathlib.Path({str(directory)!r})\n"
                "a=socket.socket(socket.AF_UNIX);a.bind(str(root/'w'));b=socket.socket(socket.AF_UNIX);b.bind(str(root/'f'))\n"
                +f"def stop(*_):pathlib.Path({str(self.home/'anchor-stopped')!r}).write_text('stopped');raise SystemExit(0)\n"
                "signal.signal(signal.SIGTERM,stop)\ntime.sleep(10)\n")
            return [sys.executable,'-I','-S','-B','-c',code]
        def terminal(*_,**__):return [sys.executable,'-I','-S','-B','-c','pass']
        try:
            with patch.object(helper,'TERMUX_HOME',self.home),patch.object(helper,'TERMUX_PREFIX',self.home.parent/'usr'),patch.object(helper,'inspect_tools',return_value=dict(mode='foot')), \
                 patch.object(helper,'terminal_command',side_effect=terminal),patch.object(helper,'gui_policy',return_value=fixture.gui),patch.object(fixture.gui,'SOURCE_BYTES',source_bytes,create=True):
                result=helper.supervise(self.home,nonce,read,emit,lease_seconds=4,grace_seconds=.1,factory=factory)
        finally:
            os.close(read)
            if write is not None:os.close(write)
        self.assertTrue(result['cleaned'] and result['pipes_closed']);self.assertFalse(result['group_alive']);self.assertFalse(directory.exists())
        self.assertEqual(result['reason'],'EOF' if eof else 'Stop');self.assertTrue((self.home/'anchor-stopped').exists())
        self.assertNotIn('body',json.dumps(events));return events,closed,result
    def test_initial_gui_uses_anchor_without_foot_then_opens_foot_after_last_gui(self):
        events,closed,result=self.supervise(initial_gui=True)
        opened=[v for v in events if v['type']=='terminal_opened'];self.assertEqual([r['ordinal'] for r in opened],[1,2]);self.assertEqual(opened[0]['mode'],'gui')
        self.assertEqual(len(closed),1);self.assertEqual(closed[0]['child_returncode'],0);self.assertEqual(result['gui_children']['active'],0)
    def test_foot_and_gui_share_forward_but_keep_distinct_mode_cookie_and_global_ordinal(self):
        events,closed,_=self.supervise()
        opened=[v for v in events if v['type']=='terminal_opened'];self.assertEqual([r['ordinal'] for r in opened],[1,2,3]);self.assertEqual(opened[1]['mode'],'gui')
        self.assertIn('.gui.g-',closed[0]['app_id']);self.assertEqual(closed[0]['terminal_id'],'2')
    def test_stop_reaps_gui_worker_before_signalling_owned_anchor(self):
        _,closed,result=self.supervise(stop_game=True)
        self.assertEqual(closed[0]['child_returncode'],-15);self.assertEqual(closed[0]['reason'],'cancel');self.assertEqual(result['gui_children']['active'],0)
    def test_control_eof_reaps_gui_before_anchor_with_reproducible_receipt(self):
        _,closed,result=self.supervise(eof=True)
        self.assertEqual(closed[0]['child_returncode'],-15);self.assertTrue(result['gui_children']['retired']>=1)
    def test_changed_runtime_refuses_gui_and_existing_terminal_can_still_open_and_stop(self):
        events,closed,result=self.supervise(reject_runtime=True)
        self.assertEqual(closed,[]);opened=[v for v in events if v['type']=='terminal_opened'];self.assertEqual([v['success'] for v in opened],[True,False,True])
        self.assertEqual(opened[1]['error'],'GUI_NOT_VERIFIED');self.assertNotIn('gui_children',result)
    def test_real_worker_and_owner_hash_chain_is_checked_without_executing_game(self):
        policy=helper.gui_policy(self.home);self.assertEqual(policy.SOURCE_BYTES,(self.lib/'eidolon-gui.py').read_bytes())
        (self.lib/'eidolon_gui_owner.py').write_bytes(b'changed owner')
        with self.assertRaises(ValueError):helper.gui_policy(self.home)


if __name__=='__main__':unittest.main()
