package io.github.tinmachine.eidolon

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TermuxImeReconnectTest {
    @Test fun retryAfterShelfAttemptFinishesAndOncePerFocus() {
        val reconnect = TermuxImeReconnect<String>()
        assertFalse(reconnect.request(null, true))
        assertFalse(reconnect.request("mousepad", false))
        assertTrue(reconnect.request("mousepad", true))
        repeat(20) { assertFalse(reconnect.request("mousepad", true)) }
        assertTrue(reconnect.request("dolphin", true))
        assertFalse(reconnect.request(null, true))
        assertTrue(reconnect.request("dolphin", true))
    }

    @Test fun explicitDisconnectSurvivesFocusChangesUntilShelfIsRequestedAgain() {
        val reconnect = TermuxImeReconnect<String>()
        assertTrue(reconnect.request("mousepad", true))
        reconnect.stop()
        assertFalse(reconnect.request(null, true))
        assertFalse(reconnect.request("dolphin", true))
        reconnect.resume()
        assertTrue(reconnect.request("dolphin", true))
    }
}
