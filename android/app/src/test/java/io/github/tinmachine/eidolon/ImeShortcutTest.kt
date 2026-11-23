package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class ImeShortcutTest {
    private val left = ImeShortcut.Event(1, ImeShortcut.Key.SHIFT_LEFT, ImeShortcut.Action.DOWN, 10)
    private val right = left.copy(key = ImeShortcut.Key.SHIFT_RIGHT, downTime = 11)
    private val space = left.copy(key = ImeShortcut.Key.SPACE, downTime = 20, shift = true)
    private fun up(event: ImeShortcut.Event) = event.copy(action = ImeShortcut.Action.UP)
    private class Fixture(val accept: Boolean = true) {
        val state = ImeShortcut()
        var toggles = 0
        fun send(event: ImeShortcut.Event) = state.dispatch(event) { toggles++; accept }
    }

    @Test fun eitherShiftTogglesOnceAndOwnsSpaceAndShiftTails() {
        for (shift in listOf(left, right)) {
            val f = Fixture()
            assertFalse(f.send(shift)) // 元ownerへのShift down配送を妨げない。
            assertTrue(f.send(space))
            assertTrue(f.send(space)) // 同じdownの別入口からの再dispatch。
            assertTrue(f.send(space.copy(repeat = 1)))
            assertTrue(f.send(up(shift))) // Shiftを先に離しても所有権を失わない。
            assertTrue(f.send(space.copy(shift = false, repeat = 2)))
            assertTrue(f.send(up(space).copy(shift = false)))
            assertEquals(1, f.toggles)
            assertFalse(f.send(space.copy(downTime = 30)))
        }
    }

    @Test fun ctrlSpacePassesThroughForTheSelectedIme() {
        val f = Fixture()
        val ctrl = left.copy(key = ImeShortcut.Key.OTHER, ctrl = true)
        val ctrlSpace = space.copy(ctrl = true, shift = false)
        for (event in listOf(ctrl, ctrlSpace, ctrlSpace.copy(repeat = 1), up(ctrlSpace), up(ctrl))) {
            assertFalse(f.send(event))
        }
        assertEquals(0, f.toggles)
    }

    @Test fun bothShiftsAreOwnedButAnUnrelatedDeviceReleaseIsNot() {
        val f = Fixture()
        f.send(left); f.send(right)
        assertTrue(f.send(space))
        assertFalse(f.send(up(left).copy(device = 2)))
        assertTrue(f.send(up(left)))
        assertTrue(f.send(up(right)))
        assertTrue(f.send(up(space)))
        assertEquals(1, f.toggles)
    }

    @Test fun releasingOnlyOneShiftStillAllowsTheOtherSide() {
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

    @Test fun ctrlAltMetaAndMissingShiftAreExcludedForTheWholePress() {
        for (event in listOf(space.copy(ctrl = true), space.copy(alt = true), space.copy(meta = true), space.copy(shift = false))) {
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

    @Test fun shiftMetaFromAnotherDeviceOrBeforeResumeCannotArmShortcut() {
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

    @Test fun resetRequiresFreshShiftDownButKeepsAcceptedTails() {
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

    @Test fun normalKeysAndSpaceAlreadyHeldBeforeShiftAreUnchanged() {
        val f = Fixture()
        assertFalse(f.send(space.copy(shift = false)))
        f.send(left)
        assertFalse(f.send(space.copy(repeat = 1)))
        assertFalse(f.send(space))
        assertFalse(f.send(left.copy(key = ImeShortcut.Key.OTHER, shift = true)))
        assertFalse(f.send(up(left).copy(key = ImeShortcut.Key.OTHER, shift = true)))
        f.send(up(space))
        assertTrue(f.send(space.copy(downTime = 30)))
        assertEquals(1, f.toggles)
    }

    @Test fun synchronousOwnerResetInsideCallbackDoesNotLosePressOwnership() {
        val state = ImeShortcut()
        state.dispatch(left) { fail("Shift cannot toggle"); false }
        assertTrue(state.dispatch(space) { state.reset(); true })
        assertTrue(state.dispatch(up(space)) { fail("up cannot toggle"); false })
        assertTrue(state.dispatch(up(left)) { fail("up cannot toggle"); false })
    }
    @Test fun completedTailsCannotReleaseANewerPressAcrossTwoIngressStages() {
        val f=Fixture();f.send(left);f.send(space)
        assertTrue(f.send(up(space)));assertTrue(f.send(up(left)))
        val next=left.copy(downTime=40);f.send(next)
        assertTrue(f.send(up(left))) // 遅いpost-IMEの旧UP。新Shiftの所有を消さない。
        assertTrue(f.send(space.copy(downTime=50)))
        assertEquals(2,f.toggles)
    }
    @Test fun evictedCompletedTailsRemainSuppressedByRetirementFloor() {
        val f=Fixture()
        for(i in 0..140) {
            val shift=left.copy(downTime=100L+i*10)
            val sp=space.copy(downTime=101L+i*10)
            f.send(shift);assertTrue(f.send(sp));assertTrue(f.send(up(sp)));assertTrue(f.send(up(shift)))
        }
        val next=left.copy(downTime=2000);f.send(next)
        assertTrue(f.send(up(left.copy(downTime=100))))
        assertTrue(f.send(space.copy(downTime=2001)))
        assertEquals(142,f.toggles)
    }
    @Test fun acceptedOwnedLimitDoesNotEvictAnUnreleasedTail() {
        val f=Fixture()
        for(i in 0..80) { f.send(left.copy(downTime=100L+i*10));f.send(space.copy(downTime=101L+i*10)) }
        assertTrue(f.toggles<=64)
        assertTrue(f.send(up(space.copy(downTime=101))))
        assertTrue(f.send(up(left.copy(downTime=100))))
    }
}
