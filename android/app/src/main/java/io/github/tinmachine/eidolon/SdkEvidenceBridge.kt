package io.github.tinmachine.eidolon

/**
 * I4's SDK ownership ledger. No Entity/Surface/Scene references are stored:
 * all identities are captured at native Create admission, never derived from
 * currentHandle, a reused Entity ID, or an Android JSON metrics ring.
 *
 * The SDK callback producer, native renderer fence and SDK deletion system
 * must supply their actual receipts. Unknown or timed-out work remains pending.
 */
internal class SdkEvidenceBridge {
    data class Lease(
        val runtime: Long,
        val session: Long,
        val window: Long,
        val scene: Long,
        val generation: Long,
        val createCommand: Long?,
        val adapter: Long,
    ) {
        init {
            require(runtime > 0 && session > 0 && window > 0 && scene > 0 &&
                generation > 0 && adapter > 0 && (createCommand == null || createCommand > 0))
        }
    }

    data class Progress(
        val issued: Int,
        val complete: Int,
        val pendingCallbacks: Int,
        val sealed: Boolean,
        val failures: Int,
        val allRetired: Boolean,
    )

    private data class Record(
        val lease: Lease,
        var sealed: Boolean = false,
        var registrationResolved: Boolean = false,
        var bindingRegistered: Boolean = false,
        var bindingReleased: Boolean = false,
        var entityCreated: Boolean = false,
        var entityDestroyRequested: Boolean = false,
        var deleteObservedAt: Long? = null,
        var postDeletePass: Long? = null,
        var rendererDetached: Boolean = false,
        var auxiliaryDisposed: Boolean = false,
        var callbacks: Int = 0,
        var producers: Int = 0,
        var failed: Boolean = false,
    ) {
        fun complete(): Boolean =
            sealed && registrationResolved && !failed && callbacks == 0 && producers == 0 &&
                (!bindingRegistered || bindingReleased) &&
                (!bindingRegistered || rendererDetached) &&
                (!entityCreated || (entityDestroyRequested &&
                    deleteObservedAt != null && postDeletePass != null &&
                    postDeletePass!! > deleteObservedAt!! && auxiliaryDisposed))
    }

    private val lock = Any()
    private val records = linkedMapOf<Lease, Record>()
    private val sealedRuntimes = mutableSetOf<Long>()
    private val sealedScenes = mutableSetOf<Pair<Long, Long>>()
    private val scenePass = mutableMapOf<Pair<Long, Long>, Long>()

    /** Reservation is linearized with scene/runtime sealing. */
    fun reserve(id: Lease): Boolean = synchronized(lock) {
        if (id.runtime in sealedRuntimes || (id.adapter to id.scene) in sealedScenes ||
            records.containsKey(id)) false
        else {
            records[id] = Record(id)
            true
        }
    }

    /** Producer admission must be counted before asynchronous work can create SDK resources. */
    fun beginProducer(id: Lease): Boolean = synchronized(lock) {
        val r = records[id] ?: return@synchronized false
        if (r.sealed) return@synchronized false
        r.producers = Math.addExact(r.producers, 1)
        true
    }

    fun producerFinished(id: Lease) = synchronized(lock) {
        val r = requireNotNull(records[id])
        require(r.producers > 0)
        r.producers--
    }

    /** Enter+pending increment is atomic with seal. Caller MUST close the ticket. */
    fun enterCallback(id: Lease): CallbackTicket? = synchronized(lock) {
        val r = records[id] ?: return@synchronized null
        if (r.sealed) return@synchronized null
        r.callbacks = Math.addExact(r.callbacks, 1)
        CallbackTicket(id)
    }

    inner class CallbackTicket internal constructor(private val id: Lease) : AutoCloseable {
        private var closed = false
        fun valid(): Boolean = synchronized(lock) {
            !closed && records[id]?.sealed == false
        }
        override fun close() = synchronized(lock) {
            if (!closed) {
                closed = true
                val r = requireNotNull(records[id])
                require(r.callbacks > 0)
                r.callbacks--
            }
        }
    }

    fun registered(id: Lease) = update(id) {
        it.registrationResolved = true
        it.bindingRegistered = true
    }
    fun registrationRejected(id: Lease) = update(id) {
        require(!it.bindingRegistered)
        it.registrationResolved = true
    }
    fun entityCreated(id: Lease) = update(id) { it.entityCreated = true }
    fun rendererDetached(id: Lease) = update(id) { it.rendererDetached = true }
    fun destroyRequested(id: Lease) = update(id) {
        require(it.entityCreated)
        it.entityDestroyRequested = true
    }
    /** Only the captured Scene's delete listener may call this. */
    fun deleteObserved(id: Lease, scenePassNumber: Long) = synchronized(lock) {
        val r = requireNotNull(records[id])
        require(r.entityDestroyRequested && scenePassNumber >= 0)
        require(scenePassNumber <= (scenePass[id.adapter to id.scene] ?: 0L))
        r.deleteObservedAt = scenePassNumber
    }
    /** Called by the SDK late-execute bridge; cannot be advanced by a new Scene. */
    fun sdkCleanupPass(adapter: Long, scene: Long, pass: Long) = synchronized(lock) {
        require(pass >= 0)
        val key = adapter to scene
        val previous = scenePass[key] ?: -1L
        require(pass > previous)
        scenePass[key] = pass
        for (record in records.values) {
            if (record.lease.adapter == adapter && record.lease.scene == scene &&
                record.deleteObservedAt != null && pass > record.deleteObservedAt!!) {
                record.postDeletePass = pass
            }
        }
    }
    fun auxiliaryDisposed(id: Lease) = update(id) { it.auxiliaryDisposed = true }
    fun nativeBindingReleased(id: Lease) = update(id) {
        require(!it.bindingRegistered || it.rendererDetached)
        it.bindingReleased = true
    }
    fun failed(id: Lease) = update(id) { it.failed = true }

    fun sealScene(adapter: Long, scene: Long) = synchronized(lock) {
        sealedScenes.add(adapter to scene)
        records.values.filter { it.lease.adapter == adapter && it.lease.scene == scene }
            .forEach { it.sealed = true }
    }
    fun sealRuntime(runtime: Long) = synchronized(lock) {
        sealedRuntimes.add(runtime)
        records.values.filter { it.lease.runtime == runtime }.forEach { it.sealed = true }
    }
    fun sealLease(id: Lease) = update(id) { it.sealed = true }

    private inline fun update(id: Lease, block: (Record) -> Unit) = synchronized(lock) {
        block(requireNotNull(records[id]))
    }

    /**
     * A zero-record sealed scope can be complete, but absence of a scope is
     * not a receipt. The caller must hold the returned ticket/capture.
     */
    fun runtimeProgress(runtime: Long): Progress = synchronized(lock) {
        val own = records.values.filter { it.lease.runtime == runtime }
        progress(own, runtime in sealedRuntimes)
    }
    fun sceneProgress(adapter: Long, scene: Long): Progress = synchronized(lock) {
        val own = records.values.filter { it.lease.adapter == adapter && it.lease.scene == scene }
        progress(own, (adapter to scene) in sealedScenes)
    }
    private fun progress(own: List<Record>, sealed: Boolean): Progress {
        val completed = own.count { it.complete() }
        val failed = own.count { it.failed }
        return Progress(own.size, completed, own.sumOf { it.callbacks + it.producers },
            sealed, failed, sealed && failed == 0 && completed == own.size)
    }
}
