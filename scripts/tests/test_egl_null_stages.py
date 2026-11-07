import argparse
import contextlib
import copy
import ctypes
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import signal
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SCRIPTS))
import egl_null_stages as n
import egl_surfaceless_probe as egl
from sdl_probe_install import InstallError,canonical
from test_egl_surfaceless_probe import api_events,FakeEgl

loader=importlib.machinery.SourceFileLoader('null_cli',str(SCRIPTS/'quest-probe-egl-null-stages'));spec=importlib.util.spec_from_loader(loader.name,loader)
cli=importlib.util.module_from_spec(spec);loader.exec_module(cli)
NONCE='b'*32
ADDRESSES=[0x10000000+p[1] for p in n.POINTS]
ORIGINAL=struct.pack('<I',6|(8<<8))+bytes(260)


def gpr(index=0,nonnull=True,mode=2,fd=0xffffffff):
    values=[0]*34;values[0]=0x1234 if nonnull else 0;values[23]=mode;values[26]=fd;values[32]=ADDRESSES[index]
    return struct.pack('<34Q',*values)


class FakeBackend:
    def __init__(self):
        self.hardware=ORIGINAL;self.gpr=bytes(272);self.calls=[];self.sets=[];self.refuse=False;self.partial=False;self.corrupt=False
    def call(self,*a,**kw):self.calls.append((a,kw));return 0
    def regset(self,pid,note,size):
        if note==1:return self.gpr
        return self.hardware
    def set_hardware(self,pid,data):
        self.sets.append((pid,data))
        if self.partial:self.hardware=data[:24]+self.hardware[24:];self.partial=False;raise n.PtraceError(5)
        if self.refuse:raise n.PtraceError(1)
        self.hardware=data
        if self.corrupt:self.hardware=data[:8]+bytes(16)+data[24:];self.corrupt=False


class NormalizingBackend(FakeBackend):
    def set_hardware(self,pid,data):
        super().set_hardware(pid,data)
        normalized=bytearray(self.hardware)
        for i in range(3):
            if struct.unpack_from('<I',normalized,16+i*16)[0]==0:struct.pack_into('<I',normalized,16+i*16,0x1e4)
        self.hardware=bytes(normalized)


def ready_trace(backend=None):
    b=backend or FakeBackend();t=n.Trace(b,71,lambda *_:list(ADDRESSES),'aarch64');t.start();return b,t


def elf_fixture():
    data=bytearray(8192);data[:16]=b'\x7fELF\x02\x01\x01'+bytes(9)
    struct.pack_into('<HHIQQQIHHHHHH',data,16,3,183,1,0,64,0,0,64,56,1,0,0,0)
    struct.pack_into('<IIQQQQQQ',data,64,1,5,4096,0xaa9000,0,4096,4096,4096)
    for _,address,word in n.POINTS:struct.pack_into('<I',data,4096+address-0xaa9000,word)
    return bytes(data)


