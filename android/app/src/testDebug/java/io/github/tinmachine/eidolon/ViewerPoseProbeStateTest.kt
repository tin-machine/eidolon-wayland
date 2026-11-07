package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class ViewerPoseProbeStateTest {
    private val owner=ViewerPoseOwner(2,3,4)
    private val pose=WindowMotionPose.at(0.0,1.4,0.0)
    @Test fun stationaryTwentySamplesWithAdvancingRenderDoNotClaimMotionOrTrackingFreshness() {
        val state=ViewerPoseProbeState(owner,0,10)
        repeat(20) {state.observe(owner,true,it*100L,pose,10+it.toLong(),true)}
        assertEquals(ViewerPoseProbeState.Phase.COMPLETE,state.phase);assertEquals(20,state.samples)
        assertEquals(20,state.candidates);assertFalse(state.motionObserved);assertTrue(state.renderAdvanced)
    }
    @Test fun changingPoseIsObservedWithoutRetainingAHistory() {
        val state=ViewerPoseProbeState(owner,0,1)
        state.observe(owner,true,0,pose,1,true)
        state.observe(owner,true,100,pose.copy(p=listOf(.01,1.4,0.0)),2,true)
        assertTrue(state.motionObserved);assertEquals(2,state.samples)
    }
    @Test fun ownerLossAndFocusLossArePermanentEvenIfTheyLaterReturn() {
        for(changed in listOf(owner.copy(runtime=9),owner.copy(scene=9),owner.copy(sdk=9))) {
            val state=ViewerPoseProbeState(owner,0,1)
            state.observe(changed,true,0,pose,1,true);state.observe(owner,true,100,pose,2,true)
            assertEquals("owner_changed",state.reason);assertEquals(0,state.samples)
        }
        val state=ViewerPoseProbeState(owner,0,1);state.observe(owner,false,0,pose,1,true)
        assertEquals("not_focused",state.reason)
    }
    @Test fun timeoutBoundaryAndFastPollingRemainBounded() {
        val state=ViewerPoseProbeState(owner,0,1)
        repeat(50) {state.observe(owner,true,it*100L,pose,1,true)}
        assertEquals(20,state.samples);assertEquals(ViewerPoseProbeState.Phase.SAMPLING,state.phase)
        state.observe(owner,true,5000,pose,99,true)
        assertEquals("timeout",state.reason) // deadlineちょうどの新frameは成功へ変えない。
        val busy=ViewerPoseProbeState(owner,0,1)
        repeat(129) {busy.observe(owner,true,0,pose,1,true)}
        assertEquals("timeout",busy.reason);assertEquals(1,busy.samples)
    }
    @Test fun clockCounterOrPoseFailureIsNotReplacedByZero() {
        val time=ViewerPoseProbeState(owner,100,1);time.observe(owner,true,99,pose,1,true);assertEquals("clock_reversed",time.reason)
        val frames=ViewerPoseProbeState(owner,0,2);frames.observe(owner,true,0,pose,1,true);assertEquals("render_reversed",frames.reason)
        val invalid=ViewerPoseProbeState(owner,0,1);invalid.observe(owner,true,0,pose.copy(q=listOf(0.0,0.0,0.0,0.0)),1,true);assertEquals("invalid_pose",invalid.reason)
        val sdk=ViewerPoseProbeState(owner,0,1);sdk.observe(owner,true,0,pose,1,false);assertEquals("sdk_math_mismatch",sdk.reason)
    }
    @Test fun validReadingOutsideWindowBoundsIsSamplingEvidenceOnly() {
        val state=ViewerPoseProbeState(owner,0,1)
        repeat(20) {state.observe(owner,true,it*100L,WindowMotionPose.at(0.0,10.0,0.0),1+it.toLong(),true)}
        assertEquals(ViewerPoseProbeState.Phase.COMPLETE,state.phase);assertEquals(0,state.candidates)
        assertFalse(state.originObserved)
    }
}
