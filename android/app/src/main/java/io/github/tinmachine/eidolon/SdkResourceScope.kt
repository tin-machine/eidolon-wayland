package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import com.meta.spatial.core.DataModel
import com.meta.spatial.core.DeleteListenerHolder
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Vector2
import com.meta.spatial.core.SystemBaseWithLateExecute
import com.meta.spatial.core.SpatialSDKExperimentalAPI
import com.meta.spatial.runtime.Scene
import com.meta.spatial.runtime.SceneObject
import com.meta.spatial.runtime.InputListener
import com.meta.spatial.runtime.HitInfo
import java.util.concurrent.CompletableFuture

/** Admission was revoked before the next SDK effect, which is not an SDK failure. */
internal class SdkLeaseRevoked : IllegalStateException("SDK_LEASE_SEALED")

/** A Create's SDK effects, including controls and menu children, share one lease. */
class SdkResourceScope internal constructor(
    internal val lease: SdkEvidenceBridge.Lease,
    private val evidence: SdkEvidenceBridge,
    private val deletion: SdkSceneDeletionFence,
    private val revoked: () -> Unit,
) {
    private val ui = Handler(Looper.getMainLooper())
    internal fun open(): Boolean = evidence.isOpen(lease) && deletion.live()
    internal fun sceneLive(): Boolean = deletion.live()
    internal fun seal() = evidence.sealLease(lease)
    internal fun failed() {
        evidence.failed(lease)
        // A broken SDK observation invalidates native permission immediately;
        // the UI poll remains responsible for collecting retirement evidence.
        revoked()
    }
    internal fun rendererDetached() = evidence.rendererDetached(lease)
    internal fun auxiliaryDisposed() = evidence.auxiliaryDisposed(lease)
    internal fun readyToRelease(): Boolean = evidence.readyToRelease(lease)
    internal fun nativeReleased() {
        evidence.nativeBindingReleased(lease)
        check(evidence.collectCompleted(lease))
    }

    internal fun createEntity(create: () -> Entity): Entity {
        val ticket = evidence.enterCallback(lease) ?: throw SdkLeaseRevoked()
        try {
            if (!ticket.valid() || !deletion.live()) throw SdkLeaseRevoked()
            val entity = create()
            evidence.entityCreated(lease, entity.id)
            deletion.track(entity.id, this)
            return entity
        } catch (failure: Exception) {
            if (failure !is SdkLeaseRevoked) failed()
            throw failure
        } finally {
            ticket.close()
        }
    }

    internal fun destroyEntity(entity: Entity) {
        try {
            check(deletion.live()) { "SDK_SCENE_CLOSED" }
            // Register before the SDK call: a synchronous delete notification
            // must not escape the inventory. A thrown request never completes.
            evidence.destroyRequested(lease, entity.id)
            entity.destroy()
        } catch (failure: Exception) {
            failed()
            throw failure
        }
    }

    /** A factory admitted before seal remains an obligation until it returns. */
    internal fun <T> produce(create: () -> T): T {
        if (!evidence.beginProducer(lease)) throw SdkLeaseRevoked()
        try {
            if (!open()) throw SdkLeaseRevoked()
            return create()
        } catch (failure: Exception) {
            if (failure !is SdkLeaseRevoked) failed()
            throw failure
        } finally {
            evidence.producerFinished(lease)
        }
    }

    /** Notification callbacks cannot create new work after seal. Pending spans JNI return. */
    internal fun post(action: () -> Unit) {
        val ticket = evidence.enterCallback(lease) ?: return
        if (!ui.post {
            try {
                if (ticket.valid() && deletion.live()) action()
            } catch (failure: Exception) {
                failed()
            } finally {
                ticket.close()
            }
        }) {
            failed()
            ticket.close()
        }
    }

    /** A known future is counted before it can create a SceneObject.
     * On cancellation only cleanup of its captured result is permitted.
     */
    internal fun sceneObject(
        future: () -> CompletableFuture<SceneObject>,
        live: () -> Boolean,
        received: (SceneObject?, Throwable?) -> Unit,
    ) {
        if (!evidence.beginProducer(lease)) throw SdkLeaseRevoked()
        val pending = try {
            if (!open()) throw SdkLeaseRevoked()
            future()
        } catch (failure: Exception) {
            if (failure !is SdkLeaseRevoked) failed()
            evidence.producerFinished(lease)
            throw failure
        }
        pending.whenComplete { obj, failure ->
            if (!ui.post {
                try {
                    if (open() && live()) received(obj, failure)
                    else if (obj != null) {
                        check(deletion.live()) { "SDK_LATE_OBJECT_AFTER_SCENE_CLOSE" }
                        evidence.lateObjectCleanupRequested(lease)
                        obj.destroy()
                    }
                } catch (error: Exception) {
                    failed()
                } finally {
                    evidence.producerFinished(lease)
                }
            }) {
                failed()
                evidence.producerFinished(lease)
            }
        }
    }

    private fun <T> callback(unavailable: T, action: () -> T): T {
        val ticket = evidence.enterCallback(lease) ?: return unavailable
        try {
            return if (ticket.valid() && deletion.live()) action() else unavailable
        } catch (failure: Exception) {
            failed()
            return unavailable
        } finally { ticket.close() }
    }
    internal fun listener(delegate: InputListener): InputListener = object : InputListener by delegate {
        override fun onInput(receiver: SceneObject, hitInfo: HitInfo, sourceOfInput: Entity, changed: Int, buttonState: Int, downTime: Long): Boolean =
            callback(false) { delegate.onInput(receiver,hitInfo,sourceOfInput,changed,buttonState,downTime) }
        override fun stopInput(receiver: SceneObject, sourceOfInput: Entity, downTime: Long) =
            callback(Unit) { delegate.stopInput(receiver,sourceOfInput,downTime) }
        override fun onClickDown(receiver: SceneObject, hitInfo: HitInfo, sourceOfInput: Entity) =
            callback(Unit) { delegate.onClickDown(receiver,hitInfo,sourceOfInput) }
        override fun onClick(receiver: SceneObject, hitInfo: HitInfo, sourceOfInput: Entity) =
            callback(Unit) { delegate.onClick(receiver,hitInfo,sourceOfInput) }
        override fun onHoverStart(receiver: SceneObject, sourceOfInput: Entity) =
            callback(Unit) { delegate.onHoverStart(receiver,sourceOfInput) }
        override fun onPointerEvent(receiver: SceneObject, hitInfo: HitInfo, type: Int, sourceOfInput: Entity, scrollInfo: Vector2, semanticType: Int) =
            callback(Unit) { delegate.onPointerEvent(receiver,hitInfo,type,sourceOfInput,scrollInfo,semanticType) }
        override fun onHoverStop(receiver: SceneObject, sourceOfInput: Entity) =
            callback(Unit) { delegate.onHoverStop(receiver,sourceOfInput) }
        override fun onDragMove(receiver: SceneObject, hitInfo: HitInfo, sourceOfInput: Entity, buttonState: Int) =
            callback(Unit) { delegate.onDragMove(receiver,hitInfo,sourceOfInput,buttonState) }
    }

    internal fun deleted(pass: Long, entity: Long) {
        try {
            evidence.deleteObserved(lease, pass, entity)
        } catch (failure: Exception) {
            // An unexpected deletion is not silently credited to a new owner.
            failed()
        }
    }
    internal fun deletionNotified(entity: Long) {
        // Retract permission at the actual notification, rather than waiting
        // for the next pass used to prove destruction has finished.
        if (!evidence.deletionExpected(lease,entity)) failed()
    }
}

