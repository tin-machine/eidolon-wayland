package io.github.tinmachine.eidolon

import android.content.Context
import android.hardware.input.InputManager
import android.os.Handler
import android.os.Looper
import android.view.InputDevice
import android.view.KeyEvent

/** UI threadで入口だけを判定。文字/ユーザーのkey列をログへ残さない。 */
class PhysicalKeyboard(context: Context, private val onImeToggle: () -> Boolean = { false }) : InputManager.InputDeviceListener {
    private val manager = context.getSystemService(InputManager::class.java)
    private var active=false
    private val consumed = mutableSetOf<Pair<Int, Int>>()
    private val imeShortcut = ImeShortcut()
    fun resume() {
        if(active) return
        active=true;manager.registerInputDeviceListener(this, Handler(Looper.getMainLooper()));refreshDevices()
    }
    fun pause(resetInput: Boolean=true) { active=false;imeShortcut.reset();manager.unregisterInputDeviceListener(this);if(resetInput) NativeSessionHost.resetInput() }
    /** ownerがfocus/epochを失効させたときにも呼ぶ。受理済みupの消費は維持する。 */
    fun resetShortcutState() { imeShortcut.reset() }
    fun dispatch(event: KeyEvent, source: String = "activity"): Boolean {
        KeyboardIngress.received(source)
        if (event.action !in setOf(KeyEvent.ACTION_DOWN, KeyEvent.ACTION_UP)) {KeyboardIngress.outcome("unsupported_action");return false}
        val identity = event.deviceId to (if (event.scanCode != 0) event.scanCode else -event.keyCode)
        val previouslyConsumed = consumed.contains(identity)
        val device = event.device
        val physical = event.deviceId >= 0 && device != null && !device.isVirtual && event.isFromSource(InputDevice.SOURCE_KEYBOARD)
        val shortcut = ImeShortcut.Event(
            device = event.deviceId,
            key = when(event.keyCode) {
                KeyEvent.KEYCODE_CTRL_LEFT -> ImeShortcut.Key.CTRL_LEFT
                KeyEvent.KEYCODE_CTRL_RIGHT -> ImeShortcut.Key.CTRL_RIGHT
                KeyEvent.KEYCODE_SPACE -> ImeShortcut.Key.SPACE
                else -> ImeShortcut.Key.OTHER
            },
            action = if(event.action == KeyEvent.ACTION_DOWN) ImeShortcut.Action.DOWN else ImeShortcut.Action.UP,
            downTime = event.downTime, repeat = event.repeatCount,
            ctrl = event.isCtrlPressed, shift = event.isShiftPressed, alt = event.isAltPressed, meta = event.isMetaPressed,
            physical = physical, active = active, system = event.isSystem, cancelled = event.isCanceled,
        )
        // pause/device切替後でも受理済みpressのtailを先に消費し、新targetへkeyupを送らない。
        if (imeShortcut.dispatch(shortcut, onImeToggle)) {
            if(active && physical) KeyboardIngress.physical()
            if(event.action == KeyEvent.ACTION_UP) consumed.remove(identity)
            if(event.isCanceled) {imeShortcut.reset();NativeSessionHost.resetInput()}
            KeyboardIngress.outcome("ime_shortcut")
            return true
        }
        if (!active) {KeyboardIngress.outcome("inactive");return false}
        if (event.isSystem) {
            KeyboardIngress.outcome("system")
            if (event.action == KeyEvent.ACTION_DOWN) NativeSessionHost.resetInput()
            return false
        }
        if (!physical) {
            KeyboardIngress.outcome("not_physical_keyboard");return false
        }
        KeyboardIngress.physical()
        if (event.isCanceled) { KeyboardIngress.outcome("cancelled");consumed.remove(identity);imeShortcut.reset(); NativeSessionHost.resetInput(); return previouslyConsumed }
        val sent = NativeSessionHost.sendPhysicalKey(event)
        KeyboardIngress.outcome(if(sent) "enqueued" else "input_gate_closed")
        if (sent && event.action == KeyEvent.ACTION_DOWN) consumed.add(identity)
        if (event.action == KeyEvent.ACTION_UP) consumed.remove(identity)
        // 焦点喪失でreset済みのkey upもComposeへ漏らさない。
        return sent || previouslyConsumed
    }
    private fun refreshDevices() {
        val count=runCatching { manager.inputDeviceIds.toList().mapNotNull {manager.getInputDevice(it)}.count {
            !it.isVirtual && it.isExternal && it.supportsSource(InputDevice.SOURCE_KEYBOARD) && it.keyboardType==InputDevice.KEYBOARD_TYPE_ALPHABETIC
        } }.getOrNull()
        NativeSessionHost.setKeyboardCount(count)
    }
    override fun onInputDeviceAdded(deviceId: Int) { imeShortcut.deviceChanged(deviceId);if(active) refreshDevices() }
    override fun onInputDeviceChanged(deviceId: Int) { imeShortcut.reset();if(active) {refreshDevices();NativeSessionHost.resetInput()} }
    override fun onInputDeviceRemoved(deviceId: Int) {
        imeShortcut.reset()
        if(!active) return
        refreshDevices()
        consumed.removeAll { it.first == deviceId }
        NativeSessionHost.resetInput()
    }
}
