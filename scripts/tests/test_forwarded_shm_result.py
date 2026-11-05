"""転送の画素・実foot・ID・receiptを欠いた成功を受け入れない。"""
import copy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
from quest_checks import forwarded_shm_result


class ForwardedTests(unittest.TestCase):
    def fixture(self):
        nonce, build, uid, termux = 'a'*32, dict(profile='debug'), 4242, 4343
        package = dict(dependencies=dict(waypipe=dict(sha256='b'*64)), remote_fixture=dict(sha256='c'*64))
        local = dict(uid=uid,child_uid=uid,workers=1,owned_groups=1,ready=True,error=None,version='0.11.3',executable_sha256='b'*64,child_context='fixture')
        rows=[]
        for i, mode in enumerate(('pattern','foot')):
            token=hashlib.sha256(f'{nonce}:{i}'.encode()).hexdigest()[:16]
            tools=dict(schema=1,nonce=token,uid=termux,mode=mode,waypipe=dict(version='0.11.3',features=['lz4','zstd'],unavailable=['dmabuf','video','vaapi']))
            if i==0:tools['fixture']=dict(schema=1,fixture_revision='eidolon-shm-client-v1',frames=100,sha256='c'*64)
            else:tools['foot']=dict(version='1.28.0')
            app=dict(session='11',nonce=token,mode=mode,error=None,callback_clock='virtual',ready=True,forward_sent=True,local=copy.deepcopy(local),
                window_ids=['1'] if i==0 else ['1','2'],state='pass' if i==0 else 'running',verified_hashes=100 if i==0 else 0,
                copies=100 if i==0 else 2,releases=100,callbacks=100,configures=1,acks=1,non_uniform=True,
                pool_rejections=0,last_rejected_pool_bytes=None,
                windows=[] if i==0 else [dict(window='2',life='Mapped',frame='3')],
                **{key:0 for key in ('clients','objects','surfaces','pixel_bytes','pool_bytes','pool_count')})
            remote=dict(phase='finished' if i==0 else 'running',cleanup_pending=False,child=dict(pid=100,group=100,start='123'))
            if i==0:remote['finished']=dict(reason='Completed',exit_status=0,cleaned=True,group_alive=False,pipes_closed=True,
                pattern=dict(schema=1,uid=termux,result='pass',fixture_revision='eidolon-shm-client-v1',protocol=dict(protocol_error=False,frames=100,releases=100,callbacks=100,configures=1,acks=1)))
            forward=dict(nonce=token,mode=mode,uid=termux,error=None,registered=True,tools=tools,state='pass' if i==0 else 'running',
                remote_cleaned=True,forward_cancelled=True,pending_cleanup=[],forwarding=dict(active=0),remote=remote)
            transport=dict(session='11',state='Ready',owned_connections=1,credential_requests=1,pending_cleanup=[],app_forward=forward)
            rows.append(dict(cycle=i+1,mode=mode,app=app,transport=transport))
        app=copy.deepcopy(rows[1]['app']);app.update(state='stopped',windows=[],directory_cleaned=True,memfd_fds_after=0,memfd_maps_after=0)
        app['local'].update(workers=0,owned_groups=0,waited=True)
        transport=copy.deepcopy(rows[1]['transport']);transport.update(state='Stopped',workers=0,tokio_threads=0,owned_connections=0,forward=dict(active=0))
        transport['app_forward'].update(state='stopped',cancelled=True)
        transport['app_forward']['remote'].update(phase='finished',finished=dict(reason='Stop',cleaned=True,group_alive=False,pipes_closed=True,term_sent=True))
        result=dict(case='forwarded-shm',nonce=nonce,result='pass',session='11',credential_requests=1,stale_rejected=True,cycles=rows,
            last_handle='1',accept_ms=1,stop_ms=50,after_stop=dict(state='Stopped',app=app,transport=transport,helper_workers=0,dependency_workers=0,compositor_workers=0,render=dict(workers=0)))
        snapshot=dict(schema=1,build_info=build,check=result,runtime=dict(code=0,handle='2',registry=dict(runtimes=1,workers=1),runtime=dict(state='Ready',activity_visible=True,xr_state='FOCUSED',transport=dict(state='Idle'))))
        return snapshot,nonce,build,uid,termux,package

    def test_missing_hash_wrong_uid_reused_ids_or_unverified_receipt_never_pass(self):
        args=self.fixture();self.assertTrue(forwarded_shm_result(*args))
        for path,value in [
            (['cycles',0,'app','state'],'running'),
            (['cycles',0,'app','verified_hashes'],99),(['cycles',0,'app','local','child_uid'],4343),
            (['cycles',0,'transport','app_forward','tools','fixture','sha256'],'d'*64),
            (['cycles',1,'app','windows',0,'window'],'1'),(['cycles',1,'app','non_uniform'],False),
            (['cycles',1,'app','pool_rejections'],1),(['cycles',1,'app','last_rejected_pool_bytes'],536870912),
            (['after_stop','app','pool_bytes'],4096),(['after_stop','transport','app_forward','remote','finished','group_alive'],True),
        ]:
            bad=copy.deepcopy(args[0]);current=bad['check']
            for key in path[:-1]:current=current[key]
            current[path[-1]]=value
            with self.subTest(path=path),self.assertRaises(ValueError):forwarded_shm_result(bad,*args[1:])

    def test_native_result_does_not_prove_ui_return(self):
        args=self.fixture();args[0]['runtime']['runtime']['xr_state']='IDLE'
        self.assertFalse(forwarded_shm_result(*args))
