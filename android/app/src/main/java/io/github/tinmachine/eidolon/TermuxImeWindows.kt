package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.view.Window
import android.view.WindowManager.LayoutParams.FLAG_ALT_FOCUSABLE_IM
import java.util.WeakHashMap
import java.util.concurrent.atomic.AtomicBoolean

/** Give the selected Linux keyboard focus the physical keys before Android text conversion. */
internal object TermuxImeWindows {
    private val main = Handler(Looper.getMainLooper())
    private val raw = AtomicBoolean(false)
    private val original = WeakHashMap<Window, Boolean>()

    fun attach(window: Window) {
        original.putIfAbsent(window, window.attributes.flags and FLAG_ALT_FOCUSABLE_IM != 0)
        apply(window, original.getValue(window) || raw.get())
    }

    fun detach(window: Window) {
        original.remove(window)?.let { apply(window, it) }
    }

    fun rawInput(active: Boolean) {
        if (raw.getAndSet(active) == active) return
        if (Looper.myLooper() == Looper.getMainLooper()) refresh() else main.post { refresh() }
    }

    private fun refresh() {
        original.forEach { (window, flag) -> apply(window, flag || raw.get()) }
    }

    private fun apply(window: Window, flag: Boolean) {
        if ((window.attributes.flags and FLAG_ALT_FOCUSABLE_IM != 0) == flag) return
        if (flag) window.addFlags(FLAG_ALT_FOCUSABLE_IM) else window.clearFlags(FLAG_ALT_FOCUSABLE_IM)
    }
}
