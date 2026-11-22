package io.github.tinmachine.eidolon

import org.json.JSONObject
import java.util.Locale

internal enum class HudMetric(val label: String, val shortLabel: String) {
    FPS("Eidolon FPS", "FPS"),
    REUSED_FRAMES("前フレーム再利用", "再利用"),
    CPU("Eidolon CPU", "CPU"),
    RAM("Quest RAM 空き", "空き RAM"),
    APP_SLOTS("起動枠", "起動枠"),
    BATTERY("電池", "電池"),
    GPU_TIME("Eidolon GPU", "GPU"),
    WINDOW_UPDATES("選択窓の更新", "窓更新"),
    DRAW_P95("描画処理 p95", "描画 p95"),
    THERMAL("OS熱", "OS熱");

    val key: String get() = name.lowercase(Locale.ROOT)
}

internal enum class HudLayout { RINGS, BAR, ALERTS }
internal enum class HudComparison {
    BELOW, ABOVE, AT_LEAST;
    fun matches(value: Double, threshold: Double): Boolean = when (this) {
        BELOW -> value < threshold
        ABOVE -> value > threshold
        AT_LEAST -> value >= threshold
    }
}

internal data class HudRule(
    val comparison: HudComparison,
    val threshold: Double,
    val color: Int = 0xffffd75a.toInt(),
    val motionMs: Int = 0,
)

internal enum class HudPreset(val label: String) {
    RINGS("短く動くリング"),
    BAR("静止する横帯"),
    ALERTS("警告だけ"),
    IMPORTED("読み込んだ HUD");
}

internal data class HudTheme(
    val layout: HudLayout = HudLayout.RINGS,
    val offset: List<Float> = listOf(.48f, -.43f, 1.40f),
    val scale: Float = 1f,
    val faceViewer: Boolean = true,
    val followHead: Boolean = false,
    val roundedEnds: Boolean = false,
    val normalColor: Int = 0xffeeeeee.toInt(),
    val metrics: List<HudMetric> = HudMetric.entries.toList(),
    val rules: Map<HudMetric, HudRule> = defaultHudRules,
) {
    companion object {
        fun builtin(preset: HudPreset): HudTheme = when (preset) {
            HudPreset.BAR -> HudTheme(
                layout = HudLayout.BAR,
                metrics = listOf(HudMetric.FPS, HudMetric.CPU, HudMetric.RAM, HudMetric.BATTERY,
                    HudMetric.REUSED_FRAMES, HudMetric.APP_SLOTS),
                rules = defaultHudRules.mapValues { (_, rule) -> rule.copy(motionMs = 0) },
            )
            HudPreset.ALERTS -> HudTheme(layout = HudLayout.ALERTS, metrics = defaultHudRules.keys.toList())
            else -> HudTheme()
        }

        /** The native theme loader already validated the manifest. */
        fun fromNative(value: JSONObject): HudTheme? = runCatching {
            val layout = HudLayout.valueOf(value.getString("layout").uppercase(Locale.ROOT))
            val defaults = builtin(HudPreset.valueOf(layout.name))
            val keys = HudMetric.entries.associateBy(HudMetric::key)
            val metrics = value.optJSONArray("metrics")?.let { list ->
                List(list.length()) { keys.getValue(list.getString(it)) }
            }
            val rules = value.optJSONObject("rules")?.let { rules ->
                rules.keys().asSequence().associate { key ->
                    val rule = rules.getJSONObject(key)
                    keys.getValue(key) to HudRule(
                        HudComparison.valueOf(rule.getString("comparison").uppercase(Locale.ROOT)),
                        rule.getDouble("threshold"),
                        color(rule, "color") ?: 0xffffd75a.toInt(),
                        rule.getInt("motion_ms"),
                    )
                }
            }
            defaults.copy(
                offset = value.optJSONArray("offset_m")?.let { p -> List(3) { p.getDouble(it).toFloat() } }
                    ?: defaults.offset,
                scale = value.optDouble("scale", 1.0).toFloat(),
                faceViewer = value.optBoolean("face_viewer", true),
                followHead = value.getString("anchor") == "head",
                roundedEnds = value.getString("ring_cap") == "round",
                normalColor = color(value, "normal_color") ?: defaults.normalColor,
                metrics = metrics ?: defaults.metrics,
                rules = rules ?: defaults.rules,
            )
        }.getOrNull()

        private fun color(value: JSONObject, key: String) =
            if (value.isNull(key)) null else (0xff000000 or value.getString(key).substring(1).toLong(16)).toInt()
    }
}

private val defaultHudRules = linkedMapOf(
    HudMetric.FPS to HudRule(HudComparison.BELOW, 60.0, motionMs = 1000),
    HudMetric.REUSED_FRAMES to HudRule(HudComparison.AT_LEAST, 1.0, motionMs = 1000),
    HudMetric.CPU to HudRule(HudComparison.ABOVE, 1.0, motionMs = 1000),
    HudMetric.RAM to HudRule(HudComparison.BELOW, .5, color = 0xffff6969.toInt(), motionMs = 1000),
    HudMetric.APP_SLOTS to HudRule(HudComparison.ABOVE, 15.0, motionMs = 1000),
    HudMetric.BATTERY to HudRule(HudComparison.BELOW, 20.0, color = 0xffffad69.toInt()),
)

internal data class HudValue(val text: String, val value: Double? = null, val sampledAt: Long = 0)
internal data class HudAlert(val active: Boolean = false, val startedAt: Long = 0)

internal class HudAlertTracker {
    private var lastSample: Long? = null
    private var normalSince: Long? = null
    private var alert = HudAlert()

    fun sample(reading: HudValue, rule: HudRule, now: Long): HudAlert {
        val value = reading.value
        if (value == null) {
            normalSince = null
            return HudAlert()
        }
        if (lastSample == reading.sampledAt) return alert
        if (lastSample?.let { reading.sampledAt < it } == true) normalSince = null
        lastSample = reading.sampledAt
        if (rule.comparison.matches(value, rule.threshold)) {
            if (!alert.active) alert = HudAlert(true, now)
            normalSince = null
        } else if (alert.active) {
            val since = normalSince ?: reading.sampledAt.also { normalSince = it }
            if (reading.sampledAt - since >= 2_000) alert = HudAlert()
        }
        return alert
    }
}
