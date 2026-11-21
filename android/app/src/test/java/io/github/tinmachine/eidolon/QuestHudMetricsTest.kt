package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class QuestHudMetricsTest {
    @Test fun xrReportUsesMeasuredNumeratorAndOriginalTimestamp() {
        val wall = 1_700_000_000_500L
        val line = "1700000000.125  1254  1270 I VrApi   : FPS=45/90,Prd=21ms"
        assertEquals(QuestFrameSample(45, 625, null, null), QuestXrFrameRate.parse(line, wall - 500, wall, 1000))
        assertNull(QuestXrFrameRate.parse(line, wall, wall, 1000))
        assertNull(QuestXrFrameRate.parse(line, 0, wall + 3_000, 4000))
        assertNull(QuestXrFrameRate.parse(line, 0, wall - 500, 1000))
        assertNull(QuestXrFrameRate.parse(line.replace("VrApi", "Other"), 0, wall, 1000))
        assertNull(QuestXrFrameRate.parse(line.replace("45/90", "90"), 0, wall, 1000))
    }

    @Test fun xrReportReadsExactStaleAndGpuFieldsAndKeepsMissingValuesIndependent() {
        val wall = 1_700_000_000_500L
        val prefix = "1700000000.500  1254  1270 I VrApi: FPS=92/90"
        fun read(fields: String) = QuestXrFrameRate.parse(prefix + fields, wall, wall, 900)
        assertEquals(QuestFrameSample(92, 900, 3, 1.42),
            read(",Stale=3,Stale2/5/10/max=6/1/0/10,TW=2.01ms,App=1.42ms"))
        assertEquals(QuestFrameSample(92, 900, 0, 0.0), read(",App=0.00ms,Stale=0"))
        assertEquals(QuestFrameSample(92, 900, null, 1.42), read(",Stale2/5/10/max=6/1/0/10,App=1.42ms"))
        assertEquals(QuestFrameSample(92, 900, 2, null), read(",Stale=2,TW=2.01ms,App=NaNms"))
        assertEquals(QuestFrameSample(92, 900, null, null), read(",Stale=-1,App=-10ms"))
        assertEquals(QuestFrameSample(92, 900, null, null), read(",Stale=oops,App=1234us"))
    }

    @Test fun focusedUpdatesIgnoreDecorationAndRestartOnOwnerChangeOrGap() {
        val rate = HudContentRate()
        val target = target(1, 1, 0, 1).put("content_frame", "163").put("content_updates", 10)
        val focus = target.getJSONObject("key")
        val state = runtime(target)
        assertNull(rate.sample(1, state, focus, 1_000))
        target.put("frames", 100).put("decoration_frames", 90)
        assertEquals(0.0, rate.sample(1, state, focus, 2_000)!!, 0.001)
        target.put("content_frame", "999").put("content_updates", 16)
        assertEquals(4.0, rate.sample(1, state, focus, 3_500)!!, 0.001)
        assertNull(rate.sample(2, state, focus, 4_500))
        assertNull(rate.sample(2, state, focus, 7_500))
        target.put("presentation_paused", true)
        assertNull(rate.sample(2, state, focus, 8_500))
        target.put("presentation_paused", false)
        assertNull(rate.sample(2, state, focus, 9_500))
        assertNull(rate.sample(2, state, null, 10_500))
    }

    @Test fun batteryKeepsZeroAndRejectsStaleValues() {
        val sample = DeviceMetricsSnapshot(sampledMs = 200, batteryPercent = 0, charging = true)
        assertEquals("電池 0% 充電中", hudBattery(sample, 100, 300))
        assertEquals("電池 —", hudBattery(sample, 201, 300))
        assertEquals("電池 —", hudBattery(sample, 100, 16_000))
    }

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
