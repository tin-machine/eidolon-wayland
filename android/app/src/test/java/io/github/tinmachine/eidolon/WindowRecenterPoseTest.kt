package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test
import kotlin.math.sqrt

class WindowRecenterPoseTest {
    @Test fun identityPlacesExactlyOneAndAHalfMetresForwardAtEyeHeight() {
        val input=WindowMotionPose.at(.2,1.6,.3)
        val result=checkNotNull(WindowRecenterPose.candidate(input))
        assertTrue(result.near(WindowMotionPose.at(.2,1.6,1.8)))
        assertEquals(listOf(.2,1.6,.3),input.p)
    }
    @Test fun yawAndQuaternionSignPreserveTheSameFrontDirection() {
        val h=sqrt(.5);val input=WindowMotionPose(listOf(0.0,1.4,0.0),listOf(0.0,h,0.0,h))
        val result=checkNotNull(WindowRecenterPose.candidate(input))
        assertEquals(1.5,result.p[0],.000001);assertEquals(0.0,result.p[2],.000001)
        assertTrue(result.near(checkNotNull(WindowRecenterPose.candidate(input.copy(q=input.q.map {-it})))))
        val back=checkNotNull(WindowRecenterPose.candidate(input.copy(q=listOf(0.0,1.0,0.0,0.0))))
        assertEquals(-1.5,back.p[2],.000001)
    }
    @Test fun invalidOrVerticalPoseNeverFabricatesAFrontDirection() {
        val valid=WindowMotionPose.at(0.0,1.4,0.0)
        for(q in listOf(listOf(0.0,0.0,0.0,0.0),listOf(0.0,0.0,0.0,2.0),listOf(Double.NaN,0.0,0.0,1.0),listOf(sqrt(.5),0.0,0.0,sqrt(.5))))
            assertNull(WindowRecenterPose.candidate(valid.copy(q=q)))
        assertNull(WindowRecenterPose.candidate(valid.copy(p=listOf(Double.POSITIVE_INFINITY,1.4,0.0))))
    }
    @Test fun nativeWindowBoundsAreRejectedWithoutAxisClamping() {
        assertNull(WindowRecenterPose.candidate(WindowMotionPose.at(0.0,1.4,9.0)))
        assertNull(WindowRecenterPose.candidate(WindowMotionPose.at(0.0,-.1,0.0)))
        assertNull(WindowRecenterPose.candidate(WindowMotionPose.at(0.0,4.1,0.0)))
        assertNotNull(WindowRecenterPose.candidate(WindowMotionPose.at(0.0,4.0,8.5)))
    }
    @Test fun smallUnitRoundingIsNormalizedButLargeScaleIsNotAccepted() {
        val value=WindowMotionPose.at(0.0,1.4,0.0).copy(q=listOf(0.0,0.0,0.0,1.0001))
        val result=checkNotNull(WindowRecenterPose.candidate(value))
        assertEquals(1.0,result.q.sumOf {it*it},.0000001)
    }
}
