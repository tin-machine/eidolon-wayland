package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test
import kotlin.math.abs

class WindowControlLayoutTest {
    @Test fun controlsStayOutsideBodyAndDoNotOverlapAtMinimumAndMaximumWindowSize() {
        for(anchor in listOf("top-left","top-right","bottom-left","bottom-right")) for(size in listOf(.25,1.2,3.0)) {
            val spec=WindowControlLayout(anchor)
            val positions=spec.positions(size,size)
            assertEquals(listOf("focus","close"),positions.map {it.action})
            for(p in positions) {assertTrue(abs(p.y)-p.diameter/2>size/2);assertEquals(.08,p.diameter,0.0)}
            assertTrue(abs(positions[0].x-positions[1].x)>=spec.diameter+spec.gap-.000001)
        }
    }
    @Test fun anchorAndOffsetAreBodyRelativeWithoutWorldOrCanvasCoordinates() {
        val a=WindowControlLayout().positions(1.2,.9)[1]
        val b=WindowControlLayout("bottom-left",.1,.2).positions(1.2,.9)[1]
        assertEquals(.6,a.x,0.00001);assertEquals(-.7,b.x,.00001)
        assertEquals(-a.y-.2,b.y,.00001)
        assertFalse(WindowControlLayout("world").valid());assertFalse(WindowControlLayout(diameter=.01).valid())
        assertFalse(WindowControlLayout(offsetY=Double.NaN).valid())
    }
    @Test fun samePressFiresAtMostOnceAndNeverForDifferentLayout() {
        val p=WindowControlPress();assertTrue(p.down(1,10,"target1:layout1",listOf(0.0,0.0,0.0)))
        assertFalse(p.down(1,10,"target1:layout1",listOf(0.0,0.0,0.0)))
        assertTrue(p.up(1,10,"target1:layout1"));assertFalse(p.up(1,10,"target1:layout1"))
        p.down(1,20,"target1:layout1",listOf(0.0,0.0,0.0))
        assertFalse(p.up(1,20,"target2:layout1"))
    }
    @Test fun lateOldReleaseDoesNotRemoveNewPress() {
        val p=WindowControlPress();p.down(1,10,"owner",listOf(0.0,0.0,0.0));p.down(1,20,"owner",listOf(0.0,0.0,0.0))
        assertFalse(p.down(1,10,"old-owner",listOf(0.0,0.0,0.0)))
        assertFalse(p.down(1,20,"duplicate-owner",listOf(0.0,0.0,0.0)))
        assertFalse(p.up(1,10,"owner"));assertTrue(p.up(1,20,"owner"))
    }
    @Test fun dragHoverCancelAndNonFinitePointsNeverBecomeClicks() {
        val p=WindowControlPress();p.down(1,10,"owner",listOf(0.0,0.0,0.0));p.move(1,listOf(.1,0.0,0.0),.02)
        assertFalse(p.up(1,10,"owner"));p.down(1,20,"owner",listOf(0.0,0.0,0.0));p.cancel(1)
        assertFalse(p.up(1,20,"owner"));assertFalse(p.down(1,30,"owner",listOf(Double.NaN,0.0,0.0)))
    }
    @Test fun sourceBudgetIsBoundedAndCancelledSourcesFreeTheirSlot() {
        val p=WindowControlPress();for(source in 1L..8) assertTrue(p.down(source,10,"owner",listOf(0.0,0.0,0.0)))
        assertFalse(p.down(9,10,"owner",listOf(0.0,0.0,0.0)))
        p.cancel(1);assertTrue(p.down(9,10,"owner",listOf(0.0,0.0,0.0)))
        p.clear();assertFalse(p.up(9,10,"owner"))
    }
}
