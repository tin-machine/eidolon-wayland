package io.github.tinmachine.eidolon

import android.os.Debug
import android.os.SystemClock
import java.io.File
import java.security.MessageDigest
import org.json.JSONArray
import org.json.JSONObject

/** 固定primitiveだけをstandard JNIで実行する。任意path/FD/Wayland messageをADBから受けない。 */
object NativeDepsCase {
    fun run(nonce: String, configuration: String, store: DiagnosticsStore) {
        val result = JSONObject().put("case", "native-deps").put("nonce", nonce).put("result", "running")
        val rows = JSONArray()
        var active = 0L
        store.publishCheck(result)
        try {
            active = NativeBridge.nativeCreate(configuration)
            check(active > 0)
            waitFor { runtime(active).getString("state") == "Ready" }
            repeat(10) { index ->
                val token = MessageDigest.getInstance("SHA-256").digest("$nonce:$index".toByteArray()).take(8).joinToString("") { "%02x".format(it.toInt() and 0xff) }
                val command = JSONObject().put("schema", 1).put("type", "native_deps_probe").put("request_id", (index + 1).toString()).put("nonce", token)
                check(NativeBridge.nativeCommand(active, command.toString()) == NativeBridge.ACCEPTED)
                var report = JSONObject()
                waitFor {
                    val snapshot = runtime(active)
                    check(snapshot.getString("state") != "Failed")
                    report = snapshot.optJSONObject("native_deps") ?: JSONObject()
                    if (report.optString("nonce") == token) check(report.optString("state") != "fail")
                    report.optString("nonce") == token && report.optString("state") == "pass" && snapshot.getInt("dependency_workers") == 0
                }
                rows.put(JSONObject().put("cycle", index + 1).put("report", report)
                    .put("fds", File("/proc/self/fd").list()?.size ?: -1).put("native_heap_bytes", Debug.getNativeHeapAllocatedSize()))
                result.put("cycles", rows)
                store.publishCheck(result)
            }
            val started = SystemClock.elapsedRealtime()
            check(NativeBridge.nativeRequestStop(active) == NativeBridge.ACCEPTED)
            val accepted = SystemClock.elapsedRealtime() - started
            check(accepted < 100)
            waitFor { runtime(active).getString("state") == "Stopped" }
            val after = runtime(active)
            check(after.getInt("dependency_workers") == 0 && after.getInt("helper_workers") == 0)
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
    private fun runtime(handle: Long): JSONObject = JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("runtime")
    private fun waitFor(milliseconds: Long = 10000, test: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + milliseconds
        while (!test()) { check(SystemClock.elapsedRealtime() < deadline); Thread.sleep(5) }
    }
}
