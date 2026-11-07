package io.github.tinmachine.eidolon

/** Ctrl+Spaceの所有権だけを扱うpure model。本文やキー列を記録しない。 */
class ImeShortcut {
    enum class Key { CTRL_LEFT, CTRL_RIGHT, SPACE, OTHER }
    enum class Action { DOWN, UP }
    data class Event(
        val device: Int,
        val key: Key,
        val action: Action,
        val downTime: Long,
        val repeat: Int = 0,
        val ctrl: Boolean = false,
        val shift: Boolean = false,
        val alt: Boolean = false,
        val meta: Boolean = false,
        val physical: Boolean = true,
        val active: Boolean = true,
        val system: Boolean = false,
        val cancelled: Boolean = false,
    )

    private data class Press(val device: Int, val key: Key, val downTime: Long)
    private val controls = mutableMapOf<Int, MutableMap<Key, Long>>()
    private val spaces = mutableMapOf<Int, Long>()
    private val owned = mutableSetOf<Press>()

    /** 焦点/epoch/pauseで再armを要求する。受理済みpressの遅いupは引き続き消費する。 */
    fun reset() { controls.clear(); spaces.clear() }

    fun deviceChanged(device: Int) { controls.remove(device); spaces.remove(device) }

    /**
     * trueを返すownerは、先行Ctrl downを元のnative ownerへのresetで解除する。
     * このmodelはkeyupを合成しない。受理したCtrl/Spaceのtailも新targetへ渡さない。
     */
    fun dispatch(event: Event, onToggle: () -> Boolean): Boolean {
        val press = Press(event.device, event.key, event.downTime)
        if (event.cancelled) deviceChanged(event.device)
        if (event.system) reset()
        if (press in owned) {
            if (event.action == Action.UP) {
                owned.remove(press)
                release(event)
            }
            return true
        }
        if (!event.active || !event.physical || event.device < 0 || event.system || event.cancelled) return false
        if (event.action == Action.UP) {
            release(event)
            return false
        }
        if (event.repeat != 0) return false
        if (event.key == Key.CTRL_LEFT || event.key == Key.CTRL_RIGHT) {
            controls.getOrPut(event.device) { mutableMapOf() }[event.key] = event.downTime
            return false
        }
        if (event.key != Key.SPACE || spaces[event.device] == event.downTime) return false
        spaces[event.device] = event.downTime
        val held = controls[event.device].orEmpty().toMap()
        if (held.isEmpty() || !event.ctrl || event.shift || event.alt || event.meta) return false
        if (!onToggle()) return false
        // callbackが同期的にresetしても、このpressの所有権は失わない。
        owned.add(press)
        for ((key, downTime) in held) owned.add(Press(event.device, key, downTime))
        return true
    }

    private fun release(event: Event) {
        if (event.key == Key.SPACE) {
            if (spaces[event.device] == event.downTime) spaces.remove(event.device)
        } else if (controls[event.device]?.get(event.key) == event.downTime) {
            controls[event.device]?.remove(event.key)
            if (controls[event.device].isNullOrEmpty()) controls.remove(event.device)
        }
    }
}
