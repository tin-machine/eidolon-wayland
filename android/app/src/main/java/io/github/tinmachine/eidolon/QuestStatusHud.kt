package io.github.tinmachine.eidolon

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.Choreographer
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.key
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import java.util.Locale
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.sin
import com.meta.spatial.compose.ComposeViewPanelRegistration
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Pose
import com.meta.spatial.core.Quaternion
import com.meta.spatial.core.Vector3
import com.meta.spatial.runtime.AlphaMode
import com.meta.spatial.runtime.PanelConfigOptions
import com.meta.spatial.runtime.Scene
import com.meta.spatial.toolkit.DpPerMeterDisplayOptions
import com.meta.spatial.toolkit.MeshCollision
import com.meta.spatial.toolkit.Panel
import com.meta.spatial.toolkit.PanelInputOptions
import com.meta.spatial.toolkit.PanelRenderMode
import com.meta.spatial.toolkit.PanelStyleOptions
import com.meta.spatial.toolkit.QuadShapeOptions
import com.meta.spatial.toolkit.Scale
import com.meta.spatial.toolkit.Transform
import com.meta.spatial.toolkit.UIPanelRenderOptions
import com.meta.spatial.toolkit.UIPanelSettings
import com.meta.spatial.toolkit.Visible

internal class QuestStatusHud(context: Context, private val owner: Long) {
    private val settings = context.getSharedPreferences("quest_status_hud", Context.MODE_PRIVATE)
    private val handler = Handler(Looper.getMainLooper())
    private var entity: Entity? = null
    private var scene: Scene? = null
    private var sceneEpoch = 0L
    private var activeSince = Long.MAX_VALUE
    val enabled = mutableStateOf(settings.getBoolean("enabled", false))
    val rotation = mutableStateOf(settings.getBoolean("rotation", true))
    private val visible = mutableStateOf(false)
    private var fpsSource: QuestXrFrameRate? = null
    val preset = mutableStateOf(runCatching {
        HudPreset.valueOf(settings.getString("preset", HudPreset.RINGS.name).orEmpty())
    }.getOrDefault(HudPreset.RINGS))
    private var activeTheme: HudTheme? = null
    private val builtins = HudPreset.entries.associateWith(HudTheme::builtin)
    private val trackers = mutableMapOf<HudMetric, HudAlertTracker>()
    private data class HudRow(val metric: HudMetric, val text: String, val rule: HudRule?, val alert: HudAlert)
    private data class Presentation(val theme: HudTheme, val rows: List<HudRow>)
    private val presentation = mutableStateOf<Presentation?>(null)
    private val headFrame = object : Choreographer.FrameCallback {
        override fun doFrame(frameTimeNanos: Long) {
            val theme = activeTheme ?: return
            if (!visible.value || !theme.followHead || presentation.value?.rows.isNullOrEmpty()) return
            place(theme, resize = false)
            Choreographer.getInstance().postFrameCallback(this)
        }
    }

    private data class Snapshot(val at: Long, val device: DeviceMetricsSnapshot,
        val window: HudDrawSample?, val xr: QuestFrameSample?, val hz: Float?, val capacity: LauncherCapacity)
    private val refresh = object : Runnable {
        override fun run() {
            val current = scene ?: return
            if (!visible.value) return
            val now = SystemClock.elapsedRealtime()
            val window = NativeSessionHost.hudDraw.value?.takeIf { it.at >= activeSince && now - it.at in 0..2_500 }
            val hz = current.getConfirmedFrameRate().takeIf { it.isFinite() && it > 0 }
            val theme = selectedTheme()
            val placementChanged = activeTheme != theme
            if (placementChanged) {
                trackers.clear()
                activeTheme = theme
            }
            val sample = Snapshot(now, NativeSessionHost.deviceMetrics.value, window,
                fpsSource?.sample(now), hz, NativeSessionHost.launcherCapacity.value)
            val readings = readings(sample)
            val rows = theme.metrics.mapNotNull { metric ->
                val reading = readings.getValue(metric)
                val rule = theme.rules[metric]
                val alert = rule?.let { trackers.getOrPut(metric, ::HudAlertTracker).sample(reading, it, now) }
                    ?: HudAlert()
                if (theme.layout == HudLayout.ALERTS && !alert.active) null
                else HudRow(metric, reading.text, rule, alert)
            }
            val startFollowing = placementChanged || presentation.value?.rows.isNullOrEmpty()
            presentation.value = Presentation(theme, rows)
            if (entity == null || placementChanged) place(theme)
            if (startFollowing || rows.isEmpty()) syncFollowing(theme)
            handler.postDelayed(this, 1_000)
        }
    }

