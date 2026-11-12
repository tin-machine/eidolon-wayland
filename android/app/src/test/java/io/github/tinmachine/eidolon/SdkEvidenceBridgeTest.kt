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
        assertFalse(other.runtimeProgress(7).allRetired,
            "Entity.destroy() return is not an observed SDK deletion")
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
        assertTrue(ledger.reserve(id(1, adapter = 22)),
            "different adapter must not share Scene ownership")
        assertFalse(ledger.runtimeProgress(7).allRetired, "runtime not sealed")
        ledger.sealRuntime(7)
        assertFalse(ledger.runtimeProgress(7).allRetired,
            "different adapter pending registration must not be hidden")
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
}
