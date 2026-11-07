package io.github.tinmachine.eidolon

import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference
import org.json.JSONObject

/** 入力経路の件数だけ。キー、文字、device ID、時刻列を記録しない。 */
object KeyboardIngress {
    private val activity = AtomicLong()
    private val control = AtomicLong()
    private val launcher = AtomicLong()
    private val panels = AtomicLong()
    private val physical = AtomicLong()
    private val enqueued = AtomicLong()
    private val last = AtomicReference("not_received")
    fun received(source: String) { when(source) {
        "control" -> control.incrementAndGet()
        "launcher" -> launcher.incrementAndGet()
        else -> activity.incrementAndGet()
    } }
    fun physical() {physical.incrementAndGet()}
    fun outcome(reason: String) {last.set(reason);if(reason=="enqueued") enqueued.incrementAndGet()}
    fun panelAttached() {panels.incrementAndGet()}
    fun panelDetached() {panels.decrementAndGet()}
    fun json(): JSONObject = JSONObject().put("activity_received",activity.get()).put("control_received",control.get())
        .put("launcher_received",launcher.get()).put("panel_windows",panels.get()).put("physical_events",physical.get())
        .put("native_enqueued",enqueued.get()).put("last_outcome",last.get())
}
