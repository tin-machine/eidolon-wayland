package io.github.tinmachine.eidolon

/** CPU time belongs to this APK process. One fully busy core is 100%; multiple cores may exceed it. */
enum class ProcessCpuStatus(val wireValue: String) {
    STOPPED("stopped"), WARMING_UP("warming_up"), AVAILABLE("available"), UNAVAILABLE("unavailable"), STALE("stale"),
}

data class ProcessCpuReading(
    val status: ProcessCpuStatus = ProcessCpuStatus.STOPPED,
    val percent: Double? = null,
    val sampledMonotonicMs: Long? = null,
    val intervalMs: Long? = null,
) {
    init {
        require(sampledMonotonicMs == null || sampledMonotonicMs >= 0)
        require(intervalMs == null || intervalMs > 0)
        require((status == ProcessCpuStatus.AVAILABLE) == (percent != null))
        require(percent == null || (percent.isFinite() && percent >= 0 && sampledMonotonicMs != null && intervalMs != null))
    }
}

/**
 * UI-owner sampler. Reads at most once per five seconds, without its own timer or worker.
 * elapsedRealtime includes sleep: this is the measured interval average, not instantaneous CPU.
 * Unavailable samples discard the baseline; no inferred zero or CPU-count normalization is used.
 */
class ProcessCpuTracker {
    private data class Counter(val elapsedMs: Long, val cpuMs: Long)
    private var active = false
    private var lastAttemptMs: Long? = null
    private var lastObservedMs: Long? = null
    private var baseline: Counter? = null
    private var reading = ProcessCpuReading()

    fun start() {
        if (active) return
        active = true
        lastAttemptMs = null
        lastObservedMs = null
        baseline = null
        reading = ProcessCpuReading(ProcessCpuStatus.WARMING_UP)
    }

    fun stop(): ProcessCpuReading {
        if (!active) return reading
        active = false
        lastAttemptMs = null
        lastObservedMs = null
        baseline = null
        reading = ProcessCpuReading()
        return reading
    }

    fun sample(elapsedMs: Long, readCpuMillis: () -> Long?): ProcessCpuReading {
        if (!active) return reading
        val previousAttempt = lastAttemptMs
        val previousObservation = lastObservedMs
        if (elapsedMs < 0 || (previousObservation != null && elapsedMs < previousObservation)) {
            lastAttemptMs = null
            lastObservedMs = null
            baseline = null
            reading = ProcessCpuReading(ProcessCpuStatus.UNAVAILABLE)
            return reading
        }
        lastObservedMs = elapsedMs
        // A duplicate/early battery notification preserves both the value and its original time.
        if (previousAttempt != null && elapsedMs - previousAttempt < INTERVAL_MS) return reading
        lastAttemptMs = elapsedMs
        val cpuMs = try { readCpuMillis() } catch (_: Exception) { null }
        if (cpuMs == null || cpuMs < 0) {
            baseline = null
            reading = ProcessCpuReading(ProcessCpuStatus.UNAVAILABLE, sampledMonotonicMs = elapsedMs)
            return reading
        }
        val previous = baseline
        baseline = Counter(elapsedMs, cpuMs)
        reading = when {
            previous == null -> ProcessCpuReading(ProcessCpuStatus.WARMING_UP, sampledMonotonicMs = elapsedMs)
            cpuMs < previous.cpuMs -> {
                baseline = null
                ProcessCpuReading(ProcessCpuStatus.UNAVAILABLE, sampledMonotonicMs = elapsedMs)
            }
            elapsedMs - previous.elapsedMs > STALE_AFTER_MS -> ProcessCpuReading(ProcessCpuStatus.STALE,
                sampledMonotonicMs = elapsedMs, intervalMs = elapsedMs - previous.elapsedMs)
            else -> {
                // Subtract checked nonnegative longs first (small deltas survive near Long.MAX_VALUE).
                // Convert before multiplication to avoid signed integer overflow for large CPU deltas.
                val interval = elapsedMs - previous.elapsedMs
                val percent = (cpuMs - previous.cpuMs).toDouble() / interval.toDouble() * 100.0
                ProcessCpuReading(ProcessCpuStatus.AVAILABLE, percent, elapsedMs, interval)
            }
        }
        return reading
    }

    companion object {
        const val INTERVAL_MS = 5000L
        const val STALE_AFTER_MS = 15000L
    }
}
