package io.github.tinmachine.eidolon

import java.nio.file.Files
import java.security.SecureRandom
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/** V09/V10 integration: real POSIX settings/E1 and SDK ledger, with an explicit native port fixture. */
class RuntimeSettingsIntegrationTest {
    private val installation = "1".repeat(32)
    private val key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB"
    private val config = checkNotNull(javaClass.classLoader?.getResourceAsStream("linux-desktop-v3-configuration.json"))
        .bufferedReader().use { JSONObject(it.readText()).getJSONArray("valid").getJSONObject(0).getString("raw") }
    private fun draft(host: String = "192.0.2.10") = LinuxConnectionDraft("Linux", host, "22", "linux-user", key)
    private fun complete(runtime: Long = 11) = NativeReclamation(runtime, true, false, false, true,
        0, 0, 0, 0, 0, 0, 0, false, false)

    private class RandomBytes : SecureRandom() {
        var allocations = 0
        override fun nextBytes(bytes: ByteArray) { allocations++; bytes.fill(allocations.toByte()) }
    }

    private class Port(var observation: NativeReclamation) : RuntimeSaveCoordinator.Port {
        var runtime = 11L
        var time = 0L
        var destroyResult = 0
        val calls = mutableListOf<String>()
        override fun now() = time
        override fun currentRuntime() = runtime
        override fun observe(runtime: Long): NativeReclamation {
            check(runtime == this.runtime)
            return observation
        }
        override fun stopOrigin(runtime: Long): Boolean { calls += "origin-stop"; return true }
        override fun connectForRecovery(runtime: Long): Boolean { calls += "recover-connect"; return true }
        override fun recoverOrigin(runtime: Long): Boolean { calls += "recover"; return true }
        override fun requestRuntimeStop(runtime: Long): Int { calls += "runtime-stop"; return 0 }
        override fun destroyRuntime(runtime: Long): Int { calls += "native-destroy"; return destroyResult }
        override fun runtimeDestroyed(runtime: Long) {
            check(runtime == this.runtime)
            calls += "destroy-accepted"
            this.runtime = 0
        }
    }

    private inner class Fixture(
        initial: NativeReclamation = complete().copy(originRetired = false, shutdownComplete = false,
            connections = 1, workers = 2),
    ) : AutoCloseable {
        val fs = HostLinuxStorageFixture()
        val random = RandomBytes()
        var failConfigurationOnce = false
        var faultFired = false
        private fun fault(stage: LinuxStorageIoStage, name: String) {
            if (failConfigurationOnce && stage == LinuxStorageIoStage.BEFORE_RENAME && name == "connection.json") {
                failConfigurationOnce = false; faultFired = true
                error("INJECTED_CONFIGURATION_IO_FAILURE")
            }
        }
        val storage = LinuxStorageRepository(installation, fs.files(null), fs.files("linux-profile"),
            fs.files("linux-storage-v3"), random)
        val repository = LinuxDesktopSettingsRepository(installation, storage, fs.files("linux-profile"),
            fs.files("linux-desktop-v3", ::fault), fs.files("linux-desktop", ::fault))
        val admission = RuntimeAdmission()
        val hub = SdkRetirementHub()
        val port = Port(initial)
        val coordinator = RuntimeSaveCoordinator(admission, hub, port, timeoutMillis = 100)
        val request = repository.prepare(repository.load().captureToken!!, draft(), LinuxDesktopBackend.V3, config, true)
        var saved: LinuxDesktopSettingsRepository.Snapshot? = null
        fun begin() = coordinator.begin(port.runtime, request.captureToken) { barrier ->
            check(!admission.open() && port.runtime == 0L)
            port.calls += "settings-save"
            saved = repository.save(request, barrier)
        }
        fun files(): Map<String, List<Byte>> = Files.walk(fs.directory).use { paths ->
            paths.filter { Files.isRegularFile(it) }.toList()
                .associate { fs.directory.relativize(it).toString() to Files.readAllBytes(it).toList() }
        }
        fun assertUnpublished() {
            assertTrue(files().isEmpty())
            assertEquals(0, random.allocations)
            assertNull(saved)
            assertFalse(admission.open())
        }
        fun completeNative() {
            port.observation = complete().copy(shutdownComplete = false)
            coordinator.poll()
            port.observation = complete()
            coordinator.poll()
        }
        override fun close() = fs.close()
    }

