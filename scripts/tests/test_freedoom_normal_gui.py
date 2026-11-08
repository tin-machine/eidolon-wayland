import copy
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SCRIPTS));sys.path.insert(0,str(SCRIPTS/'tests'))
import freedoom_normal_evidence as evidence
from sdl_lease_capture import CaptureError
from test_gtk_interaction_live import sdk
loader=importlib.machinery.SourceFileLoader('freedoom_normal_cli',str(SCRIPTS/'quest-run-freedoom-gui'))
spec=importlib.util.spec_from_loader(loader.name,loader);cli=importlib.util.module_from_spec(spec);loader.exec_module(cli)
NONCE='a'*32;BINDING=dict(handle='9',session='2',nonce='0123456789abcdef');REVISION='b'*64
APP_ID=evidence.app_id(BINDING['nonce'],'2');KEY=dict(session='2',window='5',scene='3',generation='4')


def sample(main=True,retired=False,seq=1,content='2'):
    source=sdk(main=main,retired=retired)
    if main and not retired:source['targets'][-1]['content_frame']=content
    row=dict(sequence=str(seq),mapped=[w['window'] for w in source['windows']],resources=source['resources'],native_targets=[t['key'] for t in source['targets']],
        targets=[dict(window=t['key']['window'],scene=t['key']['scene'],generation=t['key']['generation'],frames=t['frames'],content_frame=t['content_frame']) for t in source['targets']],
        gui_windows=['5'] if main and not retired else [],foreign_windows=[],session_copies=10+seq,transport_state='Ready',pending_cleanup=1)
    return row


def receipt():
    return dict(schema=1,uid=10158,nonce=BINDING['nonce'],session='2',request_id='17',terminal_id='2',app_id=APP_ID,
        worker=dict(pid=19,start='23',group=19),child=dict(pid=29,start='31',group=29),child_returncode=0,worker_returncode=0,reason='natural',
        cleanup_complete=True,pipes_closed=True,assets_runtime_unchanged=True,term_signals=0,kill_signals=0,reaped=1,open_pidfds=0)


