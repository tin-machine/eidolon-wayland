#!/usr/bin/env python3
"""固定process fixtureのみ。外部PID/path/argvを受けず、子は最大4秒で自然終了する。"""
import importlib.util
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
import unittest
import importlib.machinery
import shlex

SOURCE = Path(__file__).resolve().parents[1] / 'termux/eidolon_gui_owner.py'
SPEC = importlib.util.spec_from_file_location('eidolon_linux_process_owner', SOURCE)
OWNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OWNER)
# Questと同じtask/children無しの経路をhostで必ず通す。外部PIDやCLIへは公開しない。
OWNER.descendant_snapshot = lambda owner, deadline=None: OWNER.proc_descendants(owner, min(deadline if deadline is not None else float('inf'),time.monotonic()+OWNER.SCAN_SECONDS))
CASES = {'plain', 'setsid', 'doublefork', 'pty', 'threadfork', 'pending', 'borrowed', 'existing', 'owner_reuse'}
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}


def child(mode):
    import threading
    def hold():
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        print('{"ready":true}', flush=True)
        time.sleep(4)
        os._exit(0)
    if mode in {'plain', 'pending', 'existing'}:
        hold()
    elif mode == 'setsid':
        pid = os.fork()
        if pid == 0: os.setsid(); hold()
        time.sleep(4)
    elif mode == 'doublefork':
        if os.fork() != 0: os._exit(0)
        os.setsid()
        if os.fork() != 0: os._exit(0)
        hold()
    elif mode == 'pty':
        import pty
        pid, master = pty.fork()
        if pid == 0:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
            time.sleep(4);os._exit(0)
        print('{"ready":true}', flush=True)
        time.sleep(4);os.close(master)
    elif mode == 'threadfork':
        def fork():
            if os.fork() == 0: os.setsid(); hold()
            time.sleep(4)
        worker = threading.Thread(target=fork);worker.start();worker.join()
    else: raise RuntimeError('FIXTURE_CASE_INVALID')


def fixed_child(mode):
    return [sys.executable, '-I', '-S', '-B', str(Path(__file__).resolve()), '--child', mode]


def ready(process):
    readable, _, _ = select.select([process.stdout], [], [], 2)
    if not readable or process.stdout.readline() != b'{"ready":true}\n':
        raise RuntimeError('FIXTURE_NOT_READY')


