package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Vector3
import com.meta.spatial.core.Pose
import com.meta.spatial.runtime.ButtonBits
import com.meta.spatial.runtime.HitInfo
import com.meta.spatial.runtime.InputListener
import com.meta.spatial.runtime.SceneObject
import com.meta.spatial.toolkit.Controller
import com.meta.spatial.toolkit.getAbsoluteTransform
import com.meta.spatial.toolkit.setAbsoluteTransform

/** Only samples while a panel is held. The SDK never writes an independent grab pose to this panel. */
internal class PanelGrip(
    private val panel: Entity,
    private val eye: () -> Vector3?,
    private val enabled: () -> Boolean,
    private val buttons: Int = SQUEEZE,
    private val aim: (Entity, Int) -> Pose? = { source, _ -> getAbsoluteTransform(source) },
    private val accepts: (Pose) -> Boolean = { true },
    private val finished: (Pose?) -> Unit = {},
) : InputListener {
    companion object { val SQUEEZE = ButtonBits.ButtonSqueezeL or ButtonBits.ButtonSqueezeR }
    private val handler = Handler(Looper.getMainLooper())
    private data class Hold(val source: Entity, val button: Int, val start: Pose, val offset: Pose,
                            val drag: ViewerPanelPlacement.Drag)
    private var hold: Hold? = null
    private var disposed = false
    val active get() = hold != null

    override fun onInput(receiver: SceneObject, hitInfo: HitInfo, sourceOfInput: Entity,
                         changed: Int, state: Int, downTime: Long): Boolean {
        val button = changed and state and buttons
        if (disposed || !enabled() || hitInfo.entity?.id != panel.id || Integer.bitCount(button) != 1) return false
        handler.post { begin(sourceOfInput, button) }
        return true
    }

    fun begin(source: Entity, button: Int) {
        if (disposed || active || !enabled() || button and buttons == 0) return
        val controller = runCatching { source.getComponent<Controller>() }.getOrNull() ?: return
        if (!controller.isActive || controller.buttonState and button == 0) return
        val from = runCatching { aim(source, button) }.getOrNull() ?: return
        val start = getAbsoluteTransform(panel)
        val drag = ViewerPanelPlacement.Drag.start(start.t, eye() ?: return) ?: return
        hold = Hold(source, button, start, from.inverse() * start, drag)
        handler.post(frame)
    }

    private val frame = object : Runnable {
        override fun run() {
            val h = hold ?: return
            // The scene owner cancels while the entity is live. A late frame must not write to a retired entity.
            if (!enabled()) { hold = null; return }
            val controller = runCatching { h.source.getComponent<Controller>() }.getOrNull()
            val from = runCatching { aim(h.source, h.button) }.getOrNull()
            val viewer = eye()
            if (controller?.isActive != true || from == null || viewer == null) { cancel(); return }
            val pose = h.drag.pose((from * h.offset).t, viewer)
            val accepted = pose?.takeIf(accepts)
            if (accepted != null) setAbsoluteTransform(panel, accepted)
            if (controller.buttonState and h.button == 0) {
                if (accepted == null) { cancel(); return }
                hold = null
                finished(pose)
            } else handler.postDelayed(this, 16)
        }
    }

    fun dispose() {
        disposed = true
        handler.removeCallbacks(frame)
        hold = null
    }

    fun cancel() {
        handler.removeCallbacks(frame)
        val h = hold ?: return
        hold = null
        setAbsoluteTransform(panel, h.start)
        finished(null)
    }
}