class StageTests(unittest.TestCase):
    def test_mode_and_fd_are_independently_classified_without_register_or_fd_integer(self):
        for kind,label in enumerate(n.DRI_TYPES):
            for fd,missing in ((0xffffffff,True),(15,False),(0xfffffffe,False)):
                fields=n.classify_mode(struct.unpack('<34Q',gpr(mode=kind,fd=fd)))
                self.assertEqual(fields,dict(dri_type=label,type_is_swrast=kind==2,fd_is_minus_one=missing))
                if kind!=2 or not missing:
                    b,t=ready_trace();t.prepare();b.gpr=gpr(mode=kind,fd=fd);self.assertTrue(t.trap())
                    v=t.report();self.assertEqual(v['mode_observation'],fields);self.assertFalse(v['mode_expected'])
                    self.assertEqual(v['reason'],'DRI_MODE_UNEXPECTED');self.assertEqual(list(v['stages'].values()),[None]*3)
                    self.assertEqual(b.hardware,ORIGINAL);cli.validated_internal(v)
        for value in (4,0xffffffff):
            fields=n.classify_mode(struct.unpack('<34Q',gpr(mode=value)))
            self.assertEqual(fields,dict(dri_type='unknown',type_is_swrast=False,fd_is_minus_one=True));self.assertNotIn(str(value),json.dumps(fields))
    def test_mode_wire_rejects_raw_values_inconsistent_booleans_and_free_enum(self):
        b,t=ready_trace();t.prepare();b.gpr=gpr(mode=1);t.trap();valid=t.report()
        for mutate in (lambda v:v['mode_observation'].update(fd=15),lambda v:v['mode_observation'].update(register=0),
                       lambda v:v['mode_observation'].update(dri_type='PRIVATE'),lambda v:v['mode_observation'].update(type_is_swrast=True),
                       lambda v:v['mode_observation'].update(fd_is_minus_one=1),lambda v:v.update(mode_observation=None)):
            value=copy.deepcopy(valid);mutate(value)
            with self.assertRaises(InstallError):cli.validated_internal(value)
    def test_kernel_normalized_disabled_controls_are_safe_and_other_slots_stay_unchanged(self):
        b,t=ready_trace(NormalizingBackend());self.assertTrue(t.prepare());b.gpr=gpr();self.assertTrue(t.trap())
        b.gpr=gpr(1,False);self.assertTrue(t.trap());self.assertEqual(t.report()['status'],'observed')
        self.assertNotEqual(b.hardware,ORIGINAL);self.assertTrue(n.hardware_matches(b.hardware,ORIGINAL))
        changed=bytearray(b.hardware);changed[56]=1;self.assertFalse(n.hardware_matches(changed,ORIGINAL))
    def test_setregset_writes_only_three_slot_prefix_not_all_sixteen(self):
        b=n.Backend();calls=[]
        class Iovec(ctypes.Structure):_fields_=[('base',ctypes.c_void_p),('length',ctypes.c_size_t)]
        def call(request,pid,address,data):
            view=ctypes.cast(data,ctypes.POINTER(Iovec)).contents
            calls.append((request,pid,address,view.length,ctypes.string_at(view.base,view.length)))
        b.call=call;b.set_hardware(71,ORIGINAL)
        self.assertEqual(calls,[(0x4205,71,0x402,56,ORIGINAL[:56])])
    def test_all_three_null_prefixes_and_success_are_fixed_booleans_and_disarmed(self):
        for values in ([False],[True,False],[True,True,False],[True,True,True]):
            b,t=ready_trace();self.assertTrue(t.prepare())
            for i,value in enumerate(values):b.gpr=gpr(i,value);self.assertTrue(t.trap())
            result=t.report();self.assertEqual(result['status'],'observed');self.assertTrue(result['mode_expected'])
            self.assertEqual(list(result['stages'].values()),list(values)+[None]*(3-len(values)));self.assertEqual(b.hardware,ORIGINAL)
            self.assertEqual(result['hits'],len(values));self.assertTrue(result['breakpoints_cleared']);cli.validated_internal(result)
            raw=json.dumps(result);self.assertNotIn('address',raw);self.assertNotIn('1234',raw);self.assertNotIn('registers',raw)
    def test_no_hardware_write_before_image_words_and_capability_are_verified(self):
        for failure in ('LIBRARY_UNVERIFIED','ELF_INVALID','INSTRUCTION_CHANGED','MAP_UNVERIFIED'):
            b,t=ready_trace()
            def image(*_):raise InstallError(failure)
            t.image=image;self.assertTrue(t.prepare());self.assertFalse(b.sets);self.assertEqual(t.report()['reason'],failure)
        for data,reason in ((struct.pack('<I',2|(8<<8))+bytes(260),'SLOT_LIMIT'),(ORIGINAL[:8]+b'X'+ORIGINAL[9:],'DEBUG_STATE_BUSY')):
            b,t=ready_trace();b.hardware=data;self.assertTrue(t.prepare());self.assertFalse(b.sets);self.assertEqual(t.report()['reason'],reason)
    def test_set_refusal_and_partial_write_restore_original_before_continuing(self):
        for variant in ('refuse','partial','corrupt'):
            b,t=ready_trace();setattr(b,variant,True);self.assertTrue(t.prepare());self.assertEqual(b.hardware,ORIGINAL)
            self.assertEqual(t.report()['status'],'unsupported');self.assertTrue(t.report()['breakpoints_cleared'])
            self.assertEqual(t.report()['reason'],'BREAKPOINT_VERIFY_FAILED' if variant=='corrupt' else 'SETREGSET_UNSUPPORTED')
    def test_mode_order_duplicate_unknown_trap_and_short_register_fail_without_raw_state(self):
        for variant,reason in (('mode','DRI_MODE_UNEXPECTED'),('order','STAGE_ORDER_INVALID'),('unknown','UNEXPECTED_STOP'),('short','REGSET_SIZE_INVALID')):
            b,t=ready_trace();self.assertTrue(t.prepare())
            if variant=='mode':b.gpr=gpr(mode=1)
            elif variant=='order':b.gpr=gpr(1)
            elif variant=='unknown':
                values=list(struct.unpack('<34Q',gpr()));values[32]=0x555555; b.gpr=struct.pack('<34Q',*values)
            else:b.gpr=bytes(200)
            self.assertTrue(t.trap());self.assertEqual(t.report()['reason'],reason);self.assertEqual(b.hardware,ORIGINAL)
            if variant=='unknown':self.assertFalse(t.last_trap_owned)
        b,t=ready_trace();t.prepare();b.gpr=gpr();t.trap();self.assertTrue(t.trap());self.assertEqual(t.report()['reason'],'STAGE_ORDER_INVALID')
    def test_partial_and_absent_checkpoints_do_not_claim_branch_identification(self):
        b,t=ready_trace();self.assertEqual(t.report()['reason'],'CHECKPOINT_MISSING');t.prepare();self.assertEqual(t.report()['reason'],'NO_HITS')
        b.gpr=gpr();t.trap();self.assertEqual(t.report()['reason'],'PARTIAL_STAGES');t.disarm()
    def test_external_architecture_is_unsupported_without_arming(self):
        b,t=ready_trace();t.architecture='x86_64';t.reason=None;t.start();self.assertTrue(t.prepare());self.assertFalse(b.sets);self.assertEqual(t.report()['reason'],'ARCH_UNSUPPORTED')
    def test_wire_rejects_addresses_free_stage_raw_log_and_fake_branch_success(self):
        b,t=ready_trace();t.prepare();b.gpr=gpr(nonnull=False);t.trap();valid=t.report()
        for mutate in (lambda v:v.update(address=123),lambda v:v['stages'].update(private='BODY'),lambda v:v.update(reason='PRIVATE'),
                       lambda v:v.update(breakpoints_cleared=False),lambda v:v.update(hits=3),lambda v:v.update(mode_expected=False),lambda v:v['stages'].update(pipe_screen=True)):
            value=copy.deepcopy(valid);mutate(value)
            with self.assertRaises(InstallError):cli.validated_internal(value)


