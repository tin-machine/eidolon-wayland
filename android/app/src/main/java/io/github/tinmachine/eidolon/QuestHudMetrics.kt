package io.github.tinmachine.eidolon

import org.json.JSONObject
import java.util.Locale

internal enum class HudColor { WHITE, BLUE, YELLOW, RED }

internal fun hudLoadColor(percent: Double): HudColor? = when {
    !percent.isFinite() || percent < 0 -> null
    percent < 70 -> HudColor.WHITE
    percent < 80 -> HudColor.BLUE
    percent < 90 -> HudColor.YELLOW
    else -> HudColor.RED
}

internal data class HudReading(val value: String, val color: HudColor = HudColor.WHITE)
internal data class HudDraw(
    val value: String,
    val targets: Int = 0,
    val samples: Long = 0,
    val coverageMs: Long = 0,
    val eligibleTargets: Int = 0,
)
internal data class HudDrawSample(val draw: HudDraw, val at: Long)

internal fun hudCpu(metrics: DeviceMetricsSnapshot, since: Long, now: Long): HudReading {
    val reading = metrics.processCpu
    val sampled = reading.sampledMonotonicMs
    val percent = reading.percent
    if (reading.status == ProcessCpuStatus.AVAILABLE && sampled != null && sampled >= since && now - sampled in 0..15_000 && percent != null) {
        hudLoadColor(percent)?.let { return HudReading(String.format(Locale.ROOT, "%.2f core", percent / 100), it) }
    }
    return HudReading(if (reading.status == ProcessCpuStatus.WARMING_UP || (sampled != null && sampled < since)) "— 計測中" else "— 取得待ち")
}

internal fun hudRam(metrics: DeviceMetricsSnapshot, since: Long, now: Long): HudReading {
    val available = metrics.availableRam
    val total = metrics.totalRam
    if (metrics.sampledMs < since || now - metrics.sampledMs !in 0..15_000 || available == null ||
        total == null || total <= 0 || available !in 0..total
    ) return HudReading("— 取得待ち")
    val used = 100.0 * (total - available) / total
    return HudReading(String.format(Locale.ROOT, "%.2f GiB", available / 1073741824.0), checkNotNull(hudLoadColor(used)))
}

internal fun hudThermal(metrics: DeviceMetricsSnapshot, since: Long, now: Long): String {
    val thermal = metrics.details.thermal
    val sampled = thermal.sampledMs
    val codes = listOf("NONE", "LIGHT", "MODERATE", "SEVERE", "CRITICAL", "EMERGENCY", "SHUTDOWN")
    val name = if (thermal.status == DeviceDetailStatus.AVAILABLE && sampled != null &&
        sampled >= since && now - sampled in 0..90_000
    ) thermal.code?.let(codes::getOrNull) else null
    return "OS熱: " + (name ?: "—")
}

/** The renderer supplies cumulative success counters for each live target. */
internal class QuestHudTiming {
    private data class Cut(val at: Long, val bins: List<Long>)
    private data class Interval(val from: Long, val to: Long, val bins: List<Long>)
    private data class History(var cut: Cut, val intervals: ArrayDeque<Interval> = ArrayDeque())
    private val histories = mutableMapOf<String, History>()
    private var owner = ""
    private var lastTime = -1L

    fun reset() {
        owner = ""
        lastTime = -1
        histories.clear()
    }

    fun sample(runtimeHandle: Long, runtime: JSONObject, now: Long): HudDraw {
        val render = runtime.optJSONObject("render")
        if (!runtime.optBoolean("activity_visible") || render == null || render.optBoolean("paused")) {
            reset()
            return HudDraw("— 停止中")
        }
        val nextOwner = "$runtimeHandle:${runtime.optString("scene")}:${render.optLong("context_rebuilds")}"
        if (owner != nextOwner || now < lastTime || now - lastTime > MAX_GAP_MS) reset()
        owner = nextOwner
        lastTime = now
        if (!render.isNull("error")) {
            histories.clear()
            return HudDraw("— 描画エラー")
        }
        val targets = render.optJSONArray("targets")
        val live = mutableSetOf<String>()
        var measured = 0
        var total = 0L
        var coverage = 0L
        var worst = -1
        for (i in 0 until (targets?.length() ?: 0)) {
            val target = targets?.optJSONObject(i) ?: continue
            if (target.optString("role") != "windows" || target.optBoolean("presentation_paused")) continue
            val key = target.optJSONObject("key") ?: continue
            val id = listOf("session", "window", "scene", "generation").joinToString(":") { key.optString(it) }
            if (!live.add(id)) continue
            val timing = target.optJSONObject("frame_timing")
            val bins = timing?.optJSONArray("histogram_ms")?.takeIf { it.length() == BIN_COUNT }
                ?.let { histogram -> List(BIN_COUNT) { histogram.optLong(it, -1) } }
            if (bins == null || sum(bins) != timing?.optLong("samples", -1) || bins.any { it < 0 }) {
                histories.remove(id)
                continue
            }
            val next = Cut(now, bins)
            val history = histories.getOrPut(id) { History(next) }
            val previous = history.cut
            if (now <= previous.at || bins.indices.any { bins[it] < previous.bins[it] }) {
                if (now < previous.at || bins != previous.bins) history.intervals.clear()
            } else {
                history.intervals.addLast(Interval(previous.at, now, bins.indices.map { bins[it] - previous.bins[it] }))
            }
            history.cut = next
            while (history.intervals.isNotEmpty() && (history.intervals.first().from < now - WINDOW_MS ||
                    history.intervals.size > MAX_INTERVALS)) history.intervals.removeFirst()
            val counts = LongArray(BIN_COUNT)
            for (interval in history.intervals) {
                for (bin in counts.indices) counts[bin] = Math.addExact(counts[bin], interval.bins[bin])
            }
            val count = sum(counts.asList()) ?: continue
            if (count == 0L) continue
            val rank = count - count / 20
            var cumulative = 0L
            val p95 = counts.indexOfFirst { cumulative += it; cumulative >= rank }
            worst = maxOf(worst, p95)
            measured++
            total = if (Long.MAX_VALUE - total < count) Long.MAX_VALUE else total + count
            coverage = maxOf(coverage, now - history.intervals.first().from)
        }
        histories.keys.retainAll(live)
        val value = when {
            live.isEmpty() -> "— 対象なし"
            worst < 0 -> "— サンプルなし"
            worst == BIN_COUNT - 1 -> "> 31 ms"
            else -> "≤ ${worst + 1} ms"
        }
        return HudDraw(value, measured, total, coverage, live.size)
    }

    private fun sum(values: List<Long>): Long? {
        var total = 0L
        for (value in values) {
            if (value < 0 || Long.MAX_VALUE - total < value) return null
            total += value
        }
        return total
    }

    private companion object {
        const val BIN_COUNT = 32
        const val WINDOW_MS = 10_000L
        const val MAX_GAP_MS = 2_500L
        const val MAX_INTERVALS = 12
    }
}
