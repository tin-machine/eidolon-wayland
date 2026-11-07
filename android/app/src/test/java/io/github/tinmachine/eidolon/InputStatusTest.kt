package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class InputStatusTest {
    private val ready=InputConditions(true,true,true,"Ready",false,1,true,true,"2")
    @Test fun overlappingReasonsFollowReadinessBeforeModeAndDevice() {
        assertEquals("READY",InputStatus.from(ready).reason)
        assertEquals("BACKGROUND",InputStatus.from(ready.copy(xrFocused=false,editing=true,keyboards=0)).reason)
        assertEquals("DISCONNECTED",InputStatus.from(ready.copy(transport="Failed",editing=true)).reason)
        assertEquals("EDITING",InputStatus.from(ready.copy(editing=true,keyboards=0)).reason)
        assertEquals(InputStatus.Action.FINISH_EDITING,InputStatus.from(ready.copy(editing=true)).action)
    }
    @Test fun removedKeyboardOrTargetCannotAppearReadyAndUnknownIsNotDisconnected() {
        assertEquals("NO_KEYBOARD",InputStatus.from(ready.copy(keyboards=0)).reason)
        assertEquals("DEVICE_UNKNOWN",InputStatus.from(ready.copy(keyboards=null)).reason)
        assertEquals("TARGET_NOT_READY",InputStatus.from(ready.copy(targetReady=false)).reason)
        assertEquals(InputStatus.Action.FOCUS,InputStatus.from(ready.copy(focusedWindow=null)).action)
        assertEquals(InputStatus.Action.OPEN,InputStatus.from(ready.copy(hasWindows=false,focusedWindow=null)).action)
        assertTrue(InputStatus.from(ready).message.contains("窓 2"))
    }
}
