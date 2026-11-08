package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test
import kotlin.math.sqrt

class WindowRecenterChoiceTest {
    private val eye=WindowMotionPose.at(0.0,1.4,0.0)
    private val size=WindowPhysicalSize(1.2f,.9f)
    private fun occupant(id:String="other",pose:WindowMotionPose=WindowMotionPose.at(0.0,1.4,1.5))=WindowRecenterOccupant(id,pose,size)
    @Test fun emptySceneKeepsPreferredDistanceAndDoesNotMutateViewer() {
        val result=WindowRecenterChoice.choose(eye,"selected",size,emptyList())
        assertEquals("candidate",result.reason);assertEquals(0,result.candidateIndex);assertEquals(1,result.tested)
        assertTrue(checkNotNull(result.pose).near(WindowMotionPose.at(0.0,1.4,1.5)));assertEquals(0.0,eye.p[2],0.0)
    }
    @Test fun coplanarBodyMovesOnlySelectedCandidateSlightlyCloser() {
        val fixed=occupant();val result=WindowRecenterChoice.choose(eye,"selected",size,listOf(fixed))
        assertEquals(1,result.candidateIndex);assertEquals(2,result.tested)
        assertTrue(checkNotNull(result.pose).near(WindowMotionPose.at(0.0,1.4,1.47)))
        assertEquals(1.5,fixed.pose.p[2],0.0);assertEquals(size,fixed.physical)
    }
    @Test fun CoplanarButLaterallySeparatedBodyDoesNotForceDepthShift() {
        val result=WindowRecenterChoice.choose(eye,"selected",size,listOf(occupant(pose=WindowMotionPose.at(3.0,1.4,1.5))))
        assertEquals(0,result.candidateIndex)
    }
    @Test fun crossingOrientedBodyCanOccupyEveryBoundedCandidate() {
        val h=sqrt(.5);val blocker=occupant(pose=WindowMotionPose(listOf(0.0,1.4,1.5),listOf(0.0,h,0.0,h)))
        val result=WindowRecenterChoice.choose(eye,"selected",size,listOf(blocker))
        assertEquals("no_separated_candidate",result.reason);assertNull(result.pose);assertNull(result.candidateIndex);assertEquals(9,result.tested)
    }
    @Test fun invalidOrMixedScopeNeverReturnsAPlacement() {
        val values=listOf(listOf(occupant("selected")),listOf(occupant(),occupant()),(0..8).map {occupant(it.toString())},
            listOf(occupant().copy(pose=eye.copy(q=listOf(0.0,0.0,0.0,0.0)))),listOf(occupant().copy(physical=WindowPhysicalSize(Float.NaN,.9f))))
        for(others in values) {
            val result=WindowRecenterChoice.choose(eye,"selected",size,others)
            assertEquals("invalid_scope",result.reason);assertNull(result.pose);assertEquals(0,result.tested)
        }
    }
    @Test fun badViewerOrOutOfBoundsDoesNotClampOrResample() {
        val result=WindowRecenterChoice.choose(eye.copy(p=listOf(0.0,4.1,0.0)),"selected",size,emptyList())
        assertEquals("viewer_unavailable",result.reason);assertNull(result.pose);assertEquals(0,result.tested)
        val h=sqrt(.5)
        assertNull(WindowRecenterChoice.choose(eye.copy(q=listOf(h,0.0,0.0,h)),"selected",size,emptyList()).pose)
    }
    @Test fun yawAndQuaternionSignGiveEquivalentChoiceAndHeight() {
        val h=sqrt(.5);val viewer=eye.copy(q=listOf(0.0,h,0.0,h));val obstacle=occupant(pose=WindowMotionPose(listOf(1.5,1.4,0.0),viewer.q))
        val result=WindowRecenterChoice.choose(viewer,"selected",size,listOf(obstacle))
        val sign=WindowRecenterChoice.choose(viewer.copy(q=viewer.q.map {-it}),"selected",size,listOf(obstacle.copy(pose=obstacle.pose.copy(q=obstacle.pose.q.map {-it}))))
        assertEquals(1,result.candidateIndex);assertTrue(checkNotNull(result.pose).near(checkNotNull(sign.pose)))
        assertEquals(1.4,result.pose!!.p[1],0.0);assertEquals(1.47,result.pose.p[0],.000001)
    }
    @Test fun resultStringContainsMetadataButNoWorldCoordinates() {
        val result=WindowRecenterChoice.choose(WindowMotionPose.at(.1234,1.6543,.9876),"selected",size,emptyList())
        assertEquals("WindowRecenterChoice(reason=candidate,candidateIndex=0,tested=1)",result.toString())
    }
}
