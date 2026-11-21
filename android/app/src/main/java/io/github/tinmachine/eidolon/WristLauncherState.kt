package io.github.tinmachine.eidolon

import kotlin.math.PI
import kotlin.math.atan2
import kotlin.math.hypot

internal enum class WristInput(val title: String) {
    AUTO("自動"), HANDS("素手"), CONTROLLERS("コントローラー"), OFF("手首表示オフ"),
}

internal enum class WristSkin(val title: String) {
    CIRCLE("円形"), HEXAGON("六角形");

    fun accepts(x: Double, y: Double): Boolean {
        val distance = hypot(x, y)
        if (distance !in 0.30..1.0) return false
        if (this == CIRCLE) return true
        val angle = kotlin.math.abs(atan2(x, y))
        val edge = PI / 3
        val delta = angle % edge - edge / 2
        return distance * kotlin.math.cos(delta) <= kotlin.math.cos(PI / 6)
    }
}

internal enum class WristSide(val title: String) {
    LEFT("左"), RIGHT("右");

    val left get() = this == LEFT
    val opposite get() = if (left) RIGHT else LEFT
}

internal enum class WristAction(val title: String, val explanation: String) {
    APPS("アプリ", "すべてのアプリを開く"),
    MOTION("動き", "装飾の動きを切り替える"),
    DECOR("装飾", "ウィンドーの装飾を切り替える"),
    RECALL("呼び戻す", "アプリ棚を前方へ呼び戻す"),
    SETTINGS("設定", "Eidolon の設定を開く");

    companion object {
        val sectorDegrees get() = 360.0 / entries.size

        fun hit(u: Float, v: Float, skin: WristSkin = WristSkin.CIRCLE): WristAction? {
            val x = (u - 0.5) * 2
            val y = (0.5 - v) * 2
            if (!x.isFinite() || !y.isFinite() || !skin.accepts(x, y)) return null
            val angle = (atan2(x, y) * 180.0 / PI + 360.0) % 360.0
            val sector = ((angle + sectorDegrees / 2) / sectorDegrees).toInt()
            return entries[sector % entries.size].takeIf { kotlin.math.abs(angle - sector * sectorDegrees) <= sectorDegrees / 2 - 2 }
        }
    }
}

/** Decides when the wrist panel exists. A pose can never dispatch an action. */
internal class WristVisibility {
    var shown = false
        private set
    private var qualifyingSince: Long? = null
    private var waitForExit = false

    fun reset() {
        shown = false
        qualifyingSince = null
        waitForExit = false
    }

    fun dismiss() {
        reset()
        waitForExit = true
    }

    fun update(score: Float, nowMs: Long, held: Boolean): Boolean {
        if (!score.isFinite()) {
            reset()
        } else if (held) {
            qualifyingSince = null
        } else {
            val qualified = if (shown || waitForExit) score <= EXIT else score >= ENTER
            if (!qualified) {
                qualifyingSince = null
            } else {
                val started = qualifyingSince ?: nowMs.also { qualifyingSince = it }
                if (nowMs - started >= if (shown || waitForExit) 150 else 250) {
                    qualifyingSince = null
                    if (waitForExit) waitForExit = false else shown = !shown
                }
            }
        }
        return shown
    }

    companion object {
        const val ENTER = 0.8660254f
        const val EXIT = 0.6427876f
    }
}

internal class WristPress {
    private data class Capture(val source: Long, val action: WristAction)
    private var capture: Capture? = null
    val held get() = capture != null
    val action get() = capture?.action

    fun start(source: Long, target: WristAction?) {
        if (capture == null && target != null) capture = Capture(source, target)
    }

    fun release(source: Long, target: WristAction?): WristAction? {
        val current = capture?.takeIf { it.source == source } ?: return null
        capture = null
        return current.action.takeIf { it == target }
    }

    fun clear() { capture = null }
}
