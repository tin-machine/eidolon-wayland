import hashlib
import importlib.util
import json
import os
from pathlib import Path
import selectors
import socket
import subprocess
import tempfile
import time
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / 'linux/eidolon-linux-session.py'
spec = importlib.util.spec_from_file_location('linux_helper', SOURCE)
helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)


class LinuxHelperTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='elh-')
        self.home = Path(self.temporary.name)
        self.nonce = '1' * 16
    def tearDown(self): self.temporary.cleanup()
    def bundle(self):
        directory = self.home / '.local/lib/eidolon-linux'; directory.mkdir(parents=True, mode=0o700)
        for parent in (self.home / '.local', self.home / '.local/lib'): parent.chmod(0o700)
        for name in helper.FILES:
            (directory / name).write_bytes(SOURCE.read_bytes() if name.endswith('.py') else b'fixture')
            (directory / name).chmod(0o600 if name.endswith('.py') else 0o700)
        manifest = dict(schema=1, backend='linux_posix', revision=helper.REVISION,
                        files={name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in helper.FILES})
        (directory/'bundle.json').write_text(json.dumps(manifest)); (directory/'bundle.json').chmod(0o600)
        return directory
    def test_bundle_pins_all_files_and_does_not_accept_links_or_writable_files(self):
        directory = self.bundle()
        self.assertEqual(set(helper.bundle(self.home)[1]), set(helper.FILES))
        (directory/'waypipe').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'BUNDLE_HASH_MISMATCH'): helper.bundle(self.home)
        (directory/'waypipe').unlink(); (directory/'waypipe').symlink_to('/usr/bin/true')
        with self.assertRaises(OSError): helper.bundle(self.home)
        (directory/'waypipe').unlink(); (directory/'waypipe').write_bytes(b'fixture'); (directory/'waypipe').chmod(0o777)
        with self.assertRaisesRegex(ValueError,'FILE_INVALID'): helper.bundle(self.home)
    def test_system_python_and_home_ignore_ambient_environment(self):
        self.assertEqual(str(helper.system_python()), '/usr/bin/python3')
        with mock.patch.dict(os.environ, HOME='/does/not/exist', LD_PRELOAD='hostile', PYTHONPATH='hostile', TERMUX_PREFIX='hostile'):
            self.assertNotEqual(str(helper.account_home()), '/does/not/exist')
            env = helper.environment(self.home,self.home/'fixture')
            self.assertEqual(set(env), {'HOME','PATH','LANG','LC_ALL','XDG_RUNTIME_DIR','TMPDIR','SHELL'})
            self.assertEqual(env['PATH'], '/usr/bin:/bin')
    def test_nonce_socket_bound_and_owned_record_mismatch(self):
        for nonce in ('../bad','a'*17,1):
            with self.assertRaises(ValueError):helper.paths(self.home,nonce)
        with self.assertRaisesRegex(ValueError,'SOCKET_PATH_LONG'):helper.paths(Path('/'+'a'*100),self.nonce)
        directory=helper.prepare(self.home,self.nonce)
        helper.write_record(directory/'lease.json',{**helper.marker(self.nonce),'owner':helper.identity(os.getpid()),'child':None})
        self.assertFalse(helper.cleanup(self.home,self.nonce)['cleaned'])
        owner=json.loads((directory/'owner.json').read_text()); owner['uid']+=1; helper.write_record(directory/'owner.json',owner)
        with self.assertRaisesRegex(ValueError,'OWNER_MISMATCH'):helper.cleanup(self.home,self.nonce)
    def test_stale_start_uid_group_never_matches_live_pid(self):
        identity=helper.identity(os.getpid())
        self.assertTrue(helper.same_process(identity))
        for field,value in [('start','0'),('uid',os.getuid()+1),('group',identity['group']+1)]:
            self.assertFalse(helper.same_process({**identity,field:value}))
    def test_child_command_is_fixed_and_requires_owned_streamlocal_socket(self):
        directory=self.bundle(); state=helper.prepare(self.home,self.nonce)
        with self.assertRaises(FileNotFoundError):helper.child_command(self.home,state)
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(str(state/'p'))
            command=helper.child_command(self.home,state)
            self.assertEqual(command[-2:],[str(directory/'eidolon-shm-fixture'),'--pattern'])
            self.assertNotIn('foot',command);self.assertIn('--no-gpu',command)
            (state/'w').write_text('unrelated')
            with self.assertRaisesRegex(ValueError,'DISPLAY_REUSED'):helper.child_command(self.home,state)
    def run_process(self, controls, child_source=None, close=False, lease=.2, maximum=2, wait_ready=False):
        # 固定fixtureのみ。production CLIにはhome/factory/sourceの入口を追加しない。
        wrapper = '''import importlib.util,sys,json,os
from pathlib import Path
s=importlib.util.spec_from_file_location('helper',sys.argv[1]);h=importlib.util.module_from_spec(s);s.loader.exec_module(h)
home=Path(sys.argv[2]);child=bytes.fromhex(sys.argv[3]).decode()
r=h.serve(home,'1111111111111111',0,lambda v:print(json.dumps(v),flush=True),lease_seconds=float(sys.argv[4]),maximum_seconds=float(sys.argv[5]),inspect=lambda *_:{'synthetic_tools':True},factory=lambda *_:['/usr/bin/python3','-c',child])
'''
        process=subprocess.Popen(['/usr/bin/python3','-c',wrapper,str(SOURCE),str(self.home),(child_source or 'pass').encode().hex(),str(lease),str(maximum)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        try:
            prepared=json.loads(process.stdout.readline());self.assertEqual(prepared['type'],'prepared')
            for command in controls:
                if command.get('type') == 'stop' and wait_ready:
                    ready = self.home/'.eidolon-linux'/('s-'+self.nonce)/'ready'
                    deadline=time.monotonic()+2
                    while not ready.exists():
                        self.assertLess(time.monotonic(),deadline);time.sleep(.005)
                    self.assertTrue(helper.same_process(helper.identity(int(ready.read_text()))));ready.unlink()
                process.stdin.write((json.dumps({'schema':1,'nonce':self.nonce,**command})+'\n').encode());process.stdin.flush()
            if close:process.stdin.close();process.stdin=None
            process.wait(timeout=8)
            out,err=process.stdout.read(),process.stderr.read()
            self.assertEqual(process.returncode,0,err.decode())
            values=[prepared]+[json.loads(line) for line in out.splitlines()]
            result=values[-1];self.assertEqual(result['type'],'finished');return result,values
        finally:
            if process.poll() is None: os.killpg(process.pid,9);process.wait(timeout=2)
            for pipe in (process.stdin,process.stdout,process.stderr):
                if pipe is not None:pipe.close()
    def test_prepare_eof_and_lease_cleanup_without_spawn(self):
        result,_=self.run_process([],close=True)
        self.assertEqual(result['reason'],'EOF');self.assertTrue(result['cleaned']);self.assertTrue(result['pipes_closed'])
        with self.assertRaisesRegex(ValueError,'NONCE_REUSED'):helper.prepare(self.home,self.nonce)
    def test_lease_expiration_cleans_private_state(self):
        result,_=self.run_process([])
        self.assertEqual(result['reason'],'LeaseExpired');self.assertTrue(result['cleaned'])
    def test_maximum_duration_is_independent_of_a_long_lease(self):
        result,_=self.run_process([],lease=5,maximum=.1)
        self.assertEqual(result['reason'],'MaximumDuration');self.assertTrue(result['cleaned'])
    def test_owned_child_fork_and_term_ignore_is_killed_without_touching_unrelated(self):
        unrelated=subprocess.Popen(['/usr/bin/python3','-c','import time;time.sleep(10)'],start_new_session=True)
        try:
            child="import os,signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);pid=os.fork();(Path(os.environ['XDG_RUNTIME_DIR'])/'ready').write_text(str(pid)) if pid else None;time.sleep(10)"
            result,values=self.run_process([{'type':'inspect_tools'},{'type':'start','mode':'pattern'},{'type':'stop'}],child,lease=4,wait_ready=True)
            self.assertTrue(any(v['type']=='child_started' for v in values));self.assertTrue(result['child_reaped']);self.assertFalse(result['group_alive']);self.assertTrue(result['cleaned'])
            self.assertIsNone(unrelated.poll());self.assertFalse(result['pattern_verified'])
        finally:os.killpg(unrelated.pid,9);unrelated.wait(timeout=2)
    def test_catalog_foot_arbitrary_argv_and_duplicate_keys_are_rejected(self):
        for command in ({'type':'catalog'},{'type':'start','mode':'foot'},{'type':'start','mode':'pattern','argv':['/bin/sh']}):
            with self.subTest(command=command):
                result,_=self.run_process([command]);self.assertEqual(result['reason'],'ControlInvalid');self.assertTrue(result['cleaned'])
                # Each process fixture owns and removes its temporary receipt for the next subcase only.
                (self.home/'.eidolon-linux'/('r-'+self.nonce+'.json')).unlink()
        with self.assertRaisesRegex(ValueError,'JSON_INVALID'):helper.decode(b'{"schema":1,"schema":1}')


if __name__=='__main__':unittest.main()
