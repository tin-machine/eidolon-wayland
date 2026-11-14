package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class SdkEvidenceBridgeTest {
    private fun id(gen: Long = 1, adapter: Long = 11) =
        SdkEvidenceBridge.Lease(7, 2, 3, 4, gen, gen, adapter)

    @Test fun noEvidenceFromUnresolvedRegistrationOrDestroyRequestAlone() {
        val ledger = SdkEvidenceBridge()
        val key = id()
        assertTrue(ledger.reserve(key))
        ledger.sealRuntime(7)
        assertFalse(ledger.runtimeProgress(7).allRetired)
        ledger.registrationRejected(key)
        assertTrue(ledger.runtimeProgress(7).allRetired)

        val other = SdkEvidenceBridge()
        assertTrue(other.reserve(key))
        other.registered(key)
        other.entityCreated(key)
        other.destroyRequested(key)
        other.rendererDetached(key)
        other.nativeBindingReleased(key)
        other.auxiliaryDisposed(key)
        other.sealRuntime(7)
        assertFalse("Entity.destroy() return is not an observed SDK deletion",
            other.runtimeProgress(7).allRetired)
        other.sdkCleanupPass(11, 4, 1)
        other.deleteObserved(key, 1)
        assertFalse(other.runtimeProgress(7).allRetired)
        other.sdkCleanupPass(11, 4, 2)
        assertTrue(other.runtimeProgress(7).allRetired)
    }

    @Test fun callbacksAndFutureProducersBlockRetirementAndCannotReenterAfterSeal() {
        val ledger = SdkEvidenceBridge()
        val key = id()
        assertTrue(ledger.reserve(key))
        ledger.registered(key)
        assertTrue(ledger.beginProducer(key))
        val callback = requireNotNull(ledger.enterCallback(key))
        assertTrue(callback.valid())
        ledger.rendererDetached(key)
        ledger.nativeBindingReleased(key)
        ledger.sealRuntime(7)
        assertNull(ledger.enterCallback(key))
        assertFalse(ledger.beginProducer(key))
        assertFalse(callback.valid())
        assertEquals(2, ledger.runtimeProgress(7).pendingCallbacks)
        callback.close()
        callback.close()
        ledger.producerFinished(key)
        assertTrue(ledger.runtimeProgress(7).allRetired)
    }

    @Test fun generationsAreNotEvictedAndSceneAndAdapterMustMatch() {
        val ledger = SdkEvidenceBridge()
        val all = (1L..40L).map { id(it) }
        all.forEach {
            assertTrue(ledger.reserve(it))
            ledger.registrationRejected(it)
        }
        val first = all.first()
        ledger.sealScene(11, 4)
        assertEquals(40, ledger.sceneProgress(11, 4).issued)
        assertTrue(ledger.sceneProgress(11, 4).allRetired)
        assertFalse(ledger.reserve(id(41)))
        assertTrue("different adapter must not share Scene ownership",
            ledger.reserve(id(1, adapter = 22)))
        assertFalse("runtime not sealed", ledger.runtimeProgress(7).allRetired)
        ledger.sealRuntime(7)
        assertFalse("different adapter pending registration must not be hidden",
            ledger.runtimeProgress(7).allRetired)
        ledger.registrationRejected(id(1, adapter = 22))
        assertTrue(ledger.runtimeProgress(7).allRetired)
    }

    @Test fun failedDisposalIsNotTurnedIntoSuccessByEmptySnapshots() {
        val ledger = SdkEvidenceBridge()
        val key = id()
        ledger.reserve(key)
        ledger.registered(key)
        ledger.rendererDetached(key)
        ledger.nativeBindingReleased(key)
        ledger.failed(key)
        ledger.sealRuntime(7)
        val status = ledger.runtimeProgress(7)
        assertEquals(1, status.failures)
        assertFalse(status.allRetired)
        assertFalse(SdkEvidenceBridge().runtimeProgress(7).allRetired)
    }

    @Test fun retirementTicketsAreCaptureScopedAndCannotBeReplayed() {
        val hub = SdkRetirementHub()
        val lease = id()
        hub.evidence.reserve(lease)
        hub.evidence.registrationRejected(lease)
        val ticket = hub.retireRuntime(7, 9, "capture-A")
        assertTrue(hub.progress(ticket).allRetired)
        assertSame(ticket, hub.retireRuntime(7, 9, "capture-A"))
        assertNotSame(ticket, hub.retireRuntime(7, 10, "capture-B"))
        assertFalse(hub.evidence.reserve(id(2)))
    }

    @Test fun everyChildAndProducerMustFinishBeforeSynchronousNativeRelease() {
        val ledger=SdkEvidenceBridge();val lease=id()
        assertTrue(ledger.reserve(lease));ledger.registered(lease)
        for(entity in listOf(100L,101L,102L)) ledger.entityCreated(lease,entity)
        assertTrue(ledger.beginProducer(lease))
        ledger.sealLease(lease);ledger.rendererDetached(lease);ledger.auxiliaryDisposed(lease)
        ledger.sdkCleanupPass(11,4,1)
        for(entity in listOf(100L,101L)) {
            ledger.destroyRequested(lease,entity);ledger.deleteObserved(lease,1,entity)
        }
        ledger.sdkCleanupPass(11,4,2)
        assertFalse("One auxiliary Entity still owns SDK resources",ledger.readyToRelease(lease))
        ledger.destroyRequested(lease,102);ledger.deleteObserved(lease,2,102)
        ledger.sdkCleanupPass(11,4,3)
        assertFalse("Late SceneObject producer is still pending",ledger.readyToRelease(lease))
        ledger.producerFinished(lease)
        assertTrue("Candidate must not wait on its own native receipt",ledger.readyToRelease(lease))
        assertFalse(ledger.complete(lease))
        ledger.nativeBindingReleased(lease)
        assertTrue(ledger.collectCompleted(lease))
        assertFalse("Collected receipt cannot be reused",ledger.readyToRelease(lease))
    }

    @Test fun otherScenePassCannotFinishDeletionAndSamePassDoesNotComplete() {
        val ledger=SdkEvidenceBridge();val lease=id()
        ledger.reserve(lease);ledger.registered(lease);ledger.entityCreated(lease,91)
        ledger.sealLease(lease);ledger.rendererDetached(lease);ledger.auxiliaryDisposed(lease)
        ledger.destroyRequested(lease,91);ledger.sdkCleanupPass(11,4,1);ledger.deleteObserved(lease,1,91)
        ledger.sdkCleanupPass(11,5,100)
        assertFalse(ledger.readyToRelease(lease))
        ledger.sdkCleanupPass(11,4,2)
        assertTrue(ledger.readyToRelease(lease))
    }

    @Test fun completedGenerationCollectionDoesNotEvictAnOlderPendingOwner() {
        val ledger=SdkEvidenceBridge();val pending=id()
        ledger.reserve(pending);ledger.registered(pending)
        for(generation in 2L..1002L) {
            val lease=id(generation)
            ledger.reserve(lease);ledger.registrationRejected(lease);ledger.sealLease(lease)
            assertTrue(ledger.collectCompleted(lease))
            assertFalse(ledger.collectCompleted(lease))
        }
        ledger.sealRuntime(7)
        val progress=ledger.runtimeProgress(7)
        assertEquals(1002,progress.issued)
        assertEquals(1001,progress.complete)
        assertFalse(progress.allRetired)
        assertFalse(ledger.reserve(id(1003)))
        ledger.rendererDetached(pending);ledger.nativeBindingReleased(pending)
        assertTrue(ledger.runtimeProgress(7).allRetired)
    }

    @Test fun lateFutureCannotReuseDeletionPassThatAlreadyFinished() {
        val ledger=SdkEvidenceBridge();val lease=id()
        ledger.reserve(lease);ledger.registered(lease);ledger.entityCreated(lease,91)
        ledger.beginProducer(lease)
        ledger.sealLease(lease);ledger.rendererDetached(lease);ledger.auxiliaryDisposed(lease)
        ledger.destroyRequested(lease,91);ledger.sdkCleanupPass(11,4,1);ledger.deleteObserved(lease,1,91)
        ledger.sdkCleanupPass(11,4,2)
        ledger.lateObjectCleanupRequested(lease)
        ledger.producerFinished(lease)
        assertFalse(ledger.readyToRelease(lease))
        ledger.sdkCleanupPass(11,4,3)
        assertFalse("The request may follow SDK cleanup work in this interval",ledger.readyToRelease(lease))
        ledger.sdkCleanupPass(11,5,200)
        assertFalse("A new Scene cannot finish the old late result",ledger.readyToRelease(lease))
        ledger.sdkCleanupPass(11,4,4)
        assertTrue(ledger.readyToRelease(lease))
    }

    @Test fun diagnosticLegacyAndV3GenerationsAllBelongToTheRuntimeTicket() {
        val hub = SdkRetirementHub(); val ledger = hub.evidence
        val diagnostic = SdkEvidenceBridge.Lease(7, 1, 1, 4, 1, null, 11)
        val oldDiagnostic = diagnostic.copy(scene = 3, generation = 8)
        val legacy = SdkEvidenceBridge.Lease(7, 2, 5, 4, 9, null, 11)
        val v3 = id()
        val leases = listOf(diagnostic, oldDiagnostic, legacy, v3)
        for ((index, lease) in leases.withIndex()) {
            assertTrue(ledger.reserve(lease)); ledger.registered(lease)
            ledger.entityCreated(lease, 101L + index)
        }
        val ticket = hub.retireRuntime(7, 3, "captured-settings")
        assertEquals(4, hub.progress(ticket).issued)
        assertFalse(ledger.reserve(diagnostic.copy(generation = 2)))
        ledger.sdkCleanupPass(11, 4, 1); ledger.sdkCleanupPass(11, 3, 1)
        for ((index, lease) in leases.withIndex()) {
            ledger.rendererDetached(lease); ledger.auxiliaryDisposed(lease)
            ledger.destroyRequested(lease, 101L + index)
            ledger.deleteObserved(lease, 1, 101L + index)
        }
        ledger.sdkCleanupPass(11, 4, 2)
        for (lease in listOf(diagnostic, legacy, v3)) {
            assertTrue(ledger.readyToRelease(lease)); assertFalse(ledger.complete(lease))
            ledger.nativeBindingReleased(lease); assertTrue(ledger.collectCompleted(lease))
        }
        assertEquals(3, hub.progress(ticket).complete)
        assertFalse("Current Scene completion must not hide the old diagnostic generation", hub.progress(ticket).allRetired)
        ledger.sdkCleanupPass(11, 4, 3)
        assertFalse(ledger.readyToRelease(oldDiagnostic))
        ledger.sdkCleanupPass(11, 3, 2)
        assertTrue(ledger.readyToRelease(oldDiagnostic))
        assertFalse("Native release must actually be accepted too", hub.progress(ticket).allRetired)
        ledger.nativeBindingReleased(oldDiagnostic); assertTrue(ledger.collectCompleted(oldDiagnostic))
        assertEquals(4, hub.progress(ticket).complete)
        assertTrue(hub.progress(ticket).allRetired)
    }

    @Test fun completedLegacyGenerationCannotRebindItsOldCallbacksToAnotherAllocation() {
        val ledger = SdkEvidenceBridge()
        val legacy = SdkEvidenceBridge.Lease(7, 1, 1, 4, 8, null, 11)
        assertTrue(ledger.reserve(legacy)); ledger.registered(legacy)
        ledger.sealLease(legacy); ledger.rendererDetached(legacy); ledger.nativeBindingReleased(legacy)
        assertTrue(ledger.collectCompleted(legacy))
        assertFalse(ledger.reserve(legacy))
        assertFalse(ledger.reserve(legacy.copy(generation = 7)))
        assertFalse("Changing the optional Create receipt does not create a new native target",
            ledger.reserve(legacy.copy(createCommand = 99)))
        assertNull(ledger.enterCallback(legacy)); assertFalse(ledger.beginProducer(legacy))
        assertTrue(ledger.reserve(legacy.copy(generation = 9)))
        assertNull("The prior scope cannot acquire the next generation", ledger.enterCallback(legacy))
    }

    @Test fun rejectedRegistrationCanRetryOnlyIfItHadNoSdkOrNativeEffects() {
        val ledger = SdkEvidenceBridge()
        val legacy = SdkEvidenceBridge.Lease(7, 1, 1, 4, 1, null, 11)
        assertTrue(ledger.reserve(legacy)); ledger.registrationRejected(legacy); ledger.sealLease(legacy)
        assertTrue(ledger.collectCompleted(legacy)); assertTrue(ledger.reserve(legacy))
        ledger.entityCreated(legacy, 44)
        assertThrows(IllegalArgumentException::class.java) { ledger.registrationRejected(legacy) }
        ledger.sealRuntime(7)
        assertFalse(ledger.runtimeProgress(7).allRetired)
    }

    @Test fun newRuntimeAndForgedTicketCannotReplaceTheOldRuntimeCapture() {
        val hub = SdkRetirementHub(); val ledger = hub.evidence
        val old = SdkEvidenceBridge.Lease(7, 1, 1, 4, 1, null, 11)
        ledger.reserve(old); ledger.registered(old)
        val original = hub.retireRuntime(7, 9, "old-save")
        val next = hub.retireRuntime(8, 10, "new-save")
        assertTrue(hub.progress(next).allRetired)
        assertFalse("A sealed empty new Runtime cannot prove the old owner disappeared", hub.progress(original).allRetired)
        for (forged in listOf(original.copy(), original.copy(runtime = 8), original.copy(admissionEpoch = 10),
            original.copy(captureToken = "new-save"))) {
            assertThrows(IllegalArgumentException::class.java) { hub.progress(forged) }
        }
        assertFalse(hub.progress(original).allRetired)
    }

    @Test fun independentCallbackAndFutureCompletionProgressWhileSaveWorkerObservesPending() {
        val hub = SdkRetirementHub(); val ledger = hub.evidence
        val legacy = SdkEvidenceBridge.Lease(7, 1, 1, 4, 1, null, 11)
        ledger.reserve(legacy); ledger.registered(legacy)
        val callback = checkNotNull(ledger.enterCallback(legacy))
        assertTrue(ledger.beginProducer(legacy))
        val ticket = hub.retireRuntime(7, 11, "save")
        ledger.rendererDetached(legacy)
        val pool = Executors.newFixedThreadPool(2)
        val workerObserved = CountDownLatch(1); val drained = CountDownLatch(1)
        try {
            val worker = pool.submit<Boolean> {
                assertEquals(2, hub.progress(ticket).pendingCallbacks)
                assertFalse(callback.valid())
                workerObserved.countDown()
                check(drained.await(5, TimeUnit.SECONDS))
                hub.progress(ticket).allRetired
            }
            val uiCallback = pool.submit {
                check(workerObserved.await(5, TimeUnit.SECONDS))
                callback.close(); ledger.producerFinished(legacy)
                assertTrue(ledger.readyToRelease(legacy))
                ledger.nativeBindingReleased(legacy); assertTrue(ledger.collectCompleted(legacy))
                drained.countDown()
            }
            uiCallback.get(5, TimeUnit.SECONDS)
            assertTrue(worker.get(5, TimeUnit.SECONDS))
        } finally { pool.shutdownNow(); check(pool.awaitTermination(5, TimeUnit.SECONDS)) }
    }
}
