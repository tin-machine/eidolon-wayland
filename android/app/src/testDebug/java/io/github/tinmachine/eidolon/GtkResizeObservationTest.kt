package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class GtkResizeObservationTest {
    @Test fun noReturnBeforeLargeAndExactlyOneRequestAfterObservation() {
        val gate=GtkResizeObservation()
        assertFalse(gate.requestReturn());assertFalse(gate.beginReturn("old"))
        gate.largeReady("2:5:3:6")
        assertFalse(gate.beginReturn("2:5:3:6"))
        assertEquals(GtkResizeObservation.State.LARGE_READY,gate.state)
        assertTrue(gate.requestReturn());assertFalse(gate.requestReturn())
        assertTrue(gate.beginReturn("2:5:3:6"));assertFalse(gate.beginReturn("2:5:3:6"));assertFalse(gate.requestReturn())
        gate.complete();assertEquals(GtkResizeObservation.State.COMPLETE,gate.state);assertEquals(1,gate.returnRequests)
    }
    @Test fun changedTargetAndLateRequestNeverRebindTheCapturedLarge() {
        val gate=GtkResizeObservation();gate.largeReady("2:5:3:6");gate.requestReturn()
        assertFalse(gate.beginReturn("2:5:3:7"));assertEquals(GtkResizeObservation.State.CANCELLED,gate.state)
        assertFalse(gate.requestReturn());assertFalse(gate.beginReturn("2:5:3:6"))
    }
    @Test fun cancelBeforeOrAfterHostAckNeverSchedulesReturn() {
        for(ack in listOf(false,true)) {
            val gate=GtkResizeObservation();gate.largeReady("2:5:3:6");if(ack) gate.requestReturn();gate.cancel()
            assertFalse(gate.requestReturn());assertFalse(gate.beginReturn("2:5:3:6"))
            assertEquals(if(ack) 1 else 0,gate.returnRequests)
        }
    }
    @Test fun duplicateLargeOrPrematureCompletionDoesNotResetTheOneShot() {
        val gate=GtkResizeObservation()
        assertThrows(IllegalStateException::class.java) {gate.complete()}
        gate.largeReady("2:5:3:6");gate.requestReturn()
        assertThrows(IllegalStateException::class.java) {gate.largeReady("2:5:3:8")}
        assertEquals(1,gate.returnRequests)
    }
}
