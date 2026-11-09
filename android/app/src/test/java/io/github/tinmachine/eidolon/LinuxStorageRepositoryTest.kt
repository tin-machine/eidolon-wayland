package io.github.tinmachine.eidolon

import java.nio.file.Files
import java.nio.file.attribute.PosixFilePermissions
import java.security.SecureRandom
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test

/** Real host POSIX persistence. Reclamation observations here are synthetic, not live runtime proof. */
class LinuxStorageRepositoryTest {
    private val installation = "1".repeat(32)
    private val key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB"
    private val otherKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIC"
    private fun draft(host: String = "192.0.2.10", user: String = "linux-user", name: String = "Linux desktop",
                      port: String = "22", hostKey: String = key) = LinuxConnectionDraft(name, host, port, user, hostKey)

    private class RandomBytes : SecureRandom() {
        var allocations = 0
        override fun nextBytes(bytes: ByteArray) {
            check(bytes.size == 16)
            allocations++
            bytes.fill(allocations.toByte())
        }
    }

    private class Fault {
        var stage: LinuxStorageIoStage? = null
        var name: String? = null
        var occurrence = 1
        var hits = 0
        var fired = false
        fun arm(stage: LinuxStorageIoStage, name: String, occurrence: Int = 1) {
            this.stage = stage; this.name = name; this.occurrence = occurrence; hits = 0; fired = false
        }
        fun invoke(stage: LinuxStorageIoStage, name: String) {
            if (!fired && this.stage == stage && this.name == name && ++hits == occurrence) {
                fired = true
                throw IllegalStateException("INJECTED_IO_FAILURE")
            }
        }
    }

    private fun repository(fs: HostLinuxStorageFixture, random: RandomBytes = RandomBytes(), fault: Fault = Fault(),
                           owner: String = installation) = LinuxStorageRepository(owner,
        fs.files(null, fault::invoke), fs.files("linux-profile", fault::invoke), fs.files("linux-storage-v3", fault::invoke), random)
    private fun capture(state: LinuxStorageRepository.State): LinuxStorageRepository.Capture = when (state) {
        is LinuxStorageRepository.State.Uninitialized -> state.capture
        is LinuxStorageRepository.State.Matched -> state.capture
        else -> error("Expected a saveable capture, got ${state.javaClass.simpleName}")
    }
    private fun request(capture: LinuxStorageRepository.Capture, input: LinuxConnectionDraft = draft(),
                        explicit: Boolean = true, expectedDigest: String? = capture.expectedRecordDigest,
                        owner: String = installation): LinuxStorageRepository.SaveRequest {
        val next = LinuxConnectionProfile.propose(input, installation, capture.profile)
        return LinuxStorageRepository.SaveRequest(owner, capture, capture.profile, next, capture.oldHostUserBinding,
            StorageCapture.from(installation, next).hostUserBinding, expectedDigest, explicit)
    }
    private fun resumeRequest(capture: LinuxStorageRepository.PendingCapture, explicit: Boolean = true) =
        LinuxStorageRepository.ResumeRequest(installation, capture, capture.previousProfile, capture.nextProfile,
            capture.previousProfile?.let { StorageCapture.from(installation, it).hostUserBinding }, capture.nextRecord.hostUserBinding,
            capture.previousRecord?.sha256(), capture.transactionDigest, explicit)

