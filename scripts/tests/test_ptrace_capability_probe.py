import argparse
import contextlib
import copy
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SCRIPTS))
import ptrace_capability_probe as probe
from sdl_probe_install import InstallError,canonical
loader=importlib.machinery.SourceFileLoader('cap_cli',str(SCRIPTS/'quest-probe-ptrace-capability'));spec=importlib.util.spec_from_loader(loader.name,loader)
cli=importlib.util.module_from_spec(spec);loader.exec_module(cli)
NONCE='a'*32
CAP=dict(gpr_bytes=272,hardware_bytes=264,hardware_slots=6,debug_arch=6,state_clean=True)


def report():
    return dict(status='supported',reason=None,errno=None,architecture='aarch64',child_pid=71,child_group=71,child_returncode=23,expected_exit=23,
        termination='natural',stop_observed=True,exitkill_set=True,continue_sent=True,regset=dict(CAP),term_sent=False,term_forwarded=False,kill_sent=False,timed_out=False,
        reaped=True,group_reclaimed=True,pipes_drained=True,cleanup_confirmed=True,output_bytes=dict(stdout=100,stderr=0),
        traceme=dict(phase='traceme',probe_id=NONCE,pid=71,uid=10158,success=True,errno=0),continued_receipt=dict(phase='continued',probe_id=NONCE,pid=71,uid=10158),
        egl='not_run',drawing='not_run',schema=1,operator=probe.OPERATOR,probe_id=NONCE,uid=10158)


class SchemaTests(unittest.TestCase):
    def test_public_regset_width_slots_and_clean_state_only_are_returned(self):
        data=struct.pack('<I',6|(6<<8))+bytes(260)
        self.assertEqual(probe.decode_regsets(bytes(272),data),CAP)
        data=struct.pack('<I',2|(6<<8))+bytes(260)
        self.assertEqual(probe.decode_regsets(bytes(272),data)['hardware_slots'],2)
        changed=bytearray(data);changed[8:16]=(0x1234567890).to_bytes(8,'little')
        result=probe.decode_regsets(bytes(272),changed)
        self.assertFalse(result['state_clean']);self.assertNotIn('address',json.dumps(result));self.assertNotIn('1234567890',json.dumps(result))
    def test_short_regsets_and_invalid_capacity_never_pass(self):
        for gpr,hardware in ((bytes(216),bytes(264)),(bytes(272),bytes(8)),(bytes(272),struct.pack('<I',17)+bytes(260)),(bytes(272),bytes(4)+b'BAD!'+bytes(256))):
            with self.assertRaises(InstallError):probe.decode_regsets(gpr,hardware)
    def test_receipts_bind_exact_child_uid_nonce_without_arbitrary_content(self):
        value=report()['traceme'];self.assertEqual(probe.receipt(canonical(value),71,NONCE,10158),value)
        for mutation in ({'pid':72},{'uid':10159},{'probe_id':'b'*32},{'address':123},{'body':'PRIVATE'}):
            v=dict(value,**mutation)
            with self.assertRaises(InstallError):probe.receipt(canonical(v),71,NONCE,10158)
        for mutation in ({'errno':True},{'errno':None},{'errno':-1}):
            with self.assertRaises(InstallError):probe.receipt(canonical(dict(value,**mutation)),71,NONCE,10158)
    def test_result_wire_rejects_extra_registers_false_cleanup_or_exit_zero(self):
        self.assertEqual(cli.validated_result(report(),NONCE),report())
        for mutate in (lambda v:v.update(schema=True),lambda v:v.update(registers='PRIVATE'),lambda v:v.update(child_returncode=0),lambda v:v.update(cleanup_confirmed=False),
                       lambda v:v['regset'].update(hardware_slots=2),lambda v:v['regset'].update(state_clean=False),lambda v:v['regset'].update(address=123)):
            v=report();mutate(v)
            with self.assertRaises(InstallError):cli.validated_result(v,NONCE)


