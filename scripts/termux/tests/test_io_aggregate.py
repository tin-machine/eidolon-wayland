"""NX13: waitの二重計上・scope変化・CPUとの独立性を固定fixtureで検査する。"""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from test_workload import launcher as m, row, stat_line


def io_text(read=0, write=0, chars=0, cancelled=0):
    return f'rchar: {chars}\nwchar: {chars}\nsyscr: 1\nsyscw: 1\nread_bytes: {read}\nwrite_bytes: {write}\ncancelled_write_bytes: {cancelled}\n'.encode()


class AggregateTests(unittest.TestCase):
    def setUp(self):
        self.aggregate = m.IoAggregate()
        self.rows = {1: row(1, start=10, parent=0), 2: row(2, start=20, parent=1)}
        self.roots = [self.rows[1]]
        self.fingerprint = m.io_tree(self.rows, self.roots)[0][0]

    def update(self, at, counters, *, rows=None, roots=None, identity=('boot', 'epoch'), reason=None):
        fingerprint = m.io_tree(rows or self.rows, roots or self.roots)[0][0]
        return self.aggregate.update('scope', identity, at, fingerprint, counters, reason)

    def test_live_child_then_wait_does_not_add_retired_twice(self):
        self.assertEqual(self.update(0, {1:(0,0), 2:(0,0)})['state'], 'baseline')
        self.assertEqual(self.update(1, {1:(0,0), 2:(0,1048576)})['write_bytes_per_second'], 1048576)
        parent = {1:self.rows[1]}
        waited = self.update(2, {1:(0,1048576)}, rows=parent)
        self.assertEqual(waited['reason'], 'scope_changed')
        self.assertIsNone(waited['write_bytes_per_second'])
        self.assertEqual(self.update(3, {1:(0,1048576)}, rows=parent)['state'], 'baseline')
        self.assertEqual(self.update(4, {1:(0,1048576)}, rows=parent)['write_bytes_per_second'], 0)

    def test_child_born_and_waited_between_samples_is_counted_once_in_parent(self):
        parent = {1:self.rows[1]}
        self.update(0, {1:(0,0)}, rows=parent)
        self.assertEqual(self.update(1, {1:(0,1048576)}, rows=parent)['write_bytes_per_second'], 1048576)
        self.assertEqual(self.update(2, {1:(0,1048576)}, rows=parent)['write_bytes_per_second'], 0)

    def test_new_child_cannot_turn_old_counter_into_recent_rate(self):
        self.update(0, {1:(0,0)}, rows={1:self.rows[1]})
        self.assertEqual(self.update(1, {1:(0,0), 2:(0,9000000)})['reason'], 'scope_changed')
        self.assertEqual(self.update(2, {1:(0,0), 2:(0,9000000)})['state'], 'baseline')
        self.assertEqual(self.update(3, {1:(0,0), 2:(0,9000000)})['write_bytes_per_second'], 0)

    def test_individual_regression_not_hidden_by_other_process_increment(self):
        self.update(0, {1:(0,10), 2:(0,100)})
        self.assertEqual(self.update(1, {1:(0,1000), 2:(0,50)})['reason'], 'counter_regressed')
        self.assertEqual(self.update(2, {1:(0,1000), 2:(0,50)})['state'], 'baseline')

    def test_clock_pause_epoch_and_invalid_counter_rebaseline(self):
        for at in [0, -1, 4, float('nan'), float('inf'), 1e30, 2**64/1000]:
            self.aggregate = m.IoAggregate(); self.update(0, {1:(0,0), 2:(0,0)})
            self.assertEqual(self.update(at, {1:(0,1), 2:(0,0)})['reason'], 'clock')
            self.assertEqual(self.update(5, {1:(0,1), 2:(0,0)})['state'], 'baseline')
        self.assertEqual(self.update(6, {1:(0,99), 2:(0,0)}, identity=('boot','new'))['state'], 'baseline')
        self.assertEqual(self.update(7, {1:(0,2**64), 2:(0,0)})['reason'], 'parse_invalid')
        self.update(8, {1:(0,0), 2:(0,0)})
        self.assertEqual(self.update(8.0001, {1:(0,2**64-1), 2:(0,0)})['reason'], 'rate_limit')

    def test_root_fingerprint_and_parent_start_changes_break_baseline(self):
        for changed, roots in [({**self.rows,2:row(2,start=21,parent=1)},self.roots),
                               ({**self.rows,2:row(2,start=20,parent=0)},self.roots),
                               (self.rows,[self.rows[1],self.rows[2]])]:
            self.aggregate = m.IoAggregate(); self.update(0,{1:(0,0),2:(0,0)})
            tree = m.io_tree(changed, roots)[0]
            self.assertEqual(self.aggregate.update('scope',('boot','epoch'),1,tree[0],{pid:(0,1) for pid in tree[1]})['reason'],'scope_changed')

    def test_unavailable_partial_baseline_and_true_zero_are_distinct(self):
        for reason,state in [('access_denied','unavailable'),('budget','partial')]:
            self.assertEqual(self.update(0,{},reason=reason)['state'],state)
        self.assertEqual(self.update(1,{1:(0,0),2:(0,0)})['state'],'baseline')
        zero=self.update(2,{1:(0,0),2:(0,0)})
        self.assertEqual((zero['state'],zero['read_bytes_per_second'],zero['write_bytes_per_second']),('available',0,0))

    def collect(self, *, after=None, scopes=None, reader=None, partial=False, after_partial=False, verify=lambda _:True, clock=lambda:1, stopped=lambda:False):
        scopes = scopes or {'scope':dict(roots=self.roots)}
        return m.collect_io(scopes,self.rows,partial,self.aggregate,('boot','epoch'),
                            reader=reader or (lambda p:(0,p['pid'])), clock=clock, stopped=stopped,
                            after_scan=lambda **_:(after if after is not None else self.rows,after_partial,0),verify_tmux=verify)

    def test_overlapping_roots_and_scopes_read_each_tgid_once_and_update_scope_once(self):
        reads=[]
        scopes={'scope':dict(roots=[self.rows[1],self.rows[2],self.rows[1]]), 'other':dict(roots=[self.rows[2]])}
        with patch.object(self.aggregate,'update',wraps=self.aggregate.update) as update:
            self.collect(scopes=scopes,reader=lambda p:(reads.append(p['pid']) or (0,10)))
            self.assertEqual(sorted(reads),[1,2]); self.assertEqual(update.call_count,2)
            result=self.collect(scopes=scopes,reader=lambda p:(0,20),clock=lambda:2)
            self.assertEqual(result['scope']['write_bytes_per_second'],20)
            self.assertEqual(result['other']['write_bytes_per_second'],10)

    def test_zombie_and_snapshot_changes_missing_scans_and_tmux_changes_are_partial(self):
        zombie={**self.rows,2:row(2,start=20,parent=1,state='Z')}
        self.assertEqual(m.io_tree(zombie,self.roots)[1],'zombie_pending')
        variants=[dict(after=zombie),dict(after={1:self.rows[1]}),dict(after={**self.rows,3:row(3,start=30,parent=1)}),
                  dict(partial=True),dict(after_partial=True),dict(verify=lambda _:False)]
        for kwargs in variants:
            with self.subTest(kwargs=kwargs):
                value=self.collect(**kwargs)['scope']
                self.assertEqual(value['state'],'partial'); self.assertIsNone(value['write_bytes_per_second'])

    def test_empty_and_active_tmux_sockets_can_be_compared_without_text_or_counter_changes(self):
        empty=dict(server=None,clients={},panes={},pane_ttys={})
        active=dict(server=(100,'50'),clients={'/dev/pts/1':dict(session='$1',process=self.rows[1])},
                    panes={('$1','%1'):self.rows[2]},pane_ttys={('$1','%1'):'/dev/pts/2'})
        before=m.tmux_io_fingerprint([empty,active])
        active['panes'][('$1','%1')]=dict(self.rows[2],ticks=999,rss_pages=999)
        self.assertEqual(before,m.tmux_io_fingerprint([active,empty]))
        active['pane_ttys'][('$1','%1')]='/dev/pts/3'
        self.assertNotEqual(before,m.tmux_io_fingerprint([active,empty]))

    def test_read_failure_budget_and_stop_never_become_zero(self):
        def denied(_): raise PermissionError()
        self.assertEqual(self.collect(reader=denied)['scope']['reason'],'access_denied')
        self.assertEqual(self.collect(stopped=lambda:True)['scope']['reason'],'budget')
        tick=iter([0,0,1,1,1])
        self.assertEqual(self.collect(clock=lambda:next(tick))['scope']['reason'],'budget')


