package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicReference

/** 通常Host/SDK可視性だけを使う。ImageReader・synthetic visibilityの結果で代用しない。 */
object FootSdkCase {
    fun start(nonce: String, adapter: SpatialPanelAdapter, complete: (JSONObject) -> Unit) { Check(nonce, adapter, complete).start() }
    private class Check(val nonce: String, val adapter: SpatialPanelAdapter, val complete: (JSONObject) -> Unit) : Runnable {
        val ui = Handler(Looper.getMainLooper())
        val result = JSONObject().put("case", "foot").put("nonce", nonce).put("result", "running")
            .put("consumer", "SDK").put("synthetic_visibility", false).put("product_host_path", true).put("hmd_verified", false)
        val cycles = JSONArray()
        val stop = AtomicReference<String?>(null)
        var stage = "ready"
        var handle = 0L
        var previousGeneration = 0L
        var deadline = SystemClock.elapsedRealtime()+45000
        fun start() { NativeSessionHost.publishCheck(result); ui.post(this) }
        override fun run() {
            try {
                check(SystemClock.elapsedRealtime() < deadline)
                val current = NativeSessionHost.currentHandle()
                if (current > 0) {
                    val runtime = JSONObject(NativeBridge.nativeSnapshot(current)).optJSONObject("runtime")
                    val render = runtime?.getJSONObject("render")
                    val targets = render?.getJSONArray("targets")
                    val target = if (targets?.length() == 1) targets.getJSONObject(0) else null
                    val visible = runtime?.getBoolean("activity_visible") == true && runtime.optString("xr_state") in setOf("VISIBLE", "FOCUSED")
                    when (stage) {
                        "ready" -> if (runtime?.getString("state") == "Ready") {
                            check(runtime.getJSONObject("transport").getString("state") == "Idle")
                            handle = current
                            // 通常ボタンと同じ入口で二重clickも検査する。
                            NativeSessionHost.openTerminal(); NativeSessionHost.openTerminal()
                            stage = "display"
                        }
                        "display", "recreate" -> {
                            check(current == handle)
                            val state = checkNotNull(runtime)
                            val transport = state.getJSONObject("transport")
                            // 接続拒否は製品Hostの必要時起動へ進める。認証/pin不一致は失敗のまま。
                            check(transport.getString("state") != "Failed" || transport.optString("error")=="SSH_CONNECTION_REFUSED")
                            val app = state.optJSONObject("app")
                            if (app != null) check(app.isNull("error"))
                            val plans = state.getJSONArray("panels")
                            val plan = if (plans.length() == 1) plans.getJSONObject(0) else null
                            if (visible && target != null && target.optString("role") == "windows" && target.getLong("frames") >= 3 &&
                                target.getJSONObject("key").getString("generation").toLong() > previousGeneration &&
                                plan?.optString("action") == "create" && plan.getBoolean("applied") && app != null && app.getLong("callbacks") > 0) {
                                check(app.getString("callback_clock") == "target-driven" && app.getString("mode") == "foot" && app.getBoolean("non_uniform"))
                                check(transport.getInt("credential_requests") == 1)
                                check(adapter.metrics().getInt("bindings") == 1 && adapter.metrics().getInt("listeners") == 1)
                                previousGeneration = target.getJSONObject("key").getString("generation").toLong()
                                cycles.put(JSONObject().put("runtime", state).put("adapter", adapter.metrics()))
                                result.put("cycles", cycles); NativeSessionHost.publishCheck(result)
                                if (cycles.length() == 1) {
                                    check(adapter.recreate()); stage = "recreate"; deadline = SystemClock.elapsedRealtime()+15000
                                } else {
                                    stage = "restore"; deadline = SystemClock.elapsedRealtime()+20000
                                    val started = SystemClock.elapsedRealtime()
                                    val copy = result.toString()
                                    NativeSessionHost.withStoppedRuntime({ store ->
                                        val after = JSONObject(NativeBridge.nativeSnapshot(handle))
                                        check(after.getInt("code") == NativeBridge.STALE && after.getJSONObject("registry").getInt("runtimes") == 0)
                                        stop.set(JSONObject().put("old_runtime_destroyed", true).put("stop_ms", SystemClock.elapsedRealtime()-started).toString())
                                        store.publishCheck(JSONObject(copy).put("stop", JSONObject(checkNotNull(stop.get()))))
                                    }, {})
                                }
                            }
                        }
                        "restore" -> if (current > handle && visible && runtime?.getString("state") == "Ready" &&
                            target?.optString("role") == "diagnostic" && target.getLong("frames") >= 3) {
                            check(runtime.getJSONObject("transport").getString("state") == "Idle")
                            check(runtime.getJSONObject("transport").getJSONArray("pending_cleanup").length() == 0)
                            check(target.getBoolean("readback"))
                            result.put("old_handle", handle.toString()).put("new_handle", current.toString())
                                .put("stop", JSONObject(checkNotNull(stop.get()))).put("restored", runtime).put("result", "pass")
                            complete(result); return
                        }
                    }
                }
                ui.postDelayed(this, 32)
            } catch (failure: Exception) {
                result.put("result", "fail").put("stage", stage).put("failure_class", failure.javaClass.simpleName)
                    .put("failure_site", failure.stackTrace.firstOrNull()?.toString()).put("adapter", adapter.metrics())
                val current = NativeSessionHost.currentHandle()
                if (current > 0) result.put("snapshot", JSONObject(NativeBridge.nativeSnapshot(current)))
                val copy = result.toString()
                NativeSessionHost.withStoppedRuntime({ store -> store.publishCheck(JSONObject(copy)) }, {complete(result)})
            }
        }
    }
}
