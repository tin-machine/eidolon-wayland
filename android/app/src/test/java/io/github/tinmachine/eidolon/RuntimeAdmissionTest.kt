package io.github.tinmachine.eidolon

import java.util.concurrent.Executor
import org.junit.Assert.*
import org.junit.Test

class RuntimeAdmissionTest {
    @Test fun queuedWorkCannotCrossEitherSideOfTheSaveEpoch() {
        val admission = RuntimeAdmission()
        val before = checkNotNull(admission.ticket())
        assertTrue(admission.permits(before))
        val owner = admission.begin("captured-settings")
        assertFalse(admission.open())
        assertNull(admission.ticket())
        assertFalse(admission.permits(before))
        assertTrue(admission.owns(owner))
        assertSame(owner, admission.activeOwner())
        assertThrows(IllegalStateException::class.java) { admission.begin("parallel-save") }
        admission.finish(owner)
        val after = checkNotNull(admission.ticket())
        assertTrue(after > owner.epoch)
        assertTrue(admission.permits(after))
        assertFalse(admission.permits(before))
        assertFalse(admission.owns(owner))
        assertNull(admission.activeOwner())
    }

    @Test fun sameValuesDoNotTransferSaveOwnershipToAnotherObject() {
        val admission = RuntimeAdmission()
        val owner = admission.begin("same-capture")
        val impostor = RuntimeAdmission.Owner(owner.epoch, owner.captureToken)
        assertFalse(admission.owns(impostor))
        assertThrows(IllegalStateException::class.java) { admission.finish(impostor) }
        assertTrue(admission.owns(owner))
        admission.finish(owner)
    }

    @Test fun unknownOldOwnerCannotBecomeAnUnusedRuntimeOrAnOpenEpoch() {
        val admission = RuntimeAdmission()
        val before = checkNotNull(admission.ticket())
        val owner = admission.begin("old-settings")
        admission.retainUnknownOwner()
        assertFalse(admission.owns(owner))
        assertFalse(admission.permits(before))
        assertFalse(admission.open())
        assertNull(admission.ticket())
        assertNull(admission.activeOwner())
        assertThrows(IllegalStateException::class.java) { admission.finish(owner) }
        assertThrows(IllegalStateException::class.java) { admission.begin("new-runtime-zero-counters") }
    }

    @Test fun productRequestsQueuedBeforeSaveOrSubmittedDuringSaveNeverReplayAfterIt() {
        val admission = RuntimeAdmission()
        val queue = mutableListOf<Runnable>()
        val executor = DebugSdkProductExecutor(DebugSdkRuntimeLease(), Executor { queue += it }, admission)
        var calls = 0
        executor.execute { calls++ }
        val owner = admission.begin("save-one")
        executor.execute { calls += 100 }
        assertEquals(1, queue.size)
        admission.finish(owner)
        queue.removeAt(0).run()
        assertEquals(0, calls)
        executor.execute { calls++ }
        queue.removeAt(0).run()
        assertEquals(1, calls)
    }

    @Test fun delayedRecoveryClickCannotTransferToTheNextSaveEvenWithTheSameCaptureText() {
        val admission = RuntimeAdmission()
        val first = admission.begin("identical-profile")
        val clickedOwner = checkNotNull(admission.activeOwner())
        admission.finish(first)
        val next = admission.begin("identical-profile")
        assertFalse(admission.owns(clickedOwner))
        assertNotSame(clickedOwner, next)
        assertSame(next, admission.activeOwner())
    }
}
