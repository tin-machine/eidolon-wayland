package io.github.tinmachine.eidolon

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PointerButtonsTest {
    @Test fun duplicateSdkCallbacksEmitOnePressAndOneRelease() {
        val buttons = PointerButtons()
        assertEquals(PointerButtons.Transition(1, 1), buttons.update(1, 1))
        assertNull(buttons.update(1, 1))
        assertEquals(PointerButtons.Transition(1, 0), buttons.update(1, 0))
        assertNull(buttons.update(1, 0))
    }

    @Test fun selectCallbacksPreserveOtherPressedButtons() {
        val buttons = PointerButtons()
        assertEquals(PointerButtons.Transition(2, 2), buttons.update(2, 2))
        assertEquals(PointerButtons.Transition(1, 3), buttons.update(1, 1))
        assertEquals(PointerButtons.Transition(1, 2), buttons.update(1, 0))
        assertEquals(PointerButtons.Transition(2, 0), buttons.update(7, 0))
    }
}
