package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class WindowSizeCaptureTest {
    private val owner=WindowSizeIdentity(1,"1:2:3:4",5,6)
    private val size=WindowPhysicalSize(1.2f,.9f)
    private fun menu():WindowSizeCapture=WindowSizeCapture(owner,size,null,7,0).also {assertTrue(it.resetObserved(owner,1,"ACCEPTED",true,8))}
    @Test fun ratioPreservedAndOutOfRangeIsNotClamped() {
        val next=checkNotNull(size.step(true));assertEquals(size.width/size.height,next.width/next.height,.00001f)
        assertTrue(checkNotNull(next.step(false)).near(size))
        assertNull(WindowPhysicalSize(3f,1f).step(true));assertNull(WindowPhysicalSize(1f,.25f).step(false))
        assertNull(WindowPhysicalSize(Float.NaN,1f).step(true));assertNull(WindowPhysicalSize(1f,Float.POSITIVE_INFINITY).step(false))
    }
    @Test fun menuWaitsForResetAckAndroidReleaseAndFreshEpoch() {
        val c=WindowSizeCapture(owner,size,null,7,0)
        assertFalse(c.resetObserved(owner,1,null,true,8));assertFalse(c.resetObserved(owner,2,"ACCEPTED",false,8))
        assertFalse(c.resetObserved(owner,3,null,true,7));assertTrue(c.resetObserved(owner,4,null,true,8))
        val bad=WindowSizeCapture(owner,size,null,7,0);assertFalse(bad.resetObserved(owner,1,"BUSY",true,8));assertEquals(WindowSizeCapture.Phase.CANCELLED,bad.phase)
    }
    @Test fun staleSizingOrOwnerCannotSubmit() {
        val c=menu();assertFalse(c.choose(owner,2,true,"9",size));assertEquals("size_changed",c.reason)
        val other=menu();assertFalse(other.choose(owner.copy(intent=7),2,true,null,size));assertEquals("owner_changed",other.reason)
    }
    @Test fun ackModelAndGeometryAreIndependentAndSubmitIsOnce() {
        val c=menu();assertTrue(c.choose(owner,2,true,null,size));assertFalse(c.choose(owner,3,true,null,size));assertTrue(c.submitted("20",3));assertFalse(c.submitted("21",4))
        val next=checkNotNull(c.desired)
        assertFalse(c.observed(owner,4,null,"20",next,true,true));assertFalse(c.observed(owner,5,"ACCEPTED",null,size,true,true))
        assertFalse(c.observed(owner,6,null,"20",next,false,true));assertTrue(c.observed(owner,7,null,"20",next,true,true))
        assertTrue(c.beginReturn(owner,8,9));assertFalse(c.focusObserved(owner,9,9,true));assertFalse(c.focusObserved(owner,10,10,false));assertTrue(c.focusObserved(owner,11,10,true))
        assertFalse(c.focusObserved(owner,12,11,true))
    }
    @Test fun nativeFailureAndPixelChangeNeverReturnSuccess() {
        for(code in listOf("BUSY","STALE","NOT_READY")) {val c=menu();c.choose(owner,2,true,null,size);c.submitted("20",3);assertFalse(c.observed(owner,4,code,"20",checkNotNull(c.desired),true,true));assertEquals(WindowSizeCapture.Phase.CANCELLED,c.phase)}
        val c=menu();c.choose(owner,2,true,null,size);c.submitted("20",3);assertFalse(c.observed(owner,4,"ACCEPTED","20",checkNotNull(c.desired),true,false));assertEquals("pixel_dimensions_changed",c.reason)
    }
    @Test fun exactDeadlineAndLateAckCannotResurrectCancelledOwner() {
        val c=menu();assertFalse(c.choose(owner,15000,true,null,size));assertEquals("timeout",c.reason)
        assertFalse(c.resetObserved(owner,15001,"ACCEPTED",true,8));assertFalse(c.beginReturn(owner,15001,9))
    }
    @Test fun modeDescribesOnlyTheOwnedWindowAndPauseStillWins() {
        assertEquals(WindowMode.MENU,WindowMode.from(false,true,false,null,"WINDOW_MENU",false,WindowSizeCapture.Phase.MENU))
        assertEquals(WindowMode.RESIZING,WindowMode.from(false,true,false,null,"WINDOW_RESIZING",false,WindowSizeCapture.Phase.GEOMETRY))
        assertEquals(WindowMode.RETURNING,WindowMode.from(false,true,false,null,"WINDOW_RETURNING",false,WindowSizeCapture.Phase.RETURNING))
        assertEquals(WindowMode.PAUSED,WindowMode.from(false,false,false,null,"WINDOW_MENU",false,WindowSizeCapture.Phase.MENU))
        assertEquals(WindowMode.RAW,WindowMode.from(true,true,false,null,"WINDOW_OPERATION_CLEANUP",true,null))
    }
}
