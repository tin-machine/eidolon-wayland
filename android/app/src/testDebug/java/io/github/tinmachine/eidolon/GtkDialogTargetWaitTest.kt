package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class GtkDialogTargetWaitTest {
    private val key=InputWindowKey(1,"2","3","4","5")
    private val geometry=DebugSdkInputContinuity.Geometry(640,360,640,360,1.0,.5625,listOf(0.0,1.4,1.6,0.0,0.0,0.0,1.0),0)
    private val baseline=GtkDialogTargetWait.Stamp(key,6,0,2,"1","7",geometry)
    @Test fun lateSdkPublicationWaitsWithoutClaimingAnotherTarget() {
        val wait=GtkDialogTargetWait(baseline,100)
        assertFalse(wait.observe(100,baseline,true,true,false,false))
        assertFalse(wait.observe(600,baseline,true,true,false,false))
        assertTrue(wait.observe(1100,baseline,true,true,true,false))
        assertEquals(true,wait.metadata()["wait_observed"])
    }
    @Test fun generationAndInputOrFocusChangesPermanentlyCancel() {
        for(changed in listOf(baseline.copy(key=key.copy(generation="8")),baseline.copy(epoch=7),
            baseline.copy(keys=1),baseline.copy(buttons=3),baseline.copy(focusWindow="9"),baseline.copy(focusToken="10"),
            baseline.copy(geometry=geometry.copy(physicalWidth=2.0)),baseline.copy(geometry=geometry.copy(logicalWidth=800)),
            baseline.copy(geometry=geometry.copy(pose=listOf(1.0,1.4,1.6,0.0,0.0,0.0,1.0))))) {
            val wait=GtkDialogTargetWait(baseline,100)
            assertFalse(wait.observe(101,changed,true,true,true,false))
            assertFalse(wait.observe(102,baseline,true,true,true,false))
        }
    }
    @Test fun invalidatedTrialCancelsEvenWhenCounterDoesNotChange() {
        val wait=GtkDialogTargetWait(baseline,100)
        assertFalse(wait.observe(101,baseline,false,true,true,false))
        assertFalse(wait.observe(102,baseline,true,true,true,false))
    }
    @Test fun deadlineCannotBeRescuedByLateReadyPublication() {
        val wait=GtkDialogTargetWait(baseline,100)
        assertFalse(wait.observe(3100,baseline,true,true,true,false))
        assertEquals("PROBE_DIALOG_TARGET_TIMEOUT",wait.metadata()["reason"])
    }
    @Test fun heldInputAndPermanentTargetLossCannotResume() {
        for((unpressed,failed) in listOf(false to false,true to true)) {
            val wait=GtkDialogTargetWait(baseline,100)
            assertFalse(wait.observe(101,baseline,true,unpressed,true,failed))
            assertFalse(wait.observe(102,baseline,true,true,true,false))
        }
    }
    @Test fun pollingHasAFiniteLimitEvenWhenClockDoesNotAdvance() {
        val wait=GtkDialogTargetWait(baseline,100)
        repeat(512) {assertFalse(wait.observe(100,baseline,true,true,false,false))}
        assertFalse(wait.observe(100,baseline,true,true,true,false))
        assertEquals("PROBE_DIALOG_TARGET_TIMEOUT",wait.metadata()["reason"])
    }
}
