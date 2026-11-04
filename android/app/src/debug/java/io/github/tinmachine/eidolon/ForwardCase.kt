package io.github.tinmachine.eidolon

import android.content.Context
import android.os.SystemClock
import org.json.JSONObject

/** standard JNIの同じSSH owner/credential経路から、固定StreamLocal echoを実行する。 */
object ForwardCase {
    fun run(nonce: String, context: Context, configuration: String, store: DiagnosticsStore) {
        val result = JSONObject().put("case", "forward").put("nonce", nonce).put("result", "running")
        var active = 0L
        var stage = "connect"
        store.publishCheck(result)
        try {
            active = NativeBridge.nativeCreate(configuration)
            waitFor { snapshot(active).getString("state") == "Ready" }
            check(NativeBridge.nativeCommand(active, command("connect", 1).put("profile_id", "termux-local").toString()) == NativeBridge.ACCEPTED)
            val credentials = CredentialStore(context)
            var requests = 0
            var session = "0"
            waitFor(30000) {
                val events = JSONObject(NativeBridge.nativePoll(active, 64)).getJSONArray("events")
                for (i in 0 until events.length()) {
                    val event = events.getJSONObject(i)
                    if (event.optString("type") == "credential_required") {
                        requests++; session = event.getString("session")
                        check(credentials.provide(active, session.toLong(), event.getString("request_id").toLong()) == NativeBridge.ACCEPTED)
                    }
                }
                val transport = snapshot(active).getJSONObject("transport")
                check(transport.getString("state") != "Failed")
                transport.getString("state") == "Ready"
            }
            check(requests == 1)
            stage = "forward"
            check(NativeBridge.nativeCommand(active, command("forward_probe", 2).put("session", session).put("nonce", nonce.take(16)).toString()) == NativeBridge.ACCEPTED)
            var report = JSONObject()
            waitFor(85000) {
                report = snapshot(active).getJSONObject("transport")
                val probe = report.optJSONObject("forward_probe")
                check(probe?.optString("state") != "fail")
                check(report.getString("state") != "Failed")
                probe?.optString("state") == "pass"
            }
            result.put("transport", report).put("credential_requests", requests)
            stage = "stop"
            val started = SystemClock.elapsedRealtime()
            check(NativeBridge.nativeRequestStop(active) == NativeBridge.ACCEPTED)
            waitFor { snapshot(active).getString("state") == "Stopped" }
            val after = snapshot(active).getJSONObject("transport")
            check(after.getInt("workers") == 0 && after.getInt("tokio_threads") == 0 && after.getInt("owned_connections") == 0 && after.getJSONObject("forward").getInt("active") == 0)
            waitFor { NativeBridge.nativeDestroy(active) == NativeBridge.ACCEPTED }
            result.put("last_handle", active.toString()).put("after_stop", after).put("stop_ms", SystemClock.elapsedRealtime() - started).put("result", "pass")
            active = 0
        } catch (failure: Exception) {
            result.put("result", "fail").put("stage", stage).put("failure_class", failure.javaClass.simpleName)
            if (active > 0) result.put("snapshot", JSONObject(NativeBridge.nativeSnapshot(active)))
        } finally {
            if (active > 0) {
                NativeBridge.nativeRequestStop(active)
                try { waitFor { NativeBridge.nativeDestroy(active) != NativeBridge.BUSY } } catch (_: Exception) { result.put("cleanup_failed", true) }
            }
            store.publishCheck(result)
        }
    }
    private fun command(type: String, request: Long): JSONObject = JSONObject().put("schema", 1).put("type", type).put("request_id", request.toString())
    private fun snapshot(handle: Long): JSONObject = JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("runtime")
    private fun waitFor(milliseconds: Long = 5000, test: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + milliseconds
        while (!test()) { check(SystemClock.elapsedRealtime() < deadline); Thread.sleep(5) }
    }
}
