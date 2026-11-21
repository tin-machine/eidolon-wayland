package io.github.tinmachine.eidolon

import java.util.concurrent.CopyOnWriteArraySet
import java.util.concurrent.atomic.AtomicBoolean

/** Process-wide invalidation hint. Each owner still reads and validates its own snapshot. */
internal object RuntimeUiChanges {
    private val listeners = CopyOnWriteArraySet<() -> Unit>()
    private val started = AtomicBoolean(false)

    fun subscribe(listener: () -> Unit) { listeners.add(listener) }
    fun unsubscribe(listener: () -> Unit) { listeners.remove(listener) }
    fun invalidate() { listeners.forEach { it() } }

    fun start() {
        if (!started.compareAndSet(false, true)) return
        Thread({
            var seen = 0L
            while (true) {
                val revision = NativeBridge.nativeWaitForUiChange(seen)
                if (revision != seen) {
                    seen = revision
                    invalidate()
                    // Merge bursts of input completions without delaying the first notification.
                    Thread.sleep(12)
                }
            }
        }, "eidolon-ui-wake").apply { isDaemon = true }.start()
    }
}
