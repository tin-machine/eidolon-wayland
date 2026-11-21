package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class QuestHudMetricsTest {
    @Test fun loadColorsUseUnroundedThresholdsAndInvalidIsMissing() {
        val white = listOf(0.0, 60.0, 60.1, 69.99)
        val blue = listOf(70.0, 79.99)
        val yellow = listOf(80.0, 89.99)
        val red = listOf(90.0, 100.0, 250.0)
        for ((color, values) in listOf(HudColor.WHITE to white, HudColor.BLUE to blue, HudColor.YELLOW to yellow, HudColor.RED to red)) {
            values.forEach { assertEquals(color, hudLoadColor(it)) }
        }
        assertNull(hudLoadColor(-1.0))
        assertNull(hudLoadColor(Double.NaN))
        assertNull(hudLoadColor(Double.POSITIVE_INFINITY))
    }

    @Test fun cpuUsesOneCoreBasisAndExpiresAtTheOriginalSampleTime() {
        val sample = DeviceMetricsSnapshot(processCpu = ProcessCpuReading(
            ProcessCpuStatus.AVAILABLE, 250.0, 5_000, 5_000,
        ))
        assertEquals(HudReading("2.50 core", HudColor.RED), hudCpu(sample, 1_000, 20_000))
        assertTrue(hudCpu(sample, 1_000, 20_001).value.startsWith("—"))
        assertTrue(hudCpu(sample, 5_001, 5_002).value.startsWith("—"))
        assertTrue(hudCpu(sample, 1_000, 4_999).value.startsWith("—"))
    }

    @Test fun ramUsesCurrentSampleAndKeepsValidZero() {
        val one = 1L shl 30
        val sample = DeviceMetricsSnapshot(sampledMs = 1_000, availableRam = one, totalRam = one * 4)
        assertEquals(HudReading("1.00 GiB", HudColor.BLUE), hudRam(sample, 1_000, 5_000))
        assertEquals(HudReading("0.00 GiB", HudColor.RED), hudRam(sample.copy(availableRam = 0), 1_000, 5_000))
        assertTrue(hudRam(sample, 1_001, 5_000).value.startsWith("—"))
        assertTrue(hudRam(sample, 1_000, 16_001).value.startsWith("—"))
        assertTrue(hudRam(sample, 1_000, 999).value.startsWith("—"))
        for (bad in listOf(sample.copy(totalRam = 0), sample.copy(availableRam = -1),
            sample.copy(availableRam = one * 5), sample.copy(availableRam = null))) {
            assertTrue(hudRam(bad, 1_000, 5_000).value.startsWith("—"))
        }
    }

    @Test fun usesWorstWindowPercentileAndDoesNotCarryHistoryAcrossRebuilds() {
        val timing = QuestHudTiming()
        assertEquals("— サンプルなし", timing.sample(1, runtime(target(1, 1, 1, 100), target(2, 1, 31, 100)), 1_000).value)
        val update = timing.sample(1, runtime(target(1, 1, 1, 110), target(2, 1, 31, 102)), 2_000)
        assertEquals(HudDraw("> 31 ms", 2, 12, 1_000, 2), update)
        assertEquals("— サンプルなし", timing.sample(1, runtime(target(1, 1, 1, 114), rebuild = 1), 3_000).value)
        assertEquals("≤ 2 ms", timing.sample(1, runtime(target(1, 1, 1, 116), rebuild = 1), 4_000).value)
        assertEquals("— サンプルなし", timing.sample(1, runtime(target(1, 2, 1, 2), rebuild = 1), 5_000).value)
        assertEquals("— 描画エラー", timing.sample(1, runtime(target(1, 2, 1, 3), rebuild = 1, error = "RENDER_STALLED"), 6_000).value)
    }

    @Test fun aStaticActiveWindowLosesItsOldDistribution() {
        val timing = QuestHudTiming()
        timing.sample(1, runtime(target(1, 1, 4, 3)), 1_000)
        assertEquals("≤ 5 ms", timing.sample(1, runtime(target(1, 1, 4, 4)), 2_000).value)
        for (time in 3_000L..12_000L step 1_000) timing.sample(1, runtime(target(1, 1, 4, 4)), time)
        assertEquals("— サンプルなし", timing.sample(1, runtime(target(1, 1, 4, 4)), 13_000).value)
        assertEquals("— 対象なし", timing.sample(1, runtime(), 14_000).value)
    }

    private fun runtime(vararg targets: JSONObject, rebuild: Long = 0, error: String? = null): JSONObject =
        JSONObject().put("activity_visible", true).put("scene", "1").put("render", JSONObject()
            .put("context_rebuilds", rebuild).put("error", error ?: JSONObject.NULL).put("targets", JSONArray(targets.toList())))

    private fun target(window: Int, generation: Int, bin: Int, count: Long): JSONObject =
        JSONObject().put("key", JSONObject().put("session", "1").put("window", "$window").put("scene", "1")
            .put("generation", "$generation")).put("role", "windows").put("frame_timing",
            JSONObject().put("samples", count).put("histogram_ms", JSONArray(List(32) { if (it == bin) count else 0L })))
}
