package io.github.tinmachine.eidolon

import android.content.Context
import android.opengl.EGL14
import android.opengl.GLES20
import android.os.Process
import android.os.SystemClock
import android.system.ErrnoException
import android.system.Os
import android.system.OsConstants
import java.io.File
import java.util.concurrent.atomic.AtomicReference
import org.json.JSONArray
import org.json.JSONObject

/** 製品JNI・SSH・Waylandを使わないGPU計測FDの比較。SDK所有のFDには触れない。 */
object GpuFdProbe {
    private const val CYCLES = 10
    private const val HOLD_MS = 2000L
    private val gpuPath = Regex("/sys/devices/virtual/kgsl/kgsl/thread/([0-9]+)/active_time")
    fun snapshot(): JSONObject {
        val entries = checkNotNull(File("/proc/self/fd").list())
        val live = checkNotNull(File("/proc/self/task").list()).map { it.toInt() }.toSet()
        val gpu = JSONArray()
        for (fd in entries) {
            val path = try { Os.readlink("/proc/self/fd/$fd") }
            catch (error: ErrnoException) { if (error.errno == OsConstants.ENOENT) continue else throw error }
            val tid = gpuPath.matchEntire(path)?.groupValues?.get(1)?.toInt() ?: continue
            gpu.put(JSONObject().put("fd", fd.toInt()).put("tid", tid).put("alive", tid in live))
        }
        val maps = File("/proc/self/maps").readText()
        return JSONObject().put("elapsed_ms", SystemClock.elapsedRealtime()).put("fds", entries.size)
            .put("gpu", gpu).put("sdk_loaded", maps.contains("/libMetaSpatialSDK.so"))
            .put("product_native_loaded", maps.contains("/libeidolon_native.so"))
    }
    private fun eglCycle(): JSONObject {
        val row = JSONObject().put("tid", Process.myTid())
        val display = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY)
        check(display != EGL14.EGL_NO_DISPLAY)
        val version = IntArray(2)
        check(EGL14.eglInitialize(display, version, 0, version, 1))
        var context = EGL14.EGL_NO_CONTEXT
        var surface = EGL14.EGL_NO_SURFACE
        try {
            val configs = arrayOfNulls<android.opengl.EGLConfig>(1)
            val count = IntArray(1)
            check(EGL14.eglChooseConfig(display, intArrayOf(EGL14.EGL_SURFACE_TYPE, EGL14.EGL_PBUFFER_BIT,
                EGL14.EGL_RENDERABLE_TYPE, EGL14.EGL_OPENGL_ES2_BIT, EGL14.EGL_RED_SIZE, 8,
                EGL14.EGL_GREEN_SIZE, 8, EGL14.EGL_BLUE_SIZE, 8, EGL14.EGL_NONE), 0, configs, 0, 1, count, 0) && count[0] == 1)
            context = EGL14.eglCreateContext(display, configs[0], EGL14.EGL_NO_CONTEXT,
                intArrayOf(EGL14.EGL_CONTEXT_CLIENT_VERSION, 2, EGL14.EGL_NONE), 0)
            check(context != EGL14.EGL_NO_CONTEXT)
            surface = EGL14.eglCreatePbufferSurface(display, configs[0],
                intArrayOf(EGL14.EGL_WIDTH, 16, EGL14.EGL_HEIGHT, 16, EGL14.EGL_NONE), 0)
            check(surface != EGL14.EGL_NO_SURFACE)
            check(EGL14.eglMakeCurrent(display, surface, surface, context))
            val until = SystemClock.elapsedRealtime() + HOLD_MS
            var draws = 0
            do {
                GLES20.glClearColor(0f, 0f, 0f, 1f)
                GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT); GLES20.glFinish()
                check(GLES20.glGetError() == GLES20.GL_NO_ERROR)
                draws++; Thread.sleep(50)
            } while (SystemClock.elapsedRealtime() < until)
            row.put("draws", draws).put("during", snapshot())
        } finally {
            // SDKと共有するdisplayをeglTerminateしない。両比較とも同じ解放順。
            val unbound = EGL14.eglMakeCurrent(display, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT)
            val surfaceGone = surface == EGL14.EGL_NO_SURFACE || EGL14.eglDestroySurface(display, surface)
            val contextGone = context == EGL14.EGL_NO_CONTEXT || EGL14.eglDestroyContext(display, context)
            val released = EGL14.eglReleaseThread()
            row.put("unbound", unbound).put("surface_destroyed", surfaceGone)
                .put("context_destroyed", contextGone).put("thread_released", released)
            check(unbound && surfaceGone && contextGone && released) { "EGL_CLEANUP_FAILED" }
        }
        return row
    }
    private fun worker(action: () -> Unit) {
        val failure = AtomicReference<Throwable?>()
        val thread = Thread({ try { action() } catch (error: Throwable) { failure.set(error) } }, "gpu-fd-fixture")
        thread.start(); thread.join(60000)
        if (thread.isAlive) { thread.interrupt(); thread.join(5000) }
        check(!thread.isAlive) { "PROBE_THREAD_TIMEOUT" }
        failure.get()?.let { throw it }
    }
    fun run(context: Context, nonce: String, sdk: Boolean, visible: () -> Boolean = { true }): JSONObject {
        require(nonce.matches(Regex("[0-9a-f]{32}")))
        val result = JSONObject().put("schema", 1).put("case", "gpu-fds").put("nonce", nonce)
            .put("mode", if (sdk) "sdk" else "platform").put("pid", Process.myPid()).put("uid", Process.myUid())
            .put("build", BuildConfig.VERSION_CODE).put("build_info", JSONObject(BuildConfig.NATIVE_BUILD_INFO_JSON))
            .put("status", "running").put("acceptance", "not_run").put("cycles", CYCLES).put("hold_ms", HOLD_MS)
        fun checkpoint(stage: String) { result.put("stage", stage); save(context, nonce, result) }
        try {
            check(visible()) { "SDK_NOT_FOCUSED" }
            val baseline = snapshot()
            check(baseline.getBoolean("sdk_loaded") == sdk && !baseline.getBoolean("product_native_loaded"))
            result.put("baseline", baseline); checkpoint("empty_threads")
            val empty = JSONArray()
            repeat(3) { worker { empty.put(JSONObject().put("tid", Process.myTid())); Thread.sleep(HOLD_MS) } }
            result.put("empty_threads", empty).put("after_empty", snapshot()); checkpoint("fresh_threads")
            val fresh = JSONArray(); result.put("fresh", fresh)
            repeat(CYCLES) {
                check(visible()) { "SDK_NOT_FOCUSED" }
                worker { fresh.put(eglCycle()) }
                fresh.getJSONObject(it).put("after_join", snapshot()); checkpoint("fresh_threads")
            }
            Thread.sleep(30000)
            result.put("after_fresh_wait", snapshot()); checkpoint("reused_thread")
            val reused = JSONArray(); result.put("reused", reused)
            worker { repeat(CYCLES) { check(visible()) { "SDK_NOT_FOCUSED" }; reused.put(eglCycle()) } }
            result.put("after_reuse_join", snapshot()); checkpoint("settle")
            Thread.sleep(30000)
            check(visible()) { "SDK_NOT_FOCUSED" }
            val end = snapshot()
            check(!end.getBoolean("product_native_loaded") && end.getBoolean("sdk_loaded") == sdk)
            result.put("final", end).put("status", "observed")
        } catch (error: Throwable) {
            result.put("status", "failed").put("failure_class", error.javaClass.simpleName)
                .put("failure_site", error.stackTrace.firstOrNull()?.toString())
        }
        save(context, nonce, result)
        return result
    }
    private fun save(context: Context, nonce: String, result: JSONObject) {
        val root = File(context.noBackupFilesDir, "gpu-fd-probe"); check(root.isDirectory || root.mkdirs())
        Os.chmod(root.path, 448)
        val temporary = File(root, "$nonce.tmp")
        temporary.writeText(result.toString()); Os.chmod(temporary.path, 384)
        check(temporary.renameTo(File(root, "$nonce.json")))
    }
}