@unittest.skipUnless(sys.platform.startswith('linux'),'Linux ptrace fixture')
class OwnChildTests(unittest.TestCase):
    def command(self,after='',before=''):
        source=(f"import sys,os,time,signal,resource\nsys.path.insert(0,{str(SCRIPTS)!r})\nimport ptrace_capability_probe as p\np.UID=os.getuid()\n"
            +before+"\nresult=p.child_main()\n"+after+"\nraise SystemExit(result)\n")
        return [sys.executable,'-c',source]
    def observe(self,command=None,**kwargs):
        read_fd,write_fd=os.pipe()
        try:return probe.observe(command or self.command(),dict(PATH=os.environ['PATH']),read_fd,NONCE,expected_uid=os.getuid(),
            architecture=kwargs.pop('architecture','aarch64'),assessor=kwargs.pop('assessor',lambda *_:dict(CAP)),timeout=kwargs.pop('timeout',2),grace=.2,**kwargs)
        finally:os.close(read_fd);os.close(write_fd)
    def require_trace(self,value):
        if value.get('traceme') and not value['traceme']['success']:self.skipTest('kernel refused own-child TRACEME')
    def test_real_traceme_stop_continue_preserves_nonzero_sentinel_and_reaps(self):
        value=self.observe();self.require_trace(value)
        self.assertEqual(value['status'],'supported');self.assertEqual(value['child_returncode'],23)
        self.assertTrue(value['stop_observed'] and value['exitkill_set'] and value['continue_sent'])
        self.assertTrue(value['cleanup_confirmed']);self.assertFalse(value['term_sent'] or value['kill_sent']);self.assertEqual(value['egl'],'not_run')
    def test_architecture_and_small_slot_capability_are_unsupported_but_resume_and_reap(self):
        for kwargs,reason in ((dict(architecture='x86_64'),'ARCH_UNSUPPORTED'),(dict(assessor=lambda *_:dict(CAP,hardware_slots=2)),'SLOT_LIMIT'),
                              (dict(assessor=lambda *_:dict(CAP,state_clean=False)),'DEBUG_STATE_BUSY')):
            v=self.observe(**kwargs);self.require_trace(v);self.assertEqual(v['status'],'unsupported');self.assertEqual(v['reason'],reason)
            self.assertEqual(v['child_returncode'],23);self.assertTrue(v['cleanup_confirmed'])
    def test_regset_refusal_preserves_errno_and_sentinel(self):
        def refused(*_):raise probe.PtraceError(5)
        v=self.observe(assessor=refused);self.require_trace(v)
        self.assertEqual(v['reason'],'REGSET_UNSUPPORTED');self.assertEqual(v['errno'],5);self.assertEqual(v['child_returncode'],23);self.assertTrue(v['cleanup_confirmed'])
    def test_real_getregset_iovec_receives_native_width_without_logging_registers(self):
        sizes={'x86_64':216,'aarch64':272};size=sizes.get(os.uname().machine)
        if size is None:self.skipTest('native GPR width not declared')
        def assessment(backend,pid):
            self.assertEqual(len(backend.regset(pid,1,size)),size)
            return dict(CAP)
        v=self.observe(assessor=assessment);self.require_trace(v)
        self.assertEqual(v['status'],'supported');self.assertTrue(v['cleanup_confirmed']);self.assertNotIn('registers',json.dumps(v))
    def test_wrong_exit_and_original_signal_are_never_translated_to_success(self):
        for after,code in (('result=7',7),('os.kill(os.getpid(),signal.SIGUSR1)',-signal.SIGUSR1)):
            v=self.observe(self.command(after));self.require_trace(v)
            self.assertEqual(v['status'],'unsupported');self.assertEqual(v['child_returncode'],code);self.assertTrue(v['cleanup_confirmed'])
    def test_deadline_forwards_term_from_traced_stop_and_preserves_unrelated_child(self):
        unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
        try:
            v=self.observe(self.command('time.sleep(30)'),timeout=.2);self.require_trace(v)
            self.assertEqual(v['reason'],'TIMEOUT');self.assertTrue(v['timed_out'] and v['term_sent'] and v['term_forwarded'])
            self.assertEqual(v['child_returncode'],-signal.SIGTERM);self.assertFalse(v['kill_sent']);self.assertTrue(v['cleanup_confirmed']);self.assertIsNone(unrelated.poll())
        finally:unrelated.terminate();unrelated.wait(timeout=2)
    def test_cancel_and_malformed_stdout_discard_body_and_reclaim_only_child(self):
        with tempfile.TemporaryDirectory(prefix='cap-cancel-') as td:
            ready=Path(td)/'ready'
            v=self.observe(self.command(f"open({str(ready)!r},'w').close();time.sleep(30)"),interrupted=ready.exists);self.require_trace(v)
            self.assertEqual(v['reason'],'HOST_CANCEL');self.assertTrue(v['term_forwarded'] and v['cleanup_confirmed'])
        v=self.observe(self.command(before="os.write(1,b'PRIVATE /body/address\\n')"))
        self.assertEqual(v['reason'],'CHILD_OUTPUT_INVALID');self.assertTrue(v['cleanup_confirmed']);self.assertNotIn('PRIVATE',json.dumps(v))
    def test_control_eof_is_finite_and_reaped(self):
        read_fd,write_fd=os.pipe();os.close(write_fd)
        try:v=probe.observe(self.command('time.sleep(30)'),dict(PATH=os.environ['PATH']),read_fd,NONCE,expected_uid=os.getuid(),architecture='aarch64',assessor=lambda *_:dict(CAP),timeout=2,grace=.2)
        finally:os.close(read_fd)
        self.assertEqual(v['reason'],'CONTROL_EOF');self.assertTrue(v['cleanup_confirmed'])
    def test_kernel_denial_is_fixed_unsupported_and_original_exit_retained(self):
        source="import json,os,sys;x=json.loads(sys.stdin.readline());print(json.dumps(dict(phase='traceme',success=False,errno=1,probe_id=x['probe_id'],pid=os.getpid(),uid=os.getuid())),flush=True);raise SystemExit(77)"
        v=self.observe([sys.executable,'-c',source]);self.assertEqual(v['reason'],'PTRACE_DENIED');self.assertEqual(v['errno'],1);self.assertEqual(v['child_returncode'],77);self.assertTrue(v['cleanup_confirmed'])


