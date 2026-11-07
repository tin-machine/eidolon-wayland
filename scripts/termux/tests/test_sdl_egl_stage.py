import importlib.util
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('sdl_probe_stage', Path(__file__).resolve().parents[1] / 'eidolon-sdl-probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


HEADER = r'''
typedef struct SDL_Window { int value; } SDL_Window;
typedef void *SDL_GLContext;
enum { SDL_INIT_VIDEO=32, SDL_GL_CONTEXT_PROFILE_MASK=21, SDL_GL_CONTEXT_PROFILE_ES=4,
       SDL_GL_CONTEXT_MAJOR_VERSION=17, SDL_GL_CONTEXT_MINOR_VERSION=18,
       SDL_WINDOWPOS_UNDEFINED=0, SDL_WINDOW_OPENGL=2, SDL_WINDOW_HIDDEN=8 };
int SDL_Init(unsigned); void SDL_StopTextInput(void); int SDL_GL_SetAttribute(int,int);
int SDL_GL_LoadLibrary(const char *); void SDL_GL_UnloadLibrary(void);
SDL_Window *SDL_CreateWindow(const char *,int,int,int,int,unsigned);
SDL_GLContext SDL_GL_CreateContext(SDL_Window *); void SDL_GL_DeleteContext(SDL_GLContext);
void SDL_DestroyWindow(SDL_Window *); void SDL_Quit(void); const char *SDL_GetError(void);
'''

FAKE = r'''
#include <SDL.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
static void note(const char *name) {
    FILE *f=fopen(getenv("TEST_CALLS"),"a"); if(f){fprintf(f,"%s\n",name);fclose(f);}
}
static int fail(const char *name) { const char *s=getenv("TEST_FAIL"); return s && !strcmp(s,name); }
int SDL_Init(unsigned value) {(void)value;note("init");note(getenv("WAYLAND_DEBUG")?"raw-debug-on":"raw-debug-off");return fail("init")?-1:0;}
void SDL_StopTextInput(void) {note("stop-text");}
int SDL_GL_SetAttribute(int key,int value) {char s[64];snprintf(s,sizeof(s),"attribute:%d:%d",key,value);note(s);return fail("attributes")?-1:0;}
int SDL_GL_LoadLibrary(const char *value) {(void)value;note("load");return fail("load")?-1:0;}
void SDL_GL_UnloadLibrary(void) {note("unload");}
SDL_Window *SDL_CreateWindow(const char *title,int x,int y,int w,int h,unsigned flags) {
    static SDL_Window window; (void)title;(void)x;(void)y;(void)w;(void)h;
    note(flags==(SDL_WINDOW_OPENGL|SDL_WINDOW_HIDDEN)?"window:hidden-opengl":"wrong-flags");
    return fail("window")?NULL:&window;
}
SDL_GLContext SDL_GL_CreateContext(SDL_Window *window) {note("context");return fail("context")?NULL:window;}
void SDL_GL_DeleteContext(SDL_GLContext context) {(void)context;note("delete-context");}
void SDL_DestroyWindow(SDL_Window *window) {(void)window;note("destroy-window");}
void SDL_Quit(void) {note("quit");if(getenv("TEST_CRASH_CLEANUP"))raise(SIGSEGV);if(getenv("TEST_HOLD_CLEANUP"))sleep(30);}
const char *SDL_GetError(void) {const char *e=getenv("TEST_ERROR");return e?e:"PRIVATE UNKNOWN ERROR";}
'''


class SdlEglStageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which('cc') is None:
            raise unittest.SkipTest('C compiler required for the SDL API fixture')
        cls.temporary = tempfile.TemporaryDirectory(prefix='eidolon-egl-stage-')
        cls.root = Path(cls.temporary.name)
        (cls.root / 'SDL.h').write_text(HEADER)
        (cls.root / 'fake.c').write_text(FAKE)
        # productionのstage関数自体をcompileする。render mainはSDL fixtureの範囲外。
        source = (Path(__file__).resolve().parents[2] / 'fixtures/sdl-wayland-probe.c').read_text().split('int main(', 1)[0]
        (cls.root / 'stage.c').write_text(source + '\nint main(int argc,char **argv){if(argc!=2)return 64;return egl_stage_probe(argv[1]);}\n')
        cls.binary = cls.root / 'stage'
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                        '-I', str(cls.root), str(cls.root / 'stage.c'), str(cls.root / 'fake.c'), '-o', str(cls.binary)],
                       check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.calls = self.root / ('calls-' + self._testMethodName)
        self.environment = dict(TEST_CALLS=str(self.calls), TEST_ERROR='Could not initialize EGL PRIVATE PATH')

    def direct(self, stage, **environment):
        def no_core():
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        result = subprocess.run([str(self.binary), stage], capture_output=True, timeout=3,
                                env={**os.environ, **self.environment, **environment}, preexec_fn=no_core)
        return result, probe.stage_report(result.stdout, stage)

    def test_each_api_and_stage_stops_before_the_next_operation(self):
        for api in ('gl', 'gles'):
            for operation in ('load', 'window', 'context'):
                self.calls.write_text('')
                stage = api + '-' + operation
                result, report = self.direct(stage)
                calls = self.calls.read_text().splitlines()
                self.assertEqual(result.returncode, 0)
                self.assertTrue(report['api']['completed'])
                self.assertEqual(report['api']['stage'], operation)
                self.assertEqual(report['cleanup_status'], 'observed')
                self.assertIn('attribute:21:' + ('4' if api == 'gles' else '0'), calls)
                self.assertIn('attribute:17:2', calls)
                self.assertIn('attribute:18:' + ('0' if api == 'gles' else '1'), calls)
                self.assertEqual('window:hidden-opengl' in calls, operation != 'load')
                self.assertEqual('context' in calls, operation == 'context')
                self.assertEqual('delete-context' in calls, operation == 'context')
                self.assertNotIn('wrong-flags', calls)

    def test_first_failure_is_classified_without_error_text_or_later_calls(self):
        for reached in ('init', 'attributes', 'load', 'window', 'context'):
            self.calls.write_text('')
            result, report = self.direct('gl-context', TEST_FAIL=reached)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(report['api']['stage'], reached)
            self.assertEqual(report['api']['error_class'], 'egl_initialize_failed')
            self.assertNotIn('PRIVATE', result.stdout.decode())
            calls = self.calls.read_text().splitlines()
            if reached in {'init', 'attributes', 'load'}:
                self.assertNotIn('window:hidden-opengl', calls)
            if reached != 'context': self.assertNotIn('context', calls)

    def test_unknown_error_is_fixed_classification(self):
        result, report = self.direct('gles-load', TEST_FAIL='load', TEST_ERROR='SECRET free text')
        self.assertEqual(report['api']['error_class'], 'unclassified_sdl_error')
        self.assertNotIn('SECRET', result.stdout.decode())

    def test_cleanup_crash_keeps_first_failure_but_is_only_partial(self):
        result, report = self.direct('gl-load', TEST_FAIL='load', TEST_CRASH_CLEANUP='1')
        self.assertEqual(result.returncode, -signal.SIGSEGV)
        self.assertEqual(report['api']['error_class'], 'egl_initialize_failed')
        self.assertEqual(report['cleanup_status'], 'missing')
        self.assertIsNone(report['cleanup'])

    def test_runner_observes_negative_stage_without_graphics_or_raw_debug(self):
        # runnerのbinary argvは--frames 120 --stage ...なのでfixture入口だけ対応させる。
        wrapper = self.root / 'runner-child'
        wrapper.write_text('#!/bin/sh\nulimit -c 0\nexec ' + str(self.binary) + ' "$4"\n')
        wrapper.chmod(0o700)
        with patch.dict(os.environ, {**self.environment, 'TEST_FAIL': 'load', 'WAYLAND_DEBUG': 'client'}):
            result = probe.run(wrapper, driver='wayland', stage='gl-load', timeout=2)
        self.assertEqual(result['returncode'], 1)
        self.assertEqual(result['diagnostic_gate'], 'observed')
        self.assertEqual(result['graphics_client'], 'not_run')
        self.assertEqual(result['shm_gate'], 'not_run')
        self.assertEqual(result['client']['api']['error_class'], 'egl_initialize_failed')
        self.assertIn('raw-debug-off', self.calls.read_text())
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertTrue(result['reaped'] and result['group_reclaimed'])

    def test_runner_keeps_initial_error_and_sigsegv_when_cleanup_crashes(self):
        wrapper = self.root / 'runner-crash-child'
        wrapper.write_text('#!/bin/sh\nulimit -c 0\nexec ' + str(self.binary) + ' "$4"\n')
        wrapper.chmod(0o700)
        with patch.dict(os.environ, {**self.environment, 'TEST_FAIL': 'load', 'TEST_CRASH_CLEANUP': '1'}):
            result = probe.run(wrapper, driver='wayland', stage='gl-load', timeout=2)
        self.assertEqual(result['returncode'], -signal.SIGSEGV)
        self.assertEqual(result['client']['api']['error_class'], 'egl_initialize_failed')
        self.assertEqual(result['diagnostic_gate'], 'partial')
        self.assertEqual(result['graphics_client'], 'not_run')
        self.assertTrue(result['reaped'] and result['group_reclaimed'])

    def test_parser_rejects_unknown_fields_and_preserves_api_on_bad_cleanup(self):
        result, report = self.direct('gl-load', TEST_FAIL='load')
        first = report['api']
        for changed in ({**first, 'error_class': 'PRIVATE'}, {**first, 'text': 'PRIVATE'},
                        {**first, 'completed': 'true'}, {**first, 'requested_stage': 'gles-load'}):
            with self.assertRaises(ValueError):
                probe.stage_report(json.dumps(changed).encode(), 'gl-load')
        parsed = probe.stage_report(json.dumps(first).encode() + b'\nPRIVATE CLEANUP', 'gl-load')
        self.assertEqual(parsed['api'], first)
        self.assertEqual(parsed['cleanup_status'], 'invalid')
        self.assertNotIn('PRIVATE', json.dumps(parsed))

    def test_stage_stderr_limit_cannot_become_observed(self):
        _, parsed = self.direct('gl-load', TEST_FAIL='load')
        child = self.root / 'stage-output-flood'
        first = json.dumps(parsed['api'])
        cleanup = json.dumps(parsed['cleanup'])
        child.write_text('#!' + os.sys.executable + '\nimport os\n'
                         + 'os.write(1,' + repr((first + '\n' + cleanup + '\n').encode()) + ')\n'
                         + 'while True:os.write(2,b"PRIVATE"*16384)\n')
        child.chmod(0o700)
        result = probe.run(child, driver='wayland', stage='gl-load', timeout=2)
        self.assertGreater(result['trace']['bytes'], probe.TRACE_LIMIT)
        self.assertEqual(result['diagnostic_gate'], 'partial')
        self.assertEqual(result['client']['api']['error_class'], 'egl_initialize_failed')
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertTrue(result['reaped'] and result['group_reclaimed'])


if __name__ == '__main__':
    unittest.main()
