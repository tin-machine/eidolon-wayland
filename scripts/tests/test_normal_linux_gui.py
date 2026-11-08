import contextlib
import copy
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SCRIPTS));sys.path.insert(0,str(SCRIPTS/'tests'))
import normal_linux_evidence as evidence
from sdl_lease_capture import CaptureError
from test_gtk_interaction_live import raw_resources
loader=importlib.machinery.SourceFileLoader('normal_linux_cli',str(SCRIPTS/'quest-observe-normal-linux-gui'))
spec=importlib.util.spec_from_loader(loader.name,loader);cli=importlib.util.module_from_spec(spec);loader.exec_module(cli)
DIGEST='d'*64;NONCE='a'*32;REMOTE='b'*16
MAIN=dict(session='2',window='5',scene='3',generation='4');DIALOG=dict(MAIN,window='6')


def snapshot(sequence=1,stage='main'):
    idle=stage=='idle';closed=stage=='closed';dialog=stage=='dialog'
    keys=[] if idle or closed else [MAIN,DIALOG] if dialog else [MAIN]
    frames=2 if dialog else 1
    desktop=None if idle else dict(session='2',nonce=REMOTE,request_id='17',finished=closed,channel_complete=closed,router_drained=closed,
        started=True,start_requested=True,forward_requested=True,forward_cancelled=closed,error=None,cancelled=False,journal_pending=not closed,
        remote=dict(**{name:closed for name in ('process_complete','pipes_eof','remote_cleaned','receipt_written','terminal_event','channel_closed','eof')},
            protocol_valid=True,exit_status=0 if closed else None,exit_signal=False,reason='process_exited' if closed else None))
    app=None if idle else dict(state='stopped' if closed else 'running',mode='linux_desktop',session='2',remote_gui_verified=not closed,
        windows=[dict(window=k['window'],parent=None if k==MAIN else MAIN['window'],life='Mapped',
            app_id='io.github.tinmachine.eidolon.linuxdesktop.r'+REMOTE*2+('' if k==MAIN else '.dialog'),title='PRIVATE TITLE') for k in keys],
        input=dict(delivered=2 if dialog or closed else 0,private_body='NEVER RECORD'),directory_cleaned=closed,
        local=dict(workers=0 if closed else 1,owned_groups=0 if closed else 1,waited=closed),
        **{name:0 if closed else 1 for name in ('clients','objects','surfaces','pool_bytes','pool_count','pixel_bytes')})
    runtime=dict(schema=1,state='Ready',scene='3',activity_visible=True,xr_state='FOCUSED',app=app,app_sdk_bindings=len(keys),
        transport=dict(session=None if idle else '2',profile_id=None if idle else 'remote-linux',state='Idle' if idle else 'Disconnected' if closed else 'DesktopReady',
            owned_connections=0 if idle or closed else 1,desktop_pending=False if idle or closed else True,pending_cleanup=[],desktop=desktop),
        render=dict(targets=[dict(key=k,frames=frames,content_frame=str(frames)) for k in keys]))
    adapter=raw_resources(runtime,closed)
    return dict(schema=1,sequence=str(sequence),process_id=83,build_info=dict(schema=1,source_digest=DIGEST,source_commit='e'*40,source_dirty=False,
        profile='debug',target='aarch64-linux-android',apk_version_code=177),runtime=dict(schema=1,code=0,handle='9',runtime=runtime),adapter=adapter)


def diagnostic_snapshot(sequence=1):
    value=snapshot(sequence,'idle');runtime=value['runtime']['runtime'];adapter=value['adapter']
    target=dict(session='1',window='1',scene='3',generation='7')
    runtime['render'].update(mode='diagnostic',targets=[dict(key=target,role='diagnostic',frames=5,content_frame=None)])
    runtime['shutdown']['sdk_bindings']=1
    adapter.update(created=1,bindings=1,listeners=1,targets=[dict(target=target,runtime='9',role='diagnostic',entity='100',
        visual_entity=None,listener_owned=True,input_owned=True,retiring=False,sdk_disposed=False,disposal_failed=False,
        controls=None,mode_badge=None,move=None)])
    return value


