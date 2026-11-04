package io.github.tinmachine.eidolon.probe

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.Surface
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.unit.dp
import com.meta.spatial.compose.ComposeFeature
import com.meta.spatial.compose.ComposeViewPanelRegistration
import com.meta.spatial.core.*
import com.meta.spatial.runtime.ReferenceSpace
import com.meta.spatial.runtime.SessionState
import com.meta.spatial.toolkit.*
import com.meta.spatial.vr.VRFeature
import org.json.JSONObject
import java.io.File
import java.util.UUID
import java.util.concurrent.Executors

/** 方式の成立だけを確認する独立debug APK。製品runtime/credentialは持たない。 */
class ProbeActivity : AppSystemActivity() {
    private val handler = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor()
    private val cleanupIds = mutableSetOf<Long>()
    private val fileLock = Any()
    private var nativePanel: Entity? = null
    private var statusPanel: Entity? = null
    private var pendingSurface: Pair<Long, Surface>? = null
    private var runId = 0L
    private var sceneReady = false
    private var closing = false
    private var transitioning by mutableStateOf(false)
    private var visible = false
    private var generation = 0
    private var completedCycles = 0
    private var requestedCycles = 0
    private var nonce = "starting"
    private var nativeInfo = JSONObject()
    private var helperResult = JSONObject().put("pending", true)
    private var transportResult = JSONObject().put("status", "not_run")
    private var compositorResult = JSONObject().put("pending", true)
    private var statusText by mutableStateOf("Starting native probe")

