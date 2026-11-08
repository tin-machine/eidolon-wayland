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
    fun imeProductProbeAllowed():Boolean=!running
    fun registerMeshes(activity: com.meta.spatial.toolkit.AppSystemActivity) = ThemeInputCandidates.register(activity)
    fun additionalPanels() = ThemeSurfaceProbe.registrations() + ImeProbe.registrations()
    fun sceneClosing() { ImeProbe.sceneClosing(); ThemeSurfaceProbe.sceneClosing() }
    fun imeProbeActivity(activity: android.app.Activity) { ImeProbe.activity(activity) }
    fun imeProbeActive(): Boolean = ImeProbe.isActive()
    fun imeProbePhysicalKey() = ImeProbe.physicalKey()
    private val ui = Handler(Looper.getMainLooper())
    private var running = false
    private val consumedNonces = ArrayDeque<String>()
    private val gtkRecoveries=LinuxGtkRecoveryBudget()
    fun commandComplete(request: String, code: String) { KeyboardCase.commandComplete(request, code);WindowControlsCase.commandComplete(request,code);WindowRecenterCase.commandComplete(request,code);WindowSlotsCase.commandComplete(request,code);LinuxShmSdkCase.commandComplete(NativeSessionHost.currentHandle(),request,code);LinuxGtkSdkCase.commandComplete(NativeSessionHost.currentHandle(),request,code);FreedoomGuiCase.commandComplete(NativeSessionHost.currentHandle(),request,code) }
    fun windowControlRequested(action:String,runtime:Long,target:String,revision:Long,request:String,code:Int) {
        WindowControlsCase.requested(action,runtime,target,revision,request,code);WindowRecenterCase.requested(action,runtime,target,revision,request,code)
    }
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
    fun sceneReady(adapter: SpatialPanelAdapter, scene: () -> Scene, recreate: () -> Unit) {
        ImeProbe.sceneReady()
        LayoutSdkCase.sceneReady(adapter,scene,recreate)
    }
    fun accept(intent: Intent?, adapter: SpatialPanelAdapter, context: Context, scene: () -> Scene, recreate: () -> Unit) {
        val name = intent?.getStringExtra("case") ?: return
        val nonce = intent.getStringExtra("nonce") ?: return
        if (!nonce.matches(Regex("[0-9a-f]{32}"))) return
        if (intent.extras?.keySet()?.any { it != "case" && it != "nonce" } == true) return
        if (name == "linux-gtk-stop") {
            intent.removeExtra("case");intent.removeExtra("nonce")
            LinuxGtkSdkCase.stop(nonce);return
        }
        if (name == "linux-shm-sdk-stop") {
            intent.removeExtra("case"); intent.removeExtra("nonce")
            LinuxShmSdkCase.stop(nonce); return
        }
        // pointer配送停止中も、開始nonceだけを持つ明示終了を受け付ける。
        if (name == "ime-editor-probe-stop") {
            intent.removeExtra("case"); intent.removeExtra("nonce")
            ImeProbe.stop(nonce)
            return
        }
        if (name == "ime-editor-probe-exercise") {
            intent.removeExtra("case"); intent.removeExtra("nonce")
            ImeProbe.exercise(nonce)
            return
        }
        if (name == "ime-editor-probe-sdk-tap") {
            intent.removeExtra("case"); intent.removeExtra("nonce")
            ImeProbe.sdkTap(nonce)
            return
        }
        if (running || ImeProductCommandReceiver.probeActive() || name !in setOf("viewer-pose", "window-recenter", "window-size", "linux-gtk-input", "linux-gtk-dialog", "linux-gtk-input-recover", "linux-gtk-dialog-recover", "linux-shm-sdk", "window-slots", "window-move", "window-controls", "extensions", "stop", "theme-sdk", "theme-surface", "two-windows", "layout", "resize", "pointer", "keyboard", "ray", "foot", "surface", "pattern", "surface-cycle", "ssh-product", "ime-editor-probe", "ime-terminal")) return
        intent.removeExtra("case"); intent.removeExtra("nonce")
        val gtkRecovery=name in setOf("linux-gtk-input-recover","linux-gtk-dialog-recover")
        if(gtkRecovery) {if(!gtkRecoveries.claim(name,nonce)) return} else if(nonce in consumedNonces) return
        if(nonce !in consumedNonces) {
            if(consumedNonces.size==64) consumedNonces.removeFirst()
            consumedNonces.addLast(nonce)
        }
        running = true
        if (name in setOf("linux-gtk-input","linux-gtk-dialog","linux-gtk-input-recover","linux-gtk-dialog-recover")) LinuxGtkSdkCase.start(name,nonce,context,adapter) {running=false}
        else if (name == "linux-shm-sdk") LinuxShmSdkCase.start(nonce,context,adapter) {running=false}
        else if (name == "viewer-pose") ViewerPoseCase.start(nonce,scene) {running=false}
        else if (name == "ime-terminal") ImeTerminalCase.start(nonce,scene) {running=false}
        else if (name == "ime-editor-probe") ImeProbe.start(nonce,context,scene) {running=false}
        else if (name == "stop") SdkCaseEnvironment.start(name,nonce,context,{done->LifecycleSdkCase.start(nonce,adapter,done)}) {running=false}
        else if (name == "theme-surface") ThemeSurfaceProbe.start(nonce,scene) {running=false}
        else if (name in setOf("layout","two-windows","theme-sdk")) LayoutSdkCase.start(nonce,context,adapter,scene,recreate,name) {running=false}
        else if(name in setOf("window-recenter","window-size","window-slots","window-move","window-controls","extensions","resize","pointer","keyboard","ray","foot")) SdkCaseEnvironment.start(name,nonce,context,{ done ->
            when(name) {
                "window-recenter" -> WindowRecenterCase.start(nonce,adapter,scene,done)
                "window-size" -> WindowControlsCase.startSize(nonce,adapter,scene,done)
                "window-slots" -> WindowSlotsCase.start(nonce,adapter,done)
                "window-move" -> WindowControlsCase.startMove(nonce,adapter,scene,done)
                "window-controls" -> WindowControlsCase.start(nonce,adapter,scene,done)
                "extensions" -> ExtensionsSdkCase.start(nonce,done)
                "resize" -> ResizeCase.start(nonce,adapter,scene,done)
                "pointer" -> PointerCase.start(nonce,adapter,scene,done)
                "keyboard" -> KeyboardCase.start(nonce,adapter,done)
                "ray" -> RayCase.start(nonce,adapter,scene,done)
                "foot" -> FootSdkCase.start(nonce,adapter,done)
            }
        }) {running=false}
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
