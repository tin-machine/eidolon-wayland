"""観測identity/欠測/共有tmuxを検査する。観測processを終了権限にしない。"""
import importlib.util
import os
import json
import pty
import socket
import subprocess
import sys
import time
import threading
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('workload_launcher', Path(__file__).resolve().parents[1] / 'eidolon-session.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def stat_line(pid=123, *, comm='bash', start=100, parent=1, ticks=10, rss=8, state='S'):
    fields = [state] + ['0'] * 49
    fields[1:5] = [str(parent), str(pid), str(pid), '34816']
    fields[11:15] = [str(ticks), '2', '999999', '999999']
    fields[19], fields[21] = str(start), str(rss)
    return f'{pid} ({comm}) ' + ' '.join(fields) + '\n'


def row(pid=123, **kwargs):
    return launcher.proc_stat(stat_line(pid, **kwargs), pid)


class WorkloadTests(unittest.TestCase):
    def test_versioned_launch_keeps_receipt_schema_and_cleans_observer_socket(self):
        with tempfile.TemporaryDirectory(prefix='eidolon-v1-') as name:
            home,nonce=Path(name),'d'*16
            read,write=os.pipe();events=[]
            def send(kind,**fields):
                os.write(write,(json.dumps(dict(type=kind,nonce=nonce,**fields))+'\n').encode())
            def emit(value):
                events.append(value)
                if value['type']=='prepared':send('inspect_tools',mode='foot')
                elif value['type']=='tools':send('start_terminal_v1',schema=1,session='17',terminal_id='1',mode='foot')
                elif value['type']=='terminal_opened':
                    self.assertEqual(set(value),{'type','schema','nonce','uid','request_id','success','ordinal'})
                    self.assertTrue(value['success'])
                    send('observe_workload_v1',schema=1,session='17',enabled=value['ordinal']!=1)
                    if value['ordinal']==1:send('open_terminal_v1',schema=1,session='17',terminal_id='2',request_id='100')
                    else:send('stop')
            def factory(mode,nonce):
                return [sys.executable,'-c',f"import socket,time;s=socket.socket(socket.AF_UNIX);s.bind({str(home/'.eidolon'/('s-'+nonce)/'f')!r});time.sleep(30)"]
            try:
                with patch.object(launcher,'inspect_tools',return_value={'mode':'foot'}), \
                     patch.object(launcher,'terminal_command',return_value=[sys.executable,'-c','pass']):
                    result=launcher.supervise(home,nonce,read,emit,lease_seconds=3,grace_seconds=.1,factory=factory)
                self.assertTrue(result['cleaned'],result)
                self.assertEqual(result['reason'],'Stop')
                self.assertEqual([v['ordinal'] for v in events if v['type']=='terminal_opened'],[1,2])
                self.assertFalse(any(t.name=='eidolon-observer' for t in threading.enumerate()))
                self.assertTrue(any(v['type']=='workload_v1' and v['session']=='17' for v in events))
            finally:
                os.close(read);os.close(write)

    def test_disabled_observer_stops_scans_and_resumes_with_fresh_baseline(self):
        with tempfile.TemporaryDirectory(prefix='eidolon-observer-') as name:
            def sample(observer, predicate):
                deadline=time.monotonic()+4
                while time.monotonic()<deadline:
                    value=observer.take()
                    if value and predicate(value):return value
                    time.sleep(.01)
                self.fail('observer sample timeout')
            with patch.object(launcher,'scan_processes',return_value=({},False,0.001)) as scan:
                observer=launcher.WorkloadObserver(Path(name),'e'*16,'1')
                try:
                    first=sample(observer,lambda v:v['capability']=='available')
                    observer.set_enabled(False)
                    off=sample(observer,lambda v:v['capability']=='unavailable')
                    count=scan.call_count
                    again=sample(observer,lambda v:v['capability']=='unavailable')
                    self.assertEqual(scan.call_count,count)
                    self.assertEqual(again['scan_seconds'],0)
                    self.assertEqual(first['epoch'],off['epoch'])
                    observer.set_enabled(True)
                    resumed=sample(observer,lambda v:v['capability']=='available')
                    self.assertEqual(resumed['epoch'],off['epoch'])
                    self.assertIsNone(resumed['interval_seconds'])
                    self.assertGreater(int(resumed['sequence']),int(off['sequence']))
                    self.assertGreater(scan.call_count,count)
                finally:observer.close()
                self.assertFalse(observer.thread.is_alive())

    def test_nonblocking_output_coalesces_metrics_and_preserves_control_json(self):
        read, write = os.pipe()
        output = launcher.JsonOutput(write)
        os.set_blocking(read, False)
        received = bytearray()
        try:
            for index in range(200):
                output(dict(type='workload_v1', sequence=index, payload='x'*10000))
            output(dict(type='finished', cleaned=True))
            self.assertIsNotNone(output.metric)
            self.assertLessEqual(len(output.current) + len(output.metric), 32768)
            deadline=time.monotonic()+2
            while output.current or output.control or output.metric:
                try: received.extend(os.read(read,65536))
                except BlockingIOError: pass
                output.flush()
                self.assertLess(time.monotonic(),deadline)
            while True:
                try: received.extend(os.read(read,65536))
                except BlockingIOError: break
            rows=[json.loads(line) for line in received.splitlines()]
            self.assertIn(dict(type='finished',cleaned=True),rows)
            metrics=[r for r in rows if r['type']=='workload_v1']
            self.assertLess(len(metrics),200)
            self.assertEqual(metrics[-1]['sequence'],199)
            self.assertTrue(os.get_blocking(write) is False)
            output.close()
            self.assertTrue(os.get_blocking(write))
        finally:
            os.close(read);os.close(write)

    def test_bounded_query_reaps_only_its_child_on_flood_and_timeout(self):
        unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
        try:
            self.assertEqual(launcher.bounded_observation_command([sys.executable,'-c',"print('fixed format')"]),'fixed format\n')
            for source in ['import os\nwhile True:os.write(1,b"x"*4096)', 'import time;time.sleep(30)']:
                with self.assertRaises(ValueError):
                    launcher.bounded_observation_command([sys.executable,'-c',source],seconds=.05)
                self.assertIsNone(unrelated.poll())
        finally:
            unrelated.terminate();unrelated.wait(timeout=2)

    def test_observer_joins_real_pty_credentials_and_never_kills_registered_process(self):
        with tempfile.TemporaryDirectory(prefix='eidolon-observe-') as name:
            home,nonce=Path(name),'c'*16
            directory=Path(launcher.prepare(home,nonce)['socket']).parent
            observer=launcher.WorkloadObserver(directory,nonce,'1')
            master,slave=pty.openpty()
            child=None
            try:
                observer.allow('7')
                with self.assertRaisesRegex(ValueError,'REUSED'):observer.allow('7')
                with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as peer:
                    for malformed in [b'[]',b'null',b'{',b'x'*3000]:
                        peer.sendto(malformed,str(directory/'m'))
                code=f'''import os,fcntl,termios,importlib.util,time
os.setsid();fcntl.ioctl(0,termios.TIOCSCTTY,0)
spec=importlib.util.spec_from_file_location('session',{str(Path(launcher.__file__).resolve())!r})
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
d,v=m.terminal_registration(m.Path({str(home)!r}),{nonce!r},'7');m.send_registration(d,v)
time.sleep(30)
'''
                child=subprocess.Popen([sys.executable,'-B','-c',code],stdin=slave,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
                deadline=time.monotonic()+4
                found=None
                while time.monotonic()<deadline:
                    value=observer.take()
                    if value and value['registrations']:
                        found=value;break
                    self.assertIsNone(child.poll())
                    time.sleep(.02)
                self.assertIsNotNone(found)
                self.assertEqual(found['registrations'][0]['terminal_id'],'7')
                self.assertEqual(found['registrations'][0]['pid'],child.pid)
                self.assertEqual(found['workloads'][0]['scope'],'shell_tree')
                observer.close()
                self.assertFalse(observer.thread.is_alive())
                self.assertIsNone(child.poll())
            finally:
                if observer.thread.is_alive():observer.close()
                if child is not None:
                    child.terminate();child.wait(timeout=2);child.stderr.close()
                os.close(master);os.close(slave)
            self.assertTrue(launcher.cleanup(home,nonce,creator=True)['cleaned'])

    def test_stat_comm_is_not_a_command_identity_or_simple_split(self):
        for comm in ['bash', 'linker64', 'x ) ( y', ')\n(x']:
            value = launcher.proc_stat(stat_line(comm=comm), 123)
            self.assertEqual(value['ticks'], 12)
            self.assertEqual(value['start'], '100')
            self.assertEqual(value['rss_pages'], 8)
            self.assertNotIn('comm', value)
        for text in [stat_line(pid=456), '123 (bad) S 1', stat_line(start=0), stat_line(rss=-1), 'x' * 8193]:
            with self.assertRaises(ValueError):
                launcher.proc_stat(text, 123)

    def test_proc_directory_and_stat_symlinks_are_not_followed(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / '123').mkdir()
            (root / '123/stat').write_text(stat_line())
            self.assertEqual(launcher.observe_process(123, root)['pid'], 123)
            (root / '124').symlink_to(root / '123')
            with self.assertRaises(OSError):
                launcher.observe_process(124, root)
            (root / '123/stat').unlink()
            (root / 'payload').write_text(stat_line())
            (root / '123/stat').symlink_to(root / 'payload')
            with self.assertRaises(OSError):
                launcher.observe_process(123, root)
            # 同UIDだけを読み取る。
            with patch.object(launcher.os, 'getuid', return_value=os.getuid() + 1):
                with self.assertRaises(PermissionError):
                    launcher.observe_process(123, root)

    def test_real_self_stat_and_bounded_same_uid_scan(self):
        current = launcher.observe_process(os.getpid())
        self.assertGreater(int(current['start']), 0)
        self.assertRegex(launcher.observation_boot(), r'^[a-f0-9-]{36}$')
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for pid in range(123, 127):
                (root / str(pid)).mkdir()
                (root / str(pid) / 'stat').write_text(stat_line(pid))
            with patch.object(launcher, 'WORKLOAD_PROCESSES', 2):
                values, partial, _ = launcher.scan_processes(root)
                self.assertEqual(len(values), 2)
                self.assertTrue(partial)
            values, partial, _ = launcher.scan_processes(root, stopped=lambda: True)
            self.assertEqual(values, {})
            self.assertTrue(partial)

    def test_distinct_roots_do_not_include_shared_terminal_server(self):
        rows = {1: row(1, parent=0, start=1), 10: row(10, start=10), 20: row(20, start=20),
                11: row(11, parent=10, start=11), 21: row(21, parent=20, start=21)}
        first, missing = launcher.process_tree(rows, [rows[10]])
        self.assertFalse(missing)
        self.assertEqual(set(first), {10, 11})
        second, _ = launcher.process_tree(rows, [rows[20]])
        self.assertEqual(set(second), {20, 21})
        rows[10] = row(10, start=99)
        found, missing = launcher.process_tree(rows, [row(10, start=10)])
        self.assertEqual(found, {})
        self.assertTrue(missing)
        # 観測rootのpid再利用後に古い子を新rootへ結び直さない。
        found, missing = launcher.process_tree(rows, [rows[10]])
        self.assertEqual(set(found), {10})
        self.assertFalse(missing)

    def test_cpu_uses_own_ticks_and_collector_monotonic_delta(self):
        counter = launcher.WorkloadDelta(100, 4096)
        rows = {10: row(10, ticks=100), 20: row(20, ticks=200)}
        values, dt = counter.update('boot', 'epoch', 10., rows)
        self.assertIsNone(dt)
        self.assertIsNone(counter.total(rows, values)['cpu_cores'])
        rows = {10: row(10, ticks=150), 20: row(20, ticks=300)}
        values, dt = counter.update('boot', 'epoch', 10.5, rows)
        self.assertEqual(dt, .5)
        total = counter.total(rows, values)
        self.assertEqual(total['cpu_cores'], 3.)
        self.assertEqual(total['rss_bytes'], 16 * 4096)
        self.assertEqual(total['coverage'], 'complete')

    def test_missing_pid_reuse_epoch_counter_reverse_and_gap_need_fresh_baseline(self):
        mutations = [
            ('boot', 'epoch', 2., {10: row(10, start=101, ticks=500)}),
            ('boot', 'epoch', 2., {10: row(10, ticks=1)}),
            ('newboot', 'epoch', 2., {10: row(10, ticks=500)}),
            ('boot', 'newepoch', 2., {10: row(10, ticks=500)}),
            ('boot', 'epoch', 5., {10: row(10, ticks=500)}),
            ('boot', 'epoch', .5, {10: row(10, ticks=500)}),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation[:3]):
                counter = launcher.WorkloadDelta(100, 4096)
                counter.update('boot', 'epoch', 1., {10: row(10, ticks=100)})
                values, _ = counter.update(*mutation)
                self.assertIsNone(counter.total(mutation[3], values)['cpu_cores'])
        counter = launcher.WorkloadDelta(100, 4096)
        counter.update('boot', 'epoch', 1., {10: row(10)})
        counter.update('boot', 'epoch', 2., {})
        rows = {10: row(10, ticks=500)}
        values, _ = counter.update('boot', 'epoch', 3., rows)
        self.assertIsNone(counter.total(rows, values)['cpu_cores'])
        values, _ = counter.update('boot', 'epoch', 4., {10: row(10, ticks=600)})
        self.assertIsNone(counter.total(rows, values, partial=True)['cpu_cores'])
        self.assertEqual(counter.total({}, values, missing=True)['reason'], 'root_missing')

    def test_tmux_shared_sessions_reuse_same_workload_roots_without_pid_doubling(self):
        rows = {10: row(10), 11: row(11, parent=10), 20: row(20), 21: row(21, parent=20),
                30: row(30), 31: row(31, parent=30), 32: row(32, parent=30)}
        clients = '30\t11\t/dev/pts/1\t$1\n30\t21\t/dev/pts/2\t$1\n'
        panes = '30\t$1\t@1\t%1\t31\t/dev/pts/3\n30\t$1\t@2\t%2\t32\t/dev/pts/4\n'
        snapshot = launcher.parse_tmux_snapshot(clients, panes, rows)
        scopes = []
        for pid, tty in [(10, '/dev/pts/1'), (20, '/dev/pts/2')]:
            tree, _ = launcher.process_tree(rows, [rows[pid]])
            scopes.append(launcher.tmux_scope(snapshot, tty, tree))
        self.assertEqual(scopes[0], scopes[1])
        found, _ = launcher.process_tree(rows, scopes[0]['roots'] * 2)
        self.assertEqual(set(found), {31, 32})
        self.assertIsNone(launcher.tmux_scope(snapshot, '/dev/pts/9', {}))
        with self.assertRaisesRegex(ValueError, 'UNRELATED'):
            launcher.tmux_scope(snapshot, '/dev/pts/1', {})

    def test_tmux_ambiguous_changed_server_malformed_and_oversized_are_rejected(self):
        rows = {11: row(11), 30: row(30), 31: row(31)}
        good = '30\t11\t/dev/pts/1\t$1\n'
        for clients, panes in [(good + good, ''), (good, '31\t$1\t@1\t%1\t11\t/dev/pts/2\n'),
                               ('30\t11\t/tmp/whatever\t$1\n', ''), (good, 'not a fixed format'),
                               ('x' * 32769, ''), ('30\t999\t/dev/pts/1\t$1\n', '')]:
            with self.subTest(clients=clients[:50], panes=panes):
                with self.assertRaises(ValueError):
                    launcher.parse_tmux_snapshot(clients, panes, rows)


if __name__ == '__main__':
    unittest.main()
