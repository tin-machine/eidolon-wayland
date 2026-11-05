"""targetなし/非表示のcallbackや、古い画面/SDK未適用/回収漏れを成功にしない。"""
import copy
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from quest_checks import foot_result, foot_sdk_result


class FootTests(unittest.TestCase):
    def fixture(self):
        nonce = 'a'*32
        token = hashlib.sha256(nonce.encode()).hexdigest()[:16]
        def state(callbacks, paused=False):
            local = dict(uid=4242, child_uid=4242, executable_sha256='0'*64, workers=1, owned_groups=1, error=None)
            forward = dict(uid=4343, nonce=token, mode='foot_fixture', state='running', error=None,
                           registered=True, tools=dict(foot=dict(version='1.28.0')))
            return dict(state='Ready', app=dict(session='1', nonce=token, mode='foot_fixture', callback_clock='target-driven',
                        ready=True, error=None, pool_rejections=0, local=local, callbacks=callbacks, non_uniform=True,
                        windows=[dict(window='1', life='Mapped', frame=str(callbacks))],
                        input=dict(keyboard=True, layout='us', include_paths=0, keymap_sha256='1'*64)),
                        transport=dict(state='Ready', session='1', credential_requests=1, app_forward=forward),
                        render=dict(mode='windows', error=None, latest_images=1, targets=[], contexts=1, paused=paused,
                                    composition=dict(updates=5, textures=1)))
        rows = []
        for cycle, generation, callbacks, count in [(1, '1', 6, 4), (2, '3', 15, 8)]:
            current = state(callbacks)
            key = dict(session='1', window='1', scene='1', generation=generation)
            current['render']['targets'] = [dict(role='windows', key=key, content_frame=str(callbacks),
                width=1024 if cycle == 1 else 800, height=768 if cycle == 1 else 600)]
            current['panels'] = [dict(target=key, command_seq=generation, action='create', applied=True, error=None)]
            rows.append(dict(generation=cycle, target_generation=generation, runtime=current,
                pixels=dict(distinct_images=count, non_uniform_images=count, sample_step=4, hashes=[f'{i:064x}' for i in range(count)])))
        after = dict(state='Stopped', app=dict(state='stopped', directory_cleaned=True, error=None,
            clients=0, objects=0, surfaces=0, pool_bytes=0, pixel_bytes=0, pool_count=0,
            local=dict(workers=0, owned_groups=0, waited=True), input=dict(keyboard=False)),
            transport=dict(workers=0, tokio_threads=0, owned_connections=0, pending_cleanup=[],
                app_forward=dict(state='stopped', error=None, remote_cleaned=True, forward_cancelled=True, pending_cleanup=[],
                    remote=dict(finished=dict(reason='Stop', cleaned=True, group_alive=False, pipes_closed=True)))),
            render=dict(targets=[], workers=0, contexts=0, latest_images=0, queued=0, composition=dict(textures=0, texture_bytes=0)))
        result = dict(case='foot', nonce=nonce, result='pass', session='1', window='1', token=token, credential_requests=1,
            consumer='ImageReader', synthetic_visibility=True, consumer_worker_stopped=True, before_target=state(0), cycles=rows,
            pause=state(7, True), pause_held=state(7, True), resumed=state(10), after_stop=after, accept_ms=0, stop_ms=150)
        scrolling = copy.deepcopy(rows[1]['runtime'])
        scrolling['app']['callbacks'] = 30
        result['scroll'] = dict(observed_ms=4000, callbacks_before=20, runtime=scrolling,
            pixels=dict(lower_distinct_images=5, lower_non_uniform_images=5, lower_hashes=[f'{i:064x}' for i in range(5)]))
        return dict(schema=1, build_info=dict(profile='debug'), check=result)

    def test_fake_present_virtual_clock_stale_surface_unapplied_sdk_and_leaks_are_rejected(self):
        package = dict(dependencies=dict(waypipe=dict(sha256='0'*64), keymaps=dict(us=dict(sha256='1'*64))))
        def judge(snapshot):
            return foot_result(snapshot, 'a'*32, dict(profile='debug'), 4242, 4343, package)
        fixture = self.fixture()
        with patch('quest_checks.instrumentation_result', return_value=True):
            self.assertTrue(judge(fixture))
            for path, value in [
                (['before_target','app','callbacks'],1),
                (['cycles',0,'runtime','app','callback_clock'],'virtual'),
                (['cycles',0,'runtime','app','local','child_uid'],4343),
                (['cycles',0,'runtime','app','input','include_paths'],1),
                (['cycles',0,'runtime','render','targets',0,'content_frame'],'5'),
                (['cycles',1,'target_generation'],'1'),
                (['cycles',0,'runtime','panels',0,'applied'],False),
                (['cycles',0,'pixels','non_uniform_images'],0),
                (['cycles',0,'pixels','distinct_images'],0),
                (['pause_held','app','callbacks'],8),
                (['scroll','observed_ms'],200),(['scroll','pixels','lower_distinct_images'],0),
                (['after_stop','render','contexts'],1),
                (['after_stop','transport','app_forward','remote','finished','group_alive'],True),
            ]:
                bad = copy.deepcopy(fixture); current = bad['check']
                for key in path[:-1]: current = current[key]
                current[path[-1]] = value
                with self.subTest(path=path), self.assertRaises(ValueError): judge(bad)

    def test_sdk_scope_requires_actual_visibility_panel_ack_recreation_and_ui_restore(self):
        fixture = self.fixture()
        result = fixture['check']
        result.update(consumer='SDK', synthetic_visibility=False, product_host_path=True, hmd_verified=False,
                      old_handle='1', new_handle='2', stop=dict(old_runtime_destroyed=True, stop_ms=150))
        for index, row in enumerate(result['cycles']):
            state = row['runtime']
            state.update(scene='1', activity_visible=True, xr_state='FOCUSED')
            state['app'].update(mode='foot', copies=10)
            state['transport']['app_forward']['mode'] = 'foot'
            target = state['render']['targets'][0]
            target.update(frames=3, width=1024, height=768)
            row['adapter'] = dict(bindings=1, listeners=1, created=2+index, destroyed=1+index)
        result['restored'] = dict(state='Ready', activity_visible=True, xr_state='FOCUSED',
            transport=dict(state='Idle', pending_cleanup=[]),
            render=dict(mode='diagnostic', targets=[dict(role='diagnostic', readback=True, frames=3)]))
        package = dict(dependencies=dict(waypipe=dict(sha256='0'*64), keymaps=dict(us=dict(sha256='1'*64))))
        def judge(value):
            return foot_sdk_result(value, 'a'*32, dict(profile='debug'), 4242, 4343, package)
        self.assertTrue(judge(fixture))
        for path, value in [
            (['synthetic_visibility'],True),(['hmd_verified'],True),
            (['cycles',0,'runtime','activity_visible'],False),
            (['cycles',0,'runtime','xr_state'],'IDLE'),
            (['cycles',0,'runtime','panels',0,'applied'],False),
            (['cycles',1,'adapter','destroyed'],1),
            (['new_handle'],'1'),(['restored','transport','pending_cleanup'],['fixture']),
        ]:
            bad = copy.deepcopy(fixture); current = bad['check']
            for key in path[:-1]: current = current[key]
            current[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(ValueError): judge(bad)
