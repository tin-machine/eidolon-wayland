package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.Process
import android.os.SystemClock
import android.util.Log
import android.view.Surface
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Pose
import com.meta.spatial.core.Vector3
import com.meta.spatial.runtime.PanelSceneObject
import com.meta.spatial.toolkit.*
import java.util.concurrent.atomic.AtomicLong
import org.json.JSONArray
import org.json.JSONObject

/** Activity単位のSDK所有者。Java Surfaceをreleaseせず、native detach ack後にEntityを破棄する。 */
class SpatialPanelAdapter {
    private val ui = Handler(Looper.getMainLooper())
    private var scene = 0L
    private var closed = false
    private var creating = false
    private var binding: Binding? = null
    private var pendingSurface: Pair<Long, Surface>? = null
    private var pendingObject: Pair<Long, PanelSceneObject>? = null
    private var pendingUntil = 0L
    private var created = 0
    private var destroyed = 0
    private var listeners = 0
    private var callbacks = 0
    private var lastPublish = 0L
    private var attempting: JSONObject? = null
    private val trace = ArrayDeque<JSONObject>()
    private data class Binding(val entity: Entity, val handle: Long, var key: JSONObject,
        val role: String = "diagnostic", val commandSeq: String? = null, var destroyCommand: JSONObject? = null,
        var surface: Surface? = null, var sceneObject: PanelSceneObject? = null, var listener: Long? = null,
        var input: InputAdapter? = null,
        var attached: Boolean = false, var retire: Boolean = false, var destroy: Boolean = false,
        var hidden: Boolean = false,
        var detachSent: Boolean = false, var replacement: Surface? = null)

    val registration = VideoSurfacePanelRegistration(R.id.native_panel,
        surfaceConsumer = { entity, surface ->
            val tid = Process.myTid()
            ui.post { event("surface_callback", entity.id, tid); callbacks++; surfaceReady(entity.id, surface) }
        },
        settingsCreator = { MediaPanelSettings(shape = QuadShapeOptions(width = 1.2f, height = 0.9f),
            display = PixelDisplayOptions(width = 1024, height = 768)) },
        panelSetup = { obj, entity ->
            val tid = Process.myTid()
            ui.post { event("panel_setup", entity.id, tid); objectReady(entity.id, obj) }
        })

    fun sceneReady(epoch: Long) {
        check(Looper.myLooper() == Looper.getMainLooper())
        scene = epoch
        closed = false
        binding?.let { it.retire = true; it.destroy = true }
        event("scene_ready")
        ui.removeCallbacks(poll)
        ui.post(poll)
    }
    fun close() {
        closed = true
        pendingSurface = null; pendingObject = null
        binding?.let { it.retire = true; it.destroy = true }
        event("shutdown")
    }
    fun recreate(): Boolean {
        val current = binding ?: return false
        if (current.retire) return false
        current.retire = true; current.destroy = true
        event("recreate", current.entity.id)
        return true
    }
    fun currentKey(): JSONObject? = binding?.key?.let { JSONObject(it.toString()) }
    fun currentEntityId(): Long? = binding?.entity?.id
    fun currentSceneObject(): PanelSceneObject? {
        check(Looper.myLooper() == Looper.getMainLooper())
        return binding?.sceneObject
    }
    fun beginInputCapture(source: Long, receive: (JSONObject) -> Unit): Boolean {
        val input = binding?.input ?: return false
        input.beginCapture(source, receive); return true
    }
    fun endInputCapture() { binding?.input?.endCapture() }
    fun currentRender(): JSONObject? {
        val current = binding ?: return null
        val snapshot = JSONObject(NativeBridge.nativeSnapshot(current.handle))
        return snapshot.optJSONObject("runtime")?.optJSONObject("render")
    }
    fun duplicateCurrent(): Int {
        val current = binding ?: return NativeBridge.NOT_READY
        return attach(current, current.surface ?: return NativeBridge.NOT_READY)
    }
    fun metrics(): JSONObject = JSONObject().put("scene", scene.toString()).put("created", created).put("destroyed", destroyed)
        .put("bindings", if (binding == null) 0 else 1).put("listeners", listeners).put("callbacks", callbacks)
        .put("pending", if (pendingSurface != null || pendingObject != null) 1 else 0)
        .put("trace", JSONArray(trace.toList()))