class CliAndRunTests(unittest.TestCase):
    def test_private_directory_modes_nonoverwrite_and_symlink_rejection(self):
        with tempfile.TemporaryDirectory(prefix='cap-path-') as td,patch.object(probe,'UID',os.getuid()):
            home=Path(td)/'home';home.mkdir(mode=0o700)
            path=probe.private_run(home,NONCE);self.assertEqual(path.stat().st_mode&0o777,0o700)
            with self.assertRaises(InstallError):probe.private_run(home,NONCE)
            elsewhere=Path(td)/'other';elsewhere.mkdir(mode=0o700);home2=Path(td)/'home2';home2.mkdir(mode=0o700);(home2/'.local').symlink_to(elsewhere)
            with self.assertRaises(OSError):probe.private_run(home2,'b'*32)
    def test_remote_bootstrap_is_bounded_imports_only_fixed_code_and_hashes_cli(self):
        import shlex
        source=cli.remote_source();compile(source,'cap-remote','exec')
        self.assertLessEqual(len(shlex.join(['run-as','com.termux','/data/data/com.termux/files/usr/bin/python3','-c',source]).encode()),49*1024)
        program=cli.remote_program().rsplit("raise SystemExit(sys.modules['ptrace_probe'].remote_main())",1)[0]+"print(callable(sys.modules['ptrace_probe'].observe))\n"
        p=subprocess.run([sys.executable,'-'],input=program.encode(),capture_output=True,timeout=5)
        self.assertEqual(p.returncode,0);self.assertEqual(p.stdout.strip(),b'True');self.assertIn('scripts/quest-probe-ptrace-capability',cli.source_hashes())
        self.assertNotIn('eglInitialize',cli.remote_program());self.assertNotIn('PTRACE_ATTACH',cli.remote_program())
    def test_remote_early_exit_and_broken_stdin_keep_artifact(self):
        class Device:
            def __init__(self,*_):pass
            def validate(self):return dict(model='Quest 3',api=34)
            def adb(self,*_):return 'package:com.termux uid:10158\n'
        with tempfile.TemporaryDirectory(prefix='cap-cli-') as td:
            original=subprocess.Popen
            def spawn(*_,**kwargs):return original([sys.executable,'-c','import os;os.close(0);raise SystemExit(7)'],**kwargs)
            with patch.object(cli.argparse.ArgumentParser,'parse_args',return_value=argparse.Namespace(serial='fixture',artifact_root=Path(td))),patch.object(cli,'QuestDevice',Device),patch.object(cli.subprocess,'Popen',side_effect=spawn),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(),2)
            v=json.loads(next(Path(td).glob('*/result.json')).read_text());self.assertEqual(v['status'],'unsupported');self.assertEqual(v['remote_returncode'],7);self.assertEqual(v['reason'],'REMOTE_OUTPUT_INVALID')


if __name__=='__main__':unittest.main()