def worker(mode):
    owner = None
    process = None
    try:
        if mode == 'existing':
            process = subprocess.Popen(fixed_child('existing'), env=ENV, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, start_new_session=True)
            ready(process)
            try: OWNER.ProcessOwner()
            except OWNER.OwnershipError as error:
                if str(error) != 'HELPER_NOT_DEDICATED': raise
            else: raise RuntimeError('EXISTING_CHILD_ACCEPTED')
            process.kill();process.communicate(timeout=2)
            return dict(case=mode, existing_rejected=True)
        owner = OWNER.ProcessOwner()
        if mode == 'owner_reuse':
            original_scan = OWNER.descendant_snapshot
            def unknown(*_args, **_kwargs): raise OWNER.OwnershipError('PROCESS_SCAN_LIMIT')
            OWNER.descendant_snapshot = unknown
            try: pending = owner.finish(timeout=.03, grace=0)
            finally: OWNER.descendant_snapshot = original_scan
            if not pending['pending']: raise RuntimeError('PENDING_NOT_OBSERVED')
            try: OWNER.ProcessOwner()
            except OWNER.OwnershipError as error:
                if str(error) != 'OWNER_ALREADY_ACTIVE': raise
            else: raise RuntimeError('PENDING_OWNER_REPLACED')
            owner.finish()
            old = owner;owner = OWNER.ProcessOwner()
            try: old.finish()
            except OWNER.OwnershipError as error:
                if str(error) != 'OWNER_CHANGED': raise
            else: raise RuntimeError('OLD_OWNER_STILL_ACTIVE')
            process = owner.spawn(fixed_child('plain'), env=ENV);ready(process)
            result = owner.finish(grace=.05)
            return dict(case=mode, pending_owner_rejected=True, old_owner_rejected=True, result=result)
        if mode == 'borrowed':
            read, write = os.pipe()
            pid = os.fork()
            if pid == 0:
                os.close(read)
                try: owner.finish()
                except OWNER.OwnershipError as error:
                    if str(error) == 'OWNER_CHANGED': os.write(write, b'OK')
                os._exit(0)
            os.close(write)
            with os.fdopen(read, 'rb') as source: accepted = source.read(8) == b'OK'
            os.waitpid(pid, 0)
            if not accepted: raise RuntimeError('BORROWED_OWNER_ACCEPTED')
            return dict(case=mode, borrowed_rejected=True, result=owner.finish())
        process = owner.spawn(fixed_child(mode), env=ENV)
        ready(process)
        # readiness出力と親exit/adoptionの間の有限な移動を許容する。
        deadline = time.monotonic() + 1
        while True:
            try: observed = OWNER.descendant_snapshot(owner.owner);break
            except (FileNotFoundError, ProcessLookupError, OWNER.OwnershipError):
                if time.monotonic() >= deadline: raise
                time.sleep(.01)
        different_group = any(row['group'] != process.pid for row in observed.values())
        different_session = any(row['session'] != process.pid for row in observed.values())
        retry = None
        if mode == 'pending':
            actual = OWNER.descendant_snapshot
            calls = [0]
            def limited(*args, **kwargs):
                calls[0] += 1
                if calls[0] == 1: return actual(*args, **kwargs)
                raise OWNER.OwnershipError('PROCESS_SCAN_LIMIT')
            OWNER.descendant_snapshot = limited
            try: retry = owner.finish(timeout=.08, grace=.06)
            finally: OWNER.descendant_snapshot = actual
            if not retry['pending'] or retry['open_pidfds'] == 0:
                raise RuntimeError('PENDING_OWNER_NOT_RETAINED')
            try: owner.spawn(fixed_child('plain'), env=ENV)
            except OWNER.OwnershipError as error:
                if str(error) != 'OWNER_CLOSED': raise
            else: raise RuntimeError('PENDING_OWNER_REUSED')
        result = owner.finish(grace=.05)
        if not result['complete']: raise RuntimeError('CLEANUP_INCOMPLETE')
        duplicate = owner.finish()
        empty = OWNER.descendant_snapshot(owner.owner) == {}
        process.communicate(timeout=1)
        return dict(case=mode, observed=len(observed), different_group=different_group,
                    different_session=different_session, result=result, retry=retry,
                    duplicate_same=duplicate == result, descendants_empty=empty,
                    root_reaped=process.returncode is not None,
                    pipes_closed=process.stdout.closed and process.stderr.closed)
    finally:
        if owner is not None:
            # 失敗fixtureもこのlive ownerだけで回収。外部PIDや保存PIDをsignalしない。
            owner.finish(grace=0)
        if process is not None:
            try: process.communicate(timeout=5)
            except subprocess.TimeoutExpired: process.kill();process.communicate(timeout=1)


