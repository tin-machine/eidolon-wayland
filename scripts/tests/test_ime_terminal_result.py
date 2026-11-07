import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
loader=importlib.machinery.SourceFileLoader('ime_terminal_cli',str(Path(__file__).resolve().parents[1]/'quest-ime-terminal'))
spec=importlib.util.spec_from_loader(loader.name,loader)
cli=importlib.util.module_from_spec(spec);loader.exec_module(cli)

class TerminalMetadataTests(unittest.TestCase):
    def fixture(self):
        data={key:False for key in cli.BOOLS}
        data.update({key:0 for key in cli.NUMBERS})
        data.update(case='ime-terminal',nonce='a'*32,result='running',scope='debug_terminal_ime',phase='DIRECT_READY',
                    reason='ready',raw_return='not_requested',failure=None,connection_failure=None,raw_return_failure=None,last_external_loss=None,
                    acceptance=dict(actual_ime='not_verified',physical_input='not_verified',hmd='not_verified'))
        return data
    def read(self,data):
        class Device:
            def adb(self,*args,**kwargs):return json.dumps({'check':data})
        return cli.receipt(Device(),'a'*32)
    def test_only_matching_nonce_and_fixed_case_are_received(self):
        self.assertEqual(self.read(self.fixture()),self.fixture())
        data=self.fixture();data['nonce']='b'*32;self.assertIsNone(self.read(data))
        data=self.fixture();data['case']='other';self.assertIsNone(self.read(data))
    def test_text_bearing_unknown_fields_and_false_acceptance_are_rejected(self):
        for key in ['preedit','commit','surrounding','arbitrary']:
            data=self.fixture();data[key]='must not persist'
            with self.assertRaisesRegex(ValueError,'SCHEMA'):self.read(data)
        data=self.fixture();data['acceptance']['actual_ime']='pass'
        with self.assertRaisesRegex(ValueError,'ACCEPTANCE'):self.read(data)
    def test_focus_and_prior_external_loss_metadata_are_separate(self):
        data=self.fixture();data['focus_requests']=3;data['reason']='focus_requested';data['last_external_loss']='external_focus_lost'
        self.assertEqual(self.read(data),data)
        data['last_external_loss']='untrusted body'
        with self.assertRaisesRegex(ValueError,'TOKEN'):self.read(data)
    def test_connection_and_raw_return_failures_are_independent(self):
        data=self.fixture();data.update(connection_failure='closed',raw_return_failure='return_timeout',failure='return_timeout')
        self.assertEqual(self.read(data),data)
        data['raw_return_failure']='private content'
        with self.assertRaisesRegex(ValueError,'TOKEN'):self.read(data)
    def test_malformed_types_and_unknown_status_are_rejected(self):
        for key,value in [('applied',1.0),('enqueued',-1),('sdk_down_submitted',1)]:
            data=self.fixture();data[key]=value
            with self.assertRaisesRegex(ValueError,'TYPE'):self.read(data)
        data=self.fixture();data['failure']='user text'
        with self.assertRaisesRegex(ValueError,'TOKEN'):self.read(data)