class ImageTests(unittest.TestCase):
    def test_elf_machine_segments_and_all_instruction_words_are_mandatory(self):
        data=elf_fixture();self.assertEqual(len(n.elf_points(data)),3)
        for mutate in (lambda b:b.__setitem__(0,0),lambda b:struct.pack_into('<H',b,18,62),lambda b:struct.pack_into('<Q',b,32,99999),
                       lambda b:struct.pack_into('<I',b,68,4),lambda b:struct.pack_into('<I',b,4096+n.POINTS[1][1]-0xaa9000,0)):
            value=bytearray(data);mutate(value)
            with self.assertRaises(InstallError):n.elf_points(value)
        with self.assertRaises(InstallError):n.elf_points(data[:70])
    def test_selected_map_ignores_other_libraries_but_rejects_deleted_inode_or_path(self):
        with tempfile.TemporaryDirectory(prefix='null-map-') as td:
            path=Path(td)/n.NAME;path.write_bytes(elf_fixture());info=path.stat()
            row=f'10aa9000-10aaa000 r-xp 00001000 {os.major(info.st_dev):x}:{os.minor(info.st_dev):x} {info.st_ino} {path}\n'.encode()
            raw=b'1111-2222 r-xp 0000 00:00 9 PRIVATE /other-library\n'+row
            selected=n.selected_mappings(raw,path,info);self.assertEqual(n.mapped_addresses(selected,n.elf_points(path.read_bytes()),4096),ADDRESSES)
            for changed in (row[:-1]+b' (deleted)\n',row.replace(str(info.st_ino).encode(),b'1'),row.replace(os.fsencode(path),b'PRIVATE/'+n.NAME.encode()),row[:-1],b'x'*(n.MAX_MAPS+1)):
                with self.assertRaises(InstallError):n.selected_mappings(changed,path,info)
    def test_file_pin_uid_mode_symlink_and_in_memory_instruction_are_all_checked(self):
        with tempfile.TemporaryDirectory(prefix='null-image-') as td:
            home=Path(td)/'home';home.mkdir();directory=Path(td)/'usr/lib';directory.mkdir(parents=True);path=directory/n.NAME;path.write_bytes(elf_fixture());path.chmod(0o600)
            info=path.stat();before={n.NAME:dict(identity=list(egl.identity(info)))};pins={n.NAME:hashlib.sha256(path.read_bytes()).hexdigest()}
            raw=f'10aa9000-10aaa000 r-xp 00001000 {os.major(info.st_dev):x}:{os.minor(info.st_dev):x} {info.st_ino} {path}\n'.encode()
            b=FakeBackend();b.instruction=lambda pid,address:n.POINTS[ADDRESSES.index(address)][2]
            def maps(*args,**kwargs):self.assertEqual(args[0],'/proc/71/maps');return io.BytesIO(raw)
            with patch.object(n,'UID',os.getuid()),patch.dict(egl.LIBRARIES,pins),patch('builtins.open',side_effect=maps):
                self.assertEqual(n.verified_image(home,before,71,b),ADDRESSES)
                b.instruction=lambda *_:0
                with self.assertRaisesRegex(InstallError,'INSTRUCTION_CHANGED'):n.verified_image(home,before,71,b)
                b.instruction=lambda pid,address:n.POINTS[ADDRESSES.index(address)][2]
                pins[n.NAME]='f'*64
                with patch.dict(egl.LIBRARIES,pins),self.assertRaisesRegex(InstallError,'LIBRARY_UNVERIFIED'):n.verified_image(home,before,71,b)
                path.chmod(0o666)
                with self.assertRaisesRegex(InstallError,'LIBRARY_UNVERIFIED'):n.verified_image(home,before,71,b)
                path.chmod(0o600);path.unlink();path.symlink_to(directory/'missing')
                with self.assertRaises(OSError):n.verified_image(home,before,71,b)


