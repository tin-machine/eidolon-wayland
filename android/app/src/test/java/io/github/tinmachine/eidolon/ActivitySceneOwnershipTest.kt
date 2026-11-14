package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class ActivitySceneOwnershipTest {
    @Test fun unregisteredSceneCannotBorrowOldActivityAndOldCloseCannotCloseNewScene() {
        val gate=ActivitySceneOwnership()
        val first=gate.register()
        assertFalse(gate.owns(first,0))
        assertTrue(gate.activate(first,7))
        assertTrue(gate.owns(first,7))
        val replacement=gate.register()
        assertEquals(0L,gate.activeEpoch())
        assertFalse(gate.owns(first,7))
        assertFalse(gate.owns(replacement,7))
        assertFalse(gate.activate(first,8))
        assertTrue(gate.activate(replacement,8))
        assertFalse(gate.close(first,7))
        assertTrue(gate.owns(replacement,8))
    }

    @Test fun closedEpochCannotBeRevivedAndNewSceneRequiresNewAdmission() {
        val gate=ActivitySceneOwnership()
        val owner=gate.register()
        assertTrue(gate.activate(owner,1))
        assertTrue(gate.close(owner,1))
        assertFalse(gate.owns(owner,1))
        assertFalse(gate.activate(owner,1))
        assertFalse(gate.activate(owner,0))
        assertTrue(gate.activate(owner,2))
        assertFalse(gate.close(owner,1))
        assertTrue(gate.owns(owner,2))
    }
}
