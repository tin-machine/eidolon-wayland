package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class RuntimeOriginStopTest {
    @Test fun acceptedConnectWithoutSessionIsStillCancelledByOriginalRequest() {
        val stop = RuntimeOriginStop()
        val issued = mutableListOf<Pair<RuntimeOriginStop.Target, String>>()
        var sequence = 10
        fun step(session: String?) = stop.step(7, "4", session, { (++sequence).toString() }) { target, request ->
            issued += target to request; 0
        }
        assertFalse(step(null))
        assertEquals(RuntimeOriginStop.Target(7, "cancel_connect", "connect_request", "4"), issued.single().first)
        assertFalse(step("8"))
        assertEquals(1, issued.size)
        stop.complete(7, issued.single().second, "ACCEPTED")
        assertTrue(step("8"))
        assertEquals(1, issued.size)
    }

    @Test fun staleCancellationFallsBackToCapturedSessionAndNeverCountsAsSuccess() {
        val stop = RuntimeOriginStop()
        var sequence = 10
        val issued = mutableListOf<Pair<RuntimeOriginStop.Target, String>>()
        fun step(session: String?) = stop.step(7, "4", session, { (++sequence).toString() }) { target, request ->
            issued += target to request; 0
        }
        assertFalse(step(null))
        stop.complete(7, issued[0].second, "STALE")
        assertFalse(step(null))
        assertFalse(step("8"))
        assertEquals(RuntimeOriginStop.Target(7, "disconnect", "session", "8"), issued[1].first)
        stop.complete(7, issued[0].second, "ACCEPTED") // old command cannot acknowledge the fallback
        assertFalse(step("8"))
        stop.complete(7, issued[1].second, "ACCEPTED")
        assertTrue(step("8"))
    }

    @Test fun newRecoveryConnectAndNewRuntimeRequireTheirOwnCompletion() {
        val stop = RuntimeOriginStop()
        var sequence = 10
        var latest = ""
        fun step(runtime: Long, connect: String) = stop.step(runtime, connect, null, { (++sequence).toString() }) { _, request ->
            latest = request; 0
        }
        assertFalse(step(7, "4")); stop.complete(7, latest, "ACCEPTED"); assertTrue(step(7, "4"))
        assertFalse(step(7, "5"))
        stop.complete(6, latest, "ACCEPTED"); assertFalse(step(7, "5"))
        stop.complete(7, latest, "ACCEPTED"); assertTrue(step(7, "5"))
        assertFalse(step(9, "5"))
        stop.reset()
        assertFalse(step(9, "5"))
    }

    @Test fun queueBusyAndCommandBusyRetryButMissingTargetDoesNotSucceed() {
        val stop = RuntimeOriginStop()
        var calls = 0
        var request = ""
        assertFalse(stop.step(7, null, null, { error("must not enqueue") }) { _, _ -> error("must not enqueue") })
        fun step(queueCode: Int) = stop.step(7, null, "8", { (++calls).toString() }) { _, value -> request = value; queueCode }
        assertFalse(step(2)); assertFalse(step(0)); assertEquals(2, calls)
        stop.complete(7, request, "BUSY")
        assertFalse(step(0)); assertEquals(3, calls)
        stop.complete(7, request, "ACCEPTED")
        assertTrue(step(0)); assertEquals(3, calls)
    }
}