class HookAndBootstrapTests(unittest.TestCase):
    def test_prior_failure_match_is_explicit_and_never_changes_api_result(self):
        api=api_events()[0]['result'];original=copy.deepcopy(api)
        self.assertTrue(n.prior_failure_matches(api,1));self.assertEqual(api,original)
        self.assertFalse(n.prior_failure_matches(api,0));self.assertIsNone(n.prior_failure_matches(None,1))
        api['initialize']['returned']=True;api['initialize']['error']=dict(value=12288,name='success')
        self.assertFalse(n.prior_failure_matches(api,0))
    def test_hook_only_before_initialize_preserves_first_error_flush_then_cleanup(self):
        library=FakeEgl();events=[]
        def hook():self.assertEqual(library.calls[-1],'error');self.assertNotIn('initialize',library.calls);library.calls.append('hook')
        self.assertEqual(egl.perform_api(library,events.append,before_initialize=hook),1)
        self.assertEqual(library.calls[library.calls.index('hook')+1],'initialize');self.assertEqual(events[0]['result']['initialize']['error']['value'],12289)
        library=FakeEgl(display=False);egl.perform_api(library,lambda *_:None,before_initialize=lambda:self.fail('null display hook'))
    def test_binding_event_is_exact_and_forbids_body_registers_and_other_child(self):
        event=dict(phase='initialize_checkpoint',probe_id=NONCE,pid=71,uid=10158)
        self.assertEqual(n.decode_event(canonical(event),71,NONCE),event)
        for mutation in ({'pid':72},{'probe_id':'c'*32},{'body':'PRIVATE'},{'register':1}):
            with self.assertRaises(InstallError):n.decode_event(canonical(dict(event,**mutation)),71,NONCE)
    def test_compressed_bootstrap_is_under_budget_and_imports_without_egl_execution(self):
        command=shlex.join(['run-as','com.termux','/data/data/com.termux/files/usr/bin/python3','-c',cli.remote_source()]);self.assertLessEqual(len(command.encode()),49*1024)
        source=cli.remote_program().rsplit("raise SystemExit(sys.modules['egl_null_stages'].remote_main())",1)[0]+"print(callable(sys.modules['egl_null_stages'].observe))\n"
        p=subprocess.run([sys.executable,'-'],input=source.encode(),capture_output=True,timeout=5)
        self.assertEqual(p.returncode,0,p.stderr.decode());self.assertEqual(p.stdout.strip(),b'True');self.assertIn('scripts/quest-probe-egl-null-stages',cli.source_hashes())
        self.assertNotIn('WaylandTrace:',cli.remote_program());self.assertNotIn('def observe_owned(',cli.remote_program())
        import ast
        assignment=next(v for v in ast.parse(cli.remote_program()).body if isinstance(v,ast.Assign) and any(isinstance(t,ast.Attribute) and t.attr=='CHILD_SOURCE' for t in v.targets))
        child=n.child_bootstrap(ast.literal_eval(assignment.value));self.assertLessEqual(len(child.encode()),49*1024)
        smoke=n.child_bootstrap("print('fixed child bootstrap')")
        p=subprocess.run([sys.executable,'-c',smoke],capture_output=True,timeout=5);self.assertEqual(p.returncode,0);self.assertEqual(p.stdout.strip(),b'fixed child bootstrap')


