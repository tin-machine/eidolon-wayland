package io.github.tinmachine.eidolon

import android.content.Context
import android.hardware.input.InputManager
import android.os.Handler
import android.os.Looper
import android.view.InputDevice
import android.view.KeyEvent

/** UI threadで入口だけを判定。文字/ユーザーのkey列をログへ残さない。 */
class PhysicalKeyboard(context: Context) : InputManager.InputDeviceListener {
    private val manager = context.getSystemService(InputManager::class.java)
    private var active=false
    private val consumed = mutableSetOf<Pair<Int, Int>>()
    fun resume() {
        if(active) return
        active=true;manager.registerInputDeviceListener(this, Handler(Looper.getMainLooper()));refreshDevices()
    }
    fun pause(resetInput: Boolean=true) { active=false;manager.unregisterInputDeviceListener(this);if(resetInput) NativeSessionHost.resetInput() }
    fun dispatch(event: KeyEvent): Boolean {
        if (event.action !in setOf(KeyEvent.ACTION_DOWN, KeyEvent.ACTION_UP)) return false
        val identity = event.deviceId to (if (event.scanCode != 0) event.scanCode else -event.keyCode)
        val previouslyConsumed = consumed.contains(identity)
        if (event.isSystem) {
            if (event.action == KeyEvent.ACTION_DOWN) NativeSessionHost.resetInput()
            return false
        }
        val device = event.device
        if (event.deviceId < 0 || device == null || device.isVirtual || !event.isFromSource(InputDevice.SOURCE_KEYBOARD)) return false
        if (event.isCanceled) { consumed.remove(identity); NativeSessionHost.resetInput(); return previouslyConsumed }
        val sent = NativeSessionHost.sendPhysicalKey(event)
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
    override fun onInputDeviceAdded(deviceId: Int) { if(active) refreshDevices() }
    override fun onInputDeviceChanged(deviceId: Int) { if(active) {refreshDevices();NativeSessionHost.resetInput()} }
    override fun onInputDeviceRemoved(deviceId: Int) {
        if(!active) return
        refreshDevices()
        consumed.removeAll { it.first == deviceId }
        NativeSessionHost.resetInput()
    }
}
