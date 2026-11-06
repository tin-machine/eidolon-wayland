import base64
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest
from test_pairing import key
from pairing import PairError

loader = importlib.machinery.SourceFileLoader('termux_pair', str(Path(__file__).parents[1] / 'termux/eidolon-pair'))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)


class TermuxPairTests(unittest.TestCase):
    def test_public_key_round_trip_idempotence_and_validation_before_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            authorized = root / '.ssh/authorized_keys'
            host = root / 'host.pub'
            host.write_text(key(2) + ' termux\n')
            result = module.pair(key(1), 'u0_a123', host, authorized)
            self.assertTrue(result['changed'])
            self.assertEqual(result['host_key'], key(2))
            self.assertEqual(len(base64.b64decode(result['host_fingerprint'][7:] + '=')), 32)
            before = authorized.read_bytes()
            self.assertFalse(module.pair(key(1), 'u0_a123', host, authorized)['changed'])
            for user, public in [('bad;command', key(3)), ('u0_a123', key(3) + '\n' + key(4))]:
                with self.assertRaises(PairError): module.pair(public, user, host, authorized)
            host.write_text('ssh-rsa invalid')
            with self.assertRaises(PairError): module.pair(key(3), 'u0_a123', host, authorized)
            self.assertEqual(authorized.read_bytes(), before)