    /** This fixture supplies stated observations solely to test gate validation; no real reclaim claim. */
    private class SyntheticBarrier(private val defect: String? = null) : LinuxStorageRepository.ReclamationBarrier {
        override fun <T> withReclaimed(captureToken: String, action: (LinuxStorageRepository.ReclamationEvidence) -> T): T {
            if (defect == "throw-before") error("BARRIER_FAILED")
            val result = action(LinuxStorageRepository.ReclamationEvidence(
                if (defect == "token") "0".repeat(64) else captureToken,
                if (defect == "runtime") 0 else 42,
                defect != "shutdown", defect != "destroy",
                if (defect == "connections") 1 else 0,
                if (defect == "workers") 1 else 0,
                if (defect == "targets") 1 else 0,
                if (defect == "panels") 1 else 0,
                if (defect == "sdk") 1 else 0,
                if (defect == "board") 1 else 0,
                if (defect == "operations") 1 else 0,
                defect == "journal",
            ))
            if (defect == "throw-after") error("BARRIER_FAILED")
            return result
        }
    }
    private fun save(repo: LinuxStorageRepository, input: LinuxConnectionDraft = draft()) =
        repo.save(request(capture(repo.load(installation)), input), SyntheticBarrier())
    private fun snapshot(fs: HostLinuxStorageFixture): Map<String, ByteArray> = Files.walk(fs.directory).use { paths ->
        paths.filter { Files.isRegularFile(it) }.toList().associate { fs.directory.relativize(it).toString() to Files.readAllBytes(it) }
    }
    private fun assertSameSnapshot(a: Map<String, ByteArray>, b: Map<String, ByteArray>) {
        assertEquals(a.keys, b.keys)
        a.forEach { (name, bytes) -> assertArrayEquals(name, bytes, b[name]) }
    }

    @Test fun readHasNoEnrollmentSideEffectAndFirstSaveUsesIndependentRandomIds() = HostLinuxStorageFixture().use { fs ->
        val random = RandomBytes(); val repo = repository(fs, random)
        assertTrue(repo.load(installation) is LinuxStorageRepository.State.Uninitialized)
        assertEquals(0, snapshot(fs).size)
        assertFalse(Files.exists(fs.directory.resolve("linux-storage-v3")))
        // Legacy AtomicFile.read creates this directory. Its existence is not an E1 enrollment signal.
        fs.files("linux-profile").create()
        assertTrue(repo.load(installation) is LinuxStorageRepository.State.Uninitialized)
        val saved = save(repo).capture
        assertEquals(2, random.allocations)
        assertEquals("01".repeat(16), saved.record!!.installationId.value)
        assertEquals("02".repeat(16), saved.record.profileStorageId.value)
        assertEquals(installation, saved.profile!!.installation)
        assertEquals("1", saved.profile.revision)
        assertEquals(7, saved.record.toFields().size)
        assertEquals(saved.record.sha256(), capture(repository(fs).load(installation)).record!!.sha256())
    }

    @Test fun enrollmentAlongsideLegacyProfileKeepsItsDefaultIdentityAndRevisionRules() = HostLinuxStorageFixture().use { fs ->
        val old = LinuxConnectionProfile.propose(draft(), installation, null)
        val noncanonical = (" \n" + ConnectionProfileV2Codec.encode(old).toString(Charsets.UTF_8) + " \n").toByteArray()
        fs.files("linux-profile").apply { create(); publish(LinuxStorageRepository.PROFILE, null, noncanonical) }
        val repo = repository(fs)
        assertEquals(old.toFields(), capture(repo.load(installation)).profile!!.toFields())
        val saved = save(repo).capture
        assertEquals("2", saved.profile!!.revision)
        assertEquals(installation, saved.profile.installation)
        assertNotEquals(installation, saved.record!!.installationId.value)
    }

    @Test fun addressPortNameRevisionRestartAndCanonicalHostInputKeepBothIds() = HostLinuxStorageFixture().use { fs ->
        val random = RandomBytes(); var repo = repository(fs, random)
        val first = save(repo).capture
        repo = repository(fs, random) // APK/process restart: IDs are loaded, never regenerated.
        val second = save(repo, draft(host = "192.0.2.11", port = "2222", name = "新しい表示名", hostKey = " $key comment ")).capture
        assertEquals(first.record!!.installationId, second.record!!.installationId)
        assertEquals(first.record.profileStorageId, second.record.profileStorageId)
        assertNotEquals(first.profile!!.binding(), second.profile!!.binding())
        assertEquals("2", second.profile.revision)
        assertEquals(2, random.allocations)
        // Client authentication keys are not an input of either storage API or host/user capture.
        assertEquals(StorageCapture.from(installation, first.profile).hostUserBinding,
            StorageCapture.from(installation, second.profile).hostUserBinding)
    }

