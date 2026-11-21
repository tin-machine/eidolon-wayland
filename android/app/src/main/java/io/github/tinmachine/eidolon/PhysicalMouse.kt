package io.github.tinmachine.eidolon

import android.content.Context
import android.hardware.input.InputManager
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.InputDevice
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import androidx.compose.runtime.mutableStateOf
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Pose
import com.meta.spatial.core.Vector3
import com.meta.spatial.runtime.ButtonBits
import com.meta.spatial.runtime.HitInfo
import com.meta.spatial.runtime.Scene
import com.meta.spatial.toolkit.Mesh
import com.meta.spatial.toolkit.MeshCollision
import com.meta.spatial.toolkit.Scale
import com.meta.spatial.toolkit.Transform
import java.lang.ref.WeakReference

/** The same reference frame is used for drawing and for hit testing. */
internal class MouseWorkspace(initialPose: Pose) {
    private val anchor = Pose(initialPose.t.copy(), initialPose.q.copy())
    private var x = 0f
    private var y = 0f

    fun move(dx: Float, dy: Float) {
        x = (x + dx / 512f).coerceIn(-.95f, .95f)
        y = (y - dy / 512f).coerceIn(-.72f, .72f)
    }

    fun ray(): Pair<Vector3, Vector3> {
        val direction = (anchor.forward() + anchor.right() * x + anchor.up() * y).normalize()
        return anchor.t to direction
    }

    fun cursorPose(location: Vector3) = Pose(location, anchor.q)
}

/** Owns one mouse ray and cursor in the current XR scene. */
class PhysicalMouse(context: Context, private val panels: SpatialPanelAdapter) {
    private val devices = context.getSystemService(InputManager::class.java)
    private var listening = false
    private var mousePresent = false
    private val deviceListener = object : InputManager.InputDeviceListener {
        override fun onInputDeviceAdded(id: Int) = refreshDevices()
        override fun onInputDeviceChanged(id: Int) = refreshDevices()
        override fun onInputDeviceRemoved(id: Int) {
            if (capture?.device == id) stopCapture()
            refreshDevices()
        }
    }
    val enabled = mutableStateOf(true)
    private val ui = Handler(Looper.getMainLooper())
    private val hosts = mutableListOf<WeakReference<View>>()
    private var scene: Scene? = null
    private var capture: Capture? = null
    private var workspaceScene: WeakReference<Scene>? = null
    private var workspace: MouseWorkspace? = null
    private var retryAfter = 0L
    private var toggleChord = false

    private class Capture(val view: View, val scene: Scene, val requestedAt: Long) {
        var source: Entity? = null
        var cursor: Entity? = null
        var hovered: Long? = null
        var device: Int? = null
        var buttons = 0
    }

    fun track(view: View) {
        hosts.removeAll { it.get() == null || it.get() === view }
        hosts.add(WeakReference(view))
        focusChanged()
    }

    fun setFocusedScene(value: Scene?) {
        if (Looper.myLooper() != ui.looper) {
            ui.post { setFocusedScene(value) }
            return
        }
        if (scene === value) return
        stopCapture()
        scene = value
        updateListening()
    }

    fun toggle() {
        enabled.value = !enabled.value
        if (!enabled.value) {
            stopCapture()
            workspace = null
            workspaceScene = null
        }
        updateListening()
    }

    private fun updateListening() {
        ui.removeCallbacks(maintain)
        val needed = scene != null && enabled.value
        if (needed != listening) {
            listening = needed
            if (needed) devices.registerInputDeviceListener(deviceListener, ui)
            else devices.unregisterInputDeviceListener(deviceListener)
        }
        if (needed) refreshDevices()
    }

    private fun refreshDevices() {
        if (!listening) return
        mousePresent = devices.inputDeviceIds.any { id ->
            devices.getInputDevice(id)?.let { !it.isVirtual && it.supportsSource(InputDevice.SOURCE_MOUSE) } == true
        }
        if (!mousePresent) stopCapture()
        focusChanged()
    }

    fun focusChanged() {
        ui.removeCallbacks(maintain)
        if (listening && mousePresent) {
            retryAfter = 0L
            ui.post(maintain)
        }
    }

    fun key(event: KeyEvent): Boolean {
        if (event.keyCode != KeyEvent.KEYCODE_M) return false
        if (event.action == KeyEvent.ACTION_UP && toggleChord) {
            toggleChord = false
            return true
        }
        if (event.action != KeyEvent.ACTION_DOWN || !event.isCtrlPressed || !event.isAltPressed) return false
        if (event.repeatCount == 0 && scene != null) {
            toggleChord = true
            toggle()
        }
        return toggleChord
    }

    private val maintain = object : Runnable {
        override fun run() {
            val currentScene = scene ?: return
            if (!enabled.value || !mousePresent) return
            val now = SystemClock.uptimeMillis()
            val current = capture
            if (current == null) {
                if (now >= retryAfter) {
                    retryAfter = now + 1000
                    startCapture(currentScene, now)
                }
            } else if (!current.view.isAttachedToWindow || !current.view.hasWindowFocus() || !current.view.isFocused ||
                (!current.view.hasPointerCapture() && now - current.requestedAt >= 800) ||
                current.device?.let { InputDevice.getDevice(it) == null } == true
            ) {
                stopCapture()
            } else if (current.view.hasPointerCapture()) {
                val hit = placeCursor(current)
                if (current.hovered != hit?.entity?.id) dispatch(current, hit, 0)
            }
            // A stationary cursor still needs hit testing when another window moves beneath it.
            val delay = when {
                capture?.view?.hasPointerCapture() == true -> 33L
                capture != null -> 150L
                else -> 1000L
            }
            ui.postDelayed(this, delay)
        }
    }

