package io.github.tinmachine.eidolon

import android.view.KeyEvent
import android.view.View
import android.view.Window

/** SpatialのPresentationはActivityとは別Window。子Viewが消費する前に同じ物理入力gateを通す。 */
class PanelKeyboardBridge(private val view: View, private val window: Window, forward: (KeyEvent) -> Boolean) : AutoCloseable {
    private val previous = checkNotNull(window.callback)
    private val callback = object : Window.Callback by previous {
        override fun dispatchKeyEvent(event: KeyEvent): Boolean = forward(event) || previous.dispatchKeyEvent(event)
    }
    private var closed = false
    private val listener = object : View.OnAttachStateChangeListener {
        override fun onViewAttachedToWindow(view: View) = Unit
        override fun onViewDetachedFromWindow(view: View) { close() }
    }
    init {
        window.callback = callback
        view.addOnAttachStateChangeListener(listener)
        KeyboardIngress.panelAttached()
    }
    override fun close() {
        if (closed) return
        closed = true
        if (window.callback === callback) window.callback = previous
        view.removeOnAttachStateChangeListener(listener)
        KeyboardIngress.panelDetached()
    }
}
