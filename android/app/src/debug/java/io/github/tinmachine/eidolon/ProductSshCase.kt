package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicReference

/** 製品Hostのcredential callbackを使用し、GLとSSHが動くruntimeを停止・再作成する。 */
object ProductSshCase {
    fun start(nonce: String, complete: () -> Unit) { Check(nonce, complete).start() }
    private class Check(val nonce: String, val complete: () -> Unit) : Runnable {
        val ui = Handler(Looper.getMainLooper())
        val result = JSONObject().put("case", "ssh-product").put("nonce", nonce).put("result", "running")
        var stage = "ready"
        var handle = 0L
        var frames = 0L
        var deadline = SystemClock.elapsedRealtime() + 40000
        var stopStarted = 0L
        val retired = AtomicReference<String?>(null)
        fun start() { NativeSessionHost.publishCheck(result); ui.post(this) }
        override fun run() {
            try {
                check(SystemClock.elapsedRealtime() < deadline) { "PRODUCT_SSH_TIMEOUT" }
                val current = NativeSessionHost.currentHandle()
                if (current > 0) {
                    val snapshot = JSONObject(NativeBridge.nativeSnapshot(current))
                    val runtime = snapshot.optJSONObject("runtime")
                    val targets = runtime?.getJSONObject("render")?.getJSONArray("targets")
                    val target = if (targets?.length() == 1) targets.getJSONObject(0) else null
                    val visible = runtime?.getBoolean("activity_visible") == true && runtime.optString("xr_state") in setOf("VISIBLE", "FOCUSED")
                    when (stage) {
                        "ready" -> if (visible && runtime?.getString("state") == "Ready" && target != null && target.getLong("frames") >= 3) {
                            handle = current; frames = target.getLong("frames")
                            check(runtime.getJSONObject("transport").getString("state") == "Idle")
                            NativeSessionHost.connect(); stage = "connect"
                        }
                        "connect" -> {
                            check(current == handle)
                            val transport = checkNotNull(runtime).getJSONObject("transport")
                            check(transport.getString("state") != "Failed") { "PRODUCT_SSH_FAILED" }
                            if (transport.getString("state") == "Ready" && target != null && target.getLong("frames") >= frames + 3) {
                                check(transport.getInt("credential_requests") == 1 && transport.getInt("owned_connections") == 1)
                                val probe = transport.getJSONObject("probe")
                                check(probe.getBoolean("token_matched") && probe.getBoolean("eof") && probe.getInt("exit_status") == 0)
                                result.put("transport", JSONObject(transport.toString())).put("render_frames_advanced", true)
                                    .put("product_host_path", true).put("old_handle", handle.toString())
                                stage = "restore"; stopStarted = SystemClock.elapsedRealtime()
                                val runningResult = result.toString()
                                NativeSessionHost.withStoppedRuntime({ store ->
                                    val stale = JSONObject(NativeBridge.nativeSnapshot(handle))
                                    check(stale.getInt("code") == NativeBridge.STALE && stale.getJSONObject("registry").getInt("runtimes") == 0)
                                    val stop = JSONObject().put("old_runtime_destroyed", true).put("stop_ms", SystemClock.elapsedRealtime() - stopStarted)
                                    retired.set(stop.toString())
                                    store.publishCheck(JSONObject(runningResult).put("stop", stop))
                                }, { /* 再作成後の描画はこのUI pollで照合する。 */ })
                            }
                        }
                        "restore" -> if (current > handle && visible && runtime?.getString("state") == "Ready" && target != null && target.getLong("frames") >= 3) {
                            check(runtime.getJSONObject("transport").getString("state") == "Idle")
                            check(target.getBoolean("readback"))
                            result.put("stop", JSONObject(checkNotNull(retired.get()))).put("new_handle", current.toString()).put("result", "pass")
                            NativeSessionHost.publishCheck(JSONObject(result.toString())); complete(); return
                        }
                    }
                }
                ui.postDelayed(this, 32)
            } catch (failure: Exception) {
                NativeSessionHost.disconnect()
                result.put("result", "fail").put("stage", stage).put("failure_class", failure.javaClass.simpleName)
                NativeSessionHost.publishCheck(JSONObject(result.toString())); complete()
            }
        }
    }
}