    @Test fun realFilesAndIdsAppearOnlyAfterOriginNativeAndEverySdkGenerationRetire() = Fixture().use { f ->
        val oldAdmission = checkNotNull(f.admission.ticket())
        val ledger = f.hub.evidence
        val diagnostic = SdkEvidenceBridge.Lease(11, 7, 9, 2, 1, null, 4)
        val gui = SdkEvidenceBridge.Lease(11, 7, 10, 3, 2, 100, 4)
        listOf(diagnostic, gui).forEach {
            assertTrue(ledger.reserve(it)); ledger.registered(it); ledger.entityCreated(it, 44)
        }
        assertTrue(ledger.beginProducer(diagnostic))
        val lateCallback = checkNotNull(ledger.enterCallback(diagnostic))
        val operation = f.begin()
        f.coordinator.poll()
        assertEquals(listOf("origin-stop"), f.port.calls)
        f.assertUnpublished()
        assertFalse(f.admission.permits(oldAdmission))
        f.port.observation = complete().copy(shutdownComplete = false, nativeTargets = 1,
            sdkBindings = 2, panels = 1, boardWindows = 1)
        f.coordinator.poll()
        assertEquals(RuntimeSaveCoordinator.Phase.RUNTIME_STOPPING, operation.phase)
        f.assertUnpublished()
        assertFalse(lateCallback.valid())

        // Native zero alone is not enough: the real ledger still owns diagnostic and old Scene resources.
        f.port.observation = complete()
        f.coordinator.poll()
        f.assertUnpublished()
        assertFalse("native-destroy" in f.port.calls)
        listOf(diagnostic, gui).forEach {
            ledger.rendererDetached(it); ledger.destroyRequested(it, 44)
            ledger.deleteObserved(it, 0, 44); ledger.auxiliaryDisposed(it)
        }
        ledger.sdkCleanupPass(4, 3, 1)
        assertTrue(ledger.readyToRelease(gui)); ledger.nativeBindingReleased(gui)
        assertTrue(ledger.collectCompleted(gui))
        ledger.lateObjectCleanupRequested(diagnostic)
        lateCallback.close(); ledger.producerFinished(diagnostic)
        f.coordinator.poll()
        f.assertUnpublished()
        ledger.sdkCleanupPass(4, 3, 10) // A newer Scene cannot discharge the old Scene's fence.
        f.coordinator.poll()
        f.assertUnpublished()
        ledger.sdkCleanupPass(4, 2, 1)
        f.coordinator.poll()
        f.assertUnpublished()
        ledger.sdkCleanupPass(4, 2, 2)
        assertTrue(ledger.readyToRelease(diagnostic)); ledger.nativeBindingReleased(diagnostic)
        assertTrue(ledger.collectCompleted(diagnostic))
        f.coordinator.poll()

        assertEquals(listOf("origin-stop", "runtime-stop", "native-destroy", "destroy-accepted", "settings-save"), f.port.calls)
        val saved = checkNotNull(f.saved)
        assertEquals(LinuxDesktopSettingsRepository.Status.READY_V3, saved.status)
        assertEquals("1", saved.profile!!.revision)
        assertEquals(StorageInstallationId("01".repeat(16)), saved.storageRecord!!.installationId)
        assertEquals(ProfileStorageId("02".repeat(16)), saved.storageRecord.profileStorageId)
        assertEquals(2, f.random.allocations)
        val marker = LinuxDesktopActiveBackendRepository.decode(f.fs.files("linux-desktop").read("active-backend.json")!!)
        assertEquals(saved.profile.binding(), marker.profileBinding)
        assertEquals(saved.storageRecord.sha256(), marker.storageRecordDigest)
        assertNotNull(saved.v3Configuration!!.forProfile(saved.profile, saved.storageRecord))
        assertEquals(saved.captureToken, f.repository.load().captureToken)
        assertEquals(RuntimeSaveCoordinator.Phase.COMMITTED, operation.phase)
        assertTrue(f.admission.open()); assertFalse(f.admission.permits(oldAdmission))
    }

