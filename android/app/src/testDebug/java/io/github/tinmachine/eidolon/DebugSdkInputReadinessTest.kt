package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class DebugSdkInputReadinessTest {
    private fun stamp(intent:Long=1)=DebugSdkInputReadiness.Stamp(7,"5:1:9:3",12,intent,20,30,null,null)
    private fun observe(gate:DebugSdkInputReadiness,time:Long=0,current:DebugSdkInputReadiness.Stamp?=gate.baseline,
        owner:Boolean=true,intent:Boolean=true,unpressed:Boolean=true,adapter:Boolean=true,frame:Boolean=true)=
        gate.observe(time,current,owner,intent,unpressed,adapter,frame)

    @Test fun delayedAdapterCacheWaitsWithoutClaimAndCapturesOnlyOnce() {
        val gate=DebugSdkInputReadiness(stamp(),100)
        assertEquals(DebugSdkInputReadiness.State.WAITING,observe(gate,100,adapter=false))
        assertEquals("WAIT_ADAPTER",gate.reason);assertFalse(gate.capture())
        assertEquals(DebugSdkInputReadiness.State.WAITING,observe(gate,1099,adapter=false))
        assertEquals(DebugSdkInputReadiness.State.READY,observe(gate,1100))
        assertTrue(gate.capture());assertFalse(gate.capture())
        assertEquals(true,gate.metadata()["wait_adapter_observed"])
        assertEquals(false,gate.metadata()["wait_frame_observed"])
    }

    @Test fun newerProducerFrameWaitsAndBothReasonsAreRecordedWithoutUsingOldDrawAsReady() {
        val gate=DebugSdkInputReadiness(stamp(),0)
        observe(gate,frame=false,adapter=false)
        assertEquals("WAIT_ADAPTER_AND_FRAME",gate.reason)
        observe(gate,10,frame=false)
        assertEquals("WAIT_PRODUCER_FRAME",gate.reason);assertFalse(gate.capture())
        observe(gate,20);assertTrue(gate.capture())
        assertEquals(true,gate.metadata()["wait_adapter_observed"])
        assertEquals(true,gate.metadata()["wait_frame_observed"])
    }

    @Test fun everyCapturedIdentityOrInputChangePermanentlyCancelsInsteadOfRebasing() {
        for(changed in listOf(stamp().copy(runtime=8),stamp().copy(target="5:1:9:4"),stamp().copy(target="6:1:9:3"),
            stamp().copy(target="5:1:10:3"),stamp().copy(target="5:2:9:3"),stamp().copy(epoch=13),stamp().copy(intent=2),
            stamp().copy(keys=21),stamp().copy(buttons=31),stamp().copy(focusWindow="1"),stamp().copy(focusToken="5"))) {
            val gate=DebugSdkInputReadiness(stamp(),0)
            observe(gate,adapter=false)
            assertEquals(DebugSdkInputReadiness.State.CANCELLED,observe(gate,10,current=changed))
            assertEquals(DebugSdkInputReadiness.State.CANCELLED,observe(gate,20))
            assertFalse(gate.capture())
        }
    }

    @Test fun provisionalTrialNoticesInvalidationWithoutCounterChangeAndNeverStealsNewTrial() {
        val intents=InputIntentSerial();val ticket=intents.capture()
        val gate=DebugSdkInputReadiness(stamp(ticket.expected),0)
        observe(gate,adapter=false)
        val oldCounter=intents.get();intents.invalidate()
        assertEquals(oldCounter,intents.get())
        assertEquals(DebugSdkInputReadiness.State.CANCELLED,observe(gate,10,current=stamp(intents.get()),intent=intents.current(ticket)))
        val unrelated=intents.capture()
        intents.release(ticket)
        assertTrue(intents.current(unrelated))
        assertFalse(gate.capture())
    }

    @Test fun ownerLossMissingTargetOrHeldInputIsNotTreatedAsCacheDelay() {
        val flags=listOf(Triple(false,true,true),Triple(true,false,true),Triple(true,true,false))
        for((owner,intent,unpressed) in flags) {
            val gate=DebugSdkInputReadiness(stamp(),0)
            assertEquals(DebugSdkInputReadiness.State.CANCELLED,observe(gate,owner=owner,intent=intent,unpressed=unpressed,adapter=false))
            assertFalse(gate.capture())
        }
        val missing=DebugSdkInputReadiness(stamp(),0)
        assertEquals(DebugSdkInputReadiness.State.CANCELLED,observe(missing,current=null))
    }

    @Test fun ExactDeadlineRejectsEvenIfCacheBecomesReadyAndRepeatedPollsCannotExtendIt() {
        val gate=DebugSdkInputReadiness(stamp(),100)
        observe(gate,100,adapter=false)
        assertEquals(DebugSdkInputReadiness.State.WAITING,observe(gate,3099,adapter=false))
        assertEquals(DebugSdkInputReadiness.State.CANCELLED,observe(gate,3100))
        assertEquals("PROBE_INPUT_PREPARE_TIMEOUT",gate.reason)
        assertFalse(gate.capture())
        val clock=DebugSdkInputReadiness(stamp(),100)
        assertEquals(DebugSdkInputReadiness.State.CANCELLED,observe(clock,99))
        val polls=DebugSdkInputReadiness(stamp(),0)
        repeat(513) {observe(polls,adapter=false)}
        assertEquals("PROBE_INPUT_PREPARE_TIMEOUT",polls.reason)
    }

    @Test fun metadataIsBoundedAndReadyMustBeCheckedAgainBeforeOneTimeScopeClaim() {
        val scope=DebugSdkInputScope(Any(),7,"5","a".repeat(16),"input",9)
        val gate=DebugSdkInputReadiness(stamp(),0)
        observe(gate)
        assertEquals(DebugSdkInputReadiness.State.READY,gate.state)
        // 実Hostは同monitor内でobserve→claim。ここでは外部変化が先に起きた場合の失効を確認。
        observe(gate,10,current=stamp().copy(intent=2))
        assertFalse(gate.capture())
        assertTrue(scope.claimTrial()) // gateはscope claimを早期に消費していない。
        assertFalse(scope.claimTrial())
        assertEquals(setOf("state","reason","polls","elapsed_ms","adapter_ready","producer_frame_ready",
            "wait_adapter_observed","wait_frame_observed"),gate.metadata().keys)
    }
}
