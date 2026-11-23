package io.github.tinmachine.eidolon

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
    }
}
