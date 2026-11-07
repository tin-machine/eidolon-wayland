package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test
import kotlin.math.sqrt

class WindowMotionTest {
    private val owner=WindowMoveIdentity(1,"1:2:3:4",5,6,7,8)
    private fun capture()=WindowMoveCapture(owner,WindowMotionPose.at(.2,1.0,.3),WindowMotionPose.at(0.0,1.4,1.5),0)
    @Test fun completedMoveCannotBeRearmedByAnOldOrDuplicateDown() {
        val starts=WindowMoveStarts();assertTrue(starts.accept(1,10));assertTrue(starts.accept(1,20))
        assertFalse(starts.accept(1,10));assertFalse(starts.accept(1,20));assertTrue(starts.accept(1,21))
    }
    @Test fun sourcePressHistoryIsBoundedWithoutEvictingOldProtection() {
        val starts=WindowMoveStarts();for(source in 1L..8) assertTrue(starts.accept(source,10))
        assertFalse(starts.accept(9,20));assertFalse(starts.accept(1,9));assertTrue(starts.accept(1,11))
    }
    @Test fun captureDoesNotJumpAndTranslationIncludesDepth() {
        val c=capture();assertTrue(c.initial.near(c.preview(owner,WindowMotionPose.at(.2,1.0,.3),1)!!))
        assertTrue(WindowMotionPose.at(.3,1.6,1.9).near(c.preview(owner,WindowMotionPose.at(.5,1.2,.7),2)!!))
    }
    @Test fun rotationMovesTheWholeWindowAboutTheCapturedSourceOffset() {
        val c=WindowMoveCapture(owner,WindowMotionPose.at(),WindowMotionPose.at(0.0,1.0,2.0),0)
        val rotated=WindowMotionPose(listOf(0.0,0.0,0.0),listOf(0.0,sqrt(.5),0.0,sqrt(.5)))
        val p=c.preview(owner,rotated,1)!!
        assertEquals(2.0,p.p[0],.00001);assertEquals(1.0,p.p[1],.00001);assertEquals(0.0,p.p[2],.00001)
        assertTrue(p.q.zip(rotated.q).all {(a,b)->kotlin.math.abs(a-b)<.00001})
    }
    @Test fun everyCaptureIdentityChangeCancelsWithoutRetargeting() {
        for(changed in listOf(owner.copy(runtime=2),owner.copy(target="1:2:3:5"),owner.copy(revision=6),owner.copy(intent=7),owner.copy(source=8),owner.copy(downTime=9))) {
            val c=capture();assertNull(c.preview(changed,WindowMotionPose.at(.2,1.0,.3),1));assertEquals(WindowMoveCapture.Phase.CANCELLED,c.phase)
        }
    }
    @Test fun invalidSourceOrWindowPoseNeverReachesSubmit() {
        val c=capture();assertNull(c.preview(owner,WindowMotionPose.at(Double.NaN),1));assertFalse(c.release(owner,2))
        val d=capture();assertNull(d.preview(owner,WindowMotionPose.at(100.0,0.0,0.0),1));assertEquals(d.initial,d.pose)
    }
    @Test fun exactDeadlineCancelsAndReleaseSubmitsOnlyOnce() {
        val c=capture();assertFalse(c.release(owner,60000));assertEquals(WindowMoveCapture.Phase.CANCELLED,c.phase)
        val d=capture();assertTrue(d.release(owner,10));assertFalse(d.release(owner,11));assertTrue(d.submitted("10",12));assertFalse(d.submitted("11",13))
        assertFalse(d.observe(owner,5010,"ACCEPTED","10",d.pose,true));assertEquals(WindowMoveCapture.Phase.CANCELLED,d.phase)
    }
    @Test fun nativeAckModelAndGeometryMustAllMatchBeforeCompletion() {
        val c=capture();c.release(owner,1);c.submitted("10",2)
        assertFalse(c.observe(owner,3,null,"10",c.pose,true));assertEquals(WindowMoveCapture.Phase.COMMITTING,c.phase)
        assertFalse(c.observe(owner,4,"ACCEPTED","9",c.pose,true))
        assertFalse(c.observe(owner,5,null,"10",c.pose,false));assertEquals(WindowMoveCapture.Phase.GEOMETRY,c.phase)
        assertTrue(c.observe(owner,6,null,"10",c.pose,true));assertEquals(WindowMoveCapture.Phase.COMPLETE,c.phase)
    }
    @Test fun failedNativeCommitIsNotRetriedAndCannotRestoreInput() {
        val c=capture();c.release(owner,1);c.submitted("10",2)
        assertFalse(c.observe(owner,3,"BUSY","10",c.pose,true));assertEquals(WindowMoveCapture.Phase.CANCELLED,c.phase)
        assertFalse(c.submitted("11",4));assertFalse(c.observe(owner,5,"ACCEPTED","10",c.pose,true))
    }
    @Test fun perWindowBadgeNeverCallsUnfocusedOrBlockedWindowReady() {
        fun badge(reason:String="READY",focus:Boolean=true,move:WindowMoveCapture.Phase?=null)=WindowMode.from(true,true,false,move,reason,focus)
        assertEquals(WindowMode.RAW,badge());assertEquals(WindowMode.SELECT,badge(focus=false))
        assertEquals(WindowMode.MOVING,badge(move=WindowMoveCapture.Phase.DRAGGING))
        assertEquals(WindowMode.PLACING,badge(move=WindowMoveCapture.Phase.COMMITTING))
        assertEquals(WindowMode.IME_WAIT,badge("IME_RECOVERY"));assertEquals(WindowMode.IME,badge("IME_CONNECTED"))
        assertEquals(WindowMode.PANEL,badge("PANEL_INPUT"));assertEquals(WindowMode.NO_KEYBOARD,badge("NO_KEYBOARD"))
        assertEquals(WindowMode.PAUSED,badge("DISCONNECTED"));assertEquals(WindowMode.PREPARING,badge("CONNECTING"))
    }
}
