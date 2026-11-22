package io.github.tinmachine.eidolon

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class QuestHudPresentationTest {
    @Test fun alertStartsOnceUntilFreshSamplesConfirmRecovery() {
        val tracker = HudAlertTracker()
        val rule = HudRule(HudComparison.BELOW, 60.0, motionMs = 1000)
        fun at(fps: Double?, time: Long, now: Long = time) =
            tracker.sample(HudValue("", fps, time), rule, now)
        assertFalse(at(60.0, 1000).active)
        assertEquals(HudAlert(true, 2000), at(58.0, 2000))
        assertEquals(HudAlert(true, 2000), at(58.0, 2000, 2900))
        assertEquals(HudAlert(true, 2000), at(55.0, 3000))
        assertFalse(at(null, 3200).active)
        assertEquals(HudAlert(true, 2000), at(91.0, 4000))
        assertEquals(HudAlert(true, 2000), at(91.0, 4000, 7000))
        assertEquals(HudAlert(true, 2000), at(90.0, 5000))
        assertFalse(at(90.0, 6000).active)
        assertEquals(HudAlert(true, 7000), at(59.0, 7000))
    }

    @Test fun presetsUseExplicitBoundariesAndStaticBar() {
        val rings = HudTheme.builtin(HudPreset.RINGS)
        fun triggered(metric: HudMetric, value: Double): Boolean =
            rings.rules.getValue(metric).let { it.comparison.matches(value, it.threshold) }
        assertFalse(triggered(HudMetric.REUSED_FRAMES, 0.0))
        assertTrue(triggered(HudMetric.REUSED_FRAMES, 1.0))
        assertFalse(triggered(HudMetric.CPU, 1.0))
        assertTrue(triggered(HudMetric.CPU, 1.01))
        assertFalse(triggered(HudMetric.RAM, .5))
        assertTrue(triggered(HudMetric.RAM, .49))
        assertFalse(triggered(HudMetric.APP_SLOTS, 15.0))
        assertTrue(triggered(HudMetric.APP_SLOTS, 16.0))
        assertFalse(triggered(HudMetric.BATTERY, 20.0))
        assertTrue(triggered(HudMetric.BATTERY, 19.0))
        assertEquals(0, rings.rules.getValue(HudMetric.BATTERY).motionMs)
        assertEquals(HudRingStack(), rings.ringStack)
        assertEquals(listOf(.85f, -.81f, 1.4f), rings.offset)
        assertTrue(HudTheme.builtin(HudPreset.BAR).rules.values.all { it.motionMs == 0 })
    }

    @Test fun importedHudUsesItsOwnPlacementOrderRulesAndAppearance() {
        val custom = HudTheme.fromNative(JSONObject("""
            {"layout":"alerts","anchor":"head","ring_cap":"round","offset_m":[0.4,-0.5,1.4],
            "face_viewer":false,"normal_color":"#102030","scale":0.9,
            "metrics":["battery","fps"],"rules":{"fps":{
                "comparison":"below","threshold":72.0,"motion_ms":750,"color":"#FFAA00"}}}
        """.trimIndent()))!!
        assertEquals(HudLayout.ALERTS, custom.layout)
        assertEquals(listOf(.4f, -.5f, 1.4f), custom.offset)
        assertEquals(.9f, custom.scale)
        assertTrue(custom.followHead)
        assertFalse(custom.faceViewer)
        assertTrue(custom.roundedEnds)
        assertNull(custom.ringStack)
        assertEquals(0xff102030.toInt(), custom.normalColor)
        assertEquals(listOf(HudMetric.BATTERY, HudMetric.FPS), custom.metrics)
        assertEquals(mapOf(HudMetric.FPS to HudRule(HudComparison.BELOW, 72.0, 0xffffaa00.toInt(), 750)), custom.rules)
    }
    @Test fun importedHudCanSelectStackedRingsAndTuneTheirDimensions() {
        val config = JSONObject("""{"layout":"rings","anchor":"space","ring_cap":"flat",
            "ring_layout":"stacked","stack_width_dp":124,"stack_height_dp":42,"stack_stroke_dp":6}
        """.trimIndent())
        assertEquals(HudRingStack(124f, 42f, 6f), HudTheme.fromNative(config)?.ringStack)
        assertNull(HudTheme.fromNative(config.put("ring_layout", "inline"))?.ringStack)
    }

}
