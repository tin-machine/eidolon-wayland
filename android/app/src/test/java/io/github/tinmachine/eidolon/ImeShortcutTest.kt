package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class ImeShortcutTest {
    private val left = ImeShortcut.Event(1, ImeShortcut.Key.CTRL_LEFT, ImeShortcut.Action.DOWN, 10)
    private val right = left.copy(key = ImeShortcut.Key.CTRL_RIGHT, downTime = 11)
    private val space = left.copy(key = ImeShortcut.Key.SPACE, downTime = 20, ctrl = true)
    private fun up(event: ImeShortcut.Event) = event.copy(action = ImeShortcut.Action.UP)
    private class Fixture(val accept: Boolean = true) {
        val state = ImeShortcut()
        var toggles = 0
        fun send(event: ImeShortcut.Event) = state.dispatch(event) { toggles++; accept }
    }

    @Test fun eitherControlTogglesOnceAndOwnsSpaceAndControlTails() {
        for (ctrl in listOf(left, right)) {
            val f = Fixture()
            assertFalse(f.send(ctrl)) // 元ownerへのCtrl down配送を妨げない。
            assertTrue(f.send(space))
            assertTrue(f.send(space)) // 同じdownの別入口からの再dispatch。
            assertTrue(f.send(space.copy(repeat = 1)))
            assertTrue(f.send(up(ctrl))) // Ctrlを先に離しても所有権を失わない。
            assertTrue(f.send(space.copy(ctrl = false, repeat = 2)))
            assertTrue(f.send(up(space).copy(ctrl = false)))
            assertEquals(1, f.toggles)
            assertFalse(f.send(space.copy(downTime = 30)))
        }
    }

    @Test fun bothControlsAreOwnedButAnUnrelatedDeviceReleaseIsNot() {
        val f = Fixture()
        f.send(left); f.send(right)
        assertTrue(f.send(space))
        assertFalse(f.send(up(left).copy(device = 2)))
        assertTrue(f.send(up(left)))
        assertTrue(f.send(up(right)))
        assertTrue(f.send(up(space)))
        assertEquals(1, f.toggles)
    }

    @Test fun releasingOnlyOneControlStillAllowsTheOtherSide() {
        val f = Fixture()
        f.send(left); f.send(right); f.send(up(left))
        assertTrue(f.send(space))
        assertFalse(f.send(up(left)))
        assertTrue(f.send(up(right)))
    }

    @Test fun declinedCallbackPreservesNormalShortcutAndIsNotRetriedForTheSamePress() {
        val f = Fixture(false)
        f.send(left)
        assertFalse(f.send(space))
        assertFalse(f.send(space))
        assertFalse(f.send(space.copy(repeat = 3)))
        assertFalse(f.send(up(space)))
        assertFalse(f.send(up(left)))
        assertEquals(1, f.toggles)
    }

    @Test fun shiftAltMetaAndMissingCtrlAreExcludedForTheWholePress() {
        for (event in listOf(space.copy(shift = true), space.copy(alt = true), space.copy(meta = true), space.copy(ctrl = false))) {
            val f = Fixture()
            f.send(left)
            assertFalse(f.send(event))
            assertFalse(f.send(space.copy(repeat = 1)))
            assertFalse(f.send(space))
            f.send(up(space))
            assertEquals(0, f.toggles)
            assertTrue(f.send(space.copy(downTime = 21)))
        }
    }

    @Test fun controlMetaFromAnotherDeviceOrBeforeResumeCannotArmShortcut() {
        val f = Fixture()
        assertFalse(f.send(space))
        f.send(left)
        assertFalse(f.send(space.copy(device = 2)))
        f.send(left.copy(device = 2))
        f.send(up(space).copy(device = 2))
        assertTrue(f.send(space.copy(device = 2, downTime = 21)))
        assertFalse(f.send(up(left)))
        assertEquals(1, f.toggles)
    }

    @Test fun resetRequiresFreshControlDownButKeepsAcceptedTails() {
        val f = Fixture()
        f.send(left); assertTrue(f.send(space))
        f.state.reset()
        assertTrue(f.send(space.copy(repeat = 1, active = false)))
        assertTrue(f.send(up(space).copy(active = false)))
        assertTrue(f.send(up(left).copy(active = false)))
        assertFalse(f.send(left.copy(repeat = 1)))
        assertFalse(f.send(space.copy(downTime = 30)))
        assertEquals(1, f.toggles)
        f.send(left.copy(downTime = 40))
        f.send(up(space).copy(downTime = 30))
        assertTrue(f.send(space.copy(downTime = 50)))
        assertEquals(2, f.toggles)
    }

    @Test fun deviceReplacementSeparatesOldTailFromNewPressWithSameDeviceId() {
        val f = Fixture()
        f.send(left); f.send(space)
        f.state.deviceChanged(1)
        val replacementSpace = space.copy(downTime = 30)
        assertFalse(f.send(replacementSpace))
        assertTrue(f.send(up(space).copy(physical = false))) // removed deviceの遅いup。
        assertFalse(f.send(up(replacementSpace)))
        assertTrue(f.send(up(left).copy(physical = false)))
        assertEquals(1, f.toggles)
        f.send(left.copy(downTime = 40))
        assertTrue(f.send(space.copy(downTime = 50)))
    }

    @Test fun cancellationNeverTogglesAndCancelsEligibilityButConsumesOwnedUp() {
        val f = Fixture()
        f.send(left)
        assertFalse(f.send(space.copy(cancelled = true)))
        assertFalse(f.send(space.copy(downTime = 30)))
        assertEquals(0, f.toggles)
        f.send(left.copy(downTime = 40))
        val accepted = space.copy(downTime = 50)
        assertTrue(f.send(accepted))
        assertTrue(f.send(up(accepted).copy(cancelled = true)))
        assertTrue(f.send(up(left).copy(downTime = 40)))
        assertEquals(1, f.toggles)
    }

    @Test fun inactiveVirtualSystemAndUnknownDevicesCannotTriggerOrArm() {
        for (event in listOf(left.copy(active = false), left.copy(physical = false), left.copy(system = true), left.copy(device = -1))) {
            val f = Fixture()
            assertFalse(f.send(event))
            assertFalse(f.send(space))
            assertEquals(0, f.toggles)
        }
        for (event in listOf(space.copy(active = false), space.copy(physical = false), space.copy(system = true), space.copy(device = -1))) {
            val f = Fixture()
            f.send(left)
            assertFalse(f.send(event))
            assertEquals(0, f.toggles)
        }
    }

    @Test fun systemNavigationResetsEligibilityWithoutDroppingAcceptedTails() {
        val f = Fixture()
        f.send(left); f.send(space)
        assertFalse(f.send(left.copy(key = ImeShortcut.Key.OTHER, system = true)))
        assertTrue(f.send(up(space).copy(system = true)))
        assertTrue(f.send(up(left)))
        assertFalse(f.send(space.copy(downTime = 30)))
        assertEquals(1, f.toggles)
    }

    @Test fun normalKeysAndSpaceAlreadyHeldBeforeControlAreUnchanged() {
        val f = Fixture()
        assertFalse(f.send(space.copy(ctrl = false)))
        f.send(left)
        assertFalse(f.send(space.copy(repeat = 1)))
        assertFalse(f.send(space))
        assertFalse(f.send(left.copy(key = ImeShortcut.Key.OTHER, ctrl = true)))
        assertFalse(f.send(up(left).copy(key = ImeShortcut.Key.OTHER, ctrl = true)))
        f.send(up(space))
        assertTrue(f.send(space.copy(downTime = 30)))
        assertEquals(1, f.toggles)
    }

    @Test fun synchronousOwnerResetInsideCallbackDoesNotLosePressOwnership() {
        val state = ImeShortcut()
        state.dispatch(left) { fail("Ctrl cannot toggle"); false }
        assertTrue(state.dispatch(space) { state.reset(); true })
        assertTrue(state.dispatch(up(space)) { fail("up cannot toggle"); false })
        assertTrue(state.dispatch(up(left)) { fail("up cannot toggle"); false })
    }
}
