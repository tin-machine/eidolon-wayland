package io.github.tinmachine.eidolon

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SummonChordTest {
    private val chord = SummonChord<String>()

    private fun sample(a: Boolean = false, b: Boolean = false, hit: String? = null, source: Long = 10) =
        chord.sample(source, a, b, hit)

    @Test fun releasesOnlyTheWindowCapturedOnADown() {
        sample()
        assertNull(sample(a = true, hit = "first").action)
        assertNull(sample(a = true, hit = "second").action)
        assertEquals(SummonChord.Action.Summon("first"), sample(hit = "second").action)
        assertNull(sample().action)
    }

    @Test fun noHitStillPermitsReturnAndSuppressesBUntilReleased() {
        sample()
        sample(a = true)
        val result = sample(a = true, b = true)
        assertEquals(SummonChord.Action.Return, result.action)
        assertTrue(result.consumeSecondary)
        assertTrue(sample(b = true).consumeSecondary)
        val released = sample()
        assertTrue(released.consumeSecondary)
        assertNull(released.action)
        assertFalse(sample(b = true).consumeSecondary)
    }

    @Test fun simultaneousDownPrefersReturnAndEitherReleaseOrderIsHarmless() {
        sample()
        assertEquals(SummonChord.Action.Return, sample(a = true, b = true, hit = "window").action)
        val bUp = sample(a = true)
        assertNull(bUp.action)
        assertTrue(bUp.consumeSecondary)
        assertNull(sample(hit = "window").action)
    }

    @Test fun priorSecondaryPressIsNeverReclassifiedAsAChord() {
        sample()
        assertFalse(sample(b = true).consumeSecondary)
        val reverse = sample(a = true, b = true, hit = "window")
        assertNull(reverse.action)
        assertFalse(reverse.consumeSecondary)
        assertNull(sample(a = true, hit = "window").action)
        assertNull(sample().action)
    }

    @Test fun trackingLossDoesNotReplayThePreviousPressOrRelease() {
        sample()
        sample(a = true, hit = "window")
        chord.invalidate()
        assertNull(sample(a = true, hit = "other").action)
        assertNull(sample().action)
        sample(a = true, hit = "other")
        assertEquals(SummonChord.Action.Summon("other"), sample().action)
    }

    @Test fun aReplacementControllerMustReachNeutralBeforeArming() {
        sample()
        sample(a = true, hit = "old")
        assertNull(sample(a = true, hit = "new", source = 11).action)
        assertNull(sample(source = 11).action)
        sample(a = true, hit = "new", source = 11)
        assertEquals(SummonChord.Action.Summon("new"), sample(source = 11).action)
    }
}