    @Test fun hostKeyAndUsernameRotateOnlyProfileStorageAndRejectIdReuse() = HostLinuxStorageFixture().use { fs ->
        val random = RandomBytes(); val repo = repository(fs, random)
        val a = save(repo).capture.record!!
        val b = save(repo, draft(hostKey = otherKey)).capture.record!!
        val c = save(repo, draft(hostKey = otherKey, user = "another-user")).capture.record!!
        assertEquals(a.installationId, b.installationId); assertEquals(b.installationId, c.installationId)
        assertNotEquals(a.profileStorageId, b.profileStorageId); assertNotEquals(b.profileStorageId, c.profileStorageId)
        assertEquals(4, random.allocations)
        assertEquals(setOf("linux-storage-v3-anchor.json", "linux-profile/remote-linux.json",
            "linux-storage-v3/transaction.json", "linux-storage-v3/record.json", "linux-storage-v3/commit.json"), snapshot(fs).keys)
    }

    @Test fun staleCaptureExpectedDigestAndDifferentInstallationNeverOverwrite() = HostLinuxStorageFixture().use { fs ->
        val repo = repository(fs); val first = save(repo).capture
        val second = save(repo, draft(host = "192.0.2.12")).capture
        val before = snapshot(fs)
        val stale = assertThrows(LinuxStorageRepository.SaveFailure::class.java) { repo.save(request(first), SyntheticBarrier()) }
        assertEquals(second.profile!!.binding(), capture(stale.reloaded).profile!!.binding())
        assertThrows(LinuxStorageRepository.SaveFailure::class.java) {
            repo.save(request(second, expectedDigest = "0".repeat(64)), SyntheticBarrier())
        }
        assertThrows(LinuxStorageRepository.SaveFailure::class.java) {
            repo.save(request(second, owner = "2".repeat(32)), SyntheticBarrier())
        }
        assertTrue(repository(fs, owner = "2".repeat(32)).load("2".repeat(32)) is LinuxStorageRepository.State.Blocked)
        assertSameSnapshot(before, snapshot(fs))
    }

    @Test fun bothHostCapturesCurrentProfileAndNextRevisionAreMandatory() = HostLinuxStorageFixture().use { fs ->
        val random = RandomBytes(); val repo = repository(fs, random); val first = save(repo).capture
        val valid = request(first)
        fun changed(old: ConnectionProfileV2.Value? = valid.capturedProfile, next: ConnectionProfileV2.Value = valid.nextProfile,
                    oldHost: String? = valid.oldHostUserBinding, newHost: String = valid.newHostUserBinding) =
            LinuxStorageRepository.SaveRequest(installation, first, old, next, oldHost, newHost, first.expectedRecordDigest, true)
        val skipped = ConnectionProfileV2.parse(valid.nextProfile.toFields() + ("revision" to "99"), installation)
        val foreign = ConnectionProfileV2.parse(valid.nextProfile.toFields() + ("installation" to "3".repeat(32)), "3".repeat(32))
        val before = snapshot(fs)
        for (bad in listOf(changed(old = valid.nextProfile), changed(oldHost = "0".repeat(64)),
            changed(newHost = "0".repeat(64)), changed(next = skipped), changed(next = foreign))) {
            assertThrows(LinuxStorageRepository.SaveFailure::class.java) { repo.save(bad, SyntheticBarrier()) }
            assertSameSnapshot(before, snapshot(fs))
            assertEquals(2, random.allocations)
        }
    }