class HybridBackend(n.Backend):
    """hostではhardware stateのみfixture。TRACEME/STOP/CONT/EXITKILLは実syscall。"""
    def __init__(self,values=(),refuse=False):super().__init__();self.hardware=ORIGINAL;self.states=[bytes(272)]+[gpr(i,v) for i,v in enumerate(values)];self.refuse=refuse
    def regset(self,pid,note,size):return self.states.pop(0) if note==1 else self.hardware
    def set_hardware(self,pid,data):
        if self.refuse:raise n.PtraceError(1)
        self.hardware=data


def completed_report():
    b,t=ready_trace();t.prepare();b.gpr=gpr(nonnull=False);t.trap();events={e['phase']:e for e in api_events()}
    return dict(status='diagnosed',loading_profile=n.LOADING_PROFILE,prior_initialize_failure_matches=True,api_observation='diagnosed',
        api=events['api']['result'],loaded_libraries=events['loaded_libraries']['result'],egl_cleanup=events['egl_cleanup']['result'],runtime_unchanged=True,
        child_blocked=None,internal=t.report(),child_pid=71,child_group=71,child_returncode=1,termination='natural',term_sent=False,term_forwarded=False,kill_sent=False,
        timed_out=False,cancel_reason=None,error=None,reaped=True,group_reclaimed=True,pipes_drained=True,cleanup_confirmed=True,output_bytes=dict(stdout=3000,stderr=0),
        egl_log=egl.InitLogSummary(True).summary(),schema=1,operator=n.OPERATOR,probe_id=NONCE,uid=10158,libraries=dict(egl.LIBRARIES),
        wayland='not_run',sdk='not_run',drawing='not_run',input='not_run',hmd='not_run')


