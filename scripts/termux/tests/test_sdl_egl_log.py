import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('sdl_egl_log_probe', Path(__file__).resolve().parents[1] / 'eidolon-sdl-probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class EglLogTests(unittest.TestCase):
    def test_fragmented_known_messages_keep_only_counts_and_first_failure(self):
        data = (b"MESA-EGL: Found 'LIBGL_ALWAYS_SOFTWARE' set, will use a CPU renderer\n"
                b'MESA-EGL: egl: failed to create dri2 screen\n'
                b'MESA-LOADER: failed to open /PRIVATE/path.so: private symbol\n'
                b'MESA-EGL: egl: failed to create dri2 screen\n'
                b'PRIVATE OTHER MESSAGE\n')
        value = probe.EglLogSummary(True)
        for offset in range(0, len(data), 7): value.feed(data[offset:offset + 7])
        result = value.summary()
        self.assertEqual(result['known'], dict(forced_software=1, dri2_screen_failed=2, driver_load_failed=1))
        self.assertEqual(result['first_failure'], 'dri2_screen_failed')
        self.assertEqual(result['lines'], 5)
        self.assertEqual(result['unmatched_lines'], 1)
        self.assertTrue(result['complete'])
        self.assertEqual(result['bytes'], len(data))
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('symbol', json.dumps(result))

    def test_long_line_tail_is_never_classified_as_a_fresh_message(self):
        value = probe.EglLogSummary(True)
        message = b'egl: failed to create dri2 screen\n'
        value.feed(b'x' * (probe.LINE_LIMIT + 1))
        value.feed(message + message)
        result = value.summary()
        self.assertEqual(result['known'], dict(dri2_screen_failed=1))
        self.assertEqual(result['truncated_lines'], 1)
        self.assertFalse(result['complete'])
        self.assertEqual(result['lines'], 2)

    def test_stream_limit_and_partial_final_line_are_not_complete(self):
        value = probe.EglLogSummary(True)
        with patch.object(probe, 'TRACE_LIMIT', 128): value.feed(b'x' * 129)
        self.assertEqual(value.summary()['bytes'], 129)
        self.assertFalse(value.summary()['complete'])
        self.assertEqual(value.summary()['known'], {})
        self.assertNotIn('x' * 16, json.dumps(value.summary()))
        value = probe.EglLogSummary(False)
        value.feed(b'egl: failed to create dri2 screen')
        self.assertEqual(value.summary()['known'], {})
        self.assertFalse(value.summary()['complete'])

    def test_real_stage_keeps_api_failure_and_cleanup_without_wayland_trace(self):
        api = dict(schema=1, probe='eidolon-sdl-egl-stage-v1', requested_stage='gl-load', stage='load',
                   completed=False, error_class='egl_initialize_failed', fd_before=3)
        cleanup = dict(schema=1, probe='eidolon-sdl-egl-cleanup-v1', requested_stage='gl-load', completed=True, fd_after=7)
        with tempfile.TemporaryDirectory(prefix='eidolon-egl-log-') as temporary:
            child = Path(temporary) / 'stage'
            child.write_text(f'#!{sys.executable}\nimport json,os,sys\n'
                             'assert "WAYLAND_DEBUG" not in os.environ\n'
                             + f'print({json.dumps(json.dumps(api))},flush=True)\n'
                             + f'print({json.dumps(json.dumps(cleanup))},flush=True)\n'
                             'print("MESA-EGL: egl: failed to create dri2 screen PRIVATE",file=sys.stderr)\n'
                             'print("MESA-EGL: failed to add any EGLConfigs",file=sys.stderr)\n'
                             'raise SystemExit(1)\n')
            child.chmod(0o700)
            with patch.dict(os.environ, dict(EGL_LOG_LEVEL='debug', WAYLAND_DEBUG='client')):
                result = probe.run(child, driver='wayland', stage='gl-load', timeout=2)
        self.assertEqual(result['returncode'], 1)
        self.assertEqual(result['client']['api'], api)
        self.assertEqual(result['client']['cleanup'], cleanup)
        self.assertEqual(result['diagnostic_gate'], 'observed')
        self.assertEqual(result['graphics_client'], 'not_run')
        self.assertEqual(result['shm_gate'], 'not_run')
        self.assertTrue(result['reaped'] and result['group_reclaimed'])
        self.assertEqual(result['trace']['bound_globals'], {})
        self.assertTrue(result['egl_log']['enabled'])
        self.assertEqual(result['egl_log']['known'], dict(dri2_screen_failed=1, egl_configs_missing=1))
        self.assertNotIn('PRIVATE', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
