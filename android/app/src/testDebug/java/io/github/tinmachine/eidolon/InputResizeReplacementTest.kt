package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class InputResizeReplacementTest {
    private val old=InputWindowKey(4,"518","7","1","18")
    private val next=old.copy(generation="20")
    private fun token()=InputResizeReplacement(9400001,old,18,800,500,1000)
    private fun proof(target:InputWindowKey=next,epoch:Long=20)=InputResizeReplacement.Proof(target,epoch,true,true,true,true,true,true)
    @Test fun ownRetirementAckAndFreshProofAreAllRequired() {
        val t=token();assertFalse(t.observe(proof(),10));assertTrue(t.retiring(old.retirement,11))
        assertFalse(t.observe(proof(),12));t.acknowledge(true,13);assertTrue(t.observe(proof(),14))
        assertFalse(t.observe(proof(),15));assertFalse(t.retiring(old.retirement,15))
    }
    @Test fun snapshotPublicationCanLagCommandAndRetirementWithoutAdoptingOldGeneration() {
        val t=token();t.acknowledge(true,1);assertTrue(t.retiring(old.retirement,2))
        assertFalse(t.observe(proof(old,19).copy(receiptMatches=false),3));assertNull(t.failure)
        assertFalse(t.observe(proof(old,20),4));assertNull(t.failure)
        assertTrue(t.observe(proof(),5))
    }
    @Test fun ownedReceiptCannotBeReplacedAfterItWasObserved() {
        val t=token();assertFalse(t.observe(proof().copy(resourcesReady=false),1))
        assertFalse(t.observe(proof().copy(receiptMatches=false),2));assertEquals("RESIZE_REQUEST_CHANGED",t.failure)
    }
    @Test fun deadlineEqualityAndLateAckNeverRestore() {
        for(time in listOf(1000L,1001L)) {
            val t=token();t.retiring(old.retirement,1);t.acknowledge(true,time)
            assertFalse(t.observe(proof(),time));assertEquals("RESIZE_DEADLINE",t.failure)
        }
    }
    @Test fun anotherRuntimeSessionWindowOrSceneIsNeverAdopted() {
        for(key in listOf(next.copy(runtime=5),next.copy(session="519"),next.copy(window="8"),next.copy(scene="2"))) {
            val t=token();t.retiring(old.retirement,1);t.acknowledge(true,1)
            assertFalse(t.observe(proof(key),2));assertEquals("RESIZE_TARGET_CHANGED",t.failure)
        }
    }
    @Test fun generationReuseUnexplainedEpochAndForeignInputFailClosed() {
        for(p in listOf(proof(old.copy(generation="17")),proof(epoch=21),proof().copy(inputUnchanged=false))) {
            val t=token();t.retiring(old.retirement,1);t.acknowledge(true,1)
            assertFalse(t.observe(p,2));assertNotNull(t.failure)
        }
    }
    @Test fun nativeAppliedPixelsSerialsAndOldOwnerCleanupCannotBeSubstitutedByAck() {
        for(p in listOf(proof().copy(sizesApplied=false),proof().copy(serialsPresent=false),proof().copy(producerAdvanced=false),proof().copy(resourcesReady=false))) {
            val t=token();t.retiring(old.retirement,1);t.acknowledge(true,1)
            assertFalse(t.observe(p,2));assertNull(t.failure);assertTrue(t.observe(proof(),3))
        }
    }
    @Test fun busyCompletionCannotBeRetriedIntoSuccess() {
        val t=token();t.acknowledge(false,1);t.acknowledge(true,2)
        assertFalse(t.observe(proof(),3));assertEquals("RESIZE_COMMAND_REJECTED",t.failure)
    }
    @Test fun sameCanvasCanKeepExactTargetAndEpochWithoutConsumingForeignRetirement() {
        val t=token();t.acknowledge(true,1);assertTrue(t.observe(proof(old,18),2))
        assertFalse(t.retiring(old.retirement,3))
    }
    @Test fun pendingBlocksDispatchAndSealUntilSuccessfulAdoption() {
        var time=1L;val s=InputIntentSerial {time};val trial=s.capture();val t=token()
        assertTrue(s.beginReplacement(trial,t));assertNull(s.dispatch(trial,{true}) {true});assertFalse(s.seal(trial))
        s.retiring(old.retirement);t.acknowledge(true,time);assertTrue(s.completeReplacement(trial,t,proof()))
        assertEquals(true,s.dispatch(trial,{true}) {true});assertTrue(s.seal(trial))
    }
    @Test fun foreignAndDuplicateRetirementPermanentlyLosePendingTrial() {
        for(foreign in listOf(true,false)) {
            val s=InputIntentSerial {1};val trial=s.capture();val t=token();s.beginReplacement(trial,t)
            if(!foreign) s.retiring(old.retirement)
            s.retiring(if(foreign) old.retirement.copy(runtime=5) else old.retirement)
            t.acknowledge(true,1);assertFalse(s.current(trial));assertFalse(s.completeReplacement(trial,t,proof()))
        }
    }
    @Test fun externalIntentAfterAllProofStillWinsAndLateReleaseCannotLoseNewTrial() {
        val s=InputIntentSerial {1};val trial=s.capture();val t=token();s.beginReplacement(trial,t)
        s.retiring(old.retirement);t.acknowledge(true,1);s.invalidate()
        assertFalse(s.completeReplacement(trial,t,proof()));val fresh=s.capture();s.release(trial);assertTrue(s.current(fresh))
    }
    @Test fun replacementCountAndRequestReuseAreBoundedWithoutEvictingActiveToken() {
        val s=InputIntentSerial {1};val trial=s.capture()
        for(id in 9400001L..9400002L) {
            val t=InputResizeReplacement(id,old,18,800,500,1000)
            assertTrue(s.beginReplacement(trial,t));assertFalse(s.beginReplacement(trial,token()))
            t.acknowledge(true,1);assertTrue(s.completeReplacement(trial,t,proof(old,18)))
            assertFalse(s.beginReplacement(trial,t))
        }
        assertFalse(s.beginReplacement(trial,InputResizeReplacement(9400003,old,18,800,500,1000)))
        assertTrue(s.current(trial))
    }
    @Test fun timeoutLosesTrialEvenBeforeCallerPollsAdoption() {
        var time=1L;val s=InputIntentSerial {time};val trial=s.capture();val t=token();s.beginReplacement(trial,t)
        time=1000;assertFalse(s.current(trial));assertNull(s.dispatch(trial,{true}) {true})
    }
}
