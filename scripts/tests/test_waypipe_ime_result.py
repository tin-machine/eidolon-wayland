"""転送だけ、古いserial、物理入力の自己申告をIME受入れの証拠にしない。"""
import copy
import os
from pathlib import Path
import runpy
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
validate = runpy.run_path(str(SCRIPTS / 'check-waypipe-ime'))['validate']


class WaypipeImeResultTests(unittest.TestCase):
    metadata = dict(version='0.11.3', sha256='fixed-hash')

    def fixture(self):
        return dict(schema=1, state='pass', fixture_revision='eidolon-ime-waypipe-protocol-v2',
                    server='Eidolon ProtocolState', synthetic_input=True, physical_input_verified=False,
                    uid=os.getuid(), waypipe_version='0.11.3', waypipe_sha256='fixed-hash',
                    waypipe_ends=2, connections=2, checkpoints=12, geometry_race_preserved=True,
                    queued_payload_unchanged=True, clients_after=0, objects_after=0, surfaces_after=0,
                    windows_after=0, pixel_bytes_after=0, pool_bytes_after=0, owned_groups_waited=3,
                    drive_error=None, fixed_payload_only=True, payload_logged=False,
                    ime=dict(objects=0, active=False, delivered=7, rejected=4),
                    client=dict(schema=1, state='pass', fixture_revision='eidolon-ime-waypipe-client-v2',
                                manager_version=1, mapped=2, enters=3, leaves=3, done=7, commits=4, preedits=4,
                                done_serials=[1, 1, 1, 1, 2, 3, 4], fixed_payload_only=True, payload_logged=False))

    def test_complete_evidence_passes_but_each_missing_observation_fails(self):
        valid = self.fixture()
        validate(valid, self.metadata)
        for key in valid:
            with self.subTest(key=key), self.assertRaises(ValueError):
                incomplete = copy.deepcopy(valid)
                del incomplete[key]
                validate(incomplete, self.metadata)

    def test_fake_server_claim_wrong_serial_duplicate_commit_or_leak_fails(self):
        mutations = [('server', 'stub'), ('physical_input_verified', True),
                     ('geometry_race_preserved', False), ('queued_payload_unchanged', False),
                     ('clients_after', 1), ('clients_after', False), ('owned_groups_waited', 2),
                     ('waypipe_sha256', 'other'), ('connections', 0)]
        for key, value in mutations:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                altered = self.fixture()
                altered[key] = value
                validate(altered, self.metadata)
        for key, value in [('done_serials', [1, 1, 1, 1, 1, 3, 4]), ('commits', 5),
                           ('manager_version', True), ('done_serials', [True, 1, 1, 1, 2, 3, 4])]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                altered = self.fixture()
                altered['client'][key] = value
                validate(altered, self.metadata)
        for key, value in [('objects', False), ('active', 0), ('delivered', 6), ('rejected', 0)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                altered = self.fixture()
                altered['ime'][key] = value
                validate(altered, self.metadata)


if __name__ == '__main__':
    unittest.main()
