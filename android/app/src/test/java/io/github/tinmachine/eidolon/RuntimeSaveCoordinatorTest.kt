package io.github.tinmachine.eidolon

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class RuntimeSaveCoordinatorTest {
    private val capture = "c".repeat(64)
    private fun complete(runtime: Long = 11) = NativeReclamation(runtime, true, false, false, true,
        0, 0, 0, 0, 0, 0, 0, false, false)
    private class Port(var observation: NativeReclamation) : RuntimeSaveCoordinator.Port {
        var time = 0L
        var runtime = 11L
        var stopResult = 0
        var destroyResult = 0
        var failObserve = false
        val calls = mutableListOf<String>()
        override fun now() = time
        override fun currentRuntime() = runtime
        override fun observe(runtime: Long): NativeReclamation {
            check(!failObserve) { "injected observation failure" }
            return observation
        }
        override fun stopOrigin(runtime: Long): Boolean { calls += "origin-stop"; return true }
        override fun connectForRecovery(runtime: Long): Boolean { calls += "recovery-connect"; return true }
        override fun recoverOrigin(runtime: Long): Boolean { calls += "origin-recover"; return true }
        override fun requestRuntimeStop(runtime: Long): Int { calls += "runtime-stop"; return stopResult }
        override fun destroyRuntime(runtime: Long): Int { calls += "native-destroy"; return destroyResult }
        override fun runtimeDestroyed(runtime: Long) {
            check(this.runtime == runtime)
            calls += "destroy-accepted"
            this.runtime = 0
        }
    }
    private inner class Fixture(initial: NativeReclamation = complete().copy(originRetired = false,
        shutdownComplete = false, connections = 1, workers = 2, sdkBindings = 1)) {
        val admission = RuntimeAdmission()
        val hub = SdkRetirementHub()
        val port = Port(initial)
        val owner = RuntimeSaveCoordinator(admission, hub, port, timeoutMillis = 100)
        var publications = 0
        fun begin() = owner.begin(11, capture) { barrier ->
            barrier.withReclaimed(capture) { evidence ->
                assertTrue(evidence.fullyReclaimed)
                assertEquals(11, evidence.stoppedRuntime)
                assertFalse(admission.open())
                assertEquals(0, port.runtime)
                port.calls += "publish"
                publications++
            }
        }
    }

    @Test fun originCleanupPrecedesWholeRuntimeStopAndActualDestroyPrecedesPublication() {
        val f = Fixture()
        val operation = f.begin()
        f.owner.poll()
        assertEquals(listOf("origin-stop"), f.port.calls)
        assertEquals(0, f.publications)
        // The controller remains alive after origin retirement; diagnostic resources still exist.
        f.port.observation = f.port.observation.copy(originRetired = true)
        f.owner.poll()
        assertEquals(listOf("origin-stop", "runtime-stop"), f.port.calls)
        assertEquals(RuntimeSaveCoordinator.Phase.RUNTIME_STOPPING, operation.phase)
        f.port.observation = complete()
        f.owner.poll()
        assertEquals(listOf("origin-stop", "runtime-stop", "native-destroy", "destroy-accepted", "publish"), f.port.calls)
        assertEquals(1, f.publications)
        assertTrue(f.admission.open())
        assertEquals(RuntimeSaveCoordinator.Phase.COMMITTED, operation.phase)
        f.owner.poll()
        assertEquals(1, f.publications)
    }

    @Test fun everySingleRemainingResourceAndRemoteOrLocalPendingPreventsAnySave() {
        val f = Fixture(complete().copy(shutdownComplete = false))
        f.begin(); f.owner.poll()
        val remaining = listOf(
            complete().copy(connections = 1), complete().copy(workers = 1), complete().copy(nativeTargets = 1),
            complete().copy(panels = 1), complete().copy(sdkBindings = 1), complete().copy(boardWindows = 1),
            complete().copy(pendingOperations = 1), complete().copy(remoteCleanupPending = true),
            complete().copy(localCleanupPending = true), complete().copy(originRetired = false),
            complete().copy(shutdownComplete = false),
        )
        remaining.forEachIndexed { index, evidence ->
            f.port.observation = evidence
            f.owner.poll()
            assertEquals("case $index", 0, f.publications)
            assertFalse("case $index", "native-destroy" in f.port.calls)
            assertFalse(f.admission.open())
        }
        f.port.observation = complete(); f.owner.poll()
        assertEquals(1, f.publications)
    }

    @Test fun remoteUnknownKeepsControllerAndCaptureUntilExplicitLiveRecoverAndFinalizer() {
        val f = Fixture(complete().copy(originRetired = false, shutdownComplete = false, remoteCleanupPending = true))
        val operation = f.begin()
        f.owner.poll()
        f.port.time = 101; f.owner.poll()
        assertEquals(RuntimeSaveCoordinator.Phase.RECOVERY_REQUIRED, operation.phase)
        assertEquals("SAVE_REMOTE_PENDING", operation.firstFailure)
        assertEquals(listOf("origin-stop"), f.port.calls)
        assertEquals(capture, operation.owner.captureToken)
        f.port.observation = f.port.observation.copy(recoveryConnectAllowed = true)
        f.owner.poll() // New available evidence does not auto-recover.
        assertEquals(listOf("origin-stop"), f.port.calls)
        assertTrue(f.owner.continueReclamation())
        f.owner.poll()
        assertEquals("recovery-connect", f.port.calls.last())
        f.port.observation = f.port.observation.copy(recoveryConnectAllowed = false, recoveryCommandAllowed = true)
        f.owner.poll()
        assertEquals("origin-recover", f.port.calls.last())
        assertFalse("runtime-stop" in f.port.calls)
        // Remote completion alone is insufficient while local finalizer/origin proof is absent.
        f.port.observation = f.port.observation.copy(remoteCleanupPending = false, localCleanupPending = true)
        f.owner.poll()
        assertEquals(0, f.publications)
        assertFalse("runtime-stop" in f.port.calls)
        f.port.observation = complete().copy(shutdownComplete = false)
        f.owner.poll()
        assertEquals("runtime-stop", f.port.calls.last())
        f.port.observation = complete(); f.owner.poll()
        assertEquals(1, f.publications)
        assertEquals("SAVE_REMOTE_PENDING", operation.firstFailure)
        assertNull(operation.cleanupFailure)
    }

    @Test fun sdkDisposalAndOldSceneLateCallbacksContinueBetweenNonblockingPolls() {
        val f = Fixture(complete().copy(shutdownComplete = false))
        val lease = SdkEvidenceBridge.Lease(11, 7, 9, 2, 1, null, 4) // Diagnostic/legacy owner uses the same ledger.
        val evidence = f.hub.evidence
        assertTrue(evidence.reserve(lease)); evidence.registered(lease); evidence.entityCreated(lease, 44)
        assertTrue(evidence.beginProducer(lease))
        val callback = checkNotNull(evidence.enterCallback(lease))
        f.begin(); f.owner.poll() // Seal is synchronous; disposal itself is performed independently.
        f.port.observation = complete(); f.owner.poll()
        assertFalse(callback.valid())
        assertEquals(0, f.publications)
        assertFalse("native-destroy" in f.port.calls)
        evidence.rendererDetached(lease)
        evidence.destroyRequested(lease, 44)
        evidence.deleteObserved(lease, 0, 44)
        evidence.auxiliaryDisposed(lease)
        evidence.lateObjectCleanupRequested(lease)
        evidence.sdkCleanupPass(4, 3, 100) // A different Scene cannot satisfy the old deletion fence.
        callback.close(); evidence.producerFinished(lease)
        f.owner.poll(); assertEquals(0, f.publications)
        evidence.sdkCleanupPass(4, 2, 1)
        f.owner.poll(); assertEquals(0, f.publications)
        evidence.sdkCleanupPass(4, 2, 2)
        assertTrue(evidence.readyToRelease(lease))
        evidence.nativeBindingReleased(lease)
        assertTrue(evidence.collectCompleted(lease))
        f.owner.poll()
        assertEquals(1, f.publications)
    }

    @Test fun sdkDisposeFailureAndNativeDestroyFailureNeverPublish() {
        val f = Fixture(complete().copy(shutdownComplete = false))
        val lease = SdkEvidenceBridge.Lease(11, 7, 9, 2, 1, null, 4)
        assertTrue(f.hub.evidence.reserve(lease)); f.hub.evidence.registrationRejected(lease)
        f.hub.evidence.failed(lease)
        val operation = f.begin(); f.owner.poll()
        f.port.observation = complete(); f.owner.poll()
        assertEquals("SAVE_SDK_DISPOSAL_FAILED", operation.firstFailure)
        assertEquals(RuntimeSaveCoordinator.Phase.SAVE_PENDING, operation.phase)
        assertFalse(operation.canRetryReclamation)
        assertFalse(f.owner.continueReclamation())
        assertEquals(0, f.publications)
        assertFalse("native-destroy" in f.port.calls)
        for (code in listOf(1, 5)) { // STALE and FAILED cannot substitute for Accepted.
            val rejected = Fixture(complete().copy(shutdownComplete = false))
            val op = rejected.begin(); rejected.owner.poll()
            rejected.port.observation = complete(); rejected.port.destroyResult = code
            rejected.owner.poll()
            assertEquals("SAVE_DESTROY_REJECTED", op.firstFailure)
            assertFalse(op.canRetryReclamation)
            assertFalse(rejected.owner.continueReclamation())
            assertEquals(0, rejected.publications)
            assertEquals(11, rejected.port.runtime)
            assertFalse(rejected.admission.open())
        }
    }

    @Test fun timeoutAndBusyKeepReceiptAbsentAndNeverConvertZeroHandleToReclaimed() {
        val f = Fixture(complete().copy(shutdownComplete = false))
        val operation = f.begin(); f.owner.poll()
        f.port.observation = complete(); f.port.destroyResult = 2
        f.owner.poll()
        assertNull(operation.receipt)
        f.port.time = 101; f.owner.poll()
        assertEquals("SAVE_RUNTIME_PENDING", operation.firstFailure)
        assertTrue(operation.canRetryReclamation)
        assertEquals(0, f.publications)
        f.port.runtime = 0 // Lost/destroyed-by-another owner is not this owner's StopReceipt.
        assertFalse(f.owner.continueReclamation())
        assertFalse(f.owner.resumePublication(capture, capture) { error("must not publish") })
        assertFalse(f.admission.open())
    }

    @Test fun publishFailureKeepsOriginalReceiptEpochAndReasonForExplicitResume() {
        val f = Fixture(complete().copy(shutdownComplete = false))
        var attempts = 0
        val operation = f.owner.begin(11, capture) { barrier ->
            barrier.withReclaimed(capture) {
                attempts++
                throw IllegalStateException("injected post-publish readback failure")
            }
        }
        f.owner.poll(); f.port.observation = complete(); f.owner.poll()
        assertEquals(1, attempts)
        assertEquals(0, f.port.runtime)
        assertTrue(operation.canResumePublication)
        assertEquals("SAVE_PUBLICATION_FAILED", operation.firstFailure)
        assertFalse(f.admission.open())
        assertFalse(f.owner.resumePublication("other-capture", capture) { attempts++ })
        val resumeToken = "d".repeat(64)
        assertTrue(f.owner.resumePublication(capture, resumeToken) { barrier ->
            barrier.withReclaimed(resumeToken) {
                assertEquals(resumeToken, it.captureToken)
                assertEquals(11, it.stoppedRuntime)
                assertTrue(it.fullyReclaimed)
                attempts++
            }
        })
        assertEquals(2, attempts)
        assertEquals(1, f.port.calls.count { it == "native-destroy" })
        assertEquals("SAVE_PUBLICATION_FAILED", operation.firstFailure)
        assertEquals(RuntimeSaveCoordinator.Phase.COMMITTED, operation.phase)
        assertTrue(f.admission.open())
    }

    @Test fun foreignRuntimeEpochAndPublicationTokenCannotBorrowTheReceipt() {
        val f = Fixture()
        val operation = f.begin()
        f.port.runtime = 12
        f.owner.poll()
        assertEquals("SAVE_CAPTURE_STALE", operation.firstFailure)
        assertFalse(operation.canRetryReclamation)
        assertEquals(0, f.publications)
        assertFalse(f.admission.open())
        val unknown = Fixture()
        val unknownOp = unknown.begin()
        unknown.admission.retainUnknownOwner(); unknown.owner.poll()
        assertEquals("SAVE_CAPTURE_STALE", unknownOp.firstFailure)
        assertFalse(unknown.owner.continueReclamation())
        val wrongToken = Fixture(complete().copy(shutdownComplete = false))
        wrongToken.owner.begin(11, capture) { barrier ->
            barrier.withReclaimed("different-capture") { wrongToken.publications++ }
        }
        wrongToken.owner.poll(); wrongToken.port.observation = complete(); wrongToken.owner.poll()
        assertEquals(0, wrongToken.publications)
        assertEquals("SAVE_PUBLICATION_FAILED", wrongToken.owner.operation?.firstFailure)
    }

    private fun nativeEnvelope() = JSONObject("""{
      "schema":1,"code":0,"handle":"11","runtime":{"state":"Stopped","shutdown":{"complete":true},
      "reclamation":{"runtime":"11","origin_retired":true,"recovery_connect_allowed":false,
      "recovery_command_allowed":false,"shutdown_complete":true,"connections":0,"workers":0,
      "native_targets":0,"panels":0,"sdk_bindings":0,"board_windows":0,"pending_operations":0,
      "remote_cleanup_pending":false,"local_cleanup_pending":false}}}
    """)

    @Test fun typedNativeSnapshotRejectsMissingCoercedNegativeAndForeignEvidence() {
        assertTrue(NativeReclamation.decode(11, nativeEnvelope()).fullyStopped)
        val changes = listOf<(JSONObject) -> Unit>(
            { it.put("schema", "1") }, { it.put("code", "0") }, { it.put("code", 1) }, { it.put("handle", "12") },
            { it.getJSONObject("runtime").put("state", "Running") },
            { it.getJSONObject("runtime").getJSONObject("shutdown").put("complete", false) },
            { it.getJSONObject("runtime").getJSONObject("reclamation").put("runtime", "12") },
            { it.getJSONObject("runtime").getJSONObject("reclamation").put("workers", "0") },
            { it.getJSONObject("runtime").getJSONObject("reclamation").put("workers", 0.0) },
            { it.getJSONObject("runtime").getJSONObject("reclamation").put("workers", -1) },
            { it.getJSONObject("runtime").getJSONObject("reclamation").put("workers", 2147483648L) },
            { it.getJSONObject("runtime").getJSONObject("reclamation").put("shutdown_complete", "true") },
            { it.getJSONObject("runtime").getJSONObject("reclamation").remove("sdk_bindings") },
        )
        changes.forEachIndexed { index, change ->
            assertThrows("case $index", Exception::class.java) { NativeReclamation.decode(11, nativeEnvelope().also(change)) }
        }
        assertThrows(Exception::class.java) { NativeReclamation.decode(0, nativeEnvelope()) }
        assertThrows(Exception::class.java) { NativeReclamation.decode(11, JSONObject()) }
    }

    @Test fun stopReceiptRequiresExactRuntimeEpochCaptureAndAcceptedDestroy() {
        val admission = RuntimeAdmission()
        val owner = admission.begin(capture)
        val hub = SdkRetirementHub()
        val ticket = hub.retireRuntime(11, owner.epoch, capture)
        val progress = hub.progress(ticket)
        val receipt = StopReceipt.accept(owner, 11, complete(), ticket, progress, 0)
        assertTrue(receipt.evidence(capture).fullyReclaimed)
        assertThrows(IllegalArgumentException::class.java) { StopReceipt.accept(owner, 11, complete(), ticket, progress, 1) }
        assertThrows(IllegalArgumentException::class.java) { StopReceipt.accept(owner, 12, complete(), ticket, progress, 0) }
        assertThrows(IllegalArgumentException::class.java) { StopReceipt.accept(owner, 11, complete().copy(panels = 1), ticket, progress, 0) }
        val wrongEpoch = hub.retireRuntime(11, owner.epoch + 1, capture)
        assertThrows(IllegalArgumentException::class.java) { StopReceipt.accept(owner, 11, complete(), wrongEpoch, hub.progress(wrongEpoch), 0) }
        val wrongCapture = hub.retireRuntime(11, owner.epoch, "other-capture")
        assertThrows(IllegalArgumentException::class.java) { StopReceipt.accept(owner, 11, complete(), wrongCapture, hub.progress(wrongCapture), 0) }
    }
}
