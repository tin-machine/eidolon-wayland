package io.github.tinmachine.eidolon

import java.nio.file.Files
import java.security.SecureRandom
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/** V09: real host descriptor I/O. Synthetic barrier input tests validation, not Quest reclamation. */
class LinuxDesktopSettingsRepositoryTest {
    private val installation = "1".repeat(32)
    private val key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB"
    private val otherKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIC"
    private val configCase = checkNotNull(javaClass.classLoader?.getResourceAsStream("linux-desktop-v3-configuration.json"))
        .bufferedReader().use { JSONObject(it.readText()).getJSONArray("valid").getJSONObject(0) }
    private val config = configCase.getString("raw")
    private val pins = "{\"schema\":1,\"backend\":\"linux_desktop\",\"base_bundle_sha256\":\"${"1".repeat(64)}\",\"desktop_bundle_sha256\":\"${"2".repeat(64)}\",\"waypipe_sha256\":\"${"3".repeat(64)}\",\"shm_fixture_sha256\":\"${"4".repeat(64)}\",\"desktop_binary_sha256\":\"${"5".repeat(64)}\"}"
    private fun draft(host: String = "192.0.2.10", user: String = "linux-user", hostKey: String = key) =
        LinuxConnectionDraft("Linux", host, "22", user, hostKey)
    private class RandomBytes : SecureRandom() {
        var allocations = 0
        override fun nextBytes(bytes: ByteArray) { allocations++; bytes.fill(allocations.toByte()) }
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
                fired = true; error("INJECTED_IO_FAILURE")
            }
        }
    }
    private class Barrier(private val defect: String? = null) : LinuxStorageRepository.ReclamationBarrier {
        override fun <T> withReclaimed(captureToken: String, action: (LinuxStorageRepository.ReclamationEvidence) -> T): T {
            val result = action(LinuxStorageRepository.ReclamationEvidence(
                if (defect == "token") "0".repeat(64) else captureToken, if (defect == "runtime") 0 else 41,
                defect != "shutdown", defect != "destroy", if (defect == "connections") 1 else 0,
                if (defect == "workers") 1 else 0, if (defect == "targets") 1 else 0,
                if (defect == "panels") 1 else 0, if (defect == "sdk") 1 else 0,
                if (defect == "board") 1 else 0, if (defect == "operations") 1 else 0, defect == "remote"))
            if (defect == "after") error("BARRIER_FAILED_AFTER_ACTION")
            return result
        }
    }
    private fun e1(fs: HostLinuxStorageFixture, random: RandomBytes = RandomBytes(), fault: Fault = Fault()) =
        LinuxStorageRepository(installation, fs.files(null, fault::invoke), fs.files("linux-profile", fault::invoke),
            fs.files("linux-storage-v3", fault::invoke), random)
    private fun repo(fs: HostLinuxStorageFixture, random: RandomBytes = RandomBytes(), settingsFault: Fault = Fault(),
                     storageFault: Fault = Fault()) = LinuxDesktopSettingsRepository(installation, e1(fs, random, storageFault),
        fs.files("linux-profile", storageFault::invoke), fs.files("linux-desktop-v3", settingsFault::invoke),
        fs.files("linux-desktop", settingsFault::invoke))
    private fun save(repo: LinuxDesktopSettingsRepository, input: LinuxConnectionDraft? = draft(),
                     backend: LinuxDesktopBackend = LinuxDesktopBackend.V3, raw: String = config) =
        repo.save(repo.prepare(checkNotNull(repo.load().captureToken), input, backend, raw, true), Barrier())
    private fun assertBlocked(snapshot: LinuxDesktopSettingsRepository.Snapshot) {
        assertEquals(LinuxDesktopSettingsRepository.Status.BLOCKED, snapshot.status)
        assertFalse(snapshot.ready); assertFalse(snapshot.legacyAllowed)
    }
    private fun files(fs: HostLinuxStorageFixture): Map<String, List<Byte>> = Files.walk(fs.directory).use { stream ->
        stream.filter { Files.isRegularFile(it) }.toList().associate { fs.directory.relativize(it).toString() to Files.readAllBytes(it).toList() }
    }

    @Test fun readOnlyFreshStateAndFullV3SaveBindExactlyOneProfileRecordAndMarker() = HostLinuxStorageFixture().use { fs ->
        val random = RandomBytes(); val repository = repo(fs, random)
        val empty = repository.load()
        assertEquals(LinuxDesktopSettingsRepository.Status.UNCONFIGURED, empty.status)
        assertTrue(empty.legacyAllowed); assertEquals(0, files(fs).size)
        val request = repository.prepare(empty.captureToken!!, draft(), LinuxDesktopBackend.V3, config, true)
        assertNotEquals(empty.captureToken, request.captureToken)
        assertEquals(0, files(fs).size)
        val saved = repository.save(request, Barrier())
        assertEquals(LinuxDesktopSettingsRepository.Status.READY_V3, saved.status)
        assertEquals(request.intentToken, saved.intentToken)
        assertEquals(2, random.allocations); assertEquals("1", saved.profile!!.revision)
        assertNotNull(saved.v3Configuration!!.forProfile(saved.profile, saved.storageRecord))
        assertEquals(configCase.getString("sha256"), saved.v3Configuration.configuration.sha256())
        assertFalse(saved.legacyAllowed)
        val marker = LinuxDesktopActiveBackendRepository.decode(fs.files("linux-desktop").read("active-backend.json")!!)
        assertEquals(saved.profile.binding(), marker.profileBinding)
        assertEquals(saved.storageRecord!!.sha256(), marker.storageRecordDigest)
        assertEquals(saved.v3Configuration.configuration.sha256(), marker.configurationDigest)
        assertEquals(saved.captureToken, repo(fs, random).load().captureToken)
    }

    @Test fun profileChangesReuseE1IdentityRulesAndConfigOnlyDoesNotAdvanceRevision() = HostLinuxStorageFixture().use { fs ->
        val random = RandomBytes(); val repository = repo(fs, random)
        val a = save(repository)
        val unchanged = save(repository, input = null)
        assertEquals("1", unchanged.profile!!.revision)
        assertEquals(a.storageRecord, unchanged.storageRecord)
        val b = save(repository, draft(host = "192.0.2.11"))
        assertEquals("2", b.profile!!.revision)
        assertEquals(a.storageRecord!!.installationId, b.storageRecord!!.installationId)
        assertEquals(a.storageRecord.profileStorageId, b.storageRecord.profileStorageId)
        val c = save(repository, draft(hostKey = otherKey))
        val d = save(repository, draft(hostKey = otherKey, user = "another-user"))
        assertEquals("4", d.profile!!.revision)
        assertEquals(a.storageRecord.installationId, d.storageRecord!!.installationId)
        assertNotEquals(b.storageRecord.profileStorageId, c.storageRecord!!.profileStorageId)
        assertNotEquals(c.storageRecord.profileStorageId, d.storageRecord.profileStorageId)
        assertEquals(4, random.allocations)
        val edited = repository.save(repository.prepareProfile(d.captureToken!!, draft(hostKey = otherKey, user = "another-user"), true), Barrier())
        assertEquals("5", edited.profile!!.revision); assertEquals(4, random.allocations)
        assertNotNull(edited.v3Configuration!!.forProfile(edited.profile, edited.storageRecord))
    }

    @Test fun legacyV2IsRecognizedOnlyWithoutManagedWitnessesAndEnrollmentAdvancesOnce(): Unit = HostLinuxStorageFixture().use { fs ->
        val profile = LinuxConnectionProfile.propose(draft(), installation, null)
        fs.files("linux-profile").apply { create(); publish("remote-linux.json", null, ConnectionProfileV2Codec.encode(profile)) }
        fs.files("linux-desktop").apply { create(); publish("desktop-pins.json", null,
            LinuxDesktopPinsRepository.encode(profile, LinuxDesktopPins.parse(pins))) }
        val repository = repo(fs)
        val legacy = repository.load()
        assertEquals(LinuxDesktopSettingsRepository.Status.LEGACY_V2, legacy.status)
        assertTrue(legacy.legacyAllowed); assertEquals(pins, legacy.activeRawConfig())
        val managed = save(repository, input = null)
        assertEquals("2", managed.profile!!.revision)
        assertEquals(LinuxDesktopSettingsRepository.Status.READY_V3, managed.status)
        assertThrows(IllegalArgumentException::class.java) {
            LinuxDesktopSettingsRepository.requireLegacyWritesAllowed(e1(fs).load(installation), true, true)
        }
    }

    @Test fun explicitV3ToV2RetainsStorageAndV3ConfigWithoutChangingRevision() = HostLinuxStorageFixture().use { fs ->
        val random = RandomBytes(); val repository = repo(fs, random)
        val v3 = save(repository)
        val beforeConfig = fs.files("linux-desktop-v3").read("connection.json")!!
        val beforeRecord = fs.files("linux-storage-v3").read("record.json")!!
        val v2 = save(repository, null, LinuxDesktopBackend.V2, pins)
        assertEquals(LinuxDesktopSettingsRepository.Status.READY_V2, v2.status)
        assertEquals(v3.profile!!.revision, v2.profile!!.revision)
        assertEquals(v3.storageRecord, v2.storageRecord); assertEquals(2, random.allocations)
        assertArrayEquals(beforeConfig, fs.files("linux-desktop-v3").read("connection.json"))
        assertArrayEquals(beforeRecord, fs.files("linux-storage-v3").read("record.json"))
        val marker = LinuxDesktopActiveBackendRepository.decode(fs.files("linux-desktop").read("active-backend.json")!!)
        assertNull(marker.storageRecordDigest)
        // Independently fixed SHA-256 of the canonical fixture pins, not a runtime-produced oracle.
        assertEquals("eb54ef29cbc5afc6160909d6ebb7d0e8adbd4c912a17fe4a6ee6a3e0477a07fe", marker.configurationDigest)
        val back = save(repository, null)
        assertEquals("1", back.profile!!.revision); assertEquals(v3.storageRecord, back.storageRecord)
    }

    @Test fun firstV2ManagedTransactionDoesNotEnrollStorageAndProfileEditStillUsesCapture() = HostLinuxStorageFixture().use { fs ->
        val random = RandomBytes(); val repository = repo(fs, random)
        val first = save(repository, draft(), LinuxDesktopBackend.V2, pins)
        assertEquals(LinuxDesktopSettingsRepository.Status.READY_V2, first.status)
        assertEquals(0, random.allocations); assertNull(first.storageRecord)
        val edited = repository.save(repository.prepareProfile(first.captureToken!!, draft(host = "192.0.2.11"), true), Barrier())
        assertEquals("2", edited.profile!!.revision)
        assertEquals(0, random.allocations)
        assertEquals(edited.profile.binding(), edited.v2Pins!!.profileBinding)
    }

    @Test fun v2ProfileEditAfterV3EnrollmentStillUsesE1AndRetainsTheV3Configuration() = HostLinuxStorageFixture().use { fs ->
        val random = RandomBytes(); val repository = repo(fs, random)
        val first = save(repository)
        val v2 = save(repository, null, LinuxDesktopBackend.V2, pins)
        val retained = fs.files("linux-desktop-v3").read("connection.json")!!
        val edited = repository.save(repository.prepareProfile(v2.captureToken!!, draft(user = "new-user"), true), Barrier())
        assertEquals(LinuxDesktopSettingsRepository.Status.READY_V2, edited.status)
        assertEquals("2", edited.profile!!.revision)
        assertEquals(first.storageRecord!!.installationId, edited.storageRecord!!.installationId)
        assertNotEquals(first.storageRecord.profileStorageId, edited.storageRecord.profileStorageId)
        assertEquals(3, random.allocations)
        assertArrayEquals(retained, fs.files("linux-desktop-v3").read("connection.json"))
        assertNull(edited.v3Configuration!!.forProfile(edited.profile, edited.storageRecord))
        assertEquals(edited.profile.binding(), edited.v2Pins!!.profileBinding)
    }

    @Test fun allBarrierDefectsBlockEverySettingsAndE1Write() {
        for (defect in listOf("token", "runtime", "shutdown", "destroy", "connections", "workers", "targets", "panels", "sdk", "board", "operations", "remote")) {
            HostLinuxStorageFixture().use { fs ->
                val random = RandomBytes(); val repository = repo(fs, random)
                val request = repository.prepare(repository.load().captureToken!!, draft(), LinuxDesktopBackend.V3, config, true)
                assertThrows(defect, LinuxDesktopSettingsRepository.SaveFailure::class.java) { repository.save(request, Barrier(defect)) }
                assertEquals(defect, 0, files(fs).size); assertEquals(defect, 0, random.allocations)
            }
        }
    }

    @Test fun everyConfigurationAndMarkerCutResumesWithoutNewIdsOrExtraRevision() {
        val cuts = listOf(
            LinuxStorageIoStage.RENAMED to "transaction.json",
            LinuxStorageIoStage.READBACK to "transaction.json",
            LinuxStorageIoStage.BEFORE_RENAME to "connection.json",
            LinuxStorageIoStage.RENAMED to "connection.json",
            LinuxStorageIoStage.READBACK to "connection.json",
            LinuxStorageIoStage.BEFORE_RENAME to "active-backend.json",
            LinuxStorageIoStage.RENAMED to "active-backend.json",
            LinuxStorageIoStage.READBACK to "active-backend.json",
        )
        for ((stage, name) in cuts) HostLinuxStorageFixture().use { fs ->
            val random = RandomBytes(); val fault = Fault().apply { arm(stage, name) }
            val repository = repo(fs, random, settingsFault = fault)
            val request = repository.prepare(repository.load().captureToken!!, draft(), LinuxDesktopBackend.V3, config, true)
            val failure = assertThrows("$stage/$name", LinuxDesktopSettingsRepository.SaveFailure::class.java) { repository.save(request, Barrier()) }
            assertTrue("$stage/$name", fault.fired)
            val pending = failure.reloaded
            assertEquals(request.intentToken, pending.intentToken)
            assertTrue("$stage/$name", pending.canResume || pending.ready)
            val restarted = repo(fs, random)
            val saved = restarted.resume(restarted.load().captureToken!!, Barrier())
            assertEquals(LinuxDesktopSettingsRepository.Status.READY_V3, saved.status)
            assertEquals("1", saved.profile!!.revision); assertEquals(2, random.allocations)
            val again = restarted.resume(saved.captureToken!!, Barrier())
            assertEquals(saved.captureToken, again.captureToken); assertEquals(2, random.allocations)
        }
    }

    @Test fun e1PartialSaveUsesItsExactPendingAndEarlyLostIntentCannotBeReenrolled() {
        for (name in listOf("remote-linux.json", "record.json", "commit.json")) HostLinuxStorageFixture().use { fs ->
            val random = RandomBytes(); val fault = Fault().apply { arm(LinuxStorageIoStage.RENAMED, name) }
            val repository = repo(fs, random, storageFault = fault)
            val failure = assertThrows(LinuxDesktopSettingsRepository.SaveFailure::class.java) { save(repository) }
            assertTrue(fault.fired); assertTrue(failure.reloaded.canResume)
            val pending = (e1(fs, random).load(installation) as LinuxStorageRepository.State.Pending).capture
            val result = repo(fs, random).resume(failure.reloaded.captureToken!!, Barrier())
            assertEquals(pending.nextRecord, result.storageRecord); assertEquals("1", result.profile!!.revision)
            assertEquals(2, random.allocations)
        }
        HostLinuxStorageFixture().use { fs ->
            val random = RandomBytes(); val fault = Fault().apply { arm(LinuxStorageIoStage.RENAMED, LinuxStorageRepository.ANCHOR) }
            val repository = repo(fs, random, storageFault = fault)
            val failure = assertThrows(LinuxDesktopSettingsRepository.SaveFailure::class.java) { save(repository) }
            assertBlocked(failure.reloaded); assertEquals(0, random.allocations)
            assertBlocked(repo(fs, random).load()); assertEquals(0, random.allocations)
        }
    }

    @Test fun changedSavePendingKeepsOldSettingsAndRejectsMixedOldMarker() = HostLinuxStorageFixture().use { fs ->
        val random = RandomBytes(); val fault = Fault(); val repository = repo(fs, random, settingsFault = fault)
        val a = save(repository)
        val oldMarker = fs.files("linux-desktop").read("active-backend.json")!!
        fault.arm(LinuxStorageIoStage.BEFORE_RENAME, "active-backend.json")
        val failure = assertThrows(LinuxDesktopSettingsRepository.SaveFailure::class.java) { save(repository, draft(user = "next-user")) }
        assertEquals(LinuxDesktopSettingsRepository.Status.SAVE_PENDING, failure.reloaded.status)
        assertFalse(failure.reloaded.ready)
        assertArrayEquals(oldMarker, fs.files("linux-desktop").read("active-backend.json"))
        assertNotEquals(a.profile!!.binding(), failure.reloaded.profile!!.binding())
        val resumed = repo(fs, random).resume(failure.reloaded.captureToken!!, Barrier())
        assertEquals("2", resumed.profile!!.revision); assertEquals(3, random.allocations)
        assertNotEquals(a.storageRecord!!.profileStorageId, resumed.storageRecord!!.profileStorageId)
    }

    @Test fun missingMarkerRequiresExplicitResumeAndCorruptWitnessNeverFallsBackToV2() {
        HostLinuxStorageFixture().use { fs ->
            val repository = repo(fs); val saved = save(repository)
            Files.delete(fs.directory.resolve("linux-desktop/active-backend.json"))
            val pending = repository.load()
            assertEquals(LinuxDesktopSettingsRepository.Status.SAVE_PENDING, pending.status)
            assertFalse(pending.legacyAllowed); assertFalse(pending.ready)
            val resumed = repository.resume(pending.captureToken!!, Barrier())
            assertEquals(saved.storageRecord, resumed.storageRecord); assertEquals("1", resumed.profile!!.revision)
        }
        for (target in listOf("linux-desktop/active-backend.json", "linux-desktop-v3/connection.json",
            "linux-desktop-v3/transaction.json", "linux-storage-v3-anchor.json")) HostLinuxStorageFixture().use { fs ->
            val repository = repo(fs); save(repository)
            Files.write(fs.directory.resolve(target), "{bad".toByteArray(Charsets.UTF_8))
            assertBlocked(repository.load())
        }
        HostLinuxStorageFixture().use { fs ->
            val repository = repo(fs); save(repository)
            Files.delete(fs.directory.resolve("linux-desktop-v3/transaction.json"))
            assertBlocked(repository.load())
        }
        HostLinuxStorageFixture().use { fs ->
            fs.files("linux-desktop-v3").create()
            assertBlocked(repo(fs).load())
        }
    }

    @Test fun staleSnapshotCompetingSaveAndTamperedPartialFilesNeverOverwrite() = HostLinuxStorageFixture().use { fs ->
        val random = RandomBytes(); val repository = repo(fs, random)
        val snapshot = repository.load()
        val a = repository.prepare(snapshot.captureToken!!, draft(), LinuxDesktopBackend.V3, config, true)
        val b = repository.prepare(snapshot.captureToken, draft(host = "192.0.2.11"), LinuxDesktopBackend.V3, config, true)
        assertNotEquals(a.captureToken, b.captureToken)
        repository.save(a, Barrier())
        val before = files(fs)
        assertThrows(LinuxDesktopSettingsRepository.SaveFailure::class.java) { repository.save(b, Barrier()) }
        assertEquals(before, files(fs)); assertEquals(2, random.allocations)
        assertThrows(IllegalArgumentException::class.java) { repository.prepare(snapshot.captureToken, null, LinuxDesktopBackend.V3, config, true) }
        // Neither a matching profile binding nor an old valid marker authorizes unrelated content.
        val markerPath = fs.directory.resolve("linux-desktop/active-backend.json")
        val marker = JSONObject(String(Files.readAllBytes(markerPath), Charsets.UTF_8)).put("configuration_sha256", "f".repeat(64))
        Files.write(markerPath, marker.toString().toByteArray(Charsets.UTF_8))
        assertBlocked(repository.load())
    }

    @Test fun parallelRepositoriesHaveOneCompositeCaptureWinner() = HostLinuxStorageFixture().use { fs ->
        val random = RandomBytes(); val a = repo(fs, random); val b = repo(fs, random)
        val capture = a.load().captureToken!!
        val ra = a.prepare(capture, draft(), LinuxDesktopBackend.V3, config, true)
        val rb = b.prepare(capture, draft(host = "192.0.2.11"), LinuxDesktopBackend.V3, config, true)
        val pool = Executors.newFixedThreadPool(2); val start = CountDownLatch(1)
        try {
            val results = listOf(a to ra, b to rb).map { (repository, request) -> pool.submit<Boolean> {
                check(start.await(10, TimeUnit.SECONDS))
                try { repository.save(request, Barrier()); true } catch (_: LinuxDesktopSettingsRepository.SaveFailure) { false }
            } }
            start.countDown()
            assertEquals(1, results.count { it.get(10, TimeUnit.SECONDS) })
            assertEquals(2, random.allocations)
            assertEquals(a.load().captureToken, b.load().captureToken)
        } finally { pool.shutdownNow(); check(pool.awaitTermination(10, TimeUnit.SECONDS)) }
    }

    @Test fun markerRejectsUnknownRevisionMixedStorageUnknownFieldAndRawDuplicateKey() = HostLinuxStorageFixture().use { fs ->
        save(repo(fs))
        val original = fs.files("linux-desktop").read("active-backend.json")!!.toString(Charsets.UTF_8)
        val invalid = listOf(
            JSONObject(original).put("protocol_revision", "eidolon-linux-desktop-v4").toString(),
            JSONObject(original).put("protocol_revision", "eidolon-linux-desktop-v2").toString(),
            JSONObject(original).put("storage_record_sha256", JSONObject.NULL).toString(),
            JSONObject(original).put("unknown", false).toString(),
            original.replaceFirst("{", "{\"schema\":1,"),
        )
        for (raw in invalid) assertThrows(IllegalArgumentException::class.java) {
            LinuxDesktopActiveBackendRepository.decode(raw.toByteArray())
        }
    }

    @Test fun failedFinalReadbackIsReportedAsFailureEvenWhenAllFilesMatch() = HostLinuxStorageFixture().use { fs ->
        val random = RandomBytes(); val repository = repo(fs, random)
        val request = repository.prepare(repository.load().captureToken!!, draft(), LinuxDesktopBackend.V3, config, true)
        val failure = assertThrows(LinuxDesktopSettingsRepository.SaveFailure::class.java) { repository.save(request, Barrier("after")) }
        assertTrue(failure.reloaded.ready)
        assertEquals("SETTINGS_SAVE_FAILED", failure.message)
        assertEquals(request.intentToken, failure.reloaded.intentToken)
        val resumed = repository.resume(failure.reloaded.captureToken!!, Barrier())
        assertEquals("1", resumed.profile!!.revision); assertEquals(2, random.allocations)
    }

    @Test fun rawDuplicateImportAndImportByteBoundsPrecedeAnyDiskMutation() = HostLinuxStorageFixture().use { fs ->
        val repository = repo(fs)
        val token = repository.load().captureToken!!
        val duplicate = config.replaceFirst("{", "{\"schema\":1,")
        assertThrows(IllegalArgumentException::class.java) { repository.prepare(token, draft(), LinuxDesktopBackend.V3, duplicate, true) }
        assertThrows(IllegalArgumentException::class.java) { repository.prepare(token, draft(), LinuxDesktopBackend.V3, config + '\uD800', true) }
        assertThrows(IllegalArgumentException::class.java) { repository.prepare(token, draft(), LinuxDesktopBackend.V3, config + " ".repeat(32769), true) }
        assertThrows(IllegalArgumentException::class.java) { repository.prepare(token, draft(), LinuxDesktopBackend.V3, config, false) }
        assertEquals(0, files(fs).size)
        val padded = config + " ".repeat(LinuxDesktopV3Config.MAX_BYTES - config.toByteArray().size)
        val saved = repository.save(repository.prepare(token, draft(), LinuxDesktopBackend.V3, padded, true), Barrier())
        assertTrue(saved.ready)
        assertTrue(fs.files("linux-desktop-v3").read("connection.json")!!.size < 16384)
    }
}
