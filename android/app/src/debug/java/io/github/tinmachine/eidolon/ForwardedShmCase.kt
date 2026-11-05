package io.github.tinmachine.eidolon

import android.content.Context
import android.os.SystemClock
import java.security.MessageDigest
import org.json.JSONArray
import org.json.JSONObject

/** 同じ認証済み接続で100frame producerとfootを順に起動し、実行中Stopを検査する。 */
object ForwardedShmCase {
    fun run(nonce: String, context: Context, configuration: String, store: DiagnosticsStore) {
        val result = JSONObject().put("case", "forwarded-shm").put("nonce", nonce).put("result", "running")
        val rows = JSONArray()
        var active = 0L
        var stage = "connect"
        store.publishCheck(result)
        try {
            active = NativeBridge.nativeCreate(configuration)
            check(active > 0)
            waitFor { runtime(active).getString("state") == "Ready" }
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
                val transport = runtime(active).getJSONObject("transport")
                check(transport.getString("state") != "Failed")
                transport.getString("state") == "Ready"
            }
            check(requests == 1 && session.toLong() > 0)
            result.put("session", session).put("credential_requests", requests)
            for ((index, mode) in listOf("pattern", "foot").withIndex()) {
                stage = mode
                val token = token(nonce, index)
                val request = command("app_probe", index + 2L).put("session", session).put("nonce", token).put("mode", mode)
                check(NativeBridge.nativeCommand(active, request.toString()) == NativeBridge.ACCEPTED)
                var snapshot = JSONObject()
                waitFor(60000) {
                    snapshot = runtime(active)
                    check(snapshot.getString("state") != "Failed")
                    val transport = snapshot.getJSONObject("transport")
                    check(transport.getString("state") != "Failed")
                    val app = snapshot.optJSONObject("app") ?: JSONObject()
                    val forward = transport.optJSONObject("app_forward") ?: JSONObject()
                    if (app.optString("nonce") == token) check(app.isNull("error"))
                    if (forward.optString("nonce") == token) check(forward.isNull("error"))
                    app.optString("nonce") == token && forward.optString("nonce") == token &&
                        if (mode == "pattern") app.optString("state") == "pass" && app.optInt("verified_hashes") == 100 && forward.optString("state") == "pass" &&
                            app.optInt("clients") == 0 && app.optInt("objects") == 0 && app.optInt("pool_bytes") == 0 && app.optInt("pixel_bytes") == 0
                        else app.optLong("copies") > 0 && app.optBoolean("non_uniform") && app.optJSONArray("windows")?.length() == 1 && forward.optString("state") == "running"
                }
                rows.put(JSONObject().put("cycle", index + 1).put("mode", mode).put("app", snapshot.getJSONObject("app")).put("transport", snapshot.getJSONObject("transport")))
                result.put("cycles", rows)
                store.publishCheck(result)
            }
            // enqueue受付とcoreでのSTALE判定は別。存在しないsessionへの遅延requestを確認する。
            stage = "stale"
            val stale = command("app_probe", 99).put("session", (session.toLong() + 1).toString()).put("nonce", token(nonce, 2)).put("mode", "pattern")
            check(NativeBridge.nativeCommand(active, stale.toString()) == NativeBridge.ACCEPTED)
            var rejected = false
            waitFor {
                val events = JSONObject(NativeBridge.nativePoll(active, 64)).getJSONArray("events")
                for (i in 0 until events.length()) {
                    val event = events.getJSONObject(i)
                    if (event.optString("type") == "command_complete" && event.optString("request_id") == "99") {
                        check(event.getString("code") == "STALE"); rejected = true
                    }
                }
                rejected
            }
            result.put("stale_rejected", rejected)
            stage = "stop"
            val started = SystemClock.elapsedRealtime()
            check(NativeBridge.nativeRequestStop(active) == NativeBridge.ACCEPTED)
            val accepted = SystemClock.elapsedRealtime() - started
            check(accepted < 100)
            waitFor { runtime(active).getString("state") == "Stopped" }
            val after = runtime(active)
            check(after.getJSONObject("app").getString("state") == "stopped")
            check(after.getJSONObject("app").getBoolean("directory_cleaned"))
            check(after.getJSONObject("transport").getJSONArray("pending_cleanup").length() == 0)
            waitFor { NativeBridge.nativeDestroy(active) == NativeBridge.ACCEPTED }
            val stopped = SystemClock.elapsedRealtime() - started
            check(stopped < 10000)
            result.put("last_handle", active.toString()).put("after_stop", after).put("accept_ms", accepted).put("stop_ms", stopped).put("result", "pass")
            active = 0
        } catch (failure: Exception) {
            result.put("result", "fail").put("stage", stage).put("failure_class", failure.javaClass.simpleName).put("failure_site", failure.stackTrace.firstOrNull()?.toString())
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
    private fun command(type: String, request: Long): JSONObject = JSONObject().put("schema", 1).put("type", type).put("request_id", request.toString())
    private fun runtime(handle: Long): JSONObject = JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("runtime")
    private fun waitFor(milliseconds: Long = 10000, test: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + milliseconds
        while (!test()) { check(SystemClock.elapsedRealtime() < deadline); Thread.sleep(5) }
    }
}
