package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class DebugSdkRuntimeLeaseTest {
    @Test fun ownerClaimsInvalidateQueuedProductIntentsAndOnlyFreshRestoreReopensThem() {
        val lease = DebugSdkRuntimeLease(); val owner = Any(); val old = lease.metadata().generation
        assertTrue(lease.permitsProduct(old)); assertTrue(lease.claim(owner, 5))
        val activeTicket = lease.metadata().generation
        assertFalse(lease.permitsProduct(old)); assertFalse(lease.claim(owner, 5)); assertFalse(lease.claim(Any(), 5))
        assertFalse(lease.normalRetired(Any(), 5, true)); assertFalse(lease.normalRetired(owner, 6, true))
        assertFalse(lease.normalRetired(owner, 5, false)); assertTrue(lease.normalRetired(owner, 5, true))
        assertFalse(lease.created(owner, 0)); assertFalse(lease.created(owner, 5)); assertTrue(lease.created(owner, 6))
        assertEquals(6L, lease.beginClose(owner)); assertFalse(lease.retired(owner, 5, true))
        assertTrue(lease.retired(owner, 6, true)); assertFalse(lease.restored(owner, 7, false))
        assertFalse(lease.restored(owner, 5, true)); assertFalse(lease.restored(owner, 6, true))
        assertFalse(lease.restoreCreated(owner, 5)); assertFalse(lease.restoreCreated(owner, 6)); assertTrue(lease.restoreCreated(owner, 7))
        assertTrue(lease.restored(owner, 7, true)); assertFalse(lease.permitsProduct(old))
        assertFalse(lease.permitsProduct(activeTicket))
        assertTrue(lease.permitsProduct(lease.metadata().generation)); assertFalse(lease.owns(owner))
    }
    @Test fun failedStopKeepsTheSameHandleUntilExplicitOwnerRecovery() {
        val lease = DebugSdkRuntimeLease(); val owner = Any()
        assertTrue(lease.claim(owner, 1)); assertTrue(lease.normalRetired(owner, 1, true)); assertTrue(lease.created(owner, 2))
        assertEquals(2L, lease.beginClose(owner)); assertFalse(lease.retired(owner, 2, false)); lease.cleanupRequired(owner)
        assertTrue(lease.blocksProduct()); assertNull(lease.beginClose(Any())); assertFalse(lease.restored(owner, 3, true))
        assertEquals(2L, lease.metadata().runtime); assertEquals(2L, lease.beginClose(owner))
        assertTrue(lease.retired(owner, 2, true)); assertTrue(lease.restoreCreated(owner, 3)); assertTrue(lease.restored(owner, 3, true))
    }
    @Test fun createFailureNeedsActualEmptyRegistryAndDoesNotForgetAnUnconfirmedOwner() {
        val lease = DebugSdkRuntimeLease(); val owner = Any()
        assertTrue(lease.claim(owner, 1)); assertTrue(lease.normalRetired(owner, 1, true))
        assertFalse(lease.createFailed(owner, false)); assertTrue(lease.blocksProduct())
        assertEquals(0L, lease.beginClose(owner)); assertFalse(lease.retired(owner, 0, false))
        assertTrue(lease.retired(owner, 0, true)); assertTrue(lease.restoreCreated(owner, 3)); assertTrue(lease.restored(owner, 3, true))
    }
    @Test fun aFailedNormalStopCanOnlyBeRecoveredByItsOriginalOwner() {
        val lease = DebugSdkRuntimeLease(); val owner = Any()
        assertTrue(lease.claim(owner, 10)); lease.cleanupRequired(owner)
        assertEquals(10L, lease.beginClose(owner)); assertTrue(lease.retired(owner, 10, true))
        assertTrue(lease.restoreCreated(owner, 11))
        assertFalse(lease.restored(Any(), 11, true)); assertTrue(lease.restored(owner, 11, true))
        assertFalse(lease.retired(owner, 10, true)); assertTrue(lease.claim(Any(), 11))
    }
    @Test fun aZeroCreateWithNoRegistryOwnerCanProceedToRestoration() {
        val lease = DebugSdkRuntimeLease(); val owner = Any()
        assertTrue(lease.claim(owner, 10)); assertTrue(lease.normalRetired(owner, 10, true))
        assertTrue(lease.createFailed(owner, true)); assertEquals(DebugSdkRuntimeLease.Phase.RESTORING, lease.metadata().phase)
        assertTrue(lease.restoreCreated(owner, 11))
        assertFalse(lease.restored(owner, 11, false)); assertTrue(lease.blocksProduct())
        assertTrue(lease.restored(owner, 11, true))
    }
    @Test fun failedRestoreRetainsItsNewHandleUntilItIsRetiredToo() {
        val lease = DebugSdkRuntimeLease(); val owner = Any()
        assertTrue(lease.claim(owner, 1)); assertTrue(lease.normalRetired(owner, 1, true))
        assertTrue(lease.createFailed(owner, true)); assertTrue(lease.restoreCreated(owner, 2))
        assertFalse(lease.restored(owner, 2, false)); lease.cleanupRequired(owner)
        assertEquals(2L, lease.beginClose(owner)); assertFalse(lease.retired(owner, 0, true))
        assertTrue(lease.retired(owner, 2, true)); assertTrue(lease.restoreCreated(owner, 3)); assertTrue(lease.restored(owner, 3, true))
    }
}