    @Test fun remoteTimeoutLeavesEveryFileUntouchedUntilExplicitSameControllerRecoverAndFinalizer() =
        Fixture(complete().copy(originRetired = false, shutdownComplete = false, remoteCleanupPending = true)).use { f ->
            val operation = f.begin()
            f.coordinator.poll(); f.port.time = 101; f.coordinator.poll()
            assertEquals(RuntimeSaveCoordinator.Phase.RECOVERY_REQUIRED, operation.phase)
            assertEquals("SAVE_REMOTE_PENDING", operation.firstFailure)
            assertEquals(f.request.captureToken, operation.owner.captureToken)
            assertEquals(11, f.port.runtime)
            f.assertUnpublished()
            assertFalse("runtime-stop" in f.port.calls)
            f.port.observation = f.port.observation.copy(recoveryConnectAllowed = true)
            f.coordinator.poll()
            assertEquals(listOf("origin-stop"), f.port.calls)
            assertTrue(f.coordinator.continueReclamation()); f.coordinator.poll()
            assertEquals("recover-connect", f.port.calls.last())
            f.port.observation = f.port.observation.copy(recoveryConnectAllowed = false, recoveryCommandAllowed = true)
            f.coordinator.poll()
            assertEquals("recover", f.port.calls.last())
            f.port.observation = f.port.observation.copy(remoteCleanupPending = false, localCleanupPending = true)
            f.coordinator.poll()
            f.assertUnpublished()
            assertFalse("runtime-stop" in f.port.calls)
            f.completeNative()
            assertEquals(RuntimeSaveCoordinator.Phase.COMMITTED, operation.phase)
            assertEquals("SAVE_REMOTE_PENDING", operation.firstFailure)
            assertEquals(f.request.intentToken, f.saved!!.intentToken)
            assertEquals("1", f.saved!!.profile!!.revision)
            assertEquals(2, f.random.allocations)
        }

    @Test fun realPublishFailureRetainsReceiptAndIntentAndExplicitResumeDoesNotReallocate() = Fixture().use { f ->
        f.failConfigurationOnce = true
        val operation = f.begin()
        f.completeNative()
        assertTrue(f.faultFired)
        assertTrue(operation.canResumePublication)
        assertEquals("SAVE_PUBLICATION_FAILED", operation.firstFailure)
        assertEquals(0, f.port.runtime)
        assertFalse(f.admission.open())
        val receipt = checkNotNull(operation.receipt)
        val pending = f.repository.load()
        val record = (f.storage.load(installation) as LinuxStorageRepository.State.Matched).capture.record
        assertEquals(LinuxDesktopSettingsRepository.Status.SAVE_PENDING, pending.status)
        assertEquals(f.request.intentToken, pending.intentToken)
        assertNotEquals(f.request.captureToken, pending.captureToken)
        assertEquals("1", pending.profile!!.revision)
        assertEquals(2, f.random.allocations)
        assertNull(f.fs.files("linux-desktop").read("active-backend.json"))
        val before = f.files()
        assertFalse(f.coordinator.resumePublication("different-original-capture", pending.captureToken!!) { error("stale owner") })
        assertEquals(before, f.files())

        // Host grants this new readback token only after matching the durable intent identity.
        check(pending.intentToken == f.request.intentToken)
        assertTrue(f.coordinator.resumePublication(f.request.captureToken, pending.captureToken) { barrier ->
            f.saved = f.repository.resume(pending.captureToken, barrier)
        })
        assertSame(receipt, operation.receipt)
        assertEquals(RuntimeSaveCoordinator.Phase.COMMITTED, operation.phase)
        assertEquals("SAVE_PUBLICATION_FAILED", operation.firstFailure)
        assertEquals(1, f.port.calls.count { it == "native-destroy" })
        assertEquals(record, f.saved!!.storageRecord)
        assertEquals("1", f.saved!!.profile!!.revision)
        assertEquals(2, f.random.allocations)
        assertTrue(f.admission.open())
        assertEquals(f.saved!!.captureToken, f.repository.load().captureToken)
    }

