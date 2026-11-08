package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class FreedoomCloseProgressTest {
    @Test fun epochBeforeAckAndAckBeforeEpochBothWaitWithinCapturedBound() {
        for(epochFirst in listOf(true,false)) {
            val gate=FreedoomCloseProgress(7);gate.queued("11",0)
            assertEquals(FreedoomCloseProgress.State.WAITING,gate.observe(1,if(epochFirst) 8 else 7,if(epochFirst) emptyMap() else mapOf("11" to "ACCEPTED"),false))
            assertEquals(FreedoomCloseProgress.State.COMPLETE,gate.observe(2,8,mapOf("11" to "ACCEPTED"),false))
            gate.queued("12",3)
            assertEquals(FreedoomCloseProgress.State.WAITING,gate.observe(4,8,mapOf("11" to "ACCEPTED","12" to "ACCEPTED"),false))
            assertEquals(FreedoomCloseProgress.State.COMPLETE,gate.observe(5,9,mapOf("11" to "ACCEPTED","12" to "ACCEPTED"),false))
        }
    }
    @Test fun oneOwnRetirementResetIsBoundedAndCannotReturnToMapped() {
        val gate=FreedoomCloseProgress(7);gate.queued("11",0)
        assertEquals(FreedoomCloseProgress.State.COMPLETE,gate.observe(1,9,mapOf("11" to "ACCEPTED"),true))
        assertEquals(FreedoomCloseProgress.State.REJECTED,gate.observe(2,9,mapOf("11" to "ACCEPTED"),false))
        val extra=FreedoomCloseProgress(7);extra.queued("11",0)
        assertEquals(FreedoomCloseProgress.State.REJECTED,extra.observe(1,10,mapOf("11" to "ACCEPTED"),true))
    }
    @Test fun extraResetReversedEpochAndRejectedAckAreTerminal() {
        for(pair in listOf(9L to "ACCEPTED",8L to "STALE")) {
            val gate=FreedoomCloseProgress(7);gate.queued("11",0)
            assertEquals(FreedoomCloseProgress.State.REJECTED,gate.observe(1,pair.first,mapOf("11" to pair.second),false))
            assertEquals(FreedoomCloseProgress.State.REJECTED,gate.observe(2,8,mapOf("11" to "ACCEPTED"),false))
        }
        val gate=FreedoomCloseProgress(7);gate.queued("11",0);gate.observe(1,8,emptyMap(),false)
        assertEquals(FreedoomCloseProgress.State.REJECTED,gate.observe(2,7,emptyMap(),false))
    }
    @Test fun finiteDeadlineCannotAdoptLateAckAndThirdCloseIsForbidden() {
        val late=FreedoomCloseProgress(7);late.queued("11",100)
        assertEquals(FreedoomCloseProgress.State.REJECTED,late.observe(6100,8,mapOf("11" to "ACCEPTED"),false))
        val gate=FreedoomCloseProgress(7);gate.queued("11",0);gate.observe(1,8,mapOf("11" to "ACCEPTED"),false)
        gate.queued("12",2);gate.observe(3,9,mapOf("11" to "ACCEPTED","12" to "ACCEPTED"),false)
        assertThrows(IllegalStateException::class.java) {gate.queued("13",4)}
    }
}