    val registration = ComposeViewPanelRegistration(
        R.id.quest_status_hud,
        composeViewCreator = { _, context -> ComposeView(context).apply {
            isFocusable = false
            isFocusableInTouchMode = false
            isClickable = false
            setContent { this@QuestStatusHud.DrawHud() }
        }},
        settingsCreator = { UIPanelSettings(
            shape = QuadShapeOptions(width = .84f, height = .46f),
            display = DpPerMeterDisplayOptions(),
            rendering = UIPanelRenderOptions(PanelRenderMode.Mesh()),
            style = object : PanelStyleOptions(R.style.EidolonTheme) {
                override fun applyTo(options: PanelConfigOptions) {
                    super.applyTo(options)
                    options.alphaMode = AlphaMode.PREMULTIPLIED
                    options.enableTransparent = true
                    options.forceSceneTexture = true
                    options.layerConfig = null
                }
            },
            input = PanelInputOptions(0),
        )},
    )

    fun bind(sdkScene: Scene, epoch: Long) {
        close()
        scene = sdkScene
        sceneEpoch = epoch
    }

    fun setActive(active: Boolean) {
        val wasActive = activeSince != Long.MAX_VALUE
        if (active == wasActive) return
        activeSince = if (active) SystemClock.elapsedRealtime() else Long.MAX_VALUE
        reconcile()
    }

    fun toggle() {
        enabled.value = !enabled.value
        settings.edit().putBoolean("enabled", enabled.value).apply()
        reconcile()
    }

    fun recenter() {
        if (enabled.value && activeSince != Long.MAX_VALUE) place(selectedTheme())
    }

    fun toggleRotation() {
        rotation.value = !rotation.value
        settings.edit().putBoolean("rotation", rotation.value).apply()
    }

    fun selectPreset(value: HudPreset) {
        if (preset.value == value) return
        preset.value = value
        settings.edit().putString("preset", value.name).apply()
        if (visible.value) {
            handler.removeCallbacks(refresh)
            handler.post(refresh)
        }
    }

    private fun selectedTheme(): HudTheme =
        NativeSessionHost.themeUi.value.importedHud?.takeIf { preset.value == HudPreset.IMPORTED }
            ?: builtins.getValue(preset.value)

    private fun syncFollowing(theme: HudTheme) {
        val frames = Choreographer.getInstance()
        frames.removeFrameCallback(headFrame)
        if (theme.followHead && visible.value && !presentation.value?.rows.isNullOrEmpty()) {
            frames.postFrameCallback(headFrame)
        }
    }

    private fun place(theme: HudTheme, resize: Boolean = true) {
        val viewer = scene?.getViewerPose() ?: return
        val (x, y, z) = theme.offset
        // The front of a panel is local -Z. Point local +Z along the ray from the eyes to its center.
        val translation = Vector3(x, y, z)
        val location = viewer * if (theme.faceViewer) {
            val yaw = atan2(x, z) / 2f
            val pitch = atan2(-y, hypot(x, z)) / 2f
            Pose(translation, Quaternion(cos(yaw) * cos(pitch), cos(yaw) * sin(pitch),
                sin(yaw) * cos(pitch), -sin(yaw) * sin(pitch)))
        } else Pose(translation)
        if (!listOf(location.t.x, location.t.y, location.t.z, location.q.x, location.q.y,
                location.q.z, location.q.w).all(Float::isFinite)
        ) return
        val panel = entity
        if (panel == null) {
            entity = Entity.create(listOf(Panel(R.id.quest_status_hud, MeshCollision.NoCollision),
                Transform(location), Scale(Vector3(theme.scale, theme.scale, 1f)), Visible(true)))
        } else {
            panel.setComponent(Transform(location))
            if (resize) {
                panel.setComponent(Scale(Vector3(theme.scale, theme.scale, 1f)))
            }
        }
    }