    @Test fun everyFullReclamationConditionAndExplicitSaveAreRequired() {
        for (defect in listOf("token", "runtime", "shutdown", "destroy", "connections", "workers", "targets", "panels", "sdk", "board", "operations", "journal")) {
            HostLinuxStorageFixture().use { fs ->
                val random = RandomBytes(); val repo = repository(fs, random)
                assertThrows(defect, LinuxStorageRepository.SaveFailure::class.java) {
                    repo.save(request(capture(repo.load(installation))), SyntheticBarrier(defect))
                }
                assertEquals(defect, 0, random.allocations)
                assertEquals(defect, 0, snapshot(fs).size)
                assertTrue(repo.currentState() is LinuxStorageRepository.State.Uninitialized)
            }
        }
        HostLinuxStorageFixture().use { fs ->
            val repo = repository(fs)
            assertThrows(LinuxStorageRepository.SaveFailure::class.java) {
                repo.save(request(capture(repo.load(installation)), explicit = false), SyntheticBarrier())
            }
            assertEquals(0, snapshot(fs).size)
        }
    }

    @Test fun barrierFailuresBeforeAndAfterCallbackAlsoReloadWithoutRollback() = HostLinuxStorageFixture().use { fs ->
        val repo = repository(fs)
        val before = assertThrows(LinuxStorageRepository.SaveFailure::class.java) {
            repo.save(request(capture(repo.load(installation))), SyntheticBarrier("throw-before"))
        }
        assertTrue(before.reloaded is LinuxStorageRepository.State.Uninitialized)
        val after = assertThrows(LinuxStorageRepository.SaveFailure::class.java) {
            repo.save(request(capture(repo.load(installation))), SyntheticBarrier("throw-after"))
        }
        assertTrue(after.reloaded is LinuxStorageRepository.State.Matched)
        assertEquals("1", capture(after.reloaded).profile!!.revision)
    }

    @Test fun concurrentStoreInstancesHaveOneWinnerAndStaleCasLosesBeforeAllocation() = HostLinuxStorageFixture().use { fs ->
        val aRandom = RandomBytes(); val bRandom = RandomBytes()
        val a = repository(fs, aRandom); val b = repository(fs, bRandom)
        val ra = request(capture(a.load(installation)), draft(host = "192.0.2.11"))
        val rb = request(capture(b.load(installation)), draft(host = "192.0.2.12"))
        val start = CountDownLatch(1); val pool = Executors.newFixedThreadPool(2)
        try {
            val results = listOf(a to ra, b to rb).map { (repo, req) -> pool.submit<Boolean> {
                check(start.await(10, TimeUnit.SECONDS))
                try { repo.save(req, SyntheticBarrier()); true } catch (_: LinuxStorageRepository.SaveFailure) { false }
            } }
            start.countDown()
            assertEquals(1, results.count { it.get(10, TimeUnit.SECONDS) })
            assertEquals(2, aRandom.allocations + bRandom.allocations)
            assertEquals(capture(a.currentState()).token, capture(b.currentState()).token)
        } finally { pool.shutdownNow(); check(pool.awaitTermination(10, TimeUnit.SECONDS)) }
    }

    @Test fun markerAndIntentFailuresNeverBecomeFreshRandomEnrollment() {
        for ((stage, name, occurrence) in listOf(
            Triple(LinuxStorageIoStage.RENAMED, LinuxStorageRepository.ANCHOR, 1),
            Triple(LinuxStorageIoStage.DIRECTORY_CREATED, "linux-storage-v3", 1),
            Triple(LinuxStorageIoStage.TEMP_CREATED, LinuxStorageRepository.TRANSACTION, 1),
            Triple(LinuxStorageIoStage.RENAMED, LinuxStorageRepository.TRANSACTION, 1),
            Triple(LinuxStorageIoStage.BEFORE_RENAME, LinuxStorageRepository.ANCHOR, 2),
        )) {
            HostLinuxStorageFixture().use { fs ->
                val fault = Fault().apply { arm(stage, name, occurrence) }; val random = RandomBytes()
                val repo = repository(fs, random, fault)
                val failure = assertThrows(LinuxStorageRepository.SaveFailure::class.java) { save(repo) }
                assertTrue("$stage/$name", fault.fired)
                assertTrue("$stage/$name", failure.reloaded is LinuxStorageRepository.State.Blocked)
                val allocated = random.allocations
                assertTrue(repository(fs, random).load(installation) is LinuxStorageRepository.State.Blocked)
                assertEquals(allocated, random.allocations)
            }
        }
    }

