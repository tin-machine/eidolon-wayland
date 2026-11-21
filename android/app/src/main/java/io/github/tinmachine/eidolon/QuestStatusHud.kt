package io.github.tinmachine.eidolon

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import java.util.Locale
import com.meta.spatial.compose.ComposeViewPanelRegistration
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Pose
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
    private data class Snapshot(val at: Long, val device: DeviceMetricsSnapshot,
        val window: HudDrawSample?, val fps: Int?, val hz: Float?)
    private val snapshot = mutableStateOf<Snapshot?>(null)
    private val refresh = object : Runnable {
        override fun run() {
            val current = scene ?: return
            if (!visible.value) return
            if (entity == null) place()
            val now = SystemClock.elapsedRealtime()
            val window = NativeSessionHost.hudDraw.value?.takeIf { it.at >= activeSince && now - it.at in 0..2_500 }
            val hz = current.getConfirmedFrameRate().takeIf { it.isFinite() && it > 0 }
            snapshot.value = Snapshot(now, NativeSessionHost.deviceMetrics.value, window, fpsSource?.fps(now), hz)
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
            shape = QuadShapeOptions(width = .96f, height = .44f),
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
        if (enabled.value && activeSince != Long.MAX_VALUE) place()
    }

    fun toggleRotation() {
        rotation.value = !rotation.value
        settings.edit().putBoolean("rotation", rotation.value).apply()
    }

    private fun place() {
        val viewer = scene?.getViewerPose() ?: return
        val location = viewer * Pose(Vector3(.53f, -.34f, 1.40f))
        if (!listOf(location.t.x, location.t.y, location.t.z,
                location.q.x, location.q.y, location.q.z, location.q.w).all(Float::isFinite)
        ) return
        val panel = entity
        if (panel == null) {
            entity = Entity.create(listOf(Panel(R.id.quest_status_hud, MeshCollision.NoCollision), Transform(location), Visible(true)))
        } else {
            panel.setComponent(Transform(location))
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
        snapshot.value = null
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

    @Composable
    private fun DrawHud() {
        val sample = snapshot.value ?: return
        val now = sample.at
        val metrics = sample.device
        val draw = sample.window?.draw
        val readings = listOf(hudCpu(metrics, activeSince, now),
            HudReading(draw?.value ?: "— 取得待ち", available = (draw?.targets ?: 0) > 0), hudRam(metrics, activeSince, now))
        val labels = listOf("Eidolon CPU", "描画処理 p95", "Quest RAM 空き")
        val colors = readings.map { reading ->
            when (reading.color) {
                HudColor.WHITE -> Color.White
                HudColor.BLUE -> Color(0xff70b5ff)
                HudColor.YELLOW -> Color(0xffffd75a)
                HudColor.RED -> Color(0xffff6969)
            }
        }
        val moving = visible.value && rotation.value && !NativeSessionHost.themeUi.value.reduced && readings.any { it.available }
        val phase = remember { mutableFloatStateOf(0f) }
        LaunchedEffect(moving) {
            if (moving) {
                val start = SystemClock.uptimeMillis()
                val offset = phase.floatValue
                while (true) {
                    phase.floatValue = (offset + (SystemClock.uptimeMillis() - start) % 12_000 * .03f) % 360f
                    delay(50)
                }
            }
        }
        Row(Modifier.fillMaxSize(), horizontalArrangement = Arrangement.spacedBy(18.dp)) {
            Canvas(Modifier.weight(.43f).fillMaxHeight()) {
                val w = size.width * .86f
                val h = size.height * .28f
                for (index in colors.indices.reversed()) {
                    val angle = if (readings[index].available) phase.floatValue else 0f
                    drawArc(colors[index], 21f + angle, 332f, false,
                        Offset((size.width - w) / 2, size.height * (.13f + index * .15f)),
                        Size(w, h), style = Stroke(size.width * .061f, cap = StrokeCap.Round))
                }
            }
            Column(Modifier.weight(.57f).fillMaxHeight(), verticalArrangement = Arrangement.Center) {
                val fps = sample.fps?.toString() ?: "—"
                val hz = sample.hz?.let { String.format(Locale.ROOT, "%.0f", it) } ?: "—"
                HudLine("Eidolon FPS", "$fps fps / $hz Hz")
                val rate = sample.window?.contentHz?.let { String.format(Locale.ROOT, "%.1f", it) } ?: "—"
                HudLine("選択窓の更新", "$rate 回/秒")
                for (index in labels.indices) {
                    HudLine(labels[index], readings[index].value, colors[index])
                }
                Text(hudBattery(metrics, activeSince, now) + "  ·  " + hudThermal(metrics, activeSince, now),
                    color = Color.White, fontSize = 13.sp)
            }
        }
    }

    @Composable
    private fun HudLine(label: String, value: String, color: Color = Color.White) {
        Row(Modifier.fillMaxWidth().height(30.dp), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(label, color = color, fontSize = 15.sp, maxLines = 1)
            Text(value, color = color, fontSize = 15.sp, fontFamily = FontFamily.Monospace, maxLines = 1)
        }
    }
}
