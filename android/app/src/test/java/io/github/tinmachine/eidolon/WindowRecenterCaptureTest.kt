package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class WindowRecenterCaptureTest {
    private val owner=WindowSizeIdentity(2,"2:3:4:5",6,7)
    private val initial=WindowRecenterGeometry(WindowMotionPose.at(0.0,1.4,1.9),null,WindowPhysicalSize(1.2f,.9f),null,"logical/prod/canvas:fixed")
    private val desired=WindowRecenterPose.candidate(WindowMotionPose.at(1.0,1.6,0.0))!!
    private fun state(now:Long=100)=WindowRecenterCapture(owner,initial,desired,8,now)
    private fun applied()=initial.copy(pose=desired,poseCommand="10")
    private fun submitted():WindowRecenterCapture=state().also {assertTrue(it.readyToSubmit(owner,101,initial,8));assertTrue(it.submitted("10",102))}
    @Test fun nativeAckModelGeometryAndFreshFocusAreSeparate() {
        val c=submitted()
        assertFalse(c.observed(owner,103,"ACCEPTED",initial,9,true))
        assertFalse(c.observed(owner,104,null,applied(),9,false))
        assertEquals(WindowRecenterCapture.Phase.GEOMETRY,c.phase)
        assertTrue(c.observed(owner,105,null,applied(),9,true))
        assertTrue(c.beginReturn(owner,106,9))
        assertFalse(c.focusObserved(owner,107,9,true,applied()))
        assertFalse(c.focusObserved(owner,108,10,false,applied()))
        assertTrue(c.focusObserved(owner,109,10,true,applied()))
        assertEquals(WindowRecenterCapture.Phase.COMPLETE,c.phase)
    }
    @Test fun oneSubmissionAndNoAutomaticRetryAfterRejection() {
        val c=submitted();assertFalse(c.submitted("11",103))
        assertFalse(c.observed(owner,104,"BUSY",initial,9,false))
        assertEquals("recenter_native_rejected",c.reason)
        assertFalse(c.readyToSubmit(owner,105,initial,8));assertFalse(c.submitted("10",106))
    }
    @Test fun modelCommandAndPoseCannotBorrowOldOrDifferentAcknowledgement() {
        val c=submitted()
        assertFalse(c.observed(owner,103,"ACCEPTED",initial.copy(pose=desired,poseCommand="99"),9,true))
        assertEquals("recenter_pose_changed",c.reason)
        val d=submitted()
        assertFalse(d.observed(owner,103,"pending",applied(),9,true))
        assertEquals(WindowRecenterCapture.Phase.COMMITTING,d.phase)
    }
    @Test fun physicalSizeStampAndEveryPixelDimensionStayFixed() {
        for(changed in listOf(initial.copy(physical=WindowPhysicalSize(1.3f,.9f)),initial.copy(sizeCommand="99"),initial.copy(pixels="new-canvas"))) {
            val c=state();assertFalse(c.readyToSubmit(owner,101,changed,8));assertEquals("recenter_baseline_changed",c.reason)
            val d=submitted();assertFalse(d.observed(owner,103,"ACCEPTED",changed.copy(pose=desired,poseCommand="10"),9,true))
            assertEquals("recenter_dimensions_changed",d.reason)
        }
    }
    @Test fun changedOwnerAndEpochNeverRebind() {
        for(changed in listOf(owner.copy(runtime=9),owner.copy(target="2:3:4:6"),owner.copy(revision=7),owner.copy(intent=8))) {
            val c=submitted();assertFalse(c.observed(changed,103,"ACCEPTED",applied(),9,true));assertEquals("owner_changed",c.reason)
        }
        for(epoch in listOf(7L,10L)) {val c=submitted();assertFalse(c.observed(owner,103,"ACCEPTED",applied(),epoch,true));assertEquals("recenter_epoch_changed",c.reason)}
    }
    @Test fun deadlineBoundaryClockReversalAndLargeClockRemainBounded() {
        val c=submitted();assertFalse(c.observed(owner,10100,"ACCEPTED",applied(),9,true));assertEquals("recenter_timeout",c.reason)
        val d=submitted();assertFalse(d.observed(owner,101,"ACCEPTED",applied(),9,true));assertEquals("clock_reversed",d.reason)
        val high=state(Long.MAX_VALUE-10000);assertTrue(high.readyToSubmit(owner,Long.MAX_VALUE-9999,initial,8));assertTrue(high.submitted("10",Long.MAX_VALUE-9998))
        assertFalse(high.observed(owner,Long.MAX_VALUE,"ACCEPTED",applied(),9,true));assertEquals("recenter_timeout",high.reason)
    }
    @Test fun stoppedOrCancelledCaptureCannotRevive() {
        val c=submitted();c.cancel("scene_left");assertFalse(c.observed(owner,103,"ACCEPTED",applied(),9,true));assertFalse(c.beginReturn(owner,104,9))
        assertEquals("scene_left",c.reason)
    }
    @Test fun returnRejectsExtraEpochOrNewPlacement() {
        val c=submitted();assertTrue(c.observed(owner,103,"ACCEPTED",applied(),9,true));assertTrue(c.beginReturn(owner,104,9))
        assertFalse(c.focusObserved(owner,105,11,true,applied()));assertEquals("recenter_epoch_changed",c.reason)
        val d=submitted();assertTrue(d.observed(owner,103,"ACCEPTED",applied(),9,true));assertTrue(d.beginReturn(owner,104,9))
        assertFalse(d.focusObserved(owner,105,10,true,applied().copy(poseCommand="11")));assertEquals("recenter_geometry_changed",d.reason)
    }
    @Test fun overflowingEpochAndInvalidCandidateAreNotCaptured() {
        assertThrows(IllegalArgumentException::class.java) {WindowRecenterCapture(owner,initial,desired,Long.MAX_VALUE-1,0)}
        assertThrows(IllegalArgumentException::class.java) {WindowRecenterCapture(owner,initial,WindowMotionPose.at(11.0,1.0,0.0),8,0)}
    }
    @Test fun delayedViewerSampleExpiresBeforeAnyEnqueue() {
        val c=state();assertFalse(c.readyToSubmit(owner,350,initial,8));assertEquals("viewer_sample_expired",c.reason)
        assertFalse(c.submitted("10",351))
    }
}
