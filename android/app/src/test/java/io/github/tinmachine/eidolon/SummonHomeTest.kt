package io.github.tinmachine.eidolon

import kotlin.math.sqrt
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SummonHomeTest {
    private val first = InputWindowKey(1, "2", "3", "4", "5")
    private val second = InputWindowKey(1, "2", "6", "4", "7")
    private val initial = mapOf(
        first to WindowMotionPose.at(1.0, 1.5, 2.0),
        second to WindowMotionPose.at(2.0, 1.2, 3.0),
    )

    @Test fun movesAGroupRigidlyAndRetainsItsOriginalPoses() {
        val home = checkNotNull(SummonHome.capture(first, 8, initial))
        val half = sqrt(0.5)
        val destination = WindowMotionPose(listOf(0.0, 1.6, 1.0), listOf(0.0, half, 0.0, half))
        val positions = checkNotNull(home.posesAt(destination))
        assertTrue(positions.getValue(first).near(destination))
        val fromFirst = initial.getValue(first).inverse() * initial.getValue(second)
        val moved = positions.getValue(first).inverse() * positions.getValue(second)
        assertTrue(fromFirst.near(moved))
        assertEquals(initial, home.returnPoses())
    }

    @Test fun membershipGenerationAndRevisionMustMatch() {
        val home = checkNotNull(SummonHome.capture(first, 8, initial))
        assertTrue(home.matches(initial.keys, 8))
        assertFalse(home.matches(initial.keys, 9))
        assertFalse(home.matches(setOf(first, second.copy(generation = "8")), 8))
        assertFalse(home.matches(setOf(first), 8))
        assertNull(SummonHome.capture(first, 8, initial + (second.copy(scene = "5") to WindowMotionPose.at())))
        assertNotNull(SummonHome.capture(first, null, mapOf(first to initial.getValue(first))))
        assertNull(SummonHome.capture(first, null, initial))
    }

    @Test fun outOfBoundsMemberRejectsTheWholeDestination() {
        val home = checkNotNull(SummonHome.capture(first, 8, initial))
        assertNull(home.posesAt(WindowMotionPose.at(9.5, 1.6, 1.0)))
        assertEquals(initial, home.returnPoses())
    }

    @Test fun interpolationUsesTheShortQuaternionPathAndKeepsTheGroupRigid() {
        val home = checkNotNull(SummonHome.capture(first, 8, initial))
        val start = initial.getValue(first)
        val destination = WindowMotionPose(listOf(0.0, 1.6, 1.0), listOf(0.0, 0.0, 0.0, -1.0))
        val halfway = start.interpolate(destination, .5)
        assertTrue(halfway.near(WindowMotionPose.at(.5, 1.55, 1.5)))
        val intermediate = checkNotNull(home.posesAt(halfway))
        assertTrue((intermediate.getValue(first).inverse() * intermediate.getValue(second))
            .near(start.inverse() * initial.getValue(second)))
    }
}