class ProcTests(unittest.TestCase):
    def test_io_parser_distinguishes_storage_from_char_cache_and_cancelled(self):
        self.assertEqual(m.io_counters(io_text(chars=100000,cancelled=10000)),(0,0))
        self.assertEqual(m.io_counters(io_text(read=9,write=100,chars=9000,cancelled=50)),(9,100))
        for raw in [io_text()+b'write_bytes: 0\n',io_text().replace(b'write_bytes: 0',b'write_bytes: -1'),
                    io_text(write=2**64),io_text().replace(b'read_bytes: 0\n',b''),b'\xff']:
            with self.assertRaises((ValueError,UnicodeError)):m.io_counters(raw)

    def test_real_self_tgid_is_readable_and_tid_is_rejected(self):
        process=m.observe_process(os.getpid())
        counters=m.observe_io(process)
        self.assertTrue(all(type(v) is int and 0 <= v <= m.IO_U64_MAX for v in counters))
        results=[]
        def worker():
            try:
                tid=threading.get_native_id()
                m.observe_io(m.observe_process(tid))
                results.append('unexpected_tid_accept')
            except ValueError as error: results.append(str(error))
        thread=threading.Thread(target=worker)
        thread.start();thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results,['identity_changed'])

    def test_short_reads_drain_and_limit_is_enforced(self):
        with patch.object(m.os,'open',return_value=99),patch.object(m.os,'close'),patch.object(m.os,'read',side_effect=[b'a',b'b',b'']):
            self.assertEqual(m.io_read_at(9,'io',2),b'ab')
        with patch.object(m.os,'open',return_value=99),patch.object(m.os,'close'),patch.object(m.os,'read',side_effect=[b'ab',b'c']):
            with self.assertRaisesRegex(ValueError,'read_limit'):m.io_read_at(9,'io',2)

    def test_proc_fd_identity_uid_tgid_and_symlink_checks(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name); proc=root/'123';proc.mkdir()
            (proc/'stat').write_text(stat_line())
            status=f'Pid:\t123\nTgid:\t123\nUid:\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\n'
            (proc/'status').write_text(status);(proc/'io').write_bytes(io_text(write=10))
            self.assertEqual(m.observe_io(row(),root),(0,10))
            for changed in [status.replace('Tgid:\t123','Tgid:\t124'),status.replace('Pid:\t123','Pid:\t124')]:
                (proc/'status').write_text(changed)
                with self.assertRaisesRegex(ValueError,'identity_changed'):m.observe_io(row(),root)
            (proc/'status').write_text(status.replace('Uid:',f'Uid:\t{os.getuid()+1}'))
            with self.assertRaises(PermissionError):m.observe_io(row(),root)
            (proc/'status').write_text(status)
            with self.assertRaisesRegex(ValueError,'identity_changed'):m.observe_io(row(start=101),root)
            with patch.object(m,'io_identity',side_effect=[(123,'100',1),(123,'101',1)]):
                with self.assertRaisesRegex(ValueError,'identity_changed'):m.observe_io(row(),root)
            (proc/'io').unlink();(proc/'io').symlink_to(proc/'stat')
            with self.assertRaises(OSError):m.observe_io(row(),root)


