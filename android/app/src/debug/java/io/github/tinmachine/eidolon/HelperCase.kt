package io.github.tinmachine.eidolon

import android.os.SystemClock
import android.util.Log
import java.io.File
import org.json.JSONObject

internal object HelperCase {
    fun run(nonce: String, config: String, store: DiagnosticsStore) {
        val result = JSONObject().put("case", "helper").put("nonce", nonce).put("result", "running")
        var handle = 0L
        store.publishCheck(result)
        try {
            handle = NativeBridge.nativeCreate(config)
            waitFor { snapshot(handle).getString("state") == "Ready" }
            fun command(request: String, token: String): String = JSONObject().put("schema", 1).put("request_id", request)
                .put("type", "helper_probe").put("nonce", token).toString()
            check(NativeBridge.nativeCommand(handle, command("1", nonce.take(16))) == NativeBridge.ACCEPTED)
            waitFor {
                NativeBridge.nativePoll(handle, 64)
                val state = snapshot(handle)
                val report = state.optJSONObject("helper")
                if (state.getInt("helper_workers") == 0 && report != null) {
                    check(report.isNull("error") && !report.getBoolean("cancelled"))
                    check(report.getInt("cycles") == 10 && report.getInt("owned_children") == 0)
                    check(report.getBoolean("directory_removed") && report.getInt("exit_code") == 0)
                    check(report.getInt("uid") == android.os.Process.myUid())
                    check(report.getString("context") == File("/proc/self/attr/current").readText().trimEnd('\u0000').trim())
                    result.put("helper", report)
                    true
                } else false
            }
            // 同じowner上で別nonceを使い、実行中のstopでも回収できることを確認する。
            val alternate = nonce.take(15) + if (nonce[15] == '0') "1" else "0"
            check(NativeBridge.nativeCommand(handle, command("2", alternate)) == NativeBridge.ACCEPTED)
            waitFor { snapshot(handle).getInt("helper_workers") == 1 }
            val started = SystemClock.elapsedRealtime()
            check(NativeBridge.nativeRequestStop(handle) == NativeBridge.ACCEPTED)
            waitFor { snapshot(handle).getString("state") == "Stopped" }
            val stopped = snapshot(handle)
            check(stopped.getInt("helper_workers") == 0)
            check(stopped.getJSONObject("helper").getInt("owned_children") == 0)
            check(stopped.getJSONObject("helper").getBoolean("directory_removed"))
            result.put("stop_cleanup", true).put("stop_ms", SystemClock.elapsedRealtime() - started)
            result.put("stopped_handle", handle.toString())
            waitFor { NativeBridge.nativeDestroy(handle) == NativeBridge.ACCEPTED }
            check(JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("registry").getInt("runtimes") == 0)
            handle = 0
            result.put("result", "pass")
        } catch (failure: Exception) {
            result.put("result", "fail").put("failure_class", failure.javaClass.simpleName)
        } finally {
            if (handle != 0L) {
                NativeBridge.nativeRequestStop(handle)
                try { waitFor { NativeBridge.nativeDestroy(handle) != NativeBridge.BUSY } }
                catch (_: Exception) { result.put("cleanup_failed", true) }
            }
            store.publishCheck(result)
            Log.i("EidolonCheck", "check_finished case=helper result=${result.getString("result")}")
        }
    }
    private fun snapshot(handle: Long): JSONObject = JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("runtime")
    private fun waitFor(condition: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 10000
        while (!condition()) { check(SystemClock.elapsedRealtime() < deadline) { "HELPER_TIMEOUT" }; Thread.sleep(2) }
    }
}
