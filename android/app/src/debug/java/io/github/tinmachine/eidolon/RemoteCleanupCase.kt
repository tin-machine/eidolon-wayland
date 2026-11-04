package io.github.tinmachine.eidolon

import android.content.Context
import android.os.SystemClock
import java.security.MessageDigest
import org.json.JSONArray
import org.json.JSONObject

/** ADBはcase/nonceだけ。故障は固定enumで注入し、開発SSHやTermux全体を停止しない。 */
object RemoteCleanupCase {
    fun run(nonce: String, context: Context, configuration: String, store: DiagnosticsStore) {
        val result = JSONObject().put("case", "remote-cleanup").put("nonce", nonce).put("result", "running")
        val rows = JSONArray()
        val credentials = CredentialStore(context)
        var active = 0L
        var lastHandle = 0L
        var sequence = 0L
        var stage = "start"
        var published = 0L
        fun publish() {
            result.put("stage", stage).put("rows", rows)
            if (active > 0) result.put("active_transport", snapshot(active).getJSONObject("transport"))
            store.publishCheck(result)
            published = SystemClock.elapsedRealtime()
        }
        fun waitFor(milliseconds: Long = 10000, test: () -> Boolean) {
            val deadline = SystemClock.elapsedRealtime() + milliseconds
            while (!test()) {
                check(SystemClock.elapsedRealtime() < deadline) { "REMOTE_CLEANUP_TIMEOUT" }
                if (SystemClock.elapsedRealtime() - published >= 500) publish()
                Thread.sleep(5)
            }
        }
        fun create() {
            active = NativeBridge.nativeCreate(configuration)
            check(active > lastHandle)
            sequence = 0
            waitFor { val runtime = snapshot(active); runtime.getString("state") == "Ready" && runtime.getJSONObject("transport").getBoolean("journal_ready") }
        }
        fun command(type: String): JSONObject = JSONObject().put("schema", 1).put("type", type).put("request_id", (++sequence).toString())
        fun connect(): String {
            val request = command("connect").put("profile_id", "termux-local")
            val requestId = request.getString("request_id")
            check(NativeBridge.nativeCommand(active, request.toString()) == NativeBridge.ACCEPTED)
            var requests = 0
            var dispatched = false
            waitFor(90000) {
                val events = JSONObject(NativeBridge.nativePoll(active, 64)).getJSONArray("events")
                for (i in 0 until events.length()) {
                    val event = events.getJSONObject(i)
                    if (event.optString("type") == "command_complete" && event.optString("request_id") == requestId) {
                        check(event.getString("code") == "ACCEPTED")
                        dispatched = true
                    }
                    if (event.optString("type") == "credential_required") {
                        requests++
                        check(credentials.provide(active, event.getString("session").toLong(), event.getString("request_id").toLong()) == NativeBridge.ACCEPTED)
                    }
                }
                val transport = snapshot(active).getJSONObject("transport")
                // JNIのACCEPTEDはenqueueだけ。再接続ではdispatch前の旧Failedを読める。
                if (dispatched) check(transport.getString("state") != "Failed")
                dispatched && transport.getString("state") == "Ready"
            }
            check(requests == 1)
            val transport = snapshot(active).getJSONObject("transport")
            check(transport.getJSONArray("pending_cleanup").length() == 0)
            return transport.getString("session")
        }
        fun stopAndDestroy(): JSONObject {
            val started = SystemClock.elapsedRealtime()
            val acceptedAt = SystemClock.elapsedRealtime()
            check(NativeBridge.nativeRequestStop(active) == NativeBridge.ACCEPTED)
            val acceptedMs = SystemClock.elapsedRealtime() - acceptedAt
            check(acceptedMs < 100)
            check(snapshot(active).getString("state") in setOf("Stopping", "Stopped"))
            waitFor { snapshot(active).getString("state") == "Stopped" }
            val after = snapshot(active).getJSONObject("transport")
            check(after.getInt("workers") == 0 && after.getInt("tokio_threads") == 0 && after.getInt("owned_connections") == 0 && after.getJSONObject("forward").getInt("active") == 0)
            waitFor { NativeBridge.nativeDestroy(active) == NativeBridge.ACCEPTED }
            val stopped = JSONObject().put("after", after).put("handle", active.toString()).put("accept_ms", acceptedMs).put("stop_ms", SystemClock.elapsedRealtime() - started)
            check(stopped.getLong("stop_ms") < 10000)
            lastHandle = active; active = 0
            return stopped
        }
        publish()
        try {
            for (mode in listOf("cancel_before_exec", "prepare_eof", "stop", "control_eof", "ssh_drop", "ssh_drop_recreate", "lease_expire")) {
                stage = mode
                create()
                val firstHandle = active
                val session = connect()
                val remoteNonce = MessageDigest.getInstance("SHA-256").digest("$nonce:$mode".toByteArray()).take(8).joinToString("") { "%02x".format(it.toInt() and 0xff) }
                val scenario = if (mode == "ssh_drop_recreate") "ssh_drop" else mode
                check(NativeBridge.nativeCommand(active, command("remote_cleanup_probe").put("session", session).put("nonce", remoteNonce).put("scenario", scenario).toString()) == NativeBridge.ACCEPTED)
                val row = JSONObject().put("mode", mode).put("scenario", scenario).put("nonce", remoteNonce).put("first_handle", firstHandle.toString()).put("first_session", session)
                if (mode == "stop") {
                    waitFor(20000) {
                        val transport = snapshot(active).getJSONObject("transport")
                        check(transport.getString("state") != "Failed")
                        transport.optJSONObject("forward_probe")?.optJSONObject("remote")?.optString("phase") == "running" && transport.getJSONObject("forward").getInt("active") > 0
                    }
                    val stopped = stopAndDestroy()
                    val after = stopped.getJSONObject("after")
                    val report = after.getJSONObject("forward_probe")
                    check(report.getBoolean("remote_cleaned") && after.getJSONArray("pending_cleanup").length() == 0)
                    check(report.getJSONObject("remote").getJSONObject("finished").getString("reason") == "Stop")
                    row.put("observed_live", true).put("report", report).put("stop", stopped)
                } else if (scenario == "ssh_drop") {
                    waitFor(30000) {
                        val transport = snapshot(active).getJSONObject("transport")
                        transport.getString("state") == "Failed" && transport.getInt("owned_connections") == 0 && transport.getInt("tokio_threads") == 0
                    }
                    val lost = snapshot(active).getJSONObject("transport")
                    check(lost.getString("error") == "SSH_DROPPED")
                    check(lost.getJSONArray("pending_cleanup").length() == 1)
                    val report = lost.getJSONObject("forward_probe")
                    check(!report.getBoolean("remote_cleaned") && report.getJSONObject("remote").getBoolean("cleanup_pending"))
                    row.put("report", report).put("after_loss", lost)
                    if (mode == "ssh_drop_recreate") {
                        row.put("old_stop", stopAndDestroy())
                        create()
                        check(snapshot(active).getJSONObject("transport").getJSONArray("pending_cleanup").length() == 1)
                    }
                    val nextSession = connect()
                    check(nextSession.toLong() > session.toLong())
                    val recovered = snapshot(active).getJSONObject("transport")
                    val audits = recovered.getJSONArray("cleanup_audits")
                    check(audits.length() == 1 && audits.getJSONObject(0).getString("nonce") == remoteNonce && audits.getJSONObject(0).getBoolean("verified"))
                    val auditedChild = audits.getJSONObject(0).getJSONObject("child")
                    val expectedChild = report.getJSONObject("remote").getJSONObject("child")
                    check(auditedChild.getInt("pid") == expectedChild.getInt("pid") && auditedChild.getString("start") == expectedChild.getString("start") && auditedChild.getInt("group") == expectedChild.getInt("group"))
                    row.put("next_session", nextSession).put("next_handle", active.toString()).put("recovered", recovered).put("stop", stopAndDestroy())
                } else {
                    waitFor(if (mode == "lease_expire") 90000 else 30000) {
                        val transport = snapshot(active).getJSONObject("transport")
                        check(transport.getString("state") != "Failed")
                        transport.optJSONObject("forward_probe")?.optString("state") == "pass"
                    }
                    val report = snapshot(active).getJSONObject("transport").getJSONObject("forward_probe")
                    if (mode == "cancel_before_exec") check(report.getBoolean("remote_not_started") && report.getJSONArray("pending_cleanup").length() == 0)
                    else {
                        val finished = report.getJSONObject("remote").getJSONObject("finished")
                        check(finished.getString("reason") == if (mode == "lease_expire") "LeaseExpired" else "EOF")
                        check(report.getBoolean("remote_cleaned"))
                    }
                    if (mode == "lease_expire") check(report.getLong("elapsed_ms") >= 59000)
                    row.put("report", report).put("stop", stopAndDestroy())
                }
                row.put("result", "pass"); rows.put(row); publish()
            }
            result.remove("active_transport")
            result.put("last_handle", lastHandle.toString()).put("result", "pass")
        } catch (failure: Exception) {
            result.put("result", "fail").put("stage", stage).put("failure_class", failure.javaClass.simpleName)
                .put("failure_site", failure.stackTrace.firstOrNull()?.toString())
            if (active > 0) result.put("snapshot", JSONObject(NativeBridge.nativeSnapshot(active)))
        } finally {
            if (active > 0) {
                NativeBridge.nativeRequestStop(active)
                try { waitFor { NativeBridge.nativeDestroy(active) != NativeBridge.BUSY } } catch (_: Exception) { result.put("cleanup_failed", true) }
            }
            store.publishCheck(result)
        }
    }
    private fun snapshot(handle: Long): JSONObject = JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("runtime")
}