class HostCliTests(unittest.TestCase):
    def run_host(self,td,source,hashes=None):
        class Device:
            def __init__(self,*_):pass
            def validate(self):return dict(model='Quest 3',api=34)
            def adb(self,*_):return 'package:com.termux uid:10158\n'
        original=subprocess.Popen
        def spawn(*_,**kwargs):return original([sys.executable,'-c',source],**kwargs)
        with patch.object(cli.argparse.ArgumentParser,'parse_args',return_value=argparse.Namespace(serial='fixture',artifact_root=Path(td))), \
             patch.object(cli,'QuestDevice',Device),patch.object(cli.secrets,'token_hex',return_value=NONCE),patch.object(cli,'remote_source',return_value='fixed source'), \
             patch.object(cli,'source_hashes',side_effect=hashes or [{'fixed':'a'}]*3),patch.object(cli.subprocess,'Popen',side_effect=spawn),contextlib.redirect_stdout(io.StringIO()):
            code=cli.main()
        return code,json.loads(next(Path(td).glob('*/result.json')).read_text())
    def test_remote_early_exit_and_broken_stdin_preserve_original_returncode(self):
        with tempfile.TemporaryDirectory(prefix='null-host-') as td:
            code,value=self.run_host(td,'import os;os.close(0);raise SystemExit(7)')
            self.assertEqual(code,1);self.assertEqual(value['status'],'failed');self.assertEqual(value['remote_returncode'],7)
    def test_host_source_change_after_finished_keeps_original_api_and_child_exit(self):
        report=completed_report();cli.validated_result(report,NONCE)
        source=f"import sys,json\nsys.stdin.readline()\nprint(json.dumps({dict(phase='finished',result=report)!r}),flush=True)\n"
        with tempfile.TemporaryDirectory(prefix='null-source-') as td:
            code,value=self.run_host(td,source,[{'fixed':'a'},{'fixed':'a'},{'fixed':'changed'}])
            self.assertEqual(code,1);self.assertEqual(value['reason'],'SOURCE_CHANGED_OR_OUTPUT_INVALID');self.assertEqual(value['remote_returncode'],0)
            self.assertEqual(value['remote_result']['api']['initialize']['error']['value'],12289);self.assertEqual(value['remote_result']['child_returncode'],1)