    @Test fun beforeAnchorPublicationFailureIsStillUninitializedBecauseNoIdWasGenerated() = HostLinuxStorageFixture().use { fs ->
        val fault = Fault().apply { arm(LinuxStorageIoStage.FILE_SYNCED, LinuxStorageRepository.ANCHOR) }
        val random = RandomBytes(); val repo = repository(fs, random, fault)
        val failure = assertThrows(LinuxStorageRepository.SaveFailure::class.java) { save(repo) }
        assertTrue(failure.reloaded is LinuxStorageRepository.State.Uninitialized)
        assertEquals(0, random.allocations)
        assertEquals(0, snapshot(fs).size)
    }

    @Test fun everyPartialPublicationCutResumesExactIdsAfterFreshFullBarrier() {
        val cuts = listOf(
            Triple(LinuxStorageIoStage.RENAMED, LinuxStorageRepository.ANCHOR, 2),
            Triple(LinuxStorageIoStage.BEFORE_RENAME, LinuxStorageRepository.PROFILE, 1),
            Triple(LinuxStorageIoStage.RENAMED, LinuxStorageRepository.PROFILE, 1),
            Triple(LinuxStorageIoStage.BEFORE_RENAME, LinuxStorageRepository.RECORD, 1),
            Triple(LinuxStorageIoStage.RENAMED, LinuxStorageRepository.RECORD, 1),
            Triple(LinuxStorageIoStage.BEFORE_RENAME, LinuxStorageRepository.COMMIT, 1),
            Triple(LinuxStorageIoStage.RENAMED, LinuxStorageRepository.COMMIT, 1),
            Triple(LinuxStorageIoStage.BEFORE_RENAME, LinuxStorageRepository.ANCHOR, 3),
        )
        for ((stage, name, occurrence) in cuts) HostLinuxStorageFixture().use { fs ->
            val random = RandomBytes(); val fault = Fault().apply { arm(stage, name, occurrence) }
            val repo = repository(fs, random, fault)
            val failure = assertThrows(LinuxStorageRepository.SaveFailure::class.java) { save(repo) }
            assertTrue("$stage/$name", fault.fired)
            val pending = (failure.reloaded as LinuxStorageRepository.State.Pending).capture
            assertEquals(2, random.allocations)
            val restarted = repository(fs, random)
            val request = resumeRequest((restarted.load(installation) as LinuxStorageRepository.State.Pending).capture)
            assertThrows(LinuxStorageRepository.SaveFailure::class.java) { restarted.resumePending(request, SyntheticBarrier("sdk")) }
            assertThrows(LinuxStorageRepository.SaveFailure::class.java) {
                restarted.resumePending(resumeRequest(request.captured, explicit = false), SyntheticBarrier())
            }
            val result = restarted.resumePending(request, SyntheticBarrier()).capture
            assertEquals(pending.nextRecord.sha256(), result.record!!.sha256())
            assertEquals(pending.nextProfile.toFields(), result.profile!!.toFields())
            assertEquals(2, random.allocations)
        }
    }

