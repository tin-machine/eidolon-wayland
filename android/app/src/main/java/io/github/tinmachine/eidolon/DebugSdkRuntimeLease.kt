package io.github.tinmachine.eidolon

/** Host workerの固定SDK検査owner。設定・鍵・Android Viewは持たず、回収証拠の順序だけを検査する。 */
class DebugSdkRuntimeLease {
    enum class Phase { IDLE, PREPARING, CREATING, ACTIVE, STOPPING, CLEANUP_REQUIRED, RESTORING }
    data class Metadata(val phase: Phase, val generation: Long, val runtime: Long, val previousRuntime: Long)
    private var owner: Any? = null
    private var phase = Phase.IDLE
    private var generation = 0L
    private var runtime = 0L
    private var previousRuntime = 0L
    private var probeRuntime = 0L

    @Synchronized fun metadata() = Metadata(phase, generation, runtime, previousRuntime)
    @Synchronized fun blocksProduct() = phase != Phase.IDLE
    @Synchronized fun permitsProduct(ticket: Long) = phase == Phase.IDLE && ticket == generation
    @Synchronized fun owns(candidate: Any) = owner === candidate && phase != Phase.IDLE
    @Synchronized fun claim(candidate: Any, previous: Long): Boolean {
        if (phase != Phase.IDLE || previous <= 0 || generation >= Long.MAX_VALUE - 1) return false
        generation++
        owner = candidate; previousRuntime = previous; runtime = previous; probeRuntime = 0
        phase = Phase.PREPARING
        return true
    }
    @Synchronized fun normalRetired(candidate: Any, retired: Long, registryEmpty: Boolean): Boolean {
        if (!owns(candidate) || phase != Phase.PREPARING || retired != previousRuntime || runtime != retired || !registryEmpty) return false
        runtime = 0; phase = Phase.CREATING
        return true
    }
    @Synchronized fun created(candidate: Any, created: Long): Boolean {
        if (!owns(candidate) || phase != Phase.CREATING || created <= 0 || created == previousRuntime) return false
        runtime = created; probeRuntime = created; phase = Phase.ACTIVE
        return true
    }
    @Synchronized fun createFailed(candidate: Any, registryEmpty: Boolean): Boolean {
        if (!owns(candidate) || phase != Phase.CREATING || runtime != 0L) return false
        phase = if (registryEmpty) Phase.RESTORING else Phase.CLEANUP_REQUIRED
        return registryEmpty
    }
    @Synchronized fun beginClose(candidate: Any): Long? {
        if (!owns(candidate) || phase !in setOf(Phase.ACTIVE, Phase.PREPARING, Phase.CLEANUP_REQUIRED, Phase.STOPPING, Phase.RESTORING)) return null
        phase = Phase.STOPPING
        return runtime
    }
    @Synchronized fun cleanupRequired(candidate: Any) {
        if (owns(candidate)) phase = Phase.CLEANUP_REQUIRED
    }
    @Synchronized fun retired(candidate: Any, retired: Long, registryEmpty: Boolean): Boolean {
        if (!owns(candidate) || phase != Phase.STOPPING || retired != runtime || !registryEmpty) return false
        runtime = 0; phase = Phase.RESTORING
        return true
    }
    @Synchronized fun restoreCreated(candidate: Any, created: Long): Boolean {
        if (!owns(candidate) || phase != Phase.RESTORING || runtime != 0L || created <= 0 ||
            created == previousRuntime || created == probeRuntime) return false
        runtime = created
        return true
    }
    @Synchronized fun restored(candidate: Any, restored: Long, readyAndIdle: Boolean): Boolean {
        if (!owns(candidate) || phase != Phase.RESTORING || runtime != restored || !readyAndIdle || restored <= 0 ||
            restored == previousRuntime || restored == probeRuntime) return false
        generation++
        owner = null; phase = Phase.IDLE; runtime = 0; previousRuntime = 0; probeRuntime = 0
        return true
    }
}