    override fun registerFeatures(): List<SpatialFeature> = listOf(VRFeature(this), ComposeFeature())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        nonce = intent.getStringExtra("run_nonce")?.takeIf { it.matches(Regex("[A-Za-z0-9_-]{1,64}")) } ?: UUID.randomUUID().toString()
        requestedCycles = intent.getIntExtra("cycles", 0).coerceIn(0, 20)
        nativeInfo = JSONObject(NativeProbe.buildInfo())
        event("native_ready", nativeInfo)
        worker.execute {
            val result = runCatching { JSONObject(NativeProbe.helper(applicationInfo.nativeLibraryDir, filesDir.absolutePath)) }
                .getOrElse { JSONObject().put("pass", false).put("error", it.javaClass.simpleName) }
            event("helper_complete", result)
            runOnUiThread { helperResult = result; updateSnapshot() }
            val compositor = runCatching { JSONObject(NativeProbe.compositor()) }
                .getOrElse { JSONObject().put("pass", false).put("error", it.javaClass.simpleName) }
            event("compositor_complete", compositor)
            runOnUiThread { compositorResult = compositor; updateSnapshot() }
            if (intent.getBooleanExtra("transport", false)) {
                val transport = runCatching { JSONObject(NativeProbe.transport(noBackupFilesDir.absolutePath, nonce)) }
                    .getOrElse { JSONObject().put("pass", false).put("error", it.javaClass.simpleName) }
                event("transport_complete", transport)
                runOnUiThread { transportResult = transport; updateSnapshot() }
            }
        }
        handler.post(tick)
    }

    private fun event(name: String, details: JSONObject = JSONObject()) {
        val line = JSONObject().put("event", name).put("nonce", nonce)
            .put("elapsed_ms", SystemClock.elapsedRealtime()).put("thread", Thread.currentThread().name)
            .put("generation", generation).put("details", details).toString()
        Log.i(TAG, line)
        synchronized(fileLock) {
            val output = File(noBackupFilesDir, "probe-events.jsonl")
            if (output.length() > 1024 * 1024) output.writeText("")
            output.appendText(line + "\n")
        }
    }

    private fun updateSnapshot(): JSONObject {
        val rendering = if (runId > 0) JSONObject(NativeProbe.snapshot(runId)) else JSONObject()
        val snapshot = JSONObject().put("schema", 1).put("nonce", nonce).put("version", BuildConfig.VERSION_CODE)
            .put("native", nativeInfo).put("helper", helperResult).put("render", rendering)
            .put("transport", transportResult)
            .put("compositor", compositorResult)
            .put("scene_ready", sceneReady).put("visible", visible).put("generation", generation)
            .put("completed_cycles", completedCycles).put("requested_cycles", requestedCycles)
            .put("fd_count", File("/proc/self/fd").list()?.size ?: -1)
        synchronized(fileLock) {
            val temp = File(noBackupFilesDir, "probe-latest.tmp")
            temp.writeText(snapshot.toString())
            check(temp.renameTo(File(noBackupFilesDir, "probe-latest.json")))
        }
        statusText = "Probe ${BuildConfig.VERSION_CODE} · helper ${if (helperResult.optBoolean("pass")) "PASS" else "pending/failed"}\n" +
            "Rust frames ${rendering.optLong("frames")} · pixels ${rendering.optBoolean("readback")} · cycles $completedCycles/$requestedCycles"
        return rendering
    }

    private val tick = object : Runnable {
        override fun run() {
            if (closing) return
            val rendering = updateSnapshot()
            if (runId > 0 && requestedCycles > completedCycles && rendering.optLong("frames") >= 30) recreatePanel()
            handler.postDelayed(this, 1000)
        }
    }

    override fun onSceneReady() {
        super.onSceneReady()
        sceneReady = true
        scene.setReferenceSpace(ReferenceSpace.LOCAL_FLOOR)
        scene.setViewOrigin(0f, 0f, 0f, 0f)
        scene.enablePassthrough(true)
        if (statusPanel == null) statusPanel = Entity.create(listOf(Panel(R.id.probe_status), Transform(Pose(Vector3(0f, 0.75f, 1.8f)))))
        createPanel()
        event("scene_ready")
    }

    private fun createPanel() {
        if (!sceneReady || closing || nativePanel != null) return
        generation++
        nativePanel = Entity.create(listOf(Panel(R.id.probe_native), Transform(Pose(Vector3(0f, 1.5f, 1.8f)))))
        event("panel_created", JSONObject().put("entity", nativePanel?.id.toString()))
        pendingSurface?.let { (entity, surface) -> bindSurface(entity, surface) }
    }

    private fun bindSurface(entity: Long, surface: Surface) {
        if (closing || !sceneReady) return
        if (nativePanel == null) { pendingSurface = entity to surface; return }
        if (nativePanel?.id != entity || !surface.isValid) { event("surface_stale"); return }
        pendingSurface = null
        if (runId != 0L) {
            stopRender { bindSurface(entity, surface) }
            return
        }
        runId = NativeProbe.start(surface)
        check(runId > 0) { "Previous renderer still owns its Surface" }
        NativeProbe.pause(runId, !visible)
        event("surface_attached", JSONObject().put("run", runId))
    }

    private fun stopRender(after: () -> Unit) {
        val id = runId
        if (id == 0L) { after(); return }
        runId = 0
        cleanupIds.add(id)
        NativeProbe.stop(id)
        val deadline = SystemClock.elapsedRealtime() + 2000
        val poll = object : Runnable {
            override fun run() {
                val result = NativeProbe.snapshot(id)
                if (NativeProbe.collect(id) != 2) {
                    cleanupIds.remove(id)
                    event("surface_detached", JSONObject(result))
                    if (!closing) after()
                } else if (SystemClock.elapsedRealtime() < deadline && !closing) {
                    handler.postDelayed(this, 25)
                } else {
                    event("render_stalled", JSONObject(result))
                    statusText = "Renderer stop timed out; restart probe"
                }
            }
        }
        handler.post(poll)
    }

    private fun recreatePanel() {
        if (transitioning || closing || cleanupIds.isNotEmpty()) return
        val old = nativePanel ?: return
        transitioning = true
        stopRender {
            old.destroy()
            nativePanel = null
            pendingSurface = null
            completedCycles++
            event("cycle_complete", JSONObject().put("cycle", completedCycles))
            createPanel()
            transitioning = false
        }
    }

    override fun registerPanels(): List<PanelRegistration> = listOf(
        VideoSurfacePanelRegistration(
            R.id.probe_native,
            surfaceConsumer = { entity, surface ->
                event("surface_callback", JSONObject().put("entity", entity.id.toString()).put("valid", surface.isValid))
                runOnUiThread { bindSurface(entity.id, surface) }
            },
            settingsCreator = { MediaPanelSettings(shape = QuadShapeOptions(width = 1.2f, height = 0.9f), display = PixelDisplayOptions(width = 1024, height = 768)) },
            panelSetup = { _, entity -> event("panel_setup", JSONObject().put("entity", entity.id.toString())) },
        ),
        ComposeViewPanelRegistration(
            R.id.probe_status,
            composeViewCreator = { _, context -> ComposeView(context).apply {
                setContent { MaterialTheme { Column(Modifier.fillMaxSize().background(Color(0xFF102331)).padding(16.dp)) {
                    Text(statusText, color = Color.White)
                    Button(onClick = { recreatePanel() }, enabled = !transitioning) { Text("Recreate Rust panel") }
                } } }
            } },
            settingsCreator = { UIPanelSettings(shape = QuadShapeOptions(width = 1.2f, height = 0.35f), display = DpPerMeterDisplayOptions(), style = PanelStyleOptions(themeResourceId = R.style.ProbeTheme)) },
        ),
    )

    override fun onSessionStateChanged(state: SessionState) {
        super.onSessionStateChanged(state)
        visible = state == SessionState.FOCUSED || state == SessionState.VISIBLE
        if (runId > 0) NativeProbe.pause(runId, !visible)
        event("xr_state", JSONObject().put("state", state.name))
    }

    override fun onPause() {
        if (runId > 0) NativeProbe.pause(runId, true)
        event("activity_pause")
        super.onPause()
    }

    override fun onResume() {
        super.onResume()
        if (runId > 0) NativeProbe.pause(runId, !visible)
        event("activity_resume")
    }

    override fun onSpatialShutdown() {
        sceneReady = false
        stopRender {}
        nativePanel = null
        statusPanel = null
        pendingSurface = null
        event("spatial_shutdown")
        super.onSpatialShutdown()
    }

    override fun onDestroy() {
        closing = true
        handler.removeCallbacksAndMessages(null)
        if (runId > 0) { NativeProbe.stop(runId); cleanupIds.add(runId); runId = 0 }
        val ids = cleanupIds.toList()
        worker.execute {
            for (id in ids) {
                NativeProbe.stop(id)
                val deadline = SystemClock.elapsedRealtime() + 2000
                while (NativeProbe.collect(id) == 2 && SystemClock.elapsedRealtime() < deadline) Thread.sleep(25)
            }
            event("activity_destroyed")
        }
        worker.shutdown()
        super.onDestroy()
    }

    companion object { private const val TAG = "EidolonProbe" }
}