    private fun reconcile() {
        handler.removeCallbacks(refresh)
        val show = enabled.value && activeSince != Long.MAX_VALUE && scene != null
        visible.value = show
        NativeSessionHost.setHudTimingActive(owner, sceneEpoch, show)
        if (show) {
            if (fpsSource == null) fpsSource = QuestXrFrameRate().also { it.start() }
            entity?.setComponent(Visible(true))
            handler.post(refresh)
        } else {
            stopSampling()
            if (enabled.value) entity?.setComponent(Visible(false)) else {
                entity?.destroy()
                entity = null
            }
        }
    }

    private fun stopSampling() {
        visible.value = false
        fpsSource?.close()
        fpsSource = null
        Choreographer.getInstance().removeFrameCallback(headFrame)
        activeTheme = null
        trackers.clear()
        presentation.value = null
    }

    fun close() {
        handler.removeCallbacks(refresh)
        stopSampling()
        NativeSessionHost.setHudTimingActive(owner, sceneEpoch, false)
        activeSince = Long.MAX_VALUE
        entity?.destroy()
        entity = null
        scene = null
        sceneEpoch = 0L
    }

    private fun readings(sample: Snapshot): Map<HudMetric, HudValue> {
        val now = sample.at
        val metrics = sample.device
        val xr = sample.xr
        val cpu = hudCpu(metrics, activeSince, now)
        val ram = hudRam(metrics, activeSince, now)
        val battery = hudBattery(metrics, activeSince, now).removePrefix("電池 ")
        val thermal = hudThermal(metrics, activeSince, now).removePrefix("OS熱: ")
        val draw = sample.window?.draw
        fun formatted(value: Number?, pattern: String) =
            value?.let { String.format(Locale.ROOT, pattern, it) } ?: "—"
        return mapOf(
            HudMetric.FPS to HudValue("${xr?.fps ?: "—"} fps / ${formatted(sample.hz, "%.0f")} Hz",
                xr?.fps?.toDouble(), xr?.at ?: now),
            HudMetric.REUSED_FRAMES to HudValue("${xr?.stale ?: "—"} 回", xr?.stale?.toDouble(), xr?.at ?: now),
            HudMetric.CPU to HudValue(cpu.value, metrics.processCpu.percent?.takeIf { cpu.available }?.div(100),
                metrics.processCpu.sampledMonotonicMs ?: now),
            HudMetric.RAM to HudValue(ram.value, metrics.availableRam?.takeIf { ram.available }?.div(1073741824.0),
                metrics.sampledMs),
            HudMetric.APP_SLOTS to HudValue("${sample.capacity.used}/${LauncherCapacity.MAX_APPS} · 回収待ち ${sample.capacity.recovering}",
                sample.capacity.used.toDouble(), now),
            HudMetric.BATTERY to HudValue(battery, metrics.batteryPercent?.takeIf { battery != "—" }?.toDouble(),
                metrics.sampledMs),
            HudMetric.GPU_TIME to HudValue("${formatted(xr?.gpuMs, "%.1f")} / ${formatted(sample.hz?.let { 1000.0 / it }, "%.1f")} ms",
                xr?.gpuMs, xr?.at ?: now),
            HudMetric.WINDOW_UPDATES to HudValue("${formatted(sample.window?.contentHz, "%.1f")} 回/秒",
                sample.window?.contentHz, sample.window?.at ?: now),
            HudMetric.DRAW_P95 to HudValue(draw?.value ?: "— 取得待ち", draw?.p95BinUpperMs?.toDouble(), sample.window?.at ?: now),
            HudMetric.THERMAL to HudValue(thermal, hudThermalLevel(metrics, activeSince, now)?.toDouble(),
                metrics.details.thermal.sampledMs ?: now),
        )
    }

