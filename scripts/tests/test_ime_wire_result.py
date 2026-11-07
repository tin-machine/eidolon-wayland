import hashlib
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ime_checks import ime_wire_result


class ImeWireResultTests(unittest.TestCase):
    nonce = '2' * 32
    version = dict(source_commit='fixed', apk_version_code=144)
    uid = 12345

    def fixture(self):
        report = dict(state='pass', error=None, fixture_revision='eidolon-text-input-v3-wire-v2',
                      uid=self.uid, nonce=hashlib.sha256(f'{self.nonce}:0'.encode()).hexdigest()[:16],
                      synthetic_input=True, physical_input_verified=False, manager_version=1,
                      checkpoints=13, invalid_clients_isolated=8, delivered=19, rejected=34)
        for key in ['disabled_default_safe', 'all_objects_entered', 'stale_sent_no_events',
                    'active_destroy_recovered', 'enable_reset_pending', 'geometry_race_preserved',
             'semantic_changes_rejected', 'connection_invalidated', 'serial_wrap_verified',
             'seeded_serial_wrap', 'client_quota_isolated']:
            report[key] = True
        for key in ['clients_after', 'objects_after', 'surfaces_after', 'ime_objects_after']:
            report[key] = 0
        return dict(schema=1, build_info=self.version, check=dict(case='ime-wire', nonce=self.nonce, result='pass',
                    cycles=[dict(cycle=1, scenario='text_input', report=report)], last_handle='1', accept_ms=1, stop_ms=2,
                    after_stop=dict(state='Stopped', compositor_workers=0, dependency_workers=0, helper_workers=0,
                                    render=dict(workers=0), transport=dict(workers=0, owned_connections=0))))

    def test_only_native_fixture_success_and_instrumentation_recovery_can_pass(self):
        for recovery in [False, True]:
            with patch('ime_checks.instrumentation_result', return_value=recovery) as check:
                self.assertIs(ime_wire_result(self.fixture(), self.nonce, self.version, self.uid), recovery)
                check.assert_called_once()

    def test_old_result_or_other_apk_is_not_current_evidence(self):
        for key, value in [('build_info', {}), ('schema', 2)]:
            fixture = self.fixture(); fixture[key] = value
            self.assertFalse(ime_wire_result(fixture, self.nonce, self.version, self.uid))
        for key, value in [('nonce', '3'*32), ('case', 'keyboard-wire'), ('result', 'running')]:
            fixture = self.fixture(); fixture['check'][key] = value
            self.assertFalse(ime_wire_result(fixture, self.nonce, self.version, self.uid))

    def test_claimed_physical_acceptance_missing_guards_and_resource_leaks_fail(self):
        mutations = dict(physical_input_verified=True, synthetic_input=False, uid=self.uid+1,
                         fixture_revision='other', manager_version=2, checkpoints=0, invalid_clients_isolated=0,
                         stale_sent_no_events=False, all_objects_entered=False, enable_reset_pending=False,
                         geometry_race_preserved=False, semantic_changes_rejected=False, connection_invalidated=False,
                         serial_wrap_verified=False, seeded_serial_wrap=False, client_quota_isolated=False,
                         active_destroy_recovered=False, disabled_default_safe=False, clients_after=1,
                         objects_after=1, surfaces_after=1, ime_objects_after=False, delivered=0, rejected=0)
        for key, value in mutations.items():
            with self.subTest(key=key):
                fixture = self.fixture(); fixture['check']['cycles'][0]['report'][key] = value
                with self.assertRaises(ValueError):
                    ime_wire_result(fixture, self.nonce, self.version, self.uid)

    def test_runtime_not_stopped_or_cancelled_cannot_pass(self):
        for mutation in [dict(cleanup_failed=True), dict(stop_ms=10000), dict(result='fail')]:
            fixture = self.fixture(); fixture['check'].update(mutation)
            with self.assertRaises(ValueError):
                ime_wire_result(fixture, self.nonce, self.version, self.uid)
        fixture = self.fixture(); fixture['check']['after_stop']['transport']['workers'] = 1
        with self.assertRaises(ValueError):
            ime_wire_result(fixture, self.nonce, self.version, self.uid)


if __name__ == '__main__':
    unittest.main()
