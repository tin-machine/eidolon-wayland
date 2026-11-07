"""固定workloadの実process境界・取消時の専用group回収を確認する。"""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'eidolon-io-workload.py'
spec = importlib.util.spec_from_file_location('io_workload', SCRIPT)
m = importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class WorkloadFixtureTests(unittest.TestCase):
    def test_natural_exit_fixed_one_mib_and_phase_order_cleanup(self):
        with tempfile.TemporaryDirectory() as name:
            phases=[];sizes=[]
            def emit(phase):
                phases.append(phase)
                if phase=='written_live':
                    files=list(Path(name).glob('.io-data-*/fixed.bin'))
                    self.assertEqual(len(files),1)
                    sizes.append(files[0].stat().st_size)
                    self.assertEqual(files[0].stat().st_mode & 0o777,0o600)
            result=m.run_fixture(Path(name),emit,phases=(.02,.02,.02,.02))
            self.assertEqual(phases,['baseline','written_live','zombie','waited_idle'])
            self.assertEqual(sizes,[1048576]);self.assertEqual(result['result'],'completed')
            self.assertTrue(result['cleanup_completed']);self.assertFalse(result['physical_disk_guaranteed'])
            self.assertEqual(list(Path(name).iterdir()),[])

    def test_cancel_at_each_phase_reaps_only_created_child(self):
        other=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'])
        try:
            for target in ['baseline','written_live','zombie','waited_idle']:
                with tempfile.TemporaryDirectory() as name:
                    def emit(phase):
                        if phase==target:raise m.FixtureFailure('cancelled')
                    result=m.run_fixture(Path(name),emit,phases=(.01,.01,.01,.01))
                    self.assertEqual(result['reason'],'cancelled');self.assertTrue(result['cleanup_completed'])
                    self.assertIsNone(other.poll());self.assertEqual(list(Path(name).iterdir()),[])
        finally:other.terminate();other.wait(timeout=2)

    def test_child_session_initialization_failure_never_signals_parent_group(self):
        with tempfile.TemporaryDirectory() as name, patch.object(m.os,'setsid',side_effect=OSError('fixed')):
            result=m.run_fixture(Path(name),lambda phase:None,phases=(.01,)*4)
            self.assertEqual(result['result'],'fail');self.assertTrue(result['cleanup_completed'])
            self.assertEqual(list(Path(name).iterdir()),[])

    def test_invalid_owner_never_signals_a_group(self):
        with patch.object(m.os,'killpg') as group, patch.object(m.os,'kill') as process:
            for pid in [0,-1,1,os.getpid(),None]: self.assertFalse(m.reap_owned(pid,True))
            group.assert_not_called();process.assert_not_called()

    def test_actual_sigterm_cleanup_and_no_argument_interface(self):
        with tempfile.TemporaryDirectory() as name:
            p=Path(name)/'fixture.py';p.write_bytes(SCRIPT.read_bytes());p.chmod(0o600)
            env=dict(os.environ,EIDOLON_SESSION_NONCE='a'*16,EIDOLON_TERMINAL_ID='7')
            child=subprocess.Popen([sys.executable,'-B',str(p)],env=env,stdout=subprocess.PIPE,text=True)
            try:
                event=json.loads(child.stdout.readline());self.assertEqual(event['phase'],'baseline')
                child.send_signal(signal.SIGTERM)
                out=child.communicate(timeout=3)[0]
                result=json.loads(out.strip());self.assertTrue(result['cleanup_completed']);self.assertEqual(result['reason'],'cancelled')
                self.assertEqual(child.returncode,1);self.assertEqual(list(Path(name).glob('.io-data-*')),[])
            finally:
                if child.poll() is None:child.kill();child.wait()
                child.stdout.close()
            bad=subprocess.run([sys.executable,'-B',str(p),'arbitrary'],env=env,capture_output=True,text=True,timeout=2)
            self.assertEqual(bad.returncode,2);self.assertEqual(json.loads(bad.stdout)['result'],'rejected')


if __name__=='__main__':unittest.main()
