package io.github.tinmachine.eidolon

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.clipPath
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

@Composable
internal fun WristWheel(hover: WristAction?, pressed: WristAction?, skin: WristSkin) {
    val appearance = NativeSessionHost.themeUi.value
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Canvas(Modifier.size(160.dp)) {
            val span = size.minDimension
            val inset = (size.width - span) / 2f
            val top = (size.height - span) / 2f
            val polygon = Path()
            for (vertex in 0..5) {
                val angle = vertex * PI / 3
                val x = center.x + span * .5f * sin(angle).toFloat()
                val y = center.y - span * .5f * cos(angle).toFloat()
                if (vertex == 0) polygon.moveTo(x, y) else polygon.lineTo(x, y)
            }
            polygon.close()
            val arcColors = WristAction.entries.map { action ->
                when (action) {
                    pressed -> Color(0xFF935577)
                    hover -> Color(0xFF634365)
                    else -> Color(0xFF352539)
                }
            }
            val drawSectors: androidx.compose.ui.graphics.drawscope.DrawScope.() -> Unit = {
                for ((index, color) in arcColors.withIndex()) {
                    val degrees = WristAction.sectorDegrees.toFloat()
                    drawArc(color, -90f + (index - .5f) * degrees + 2f, degrees - 4f, true, Offset(inset, top), Size(span, span))
                }
            }
            if (skin == WristSkin.HEXAGON) clipPath(polygon, block = drawSectors) else drawSectors()
            drawCircle(Color(0xFF201827), radius = span * .15f)
            val border = Color(0xFFB489B0)
            if (skin == WristSkin.HEXAGON) drawPath(polygon, border, style = Stroke(1.dp.toPx()))
            else drawCircle(border, radius = span / 2f, style = Stroke(1.dp.toPx()))
        }
        for (action in WristAction.entries) {
            val angle = Math.toRadians(action.ordinal * WristAction.sectorDegrees)
            val label = when (action) {
                WristAction.MOTION -> "動き\n${if (appearance.reduced) "OFF" else "ON"}"
                WristAction.DECOR -> "装飾\n${if (appearance.selection == "off") "OFF" else "ON"}"
                else -> action.title
            }
            Text(label, Modifier.offset((53 * sin(angle)).dp, (-53 * cos(angle)).dp),
                color = Color.White, fontSize = 12.sp, lineHeight = 13.sp, textAlign = TextAlign.Center)
        }
        if (hover != null) Text(
            hover.explanation, Modifier.align(Alignment.BottomCenter).padding(bottom = 4.dp),
            color = Color(0xFFFFDCF1), fontSize = 10.sp, textAlign = TextAlign.Center, maxLines = 1,
        )
    }
}
