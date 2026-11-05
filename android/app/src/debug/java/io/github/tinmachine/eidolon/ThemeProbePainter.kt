package io.github.tinmachine.eidolon

import android.opengl.EGL14
import android.opengl.EGLConfig
import android.opengl.GLES30
import android.os.Process
import android.view.Surface
import java.nio.ByteBuffer
import java.util.concurrent.atomic.AtomicBoolean
import org.json.JSONArray
import org.json.JSONObject

/** 固定色のみ。SDK/UI threadのEGL current状態を変更せず、SDKのSurfaceをreleaseしない。 */
internal class ThemeProbePainter(private val surfaces: List<Pair<Surface, Boolean>>) {
    private val stopping = AtomicBoolean(false)
    @Volatile var report: JSONObject? = null
        private set
    private val worker = Thread({ paint() }, "eidolon-alpha-probe")
    fun start() = worker.start()
    fun stop() { stopping.set(true) }
    fun finished() = !worker.isAlive
    private fun paint() {
        val result = JSONObject().put("thread", Process.myTid()).put("result", "fail")
        val rows = JSONArray()
        val display = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY)
        var context = EGL14.EGL_NO_CONTEXT
        var target = EGL14.EGL_NO_SURFACE
        try {
            check(display != EGL14.EGL_NO_DISPLAY)
            check(EGL14.eglInitialize(display, IntArray(2), 0, IntArray(2), 0))
            val configs = arrayOfNulls<EGLConfig>(1)
            val count = IntArray(1)
            check(EGL14.eglChooseConfig(display, intArrayOf(
                EGL14.EGL_RENDERABLE_TYPE, 0x40, EGL14.EGL_SURFACE_TYPE, EGL14.EGL_WINDOW_BIT,
                EGL14.EGL_RED_SIZE,8,EGL14.EGL_GREEN_SIZE,8,EGL14.EGL_BLUE_SIZE,8,EGL14.EGL_ALPHA_SIZE,8,
                EGL14.EGL_NONE),0,configs,0,1,count,0) && count[0] == 1)
            val config = checkNotNull(configs[0])
            context = EGL14.eglCreateContext(display,config,EGL14.EGL_NO_CONTEXT,intArrayOf(EGL14.EGL_CONTEXT_CLIENT_VERSION,3,EGL14.EGL_NONE),0)
            check(context != EGL14.EGL_NO_CONTEXT)
            for ((index,pair) in surfaces.withIndex()) {
                check(!stopping.get())
                target = EGL14.eglCreateWindowSurface(display,config,pair.first,intArrayOf(EGL14.EGL_NONE),0)
                check(target != EGL14.EGL_NO_SURFACE)
                check(EGL14.eglMakeCurrent(display,target,target,context))
                val dimension = IntArray(1)
                check(EGL14.eglQuerySurface(display,target,EGL14.EGL_WIDTH,dimension,0) && dimension[0] == 256)
                check(EGL14.eglQuerySurface(display,target,EGL14.EGL_HEIGHT,dimension,0) && dimension[0] == 256)
                result.put("renderer",GLES30.glGetString(GLES30.GL_RENDERER))
                val pixels = JSONArray()
                repeat(3) {
                    check(!stopping.get())
                    GLES30.glViewport(0,0,256,256)
                    GLES30.glDisable(GLES30.GL_SCISSOR_TEST)
                    if (pair.second) GLES30.glClearColor(0f,0f,0f,0f) else GLES30.glClearColor(0f,0f,1f,1f)
                    GLES30.glClear(GLES30.GL_COLOR_BUFFER_BIT)
                    if (pair.second) {
                        GLES30.glEnable(GLES30.GL_SCISSOR_TEST)
                        GLES30.glScissor(64,64,128,128)
                        GLES30.glClearColor(0.5f,0f,0f,0.5f)
                        GLES30.glClear(GLES30.GL_COLOR_BUFFER_BIT)
                        GLES30.glDisable(GLES30.GL_SCISSOR_TEST)
                    }
                    for ((x,y) in listOf(16 to 128,128 to 128)) {
                        val bytes = ByteBuffer.allocateDirect(4)
                        GLES30.glReadPixels(x,y,1,1,GLES30.GL_RGBA,GLES30.GL_UNSIGNED_BYTE,bytes)
                        val actual = (0..3).map { bytes.get(it).toInt() and 255 }
                        val expected = if (!pair.second) listOf(0,0,255,255) else if(x==16) listOf(0,0,0,0) else listOf(128,0,0,128)
                        check(actual.zip(expected).all { (a,b) -> kotlin.math.abs(a-b)<=1 })
                        pixels.put(JSONArray(actual))
                    }
                    check(GLES30.glGetError() == GLES30.GL_NO_ERROR)
                    check(EGL14.eglSwapBuffers(display,target))
                }
                rows.put(JSONObject().put("index",index).put("front",pair.second).put("pixels",pixels))
                check(EGL14.eglMakeCurrent(display,EGL14.EGL_NO_SURFACE,EGL14.EGL_NO_SURFACE,EGL14.EGL_NO_CONTEXT))
                check(EGL14.eglDestroySurface(display,target)); target=EGL14.EGL_NO_SURFACE
            }
            result.put("result","pass").put("surfaces",rows)
        } catch (failure: Exception) {
            result.put("failure_class",failure.javaClass.simpleName).put("failure_site",failure.stackTrace.firstOrNull()?.toString())
        } finally {
            EGL14.eglMakeCurrent(display,EGL14.EGL_NO_SURFACE,EGL14.EGL_NO_SURFACE,EGL14.EGL_NO_CONTEXT)
            val surfaceReleased = target == EGL14.EGL_NO_SURFACE || EGL14.eglDestroySurface(display,target)
            val contextReleased = context == EGL14.EGL_NO_CONTEXT || EGL14.eglDestroyContext(display,context)
            val threadReleased = EGL14.eglReleaseThread()
            // DEFAULT_DISPLAYはSDKと共有するためeglTerminateしない。
            result.put("owned_egl_released",surfaceReleased && contextReleased && threadReleased)
            if (!surfaceReleased || !contextReleased || !threadReleased) result.put("result","fail")
            report=result
        }
    }
}