class ObserverTests(unittest.TestCase):
    def observer(self):
        observer=m.WorkloadObserver.__new__(m.WorkloadObserver)
        observer.receive=lambda _:None;observer.lock=threading.Lock();observer.pending_deadlines={}
        observer.stopped=threading.Event();observer.epoch='epoch';observer.sockets=[];observer.io=m.IoAggregate()
        observer.registered={'1':dict(pid=1,start='10',tty='/dev/pts/1'),'2':dict(pid=2,start='20',tty='/dev/pts/2')}
        return observer

    def test_two_windows_same_tmux_update_once_and_serialize_equal_io(self):
        observer=self.observer(); counter=m.WorkloadDelta(ticks=100,pages=4096)
        rows={1:row(1,start=10,parent=0),2:row(2,start=20,parent=0),3:row(3,start=30,parent=0)}
        tmux=dict(server=(100,'99'),session='$1',roots=[rows[3]])
        with patch.object(m,'scan_processes',return_value=(rows,False,.001)),patch.object(m,'observe_io',return_value=(0,10)), \
             patch.object(m,'tmux_scope',return_value=tmux),patch.object(m,'query_tmux',return_value={}), \
             patch.object(m,'tmux_io_fingerprint',return_value='fixed'),patch.object(m.time,'monotonic',return_value=1), \
             patch.object(observer.io,'update',wraps=observer.io.update) as update:
            observer.sockets=[Path('/')]
            first=observer.sample('boot',counter)
            self.assertEqual(update.call_count,1)
            self.assertEqual(first['workloads'][0]['io'],first['workloads'][1]['io'])
            with patch.object(m.time,'monotonic',return_value=2),patch.object(m,'observe_io',return_value=(0,20)):
                second=observer.sample('boot',counter)
            self.assertEqual(update.call_count,2)
            self.assertEqual([v['io']['write_bytes_per_second'] for v in second['workloads']],[10,10])

    def test_io_scan_failure_keeps_cpu_rss_and_resets_only_io(self):
        observer=self.observer();observer.registered.pop('2')
        rows={1:row(1,start=10,parent=0)};counter=m.WorkloadDelta(ticks=100,pages=4096)
        counter.update('boot','epoch',0,rows)
        with patch.object(m,'scan_processes',side_effect=[(rows,False,.001),PermissionError()]), \
             patch.object(m,'observe_io',return_value=(0,10)),patch.object(m.time,'monotonic',return_value=1):
            value=observer.sample('boot',counter)['workloads'][0]
        self.assertEqual(value['cpu_cores'],0);self.assertEqual(value['rss_bytes'],32768)
        self.assertEqual(value['coverage'],'complete');self.assertEqual(value['io']['state'],'unavailable')

    def test_maximum_sixteen_rows_remain_within_existing_frame_budget(self):
        # 最大幅の登録/root/id/counter rateを使用。IO追加で既存frame限界を拡張しない。
        value=dict(type='workload_v1',schema=1,nonce='a'*16,uid=4294967295,session='9223372036854775807',
                   epoch='b'*32,sequence='9223372036854775807',sampled_monotonic_ms=2**64-1,
                   boot='00000000-0000-0000-0000-000000000001',capability='available',interval_seconds=1,scan_seconds=.05,
                   registrations=[],workloads=[])
        io=dict(state='available',read_bytes_per_second=1.8446744073709552e19,write_bytes_per_second=1.8446744073709552e19,
                interval_seconds=.0000001,sampled_monotonic_ms=2**64-1,reason=None)
        for n in range(16):
            terminal=str(9223372036854775807-n)
            value['registrations'].append(dict(terminal_id=terminal,pid=4294967295,start=str(2**64-1),tty='/dev/pts/1234567890'))
            value['workloads'].append(dict(terminal_id=terminal,workload_id=f'shell:{terminal}:4294967295:{2**64-1}',scope='shell_tree',
                                          cpu_cores=4096.123456789,rss_bytes=2**64-1,processes=512,coverage='complete',reason=None,io=io))
        self.assertLessEqual(len(json.dumps(value,separators=(',',':')).encode()),m.WORKLOAD_BYTES)


if __name__ == '__main__':unittest.main()
