package io.github.tinmachine.eidolon

import android.os.Debug
import android.os.SystemClock
import java.io.File
import java.security.MessageDigest
import org.json.JSONArray
import org.json.JSONObject

/** APK同梱helperの固定version/client argvだけを実行する。SHM転送はW05で別判定する。 */
object WaypipeCase {
    fun run(nonce: String, configuration: String, store: DiagnosticsStore) {
        val result = JSONObject().put("case", "waypipe").put("nonce", nonce).put("result", "running")
        val rows = JSONArray()
        var active = 0L
        store.publishCheck(result)
        try {
            active = NativeBridge.nativeCreate(configuration)
            check(active > 0)
            waitFor { runtime(active).getString("state") == "Ready" }
            repeat(3) { index ->
                val token = token(nonce, index)
                check(NativeBridge.nativeCommand(active, command(token, index + 1)) == NativeBridge.ACCEPTED)
                var report = JSONObject()
                waitFor {
                    val snapshot = runtime(active)
                    check(snapshot.getString("state") != "Failed")
                    report = snapshot.optJSONObject("helper") ?: JSONObject()
                    if (report.optJSONObject("waypipe")?.optString("nonce") == token) check(report.isNull("error"))
                    report.optJSONObject("waypipe")?.optString("nonce") == token && report.optInt("cycles") == 3 && snapshot.getInt("helper_workers") == 0
                }
                check(!report.getBoolean("cancelled") && report.getBoolean("directory_removed"))
                rows.put(JSONObject().put("cycle", index + 1).put("report", report)
                    .put("fds", File("/proc/self/fd").list()?.size ?: -1).put("native_heap_bytes", Debug.getNativeHeapAllocatedSize()))
                result.put("cycles", rows)
                store.publishCheck(result)
            }
            val cancelledToken = token(nonce, 3)
            check(NativeBridge.nativeCommand(active, command(cancelledToken, 4)) == NativeBridge.ACCEPTED)
            waitFor {
                val report = runtime(active).optJSONObject("helper") ?: JSONObject()
                report.optJSONObject("waypipe")?.optString("nonce") == cancelledToken && report.optInt("owned_children") == 1
            }
            val started = SystemClock.elapsedRealtime()
            check(NativeBridge.nativeRequestStop(active) == NativeBridge.ACCEPTED)
            val accepted = SystemClock.elapsedRealtime() - started
            check(accepted < 100)
            waitFor { runtime(active).getString("state") == "Stopped" }
            val after = runtime(active)
            val cancelled = after.getJSONObject("helper")
            check(cancelled.getBoolean("cancelled") && cancelled.isNull("error") && cancelled.getBoolean("directory_removed"))
            check(after.getInt("helper_workers") == 0 && after.getInt("dependency_workers") == 0 && after.getInt("compositor_workers") == 0)
            check(after.getJSONObject("render").getInt("workers") == 0 && after.getJSONObject("transport").getInt("workers") == 0)
            waitFor { NativeBridge.nativeDestroy(active) == NativeBridge.ACCEPTED }
            val stopped = SystemClock.elapsedRealtime() - started
            check(stopped < 10000)
            result.put("last_handle", active.toString()).put("after_stop", after).put("accept_ms", accepted).put("stop_ms", stopped).put("result", "pass")
            active = 0
        } catch (failure: Exception) {
            result.put("result", "fail").put("failure_class", failure.javaClass.simpleName).put("failure_site", failure.stackTrace.firstOrNull()?.toString())
            if (active > 0) result.put("snapshot", JSONObject(NativeBridge.nativeSnapshot(active)))
        } finally {
            if (active > 0) {
                NativeBridge.nativeRequestStop(active)
                try { waitFor { NativeBridge.nativeDestroy(active) != NativeBridge.BUSY } } catch (_: Exception) { result.put("cleanup_failed", true) }
            }
            store.publishCheck(result)
        }
    }
    private fun token(nonce: String, index: Int): String = MessageDigest.getInstance("SHA-256").digest("$nonce:$index".toByteArray()).take(8).joinToString("") { "%02x".format(it.toInt() and 0xff) }
    private fun command(token: String, request: Int): String = JSONObject().put("schema", 1).put("type", "waypipe_probe").put("request_id", request.toString()).put("nonce", token).toString()
    private fun runtime(handle: Long): JSONObject = JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("runtime")
    private fun waitFor(test: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 10000
        while (!test()) { check(SystemClock.elapsedRealtime() < deadline); Thread.sleep(5) }
    }
}