/** One captured SDK DataModel/Scene. No new Scene can finish this fence. */
@OptIn(SpatialSDKExperimentalAPI::class)
internal class SdkSceneDeletionFence(
    private val adapter: Long,
    private val epoch: Long,
    scene: Scene,
    dataModel: DataModel,
    private val evidence: SdkEvidenceBridge,
) : AutoCloseable {
    private val lock = Any()
    private var scene: Scene? = scene
    private var model: DataModel? = dataModel
    private var pass = 0L
    private val owners = mutableMapOf<Long, SdkResourceScope>()
    private val notifications = linkedSetOf<Long>()
    private var holder: DeleteListenerHolder? = null

    init {
        evidence.sdkCleanupPass(adapter, epoch, 0)
        holder = dataModel.registerDeleteListener { entity -> synchronized(lock) {
            if (this.scene != null) owners[entity]?.let { owner ->
                owner.deletionNotified(entity)
                notifications.add(entity)
            }
        } }
    }
    fun live(): Boolean = synchronized(lock) { scene != null }
    fun owns(value: Scene): Boolean = synchronized(lock) { scene === value }
    fun track(entity: Long, scope: SdkResourceScope) = synchronized(lock) {
        check(scene != null && owners.putIfAbsent(entity, scope) == null) { "SDK_ENTITY_OWNER_CONFLICT" }
    }
    fun lateExecute(value: Scene) = synchronized(lock) {
        if (scene !== value) return@synchronized
        pass = Math.addExact(pass, 1)
        // First advance earlier observations; notifications collected in this
        // pass only become eligible at the next lateExecute boundary.
        evidence.sdkCleanupPass(adapter, epoch, pass)
        val deleted = notifications.toList()
        notifications.clear()
        deleted.forEach { entity -> owners.remove(entity)?.deleted(pass, entity) }
    }
    override fun close() = synchronized(lock) {
        if (scene == null) return@synchronized
        evidence.sealScene(adapter, epoch)
        // Called while this DataModel is still valid, before super shutdown.
        // A missing delete/pass remains pending; shutdown return is no receipt.
        try { holder?.let { model?.removeDeleteListener(it) } }
        finally { holder = null; model = null; scene = null; notifications.clear(); owners.clear() }
    }
}

/** Registered once with this Activity's manager; execute never fabricates a pass. */
internal class SdkDeletionFenceSystem : SystemBaseWithLateExecute() {
    @Volatile var active: SdkSceneDeletionFence? = null
    override fun execute() = Unit
    override fun lateExecute() { active?.lateExecute(getScene()) }
    override fun destroy() {
        active = null
        super.destroy()
    }
}
