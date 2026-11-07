package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class IoWorkloadAdmissionTest {
    private fun input(sequence: Long = 1) = IoWorkloadAdmission.Input("epoch-a", sequence, "scope-a", "scope-a", "window-a", "registered", "shell_tree", true)
    private fun gate() = IoWorkloadAdmission("window-a", 1000)

    @Test fun firstPendingRegistrationDoesNotConsumeEpochOrSequence() {
        val gate = gate()
        for (state in listOf("pending", "unavailable", "registered")) {
            assertEquals(IoWorkloadAdmission.Kind.WAIT, gate.observe(input(8).copy(nativeState=state, nativeIdentity=null), 100).kind)
        }
        assertFalse(gate.adopted)
        assertEquals(0L, gate.lastSequence)
        val fresh = input(1).copy(epoch="epoch-b", expectedIdentity="scope-b", nativeIdentity="scope-b")
        val accepted = gate.observe(fresh, 200)
        assertEquals(IoWorkloadAdmission.Kind.ACCEPT, accepted.kind)
        assertTrue(accepted.fresh)
        assertEquals(1L, gate.lastSequence)
    }

    @Test fun nativeDisplayArrivalCanAdmitSameRawSequenceWithoutUsingEarlierIo() {
        val gate = gate()
        var acceptedBytes = 0.0
        var positive = false
        fun consume(value: IoWorkloadAdmission.Input, bytes: Double): IoWorkloadAdmission.Decision {
            val decision = gate.observe(value, 100)
            if (decision.kind == IoWorkloadAdmission.Kind.ACCEPT && decision.fresh) {
                acceptedBytes += bytes
                if (bytes > 0) positive = true
            }
            return decision
        }
        assertEquals(IoWorkloadAdmission.Kind.WAIT, consume(input(4).copy(nativeIdentity=null, nativeState="pending"), 1048576.0).kind)
        assertEquals(IoWorkloadAdmission.Kind.WAIT, consume(input(4).copy(displayMatches=false), 1048576.0).kind)
        assertFalse(gate.adopted)
        assertEquals(0L, gate.lastSequence)
        assertEquals(0.0, acceptedBytes, 0.0)
        assertFalse(positive)
        assertTrue(consume(input(4), 1048576.0).fresh)
        assertFalse(consume(input(4), 1048576.0).fresh)
        assertEquals(1048576.0, acceptedBytes, 0.0)
        assertTrue(positive)
        assertEquals(IoWorkloadAdmission.Kind.WAIT, consume(input(5).copy(displayMatches=false), 9999.0).kind)
        assertEquals(4L, gate.lastSequence)
        assertTrue(consume(input(5), 0.0).fresh)
        assertEquals(1048576.0, acceptedBytes, 0.0)
        assertEquals(IoWorkloadAdmission.Reason.OLDER_SEQUENCE, consume(input(4), 1.0).reason)
        assertEquals(5L, gate.lastSequence)
    }

    @Test fun unambiguousInvalidScopeFailsEvenBeforeAdoption() {
        val bad = listOf(input().copy(nativeWindow="other"), input().copy(scope="tmux_session"),
            input().copy(nativeIdentity="other"), input().copy(nativeState="identity_changed", nativeIdentity=null),
            input().copy(nativeState="ambiguous", nativeIdentity=null), input().copy(nativeState="registration_timeout", nativeIdentity=null),
            input().copy(nativeState="disconnected", nativeIdentity=null), input().copy(nativeState="untrusted", nativeIdentity=null))
        for (value in bad) {
            val gate = gate()
            assertEquals(IoWorkloadAdmission.Reason.SCOPE_CHANGED, gate.observe(value, 1).reason)
            assertFalse(gate.adopted)
            assertEquals(0L, gate.lastSequence)
            assertEquals(IoWorkloadAdmission.Kind.FAIL, gate.observe(input(), 2).kind)
        }
    }

    @Test fun adoptedIdentityLossOrChangeCannotReturnToPreparingOrRebind() {
        val bad = listOf(input(2).copy(nativeIdentity=null, nativeState="pending"),
            input(2).copy(expectedIdentity="other", nativeIdentity="other"), input(2).copy(nativeWindow="other"),
            input(2).copy(scope="tmux_session"), input(2).copy(nativeState="identity_changed"), input(2).copy(epoch="epoch-b"))
        for (value in bad) {
            val gate = gate()
            assertEquals(IoWorkloadAdmission.Kind.ACCEPT, gate.observe(input(), 1).kind)
            assertEquals(IoWorkloadAdmission.Kind.FAIL, gate.observe(value, 2).kind)
            assertEquals(1L, gate.lastSequence)
            assertEquals(IoWorkloadAdmission.Kind.FAIL, gate.observe(input(3), 3).kind)
        }
    }

    @Test fun deadlineAndInvalidSequenceCannotBeHiddenByPendingState() {
        val gate = gate()
        assertEquals(IoWorkloadAdmission.Kind.WAIT, gate.observe(input().copy(nativeState="pending", nativeIdentity=null), 999).kind)
        assertEquals(IoWorkloadAdmission.Reason.TIMEOUT, gate.observe(input(), 1000).reason)
        assertFalse(gate.adopted)
        for (sequence in listOf(0L, -1L)) {
            assertEquals(IoWorkloadAdmission.Reason.INVALID_SEQUENCE, gate().observe(input(sequence).copy(nativeIdentity=null), 1).reason)
        }
    }

    @Test fun diagnosticsContainFixedStatesAndComparisonFlagsOnly() {
        val gate = gate()
        val value = input().copy(nativeState="pending", nativeIdentity=null)
        val result = gate.metadata(value, gate.observe(value, 1))
        assertEquals("registration_pending", result["reason"])
        assertEquals(false, result["native_identity_present"])
        assertEquals(false, result["identity_matches"])
        assertEquals(true, result["window_matches"])
        assertFalse(result.toString().contains("scope-a"))
        assertFalse(result.toString().contains("window-a"))
        assertFalse(result.toString().contains("epoch-a"))
        val unknown = input().copy(nativeState="untrusted value")
        assertEquals("unknown", gate.metadata(unknown, gate.observe(unknown, 2))["native_state"])
    }
}
