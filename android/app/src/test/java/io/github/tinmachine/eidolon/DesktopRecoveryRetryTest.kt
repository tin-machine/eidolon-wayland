package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class DesktopRecoveryRetryTest {
    @Test fun aReturningHostCanRecoverImmediatelyAndRetriesAreBounded() {
        val retry = DesktopRecoveryRetry()
        assertTrue(retry.admit(true, 0))
        assertFalse(retry.admit(true, 999))
        assertTrue(retry.admit(true, 1000))
        assertTrue(retry.admit(false, 1001))
        assertFalse(retry.admit(false, 1002))
        var now = 3000L
        for (delay in listOf(4000L, 8000L, 16000L, 32000L, 60000L, 60000L)) {
            assertTrue(retry.admit(true, now))
            now += delay
            assertFalse(retry.admit(true, now - 1))
        }
        retry.reset()
        assertTrue(retry.admit(true, now - 1))
    }
}