class ProcessOwnerTest(unittest.TestCase):
    def run_case(self, case):
        run = subprocess.run([sys.executable, '-I', '-S', '-B', str(Path(__file__).resolve()), '--worker', case],
                             env=ENV, capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr.decode(errors='replace'))
        self.assertLessEqual(len(run.stdout), 4096)
        return json.loads(run.stdout)

    def test_same_group_cleanup_reaps_and_closes_pidfds(self):
        value = self.run_case('plain')
        self.assertTrue(value['result']['complete']);self.assertFalse(value['result']['pending'])
        self.assertGreater(value['result']['kill_signals'], 0)
        self.assertEqual(value['result']['open_pidfds'], 0)
        self.assertTrue(value['duplicate_same']);self.assertTrue(value['root_reaped'])
        self.assertTrue(value['descendants_empty']);self.assertTrue(value['pipes_closed'])

    def test_setsid_and_pty_shell_are_owned_across_process_groups(self):
        for case in ('setsid', 'pty'):
            with self.subTest(case=case):
                value = self.run_case(case)
                self.assertTrue(value['different_group']);self.assertTrue(value['different_session'])
                self.assertTrue(value['result']['complete']);self.assertTrue(value['descendants_empty'])

    def test_double_fork_is_adopted_and_reaped_without_signalling_a_saved_pid(self):
        value = self.run_case('doublefork')
        self.assertTrue(value['different_group']);self.assertTrue(value['different_session'])
        self.assertTrue(value['result']['complete']);self.assertGreaterEqual(value['result']['reaped'], 2)

    def test_children_created_by_a_nonleader_thread_are_included(self):
        value = self.run_case('threadfork')
        self.assertGreaterEqual(value['observed'], 2)
        self.assertTrue(value['different_session']);self.assertTrue(value['descendants_empty'])

    def test_missing_scan_proof_retains_live_owner_then_explicit_retry_reaps_it(self):
        value = self.run_case('pending')
        self.assertFalse(value['retry']['complete']);self.assertTrue(value['retry']['pending'])
        self.assertEqual(value['retry']['reason'], 'PROCESS_SCAN_LIMIT')
        self.assertGreater(value['retry']['open_pidfds'], 0)
        self.assertTrue(value['result']['complete']);self.assertEqual(value['result']['open_pidfds'], 0)

    def test_existing_children_and_forked_copy_cannot_claim_the_owner(self):
        self.assertTrue(self.run_case('existing')['existing_rejected'])
        value = self.run_case('borrowed')
        self.assertTrue(value['borrowed_rejected']);self.assertTrue(value['result']['complete'])

    def test_pending_owner_cannot_be_replaced_and_old_callback_cannot_touch_new_owner(self):
        value = self.run_case('owner_reuse')
        self.assertTrue(value['pending_owner_rejected']);self.assertTrue(value['old_owner_rejected'])
        self.assertTrue(value['result']['complete'])

    def test_unrelated_same_uid_sibling_survives_fixture_cleanup(self):
        sibling = subprocess.Popen(fixed_child('plain'), env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            ready(sibling)
            self.assertTrue(self.run_case('plain')['result']['complete'])
            self.assertIsNone(sibling.poll())
        finally:
            sibling.kill();sibling.communicate(timeout=2)

    def test_identity_never_accepts_a_reused_pid_or_changed_uid(self):
        row = dict(pid=11, start=22, uid=(1000,) * 4, parent=1, group=11, session=11)
        self.assertTrue(OWNER.same_process(row, dict(row, group=33, session=33)))
        for change in (dict(pid=12), dict(start=23), dict(uid=(1001,) * 4)):
            self.assertFalse(OWNER.same_process(row, dict(row, **change)))


class CapabilityOperatorTests(unittest.TestCase):
    def cli(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        loader=importlib.machinery.SourceFileLoader('termux_gui_owner_cli',str(Path(__file__).resolve().parents[1]/'quest-probe-termux-gui-owner'))
        spec=importlib.util.spec_from_loader(loader.name,loader);module=importlib.util.module_from_spec(spec);loader.exec_module(module);return module
    def test_fixed_capability_bootstrap_runs_own_doublefork_and_reaps_every_descendant(self):
        cli=self.cli();source=cli.remote_source();self.assertLessEqual(len(shlex.join(['run-as','com.termux','python3','-c',source]).encode()),49*1024)
        # CLIはUID差替えを持たない。hostの固定fixture内だけ実UIDへ置換する。
        program="import sys,types\nm=types.ModuleType('eidolon_gui_owner');sys.modules[m.__name__]=m\n"+f"exec({SOURCE.read_text()!r},m.__dict__)\n"+cli.WORKER.replace('if os.getuid()!=10158:','if os.getuid()!='+str(os.getuid())+':')
        run=subprocess.run([sys.executable,'-I','-S','-B','-c',program],capture_output=True,timeout=12)
        self.assertEqual(run.returncode,0,run.stderr.decode());value=json.loads(run.stdout)
        self.assertEqual(value['status'],'supported');self.assertTrue(value['adopted'] and value['separate_session'] and value['pipes_closed'])
        self.assertTrue(value['cleanup']['complete']);self.assertEqual(value['cleanup']['open_pidfds'],0)
        value['uid']=10158;self.assertTrue(cli.valid(value));value['body']='PRIVATE';self.assertFalse(cli.valid(value))


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] in {'--worker', '--child'} and sys.argv[2] in CASES:
        if sys.argv[1] == '--child': child(sys.argv[2])
        else: print(json.dumps(worker(sys.argv[2]), separators=(',', ':')), flush=True)
    else: unittest.main()
