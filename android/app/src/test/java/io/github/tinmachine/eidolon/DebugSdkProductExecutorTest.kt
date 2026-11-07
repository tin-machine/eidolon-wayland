package io.github.tinmachine.eidolon

import java.util.concurrent.Executor
import org.junit.Assert.*
import org.junit.Test

class DebugSdkProductExecutorTest {
    @Test fun queuedBeforeClaimAndSubmittedDuringProbeNeverReplayAfterRestore() {
        val queue = mutableListOf<Runnable>(); val lease = DebugSdkRuntimeLease()
        val executor = DebugSdkProductExecutor(lease, Executor { queue.add(it) })
        var calls = 0
        executor.execute { calls++ }
        val owner = Any(); assertTrue(lease.claim(owner, 1))
        executor.execute { calls += 100 }; assertEquals(1, queue.size)
        assertTrue(lease.normalRetired(owner, 1, true)); assertTrue(lease.createFailed(owner, true))
        assertTrue(lease.restoreCreated(owner, 2)); assertTrue(lease.restored(owner, 2, true))
        queue.removeAt(0).run(); assertEquals(0, calls)
        executor.execute { calls++ }; queue.removeAt(0).run(); assertEquals(1, calls)
    }
}