    @Test fun concurrentProfileMutationInvalidatesCapturedSaveWithoutOverwritingOrCreatingE1() = Fixture().use { f ->
        val operation = f.begin()
        f.coordinator.poll()
        val other = LinuxConnectionProfile.propose(draft("192.0.2.99"), installation, null)
        val bytes = ConnectionProfileV2Codec.encode(other)
        f.fs.files("linux-profile").apply { create(); publish("remote-linux.json", null, bytes) }
        val before = f.files()
        f.completeNative()
        assertEquals(RuntimeSaveCoordinator.Phase.SAVE_PENDING, operation.phase)
        assertEquals("SAVE_PUBLICATION_FAILED", operation.firstFailure)
        assertTrue(operation.canResumePublication)
        assertEquals(before, f.files())
        assertArrayEquals(bytes, f.fs.files("linux-profile").read("remote-linux.json"))
        assertNull(f.fs.root.read(LinuxStorageRepository.ANCHOR))
        assertNull(f.repository.load().intentToken)
        assertEquals(0, f.random.allocations)
        assertFalse(f.admission.open())
        // Even the legitimate old receipt cannot turn a stale captured request into new authority.
        assertTrue(f.coordinator.resumePublication(f.request.captureToken, f.request.captureToken) { barrier ->
            f.repository.save(f.request, barrier)
        })
        assertEquals(RuntimeSaveCoordinator.Phase.SAVE_PENDING, operation.phase)
        assertEquals(before, f.files())
        assertEquals(0, f.random.allocations)
    }

    @Test fun actualDestroyRejectionOrSdkFailureNeverCreatesDurableIntentOrStorageIdentity() {
        Fixture().use { f ->
            f.port.destroyResult = 1 // STALE is an actual rejected return, never Accepted.
            val operation = f.begin(); f.completeNative()
            assertEquals("SAVE_DESTROY_REJECTED", operation.firstFailure)
            assertNull(operation.receipt)
            assertEquals(11, f.port.runtime)
            f.assertUnpublished()
        }
        Fixture().use { f ->
            val diagnostic = SdkEvidenceBridge.Lease(11, 7, 9, 2, 1, null, 4)
            assertTrue(f.hub.evidence.reserve(diagnostic)); f.hub.evidence.registered(diagnostic)
            f.hub.evidence.entityCreated(diagnostic, 44)
            f.hub.evidence.failed(diagnostic)
            val operation = f.begin(); f.completeNative()
            assertEquals("SAVE_SDK_DISPOSAL_FAILED", operation.firstFailure)
            assertNull(operation.receipt)
            assertFalse("native-destroy" in f.port.calls)
            f.assertUnpublished()
        }
    }

    @Test fun lostSameProcessAuthorityKeepsRealPendingFilesAndCannotUseAFreshRuntimeZero() = Fixture().use { f ->
        f.failConfigurationOnce = true
        val operation = f.begin(); f.completeNative()
        val pending = f.repository.load()
        assertTrue(operation.canResumePublication)
        assertEquals(f.request.intentToken, pending.intentToken)
        val before = f.files()
        f.port.runtime = 12 // A newly allocated owner is unrelated to the stopped old runtime.
        assertFalse(f.coordinator.resumePublication(f.request.captureToken, pending.captureToken!!) { error("foreign runtime") })
        f.port.runtime = 0
        f.admission.retainUnknownOwner()
        assertFalse(f.coordinator.resumePublication(f.request.captureToken, pending.captureToken) { error("lost authority") })
        assertEquals(before, f.files())
        assertEquals(2, f.random.allocations)
        assertFalse(f.admission.open())
        assertEquals(LinuxDesktopSettingsRepository.Status.SAVE_PENDING, f.repository.load().status)
    }
}
