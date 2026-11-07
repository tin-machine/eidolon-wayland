package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class ProcessCpuTrackerTest {
    private fun value(tracker: ProcessCpuTracker, time: Long, cpu: Long?) = tracker.sample(time) { cpu }
    private fun percent(expected: Double, reading: ProcessCpuReading) {
        assertEquals(ProcessCpuStatus.AVAILABLE, reading.status)
        assertEquals(expected, checkNotNull(reading.percent), 0.000001)
    }

    @Test fun stoppedCollectorDoesNotReadAndFirstCounterDoesNotInventZero() {
        val tracker = ProcessCpuTracker()
        val stopped = tracker.sample(0) { fail("inactive CPU provider called"); 0 }
        assertEquals(ProcessCpuStatus.STOPPED, stopped.status)
        tracker.start()
        val first = value(tracker, 100, 4000)
        assertEquals(ProcessCpuStatus.WARMING_UP, first.status)
        assertNull(first.percent); assertNull(first.intervalMs)
        assertEquals(100L, first.sampledMonotonicMs)
        percent(50.0, value(tracker, 5100, 6500))
    }

    @Test fun validIntervalsDistinguishZeroFiftyAndMulticoreTwoHundredFiftyPercent() {
        for ((delta, expected) in listOf(0L to 0.0, 2500L to 50.0, 12500L to 250.0)) {
            val tracker = ProcessCpuTracker(); tracker.start()
            value(tracker, 0, 600)
            val reading = value(tracker, 5000, 600 + delta)
            percent(expected, reading)
            assertEquals(5000L, reading.intervalMs)
            assertEquals(5000L, reading.sampledMonotonicMs)
        }
    }

    @Test fun frequentBatteryNotificationsDoNotReadCpuOrRefreshCpuTimestamp() {
        val tracker = ProcessCpuTracker(); tracker.start()
        var calls = 0
        val baseline = tracker.sample(0) { calls++; 100 }
        for (time in listOf(0L, 1L, 1000L, 4999L)) assertSame(baseline, tracker.sample(time) { calls++; 999999 })
        assertEquals(1, calls)
        val ready = tracker.sample(5000) { calls++; 350 }
        percent(5.0, ready)
        for (time in listOf(5000L, 5001L, 9999L)) assertSame(ready, tracker.sample(time) { calls++; 999999 })
        assertEquals(2, calls)
        assertEquals(5000L, ready.sampledMonotonicMs)
        percent(10.0, tracker.sample(10000) { calls++; 850 })
        assertEquals(3, calls)
    }

    @Test fun repeatedStartIsIdempotentButStopAndResumeRequireNewBaseline() {
        val tracker = ProcessCpuTracker(); tracker.start()
        value(tracker, 0, 100)
        tracker.start()
        percent(20.0, value(tracker, 5000, 1100))
        val stopped = tracker.stop()
        assertEquals(ProcessCpuStatus.STOPPED, stopped.status)
        assertNull(stopped.percent); assertNull(stopped.sampledMonotonicMs)
        assertEquals(stopped, tracker.stop())
        assertSame(stopped, tracker.sample(500000) { fail("stopped CPU provider called"); 999999 })
        tracker.start()
        val resumed = value(tracker, 1000000, 900000)
        assertEquals(ProcessCpuStatus.WARMING_UP, resumed.status)
        assertNull(resumed.percent)
        percent(10.0, value(tracker, 1005000, 900500))
    }

    @Test fun backwardsClockInvalidatesInsteadOfDividingOrReusingOldValue() {
        val tracker = ProcessCpuTracker(); tracker.start()
        value(tracker, 10000, 0)
        percent(20.0, value(tracker, 15000, 1000))
        val invalid = tracker.sample(14999) { fail("backwards clock read CPU"); 1001 }
        assertEquals(ProcessCpuStatus.UNAVAILABLE, invalid.status)
        assertNull(invalid.percent); assertNull(invalid.sampledMonotonicMs)
        assertEquals(ProcessCpuStatus.WARMING_UP, value(tracker, 15000, 1001).status)
        percent(0.0, value(tracker, 20000, 1001))
    }

    @Test fun clockRegressionBetweenEarlyNotificationsAlsoInvalidatesTheBaseline() {
        val tracker = ProcessCpuTracker(); tracker.start()
        value(tracker, 0, 100)
        tracker.sample(4000) { fail("early notification read CPU"); 200 }
        val invalid = tracker.sample(3999) { fail("regressing notification read CPU"); 200 }
        assertEquals(ProcessCpuStatus.UNAVAILABLE, invalid.status)
        assertNull(invalid.percent)
        assertEquals(ProcessCpuStatus.WARMING_UP, value(tracker, 4000, 200).status)
        percent(10.0, value(tracker, 9000, 700))
    }

    @Test fun negativeAndWrappedClocksCannotCreateAnOverflowInterval() {
        for (time in listOf(-1L, Long.MIN_VALUE)) {
            val tracker = ProcessCpuTracker(); tracker.start()
            value(tracker, Long.MAX_VALUE - 5000, 10)
            val invalid = tracker.sample(time) { fail("invalid clock read CPU"); 20 }
            assertEquals(ProcessCpuStatus.UNAVAILABLE, invalid.status)
            assertNull(invalid.percent)
            assertEquals(ProcessCpuStatus.WARMING_UP, value(tracker, 0, 100).status)
            percent(2.0, value(tracker, 5000, 200))
        }
    }

    @Test fun cpuReadFailureDoesNotBecomeZeroAndNeedsTwoFreshCounters() {
        for (throws in listOf(false, true)) {
            val tracker = ProcessCpuTracker(); tracker.start()
            value(tracker, 0, 500)
            val failed = tracker.sample(5000) { if (throws) error("fixture read failure") else null }
            assertEquals(ProcessCpuStatus.UNAVAILABLE, failed.status)
            assertNull(failed.percent)
            assertSame(failed, tracker.sample(9999) { fail("read retried before sampling interval"); 0 })
            assertEquals(ProcessCpuStatus.WARMING_UP, value(tracker, 10000, 10000).status)
            percent(20.0, value(tracker, 15000, 11000))
        }
    }

    @Test fun negativeOrDecreasingCpuCounterDiscardsTheBaseline() {
        for (bad in listOf(-1L, Long.MIN_VALUE, 999L)) {
            val tracker = ProcessCpuTracker(); tracker.start()
            value(tracker, 0, 1000)
            val invalid = value(tracker, 5000, bad)
            assertEquals(ProcessCpuStatus.UNAVAILABLE, invalid.status)
            assertNull(invalid.percent)
            assertEquals(ProcessCpuStatus.WARMING_UP, value(tracker, 10000, 9000).status)
            percent(20.0, value(tracker, 15000, 10000))
        }
    }

    @Test fun delayedSamplesReportActualIntervalUntilTheBoundThenBecomeStale() {
        val tracker = ProcessCpuTracker(); tracker.start()
        value(tracker, 0, 0)
        val boundary = value(tracker, 15000, 3000)
        percent(20.0, boundary)
        assertEquals(15000L, boundary.intervalMs)
        val stale = value(tracker, 30001, 5000)
        assertEquals(ProcessCpuStatus.STALE, stale.status)
        assertNull(stale.percent)
        assertEquals(15001L, stale.intervalMs)
        assertEquals(30001L, stale.sampledMonotonicMs)
        percent(10.0, value(tracker, 35001, 5500))
    }

    @Test fun preciseSmallDeltasSurviveLargeAbsoluteCountersAndClockValues() {
        val tracker = ProcessCpuTracker(); tracker.start()
        value(tracker, Long.MAX_VALUE - 10000, Long.MAX_VALUE - 5000)
        val reading = value(tracker, Long.MAX_VALUE - 5000, Long.MAX_VALUE - 2500)
        percent(50.0, reading)
        percent(50.0, value(tracker, Long.MAX_VALUE, Long.MAX_VALUE))
    }

    @Test fun cpuMultiplicationCannotOverflowToNegativeOrNonFinite() {
        val tracker = ProcessCpuTracker(); tracker.start()
        value(tracker, 0, 0)
        val reading = value(tracker, 5000, Long.MAX_VALUE)
        assertEquals(ProcessCpuStatus.AVAILABLE, reading.status)
        val percent = checkNotNull(reading.percent)
        assertTrue(percent.isFinite()); assertTrue(percent > 100.0)
        assertEquals(Long.MAX_VALUE.toDouble() / 5000.0 * 100.0, percent, 0.0)
    }
}
