"""GPU readback/世代/cache/回収を欠いた見かけの成功を拒否する。"""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).parents[1]))
from quest_checks import render_result


class RenderTests(unittest.TestCase):
    def fixture(self):
        rows, samples, uploads, updates = [], 0, 0, 0
        for index in range(36):
            generation, stage = index//18+1, index%18
            samples += 16 if stage >= 16 else (8 if generation == 1 else 6)
            if stage == 17:updates += 3
            else:uploads += 1
            target=dict(key=dict(session='1',window='1',scene='1',generation=str(generation)),
                content_frame=str(100+index),readback=True,width=64 if generation==1 else 128,height=64 if generation==1 else 96,
                vendor='fixture GPU',renderer='fixture GPU',version='GLES3',rgba_bits=[8]*4)
            composition=dict(readback_samples=samples,textures=1 if stage<16 else 3,texture_bytes=256,uploads=uploads,updates=updates)
            rows.append(dict(cycle=index+1,generation=generation,stage=stage,frame=str(100+index),
                render=dict(workers=1,contexts=1,latest_images=1,paused=False,error=None,targets=[target],composition=composition)))
        after=dict(state='Stopped',render=dict(targets=[],workers=0,contexts=0,latest_images=0,queued=0,composition=dict(textures=0,texture_bytes=0)))
        result=dict(case='render',nonce='a'*32,result='pass',consumer='ImageReader',synthetic_visibility=True,
            consumer_worker_stopped=True,consumer_images=40,cycles=rows,after_stop=after,accept_ms=1,stop_ms=90)
        result['pattern']=dict(error=None,contexts=1,latest_images=0,composition=dict(pattern_readback_samples=4),
            targets=[dict(readback=True,content_frame=None,key=dict(session='1',window='1',scene='1',generation='1'))])
        return dict(schema=1,build_info=dict(profile='debug'),check=result)

    def test_missing_readback_stale_frame_wrong_generation_cache_or_leak_never_pass(self):
        snapshot=self.fixture()
        # 共通のUID/PID/新runtimeの検証は他caseと共有し、このtestではGPU固有gateに集中する。
        with patch('quest_checks.instrumentation_result',return_value=True):
            self.assertTrue(render_result(snapshot,'a'*32,dict(profile='debug'),4242))
            for path,value in [
                (['consumer_images'],0),(['consumer_worker_stopped'],False),
                (['pattern','composition','pattern_readback_samples'],0),
                (['cycles',0,'render','targets',0,'readback'],False),
                (['cycles',18,'render','targets',0,'key','generation'],'1'),
                (['cycles',2,'render','targets',0,'content_frame'],'101'),
                (['cycles',8,'render','composition','readback_samples'],0),
                (['cycles',17,'render','composition','updates'],0),
                (['after_stop','render','contexts'],1),(['after_stop','render','composition','texture_bytes'],4096),
            ]:
                bad=copy.deepcopy(snapshot);current=bad['check']
                for key in path[:-1]:current=current[key]
                current[path[-1]]=value
                with self.subTest(path=path),self.assertRaises(ValueError):render_result(bad,'a'*32,dict(profile='debug'),4242)