    @Test fun partialUpdateNeverCombinesOldRecordWithNewProfileAndSealedFailureDoesNotRollback() = HostLinuxStorageFixture().use { fs ->
        val random = RandomBytes(); val fault = Fault(); val repo = repository(fs, random, fault)
        val first = save(repo).capture
        fault.arm(LinuxStorageIoStage.RENAMED, LinuxStorageRepository.PROFILE)
        val failure = assertThrows(LinuxStorageRepository.SaveFailure::class.java) { save(repo, draft(user = "new-user")) }
        val pending = (failure.reloaded as LinuxStorageRepository.State.Pending).capture
        val actualProfile = LinuxConnectionProfile.decode(fs.files("linux-profile").read(LinuxStorageRepository.PROFILE)!!, installation)
        val actualRecord = LinuxStorageIdentityCodec.decode(fs.files("linux-storage-v3").read(LinuxStorageRepository.RECORD)!!)
        assertEquals("new-user", actualProfile.username)
        assertEquals(first.record!!.sha256(), actualRecord.sha256())
        assertThrows(IllegalArgumentException::class.java) { actualRecord.matchesCapture(StorageCapture.from(installation, actualProfile)) }
        assertTrue(repo.currentState() is LinuxStorageRepository.State.Pending)
        val complete = repo.resumePending(resumeRequest(pending), SyntheticBarrier()).capture
        assertEquals("2", complete.profile!!.revision)
        assertEquals(first.record.installationId, complete.record!!.installationId)
        assertNotEquals(first.record.profileStorageId, complete.record.profileStorageId)

        fault.arm(LinuxStorageIoStage.RENAMED, LinuxStorageRepository.ANCHOR, 3)
        val afterSeal = assertThrows(LinuxStorageRepository.SaveFailure::class.java) { save(repo, draft(user = "new-user", name = "third")) }
        assertTrue(afterSeal.reloaded is LinuxStorageRepository.State.Matched)
        assertEquals("3", capture(afterSeal.reloaded).profile!!.revision)
        assertEquals(3, random.allocations)
    }

    @Test fun readbackFailureAfterFinalSealReportsFailureButReloadsMatchingNewPair() = HostLinuxStorageFixture().use { fs ->
        val fault = Fault().apply { arm(LinuxStorageIoStage.READBACK, LinuxStorageRepository.ANCHOR, 3) }
        val repo = repository(fs, fault = fault)
        val failure = assertThrows(LinuxStorageRepository.SaveFailure::class.java) { save(repo) }
        assertTrue(fault.fired)
        assertTrue(failure.reloaded is LinuxStorageRepository.State.Matched)
        assertEquals(capture(failure.reloaded).token, capture(repository(fs).load(installation)).token)
    }

    @Test fun sealedFileLossCorruptionAndLossOfEitherEnrollmentWitnessBlock() {
        val targets = listOf("linux-storage-v3-anchor.json", "linux-profile/remote-linux.json", "linux-storage-v3/record.json",
            "linux-storage-v3/transaction.json", "linux-storage-v3/commit.json")
        for (target in targets) for (corrupt in listOf(false, true)) HostLinuxStorageFixture().use { fs ->
            val random = RandomBytes(); val repo = repository(fs, random); val saved = save(repo).capture
            val path = fs.directory.resolve(target)
            if (corrupt) Files.write(path, byteArrayOf(0xc3.toByte())) else Files.delete(path)
            assertTrue("$target corrupt=$corrupt", repository(fs, random).load(installation) is LinuxStorageRepository.State.Blocked)
            assertThrows(LinuxStorageRepository.SaveFailure::class.java) { repo.save(request(saved), SyntheticBarrier()) }
            assertEquals(2, random.allocations)
        }
        HostLinuxStorageFixture().use { fs ->
            val random = RandomBytes(); save(repository(fs, random))
            val directory = fs.directory.resolve("linux-storage-v3")
            Files.walk(directory).use { paths -> paths.sorted(Comparator.reverseOrder()).forEach(Files::delete) }
            assertTrue(repository(fs, random).load(installation) is LinuxStorageRepository.State.Blocked)
            assertEquals(2, random.allocations)
        }
    }

