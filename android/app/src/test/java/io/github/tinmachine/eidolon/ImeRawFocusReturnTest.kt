package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class ImeRawFocusReturnTest {
    private val target=ImeSdkTarget("1","2","3","4")
    private inner class Fixture {
        var now=0L
        var sent=0
        var rejected=false
        var epoch=0L
        val model=ImeRawFocusReturn(10,target,20,1,"5",{now},{"99"},{_,value ->
            sent++;epoch=value;if(rejected) ImeEnqueueResult.BUSY else ImeEnqueueResult.ACCEPTED
        },100)
        fun frame(clean: Boolean=true, android: Boolean=true, ticket: Long=20, currentEpoch: Long=3,
            focus: ImeSessionFocus?=null, runtime: Long=10, available: Boolean=true) =
            ImeRawReturnFrame(runtime,ticket,currentEpoch,available,focus,clean,android)
        fun observe(frame: ImeRawReturnFrame=frame(), ack: ImeDeliveryResult?=null) =
            model.observe(frame,ack?.let {listOf(ImeSessionCompletion("99",it))} ?: emptyList())
        fun focus(token: String="6",epoch: Long=3,targetValue: ImeSdkTarget=target)=ImeSessionFocus(10,epoch,targetValue,token)
    }
    @Test fun cleanupAndAndroidReleaseBothPrecedeSingleFocusEnqueue() {
        val f=Fixture()
        f.observe(f.frame(clean=false));f.observe(f.frame(android=false));f.observe(f.frame(currentEpoch=1))
        assertEquals(0,f.sent)
        f.observe();assertEquals(1,f.sent);assertEquals(3,f.epoch)
        repeat(4) {f.observe()};assertEquals(1,f.sent)
        assertTrue(f.model.blocksRaw())
    }
    @Test fun snapshotAndAckMustBothMatchTheNewFocus() {
        val f=Fixture();f.observe()
        f.observe(f.frame(focus=f.focus()));assertTrue(f.model.blocksRaw())
        f.observe(f.frame(focus=f.focus("5")),ImeDeliveryResult.APPLIED);assertTrue(f.model.blocksRaw())
        f.observe(f.frame(focus=f.focus(targetValue=target.copy(window="9"))));assertTrue(f.model.blocksRaw())
        f.observe(f.frame(focus=f.focus()));assertEquals(ImeRawReturnPhase.COMPLETE,f.model.metadata().phase)
        assertFalse(f.model.blocksRaw());assertEquals(1,f.sent)
    }
    @Test fun ackBeforeObservedFocusDoesNotReopenRaw() {
        val f=Fixture();f.observe();f.observe(ack=ImeDeliveryResult.APPLIED)
        assertTrue(f.model.blocksRaw());f.observe(f.frame(focus=f.focus()))
        assertFalse(f.model.blocksRaw())
    }
    @Test fun newIntentRuntimeOrTargetLossCancelsCapturedReturn() {
        for(change in 0..2) {
            val f=Fixture();f.observe()
            f.observe(when(change) {0->f.frame(ticket=21);1->f.frame(runtime=11);else->f.frame(available=false)},ImeDeliveryResult.APPLIED)
            assertEquals(ImeRawReturnPhase.CANCELLED,f.model.metadata().phase)
            assertFalse(f.model.blocksRaw());assertEquals(1,f.sent)
        }
    }
    @Test fun epochChangeWhileWaitingNeverRetargetsTheCommand() {
        val f=Fixture();f.observe();f.observe(f.frame(currentEpoch=4,focus=f.focus(epoch=4)),ImeDeliveryResult.APPLIED)
        assertEquals(ImeRawReturnPhase.CANCELLED,f.model.metadata().phase);assertEquals(1,f.sent)
    }
    @Test fun enqueueBusyAndNegativeAckFailWithoutRetry() {
        val f=Fixture();f.rejected=true;f.observe();repeat(3) {f.observe()}
        assertEquals(ImeRawReturnPhase.FAILED,f.model.metadata().phase);assertTrue(f.model.blocksRaw());assertEquals(1,f.sent)
        val g=Fixture();g.observe();g.observe(ack=ImeDeliveryResult.STALE);g.observe(g.frame(focus=g.focus()),ImeDeliveryResult.APPLIED)
        assertEquals(ImeRawReturnPhase.FAILED,g.model.metadata().phase);assertEquals(1,g.sent)
    }
    @Test fun deadlineRejectsEvenSimultaneousAckAndFreshSnapshot() {
        for(time in listOf(100L,101L)) {
            val f=Fixture();f.observe();f.now=time
            f.observe(f.frame(focus=f.focus()),ImeDeliveryResult.APPLIED)
            assertEquals(ImeRawReturnPhase.FAILED,f.model.metadata().phase);assertTrue(f.model.blocksRaw())
        }
    }
    @Test fun watchdogWithoutAnyWorkerPacketFailsClosed() {
        val f=Fixture();f.now=100;f.model.expire()
        assertEquals(ImeRawReturnPhase.FAILED,f.model.metadata().phase);assertEquals(0,f.sent)
    }
    @Test fun cancelledOwnerIgnoresAllLateAcknowledgements() {
        val f=Fixture();f.observe();f.model.cancel()
        f.observe(f.frame(focus=f.focus()),ImeDeliveryResult.APPLIED)
        assertEquals(ImeRawReturnPhase.CANCELLED,f.model.metadata().phase)
    }
}
