"""native停止だけの成功、旧世代、remote未確認をPhase6合格へ昇格させない。"""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
from lifecycle_checks import stop_result


class LifecycleResultTests(unittest.TestCase):
    def fixture(self):
        sha = 'b'*64
        def live(handle, session):
            key = dict(session=str(session), window='1', scene='1', generation='1')
            return dict(state='Ready', handle=str(handle), session=str(session),
                app=dict(state='running', session=str(session), mode='foot', error=None,
                    local=dict(uid=4242, child_uid=4242, owned_groups=1, executable_sha256=sha),
                    windows=[dict(window='1', frame='9', life='Mapped')]),
                transport=dict(state='Ready', session=str(session), owned_connections=1, app_forward=dict(uid=4343)),
                render=dict(targets=[dict(key=key, role='windows', frames=3, content_frame='9')]),
                adapter=dict(bindings=1, listeners=1), shutdown=dict(sdk_bindings=1))
        def stopped(handle, session, full):
            return dict(state='Stopped' if full else 'Ready', handle=str(handle), session=str(session),
                helper_workers=0, dependency_workers=0, compositor_workers=0,
                app=dict(state='stopped', directory_cleaned=True, windows=[], clients=0, objects=0,
                    surfaces=0, pixel_bytes=0, pool_bytes=0, pool_count=0, memfd_fds_after=0, memfd_maps_after=0,
                    local=dict(waited=True, workers=0, owned_groups=0)),
                transport=dict(state='Stopped' if full else 'Disconnected', session=str(session), error=None,
                    owned_connections=0, forward_active=0, tokio_threads=0, workers=0 if full else 1,
                    pending_cleanup=[], app_forward=dict(uid=4343, remote_cleaned=True, forward_cancelled=True, pending_cleanup=[])),
                render=dict(targets=[], workers=0 if full else 1, contexts=0, latest_images=0),
                adapter=dict(bindings=0, listeners=0, decoration_entities=0),
                shutdown=dict(requested=full, complete=full, timed_out=False, pending_owners=[], sdk_bindings=0,
                    deadline_ms=10000, elapsed_ms=20))
        rows=[]
        for i in range(20):
            handle=i+2; session=2*i+1
            rows.append(dict(cycle=i+1, before_disconnect=live(handle, session), before_stop=live(handle, session+1),
                after_disconnect=stopped(handle, session, False), disconnect_ms=20, old_handle_rejected=True,
                stop=dict(handle=str(handle), accept_ms=1, stop_ms=20, runtime=stopped(handle, session+1, True)),
                after_restart=dict(state='Ready', handle=str(handle+1), transport=dict(state='Idle', pending_cleanup=[]),
                    render=dict(targets=[dict(role='diagnostic', frames=3)]), adapter=dict(bindings=1, listeners=1))))
        proof=dict(case='stop', nonce='a'*32, result='pass', uid=4242, pid=1234, consumer='SDK',
            synthetic_visibility=False, physical_input_verified=False, hmd_verified=False,
            product_settings_untouched=True, cycles=rows,
            preferences_environment=dict(original_handle='1', fixture_handle='2', last_handle='22', product_handle='23'),
            product_restored=dict(state='Ready', activity_visible=True, xr_state='FOCUSED',
                transport=dict(state='Idle', pending_cleanup=[]), render=dict(targets=[dict(role='diagnostic', frames=3)])))
        return dict(schema=1, build_info={}, check=proof), dict(dependencies=dict(waypipe=dict(sha256=sha)))

    def test_full_receipts_required_and_partial_or_stale_success_rejected(self):
        valid, package=self.fixture()
        def judge(value): return stop_result(value, 'a'*32, {}, 4242, 4343, package)
        self.assertTrue(judge(valid))
        paths=[
            (['physical_input_verified'], True), (['product_settings_untouched'], False),
            (['cycles'], valid['check']['cycles'][:19]),
            (['cycles', 0, 'before_stop', 'session'], '1'),
            (['cycles', 0, 'before_stop', 'app', 'local', 'child_uid'], 4343),
            (['cycles', 0, 'before_stop', 'render', 'targets', 0, 'content_frame'], '8'),
            (['cycles', 0, 'after_disconnect', 'app', 'pool_bytes'], 1024),
            (['cycles', 0, 'after_disconnect', 'transport', 'app_forward', 'remote_cleaned'], False),
            (['cycles', 0, 'after_disconnect', 'shutdown', 'sdk_bindings'], 1),
            (['cycles', 0, 'stop', 'runtime', 'shutdown', 'pending_owners'], ['sdk']),
            (['cycles', 0, 'stop', 'runtime', 'shutdown', 'timed_out'], True),
            (['cycles', 0, 'stop', 'accept_ms'], 100),
            (['cycles', 0, 'stop', 'runtime', 'transport', 'pending_cleanup'], ['owned']),
            (['cycles', 0, 'stop', 'runtime', 'render', 'contexts'], 1),
            (['cycles', 0, 'old_handle_rejected'], False),
            (['cycles', 0, 'after_restart', 'handle'], '2'),
        ]
        for path, value in paths:
            bad=copy.deepcopy(valid); target=bad['check']
            for key in path[:-1]: target=target[key]
            target[path[-1]]=value
            with self.subTest(path=path), self.assertRaises(ValueError): judge(bad)
        valid['check']['result']='running'
        self.assertFalse(judge(valid))


if __name__ == '__main__': unittest.main()
