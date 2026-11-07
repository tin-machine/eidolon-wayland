package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.Process
import android.os.SystemClock
import android.util.Log
import android.view.Surface
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Color4
import com.meta.spatial.core.Pose
import com.meta.spatial.core.Vector3
import com.meta.spatial.core.SpatialContext
import com.meta.spatial.runtime.PanelSceneObject
import com.meta.spatial.runtime.SceneObject
import com.meta.spatial.runtime.PanelConfigOptions
import com.meta.spatial.runtime.AlphaMode
import com.meta.spatial.runtime.PanelShapeLayerBlendType
import com.meta.spatial.runtime.Scene
import com.meta.spatial.toolkit.*
import java.util.concurrent.atomic.AtomicLong
import org.json.JSONArray
import org.json.JSONObject

/** Activity単位のSDK所有者。Scene終了時はSDK所有物を返し、native参照の回収を別に待つ。 */
class SpatialPanelAdapter {
    private val ui = Handler(Looper.getMainLooper())
    private var scene = 0L
    private var runtimeScene: Scene? = null
    private var closed = false
    private var creating = false
    private val bindings = linkedMapOf<Long, Binding>()
    private fun primary(): Binding? = bindings.values.firstOrNull()
    private val pendingSurfaces = mutableMapOf<Long, Surface>()
    private val pendingObjects = mutableMapOf<Long, SceneObject>()
    private var objects: SceneObjectSystem? = null
    private data class VisualConfig(val width: Int, val height: Int, val epoch: Long)
    private val visuals = java.util.concurrent.ConcurrentHashMap<Long, VisualConfig>()
    private var pendingUntil = 0L
    private var created = 0
    private var destroyed = 0
    private var listeners = 0
    private var callbacks = 0
    private var lastPublish = 0L
    private var attempting: JSONObject? = null
    private var backgroundStamp: String? = null
    private var backgroundApi: JSONObject? = null
    private val trace = ArrayDeque<JSONObject>()
    private val retired = ArrayDeque<JSONObject>()
    private data class Binding(val entity: Entity, val handle: Long, var key: JSONObject,
        val ownerKey: JSONObject = JSONObject(key.toString()),
        val role: String = "diagnostic", val commandSeq: String? = null, var destroyCommand: JSONObject? = null,
        var surface: Surface? = null, var sceneObject: SceneObject? = null, var listener: Long? = null,
        val decoration: JSONObject? = null, var visual: Entity? = null,
        var input: InputAdapter? = null,
        var controls: WindowControls? = null, var controlsStamp: String? = null,
        var move:WindowMoveController?=null,var modeBadge:WindowModeBadge?=null,
        val grabSources: MutableMap<Long,Pair<Entity,Int>> = linkedMapOf(),
        var editing: Boolean = false, var wasGrabbed: Boolean = false,
        var poseRequested: String? = null, var poseAcked: String? = null,
        var lastDragPose: JSONObject? = null, var queuedPose: JSONObject? = null,
        var poseRequest: String? = null, var poseAccepted: Boolean = false,
        var poseSentAt: Long = 0,
        var physicalRequested: String? = null, var physicalAcked: String? = null,
        var attached: Boolean = false, var retire: Boolean = false, var destroy: Boolean = false,
        var hidden: Boolean = false, var sdkDisposed: Boolean = false, var disposalFailed: Boolean = false,
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

    val decorationRegistration = object : PanelRegistration(R.id.decoration_panel) {
        override fun panelCreator(sdkScene: Scene, spatialContext: SpatialContext): (Entity) -> PanelSceneObject = { entity ->
            val config = checkNotNull(visuals[entity.id])
            val options = PanelConfigOptions().apply {
                layoutWidthInPx = config.width; layoutHeightInPx = config.height
                width = 1.2f; height = .9f
                alphaMode = AlphaMode.PREMULTIPLIED; enableTransparent = true; forceSceneTexture = true
                layerConfig = null; layerBlendType = PanelShapeLayerBlendType.ALPHA_BLEND
            }
            PanelSceneObject(sdkScene, entity, options).also { obj ->
                ui.post {
                    if (!closed && scene == config.epoch && runtimeScene === sdkScene) {
                        bindings.values.firstOrNull { it.visual?.id == entity.id && !it.destroy }?.let { current ->
                            event("decoration_surface", current.entity.id)
                            surfaceReady(current.entity.id, obj.surface)
                        }
                    }
                }
            }
        }
    }
    fun initialize(activity: AppSystemActivity) {
        activity.registerMeshCreator(ThemeHitMesh.URI) { _: Entity -> ThemeHitMesh.create() }
        WindowControlMeshes.register(activity)
        BuildHooks.registerMeshes(activity)
        objects = activity.systemManager.findSystem<SceneObjectSystem>()
    }

    fun sceneReady(epoch: Long, sdkScene: Scene) {
        check(Looper.myLooper() == Looper.getMainLooper())
        scene = epoch
        runtimeScene = sdkScene
        closed = false
        bindings.values.forEach { it.retire = true; it.destroy = true }
        event("scene_ready")
        ui.removeCallbacks(poll)
        ui.post(poll)
    }
    fun close() {
        if(closed) return
        closed = true
        bindings.values.forEach { current ->
            runCatching { if(current.role=="windows") {
                if(current.wasGrabbed || current.editing && current.entity.getComponent<Grabbable>().isGrabbed) {
                    val finalPose=PanelPoseCodec.fromSdk(getAbsoluteTransform(current.entity))
                    current.poseRequest?.let {NativeSessionHost.forgetPose(it)}
                    current.poseRequest=finalPose?.let {NativeSessionHost.submitPose(current.key,it,current.handle)}
                }
                cancelGrab(current);current.entity.setComponent(Grabbable(false,GrabbableType.FACE,false,0f,4f))
            } }.onFailure {event("shutdown_pose_failure_${it.javaClass.simpleName}",current.entity.id)}
            current.retire=true;current.destroy=true;current.hidden=true
            if(current.attached && !current.detachSent) {
                val code=NativeSessionHost.detachSurface(current.key,current.handle)
                current.detachSent=code==NativeBridge.ACCEPTED
                event("detach_$code",current.entity.id)
            }
            // SDKのDataModelはsuper.onSpatialShutdown後に失効する。Entity IDは新Sceneで再利用され得る。
            // ANativeWindow参照の回収を待つpollから、失効したSDK objectへアクセスしない。
            disposeSdk(current)
        }
        runtimeScene = null
        pendingSurfaces.clear(); pendingObjects.clear()
        event("shutdown")
    }
    fun recreate(): Boolean {
        return recreateBinding(primary())
    }
    fun recreateWindow(window: String): Boolean = recreateBinding(bindings.values.firstOrNull {it.role=="windows" && !it.retire && it.key.getString("window")==window})
    private fun recreateBinding(binding: Binding?): Boolean {
        val current = binding ?: return false
        if (current.retire) return false
        current.retire = true; current.destroy = true
        event("recreate", current.entity.id)
        return true
    }
    fun windowEntity(window: String): Entity? = bindings.values.firstOrNull {it.role=="windows" && !it.retire && it.key.getString("window")==window}?.entity
    fun currentKey(): JSONObject? = primary()?.key?.let { JSONObject(it.toString()) }
    fun currentEntityId(): Long? = primary()?.entity?.id
    fun currentSceneObject(): SceneObject? {
        check(Looper.myLooper() == Looper.getMainLooper())
        return primary()?.sceneObject
    }
    fun beginInputCapture(source: Long, receive: (JSONObject) -> Unit): Boolean {
        val input = primary()?.input ?: return false
        input.beginCapture(source, receive); return true
    }
    fun endInputCapture() { primary()?.input?.endCapture() }
    fun currentRender(): JSONObject? {
        val current = primary() ?: return null
        val snapshot = JSONObject(NativeBridge.nativeSnapshot(current.handle))
        return snapshot.optJSONObject("runtime")?.optJSONObject("render")
    }
    fun duplicateCurrent(): Int {
        val current = primary() ?: return NativeBridge.NOT_READY
        return attach(current, current.surface ?: return NativeBridge.NOT_READY)
    }
    fun metrics(): JSONObject = JSONObject().put("scene", scene.toString()).put("created", created).put("destroyed", destroyed)
        .put("bindings", bindings.size).put("listeners", listeners).put("callbacks", callbacks)
        .put("pending", pendingSurfaces.size + pendingObjects.size)
        .put("background_api", backgroundApi?.let { JSONObject(it.toString()) })
        .put("decoration_entities", visuals.size).put("window_controls",WindowControls.resources()).put("window_mode_badges",WindowModeBadge.resources())
        .put("targets", JSONArray(bindings.values.map { JSONObject().put("entity",it.entity.id.toString()).put("target",JSONObject(it.key.toString())).put("role",it.role)
            .put("retiring",it.retire).put("sdk_disposed",it.sdkDisposed).put("disposal_failed",it.disposalFailed)
            .put("listener_owned",it.listener!=null).put("input_owned",it.input!=null)
            .put("decoration",it.decoration).put("visual_entity",it.visual?.id?.toString()).put("controls",it.controls?.metadata()).put("move",it.move?.metadata()).put("mode_badge",it.modeBadge?.metadata())
            .put("visual_collision",if(!it.sdkDisposed) it.visual?.getComponent<Panel>()?.hittable?.name else null)
            .put("input_collision",if(!it.sdkDisposed && it.decoration!=null) it.entity.getComponent<Hittable>().hittable.name else null) }))
        .put("retired",JSONArray(retired.toList()))
        .put("trace", JSONArray(trace.toList()))

    private fun event(name: String, entity: Long = 0, tid: Int = Process.myTid()) {
        val record = JSONObject().put("event", name).put("thread", tid).put("elapsed_ms", SystemClock.elapsedRealtime())
            .put("scene", scene.toString()).put("entity", entity.toString())
        bindings[entity]?.let { record.put("target", JSONObject(it.key.toString())) }
        if (trace.size == 128) trace.removeFirst()
        trace.addLast(record)
        Log.i("EidolonSurface", record.toString())
    }
    private fun key(): JSONObject = JSONObject().put("session", "1").put("window", "1").put("scene", scene.toString())
        .put("generation", generations.incrementAndGet().toString())
    private fun create(handle: Long, plan: JSONObject? = null, initialPose: JSONObject? = null) {
        val pose = if(plan==null) Pose(Vector3(0f,1.4f,1.6f)) else initialPose?.takeIf {PanelPoseCodec.valid(it)}?.let {PanelPoseCodec.toSdk(it)} ?: return
        creating = true
        attempting = plan
        val ownerKey = if(plan==null) key() else JSONObject(plan.getJSONObject("target").toString())
        if(NativeSessionHost.sdkBinding(ownerKey,handle,true)!=NativeBridge.ACCEPTED) { creating=false;return }
        var entity: Entity? = null
        try {
            val decoration = plan?.optJSONObject("decoration")
            val createdEntity = if (decoration == null) Entity.create(listOf(Panel(R.id.native_panel), Transform(pose)))
                else Entity.create(listOf(Mesh(android.net.Uri.parse(ThemeHitMesh.URI), ThemeHitMesh.COLLISION), Transform(pose)))
            entity = createdEntity
            val entity = createdEntity
            if(plan!=null) entity.setComponent(Grabbable(false,GrabbableType.FACE,false,0f,4f))
            val current = if (plan == null) Binding(entity, handle, ownerKey) else
                Binding(entity, handle, ownerKey, role="windows", commandSeq=plan.getString("command_seq"), decoration=decoration)
            bindings[entity.id] = current; created++; event("entity_created", entity.id)
            if (decoration != null) {
                val content = decoration.getJSONObject("content"); val canvas = decoration.getJSONObject("canvas")
                val origin = decoration.getJSONArray("content_origin")
                val w=content.getDouble("width"); val h=content.getDouble("height")
                val cw=canvas.getInt("width"); val ch=canvas.getInt("height")
                val offset = Vector3(((cw-w-2*origin.getInt(0))*.6/w).toFloat(), (-(ch-h-2*origin.getInt(1))*.45/h).toFloat(), 0f)
                val visual = Entity.create(listOf(Transform(Pose(offset)), TransformParent(entity),
                    Scale(Vector3((cw/w).toFloat(),(ch/h).toFloat(),1f)), Hittable(MeshCollision.NoCollision)))
                current.visual = visual
                visuals[visual.id] = VisualConfig(cw,ch,scene)
                // PanelとHittableはmesh_hittable属性を共有する。後着Panelの既定値で上書きしない。
                visual.setComponent(Panel(R.id.decoration_panel, MeshCollision.NoCollision))
                val epoch=scene
                val system = checkNotNull(objects)
                (system.getSceneObject(entity) ?: system.replaceOrReuseSceneObjectFuture(entity)).whenComplete { obj, failure -> ui.post {
                    if (!closed && scene == epoch && bindings[entity.id] === current && !current.destroy) {
                        if (failure == null && obj != null) objectReady(entity.id,obj)
                        else { NativeSessionHost.ackPanel(checkNotNull(plan),handle,false); current.retire=true; current.destroy=true }
                    }
                } }
            }
            pendingObjects.remove(entity.id)?.let { objectReady(entity.id,it) }
            pendingSurfaces.remove(entity.id)?.let { surfaceReady(entity.id,it) }
        } catch (failure: Exception) {
            val current=entity?.let {bindings[it.id]}
            if(current!=null) {current.retire=true;current.destroy=true}
            else {
                // create途中に失敗しても、実Entityを破棄できたときだけ所有記録を返す。
                entity?.destroy()
                NativeSessionHost.sdkBinding(ownerKey,handle,false)
            }
            throw failure
        } finally { creating = false }
    }
    private fun surfaceReady(entity: Long, surface: Surface) {
        val current = bindings[entity]
        if (current == null) {
            if (creating && pendingSurfaces.size < WindowBudget.MAX_WINDOWS) { pendingSurfaces[entity] = surface; pendingUntil = SystemClock.elapsedRealtime() + 5000 }
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
            current.key.getString("scene").toLong(), current.key.getString("generation").toLong(), surface,
            current.decoration?.getJSONObject("canvas")?.getInt("width") ?: 1024,
            current.decoration?.getJSONObject("canvas")?.getInt("height") ?: 768)
        else NativeBridge.nativeAttachSurface(current.handle, 1, 1, current.key.getString("scene").toLong(),
            current.key.getString("generation").toLong(), surface, 1024, 768)
    private fun objectReady(entity: Long, obj: SceneObject) {
        val current = bindings[entity]
        if (current == null) {
            if (creating && pendingObjects.size < WindowBudget.MAX_WINDOWS) { pendingObjects[entity] = obj; pendingUntil = SystemClock.elapsedRealtime() + 5000 }
            else event("object_discarded", entity)
            return
        }
        if (current.listener == null && !closed && !current.destroy) {
            current.sceneObject = obj
            current.input = InputAdapter(current.key) { source,state ->
                if(current.editing && !current.retire && bindings[entity]===current) {
                    if(state==0) current.grabSources.remove(source.id)
                    else if(current.grabSources.size<8 || source.id in current.grabSources) current.grabSources[source.id]=source to state
                }
            }
            current.listener = obj.addInputListener(checkNotNull(current.input))
            listeners++; event("listener_added", entity)
        }
    }
    private fun cancelGrab(current: Binding) {
        val sdk=runtimeScene
        current.grabSources.values.toList().forEach { (source,state) -> sdk?.onNoIntersectionInput(source,state,0) }
        sdk?.resolveInput();current.grabSources.clear()
    }
    private fun closeControls(current:Binding) {
        current.controls?.let {controls ->
            controls.close()
            // close自体が捕捉したSDK失敗も、成功receiptから取り除く。
            if(controls.metadata().optLong("failures")!=0L) current.disposalFailed=true
        };current.controls=null
    }
    private fun disposeSdk(current: Binding) {
        if(current.sdkDisposed) return
        current.sdkDisposed=true
        fun dispose(action:()->Unit) {runCatching(action).onFailure {current.disposalFailed=true;event("sdk_dispose_failure_${it.javaClass.simpleName}",current.entity.id)}}
        dispose {current.move?.close()};current.move=null
        dispose {current.modeBadge?.close()};current.modeBadge=null
        dispose {closeControls(current)}
        dispose {cancelGrab(current)}
        current.poseRequest?.let {NativeSessionHost.forgetPose(it)}
        dispose {current.input?.close()}; current.input = null
        current.listener?.let { token ->
            dispose {current.sceneObject?.removeInputListener(token)}
            listeners--; event("listener_removed",current.entity.id)
        }
        current.listener = null; current.sceneObject = null; current.surface = null; current.replacement = null
        current.visual?.let { visual -> visuals.remove(visual.id); dispose { visual.destroy() } }; current.visual=null
        dispose {current.entity.destroy()}; destroyed++; event("entity_destroyed", current.entity.id)
    }
    private fun destroy(current: Binding) {
        disposeSdk(current)
        if(current.disposalFailed) return
        val code=NativeSessionHost.sdkBinding(current.ownerKey,current.handle,false)
        if(code!=NativeBridge.ACCEPTED && code!=NativeBridge.STALE) return
        bindings.remove(current.entity.id)
        if(retired.size==32) retired.removeFirst()
        retired.addLast(JSONObject().put("runtime",current.handle.toString()).put("target",JSONObject(current.ownerKey.toString()))
            .put("entity",current.entity.id.toString()).put("sdk_binding_released",true).put("sdk_disposed",true).put("disposal_failed",false))
        current.destroyCommand?.let { NativeSessionHost.ackPanel(it, current.handle) }
    }
    private fun sameKey(a: JSONObject, b: JSONObject): Boolean = listOf("session", "window", "scene", "generation").all { a.optString(it) == b.optString(it) }
    private val poll = object : Runnable {
        override fun run() {
            try { tick() } catch (failure: Exception) {
                event("adapter_failure_${failure.javaClass.simpleName}")
                attempting?.let { NativeSessionHost.ackPanel(it, NativeSessionHost.currentHandle(), false) }
            }
            if (!closed || bindings.isNotEmpty()) ui.postDelayed(this, 32)
        }
    }
    private fun tick() {
        val now = SystemClock.elapsedRealtime()
        if (now > pendingUntil) { pendingSurfaces.clear(); pendingObjects.clear() }
        val handle = NativeSessionHost.currentHandle()
        val host = if (handle > 0) JSONObject(NativeBridge.nativeSnapshot(handle)).optJSONObject("runtime") else null
        if(host!=null && !closed && scene>0 && host.optString("scene")==scene.toString()) applyBackground(handle,host)
        val plans = host?.optJSONArray("panels")?.let { values -> (0 until values.length()).map { values.getJSONObject(it) } } ?: emptyList()
        val windows = host?.optJSONObject("render")?.optString("mode") == "windows"
        val desired = plans.filter { it.getString("action") == "create" && it.isNull("error") && it.getJSONObject("target").optString("scene") == scene.toString() }
        for (current in bindings.values.toList()) {
            val plan = plans.firstOrNull { sameKey(it.getJSONObject("target"), current.key) }
            if (host?.optString("state") in setOf("Stopping","Stopped","Failed") || current.role == "diagnostic" && windows || current.handle != handle ||
                current.role == "windows" && (plan == null || plan.getString("action") == "destroy" || !plan.isNull("error"))) {
                current.retire = true; current.destroy = true
            }
            if (current.role == "windows" && plan?.optString("action") == "destroy") current.destroyCommand = plan
            val snapshot = JSONObject(NativeBridge.nativeSnapshot(current.handle))
            if (snapshot.getInt("code") == NativeBridge.STALE) { destroy(current) }
            else {
                val render = snapshot.getJSONObject("runtime").getJSONObject("render")
                if(current.role=="windows" && !current.retire && plan?.optBoolean("applied")==true) {
                    tickMove(current,snapshot.getJSONObject("runtime"))
                    if(current.move?.blocksPose()!=true) {
                        applyPose(current,snapshot.getJSONObject("runtime"))
                        applyPhysical(current,snapshot.getJSONObject("runtime"))
                    }
                    applyControls(current,snapshot.getJSONObject("runtime"))
                }
                if (current.role == "windows" && !current.retire && plan != null && !plan.getBoolean("applied")) {
                    val targets = render.getJSONArray("targets")
                    if (current.listener != null && (0 until targets.length()).any { sameKey(targets.getJSONObject(it).getJSONObject("key"), current.key) && targets.getJSONObject(it).getLong("frames") > 0 })
                        NativeSessionHost.ackPanel(plan, current.handle)
                }
                val detached = render.getJSONArray("detached")
                val acknowledged = (0 until detached.length()).any { sameKey(detached.getJSONObject(it), current.key) }
                val shutdown=snapshot.getJSONObject("runtime").getJSONObject("shutdown")
                val pending=shutdown.getJSONArray("pending_owners")
                // owner全体の終了では個別detachのqueueを処理しない。thread終了のackと
                // 全native参照の回収を照合した場合も、SDK側を解放できる。単なる空targetでは不十分。
                val ownerReleased=shutdown.getBoolean("requested") &&
                    (0 until pending.length()).none {pending.getString(it)=="render"} &&
                    render.getInt("workers")==0 && render.getInt("contexts")==0 && render.getJSONArray("targets").length()==0
                if (current.attached && (acknowledged || ownerReleased)) {
                    current.attached = false; event("surface_detached", current.entity.id)
                    if (!current.retire) { current.retire = true; current.destroy = true }
                }
                if (current.retire) {
                    current.move?.close();current.move=null;current.modeBadge?.close();current.modeBadge=null
                    closeControls(current)
                    if (current.role == "windows" && !current.hidden) {
                        cancelGrab(current);current.entity.setComponent(Grabbable(false,GrabbableType.FACE,false,0f,4f))
                        current.entity.setComponent(Visible(false)); current.hidden = true
                        // IgnoreVisibleの入力面も、native Surface解放待ちに入る前に無効化する。
                        current.entity.setComponent(Hittable(MeshCollision.NoCollision))
                        current.visual?.setComponent(Visible(false))
                        NativeSessionHost.resetInputForRetiringTarget(current.ownerKey)
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
        if (host != null) {
            val targets = host.getJSONObject("render").getJSONArray("targets")
            plans.filter { it.getString("action") == "destroy" && it.isNull("error") }.forEach { plan ->
                if (bindings.values.none { sameKey(it.key,plan.getJSONObject("target")) } && (0 until targets.length()).none { sameKey(targets.getJSONObject(it).getJSONObject("key"), plan.getJSONObject("target")) })
                    NativeSessionHost.ackPanel(plan, handle)
            }
        }
        if (!closed && scene > 0 && host?.optString("state") == "Ready" && host.optString("scene") == scene.toString()) {
            if (windows && bindings.values.none { it.role == "diagnostic" }) {
                for (plan in desired) {
                    if (bindings.size >= WindowBudget.MAX_WINDOWS) break
                    if (BuildHooks.holdPanelCreation(plan.getJSONObject("target").getString("window"))) continue
                    if (bindings.values.none { sameKey(it.key,plan.getJSONObject("target")) }) {
                        val target=plan.getJSONObject("target")
                        val placements=host.getJSONObject("workspace").getJSONArray("windows")
                        val initial=(0 until placements.length()).map {placements.getJSONObject(it)}.firstOrNull {
                            it.optString("session")==target.optString("session") && it.optString("window")==target.optString("window") &&
                                !it.isNull("slot") && it.optInt("slot",-1)==plan.optInt("slot",-2)
                        }?.optJSONObject("placement")?.optJSONObject("pose")
                        // 最初のpose正本が来るまで待つ。旧横並び位置でEntityだけを先に作らない。
                        if(initial!=null && PanelPoseCodec.valid(initial)) create(handle,plan,initial)
                    }
                }
            } else if (!windows && bindings.isEmpty()) create(handle)
        }
        if (now - lastPublish >= 1000 && scene==NativeSessionHost.currentSceneEpoch()) { NativeSessionHost.publishAdapter(metrics()); lastPublish = now }
    }
    private fun applyControls(current: Binding, runtime: JSONObject) {
        if(current.retire || current.destroy || current.sdkDisposed) {
            current.move?.close();current.move=null;current.modeBadge?.close();current.modeBadge=null;current.controls?.close();current.controls=null;return
        }
        val app=runtime.optJSONObject("app") ?: return
        if(app.optString("session")!=current.key.optString("session")) return
        val windows=app.getJSONArray("windows")
        val window=(0 until windows.length()).map {windows.getJSONObject(it)}.firstOrNull {it.optString("window")==current.key.optString("window")} ?: return
        val size=window.getJSONObject("sizing").getJSONObject("physical_size_m")
        val width=size.getDouble("width_m");val height=size.getDouble("height_m")
        val render=runtime.getJSONObject("render");val revision=render.optLong("theme_revision",0)
        val config=WindowControls.parse(render.optJSONObject("window_controls")) ?: return
        val controlTarget=current.key.toString()
        val stamp="$controlTarget:$revision:$config:$width:$height"
        if(current.controlsStamp!=stamp) {
            current.move?.close();current.move=null;current.modeBadge?.close();current.modeBadge=null
            current.controls?.close();current.controls=null
            current.controlsStamp=stamp
            runCatching {
                val live={ !closed && scene==NativeSessionHost.currentSceneEpoch() && bindings[current.entity.id]===current && !current.retire && !current.destroy &&
                    current.handle==NativeSessionHost.currentHandle() && sameKey(current.key,JSONObject(controlTarget)) }
                val move=WindowMoveController(current.entity,controlTarget,current.handle,revision,checkNotNull(runtimeScene),live)
                current.move=move
                current.controls=WindowControls(current.entity,controlTarget,current.handle,checkNotNull(runtimeScene),checkNotNull(objects),
                    live,config,width,height,revision,move)
                current.modeBadge=WindowModeBadge(current.entity,config,width,height,checkNotNull(runtimeScene),checkNotNull(objects),live)
            }.onFailure {
                current.move?.close();current.move=null;current.modeBadge?.close();current.modeBadge=null;current.controls?.close();current.controls=null
                event("controls_create_failure_${it.javaClass.simpleName}",current.entity.id)
            }
        }
        val visible=runtime.getBoolean("activity_visible") && runtime.getString("xr_state")=="FOCUSED"
        current.controls?.setEnabled(!current.editing && visible)
        val input=runtime.optJSONObject("app")?.optJSONObject("input")
        val focused=input!=null && !input.isNull("focus_token") && NativeSessionHost.keyboardFocusMatches(current.key,input.getString("focus_token"))
        current.modeBadge?.update(WindowMode.from(NativeSessionHost.inputTarget(controlTarget)!=null,visible,current.editing,current.move?.phase,NativeSessionHost.inputStatus.value.reason,focused),visible)
    }
    private fun tickMove(current:Binding,runtime:JSONObject) {
        val windows=runtime.getJSONObject("workspace").getJSONArray("windows")
        val placement=(0 until windows.length()).map {windows.getJSONObject(it)}.firstOrNull {
            it.getString("window")==current.key.getString("window") && it.getString("session")==current.key.getString("session")
        }?.getJSONObject("placement") ?: return
        current.move?.tick(runtime,placement)
    }
    private fun applyBackground(handle: Long, runtime: JSONObject) {
        val workspace=runtime.getJSONObject("workspace");val sdk=runtimeScene ?: return
        val mode=workspace.getString("background");val command=if(workspace.isNull("background_command")) null else workspace.getString("background_command")
        val stamp="$handle:$scene:${command ?: "initial"}:$mode"
        if(backgroundStamp==stamp) return
        val passthrough=mode=="passthrough"
        val color=if(passthrough) Color4(0f,0f,0f,0f) else Color4(.025f,.035f,.05f,1f)
        sdk.enablePassthrough(passthrough)
        sdk.setBackfillColor(color)
        backgroundApi=JSONObject().put("scene",scene.toString()).put("background",mode).put("background_command",command)
            .put("passthrough_requested",passthrough).put("backfill_rgba",JSONArray(listOf(color.red,color.green,color.blue,color.alpha)))
            .put("optical_verified",false)
        if(NativeSessionHost.ackBackground(scene,command,mode,handle)==NativeBridge.ACCEPTED) backgroundStamp=stamp
    }
    private fun applyPose(current: Binding, runtime: JSONObject) {
        val windows=runtime.getJSONObject("workspace").getJSONArray("windows")
        val state=(0 until windows.length()).map {windows.getJSONObject(it)}.firstOrNull {it.getString("window")==current.key.getString("window") && it.getString("session")==current.key.getString("session")} ?: return
        val placement=state.getJSONObject("placement");val desired=placement.getJSONObject("pose")
        val command=if(placement.isNull("pose_command")) null else placement.getString("pose_command")
        val editing=NativeSessionHost.isLayoutEditing() && runtime.getBoolean("activity_visible") && runtime.getString("xr_state")=="FOCUSED"
        if(current.editing!=editing) {
            if(!editing) cancelGrab(current)
            current.entity.setComponent(Grabbable(editing,GrabbableType.FACE,false,0f,4f))
            current.input?.setSuppressed(editing);current.editing=editing
        }
        val grabbed=editing && current.entity.getComponent<Grabbable>().isGrabbed
        val actual=PanelPoseCodec.fromSdk(getAbsoluteTransform(current.entity))
        if((grabbed || current.wasGrabbed) && actual!=null && (current.lastDragPose?.let { !PanelPoseCodec.near(it,actual,.001) } ?: true)) {
            current.queuedPose=actual;current.lastDragPose=actual
        }
        current.poseRequest?.let { request ->
            val status=NativeSessionHost.poseCompletion(request)
            if(status=="ACCEPTED") current.poseAccepted=true
            if(current.poseAccepted && command==request) {current.poseRequest=null;current.poseAccepted=false}
            else if(status!=null && status!="pending" && status!="ACCEPTED" || SystemClock.elapsedRealtime()-current.poseSentAt>5000) {
                NativeSessionHost.forgetPose(request);current.poseRequest=null;current.poseAccepted=false
                if(!grabbed) current.queuedPose=null
            }
        }
        if(current.poseRequest==null && current.queuedPose!=null && (SystemClock.elapsedRealtime()-current.poseSentAt>=100 || !grabbed)) {
            val request=NativeSessionHost.submitPose(current.key,checkNotNull(current.queuedPose),current.handle)
            if(request!=null) {current.poseRequest=request;current.poseSentAt=SystemClock.elapsedRealtime();current.queuedPose=null}
        }
        current.wasGrabbed=grabbed
        if(grabbed || current.poseRequest!=null || current.queuedPose!=null) {current.poseAcked=null;return}
        val stamp="${command ?: "initial"}:$desired"
        if(current.poseRequested!=stamp || actual==null || !PanelPoseCodec.near(actual,desired)) {
            setAbsoluteTransform(current.entity,PanelPoseCodec.toSdk(desired));current.poseRequested=stamp;current.poseAcked=null
        }
        val app=runtime.optJSONObject("app") ?: return
        val appWindows=app.getJSONArray("windows")
        val window=(0 until appWindows.length()).map {appWindows.getJSONObject(it)}.firstOrNull {it.getString("window")==current.key.getString("window")} ?: return
        if(current.poseAcked!=stamp && physicalGeometryMatches(current,window.getJSONObject("sizing").getJSONObject("physical_size_m"))) {
            val observed=PanelPoseCodec.fromSdk(getAbsoluteTransform(current.entity))
            if(observed!=null && PanelPoseCodec.near(observed,desired) && NativeSessionHost.ackPose(current.key,command,observed,current.handle)==NativeBridge.ACCEPTED) current.poseAcked=stamp
        }
    }
    private fun applyPhysical(current: Binding, runtime: JSONObject) {
        val app=runtime.optJSONObject("app") ?: return
        if(app.optString("session")!=current.key.optString("session")) return
        val windows=app.getJSONArray("windows")
        val window=(0 until windows.length()).map { windows.getJSONObject(it) }.firstOrNull { it.getString("window")==current.key.getString("window") } ?: return
        val sizing=window.getJSONObject("sizing"); val size=sizing.getJSONObject("physical_size_m")
        val command=if(sizing.isNull("size_command")) null else sizing.getString("size_command")
        val stamp="${command ?: "initial"}:$size"
        val x=size.getDouble("width_m").toFloat()/1.2f; val y=size.getDouble("height_m").toFloat()/.9f
        if(current.physicalRequested!=stamp) {
            current.entity.setComponent(Scale(Vector3(x,y,1f)))
            current.physicalRequested=stamp; current.physicalAcked=null
        }
        if(current.physicalAcked!=stamp && physicalGeometryMatches(current,size)) {
            if(NativeSessionHost.ackPhysicalSize(current.key,command,size,current.handle)==NativeBridge.ACCEPTED) current.physicalAcked=stamp
        }
    }
    private fun physicalGeometryMatches(current: Binding, size: JSONObject): Boolean {
        val sdk=runtimeScene ?: return false
        val pose=getAbsoluteTransform(current.entity)
        // 公開ray APIで実quadを測る。希望componentだけを読んでSDK適用済みとはしない。
        for((u,v) in listOf(.2 to .35,.8 to .65)) {
            val x=((u-.5)*size.getDouble("width_m")).toFloat();val y=((.5-v)*size.getDouble("height_m")).toFloat()
            val world=pose*Vector3(x,y,0f)
            val start=pose*Vector3(.27f,.31f,-1.3f)
            val end=pose*Vector3(x+(x-.27f)*(.8f/1.3f),y+(y-.31f)*(.8f/1.3f),.8f)
            val hit=sdk.lineSegmentIntersect(start,end) ?: return false
            if(hit.entity?.id!=current.entity.id || kotlin.math.abs(hit.textureCoordinate.x-u)>=.005 || kotlin.math.abs(hit.textureCoordinate.y-v)>=.005 ||
                kotlin.math.abs(hit.point.x-world.x)>=.0001 || kotlin.math.abs(hit.point.y-world.y)>=.0001 || kotlin.math.abs(hit.point.z-world.z)>=.0001) return false
        }
        return true
    }
    companion object { private val generations = AtomicLong(0) }
}