    @Test fun actualReadPermissionFailureIsNotAbsenceOrPermissionToRegenerate() {
        assumeTrue("Real EACCES requires a non-root UID; Android CI runs this case", HostLinuxStoragePosix.uid != 0)
        HostLinuxStorageFixture().use { fs ->
            val nested = fs.directory.resolve("no-backup")
            Files.createDirectory(nested)
            Files.setPosixFilePermissions(nested, PosixFilePermissions.fromString("rwx------"))
            val random = RandomBytes()
            val repo = LinuxStorageRepository(installation,
                SecureLinuxStorageFiles(nested.toFile(), null, HostLinuxStoragePosix),
                SecureLinuxStorageFiles(nested.toFile(), "linux-profile", HostLinuxStoragePosix),
                SecureLinuxStorageFiles(nested.toFile(), "linux-storage-v3", HostLinuxStoragePosix), random)
            val saved = save(repo).capture
            val before = snapshot(fs)
            Files.setPosixFilePermissions(fs.directory, PosixFilePermissions.fromString("---------"))
            try {
                assertTrue(repo.load(installation) is LinuxStorageRepository.State.Blocked)
                assertThrows(LinuxStorageRepository.SaveFailure::class.java) { repo.save(request(saved), SyntheticBarrier()) }
                assertEquals(2, random.allocations)
            } finally { Files.setPosixFilePermissions(fs.directory, PosixFilePermissions.fromString("rwx------")) }
            assertSameSnapshot(before, snapshot(fs))
            assertEquals(saved.token, capture(repo.load(installation)).token)
        }
    }

    @Test fun pendingTransactionIdSubstitutionAndUnrelatedProfileCasNeverGetAdopted() = HostLinuxStorageFixture().use { fs ->
        val fault = Fault(); val repo = repository(fs, fault = fault)
        save(repo)
        fault.arm(LinuxStorageIoStage.BEFORE_RENAME, LinuxStorageRepository.PROFILE)
        val failure = assertThrows(LinuxStorageRepository.SaveFailure::class.java) { save(repo, draft(host = "192.0.2.11")) }
        val pending = (failure.reloaded as LinuxStorageRepository.State.Pending).capture
        val path = fs.directory.resolve("linux-storage-v3/transaction.json")
        val original = Files.readAllBytes(path)
        // Coherent old/new ID replacement in the pending body must fail the independent anchor hash.
        Files.write(path, original.toString(Charsets.UTF_8).replace(pending.nextRecord.installationId.value, "f".repeat(32)).toByteArray())
        assertTrue(repository(fs).load(installation) is LinuxStorageRepository.State.Blocked)
        Files.write(path, original)
        val other = LinuxConnectionProfile.propose(draft(host = "192.0.2.99"), installation, pending.previousProfile)
        val profileFiles = fs.files("linux-profile")
        profileFiles.publish(LinuxStorageRepository.PROFILE, profileFiles.read(LinuxStorageRepository.PROFILE), ConnectionProfileV2Codec.encode(other))
        assertTrue(repository(fs).load(installation) is LinuxStorageRepository.State.Blocked)
        assertThrows(LinuxStorageRepository.SaveFailure::class.java) { repo.resumePending(resumeRequest(pending), SyntheticBarrier()) }
        assertEquals(other.toFields(), LinuxConnectionProfile.decode(profileFiles.read(LinuxStorageRepository.PROFILE)!!, installation).toFields())
    }

    @Test fun stalePendingCaptureCannotReapplyCompletedIntent() = HostLinuxStorageFixture().use { fs ->
        val fault = Fault().apply { arm(LinuxStorageIoStage.RENAMED, LinuxStorageRepository.RECORD) }
        val repo = repository(fs, fault = fault)
        val failure = assertThrows(LinuxStorageRepository.SaveFailure::class.java) { save(repo) }
        val pending = (failure.reloaded as LinuxStorageRepository.State.Pending).capture
        repository(fs).resumePending(resumeRequest(pending), SyntheticBarrier())
        val before = snapshot(fs)
        assertThrows(LinuxStorageRepository.SaveFailure::class.java) { repo.resumePending(resumeRequest(pending), SyntheticBarrier()) }
        assertSameSnapshot(before, snapshot(fs))
    }
}