    private fun event(name: String, entity: Long = 0, tid: Int = Process.myTid()) {
        val record = JSONObject().put("event", name).put("thread", tid).put("elapsed_ms", SystemClock.elapsedRealtime())
            .put("scene", scene.toString()).put("entity", entity.toString())
        binding?.let { record.put("target", JSONObject(it.key.toString())) }
        if (trace.size == 128) trace.removeFirst()
        trace.addLast(record)
        Log.i("EidolonSurface", record.toString())
    }
    private fun key(): JSONObject = JSONObject().put("session", "1").put("window", "1").put("scene", scene.toString())
        .put("generation", generations.incrementAndGet().toString())
    private fun create(handle: Long, plan: JSONObject? = null) {
        creating = true
        attempting = plan
        try {
            val position = if (plan == null) Vector3(-0.8f, 1.4f, 1.6f) else Vector3(0f, 1.4f, 1.5f)
            val entity = Entity.create(listOf(Panel(R.id.native_panel), Transform(Pose(position))))
            val current = if (plan == null) Binding(entity, handle, key()) else
                Binding(entity, handle, JSONObject(plan.getJSONObject("target").toString()), "windows", plan.getString("command_seq"))
            binding = current; created++; event("entity_created", entity.id)
            pendingObject?.takeIf { it.first == entity.id }?.let { objectReady(it.first, it.second) }
            pendingSurface?.takeIf { it.first == entity.id }?.let { surfaceReady(it.first, it.second) }
            pendingObject = null; pendingSurface = null
        } finally { creating = false }
    }
    private fun surfaceReady(entity: Long, surface: Surface) {
        val current = binding
        if (current == null || current.entity.id != entity) {
            if (creating && pendingSurface == null) { pendingSurface = entity to surface; pendingUntil = SystemClock.elapsedRealtime() + 5000 }
            else event("surface_discarded", entity)
            return
        }
        if (closed || current.destroy || !surface.isValid) return
        if (current.attached) {
            if (current.surface === surface) { event("surface_duplicate", entity); return }
            current.replacement = surface; current.retire = true
            if (current.role == "windows") current.destroy = true
        } else {
            current.surface = surface
            val code = attach(current, surface)
            current.attached = code == NativeBridge.ACCEPTED
            event("attach_$code", entity)
        }
    }
    private fun attach(current: Binding, surface: Surface): Int = if (current.role == "windows")
        NativeBridge.nativeAttachWindowSurface(current.handle, current.key.getString("session").toLong(), current.key.getString("window").toLong(),
            current.key.getString("scene").toLong(), current.key.getString("generation").toLong(), surface, 1024, 768)
        else NativeBridge.nativeAttachSurface(current.handle, 1, 1, current.key.getString("scene").toLong(),
            current.key.getString("generation").toLong(), surface, 1024, 768)
    private fun objectReady(entity: Long, obj: PanelSceneObject) {
        val current = binding
        if (current == null || current.entity.id != entity) {
            if (creating && pendingObject == null) { pendingObject = entity to obj; pendingUntil = SystemClock.elapsedRealtime() + 5000 }
            else event("object_discarded", entity)
            return
        }
        if (current.listener == null && !closed && !current.destroy) {
            current.sceneObject = obj
            current.input = InputAdapter(current.key)
            current.listener = obj.addInputListener(checkNotNull(current.input))
            listeners++; event("listener_added", entity)
        }
    }
    private fun destroy(current: Binding) {
        current.input?.close(); current.input = null
        current.listener?.let { current.sceneObject?.removeInputListener(it); listeners--; event("listener_removed", current.entity.id) }
        current.listener = null; current.sceneObject = null; current.surface = null; current.replacement = null
        current.entity.destroy(); destroyed++; event("entity_destroyed", current.entity.id)
        binding = null
        current.destroyCommand?.let { NativeSessionHost.ackPanel(it, current.handle) }
    }
    private fun sameKey(a: JSONObject, b: JSONObject): Boolean = listOf("session", "window", "scene", "generation").all { a.optString(it) == b.optString(it) }
    private val poll = object : Runnable {
        override fun run() {
            try { tick() } catch (failure: Exception) {
                event("adapter_failure_${failure.javaClass.simpleName}")
                attempting?.let { NativeSessionHost.ackPanel(it, NativeSessionHost.currentHandle(), false) }
            }
            if (!closed || binding != null) ui.postDelayed(this, 32)
        }
    }
    private fun tick() {
        val now = SystemClock.elapsedRealtime()
        if (now > pendingUntil) { pendingSurface = null; pendingObject = null }
        val handle = NativeSessionHost.currentHandle()
        val host = if (handle > 0) JSONObject(NativeBridge.nativeSnapshot(handle)).optJSONObject("runtime") else null
        val plans = host?.optJSONArray("panels")?.let { values -> (0 until values.length()).map { values.getJSONObject(it) } } ?: emptyList()
        val windows = host?.optJSONObject("render")?.optString("mode") == "windows"
        val desired = plans.firstOrNull { it.getString("action") == "create" && it.isNull("error") && it.getJSONObject("target").optString("scene") == scene.toString() }
        val current = binding
        if (current != null) {
            val plan = plans.firstOrNull { sameKey(it.getJSONObject("target"), current.key) }
            if (current.role == "diagnostic" && windows || current.handle != handle ||
                current.role == "windows" && (plan == null || plan.getString("action") == "destroy" || !plan.isNull("error"))) {
                current.retire = true; current.destroy = true
            }
            if (current.role == "windows" && plan?.optString("action") == "destroy") current.destroyCommand = plan
            val snapshot = JSONObject(NativeBridge.nativeSnapshot(current.handle))
            if (snapshot.getInt("code") == NativeBridge.STALE) { destroy(current) }
            else {
                val render = snapshot.getJSONObject("runtime").getJSONObject("render")
                if (current.role == "windows" && !current.retire && plan != null && !plan.getBoolean("applied")) {
                    val targets = render.getJSONArray("targets")
                    if ((0 until targets.length()).any { sameKey(targets.getJSONObject(it).getJSONObject("key"), current.key) && targets.getJSONObject(it).getLong("frames") > 0 })
                        NativeSessionHost.ackPanel(plan, current.handle)
                }
                val detached = render.getJSONArray("detached")
                val acknowledged = (0 until detached.length()).any { sameKey(detached.getJSONObject(it), current.key) }
                if (current.attached && acknowledged) {
                    current.attached = false; event("surface_detached", current.entity.id)
                    if (!current.retire) { current.retire = true; current.destroy = true }
                }
                if (current.retire) {
                    if (current.role == "windows" && !current.hidden) {
                        current.entity.setComponent(Visible(false)); current.hidden = true
                        NativeSessionHost.resetInput()
                        event("window_hidden", current.entity.id)
                    }
                    if (current.attached && !current.detachSent) {
                        val code = NativeSessionHost.detachSurface(current.key, current.handle)
                        current.detachSent = code == NativeBridge.ACCEPTED
                        event("detach_$code", current.entity.id)
                    }
                    if (!current.attached) {
                        if (current.destroy || closed) destroy(current)
                        else {
                            val replacement = current.replacement
                            current.replacement = null; current.surface = null
                            current.key = key(); current.retire = false; current.detachSent = false
                            current.input?.updateTarget(current.key)
                            if (replacement != null) surfaceReady(current.entity.id, replacement)
                        }
                    }
                } else if (!current.attached && current.surface != null) surfaceReady(current.entity.id, current.surface!!)
            }
        }
        // 一度もEntityを作らなかった旧sceneのdestroyも、native参照が無ければackする。
        if (binding == null && host != null) {
            val targets = host.getJSONObject("render").getJSONArray("targets")
            plans.filter { it.getString("action") == "destroy" && it.isNull("error") }.forEach { plan ->
                if ((0 until targets.length()).none { sameKey(targets.getJSONObject(it).getJSONObject("key"), plan.getJSONObject("target")) })
                    NativeSessionHost.ackPanel(plan, handle)
            }
        }
        if (!closed && binding == null && scene > 0) {
            if (host?.optString("state") == "Ready" && host.optString("scene") == scene.toString() && (!windows || desired != null)) create(handle, if (windows) desired else null)
        }
        if (now - lastPublish >= 1000) { NativeSessionHost.publishAdapter(metrics()); lastPublish = now }
    }
    companion object { private val generations = AtomicLong(0) }
}
