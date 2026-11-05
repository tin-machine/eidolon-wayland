package io.github.tinmachine.eidolon

import android.content.Intent
import android.content.Context
import android.system.Os
import com.meta.spatial.runtime.Scene
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.os.Debug
import java.io.File
import org.json.JSONArray
import org.json.JSONObject

/** 実際のSDK Surfaceを使う固定case。UIをblockせず段階ごとに待つ。 */
object BuildHooks {
    private val ui = Handler(Looper.getMainLooper())
    private var running = false
    private val consumedNonces = ArrayDeque<String>()
    fun commandComplete(request: String, code: String) { KeyboardCase.commandComplete(request, code) }
    @Volatile var preferencesFixture: String? = null
    private var heldPanel: String? = null
    fun holdPanel(window: String?) { heldPanel=window }
    fun holdPanelCreation(window: String): Boolean = heldPanel==window
    fun runtimeConfiguration(configuration: String): String {
        val nonce=preferencesFixture ?: return configuration
        check(nonce.matches(Regex("[0-9a-f]{32}")))
        val config=JSONObject(configuration);val root=File(config.getString("preferences_dir")).canonicalFile;val parent=File(root,"sdk-layout-fixtures/$nonce")
        parent.mkdirs();check(parent.canonicalFile==parent.absoluteFile);Os.chmod(parent.parentFile!!.absolutePath,448);Os.chmod(parent.absolutePath,448)
        return config.put("preferences_dir",parent.canonicalPath).toString()
    }
    fun sceneReady(adapter: SpatialPanelAdapter, scene: () -> Scene, recreate: () -> Unit) { LayoutSdkCase.sceneReady(adapter,scene,recreate) }
    fun accept(intent: Intent?, adapter: SpatialPanelAdapter, context: Context, scene: () -> Scene, recreate: () -> Unit) {
        val name = intent?.getStringExtra("case") ?: return
        val nonce = intent.getStringExtra("nonce") ?: return
        if (running || name !in setOf("two-windows", "layout", "resize", "pointer", "keyboard", "ray", "foot", "surface", "pattern", "surface-cycle", "ssh-product") || !nonce.matches(Regex("[0-9a-f]{32}"))) return
        if (intent.extras?.keySet()?.any { it != "case" && it != "nonce" } == true) return
        intent.removeExtra("case"); intent.removeExtra("nonce")
        if(nonce in consumedNonces) return
        if(consumedNonces.size==64) consumedNonces.removeFirst()
        consumedNonces.addLast(nonce)
        running = true
        if (name in setOf("layout","two-windows")) LayoutSdkCase.start(nonce,context,adapter,scene,recreate,name) {running=false}
        else if (name == "resize") ResizeCase.start(nonce,adapter,scene) { running=false }
        else if (name == "pointer") PointerCase.start(nonce, adapter, scene) { running = false }
        else if (name == "keyboard") KeyboardCase.start(nonce, adapter) { running = false }
        else if (name == "ray") RayCase.start(nonce, adapter, scene) { running = false }
        else if (name == "foot") FootSdkCase.start(nonce, adapter) { running = false }
        else if (name == "ssh-product") ProductSshCase.start(nonce) { running = false }
        else Case(name, nonce, adapter).start()
    }
    private class Case(val name: String, val nonce: String, val adapter: SpatialPanelAdapter) : Runnable {
        val result = JSONObject().put("case", name).put("nonce", nonce).put("result", "running")
        val cycles = JSONArray()
        val count = when (name) { "surface" -> 10; "surface-cycle" -> 20; else -> 1 }
        var previous: JSONObject? = null
        var deadline = SystemClock.elapsedRealtime() + 15000
        var stage = "frames"
        var until = 0L
        var pausedFrames = 0L
        var duplicateBaseline = 0L
        var staleBaseline = 0L
        fun start() { NativeSessionHost.publishCheck(result); ui.post(this) }
        override fun run() {
            try {
                check(SystemClock.elapsedRealtime() < deadline) { "SURFACE_TIMEOUT" }
                val render = adapter.currentRender()
                val targets = render?.getJSONArray("targets")
                val target = if (targets != null && targets.length() == 1) targets.getJSONObject(0) else null
                when (stage) {
                    "frames" -> if (target != null && target.getLong("frames") >= 3 && target.getBoolean("readback")) {
                        val key = target.getJSONObject("key")
                        if (previous == null || key.getString("generation") != previous!!.getString("generation")) {
                            check(target.getInt("width") == 1024 && target.getInt("height") == 768)
                            check(adapter.metrics().getInt("listeners") == 1)
                            val sample = JSONObject(target.toString()).put("resources", resources())
                            cycles.put(sample); previous = JSONObject(key.toString())
                            result.put("cycles", cycles); NativeSessionHost.publishCheck(result)
                            if (cycles.length() < count) { check(adapter.recreate()); deadline = SystemClock.elapsedRealtime() + 10000 }
                            else {
                                duplicateBaseline = render!!.getLong("duplicates")
                                check(adapter.duplicateCurrent() == NativeBridge.ACCEPTED)
                                staleBaseline = render.getLong("stale")
                                val old = JSONObject(key.toString()).put("generation", "1")
                                if (key.getString("generation") == "1") old.put("scene", "9223372036854775807")
                                check(NativeSessionHost.detachSurface(old) == NativeBridge.ACCEPTED)
                                stage = "stale"
                            }
                        }
                    }
                    "stale" -> if (render != null && render.getLong("duplicates") > duplicateBaseline && render.getLong("stale") > staleBaseline) {
                        check(target != null && target.getJSONObject("key").getString("generation") == previous!!.getString("generation"))
                        NativeSessionHost.setActivityVisible(false)
                        stage = "pause_settle"; until = SystemClock.elapsedRealtime() + 200
                    }
                    "pause_settle" -> if (SystemClock.elapsedRealtime() >= until && render?.getBoolean("paused") == true) {
                        pausedFrames = checkNotNull(target).getLong("frames")
                        stage = "pause_check"; until = SystemClock.elapsedRealtime() + 300
                    }
                    "pause_check" -> if (SystemClock.elapsedRealtime() >= until) {
                        check(checkNotNull(target).getLong("frames") == pausedFrames)
                        result.put("paused_frames_stable", true).put("stale_detach_safe", true).put("duplicate_safe", true)
                        NativeSessionHost.setActivityVisible(true); stage = "resume"
                    }
                    "resume" -> if (target != null && target.getLong("frames") > pausedFrames + 2) {
                        val metrics = adapter.metrics()
                        check(metrics.getInt("created") - metrics.getInt("destroyed") == 1 && metrics.getInt("listeners") == 1)
                        result.put("result", "pass").put("adapter", metrics).put("render", render)
                            .put("hmd", "not_run").put("physical_input", "not_run")
                        finish(); return
                    }
                }
                ui.postDelayed(this, 40)
            } catch (failure: Exception) {
                NativeSessionHost.setActivityVisible(true)
                result.put("result", "fail").put("stage", stage).put("failure_class", failure.javaClass.simpleName)
                    .put("adapter", adapter.metrics()).put("render", adapter.currentRender())
                finish()
            }
        }
        private fun finish() { NativeSessionHost.publishCheck(result); running = false }
        private fun resources(): JSONObject = JSONObject().put("fds", File("/proc/self/fd").list()?.size ?: -1)
            .put("native_heap_bytes", Debug.getNativeHeapAllocatedSize()).put("elapsed_ms", SystemClock.elapsedRealtime())
    }
}
