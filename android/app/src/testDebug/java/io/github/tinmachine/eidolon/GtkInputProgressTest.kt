package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class GtkInputProgressTest {
    private fun sample(k:Long=10,b:Long=20,kp:Int=0,bp:Int=0)=GtkInputProgress.Observed(k,b,kp,bp)
    @Test fun focusAndAckMayPrecedeButtonUpAndFourKeyBurstPublication() {
        val g=GtkInputProgress(10,20,false)
        assertEquals(GtkInputProgress.State.WAITING,g.observe("WAIT_FOCUS",0,sample()))
        assertEquals(GtkInputProgress.State.WAITING,g.observe("WAIT_FOCUS",5,sample(b=21,bp=1)))
        assertEquals(GtkInputProgress.State.COMPLETE,g.observe("WAIT_FOCUS",6,sample(b=22)))
        for(i in 0L..3) assertEquals(GtkInputProgress.State.WAITING,g.observe("WAIT_KEYS",10+i,sample(k=10+i,b=22,kp=(i%2).toInt())))
        assertEquals(GtkInputProgress.State.COMPLETE,g.observe("WAIT_KEYS",15,sample(k=14,b=22)))
        assertEquals(GtkInputProgress.State.WAITING,g.observe("WAIT_CLICK",16,sample(k=14,b=23,bp=1)))
        assertEquals(GtkInputProgress.State.COMPLETE,g.observe("WAIT_CLICK",17,sample(k=14,b=24)))
    }
    @Test fun extraOrRegressedInputIsTerminalAndNeverRebased() {
        for(value in listOf(sample(k=11),sample(b=23),sample(b=19),sample(k=9))) {
            val g=GtkInputProgress(10,20,false)
            assertEquals(GtkInputProgress.State.REJECTED,g.observe("WAIT_FOCUS",1,value))
            assertEquals(GtkInputProgress.State.REJECTED,g.observe("WAIT_FOCUS",2,sample(b=22)))
        }
        val g=GtkInputProgress(10,20,false)
        g.observe("WAIT_FOCUS",1,sample(b=21,bp=1))
        assertEquals(GtkInputProgress.State.REJECTED,g.observe("WAIT_FOCUS",2,sample()))
        assertEquals("COUNTER_REGRESSED",g.reason)
    }
    @Test fun heldInputCannotAdvanceOrAppearOnTheIdleChannel() {
        val g=GtkInputProgress(10,20,false)
        assertEquals(GtkInputProgress.State.WAITING,g.observe("WAIT_FOCUS",1,sample(b=22,bp=1)))
        assertEquals(GtkInputProgress.State.COMPLETE,g.observe("WAIT_FOCUS",2,sample(b=22)))
        assertEquals(GtkInputProgress.State.REJECTED,g.observe("WAIT_FOCUS",3,sample(b=22,bp=1)))
        for(v in listOf(sample(kp=1),sample(bp=2),sample(bp=-1))) {
            assertEquals(GtkInputProgress.State.REJECTED,GtkInputProgress(10,20,false).observe("WAIT_FOCUS",1,v))
        }
    }
    @Test fun exactDeadlineLateSuccessAndClockRegressionAreRejected() {
        val g=GtkInputProgress(10,20,false)
        g.observe("WAIT_FOCUS",100,sample())
        assertEquals(GtkInputProgress.State.REJECTED,g.observe("WAIT_FOCUS",6100,sample(b=22)))
        assertEquals("PROGRESS_TIMEOUT",g.reason)
        assertEquals(GtkInputProgress.State.REJECTED,g.observe("WAIT_FOCUS",6101,sample(b=22)))
        val clock=GtkInputProgress(10,20,false);clock.observe("WAIT_FOCUS",100,sample())
        assertEquals(GtkInputProgress.State.REJECTED,clock.observe("WAIT_FOCUS",99,sample(b=22)))
    }
    @Test fun dialogWaitsForUpBeforeEscapeAndDoesNotRelaxFinalCounts() {
        val g=GtkInputProgress(10,20,true)
        g.observe("WAIT_DIALOG",0,sample(b=22))
        assertEquals(GtkInputProgress.State.WAITING,g.observe("WAIT_DIALOG_FOCUS",1,sample(b=23,bp=1)))
        assertEquals(GtkInputProgress.State.COMPLETE,g.observe("WAIT_DIALOG_FOCUS",2,sample(b=24)))
        g.observe("DIALOG_READY",3,sample(b=24))
        assertEquals(GtkInputProgress.State.COMPLETE,g.observe("DIALOG_READY",20000,sample(b=24)))
        assertEquals(GtkInputProgress.State.WAITING,g.observe("WAIT_DIALOG_CLOSED",20001,sample(k=11,b=24,kp=1)))
        assertEquals(GtkInputProgress.State.COMPLETE,g.observe("WAIT_DIALOG_CLOSED",20002,sample(k=12,b=24)))
        assertEquals(GtkInputProgress.State.COMPLETE,g.observe("WAIT_RETIRED",20003,sample(k=12,b=24)))
        assertEquals(GtkInputProgress.State.REJECTED,g.observe("WAIT_RETIRED",20004,sample(k=13,b=24)))
    }
    @Test fun missingEarlierCountsAndCounterOverflowCannotBecomeNewBaseline() {
        assertEquals(GtkInputProgress.State.REJECTED,GtkInputProgress(10,20,false).observe("WAIT_KEYS",1,sample()))
        for(k in listOf(-1L,Long.MAX_VALUE-3,Long.MAX_VALUE)) {
            assertThrows(IllegalArgumentException::class.java) {GtkInputProgress(k,20,false)}
            assertThrows(IllegalArgumentException::class.java) {GtkInputProgress(10,k,false)}
        }
    }
}
