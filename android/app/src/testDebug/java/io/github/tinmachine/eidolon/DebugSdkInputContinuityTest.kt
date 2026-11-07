package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class DebugSdkInputContinuityTest {
    private fun proof()=DebugSdkInputContinuity.Proof(InputWindowKey(7,"2","1","3","4"),8,
        DebugSdkInputContinuity.Geometry(640,360,1280,720,1.2,.675,listOf(0.0,2.0,1.5,0.0,0.0,0.0,1.0),1),true,true,true,true)
    @Test fun initialFrameIsRequiredButRepaintDoesNotDropDownUpOrFourKeys() {
        val gate=DebugSdkInputContinuity(7,8,"2","3");val p=proof()
        assertFalse(gate.accept(p.copy(frameReady=false)))
        assertTrue(gate.accept(p))
        repeat(6) {assertTrue(gate.accept(p.copy(frameReady=false)))}
        assertEquals(6,gate.metadata()["repaint_checks"])
        assertTrue(gate.accept(p))
    }
    @Test fun geometryGenerationEpochAndRuntimeChangesNeverRebind() {
        val p=proof()
        for(changed in listOf(p.copy(geometry=p.geometry.copy(logicalWidth=800)),p.copy(geometry=p.geometry.copy(physicalWidth=2.0)),
            p.copy(geometry=p.geometry.copy(pose=listOf(0.0,3.0,1.5,0.0,0.0,0.0,1.0))),p.copy(key=p.key.copy(generation="5")),
            p.copy(key=p.key.copy(session="9")),p.copy(key=p.key.copy(scene="9")),p.copy(epoch=9),p.copy(key=p.key.copy(runtime=9)))) {
            val g=DebugSdkInputContinuity(7,8,"2","3");assertTrue(g.accept(p));assertFalse(g.accept(changed));assertFalse(g.accept(p))
        }
    }
    @Test fun sdkLossNativeInteractiveAndUnappliedGeometryFailClosed() {
        val p=proof()
        for(changed in listOf(p.copy(sdkOwned=false),p.copy(interactive=false),p.copy(geometryApplied=false))) {
            val g=DebugSdkInputContinuity(7,8,"2","3");assertTrue(g.accept(p));assertFalse(g.accept(changed));assertFalse(g.accept(p))
        }
    }
    @Test fun dialogHasIndependentInitialSyncAndBoundedOwner() {
        val p=proof();val dialog=p.copy(key=p.key.copy(window="2",generation="6"))
        val g=DebugSdkInputContinuity(7,8,"2","3");assertTrue(g.accept(p))
        assertFalse(g.accept(dialog.copy(frameReady=false)));assertFalse(g.accept(dialog.copy(geometryApplied=false)))
        assertTrue(g.accept(dialog));assertTrue(g.accept(dialog.copy(frameReady=false)))
        assertFalse(g.accept(p.copy(key=p.key.copy(window="3"))))
    }
}