@unittest.skipUnless(sys.platform.startswith('linux'),'Linux own-child fixture')
class OwnChildTests(unittest.TestCase):
    def command(self,traps=1,after='',traced=True):
        source=f"import sys,os,json,signal,time,resource\nresource.setrlimit(resource.RLIMIT_CORE,(0,0))\nsys.path.insert(0,{str(SCRIPTS)!r})\nfrom ptrace_capability_probe import Ptrace\nx=json.loads(sys.stdin.readline());binding=dict(probe_id=x['probe_id'],pid=os.getpid(),uid=10158)\n"
        if traced:
            source+="Ptrace().call(0)\nprint(json.dumps(dict(phase='traceme',success=True,errno=0,**binding)),flush=True)\nos.kill(os.getpid(),signal.SIGSTOP)\nprint(json.dumps(dict(phase='initialize_checkpoint',**binding)),flush=True)\nos.kill(os.getpid(),signal.SIGSTOP)\n"
            source+='os.kill(os.getpid(),signal.SIGTRAP)\n'*traps
        else:source+="print(json.dumps(dict(phase='traceme',success=False,errno=1,**binding)),flush=True)\n"
        source+=f"events={api_events()!r}\nfor e in events:print(json.dumps(e),flush=True)\n"+after+"\nraise SystemExit(1)\n"
        return [sys.executable,'-c',source]
    def observe(self,command=None,backend=None,**kwargs):
        r,w=os.pipe()
        try:return n.observe(command or self.command(),dict(PATH=os.environ['PATH']),r,kwargs.pop('checker',lambda:None),NONCE,lambda *_:list(ADDRESSES),
            backend=backend or HybridBackend([False]),architecture='aarch64',timeout=kwargs.pop('timeout',2),grace=.2,**kwargs)
        finally:os.close(r);os.close(w)
    def test_real_child_tracing_preserves_negative_egl_api_and_original_exit_one(self):
        value=self.observe();self.assertEqual(value['status'],'diagnosed');self.assertEqual(value['child_returncode'],1)
        self.assertEqual(value['api']['initialize']['error']['value'],12289);self.assertEqual(value['egl_cleanup']['error']['value'],0x3008)
        self.assertEqual(value['internal']['stages'],dict(screen_allocation=False,pipe_screen=None,dri_configs=None));self.assertTrue(value['cleanup_confirmed']);self.assertFalse(value['term_sent'])
        enriched=dict(value,schema=1,operator=n.OPERATOR,probe_id=NONCE,uid=10158,libraries=dict(egl.LIBRARIES),wayland='not_run',sdk='not_run',drawing='not_run',input='not_run',hmd='not_run')
        cli.validated_result(enriched,NONCE)
        for mutation in (lambda v:v['internal'].update(address=3),lambda v:v['egl_log'].update(raw='PRIVATE'),lambda v:v.update(error='PRIVATE'),lambda v:v['api'].update(registers='PRIVATE')):
            changed=copy.deepcopy(enriched);mutation(changed)
            with self.assertRaises(InstallError):cli.validated_result(changed,NONCE)
    def test_refused_set_and_traceme_keep_api_failure_code_and_cleanup(self):
        for command,b in ((self.command(0),HybridBackend(refuse=True)),(self.command(0,traced=False),HybridBackend())):
            value=self.observe(command,b);self.assertEqual(value['status'],'unsupported');self.assertEqual(value['api_observation'],'diagnosed')
            self.assertEqual(value['api']['initialize']['error']['value'],12289);self.assertEqual(value['child_returncode'],1);self.assertTrue(value['cleanup_confirmed'])
            self.assertIn(value['internal']['reason'],{'SETREGSET_UNSUPPORTED','PTRACE_DENIED'})
    def test_deadline_cancel_and_original_signal_do_not_touch_unrelated_child(self):
        unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
        try:
            value=self.observe(self.command(after='time.sleep(30)'),timeout=.3)
            self.assertEqual(value['cancel_reason'],'TIMEOUT');self.assertTrue(value['term_forwarded']);self.assertEqual(value['child_returncode'],-signal.SIGTERM)
            self.assertEqual(value['api']['initialize']['error']['value'],12289);self.assertTrue(value['cleanup_confirmed']);self.assertIsNone(unrelated.poll())
            value=self.observe(self.command(after='os.kill(os.getpid(),signal.SIGUSR1)'))
            self.assertEqual(value['child_returncode'],-signal.SIGUSR1);self.assertEqual(value['status'],'failed');self.assertTrue(value['cleanup_confirmed'])
            value=self.observe(self.command(after='os.kill(os.getpid(),signal.SIGTRAP)'))
            self.assertEqual(value['child_returncode'],-signal.SIGTRAP);self.assertEqual(value['api']['initialize']['error']['value'],12289);self.assertTrue(value['cleanup_confirmed'])
        finally:unrelated.terminate();unrelated.wait(timeout=2)
    def test_cancel_and_raw_stdout_are_bounded_and_keep_existing_api_failure(self):
        import threading,time
        with tempfile.TemporaryDirectory(prefix='null-cancel-') as td:
            ready=Path(td)/'ready';r,w=os.pipe();done=threading.Event()
            def cancel():
                until=time.monotonic()+2
                while not ready.exists() and time.monotonic()<until and not done.wait(.01):pass
                if not done.is_set():os.write(w,b'{"type":"cancel"}\n')
            thread=threading.Thread(target=cancel);thread.start()
            try:value=n.observe(self.command(after=f"open({str(ready)!r},'w').close();time.sleep(30)"),dict(PATH=os.environ['PATH']),r,lambda:None,NONCE,lambda *_:list(ADDRESSES),backend=HybridBackend([False]),architecture='aarch64',timeout=3,grace=.2)
            finally:done.set();thread.join(2);os.close(r);os.close(w)
            self.assertEqual(value['cancel_reason'],'HOST_CANCEL');self.assertTrue(value['term_forwarded'] and value['cleanup_confirmed']);self.assertEqual(value['child_returncode'],-signal.SIGTERM)
            self.assertEqual(value['api']['initialize']['error']['value'],12289)
        value=self.observe(self.command(after="print('PRIVATE /map/addr/register',flush=True);time.sleep(30)"))
        self.assertEqual(value['error'],'CHILD_OUTPUT_INVALID');self.assertTrue(value['cleanup_confirmed']);self.assertNotIn('PRIVATE',json.dumps(value))
    def test_eof_is_finite_and_early_termination_keeps_original_status(self):
        r,w=os.pipe();os.close(w)
        try:value=n.observe(self.command(after='time.sleep(30)'),dict(PATH=os.environ['PATH']),r,lambda:None,NONCE,lambda *_:ADDRESSES,backend=HybridBackend([False]),architecture='aarch64',timeout=2,grace=.2)
        finally:os.close(r)
        self.assertEqual(value['cancel_reason'],'CONTROL_EOF');self.assertTrue(value['cleanup_confirmed'])
        value=self.observe([sys.executable,'-c','raise SystemExit(7)'])
        self.assertEqual(value['child_returncode'],7);self.assertTrue(value['cleanup_confirmed']);self.assertEqual(value['status'],'failed')


if __name__=='__main__':unittest.main()
