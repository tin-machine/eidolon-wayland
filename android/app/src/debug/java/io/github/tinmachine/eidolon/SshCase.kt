package io.github.tinmachine.eidolon

import android.content.Context
import android.os.SystemClock
import android.os.Process
import java.io.File
import org.json.JSONObject
import org.json.JSONArray

/** standard JNIを通す実APK→Termux検証。変更pin/別鍵はin-memoryだけに使う。 */
object SshCase {
    fun run(nonce: String, context: Context, configuration: String, store: DiagnosticsStore) {
        val result = JSONObject().put("case", "ssh").put("nonce", nonce).put("result", "running")
        val cases = JSONArray()
        val credentials = CredentialStore(context)
        val profileFile = File(context.noBackupFilesDir, "credentials/profile.json")
        var active = 0L
        var previous = 0L
        var stage = "profile"
        store.publishCheck(result)
        try {
            val before = profileFile.readBytes()
            for (mode in listOf("normal", "mismatch", "auth_failed", "cancel_credential")) {
                stage = mode
                val config = JSONObject(configuration)
                check(config.getJSONArray("profiles").length() == 1) { "PAIR_REQUIRED" }
                if (mode == "mismatch") {
                    val plaintext = NativeBridge.nativeGenerateIdentity()
                    val otherPublic = try { NativeBridge.nativePublicKey(plaintext) } finally { plaintext.fill(0) }
                    config.getJSONArray("profiles").getJSONObject(0).put("host_key", otherPublic)
                }
                active = NativeBridge.nativeCreate(config.toString())
                check(active > previous)
                waitFor { snapshot(active).getString("state") == "Ready" }
                check(NativeBridge.nativeCommand(active, command("connect", 1).put("profile_id", "termux-local").toString()) == NativeBridge.ACCEPTED)
                var requests = 0
                var provided = 0
                var staleComplete = false
                var session = "0"
                var report = JSONObject()
                val deadline = SystemClock.elapsedRealtime() + 30000
                while (true) {
                    check(SystemClock.elapsedRealtime() < deadline) { "SSH_TIMEOUT" }
                    val events = JSONObject(NativeBridge.nativePoll(active, 64)).getJSONArray("events")
                    var cancel = false
                    for (i in 0 until events.length()) {
                        val event = events.getJSONObject(i)
                        if (event.optString("type") == "credential_required") {
                            requests++; session = event.getString("session")
                            check(event.getString("profile_id") == "termux-local")
                            if (mode == "cancel_credential") { cancel = true; break }
                            if (mode == "normal") {
                                val old = command("disconnect", 2).put("session", (session.toLong() + 999).toString())
                                check(NativeBridge.nativeCommand(active, old.toString()) == NativeBridge.ACCEPTED)
                            }
                            val request = event.getString("request_id").toLong()
                            val code = if (mode == "auth_failed") {
                                val plaintext = NativeBridge.nativeGenerateIdentity()
                                try { NativeBridge.nativeProvideCredential(active, session.toLong(), request, plaintext) } finally { plaintext.fill(0) }
                            } else credentials.provide(active, session.toLong(), request)
                            check(code == NativeBridge.ACCEPTED)
                            provided++
                        }
                        if (event.optString("type") == "command_complete" && event.optString("request_id") == "2") {
                            check(event.getString("code") == "STALE"); staleComplete = true
                        }
                    }
                    report = snapshot(active).getJSONObject("transport")
                    if (cancel) break
                    val state = report.getString("state")
                    if (state == "Ready") {
                        check(mode == "normal" && requests == 1)
                        if (!staleComplete) { Thread.sleep(5); continue }
                        val probe = report.getJSONObject("probe")
                        check(probe.getBoolean("token_matched") && probe.getBoolean("eof") && probe.getInt("exit_status") == 0 && !probe.getBoolean("exit_signal"))
                        check(probe.getInt("uid") != Process.myUid())
                        check(NativeBridge.nativeCommand(active, command("disconnect", 3).put("session", session).toString()) == NativeBridge.ACCEPTED)
                        waitFor {
                            val value = snapshot(active).getJSONObject("transport")
                            value.getString("state") == "Disconnected" && value.getInt("owned_connections") == 0 && value.getInt("tokio_threads") == 0
                        }
                        break
                    }
                    if (state == "Failed") {
                        check(mode in setOf("mismatch", "auth_failed"))
                        check(report.getString("error") == if (mode == "mismatch") "HOST_KEY_MISMATCH" else "AUTH_FAILED")
                        check(requests == if (mode == "mismatch") 0 else 1)
                        check(report.getInt("owned_connections") == 0 && report.getInt("tokio_threads") == 0)
                        break
                    }
                    Thread.sleep(5)
                }
                val stopStarted = SystemClock.elapsedRealtime()
                check(NativeBridge.nativeRequestStop(active) == NativeBridge.ACCEPTED)
                waitFor { snapshot(active).getString("state") == "Stopped" }
                val after = snapshot(active).getJSONObject("transport")
                check(after.getInt("owned_connections") == 0 && after.getInt("workers") == 0 && after.getInt("tokio_threads") == 0)
                waitFor { NativeBridge.nativeDestroy(active) == NativeBridge.ACCEPTED }
                cases.put(JSONObject().put("mode", mode).put("handle", active.toString()).put("result", "pass").put("transport", report)
                    .put("credential_requests", requests).put("stale_disconnect_safe", staleComplete)
                    .put("credentials_provided", provided)
                    .put("stop_ms", SystemClock.elapsedRealtime() - stopStarted).put("after_stop", after))
                previous = active; active = 0
                result.put("cases", cases); store.publishCheck(result)
            }
            check(before.contentEquals(profileFile.readBytes()))
            result.put("profile_unchanged", true).put("last_handle", previous.toString()).put("result", "pass")
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
    private fun waitFor(test: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 5000
        while (!test()) { check(SystemClock.elapsedRealtime() < deadline); Thread.sleep(2) }
    }
}