def trace():
    return dict(remote_nonce=REMOTE,main_target=MAIN,dialog_targets=[dict(target=DIALOG,parent='5')],targets=[MAIN,DIALOG],
        input_baseline=0,input_last=2,input_delta=2,pointer_baseline=0,pointer_last=2,pointer_delta=2,first_content_frame=1,last_content_frame=2,
        frame_progress=True,dialog_parent_verified=True,main_closed=True,targets_retired=True,cleanup_complete=True,
        cleanup=dict(local_complete=True,remote_complete=True,connection_closed=True,desktop_pending=False,sdk_bindings=0,render_targets=0,panels=0,board_occupied=0),
        observations=sorted(evidence.OBSERVATIONS))


def check(result='completed'):
    return dict(case='normal-linux-gtk',nonce=NONCE,scope='production_linux_manual_observation',result=result,phase='COMPLETE',reason=None,
        runtime='9',session='2',scene='3',connect_request_id='15',inspect_request_id='16',launch_request_id='17',catalog_verified=True,launched=True,
        stop_requested=False,physical_input_verified=False,visual_input_verified=False,ime_verified=False,trace=trace())


class EvidenceTests(unittest.TestCase):
    def samples(self):return [evidence.sample(snapshot(i+1,s)) for i,s in enumerate(('idle','main','dialog','closed'))]
    def summary(self,samples=None,value=None):return evidence.summarize(samples or self.samples(),value or trace(),'2','3','17')

    def test_observes_main_content_input_parented_dialog_and_independent_cleanup(self):
        result=self.summary();self.assertEqual(result['status'],'observed')
        self.assertEqual(result['physical_input'],'not_verified');self.assertEqual(result['visual_input'],'not_verified')
        self.assertEqual(result['hmd'],'not_run');self.assertEqual(result['ime'],'not_run')
        self.assertNotIn('PRIVATE',json.dumps(self.samples()));self.assertNotIn('NEVER RECORD',json.dumps(self.samples()))

    def test_android_completion_alone_cannot_prove_content_input_or_sdk(self):
        for change in (lambda s:s[2]['targets'][0].update(content_frame='1'),lambda s:s[2].update(delivered=0),
            lambda s:s[2]['resources']['targets'].clear(),lambda s:s[2]['windows'][1].update(parent=None)):
            samples=self.samples();change(samples);self.assertEqual(self.summary(samples)['status'],'not_verified')

    def test_mapped_disappearance_does_not_prove_every_generation_retired(self):
        for change in (lambda s:s[-1]['resources']['retired'].pop(),lambda s:s[-1]['resources']['retired'][0]['key'].update(generation='99'),
            lambda s:s[-1]['resources']['retired'][0].update(runtime='10'),lambda s:s[-1]['resources']['retired'][0].update(disposal_failed=True),
            lambda s:s[-1]['resources']['owners'].update(mode_texture_owners=1),lambda s:s[-1]['resources']['board'].update(cleanup_required=True),
            lambda s:s[-1]['resources'].update(sdk_bindings=1)):
            samples=copy.deepcopy(self.samples());change(samples);self.assertFalse(self.summary(samples)['all_resources_reclaimed'])

    def test_helper_unknown_and_connection_or_local_leaks_cannot_pass(self):
        for field,value in (('local_complete',False),('remote_complete',False),('desktop_pending',True),('owned_connections',1),('termux_pending',1)):
            samples=self.samples();samples[-1][field]=value
            self.assertFalse(self.summary(samples)['all_resources_reclaimed'])

    def test_later_clean_session_cannot_retire_old_run(self):
        for field,value in (('session','3'),('desktop_session','3'),('app_session','3'),('app_mode','foot'),('nonce','c'*16),('request_id','18')):
            samples=self.samples();samples[-1][field]=value
            if field in {'nonce','request_id'}:
                with self.assertRaises(CaptureError):self.summary(samples)
            else:self.assertFalse(self.summary(samples)['all_resources_reclaimed'])

    def test_terminal_protocol_channel_and_forward_proofs_are_independent(self):
        for section,field,value in (('remote','protocol_valid',False),('remote','terminal_event',False),('remote','eof',False),
            ('remote','channel_closed',False),('remote','exit_status',1),('remote','exit_signal',True),('desktop','forward_cancelled',False)):
            raw=snapshot(4,'closed');desktop=raw['runtime']['runtime']['transport']['desktop']
            (desktop if section=='desktop' else desktop['remote'])[field]=value
            sample=evidence.sample(raw)
            self.assertFalse(evidence.reclaimed(sample,[MAIN,DIALOG],'2',REMOTE,'17'))

    def test_stop_lease_expiry_and_errors_can_be_cleaned_without_gui_success(self):
        for reason in ('stop','lease_expired','input_eof'):
            samples=self.samples();samples[-1]['remote_proof']['reason']=reason
            result=self.summary(samples);self.assertTrue(result['all_resources_reclaimed']);self.assertEqual(result['status'],'not_verified')
        for field in ('error','cancelled'):
            samples=self.samples();samples[-1][field]=True
            self.assertEqual(self.summary(samples)['status'],'not_verified')

    def test_proven_startup_cleanup_does_not_forge_worker_wait_or_gui_success(self):
        value=snapshot(4,'closed');runtime=value['runtime']['runtime'];app=runtime['app'];desktop=runtime['transport']['desktop']
        app['local']['waited']=False;app.update(startup_failed=True,startup_cleanup_complete=True)
        desktop.update(request_id=None,start_requested=False,started=False,forward_requested=False)
        desktop['remote']['reason']='stop'
        result=evidence.sample(value)
        self.assertTrue(evidence.reclaimed(result,[],'2',REMOTE,'17'))
        app['startup_cleanup_complete']=False
        self.assertFalse(evidence.reclaimed(evidence.sample(value),[],'2',REMOTE,'17'))

    def test_unrelated_session_owner_nonce_and_foreign_windows_rejected(self):
        for change in (lambda s:s[2].update(profile='termux-local'),lambda s:s[2].update(nonce='c'*16),lambda s:s[2]['windows'][0].update(role='other')):
            samples=self.samples();change(samples)
            with self.assertRaises(CaptureError):self.summary(samples)
        samples=self.samples();samples[2]['session']='3'
        self.assertEqual(self.summary(samples)['status'],'not_verified')

    def test_trace_schema_parent_counter_and_false_physical_claims_fail(self):
        for change in (lambda t:t.update(body='PRIVATE'),lambda t:t.update(input_delta=999),lambda t:t.update(last_content_frame=0),
            lambda t:t['dialog_targets'][0].update(parent='99'),lambda t:t['targets'].append(dict(MAIN)),lambda t:t['cleanup'].update(panels=1)):
            row=copy.deepcopy(trace());change(row)
            with self.assertRaises(CaptureError):evidence.trace(row,'2','3')
        owner=evidence.binding(snapshot(),DIGEST);row=check();row['physical_input_verified']=True
        with self.assertRaises(CaptureError):evidence.check(dict(check=row),NONCE,owner)

    def test_stale_check_binding_and_trace_regression_are_rejected(self):
        owner=evidence.binding(snapshot(),DIGEST);before=check()
        for field,value in (('runtime','10'),('session','3'),('launch_request_id','18')):
            row=copy.deepcopy(before);row[field]=value
            with self.assertRaises(CaptureError):evidence.check(dict(check=row),NONCE,owner,before)
        row=copy.deepcopy(before);row['trace']['remote_nonce']='c'*16
        with self.assertRaises(CaptureError):evidence.check(dict(check=row),NONCE,owner,before)
        self.assertIsNone(evidence.check(dict(check=before),'f'*32,owner))

    def test_source_or_runtime_binding_changes_are_not_same_run(self):
        value=snapshot();before=evidence.binding(value,DIGEST)
        value['runtime']['handle']='10';self.assertNotEqual(evidence.binding(value,DIGEST),before)
        value['build_info']['source_digest']='c'*64
        with self.assertRaises(CaptureError):evidence.binding(value,DIGEST)

    def test_initial_diagnostic_can_start_before_attach_and_after_detach_but_is_not_final_cleanup(self):
        value=diagnostic_snapshot()
        self.assertTrue(evidence.idle_before_launch(value))
        self.assertFalse(evidence.empty(evidence.sample(value)))
        value['runtime']['runtime']['render']['targets']=[]
        self.assertTrue(evidence.idle_before_launch(value))
        self.assertFalse(evidence.empty(evidence.sample(value)))

    def test_diagnostic_exception_cannot_hide_app_unknown_or_foreign_owners(self):
        for change in (
            lambda v:v['runtime']['runtime'].update(app_sdk_bindings=1),
            lambda v:v['runtime']['runtime']['render']['targets'][0].update(role='windows'),
            lambda v:v['runtime']['runtime']['render']['targets'][0]['key'].update(scene='2'),
            lambda v:v['adapter']['targets'][0].update(role='windows'),
            lambda v:v['adapter']['targets'][0].update(role='unknown'),
            lambda v:v['adapter']['targets'][0].update(runtime='10'),
            lambda v:v['adapter']['targets'][0]['target'].update(scene='2'),
            lambda v:v['adapter']['targets'][0].update(retiring=True),
            lambda v:v['adapter']['targets'][0].update(disposal_failed=True),
            lambda v:v['adapter']['window_menus'].update(owners=1),
            lambda v:v['runtime']['runtime']['shutdown'].update(sdk_bindings=2),
            lambda v:v['runtime']['runtime']['window_slots'].update(cleanup_required=True),
            lambda v:v['runtime']['runtime']['transport'].update(desktop_pending=True),
            lambda v:v['runtime']['runtime']['transport'].update(pending_cleanup=[{}]),
            lambda v:v['runtime']['runtime']['transport'].update(owned_connections=1),
            lambda v:v['runtime']['runtime'].update(app=dict(state='queued')),
        ):
            value=diagnostic_snapshot();change(value)
            self.assertFalse(evidence.idle_before_launch(value))

    def test_diagnostic_still_cannot_pass_post_launch_resource_retirement(self):
        value=snapshot(4,'closed');idle=diagnostic_snapshot()
        value['runtime']['runtime']['render']=idle['runtime']['runtime']['render']
        value['runtime']['runtime']['shutdown']=idle['runtime']['runtime']['shutdown']
        value['adapter']=idle['adapter']
        self.assertTrue(evidence.idle_before_launch(value))
        self.assertFalse(evidence.reclaimed(evidence.sample(value),[MAIN,DIALOG],'2',REMOTE,'17'))

    def test_prelaunch_reclamation_requires_terminal_check_and_same_connection(self):
        value=diagnostic_snapshot();transport=value['runtime']['runtime']['transport']
        transport.update(state='Failed',session='2',profile_id='remote-linux')
        row=check('failed');row.update(launched=False,trace=None)
        self.assertTrue(evidence.before_launch_reclaimed(value,row))
        for field,replacement in (('connect_request_id',None),('session',None),('session','3'),('runtime','10'),('scene','4'),('result','running'),('launched',True),('trace',trace())):
            changed=dict(row);changed[field]=replacement
            self.assertFalse(evidence.before_launch_reclaimed(value,changed))


