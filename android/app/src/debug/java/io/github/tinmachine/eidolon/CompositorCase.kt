package io.github.tinmachine.eidolon

import android.os.Debug
import android.os.SystemClock
import java.io.File
import java.security.MessageDigest
import org.json.JSONArray
import org.json.JSONObject

/** SIGBUS試験は独立instrumentation起動で実行する。成功後も同Displayへ正常clientを接続する。 */
object CompositorCase {
    fun run(name: String, nonce: String, configuration: String, store: DiagnosticsStore) {
        val result = JSONObject().put("case", name).put("nonce", nonce).put("result", "running")
        val rows = JSONArray()
        var active = 0L
        store.publishCheck(result)
        try {
            active = NativeBridge.nativeCreate(configuration)
            check(active > 0)
            waitFor { runtime(active).getString("state") == "Ready" }
            val scenarios = when (name) {
                "popup-clipboard" -> listOf("popup", "clipboard")
                "resize-wire" -> listOf("logical_resize")
                "pointer-wire" -> listOf("pointer")
                "keyboard-wire" -> listOf("keyboard_us", "keyboard_jis")
                "ime-wire" -> listOf("text_input")
                "shm-truncate" -> listOf("truncate")
                "wayland-state" -> listOf("lifecycle", "transforms", "frame_gates", "tree", "bad_serial", "stale_configure", "premature_buffer", "unconfigured_remap", "bad_scale", "positioner_overflow")
                else -> listOf("pattern", "shared_cache", "invalid_dimensions", "invalid_stride", "invalid_offset", "invalid_format", "negative_resize", "oversized_pool", "pool_limit")
            }
            scenarios.forEachIndexed { index, scenario ->
                val token = MessageDigest.getInstance("SHA-256").digest("$nonce:$index".toByteArray()).take(8).joinToString("") { "%02x".format(it.toInt() and 0xff) }
                val request = (index + 1).toString()
                val command = JSONObject().put("schema", 1).put("type", "compositor_probe").put("request_id", request).put("nonce", token).put("scenario", scenario)
                check(NativeBridge.nativeCommand(active, command.toString()) == NativeBridge.ACCEPTED)
                var complete = false
                var report = JSONObject()
                waitFor(60000) {
                    val snapshot = runtime(active)
                    check(snapshot.getString("state") != "Failed")
                    val events = JSONObject(NativeBridge.nativePoll(active, 64)).getJSONArray("events")
                    for (i in 0 until events.length()) {
                        val event = events.getJSONObject(i)
                        if (event.optString("type") == "command_complete" && event.optString("request_id") == request) {
                            complete = true
                        }
                    }
                    report = snapshot.optJSONObject("compositor") ?: JSONObject()
                    complete && report.optString("nonce") == token && snapshot.getInt("compositor_workers") == 0
                }
                rows.put(JSONObject().put("cycle", index + 1).put("scenario", scenario).put("report", report)
                    .put("fds", File("/proc/self/fd").list()?.size ?: -1).put("native_heap_bytes", Debug.getNativeHeapAllocatedSize()))
                result.put("cycles", rows)
                store.publishCheck(result)
                check(report.getString("state") == "pass")
            }
            val started = SystemClock.elapsedRealtime()
            check(NativeBridge.nativeRequestStop(active) == NativeBridge.ACCEPTED)
            val accepted = SystemClock.elapsedRealtime() - started
            check(accepted < 100)
            waitFor { runtime(active).getString("state") == "Stopped" }
            val after = runtime(active)
            check(after.getInt("compositor_workers") == 0 && after.getInt("dependency_workers") == 0 && after.getInt("helper_workers") == 0)
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
