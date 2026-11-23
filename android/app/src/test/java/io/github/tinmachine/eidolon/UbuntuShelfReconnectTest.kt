package io.github.tinmachine.eidolon

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class UbuntuShelfReconnectTest {
    private val history = mutableMapOf<String, String>()
    private fun policy() = UbuntuShelfReconnect(history::get) { host, reason ->
        if (reason == null) history.remove(host) else history[host] = reason
    }

    @Test fun temporaryFailureSurvivesRestartAndSuccessfulRefreshClearsSavedReason() {
        policy().apply {
            bind("ubuntu")
            observe("CONNECT_TIMEOUT", false)
        }
        val retry = policy()
        retry.bind("ubuntu")
        retry.request(10)
        assertTrue(retry.notice(true, false)!!.contains("応答待ち"))
        assertFalse(retry.retryDue(2009))
        assertTrue(retry.retryDue(2010))
        retry.request(2010, automatic = true)
        assertFalse(retry.retryDue(6009))
        assertTrue(retry.retryDue(6010))
        retry.observe(null, true)
        assertFalse(retry.retryDue(9000))
        assertTrue(retry.notice(false, false)!!.contains("更新しました"))
        assertTrue(history.isEmpty())
        retry.observe(null, false, disconnected = true)
        assertTrue(retry.retryDue(9000))
    }

    @Test fun cleanupAndTransportLossReportTheSameActionTheWorkerWillTake() {
        val retry = policy()
        retry.bind("ubuntu")
        retry.request(0)
        retry.observe("DESKTOP_CLEANUP_PENDING", false)
        assertTrue(retry.retryDue(2000))
        assertTrue(retry.notice(false, false)!!.contains("自動で再接続"))
        val report = JSONObject().put("session","31").put("error","DESKTOP_HELPER_LOST")
        val tx = JSONObject().put("session","31").put("desktop_v3_transport", report)
        retry.observe(UbuntuShelfReconnect.transportFailure(tx), false)
        retry.observe("DESKTOP_CLEANUP_PENDING", false)
        assertTrue(retry.notice(false, false)!!.contains("転送処理"))
        tx.put("session", "32")
        assertNull(UbuntuShelfReconnect.transportFailure(tx))
    }

    @Test fun reportsOnlyTheAppFailureThatEndedThisHostSession() {
        val report = JSONObject().put("session", "11").put("launch_request", "5")
        val tx = JSONObject().put("session", "11").put("desktop_v3_transport", report)
        val app = JSONObject().put("session", "11")
        val runtime = JSONObject().put("app", app).put("desktop_v3_presentation",
            JSONObject().put("state", "pending").put("first_failure", "APP_LAUNCH_TIMEOUT"))
        val retry = policy()
        retry.bind("ubuntu")
        retry.request(0)
        retry.observe(UbuntuShelfReconnect.appExitReason(runtime, tx), false)
        retry.observe("DESKTOP_CLEANUP_PENDING", false)
        assertTrue(retry.notice(false, true)!!.contains("Wayland"))
        assertTrue(retry.retryDue(2000))
        report.remove("launch_request")
        assertNull(UbuntuShelfReconnect.appExitReason(runtime, tx))
        report.put("launch_request", "5")
        app.put("session", "10")
        assertNull(UbuntuShelfReconnect.appExitReason(runtime, tx))
    }

    @Test fun explicitStopHostChangeAndAuthenticationFailureDoNotRetry() {
        val retry = policy()
        retry.bind("a")
        retry.request(0)
        retry.observe("HOST_KEY_MISMATCH", false)
        assertFalse(retry.retryDue(60000))
        assertTrue(retry.notice(false, false)!!.contains("ホスト鍵"))
        retry.observe("DESKTOP_CLEANUP_PENDING", false)
        assertEquals("HOST_KEY_MISMATCH", history["a"])
        retry.bind("b")
        retry.request(0)
        assertNull(retry.notice(false, false))
        retry.observe("SSH_IO_FAILED", false)
        retry.stop()
        assertFalse(retry.retryDue(60000))
        assertNull(retry.notice(true, false))
        retry.observe("DESKTOP_CLEANUP_PENDING", false)
        assertEquals("SSH_IO_FAILED", history["b"])
    }
}
