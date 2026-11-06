import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]))
from observation import battery, compact, proc_stat, pss_kib, summarize


class ObservationTests(unittest.TestCase):
    def test_missing_battery_is_unknown_and_ac_power_is_not_charging_success(self):
        self.assertIsNone(battery('AC powered: true')['level'])
        self.assertEqual(pss_kib(' TOTAL PSS: 12345 TOTAL RSS: 99999'),12345)
        self.assertIsNone(pss_kib('permission denied'))
        s=[dict(elapsed_seconds=t,battery=dict(level=n,scale=100),errors=['adb_unavailable']) for t,n in [(0,90),(1800,80)]]
        result=summarize(s,1800,60,'power-lifecycle')
        self.assertFalse(result['sampling_complete'])
        self.assertFalse(result['battery_non_decreasing'])
        self.assertEqual(result['acceptance'],'not_run')
    def test_private_content_removed_and_stale_sequence_or_restart_not_accepted(self):
        raw=dict(process_id=12,sequence='1',build_info=dict(schema=1),runtime=dict(runtime=dict(state='Ready',app=dict(input=dict(text='PRIVATE_INPUT'),windows=[dict(title='PRIVATE_TITLE')]),
                 transport=dict(pending_cleanup=['PRIVATE_NONCE']))),check=dict(clipboard='PRIVATE_CLIPBOARD'))
        value=compact(raw)
        self.assertNotIn('PRIVATE_',json.dumps(value))
        samples=[dict(pid=12,process=dict(start_ticks=1),elapsed_seconds=t,snapshot=value,errors=[]) for t in [0,60,120]]
        result=summarize(samples,120,60,'soak')
        self.assertTrue(result['sampling_complete'])
        self.assertFalse(result['diagnostics_advancing'])
        samples[-1]['process']['start_ticks']=2
        self.assertFalse(summarize(samples,120,60,'soak')['same_process'])
        self.assertEqual(result['acceptance'],'not_run')
        with self.assertRaises(ValueError): compact(dict(process_id=12))
    def test_proc_command_with_spaces_and_parentheses_is_not_recorded(self):
        fields=['S']+['0']*19
        fields[11]='20';fields[12]='3';fields[19]='999'
        self.assertEqual(proc_stat('123 (private (cmd)) '+' '.join(fields)),dict(cpu_ticks=23,start_ticks=999))
