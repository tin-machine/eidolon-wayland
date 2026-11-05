package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.InputDevice
import android.view.KeyEvent
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicReference

/** 固定合成key。物理device filter/文字の視認成功とは分けて記録する。 */
object KeyboardCase {
    private val lateCompletion = AtomicReference<String?>(null)
    fun commandComplete(request: String, code: String) { if (request=="9000001") lateCompletion.set(code) }
    fun start(nonce: String, adapter: SpatialPanelAdapter, complete: () -> Unit) { lateCompletion.set(null); Check(nonce, adapter, complete).start() }
    private class Check(val nonce: String, val adapter: SpatialPanelAdapter, val complete: () -> Unit) : Runnable {
        val ui = Handler(Looper.getMainLooper())
        val result = JSONObject().put("case", "keyboard").put("nonce", nonce).put("result", "running")
            .put("consumer", "SDK").put("synthetic_input", true).put("synthetic_visibility", false)
            .put("physical_input_verified", false).put("physical_entry_verified", false).put("text_verified", false)
            .put("product_host_path", true)
        val rows = JSONArray()
        val stop = AtomicReference<String?>(null)
        var stage = "ready"
        var handle = 0L
        var target = JSONObject()
        var epoch = 0L
        var base = 0L
        var copies = 0L
        var deadline = SystemClock.elapsedRealtime()+45000
        val lateRequest = "9000001"
        fun start() { NativeSessionHost.publishCheck(result); ui.post(this) }
        fun record(name: String, state: JSONObject) {
            rows.put(JSONObject().put("stage", name).put("runtime", state)); result.put("cycles", rows)
            NativeSessionHost.publishCheck(result)
        }
        fun send(action: Int, code: Int, scan: Int, repeat: Int = 0) {
            val now = SystemClock.uptimeMillis()
            val event = KeyEvent(now, now, action, code, repeat, 0, Int.MAX_VALUE-7, scan, 0, InputDevice.SOURCE_KEYBOARD)
            check(NativeSessionHost.sendPhysicalKey(event))
        }
        override fun run() {
            try {
                check(SystemClock.elapsedRealtime() < deadline)
                val current = NativeSessionHost.currentHandle()
                if (current > 0) {
                    val state = JSONObject(NativeBridge.nativeSnapshot(current)).getJSONObject("runtime")
                    check(state.getString("state") != "Failed")
                    val app = state.optJSONObject("app")
                    if (app != null) check(app.isNull("error"))
                    val input = app?.optJSONObject("input")
                    val targets = state.getJSONObject("render").getJSONArray("targets")
                    val panel = if (targets.length()==1) targets.getJSONObject(0) else null
                    val focused = state.getBoolean("activity_visible") && state.getString("xr_state")=="FOCUSED"
                    when (stage) {
                        "ready" -> if (state.getString("state")=="Ready" && focused) {
                            check(state.getJSONObject("transport").getString("state")=="Idle")
                            handle = current; NativeSessionHost.openTerminal(); stage = "display"
                        }
                        "display" -> if (focused && app?.optString("mode")=="foot" && panel?.optString("role")=="windows" && panel.getLong("frames")>=3) {
                            val plans = state.getJSONArray("panels")
                            if (plans.length()==1 && plans.getJSONObject(0).getBoolean("applied")) {
                                target = JSONObject(panel.getJSONObject("key").toString()); record("before",state)
                                NativeSessionHost.focusTerminal(); stage = "focus"
                            }
                        }
                        "focus" -> if (input?.optString("focus_window")==target.getString("window") && NativeSessionHost.keyboardFocused()) {
                            check(current==handle && focused)
                            epoch=state.getLong("input_resets"); base=input.getLong("delivered"); copies=checkNotNull(app).getLong("copies")
                            record("focused",state)
                            send(KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_A,30); send(KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_A,30,1)
                            send(KeyEvent.ACTION_UP,KeyEvent.KEYCODE_A,30)
                            send(KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_DEL,14); send(KeyEvent.ACTION_UP,KeyEvent.KEYCODE_DEL,14)
                            stage="typed"; deadline=SystemClock.elapsedRealtime()+15000
                        }
                        "typed" -> if (input?.getLong("delivered")==base+4 && checkNotNull(app).getLong("copies")>copies) {
                            check(input.getInt("pressed")==0); record("typed",state)
                            send(KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_SHIFT_LEFT,42); send(KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_A,30)
                            stage="held"
                        }
                        "held" -> if (input?.getInt("pressed")==2 && input.getLong("delivered")==base+6) {
                            record("held",state); NativeSessionHost.resetInput(); stage="reset"
                        }
                        "reset" -> if (input?.getInt("pressed")==0 && input.getLong("delivered")==base+8 && input.isNull("focus_window") && state.getLong("input_resets")>epoch) {
                            record("reset",state)
                            val key = JSONObject().put("action","released").put("device",Int.MAX_VALUE-7).put("scan_code",42)
                                .put("key_code",KeyEvent.KEYCODE_SHIFT_LEFT).put("repeat",0).put("meta",0).put("time_ms",0)
                            check(NativeBridge.nativeCommand(handle,JSONObject().put("schema",1).put("type","key").put("request_id",lateRequest)
                                .put("target",target).put("input_epoch",epoch).put("key",key).toString())==NativeBridge.ACCEPTED)
                            stage="late"
                        }
                        "late" -> if (lateCompletion.getAndSet(null)=="STALE") {
                            check(input?.getLong("delivered")==base+8 && input.getInt("pressed")==0)
                            record("late",state); NativeSessionHost.focusTerminal(); stage="refocus"
                        }
                        "refocus" -> if (input?.optString("focus_window")==target.getString("window") && NativeSessionHost.keyboardFocused()) {
                            check(input.getInt("pressed")==0); record("refocused",state)
                            send(KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_SPACE,57); send(KeyEvent.ACTION_UP,KeyEvent.KEYCODE_SPACE,57)
                            stage="final"
                        }
                        "final" -> if (input?.getLong("delivered")==base+10 && input.getInt("pressed")==0) {
                            record("final",state); stage="restore"; deadline=SystemClock.elapsedRealtime()+20000
                            val started=SystemClock.elapsedRealtime(); val copy=result.toString()
                            NativeSessionHost.withStoppedRuntime({ store ->
                                check(JSONObject(NativeBridge.nativeSnapshot(handle)).getInt("code")==NativeBridge.STALE)
                                stop.set(JSONObject().put("old_runtime_destroyed",true).put("stop_ms",SystemClock.elapsedRealtime()-started).toString())
                                store.publishCheck(JSONObject(copy).put("stop",JSONObject(checkNotNull(stop.get()))))
                            }, {})
                        }
                        "restore" -> if (current>handle && focused && panel?.optString("role")=="diagnostic" && panel.getLong("frames")>=3) {
                            check(state.getJSONObject("transport").getString("state")=="Idle")
                            check(state.getJSONObject("transport").getJSONArray("pending_cleanup").length()==0)
                            result.put("old_handle",handle.toString()).put("new_handle",current.toString()).put("target",target)
                                .put("stop",JSONObject(checkNotNull(stop.get()))).put("restored",state).put("result","pass")
                            NativeSessionHost.publishCheck(result); complete(); return
                        }
                    }
                }
                ui.postDelayed(this,32)
            } catch (failure: Exception) {
                result.put("result","fail").put("stage",stage).put("failure_class",failure.javaClass.simpleName)
                    .put("failure_site",failure.stackTrace.firstOrNull()?.toString()).put("snapshot",JSONObject(NativeBridge.nativeSnapshot(NativeSessionHost.currentHandle())))
                val copy=result.toString()
                NativeSessionHost.withStoppedRuntime({ store -> store.publishCheck(JSONObject(copy)) },complete)
            }
        }
    }
}
