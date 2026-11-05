"""設定隔離を欠く成功や、隔離runtimeを製品UI復帰と誤認する証明を拒否する。"""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
from quest_checks import sdk_environment_result


class SdkEnvironmentTests(unittest.TestCase):
    def test_settings_and_product_runtime_restore_are_required(self):
        proof = dict(case='resize', nonce='a'*32, result='pass', product_settings_untouched=True,
            preferences_environment=dict(original_handle='1', fixture_handle='2', last_handle='3', product_handle='4'),
            product_restored=dict(state='Ready', activity_visible=True, xr_state='FOCUSED',
                transport=dict(state='Idle', pending_cleanup=[]), render=dict(targets=[dict(role='diagnostic', frames=3)])))
        snapshot = dict(schema=1, build_info={}, check=proof)
        self.assertTrue(sdk_environment_result(snapshot, 'a'*32, {}, 'resize'))
        for path, value in [(['product_settings_untouched'], False), (['preferences_environment'], {}),
                (['preferences_environment','fixture_handle'], '1'), (['preferences_environment','product_handle'], '3'),
                (['product_restored','transport','pending_cleanup'], ['owned']),
                (['product_restored','xr_state'], 'VISIBLE'), (['product_restored','render','targets',0,'role'], 'windows')]:
            bad = copy.deepcopy(snapshot)
            target = bad['check']
            for key in path[:-1]: target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(ValueError):
                sdk_environment_result(bad, 'a'*32, {}, 'resize')
        proof['result'] = 'running'
        self.assertFalse(sdk_environment_result(snapshot, 'a'*32, {}, 'resize'))