def android(case='keys'):
    steps=evidence.STEPS[case][0];counts=[10,10,12,14,16,16,18] if case=='keys' else [10]*len(steps)
    commands=[dict(request_id=str(i+1),kind='pointer' if i<2 else 'key' if case=='keys' else 'close_window_target',completion='ACCEPTED') for i in range(10 if case=='keys' else 4)]
    focus=dict(schema=1,phase='WAIT_FOCUS',state='complete',reason='COUNTERS_COMPLETE',elapsed_ms=30,floor_keys=10,floor_buttons=20,
        expected_keys=10,expected_buttons=22,observed_keys=10,observed_buttons=22,keys_pressed=0,buttons_pressed=0)
    close=None if case=='keys' else dict(schema=1,state='complete',reason='CLOSE_COMPLETE',base_epoch=1,observed_epoch=4,close_requests=2,retirement_bound=True,all_acknowledged=True,elapsed_ms=1000)
    return dict(case='freedoom-'+case,nonce=NONCE,result='completed',phase='COMPLETE',reason=None,cleanup_reason=None,scope='production_registered_target_bound_synthetic_native',
        physical_input_verified=False,ime_verified=False,host_physical_filter_verified=False,runtime='9',session='2',scene='3',foot_windows=['1'],revision=REVISION,
        request_id='17',launch_id='2',app_id=APP_ID,target=KEY,input_epoch=1,first_close_pending=case=='close',natural_exit=True,cleanup_complete=True,session_stop_sent=False,
        focus_progress=focus,close_progress=close,commands=commands,steps=[dict(stage=stage,target=KEY,input_epoch=1 if i<len(steps)-1 else 2 if case=='keys' else 4,delivered=count) for i,(stage,count) in enumerate(zip(steps,counts))])


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.base=sample(main=False);self.live=sample(seq=2);self.next=sample(seq=3,content='3');self.end=sample(retired=True,seq=4)
        self.samples=[self.live,self.next,self.end]
    def summary(self,case='keys',samples=None,check=None,child=None):
        return evidence.summarize(self.base,self.samples if samples is None else samples,android(case) if check is None else check,receipt() if child is None else child,case)
    def test_real_resource_fixture_two_content_frames_ack_and_natural_exit_pass(self):
        for case in evidence.CASES:
            row=android(case);self.assertEqual(evidence.android(dict(check=row),NONCE,case,BINDING,self.base,REVISION,'3'),row)
            result=self.summary(case);self.assertEqual(result['status'],'observed');self.assertTrue(result['resources_retired']);self.assertEqual(result['wire_trace'],'not_run')
            self.assertEqual(result['copy_counter_scope'],'whole_session');self.assertFalse(result['session_stop_cleanup'])
    def test_body_other_owner_fake_input_scope_and_unknown_progress_cannot_be_saved(self):
        for mutate in (lambda r:r.update(body='PRIVATE'),lambda r:r.update(app_id='PRIVATE'),lambda r:r.update(session='3'),lambda r:r.update(revision='c'*64),
                       lambda r:r.update(physical_input_verified=True),lambda r:r['focus_progress'].update(raw='PRIVATE')):
            row=android();mutate(row)
            with self.assertRaises(CaptureError):evidence.android(dict(check=row),NONCE,'keys',BINDING,self.base,REVISION,'3')
        self.assertIsNone(evidence.android(dict(check=android()),'f'*32,'keys',BINDING,self.base,REVISION,'3'))
    def test_wrong_cookie_transient_baseline_loss_and_mapped_only_retirement_fail(self):
        for mutate in (lambda s:s[0].update(gui_windows=[],foreign_windows=['5']),lambda s:s[0].update(mapped=['5']),
            lambda s:s[-1]['resources']['retired'].clear(),lambda s:s[-1]['resources']['owners'].update(mode_texture_owners=2),
            lambda s:s[-1]['resources']['board'].update(cleanup_required=True)):
            samples=copy.deepcopy(self.samples);mutate(samples);self.assertEqual(self.summary(samples=samples)['status'],'not_verified')
        replacement=copy.deepcopy(self.next);replacement['resources']['targets'][-1]['key']['generation']='8'
        self.assertEqual(self.summary(samples=[self.live,replacement,self.end])['status'],'not_verified')
    def test_signal_rc_and_missing_pidfd_cleanup_never_rescue_original_failure(self):
        for key,bad in (('child_returncode',-15),('worker_returncode',2),('reason','signal'),('term_signals',1),('kill_signals',1),('open_pidfds',1),('assets_runtime_unchanged',False),('child',None),('worker',None)):
            child=receipt();child[key]=bad;self.assertEqual(self.summary(child=child)['status'],'not_verified')
        row=android();row.update(result='failed',phase='PENDING_CLEANUP',reason='FREEDOOM_OTHER_INPUT',cleanup_complete=False)
        self.assertEqual(self.summary(check=row)['status'],'not_verified')
    def test_dropped_key_release_and_missing_second_close_ack_do_not_pass(self):
        row=android();row['steps'][-1]['delivered']=17;self.assertEqual(self.summary(check=row)['status'],'not_verified')
        row=android('close');row['commands'][-1]['completion']=None;self.assertEqual(self.summary('close',check=row)['status'],'not_verified')
        row=android('close');row['commands']=[c for c in row['commands'] if c['kind']!='close_window_target'];self.assertEqual(self.summary('close',check=row)['status'],'not_verified')
    def test_receipt_selection_cannot_borrow_an_old_game_or_other_uid(self):
        def runtime(rows):return dict(transport=dict(app_forward=dict(remote=dict(gui_receipts=rows))))
        self.assertEqual(evidence.receipt(runtime([receipt()]),android(),BINDING),receipt())
        wrong=receipt();wrong['request_id']='18';self.assertIsNone(evidence.receipt(runtime([wrong]),android(),BINDING))
        for key,bad in (('uid',10159),('session','3'),('app_id','PRIVATE'),('body','PRIVATE')):
            wrong=receipt();wrong[key]=bad
            with self.assertRaises(CaptureError):evidence.receipt(runtime([wrong]),android(),BINDING)
        wrong=receipt();wrong['child_returncode']=7
        with self.assertRaises(CaptureError):evidence.receipt(runtime([receipt(),wrong]),android(),BINDING)
    def test_stop_requires_all_full_targets_and_all_owners_zero_and_stays_separate(self):
        end=copy.deepcopy(self.end);end.update(mapped=[],targets=[],native_targets=[],gui_windows=[],transport_state='Idle',pending_cleanup=0)
        resource=end['resources'];resource.update(targets=[],panels=[],sdk_bindings=0,owners={name:0 for name in resource['owners']})
        resource['board']=dict(entries=[],cleanup_required=False,unassigned=[])
        resource['retired'].append(dict(runtime='9',key=dict(session='2',window='1',scene='3',generation='4'),entity='100',sdk_disposed=True,sdk_binding_released=True,disposal_failed=False))
        samples=[self.live,self.next,end];self.assertTrue(evidence.stopped(self.base,samples));self.assertEqual(self.summary(samples=samples)['status'],'not_verified')
        for mutate in (lambda r:r['owners'].update(menu_texture_owners=1),lambda r:r['retired'].clear(),lambda r:r['board'].update(unassigned=['5'])):
            bad=copy.deepcopy(samples);mutate(bad[-1]['resources']);self.assertFalse(evidence.stopped(self.base,bad))
    def test_inspect_uses_no_register_and_rejects_unknown_body_or_hashless_optin(self):
        report=dict(schema=1,status='inspected',run_id=NONCE,uid=10158,action='inspect',configured=True,revision=REVISION,total=3,target_present=True,target_matches=True,
            changed=False,other_entries_preserved=True,asset_runtime_validated=True,execution='not_run',reload_requested=False)
        def execute(command,**kwargs):
            request=json.loads(kwargs['input']);self.assertEqual(request['action'],'inspect');self.assertIsNone(request['expected_revision'])
            return subprocess.CompletedProcess(command,0,json.dumps(report).encode(),b'')
        with patch.object(cli.subprocess,'run',side_effect=execute):self.assertEqual(cli.inspect_registration('fixed',NONCE,'c'*32,'d'*64),report)
        for key,bad in (('body','PRIVATE'),('configured',False),('target_matches',False),('revision',None)):
            wrong=dict(report);wrong[key]=bad
            with patch.object(cli.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps(wrong).encode(),b'')),self.assertRaises(CaptureError):
                cli.inspect_registration('fixed',NONCE,'c'*32,'d'*64)


if __name__=='__main__':unittest.main()
