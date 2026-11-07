package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class LauncherFormTest {
    @Test fun preservesExplicitArgumentBoundaries() {
        val args=listOf("", "a b", "日本語", "$(touch nope)", "; echo nope")
        val form=LauncherForm(executable="scripts/日本語 tool.sh",interpreter="/bin/bash",args=args)
        assertTrue(form.valid())
        assertEquals(args,form.args)
        assertFalse(form.copy(args=listOf("\u0000")).valid())
        assertFalse(form.copy(args=List(65) {"a"}).valid())
        assertFalse(form.copy(id="../not-id").valid())
        assertFalse(form.copy(executable="line\nfile").valid())
    }
    @Test fun panelInputIsShownBeforeLinuxFocusAndConnection() {
        val conditions=InputConditions(visible=true,xrFocused=true,runtimeReady=true,panelEditing=true)
        assertEquals("PANEL_INPUT",InputStatus.from(conditions).reason)
        assertEquals("BACKGROUND",InputStatus.from(conditions.copy(xrFocused=false)).reason)
        assertEquals("DISCONNECTED",InputStatus.from(conditions.copy(panelEditing=false)).reason)
    }
}
