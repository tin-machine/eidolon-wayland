package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

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
}
