import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('sdl_probe', Path(__file__).resolve().parents[1] / 'eidolon-sdl-probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def report(driver='wayland'):
    return dict(schema=1, probe='eidolon-sdl-v1', completed=True, stage='complete', exit_reason='frames',
                video_driver=driver, renderer='software', renderer_flags=13, compiled_sdl='2.32.10', linked_sdl='2.32.10',
                requested_frames=3, frames=3, pixel_checks=3, key_down=0, key_up=0, focus_lost=0, close_events=0,
                synthetic_events=False, fd_before=3, fd_after=3, error='')


def content_trace(buffer_kind='shm', marker='@'):
    if buffer_kind == 'shm':
        create = 'wl_shm_pool@8.create_buffer(new id wl_buffer@9, 0, 640, 400, 2560, 0)'
    elif buffer_kind == 'dmabuf':
        create = 'zwp_linux_buffer_params_v1@8.create_immed(new id wl_buffer@9, 640, 400, 875713112, 0)'
    else:
        create = 'unknown@8.buffer(new id wl_buffer@9)'
    return ('\n'.join([
        'wl_registry@2.bind(1, "wl_shm", 1, new id wl_shm@3)',
        'xdg_wm_base@4.get_xdg_surface(new id xdg_surface@5, wl_surface@6)',
        'xdg_surface@5.get_toplevel(new id xdg_toplevel@7)', create,
        'wl_surface@6.attach(wl_buffer@9, 0, 0)',
        'wl_surface@6.frame(new id wl_callback@10)',
        'wl_surface@6.commit()', 'wl_callback@10.done(111)', 'wl_buffer@9.release()',
        'wl_surface@6.attach(wl_buffer@9, 0, 0)', 'wl_surface@6.commit()',
    ]) + '\n').replace('@', marker).encode()


class SdlProbeTests(unittest.TestCase):
    def test_fragmented_trace_accepts_real_content_shm_and_counts_callbacks(self):
        for marker in ['@', '#']:
            trace = probe.WaylandTrace()
            data = content_trace(marker=marker)
            for byte in data:
                trace.feed(bytes([byte]))
            summary = trace.summary()
            self.assertEqual(summary['content_shm_commits'], 2)
            self.assertEqual(summary['content_shm_formats'], [0])
            self.assertEqual(summary['content_frame_callbacks'], 1)
            self.assertEqual(summary['content_buffer_releases'], 1)
            self.assertEqual(summary['bound_globals'], {'wl_shm': 1})
            gates = probe.assess(report(), summary, 0, driver='wayland', reclaimed=True)
            self.assertEqual(gates['shm_gate'], 'pass')
            self.assertEqual(gates['input_gate'], 'not_run')
            self.assertEqual(gates['quest_sdk'], 'not_run')

    def test_cursor_shm_alone_is_not_application_shm(self):
        trace = probe.WaylandTrace()
        trace.feed(b'wl_shm_pool@1.create_buffer(new id wl_buffer@2, 0, 24, 24, 96, 0)\n'
                   b'wl_pointer@3.set_cursor(5, wl_surface@4, 0, 0)\n'
                   b'wl_surface@4.attach(wl_buffer@2, 0, 0)\nwl_surface@4.commit()\n'
                   b'wl_surface@4.commit()\n')
        self.assertEqual(trace.summary()['content_shm_commits'], 0)
        self.assertEqual(probe.assess(report(), trace.summary(), 0, driver='wayland', reclaimed=True)['shm_gate'], 'fail')

    def test_non_shm_unknown_protocol_error_and_incomplete_trace_cannot_pass(self):
        for kind in ['dmabuf', 'unknown']:
            trace = probe.WaylandTrace()
            trace.feed(content_trace(kind))
            self.assertEqual(probe.assess(report(), trace.summary(), 0, driver='wayland', reclaimed=True)['shm_gate'], 'fail')
        for suffix in [b'wl_display@1.error(wl_surface@6, 1, "failure")\n', b'partial', b'x' * (probe.LINE_LIMIT + 1)]:
            trace = probe.WaylandTrace()
            trace.feed(content_trace() + suffix)
            self.assertEqual(probe.assess(report(), trace.summary(), 0, driver='wayland', reclaimed=True)['shm_gate'], 'fail')
        trace = probe.WaylandTrace()
        trace.feed(content_trace().replace(b'2560, 0)', b'2560, 875708993)'))
        self.assertEqual(probe.assess(report(), trace.summary(), 0, driver='wayland', reclaimed=True)['shm_gate'], 'fail')

    def test_dummy_synthetic_input_and_cleanup_are_not_real_wayland_success(self):
        trace = probe.WaylandTrace()
        trace.feed(content_trace())
        client = report('dummy')
        client.update(key_down=1, key_up=1, synthetic_events=True)
        gates = probe.assess(client, trace.summary(), 0, driver='dummy', reclaimed=True)
        self.assertEqual(gates['graphics_client'], 'completed')
        self.assertEqual(gates['shm_gate'], 'not_run')
        self.assertEqual(gates['input_gate'], 'synthetic')
        for conditions in [dict(timed_out=True, reclaimed=True), dict(reclaimed=False)]:
            self.assertEqual(probe.assess(report(), trace.summary(), 0, driver='wayland', **conditions)['shm_gate'], 'fail')
        self.assertEqual(probe.assess({**report(), 'pixel_checks': 0}, trace.summary(), 0, driver='wayland', reclaimed=True)['shm_gate'], 'fail')

    def test_probe_report_rejects_false_success_and_unbounded_payloads(self):
        good = report()
        self.assertEqual(probe.probe_report(json.dumps(good).encode()), good)
        for fields in [dict(frames=True), dict(completed='true'), dict(frames=4), dict(schema=True),
                       dict(typed_text='private'), dict(error='x' * 385), dict(linked_sdl='unknown'),
                       dict(stage='renderer'), dict(frames=2), dict(error='unexpected')]:
            with self.subTest(fields=list(fields)), self.assertRaises(ValueError):
                probe.probe_report(json.dumps({**good, **fields}).encode())
        with self.assertRaises(ValueError):
            probe.probe_report(b'x' * (probe.STDOUT_LIMIT + 1))

    def test_buffer_identity_reuse_is_not_mistaken_for_previous_shm(self):
        trace = probe.WaylandTrace()
        trace.feed(content_trace())
        trace.feed(b'wl_buffer@9.destroy()\nwl_surface@6.attach(wl_buffer@9, 0, 0)\nwl_surface@6.commit()\n')
        self.assertEqual(trace.summary()['content_unknown_commits'], 1)
        self.assertEqual(probe.assess(report(), trace.summary(), 0, driver='wayland', reclaimed=True)['shm_gate'], 'fail')

    def test_real_child_and_descendant_timeout_are_reaped_without_recording_stderr(self):
        with tempfile.TemporaryDirectory(prefix='eidolon-sdl-runner-') as value:
            root = Path(value)
            child = root / 'probe'
            child.write_text(f'#!{sys.executable}\nimport os,time,sys\npid=os.fork()\n'
                             'if pid==0:\n print("PRIVATE KEY TRACE",file=sys.stderr,flush=True)\n time.sleep(30)\n'
                             'else:\n time.sleep(30)\n')
            child.chmod(0o700)
            result = probe.run(child, driver='dummy', frames=3, timeout=.1)
            self.assertTrue(result['timed_out'])
            self.assertTrue(result['reaped'])
            self.assertTrue(result['group_reclaimed'])
            self.assertNotIn('PRIVATE', json.dumps(result))

    def test_real_process_report_and_stderr_flood_are_bounded(self):
        with tempfile.TemporaryDirectory(prefix='eidolon-sdl-output-') as value:
            child = Path(value) / 'probe'
            child.write_text(f'#!{sys.executable}\nimport json\nprint({json.dumps(json.dumps(report("dummy")))})\n')
            child.chmod(0o700)
            result = probe.run(child, driver='dummy', frames=3, timeout=2)
            self.assertEqual(result['graphics_client'], 'completed')
            self.assertEqual(result['shm_gate'], 'not_run')
            child.write_text(f'#!{sys.executable}\nimport os\nwhile True:os.write(1,b"x"*65536)\n')
            result = probe.run(child, driver='dummy', frames=3, timeout=2)
            self.assertTrue(result['output_overflow'])
            self.assertTrue(result['group_reclaimed'])
            self.assertEqual(result['graphics_client'], 'failed')


if __name__ == '__main__':
    unittest.main()