    private fun startCapture(currentScene: Scene, now: Long) {
        hosts.removeAll { it.get() == null }
        val host = hosts.asReversed().firstNotNullOfOrNull { entry ->
            entry.get()?.takeIf { it.isAttachedToWindow && it.hasWindowFocus() }
        } ?: return
        // Android delivers captured events to the focused child in this window.
        val view = host.rootView.findFocus() ?: host.takeIf {
            it.isFocusable && it.requestFocus()
        } ?: return
        val current = Capture(view, currentScene, now)
        capture = current
        view.setOnCapturedPointerListener { _, event -> handle(current, event) }
        view.requestPointerCapture()
    }

    private fun stopCapture() {
        val current = capture ?: return
        capture = null
        current.view.setOnCapturedPointerListener(null)
        current.view.releasePointerCapture()
        current.source?.let { source ->
            current.scene.onNoIntersectionInput(source, current.buttons, 0)
            current.scene.resolveInput()
            source.destroy()
        }
        current.cursor?.destroy()
    }

    private fun workspaceFor(scene: Scene): MouseWorkspace {
        if (workspaceScene?.get() === scene) workspace?.let { return it }
        // Sample once so moving the headset cannot move the pointer or its click ray.
        return MouseWorkspace(scene.getViewerPose()).also {
            workspace = it
            workspaceScene = WeakReference(scene)
        }
    }

    private fun placeCursor(current: Capture): HitInfo? {
        val workspace = workspaceFor(current.scene)
        val (origin, direction) = workspace.ray()
        val hit = current.scene.lineSegmentIntersect(origin, origin + direction * 8f)
        val location = hit?.point?.minus(direction * .012f) ?: (origin + direction * 2f)
        val scale = (origin.distanceTo(location) / 1.6f).coerceIn(.4f, 3f)
        val pose = workspace.cursorPose(location)
        val cursor = current.cursor ?: Entity.create(listOf(
            Mesh(Uri.parse(WindowControlMeshes.MOUSE_CURSOR), MeshCollision.NoCollision),
            Transform(pose), Scale(Vector3(scale)),
        )).also { current.cursor = it }
        cursor.setComponent(Transform(pose))
        cursor.setComponent(Scale(Vector3(scale)))
        return hit
    }

    private fun dispatch(current: Capture, hit: HitInfo?, changed: Int) {
        val source = current.source ?: Entity.create(emptyList()).also { current.source = it }
        if (hit == null) current.scene.onNoIntersectionInput(source, changed, current.buttons)
        else current.scene.onInput(hit, source, changed, current.buttons)
        current.scene.resolveInput()
        current.hovered = hit?.entity?.id
    }

    private fun handle(current: Capture, event: MotionEvent): Boolean {
        if (capture !== current || !current.view.hasPointerCapture() ||
            (!event.isFromSource(InputDevice.SOURCE_MOUSE_RELATIVE) && !event.isFromSource(InputDevice.SOURCE_MOUSE))
        ) return false
        if (event.deviceId < 0 || current.device?.let { it != event.deviceId } == true) return false
        current.device = event.deviceId
        if (event.actionMasked == MotionEvent.ACTION_CANCEL) {
            stopCapture()
            return true
        }
        when (event.actionMasked) {
            MotionEvent.ACTION_MOVE, MotionEvent.ACTION_HOVER_MOVE -> {
                var dx = event.x
                var dy = event.y
                for (i in 0 until event.historySize) {
                    dx += event.getHistoricalX(i)
                    dy += event.getHistoricalY(i)
                }
                if (!dx.isFinite() || !dy.isFinite()) return false
                workspaceFor(current.scene).move(dx, dy)
                dispatch(current, placeCursor(current), 0)
            }
            MotionEvent.ACTION_BUTTON_PRESS, MotionEvent.ACTION_BUTTON_RELEASE,
            MotionEvent.ACTION_DOWN, MotionEvent.ACTION_UP -> {
                val state = event.buttonState
                val next = (if (state and MotionEvent.BUTTON_PRIMARY != 0) ButtonBits.ButtonTriggerR else 0) or
                    (if (state and MotionEvent.BUTTON_SECONDARY != 0) ButtonBits.ButtonB else 0) or
                    (if (state and MotionEvent.BUTTON_TERTIARY != 0) ButtonBits.ButtonThumbRClick else 0)
                val changed = current.buttons xor next
                if (changed != 0) {
                    current.buttons = next
                    dispatch(current, placeCursor(current), changed)
                }
            }
            MotionEvent.ACTION_SCROLL -> {
                val x = (event.getAxisValue(MotionEvent.AXIS_HSCROLL).toDouble() * 48).coerceIn(-4096.0, 4096.0)
                val y = (-event.getAxisValue(MotionEvent.AXIS_VSCROLL).toDouble() * 48).coerceIn(-4096.0, 4096.0)
                if (!x.isFinite() || !y.isFinite()) return false
                if (x != 0.0 || y != 0.0) {
                    val hit = placeCursor(current)
                    dispatch(current, hit, 0)
                    if (hit != null) panels.scrollMouse(hit, checkNotNull(current.source), x, y)
                }
            }
            else -> return false
        }
        return true
    }
}