class OperatorTests(unittest.TestCase):
    def run_case(self,failed,diagnostic=False):
        initial=diagnostic_snapshot() if diagnostic else snapshot(1,'idle')
        main=snapshot(2,'main');dialog=snapshot(3,'dialog');closed=snapshot(4,'closed')
        live=check('running');live['phase']='OBSERVING';live['trace']=None;main['check']=copy.deepcopy(live);dialog['check']=copy.deepcopy(live)
        final=check('failed' if failed else 'completed')
        if failed:final['reason']='LINUX_TRACE_INVALID'
        closed['check']=final
        cleanup=copy.deepcopy(closed);cleanup['sequence']='5';cleanup['check']=check('cancelled');cleanup['check'].update(stop_requested=True,reason='LINUX_EXPLICIT_STOP')
        return self.run_sequence([initial,initial,main,dialog,closed,cleanup])

    def run_sequence(self,rows):
        sequence=iter(rows);commands=[]
        def adb(*args,**kwargs):
            commands.append(args)
            return '83\n' if args[:2]==('shell','pidof') else ''
        def read(*args,**kwargs):return SimpleNamespace(returncode=0,stdout=json.dumps(next(sequence,rows[-1])).encode(),stderr=b'')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            device=SimpleNamespace(validate=lambda:dict(model='Quest 3',api=34),adb=adb)
            with patch.object(sys,'argv',['quest-observe-normal-linux-gui','--serial','fixed-fixture','--artifact-root',str(root)]),\
                patch.object(cli,'QuestDevice',return_value=device),patch.object(cli.secrets,'token_hex',return_value=NONCE),\
                patch.object(cli,'digest_sources',return_value=DIGEST),patch.object(cli,'source_hashes',return_value={'fixed':'f'*64}),\
                patch.object(cli.subprocess,'run',side_effect=read),patch.object(cli.time,'sleep'),contextlib.redirect_stdout(io.StringIO()):
                code=cli.main()
            result=json.loads(next(root.glob('*/result.json')).read_text())
        return code,result,commands

    def test_completed_manual_observation_is_not_a_physical_or_visual_claim(self):
        code,result,commands=self.run_case(False)
        self.assertEqual(code,0);self.assertEqual(result['status'],'observed');self.assertFalse(result['stop_sent'])
        self.assertEqual(result['visual_input'],'not_verified');self.assertNotIn('PRIVATE',json.dumps(result))
        self.assertEqual(sum('normal-linux-gtk' in command for command in commands),1)

    def test_failure_preserved_when_same_nonce_stop_reclaims_resources(self):
        code,result,commands=self.run_case(True)
        self.assertEqual(code,1);self.assertEqual(result['status'],'failed');self.assertTrue(result['stop_sent'])
        self.assertTrue(result['failure_cleanup_complete']);self.assertEqual(result['original_check']['reason'],'LINUX_TRACE_INVALID')
        stops=[command for command in commands if 'normal-linux-gtk-stop' in command]
        self.assertEqual(len(stops),1);self.assertEqual(stops[0][-1],NONCE)

    def test_diagnostic_startup_dispatches_connect_once_and_keeps_strict_final_cleanup(self):
        code,result,commands=self.run_case(False,diagnostic=True)
        self.assertEqual(code,0);self.assertEqual(result['status'],'observed')
        self.assertEqual(sum('normal-linux-gtk' in command for command in commands),1)
        self.assertFalse(result['stop_sent'])

    def test_foreign_sdk_owner_prevents_even_the_first_broadcast(self):
        initial=diagnostic_snapshot();initial['runtime']['runtime']['app_sdk_bindings']=1
        code,result,commands=self.run_sequence([initial])
        self.assertEqual(code,1);self.assertEqual(result['reason'],'LINUX_BASELINE_NOT_IDLE')
        self.assertFalse(any('broadcast' in command for command in commands))

    def test_cancel_before_launch_keeps_failure_and_does_not_claim_gui_cleanup(self):
        initial=diagnostic_snapshot();failed=diagnostic_snapshot(2)
        failed['runtime']['runtime']['transport'].update(state='Failed',session='2',profile_id='remote-linux')
        row=check('failed');row.update(phase='PENDING_CLEANUP',reason='LINUX_CONNECT_OR_RECOVERY_REQUIRED',
            catalog_verified=False,launched=False,inspect_request_id=None,launch_request_id=None,trace=None)
        failed['check']=row
        # Androidは既に同じ接続の終了を確認してactiveを解放済み。Stopへの新ACKは来ない。
        code,result,commands=self.run_sequence([initial,initial,failed,failed])
        self.assertEqual(code,1);self.assertEqual(result['status'],'failed')
        self.assertEqual(result['original_check']['reason'],'LINUX_CONNECT_OR_RECOVERY_REQUIRED')
        self.assertEqual(sum('normal-linux-gtk-stop' in command for command in commands),1)
        self.assertTrue(result['failure_cleanup_complete'])
        self.assertEqual(result['failure_cleanup_phase'],'before_launch')
        self.assertNotIn('observation',result)


if __name__=='__main__':unittest.main()
