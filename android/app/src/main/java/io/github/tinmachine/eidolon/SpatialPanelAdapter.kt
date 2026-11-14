package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.Process
import android.os.SystemClock
import android.util.Log
import android.view.Surface
import com.meta.spatial.core.Entity
import com.meta.spatial.core.EntityContext
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
    private val adapterOwner = adapters.incrementAndGet()
    private val evidence = NativeSessionHost.sdkRetirement.evidence
    private val deletionSystem = SdkDeletionFenceSystem()
    private var deletionFence: SdkSceneDeletionFence? = null
    private class SceneEntity(val scene: Scene, val entity: Long) {
        override fun equals(other: Any?): Boolean = other is SceneEntity && scene === other.scene && entity == other.entity
        override fun hashCode(): Int = 31 * System.identityHashCode(scene) + entity.hashCode()
    }
    private data class CallbackOwner(val binding: Binding, val sdkScene: Scene)
    private val callbackOwners = java.util.concurrent.ConcurrentHashMap<SceneEntity, CallbackOwner>()
    // Do not let a delayed factory for a destroyed Panel acquire an ID reused
    // within this Scene. New Scenes have separate reference identities.
    private val issuedPanelEntities = mutableListOf<Pair<java.lang.ref.WeakReference<Scene>, MutableSet<Long>>>()
    private fun reservePanelEntity(sdkScene: Scene, entity: Long): Boolean {
        issuedPanelEntities.removeAll { it.first.get() == null }
        val issued = issuedPanelEntities.firstOrNull { it.first.get() === sdkScene }?.second
            ?: mutableSetOf<Long>().also { issuedPanelEntities.add(java.lang.ref.WeakReference(sdkScene) to it) }
        return issued.add(entity)
    }
    private var scene = 0L
    private var runtimeScene: Scene? = null
    private var closed = false
    private val bindings = linkedMapOf<Long, Binding>()
    private val cancelledReservations = linkedMapOf<SdkEvidenceBridge.Lease, SdkResourceScope>()
    private fun primary(): Binding? = bindings.values.firstOrNull()
    private var objects: SceneObjectSystem? = null
    private data class VisualConfig(val width: Int, val height: Int, val epoch: Long)
    private val visuals = java.util.concurrent.ConcurrentHashMap<SceneEntity, VisualConfig>()
    private var created = 0
    private var destroyed = 0
    private var listeners = 0
    private var callbacks = 0
    private var lastPublish = 0L
    private var backgroundStamp: String? = null
    private var backgroundApi: JSONObject? = null
    private val trace = ArrayDeque<JSONObject>()
    private val retired = ArrayDeque<JSONObject>()
    private data class Binding(val entity: Entity, val handle: Long, var key: JSONObject,
        val sdkScene: Scene, val scope: SdkResourceScope? = null,
        val allocation: Long = allocations.incrementAndGet(),
        var retirementSent: Boolean = false, var evidenceCurrent: Boolean = false, var everAttached: Boolean = false,
        val ownerKey: JSONObject = JSONObject(key.toString()),
        val role: String = "diagnostic", val commandSeq: String? = null, var destroyCommand: JSONObject? = null,
        var surface: Surface? = null, var sceneObject: SceneObject? = null, var listener: Long? = null,
        val decoration: JSONObject? = null, var visual: Entity? = null,
        var input: InputAdapter? = null,
        var controls: WindowControls? = null, var controlsStamp: String? = null,
        var move:WindowMoveController?=null,var modeBadge:WindowModeBadge?=null,
        var menu:WindowMenuController?=null,var menuStamp:String?=null,
        val grabSources: MutableMap<Long,Pair<Entity,Int>> = linkedMapOf(),
        var editing: Boolean = false, var wasGrabbed: Boolean = false,
        var poseRequested: String? = null, var poseAcked: String? = null,
        var lastDragPose: JSONObject? = null, var queuedPose: JSONObject? = null,
        var poseRequest: String? = null, var poseAccepted: Boolean = false,
        var poseSentAt: Long = 0,
        var physicalRequested: String? = null, var physicalAcked: String? = null,
        var attached: Boolean = false, var retire: Boolean = false, var destroy: Boolean = false,
        var hidden: Boolean = false, var sdkDisposed: Boolean = false, var disposalFailed: Boolean = false,
        var detachSent: Boolean = false, var replacement: Surface? = null,
        var group:WindowGroupPresentation.Selection=WindowGroupPresentation.Unowned,
        var groupVisible:Boolean=true,var groupCollision:Boolean?=null,var groupSize:String?=null,var normalCollision:MeshCollision?=null,
        var groupAckRequest:String?=null,var groupAckFor:String?=null,var groupAckStamp:String?=null,var groupAckFailed:String?=null,
        var groupBodyGeometry:Boolean=false,var groupControlGeometry:Boolean=false)

    /** The SDK factory captures the Scene and the already reserved Create.
     * Entity IDs are never resolved against a later Scene inside a callback.
     */
    val registration = object : PanelRegistration(R.id.native_panel) {
        override fun panelCreator(sdkScene: Scene, spatialContext: SpatialContext): (Entity) -> PanelSceneObject = { entity ->
            val owner = checkNotNull(callbackOwners[SceneEntity(sdkScene,entity.id)]) { "SDK_PANEL_OWNER_MISSING" }
            check(owner.sdkScene === sdkScene) { "SDK_PANEL_SCENE_STALE" }
            val create = {
                val delegate = VideoSurfacePanelRegistration(R.id.native_panel,
                    surfaceConsumer = { _, surface ->
                        val tid = Process.myTid()
                        postCallback(owner) {
                            event("surface_callback", it.entity.id, tid); callbacks++
                            surfaceReady(it, surface)
                        }
                    },
                    settingsCreator = { MediaPanelSettings(shape = QuadShapeOptions(width = 1.2f, height = 0.9f),
                        display = PixelDisplayOptions(width = 1024, height = 768)) },
                    panelSetup = { obj, _ ->
                        val tid = Process.myTid()
                        postCallback(owner) { event("panel_setup", it.entity.id, tid); objectReady(it, obj) }
                    })
                delegate.panelCreator(sdkScene, spatialContext)(entity)
            }
            owner.binding.scope?.produce(create) ?: create()
        }
    }

    private fun postCallback(owner: CallbackOwner, action: (Binding) -> Unit) {
        val work = {
            val current = owner.binding
            if (!closed && runtimeScene === owner.sdkScene && bindings[current.allocation] === current &&
                !current.retire && !current.destroy) action(current)
        }
        owner.binding.scope?.post(work) ?: ui.post { work() }
    }

    val decorationRegistration = object : PanelRegistration(R.id.decoration_panel) {
        override fun panelCreator(sdkScene: Scene, spatialContext: SpatialContext): (Entity) -> PanelSceneObject = { entity ->
            val owner = checkNotNull(callbackOwners[SceneEntity(sdkScene,entity.id)]) { "SDK_DECORATION_OWNER_MISSING" }
            check(owner.sdkScene === sdkScene) { "SDK_PANEL_SCENE_STALE" }
            val config = checkNotNull(visuals[SceneEntity(sdkScene,entity.id)])
            val create = {
                val options = PanelConfigOptions().apply {
                    layoutWidthInPx = config.width; layoutHeightInPx = config.height
                    width = 1.2f; height = .9f
                    alphaMode = AlphaMode.PREMULTIPLIED; enableTransparent = true; forceSceneTexture = true
                    layerConfig = null; layerBlendType = PanelShapeLayerBlendType.ALPHA_BLEND
                }
                PanelSceneObject(sdkScene, entity, options).also { obj ->
                    postCallback(owner) { current ->
                        event("decoration_surface", current.entity.id)
                        surfaceReady(current, obj.surface)
                    }
                }
            }
            owner.binding.scope?.produce(create) ?: create()
        }
    }
    fun initialize(activity: AppSystemActivity) {
        activity.registerMeshCreator(ThemeHitMesh.URI) { _: Entity -> ThemeHitMesh.create() }
        WindowControlMeshes.register(activity)
        BuildHooks.registerMeshes(activity)
        objects = activity.systemManager.findSystem<SceneObjectSystem>()
        activity.systemManager.registerSystem(deletionSystem)
    }

    fun sceneReady(epoch: Long, sdkScene: Scene) {
        check(Looper.myLooper() == Looper.getMainLooper())
        bindings.values.forEach { beginRetirement(it); it.destroy = true }
        deletionFence?.close()
        scene = epoch
        runtimeScene = sdkScene
        closed = false
        deletionFence = SdkSceneDeletionFence(adapterOwner, epoch, sdkScene,
            checkNotNull(EntityContext.getDataModel()), evidence)
        deletionSystem.active = deletionFence
        event("scene_ready")
        ui.removeCallbacks(poll)
        ui.post(poll)
    }
    fun close() {
        if(closed) return
        closed = true
        bindings.values.forEach { current ->
            beginRetirement(current)
            runCatching { if(current.role=="windows" && canUseSdk(current)) {
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
        deletionFence?.close()
        deletionSystem.active = null
        runtimeScene = null
        event("shutdown")
    }
    fun recreate(): Boolean {
        return recreateBinding(primary())
    }
    fun recreateWindow(window: String): Boolean = recreateBinding(bindings.values.firstOrNull {it.role=="windows" && !it.retire && it.key.getString("window")==window})
    private fun recreateBinding(binding: Binding?): Boolean {
        val current = binding ?: return false
        if (current.retire) return false
        beginRetirement(current); current.destroy = true
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
        .put("pending", cancelledReservations.size)
        .put("background_api", backgroundApi?.let { JSONObject(it.toString()) })
        .put("decoration_entities", visuals.size).put("window_controls",WindowControls.resources()).put("window_mode_badges",WindowModeBadge.resources()).put("window_menus",WindowMenuView.resources())
        .put("targets", JSONArray(bindings.values.map { JSONObject().put("runtime",it.handle.toString()).put("entity",it.entity.id.toString()).put("target",JSONObject(it.key.toString())).put("role",it.role)
            .put("retiring",it.retire).put("sdk_disposed",it.sdkDisposed).put("disposal_failed",it.disposalFailed)
            .put("listener_owned",it.listener!=null).put("input_owned",it.input!=null)
            .put("group",groupMetadata(it)).put("decoration",it.decoration).put("visual_entity",it.visual?.id?.toString()).put("controls",it.controls?.metadata()).put("move",it.move?.metadata()).put("mode_badge",it.modeBadge?.metadata()).put("menu",it.menu?.metadata())
            .put("visual_collision",if(!it.sdkDisposed && canUseSdk(it)) it.visual?.getComponent<Panel>()?.hittable?.name else null)
            .put("input_collision",if(!it.sdkDisposed && canUseSdk(it) && it.decoration!=null) it.entity.getComponent<Hittable>().hittable.name else null) }))
        .put("group_geometry_pending",NativeSessionHost.windowGroupGeometryPending())
        .put("retired",JSONArray(retired.toList()))
        .put("trace", JSONArray(trace.toList()))

    private fun event(name: String, entity: Long = 0, tid: Int = Process.myTid()) {
        val record = JSONObject().put("event", name).put("thread", tid).put("elapsed_ms", SystemClock.elapsedRealtime())
            .put("scene", scene.toString()).put("entity", entity.toString())
        bindings.values.firstOrNull { it.entity.id == entity && it.sdkScene === runtimeScene }?.let { record.put("target", JSONObject(it.key.toString())) }
        if (trace.size == 128) trace.removeFirst()
        trace.addLast(record)
        Log.i("EidolonSurface", record.toString())
    }
    private fun key(): JSONObject = JSONObject().put("session", "1").put("window", "1").put("scene", scene.toString())
        .put("generation", generations.incrementAndGet().toString())
    private fun create(handle: Long, plan: JSONObject? = null, initialPose: JSONObject? = null,
        group:WindowGroupPresentation.Selection=WindowGroupPresentation.Unowned) {
        val initial=(group as? WindowGroupPresentation.Owned)?.pose ?: initialPose
        val pose = if(plan==null) Pose(Vector3(0f,1.4f,1.6f)) else initial?.takeIf {PanelPoseCodec.valid(it)}?.let {PanelPoseCodec.toSdk(it)} ?: return
        val sdkScene = runtimeScene ?: return
        val ownerKey = if(plan==null) key() else JSONObject(plan.getJSONObject("target").toString())
        val strict = plan?.optBoolean("v3_evidence_required", false) == true
        // Runtime saving owns diagnostics/legacy panels as well as v3 app panels.
        // Reserve all producers before the first SDK effect. Only v3 has a native Create receipt.
        val lease = SdkEvidenceBridge.Lease(handle,ownerKey.getString("session").toLong(),
            ownerKey.getString("window").toLong(),ownerKey.getString("scene").toLong(),
            ownerKey.getString("generation").toLong(),if (strict) checkNotNull(plan).getString("command_seq").toLong() else null,adapterOwner)
        val fence = deletionFence?.takeIf { it.owns(sdkScene) } ?: return
        if (!evidence.reserve(lease)) return
        val scope = SdkResourceScope(lease,evidence,fence) {
            if (lease.createCommand != null) NativeSessionHost.sdkEvidence(lease,"retiring")
        }
        val registration = if (strict) NativeSessionHost.registerSdkLease(lease)
            else NativeSessionHost.sdkBinding(ownerKey,handle,true,diagnostic=plan==null)
        if (registration != NativeBridge.ACCEPTED) {
            evidence.registrationRejected(lease);evidence.sealLease(lease);evidence.collectCompleted(lease)
            return
        }
        evidence.registered(lease)
        var entity: Entity? = null
        var binding: Binding? = null
        try {
            val decoration = plan?.optJSONObject("decoration")
            // Install immutable callback ownership before adding Panel, which
            // can immediately invoke SDK producers on another thread.
            val allocate = { if (decoration == null) Entity.create(listOf(Transform(pose)))
                else Entity.create(listOf(Mesh(android.net.Uri.parse(ThemeHitMesh.URI), ThemeHitMesh.COLLISION), Transform(pose))) }
            val createdEntity = scope.createEntity(allocate)
            entity = createdEntity
            check(reservePanelEntity(sdkScene,createdEntity.id)) { "SDK_PANEL_ENTITY_REUSED" }
            val current = if (plan == null) Binding(createdEntity, handle, ownerKey, sdkScene, scope) else
                Binding(createdEntity, handle, ownerKey, sdkScene, scope, role="windows", commandSeq=plan.getString("command_seq"), decoration=decoration)
            binding = current
            bindings[current.allocation] = current
            callbackOwners[SceneEntity(sdkScene,createdEntity.id)] = CallbackOwner(current,sdkScene)
            if (decoration == null) createdEntity.setComponent(Panel(R.id.native_panel))
            if(plan!=null) createdEntity.setComponent(Grabbable(false,GrabbableType.FACE,false,0f,4f))
            current.normalCollision=if(plan==null) null else createdEntity.getComponent<Hittable>().hittable
            current.group=group
            if(group==WindowGroupPresentation.Pending) {
                createdEntity.setComponent(Visible(false));createdEntity.setComponent(Hittable(MeshCollision.NoCollision));current.groupVisible=false
            }
            created++; event("entity_created", createdEntity.id)
            if (decoration != null) {
                val content = decoration.getJSONObject("content"); val canvas = decoration.getJSONObject("canvas")
                val origin = decoration.getJSONArray("content_origin")
                val w=content.getDouble("width"); val h=content.getDouble("height")
                val cw=canvas.getInt("width"); val ch=canvas.getInt("height")
                val offset = Vector3(((cw-w-2*origin.getInt(0))*.6/w).toFloat(), (-(ch-h-2*origin.getInt(1))*.45/h).toFloat(), 0f)
                val allocateVisual = { Entity.create(listOf(Transform(Pose(offset)), TransformParent(createdEntity),
                    Scale(Vector3((cw/w).toFloat(),(ch/h).toFloat(),1f)), Hittable(MeshCollision.NoCollision),Visible(current.groupVisible))) }
                val visual = scope.createEntity(allocateVisual)
                current.visual = visual
                check(reservePanelEntity(sdkScene,visual.id)) { "SDK_PANEL_ENTITY_REUSED" }
                callbackOwners[SceneEntity(sdkScene,visual.id)] = CallbackOwner(current,sdkScene)
                visuals[SceneEntity(sdkScene,visual.id)] = VisualConfig(cw,ch,scene)
                visual.setComponent(Panel(R.id.decoration_panel, MeshCollision.NoCollision))
                val system = checkNotNull(objects)
                val live = { !closed && runtimeScene === sdkScene && bindings[current.allocation] === current && !current.destroy }
                val received: (SceneObject?, Throwable?) -> Unit = { obj, failure ->
                    if (failure == null && obj != null) objectReady(current,obj)
                    else { NativeSessionHost.ackPanel(checkNotNull(plan),handle,false); beginRetirement(current); current.destroy=true }
                }
                scope.sceneObject({ system.getSceneObject(createdEntity) ?: system.replaceOrReuseSceneObjectFuture(createdEntity) },live,received)
            }
        } catch (failure: Exception) {
            if (failure !is SdkLeaseRevoked) scope.failed()
            val current=binding
            if(current!=null) {beginRetirement(current);current.destroy=true}
            else {
                if (failure is SdkLeaseRevoked && entity == null) {
                    // Native registration won its race with Stop, but the
                    // SDK admission was revoked before any Entity allocation.
                    scope.seal();scope.rendererDetached()
                    cancelledReservations[scope.lease]=scope
                } else {
                    // A thrown SDK factory can have partial effects. Keep the
                    // reservation and native binding rather than invent no-effect.
                    scope.seal()
                }
                if (strict) NativeSessionHost.sdkEvidence(scope.lease,"retiring")
            }
            if (failure !is SdkLeaseRevoked) plan?.let { NativeSessionHost.ackPanel(it,handle,false) }
            throw failure
        }
    }
    private fun beginRetirement(current: Binding) {
        current.retire = true
        val scope = current.scope ?: return
        scope.seal()
        current.evidenceCurrent = false
        if (scope.lease.createCommand != null && !current.retirementSent) current.retirementSent =
            NativeSessionHost.sdkEvidence(scope.lease,"retiring") == NativeBridge.ACCEPTED
    }

    private fun publishCurrent(current: Binding) {
        val scope = current.scope ?: return
        if (scope.lease.createCommand == null) return
        if (current.retire || current.destroy || closed || current.sdkDisposed || !scope.open()) {
            beginRetirement(current)
            return
        }
        if (!current.attached || current.surface?.isValid != true ||
            current.sceneObject?.handle?.let { it != 0L } != true || current.listener == null) return
        if (!current.evidenceCurrent) current.evidenceCurrent =
            NativeSessionHost.sdkEvidence(scope.lease,"current") == NativeBridge.ACCEPTED
    }
    private fun surfaceReady(current: Binding, surface: Surface) {
        val entity = current.entity.id
        if (bindings[current.allocation] !== current || runtimeScene !== current.sdkScene) return
        if (closed || current.destroy || !surface.isValid) return
        if (current.attached) {
            if (current.surface === surface) { event("surface_duplicate", entity); return }
            current.replacement = surface; beginRetirement(current)
            if (current.role == "windows") current.destroy = true
        } else {
            current.surface = surface
            val code = attach(current, surface)
            current.attached = code == NativeBridge.ACCEPTED
            current.everAttached = current.everAttached || current.attached
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
    private fun objectReady(current: Binding, obj: SceneObject) {
        val entity = current.entity.id
        if (bindings[current.allocation] !== current || runtimeScene !== current.sdkScene) return
        if (current.scope != null && current.sceneObject != null && current.sceneObject !== obj) {
            current.scope.failed();beginRetirement(current);current.destroy=true
            return
        }
        if (current.listener == null && !closed && !current.destroy) {
            current.sceneObject = obj
            current.input = InputAdapter(current.key,current.scope) { source,state ->
                if(current.editing && !current.retire && bindings[current.allocation]===current) {
                    if(state==0) current.grabSources.remove(source.id)
                    else if(current.grabSources.size<8 || source.id in current.grabSources) current.grabSources[source.id]=source to state
                }
            }
            current.listener = obj.addInputListener(current.scope?.listener(checkNotNull(current.input)) ?: checkNotNull(current.input))
            listeners++; event("listener_added", entity)
        }
    }
    private fun cancelGrab(current: Binding) {
        if (!canUseSdk(current)) { current.grabSources.clear(); return }
        val sdk=current.sdkScene
        current.grabSources.values.toList().forEach { (source,state) -> sdk.onNoIntersectionInput(source,state,0) }
        sdk.resolveInput();current.grabSources.clear()
    }
    private fun canUseSdk(current: Binding): Boolean = runtimeScene === current.sdkScene && current.scope?.sceneLive() != false
    private fun closeControls(current:Binding) {
        current.controls?.let {controls ->
            controls.close()
            // close自体が捕捉したSDK失敗も、成功receiptから取り除く。
            if(controls.metadata().optLong("failures")!=0L) {current.disposalFailed=true;current.scope?.failed()}
        };current.controls=null
    }
    private fun disposeSdk(current: Binding):Boolean {
        current.groupAckRequest?.let(NativeSessionHost::forgetWindowGroupGeometry);current.groupAckRequest=null;current.groupAckFor=null
        if(current.sdkDisposed) return !current.disposalFailed
        if (current.scope?.sceneLive() == false) {
            // This Scene's DataModel is already unavailable. Keep the identity
            // pending; accessing its SDK objects now could hit a reused ID.
            if (!current.disposalFailed) event("sdk_scene_closed_before_disposal",current.entity.id)
            current.disposalFailed=true;current.scope.failed()
            return false
        }
        // A menu owns a separate operation and may need another tick to finish native cleanup.
        // Keep its controller and the binding before marking the remaining SDK disposal complete.
        try {current.menu?.close();current.menu=null}
        catch(failure:Exception) {event("menu_cleanup_pending_${failure.javaClass.simpleName}",current.entity.id);return false}
        current.sdkDisposed=true
        fun dispose(action:()->Unit) {runCatching(action).onFailure {current.disposalFailed=true;current.scope?.failed();event("sdk_dispose_failure_${it.javaClass.simpleName}",current.entity.id)}}
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
        current.visual?.let { visual ->
            visuals.remove(SceneEntity(current.sdkScene,visual.id));callbackOwners.remove(SceneEntity(current.sdkScene,visual.id))
            dispose { current.scope?.destroyEntity(visual) ?: visual.destroy() }
        }; current.visual=null
        callbackOwners.remove(SceneEntity(current.sdkScene,current.entity.id))
        dispose {current.scope?.destroyEntity(current.entity) ?: current.entity.destroy()}
        destroyed++; event("entity_destroyed", current.entity.id)
        if (!current.disposalFailed) current.scope?.auxiliaryDisposed()
        return !current.disposalFailed
    }
    private fun destroy(current: Binding) {
        if(!disposeSdk(current)) return
        val scope = current.scope
        if (scope != null) {
            // No accepted attach means no SDK->native Surface transfer was
            // admitted; native still verifies its exact renderer fence.
            if (!current.everAttached) scope.rendererDetached()
            if (!scope.readyToRelease()) return
            val released = if (scope.lease.createCommand != null) NativeSessionHost.sdkEvidence(scope.lease,"retired")
                else NativeSessionHost.sdkBinding(current.ownerKey,current.handle,false)
            if (released != NativeBridge.ACCEPTED) return
            scope.nativeReleased()
        } else {
            val code=NativeSessionHost.sdkBinding(current.ownerKey,current.handle,false)
            if(code!=NativeBridge.ACCEPTED) return
        }
        bindings.remove(current.allocation)
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
                // create() reports failure with the captured runtime/command.
                // Never redirect an old callback to the currently selected owner.
            }
            if (!closed || bindings.isNotEmpty() || cancelledReservations.isNotEmpty()) ui.postDelayed(this, 32)
        }
    }
    private fun tick() {
        val now = SystemClock.elapsedRealtime()
        for ((lease,scope) in cancelledReservations.toList()) {
            val target = JSONObject().put("session",lease.session.toString()).put("window",lease.window.toString())
                .put("scene",lease.scene.toString()).put("generation",lease.generation.toString())
            if (scope.readyToRelease() && (if (lease.createCommand != null) NativeSessionHost.sdkEvidence(lease,"retired")
                    else NativeSessionHost.sdkBinding(target,lease.runtime,false)) == NativeBridge.ACCEPTED) {
                scope.nativeReleased();cancelledReservations.remove(lease)
            }
        }
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
                beginRetirement(current); current.destroy = true
            }
            if (current.scope != null && (!current.scope.open() || current.surface?.isValid == false || current.sceneObject?.handle == 0L)) {
                beginRetirement(current); current.destroy = true
            }
            if (current.role == "windows" && plan?.optString("action") == "destroy") current.destroyCommand = plan
            val snapshot = JSONObject(NativeBridge.nativeSnapshot(current.handle))
            if (snapshot.getInt("code") == NativeBridge.STALE) {
                beginRetirement(current)
                if (current.scope != null) current.scope.failed()
                destroy(current)
            }
            else {
                val render = snapshot.getJSONObject("runtime").getJSONObject("render")
                if(current.role=="windows" && !current.retire) {
                    val state=snapshot.getJSONObject("runtime")
                    val grouped=applyGroup(current,state)
                    if(plan?.optBoolean("applied")==true) {
                        if(!grouped) {
                            tickMove(current,state)
                            if(current.move?.blocksPose()!=true) {applyPose(current,state);applyPhysical(current,state)}
                        }
                        applyControls(current,state)
                        if(grouped) acknowledgeGroup(current,state)
                    }
                }
                publishCurrent(current)
                if (current.role == "windows" && !current.retire && plan != null && !plan.getBoolean("applied")) {
                    val targets = render.getJSONArray("targets")
                    if (current.listener != null && (0 until targets.length()).any {
                        val frame=targets.getJSONObject(it)
                        sameKey(frame.getJSONObject("key"), current.key) && frame.getLong("frames") > 0 &&
                            (current.scope?.lease?.createCommand == null || current.evidenceCurrent && frame.optString("role")=="windows" &&
                                !frame.isNull("content_frame") && !frame.optBoolean("presentation_paused") && !render.optBoolean("paused"))
                    })
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
                    current.attached = false; current.scope?.rendererDetached(); event("surface_detached", current.entity.id)
                    if (!current.retire) { beginRetirement(current); current.destroy = true }
                }
                if (current.retire) {
                    beginRetirement(current)
                    if (current.role == "windows" && !current.hidden && !current.sdkDisposed && canUseSdk(current)) {
                        cancelGrab(current);current.entity.setComponent(Grabbable(false,GrabbableType.FACE,false,0f,4f))
                        current.entity.setComponent(Visible(false)); current.hidden = true
                        // IgnoreVisibleの入力面も、native Surface解放待ちに入る前に無効化する。
                        current.entity.setComponent(Hittable(MeshCollision.NoCollision))
                        current.visual?.setComponent(Visible(false))
                        NativeSessionHost.resetInputForRetiringTarget(current.ownerKey,current.handle)
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
                            if (replacement != null) surfaceReady(current, replacement)
                        }
                    }
                } else if (!current.attached && current.surface != null) surfaceReady(current, current.surface!!)
            }
        }
        // 一度もEntityを作らなかった旧sceneのdestroyも、native参照が無ければackする。
        if (host != null) {
            val targets = host.getJSONObject("render").getJSONArray("targets")
            plans.filter { it.getString("action") == "destroy" && it.isNull("error") }.forEach { plan ->
                if (!plan.optBoolean("v3_evidence_required",false) && bindings.values.none { sameKey(it.key,plan.getJSONObject("target")) } && (0 until targets.length()).none { sameKey(targets.getJSONObject(it).getJSONObject("key"), plan.getJSONObject("target")) })
                    NativeSessionHost.ackPanel(plan, handle)
            }
        }
        if (!closed && scene > 0 && host?.optString("state") == "Ready" && host.optString("scene") == scene.toString() &&
            !evidence.runtimeProgress(handle).sealed) {
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
                        if(initial!=null && PanelPoseCodec.valid(initial)) create(handle,plan,initial,
                            WindowGroupPresentation.select(handle,target,host.optJSONObject("window_groups")))
                    }
                }
            } else if (!windows && bindings.isEmpty()) create(handle)
        }
        if (now - lastPublish >= 1000 && scene==NativeSessionHost.currentSceneEpoch()) { NativeSessionHost.publishAdapter(metrics()); lastPublish = now }
    }
    private fun applyControls(current: Binding, runtime: JSONObject) {
        if(current.retire || current.destroy || current.sdkDisposed) {
            current.menu?.close();current.menu=null
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
        val menuStamp="$controlTarget:$revision:$config"
        if(current.menuStamp!=menuStamp) {
            current.menu?.close();current.menu=null;current.menuStamp=menuStamp
            val live={ !closed && scene==NativeSessionHost.currentSceneEpoch() && bindings[current.allocation]===current && !current.retire && !current.destroy &&
                current.handle==NativeSessionHost.currentHandle() && sameKey(current.key,JSONObject(controlTarget)) }
            current.menu=WindowMenuController(current.entity,controlTarget,current.handle,revision,config,checkNotNull(runtimeScene),checkNotNull(objects),live,current.scope)
        }
        val stamp="$controlTarget:$revision:$config:$width:$height"
        if(current.controlsStamp!=stamp) {
            current.move?.close();current.move=null;current.modeBadge?.close();current.modeBadge=null
            current.controls?.close();current.controls=null
            current.controlsStamp=stamp
            runCatching {
                val live={ !closed && scene==NativeSessionHost.currentSceneEpoch() && bindings[current.allocation]===current && !current.retire && !current.destroy &&
                    current.handle==NativeSessionHost.currentHandle() && sameKey(current.key,JSONObject(controlTarget)) }
                val move=WindowMoveController(current.entity,controlTarget,current.handle,revision,checkNotNull(runtimeScene),live)
                current.move=move
                val createControls={WindowControls(current.entity,controlTarget,current.handle,checkNotNull(runtimeScene),checkNotNull(objects),
                    live,config,width,height,revision,move,current.scope) {ticket -> current.menu?.open(ticket)==true}}
                current.controls=current.scope?.produce(createControls) ?: createControls()
                val createBadge={WindowModeBadge(current.entity,config,width,height,checkNotNull(runtimeScene),checkNotNull(objects),live,current.scope)}
                current.modeBadge=current.scope?.produce(createBadge) ?: createBadge()
            }.onFailure {
                if (it !is SdkLeaseRevoked) current.scope?.failed()
                current.move?.close();current.move=null;current.modeBadge?.close();current.modeBadge=null;current.controls?.close();current.controls=null
                event("controls_create_failure_${it.javaClass.simpleName}",current.entity.id)
            }
        }
        val visible=runtime.getBoolean("activity_visible") && runtime.getString("xr_state")=="FOCUSED"
        val group=current.group as? WindowGroupPresentation.Owned
        val groupHidden=current.group==WindowGroupPresentation.Pending || group?.let {it.phase=="stable" && !it.inputAllowed}==true
        current.controls?.setVisible(visible && !groupHidden)
        current.controls?.setMenuOnly(group?.phase=="animating")
        current.controls?.setEnabled(!current.editing && visible && !groupHidden &&
            (group==null || group.phase in setOf("settling","stable") || group.phase=="animating" && group.front && group.sourceReady))
        val controls=current.controls?.metadata()
        val controlsReady=controls?.optBoolean("enabled")==true && controls.optLong("failures")==0L &&
            controls.optJSONArray("buttons")?.let {buttons -> buttons.length()==4 && (0 until buttons.length()).all {buttons.getJSONObject(it).optBoolean("ready")}}==true &&
            current.modeBadge?.metadata()?.optBoolean("object_ready")==true
        current.menu?.tick(runtime,controlsReady)
        val input=runtime.optJSONObject("app")?.optJSONObject("input")
        val focused=input!=null && !input.isNull("focus_token") && NativeSessionHost.keyboardFocusMatches(current.key,input.getString("focus_token"))
        val groupMode=when {
            current.menu?.groupMenuActive==true && current.menu?.phase==WindowSizeCapture.Phase.MENU -> WindowMode.MENU
            current.menu?.groupMenuActive==true -> WindowMode.GROUP_BLOCKED
            current.group==WindowGroupPresentation.Pending -> WindowMode.GROUP_BLOCKED
            group==null -> null
            !group.sourceReady || group.phase=="blocked" -> WindowMode.GROUP_BLOCKED
            group.phase=="animating" -> WindowMode.GROUP_MOVING
            group.phase=="settling" -> WindowMode.GROUP_PLACING
            !group.inputAllowed -> WindowMode.GROUP_BACK
            else -> null
        }
        current.modeBadge?.update(groupMode ?: WindowMode.from(NativeSessionHost.inputTarget(controlTarget)!=null,visible,current.editing,current.move?.phase,NativeSessionHost.inputStatus.value.reason,focused,current.menu?.phase,recentering=current.menu?.recentering==true),visible && !groupHidden)
    }
    private fun groupMetadata(current:Binding):JSONObject?=when(val group=current.group) {
        WindowGroupPresentation.Unowned -> null
        WindowGroupPresentation.Pending -> JSONObject().put("phase","pending_capture").put("visible",current.groupVisible)
        is WindowGroupPresentation.Owned -> JSONObject().put("group",group.group).put("revision",group.revision).put("phase",group.phase)
            .put("front",group.front).put("input_allowed",group.inputAllowed).put("visible",current.groupVisible)
            .put("body_geometry",current.groupBodyGeometry).put("control_geometry",current.groupControlGeometry)
            .put("component_pose",runCatching {if(current.sdkDisposed || !canUseSdk(current)) null else PanelPoseCodec.fromSdk(getAbsoluteTransform(current.entity))}.getOrNull() ?: JSONObject.NULL)
            .put("ack_request",current.groupAckRequest ?: JSONObject.NULL).put("ack_failed",current.groupAckFailed ?: JSONObject.NULL)
    }
    private fun groupVisible(current:Binding,value:Boolean,collision:Boolean) {
        if(current.groupVisible!=value) {current.entity.setComponent(Visible(value));current.visual?.setComponent(Visible(value));current.groupVisible=value}
        if(current.groupCollision!=collision) {current.entity.setComponent(Hittable(if(collision) checkNotNull(current.normalCollision) else MeshCollision.NoCollision));current.groupCollision=collision}
        if(!value) {current.controls?.setEnabled(false);current.controls?.setVisible(false);current.modeBadge?.update(WindowMode.GROUP_BLOCKED,false)}
    }
    /** Returns true while the group owns SDK pose, including a scene/Surface capture gap. */
    private fun applyGroup(current:Binding,state:JSONObject):Boolean {
        val selection=WindowGroupPresentation.select(current.handle,current.key,state.optJSONObject("window_groups"))
        if(selection==WindowGroupPresentation.Unowned) {
            if(current.group!=WindowGroupPresentation.Unowned) {
                current.groupAckRequest?.let(NativeSessionHost::forgetWindowGroupGeometry);current.groupAckRequest=null;current.groupAckFor=null
                current.groupAckStamp=null;current.groupAckFailed=null;current.groupBodyGeometry=false;current.groupControlGeometry=false;current.groupSize=null
                groupVisible(current,true,true);current.input?.setSuppressed(false)
            }
            current.group=selection;return false
        }
        current.move?.cancelForGroup()
        val menuClosed=current.menu?.cancelForGroup(state) ?: true
        cancelGrab(current);current.entity.setComponent(Grabbable(false,GrabbableType.FACE,false,0f,4f))
        current.editing=false;current.wasGrabbed=false;current.queuedPose=null;current.lastDragPose=null
        current.poseRequest?.let(NativeSessionHost::forgetPose);current.poseRequest=null;current.poseAccepted=false
        val previous=current.group as? WindowGroupPresentation.Owned
        current.group=selection
        val group=selection as? WindowGroupPresentation.Owned
        if(previous?.stamp!=group?.stamp) {
            current.groupAckRequest?.let(NativeSessionHost::forgetWindowGroupGeometry);current.groupAckRequest=null;current.groupAckFor=null
            current.groupAckStamp=null;current.groupAckFailed=null;current.groupBodyGeometry=false;current.groupControlGeometry=false;current.groupSize=null
        }
        if(group==null || !menuClosed) {current.input?.setSuppressed(true);groupVisible(current,false,false);return true}
        val actual=PanelPoseCodec.fromSdk(getAbsoluteTransform(current.entity))
        if(actual==null || !PanelPoseCodec.near(actual,group.pose)) setAbsoluteTransform(current.entity,PanelPoseCodec.toSdk(group.pose))
        if(current.groupSize!=group.size.toString()) {
            current.entity.setComponent(Scale(Vector3(group.size.getDouble("width_m").toFloat()/1.2f,group.size.getDouble("height_m").toFloat()/.9f,1f)))
            current.groupSize=group.size.toString()
        }
        val visible=state.getBoolean("activity_visible") && state.getString("xr_state")=="FOCUSED"
        // Settling colliders stay alive for real geometry probes; Host/native still reject all member input.
        groupVisible(current,visible,visible && (group.phase!="stable" || group.inputAllowed))
        current.input?.setSuppressed(!group.inputAllowed || !group.sourceReady)
        return true
    }
    private fun acknowledgeGroup(current:Binding,state:JSONObject) {
        val group=current.group as? WindowGroupPresentation.Owned ?: return
        if(group.geometryApplied) {
            current.groupAckRequest?.let(NativeSessionHost::forgetWindowGroupGeometry);current.groupAckRequest=null;current.groupAckFor=null;current.groupAckStamp=group.stamp
            return
        }
        current.groupAckRequest?.let {request ->
            if(current.groupAckFor!=group.stamp) {
                NativeSessionHost.forgetWindowGroupGeometry(request);current.groupAckRequest=null;current.groupAckFor=null
            } else {
                val code=NativeSessionHost.windowGroupGeometryCompletion(request)
                if(code!=null && code!="pending") {
                    current.groupAckRequest=null;current.groupAckFor=null
                    if(code=="ACCEPTED") current.groupAckStamp=group.stamp else current.groupAckFailed=group.stamp
                }
            }
        }
        if(!group.settling || group.requiredFrame==null || current.groupAckRequest!=null || current.groupAckStamp==group.stamp || current.groupAckFailed==group.stamp) return
        current.groupBodyGeometry=physicalGeometryMatches(current,group.size,true)
        current.groupControlGeometry=current.controls?.geometryMatches()==true && current.modeBadge?.metadata()?.optBoolean("object_ready")==true
        if(!current.groupBodyGeometry || !current.groupControlGeometry) return
        val frames=state.getJSONObject("render").getJSONArray("targets")
        val frame=(0 until frames.length()).map {frames.getJSONObject(it)}.singleOrNull {sameKey(it.getJSONObject("key"),current.key)} ?: return
        val content=(frame.opt("content_frame") as? String)?.takeIf {it.toLongOrNull()?.let {n->n>=group.requiredFrame}==true} ?: return
        if(frame.getLong("frames")<1 || frame.optBoolean("presentation_paused")) return
        val actual=PanelPoseCodec.fromSdk(getAbsoluteTransform(current.entity)) ?: return
        if(!PanelPoseCodec.near(actual,group.pose)) return
        current.groupAckRequest=NativeSessionHost.ackWindowGroupGeometry(group,actual,content)
        current.groupAckFor=if(current.groupAckRequest!=null) group.stamp else null
        if(current.groupAckRequest==null) current.groupAckFailed=group.stamp
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
    private fun physicalGeometryMatches(current: Binding, size: JSONObject,shortProbe:Boolean=false): Boolean {
        val sdk=runtimeScene ?: return false
        val pose=getAbsoluteTransform(current.entity)
        // 公開ray APIで実quadを測る。希望componentだけを読んでSDK適用済みとはしない。
        for((u,v) in listOf(.2 to .35,.8 to .65)) {
            val x=((u-.5)*size.getDouble("width_m")).toFloat();val y=((.5-v)*size.getDouble("height_m")).toFloat()
            val world=pose*Vector3(x,y,0f)
            val start=if(shortProbe) pose*Vector3(x,y,-.005f) else pose*Vector3(.27f,.31f,-1.3f)
            val end=if(shortProbe) pose*Vector3(x,y,.005f) else pose*Vector3(x+(x-.27f)*(.8f/1.3f),y+(y-.31f)*(.8f/1.3f),.8f)
            val hit=sdk.lineSegmentIntersect(start,end) ?: return false
            if(hit.entity?.id!=current.entity.id || kotlin.math.abs(hit.textureCoordinate.x-u)>=.005 || kotlin.math.abs(hit.textureCoordinate.y-v)>=.005 ||
                kotlin.math.abs(hit.point.x-world.x)>=.0001 || kotlin.math.abs(hit.point.y-world.y)>=.0001 || kotlin.math.abs(hit.point.z-world.z)>=.0001) return false
        }
        return true
    }
    companion object { private val generations = AtomicLong(0); private val adapters = AtomicLong(0)
        private val allocations = AtomicLong(0) }
}
