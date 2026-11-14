package io.github.tinmachine.eidolon

/**
 * Identity-only SDK ownership ledger. Reserving and sealing share one lock;
 * callbacks and producers admitted before seal keep their cleanup obligations.
 * No Activity, Scene, Surface, Entity or diagnostic snapshot is retained here.
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

    private data class EntityRecord(
        var destroyRequested: Boolean = false,
        var deleteObservedAt: Long? = null,
    )

    private data class Record(
        val lease: Lease,
        var sealed: Boolean = false,
        var registrationResolved: Boolean = false,
        var bindingRegistered: Boolean = false,
        var bindingReleased: Boolean = false,
        var rendererDetached: Boolean = false,
        var auxiliaryDisposed: Boolean = false,
        var lateCleanupAfterPass: Long? = null,
        var hadEntities: Boolean = false,
        val entities: MutableMap<Long, EntityRecord> = linkedMapOf(),
        var callbacks: Int = 0,
        var producers: Int = 0,
        var failed: Boolean = false,
    ) {
        // Used before the synchronous native retirement receipt. Requiring
        // bindingReleased here would make native and SDK wait for each other.
        fun readyToRelease(): Boolean =
            sealed && registrationResolved && !failed && callbacks == 0 && producers == 0 &&
                (!bindingRegistered || rendererDetached) && entities.isEmpty() &&
                (!hadEntities || auxiliaryDisposed) && lateCleanupAfterPass == null
        fun complete(): Boolean = readyToRelease() && (!bindingRegistered || bindingReleased)
    }

    private val lock = Any()
    private val records = linkedMapOf<Lease, Record>()
    private val completed = mutableMapOf<Triple<Long, Long, Long>, Int>()
    private val sealedRuntimes = mutableSetOf<Long>()
    private val sealedScenes = mutableSetOf<Pair<Long, Long>>()
    private val scenePass = mutableMapOf<Pair<Long, Long>, Long>()

    /** Must succeed before native registration or any SDK resource effect. */
    fun reserve(id: Lease): Boolean = synchronized(lock) {
        if (id.runtime in sealedRuntimes || (id.adapter to id.scene) in sealedScenes ||
            records.containsKey(id)) false
        else {
            records[id] = Record(id)
            true
        }
    }

    fun isOpen(id: Lease): Boolean = synchronized(lock) {
        records[id]?.let { !it.sealed && !it.failed && !it.bindingReleased } == true
    }

    /** Count the producer before asking the SDK for a future/factory. */
    fun beginProducer(id: Lease): Boolean = synchronized(lock) {
        val r = records[id] ?: return@synchronized false
        if (r.sealed || r.failed) return@synchronized false
        r.producers = Math.addExact(r.producers, 1)
        true
    }

    fun producerFinished(id: Lease) = synchronized(lock) {
        val r = requireNotNull(records[id])
        require(r.producers > 0)
        r.producers--
    }

    /** Admission and pending increment are atomic with seal. */
    fun enterCallback(id: Lease): CallbackTicket? = synchronized(lock) {
        val r = records[id] ?: return@synchronized null
        if (r.sealed || r.failed) return@synchronized null
        r.callbacks = Math.addExact(r.callbacks, 1)
        CallbackTicket(id)
    }

    inner class CallbackTicket internal constructor(private val id: Lease) : AutoCloseable {
        private var closed = false
        fun valid(): Boolean = synchronized(lock) {
            !closed && records[id]?.let { !it.sealed && !it.failed } == true
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
    // Entity zero is reserved for the pure ledger fixture's single Entity.
    // Production always supplies the SDK's actual ID, scoped by this lease.
    fun entityCreated(id: Lease, entity: Long = 0) = update(id) {
        require(!it.entities.containsKey(entity))
        it.hadEntities = true
        it.entities[entity] = EntityRecord()
    }
    fun rendererDetached(id: Lease) = update(id) { it.rendererDetached = true }
    fun destroyRequested(id: Lease, entity: Long = 0) = update(id) {
        requireNotNull(it.entities[entity]).destroyRequested = true
    }
    fun deletionExpected(id: Lease, entity: Long): Boolean = synchronized(lock) {
        records[id]?.entities?.get(entity)?.destroyRequested == true
    }
    /** Only a delete listener captured from the owning DataModel calls this. */
    fun deleteObserved(id: Lease, scenePassNumber: Long, entity: Long = 0) = synchronized(lock) {
        val r = requireNotNull(records[id])
        val own = r.entities[entity] ?: return@synchronized // duplicate after collection
        require(own.destroyRequested && scenePassNumber >= 0)
        require(scenePassNumber <= (scenePass[id.adapter to id.scene] ?: 0L))
        if (own.deleteObservedAt == null) own.deleteObservedAt = scenePassNumber
    }
    /** A new Scene cannot advance an old Scene's deletion fence. */
    fun sdkCleanupPass(adapter: Long, scene: Long, pass: Long) = synchronized(lock) {
        require(pass >= 0)
        val key = adapter to scene
        require(pass > (scenePass[key] ?: -1L))
        scenePass[key] = pass
        for (record in records.values) {
            if (record.lease.adapter == adapter && record.lease.scene == scene) {
                record.entities.entries.removeAll { (_, entity) ->
                    entity.deleteObservedAt?.let { pass > it } == true
                }
                if (record.lateCleanupAfterPass?.let { pass > it } == true) record.lateCleanupAfterPass = null
            }
        }
    }
    /** A late future cannot reuse a cleanup pass that preceded its disposal.
     * Two boundaries include a complete SDK execute interval even when this
     * request arrives after another system's work in the current frame.
     */
    fun lateObjectCleanupRequested(id: Lease) = synchronized(lock) {
        val r = requireNotNull(records[id])
        val after = Math.addExact(scenePass[id.adapter to id.scene] ?: 0L, 1L)
        r.lateCleanupAfterPass = maxOf(r.lateCleanupAfterPass ?: 0L, after)
    }
    fun auxiliaryDisposed(id: Lease) = update(id) { it.auxiliaryDisposed = true }
    fun nativeBindingReleased(id: Lease) = update(id) {
        require(!it.bindingRegistered || it.rendererDetached)
        it.bindingReleased = true
    }
    fun failed(id: Lease) = update(id) { it.failed = true }
    fun readyToRelease(id: Lease): Boolean = synchronized(lock) { records[id]?.readyToRelease() == true }
    fun complete(id: Lease): Boolean = synchronized(lock) { records[id]?.complete() == true }

    /** Called only after this exact native retirement receipt was accepted.
     * Native owns the Create anti-replay barrier; pending obligations never GC.
     */
    fun collectCompleted(id: Lease): Boolean = synchronized(lock) {
        val record = records[id] ?: return@synchronized false
        if (!record.complete()) return@synchronized false
        val key = Triple(id.runtime, id.adapter, id.scene)
        completed[key] = Math.addExact(completed[key] ?: 0, 1)
        records.remove(id)
        true
    }

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

    /** Empty is valid only for a sealed scope whose every producer reserved. */
    fun runtimeProgress(runtime: Long): Progress = synchronized(lock) {
        val own = records.values.filter { it.lease.runtime == runtime }
        progress(own, runtime in sealedRuntimes, completed.filterKeys { it.first == runtime }.values.sum())
    }
    fun sceneProgress(adapter: Long, scene: Long): Progress = synchronized(lock) {
        val own = records.values.filter { it.lease.adapter == adapter && it.lease.scene == scene }
        progress(own, (adapter to scene) in sealedScenes,
            completed.filterKeys { it.second == adapter && it.third == scene }.values.sum())
    }
    private fun progress(own: List<Record>, sealed: Boolean, collected: Int): Progress {
        val done = Math.addExact(collected, own.count { it.complete() })
        val issued = Math.addExact(collected, own.size)
        val failed = own.count { it.failed }
        return Progress(issued, done, own.sumOf { it.callbacks + it.producers },
            sealed, failed, sealed && failed == 0 && done == issued)
    }
}
