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
import androidx.compose.runtime.mutableStateOf
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
    private val sampleTime = mutableStateOf(0L)
    private val refresh = object : Runnable {
        override fun run() {
            if (!enabled.value || activeSince == Long.MAX_VALUE || scene == null) return
            if (entity == null) place()
            sampleTime.value = SystemClock.elapsedRealtime()
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
            shape = QuadShapeOptions(width = .48f, height = .22f),
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
        NativeSessionHost.setHudTimingActive(owner, sceneEpoch, show)
        if (show) {
            entity?.setComponent(Visible(true))
            handler.post(refresh)
        } else if (enabled.value) {
            entity?.setComponent(Visible(false))
        } else {
            entity?.destroy()
            entity = null
        }
    }

    fun close() {
        handler.removeCallbacks(refresh)
        NativeSessionHost.setHudTimingActive(owner, sceneEpoch, false)
        activeSince = Long.MAX_VALUE
        entity?.destroy()
        entity = null
        scene = null
        sceneEpoch = 0L
    }

    @Composable
    private fun DrawHud() {
        val now = sampleTime.value
        val metrics = NativeSessionHost.deviceMetrics.value
        val draw = NativeSessionHost.hudDraw.value?.takeIf {
            it.at >= activeSince && now - it.at in 0..2_500
        }?.draw
        val readings = listOf(hudCpu(metrics, activeSince, now), HudReading(draw?.value ?: "— 取得待ち"), hudRam(metrics, activeSince, now))
        val labels = listOf("Eidolon CPU", "描画処理 p95", "Quest RAM 空き")
        val colors = readings.map { reading ->
            when (reading.color) {
                HudColor.WHITE -> Color.White
                HudColor.BLUE -> Color(0xff70b5ff)
                HudColor.YELLOW -> Color(0xffffd75a)
                HudColor.RED -> Color(0xffff6969)
            }
        }
        Row(Modifier.fillMaxSize(), horizontalArrangement = Arrangement.spacedBy(9.dp)) {
            Canvas(Modifier.weight(.43f).fillMaxHeight()) {
                val w = size.width * .86f
                val h = size.height * .28f
                for (index in colors.indices.reversed()) {
                    drawArc(colors[index], 21f, 332f, false,
                        Offset((size.width - w) / 2, size.height * (.13f + index * .15f)),
                        Size(w, h), style = Stroke(size.width * .061f, cap = StrokeCap.Round))
                }
            }
            Column(Modifier.weight(.57f).fillMaxHeight(), verticalArrangement = Arrangement.Center) {
                for (index in labels.indices) {
                    Row(Modifier.fillMaxWidth().height(25.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text(labels[index], color = colors[index], fontSize = 11.sp, maxLines = 1)
                        Text(readings[index].value, color = colors[index], fontSize = 11.sp,
                            fontFamily = FontFamily.Monospace, maxLines = 1)
                    }
                }
                Text(hudThermal(metrics, activeSince, now), color = Color.White, fontSize = 10.sp)
            }
        }
    }
}