    @Composable
    private fun DrawHud() {
        val current = presentation.value ?: return
        if (current.rows.isEmpty()) return
        val theme = current.theme
        val motion = rotation.value && !NativeSessionHost.themeUi.value.reduced
        Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.Center) {
            if (theme.layout == HudLayout.BAR) {
                for (group in current.rows.chunked(3)) {
                    Row(Modifier.fillMaxWidth().height(26.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        for (row in group) Text(
                            "${if (row.alert.active) "! " else ""}${row.metric.shortLabel} ${compactValue(row)}",
                            modifier = Modifier.weight(1f), color = rowColor(row, theme), fontSize = 12.sp, maxLines = 1)
                    }
                }
            } else {
                val rowHeight = 23.dp
                val stack = theme.ringStack
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    if (stack != null) {
                        val width = stack.widthDp.dp
                        val height = stack.heightDp.dp
                        Box(Modifier.size(width, rowHeight * (current.rows.size - 1) + height)) {
                            // Draw lower rings first. The same row spacing aligns their centers with the labels.
                            for (index in current.rows.indices.reversed()) {
                                val row = current.rows[index]
                                if (row.rule != null) key(row.metric) {
                                    Ring(row, theme, rowColor(row, theme), motion,
                                        Modifier.offset(y = rowHeight * index).size(width, height), stack.strokeDp.dp)
                                }
                            }
                        }
                    }
                    Column(Modifier.weight(1f)) {
                        for (row in current.rows) key(row.metric) {
                            Row(Modifier.fillMaxWidth().height(rowHeight), verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                                val color = rowColor(row, theme)
                                if (stack == null) {
                                    if (row.rule != null) Ring(row, theme, color, motion, Modifier.size(17.dp), 2.5.dp)
                                    else Box(Modifier.size(17.dp))
                                }
                                Text(if (row.alert.active) "!" else "", Modifier.size(10.dp, 19.dp), color = color, fontSize = 13.sp)
                                Text(row.metric.label, Modifier.weight(1f), color = color, fontSize = 15.sp, maxLines = 1)
                                Text(row.text, color = color, fontSize = 15.sp, fontFamily = FontFamily.Monospace, maxLines = 1)
                            }
                        }
                    }
                }
            }
        }
    }

    private fun compactValue(row: HudRow): String = when (row.metric) {
        HudMetric.FPS -> row.text.substringBefore(" /")
        HudMetric.APP_SLOTS -> row.text.substringBefore(" ·")
        HudMetric.BATTERY -> row.text.substringBefore(' ')
        else -> row.text
    }

    private fun rowColor(row: HudRow, theme: HudTheme) =
        Color(if (row.alert.active) row.rule?.color ?: theme.normalColor else theme.normalColor)

    @Composable
    private fun Ring(row: HudRow, theme: HudTheme, color: Color, motion: Boolean,
                     modifier: Modifier, strokeWidth: Dp) {
        val duration = row.rule?.motionMs?.takeIf { motion && row.alert.active } ?: 0
        val angle = remember(row.alert.startedAt, duration) { mutableFloatStateOf(0f) }
        LaunchedEffect(row.alert.startedAt, duration) {
            if (duration == 0) return@LaunchedEffect
            do {
                val progress = ((SystemClock.elapsedRealtime() - row.alert.startedAt).toFloat() / duration).coerceIn(0f, 1f)
                angle.floatValue = 90f * progress
                if (progress == 1f) break
                delay(40)
            } while (true)
        }
        Canvas(modifier.padding(strokeWidth / 2f)) {
            val start = if (theme.ringStack != null) 104f else -80f
            drawArc(color, start + angle.floatValue, 315f, false, style = Stroke(
                strokeWidth.toPx(), cap = if (theme.roundedEnds) StrokeCap.Round else StrokeCap.Butt))
        }
    }
}
