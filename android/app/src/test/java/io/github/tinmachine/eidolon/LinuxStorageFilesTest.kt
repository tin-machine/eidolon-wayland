package io.github.tinmachine.eidolon

import java.io.IOException
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermissions
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import org.junit.Assert.*
import org.junit.Test

/** Host Linux real filesystem coverage of the same algorithm used by AndroidLinuxStorageFiles. */
class LinuxStorageFilesTest {
    private val first = "first".toByteArray()
    private val second = "second".toByteArray()
    private fun permission(path: Path, mode: String) = Files.setPosixFilePermissions(path, PosixFilePermissions.fromString(mode))
    private fun write(path: Path, value: ByteArray) {
        Files.write(path, value)
        permission(path, "rw-------")
    }

    @Test fun readDoesNotCreateDirectoryAndWritesUsePrivateFilesAndDurableOrder(): Unit = HostLinuxStorageFixture().use { f ->
        val calls = mutableListOf<LinuxStorageIoStage>()
        val store = f.files("linux-storage-v3") { stage, _ -> calls.add(stage) }
        assertFalse(store.exists())
        assertNull(store.read("record.json"))
        assertFalse(Files.exists(f.directory.resolve("linux-storage-v3")))
        store.create()
        assertThrows(IllegalArgumentException::class.java) { store.create() }
        calls.clear()
        store.publish("record.json", null, first)
        assertArrayEquals(first, f.files("linux-storage-v3").read("record.json"))
        assertEquals(listOf(LinuxStorageIoStage.TEMP_CREATED, LinuxStorageIoStage.TEMP_WRITTEN,
            LinuxStorageIoStage.FILE_SYNCED, LinuxStorageIoStage.TEMP_VERIFIED,
            LinuxStorageIoStage.BEFORE_EXPECTED_RECHECK, LinuxStorageIoStage.BEFORE_RENAME,
            LinuxStorageIoStage.RENAMED, LinuxStorageIoStage.DIRECTORY_SYNCED, LinuxStorageIoStage.READBACK), calls)
        val dir = f.directory.resolve("linux-storage-v3")
        val file = dir.resolve("record.json")
        assertEquals("rwx------", PosixFilePermissions.toString(Files.getPosixFilePermissions(dir)))
        assertEquals("rw-------", PosixFilePermissions.toString(Files.getPosixFilePermissions(file)))
        assertEquals(1, (Files.getAttribute(file, "unix:nlink") as Number).toInt())
        assertEquals(HostLinuxStoragePosix.uid, (Files.getAttribute(file, "unix:uid") as Number).toInt())
        assertFalse(Files.exists(dir.resolve("record.json.tmp")))
    }

    @Test fun rootUsesVerifiedFrameworkModeAndRejectsWrongPermissionOrOwner(): Unit = HostLinuxStorageFixture().use { f ->
        permission(f.directory, "rwxrwx--x")
        f.root.publish("linux-storage-v3-anchor.json", null, first)
        assertArrayEquals(first, f.root.read("linux-storage-v3-anchor.json"))
        permission(f.directory, "rwxr-xr-x")
        assertThrows(IllegalArgumentException::class.java) { f.root.exists() }
        permission(f.directory, "rwx------")
        val differentExpectedUid = object : LinuxStoragePosix by HostLinuxStoragePosix {
            override val uid = HostLinuxStoragePosix.uid + 1
        }
        val store = SecureLinuxStorageFiles(f.directory.toFile(), null, differentExpectedUid)
        assertThrows(IllegalArgumentException::class.java) { store.read("linux-storage-v3-anchor.json") }
    }

    @Test fun regularFileModeLinksSizeAndNoFollowAreEnforced() {
        for (case in listOf("symlink", "hardlink", "directory", "permissions", "empty", "oversized")) {
            HostLinuxStorageFixture().use { f ->
                val store = f.files("linux-storage-v3"); store.create()
                val file = f.directory.resolve("linux-storage-v3/record.json")
                val other = f.directory.resolve("other.json")
                write(other, first)
                when (case) {
                    "symlink" -> Files.createSymbolicLink(file, other)
                    "hardlink" -> Files.createLink(file, other)
                    "directory" -> Files.createDirectory(file)
                    "permissions" -> { write(file, first); permission(file, "rw-r--r--") }
                    "empty" -> write(file, byteArrayOf())
                    "oversized" -> write(file, ByteArray(16 * 1024 + 1))
                }
                assertThrows(case, IllegalArgumentException::class.java) { store.read("record.json") }
                assertThrows(case, IllegalArgumentException::class.java) { store.publish("record.json", null, second) }
                assertArrayEquals(first, Files.readAllBytes(other))
            }
        }
    }

    @Test fun unsafeChildDirectoryAndRootSymlinkAreRejected(): Unit = HostLinuxStorageFixture().use { f ->
        val child = f.directory.resolve("linux-storage-v3")
        Files.createSymbolicLink(child, f.directory)
        assertThrows(IllegalArgumentException::class.java) { f.files("linux-storage-v3").exists() }
        Files.delete(child)
        Files.createDirectory(child)
        permission(child, "rwxr-xr-x")
        assertThrows(IllegalArgumentException::class.java) { f.files("linux-storage-v3").read("record.json") }
        val rootLink = f.directory.resolve("root-link")
        Files.createSymbolicLink(rootLink, f.directory)
        val rootStore = SecureLinuxStorageFiles(rootLink.toFile(), null, HostLinuxStoragePosix)
        assertThrows(IllegalArgumentException::class.java) { rootStore.exists() }
    }

    @Test fun noFollowAlsoRejectsSymlinkInsertedBetweenLstatAndOpen(): Unit = HostLinuxStorageFixture().use { f ->
        val setup = f.files("linux-storage-v3"); setup.create(); setup.publish("record.json", null, first)
        val target = f.directory.resolve("linux-storage-v3/record.json")
        val other = f.directory.resolve("other.json"); write(other, second)
        val raced = object : LinuxStoragePosix by HostLinuxStoragePosix {
            override fun openRead(path: String): LinuxStoragePosix.Descriptor {
                if (path == target.toString()) { Files.delete(target); Files.createSymbolicLink(target, other) }
                return HostLinuxStoragePosix.openRead(path)
            }
        }
        val store = SecureLinuxStorageFiles(f.directory.toFile(), "linux-storage-v3", raced)
        assertThrows(IOException::class.java) { store.read("record.json") }
        assertArrayEquals(second, Files.readAllBytes(other))
    }

    @Test fun exclusiveCreateRefusesTempInsertedAfterAbsenceCheck(): Unit = HostLinuxStorageFixture().use { f ->
        val setup = f.files("linux-storage-v3"); setup.create(); setup.publish("record.json", null, first)
        val temp = f.directory.resolve("linux-storage-v3/record.json.tmp")
        val raced = object : LinuxStoragePosix by HostLinuxStoragePosix {
            override fun createExclusive(path: String): LinuxStoragePosix.Descriptor {
                write(Path.of(path), second)
                return HostLinuxStoragePosix.createExclusive(path)
            }
        }
        val store = SecureLinuxStorageFiles(f.directory.toFile(), "linux-storage-v3", raced)
        assertThrows(IOException::class.java) { store.publish("record.json", first, second) }
        assertArrayEquals(first, Files.readAllBytes(f.directory.resolve("linux-storage-v3/record.json")))
        assertArrayEquals(second, Files.readAllBytes(temp))
    }

    @Test fun anyLeftoverTempFailsClosedAndIsNotDeletedOrReused() {
        for (case in listOf("empty", "symlink", "hardlink")) HostLinuxStorageFixture().use { f ->
            val store = f.files("linux-storage-v3"); store.create()
            store.publish("record.json", null, first)
            val temp = f.directory.resolve("linux-storage-v3/record.json.tmp")
            val other = f.directory.resolve("other.json"); write(other, second)
            when (case) {
                "empty" -> write(temp, byteArrayOf())
                "symlink" -> Files.createSymbolicLink(temp, other)
                "hardlink" -> Files.createLink(temp, other)
            }
            assertThrows(IllegalArgumentException::class.java) { store.read("record.json") }
            assertThrows(IllegalArgumentException::class.java) { store.publish("record.json", first, second) }
            assertTrue(Files.exists(temp))
            assertArrayEquals(first, Files.readAllBytes(f.directory.resolve("linux-storage-v3/record.json")))
            assertArrayEquals(second, Files.readAllBytes(other))
        }
    }

    @Test fun profileUsesLegacyTempAndCannotBypassEitherInterruptedSpelling() {
        for (spelling in listOf("remote-linux.tmp", "remote-linux.json.tmp")) HostLinuxStorageFixture().use { f ->
            var usedLegacyTemp = false
            val profileDirectory = f.directory.resolve("linux-profile")
            val store = f.files("linux-profile") { stage, _ ->
                if (stage == LinuxStorageIoStage.TEMP_CREATED) {
                    usedLegacyTemp = Files.exists(profileDirectory.resolve("remote-linux.tmp")) &&
                        !Files.exists(profileDirectory.resolve("remote-linux.json.tmp"))
                }
            }
            store.create()
            store.publish("remote-linux.json", null, first)
            val interrupted = profileDirectory.resolve(spelling)
            write(interrupted, "unpublished profile".toByteArray())
            assertThrows(spelling, IllegalArgumentException::class.java) {
                store.publish("remote-linux.json", first, second)
            }
            assertThrows(spelling, IllegalArgumentException::class.java) { store.read("remote-linux.json") }
            assertArrayEquals(first, Files.readAllBytes(profileDirectory.resolve("remote-linux.json")))
            assertArrayEquals("unpublished profile".toByteArray(), Files.readAllBytes(interrupted))
            assertTrue("new E1 profile writes must share legacy O_EXCL temp", usedLegacyTemp)
        }
    }

    @Test fun injectedFailureAtEveryPublishBoundaryNeverRollsBackAfterRename() {
        for (point in LinuxStorageIoStage.entries.filter { it != LinuxStorageIoStage.DIRECTORY_CREATED }) {
            HostLinuxStorageFixture().use { f ->
                val setup = f.files("linux-storage-v3"); setup.create(); setup.publish("record.json", null, first)
                val store = f.files("linux-storage-v3") { stage, _ ->
                    if (stage == point) throw IOException("INJECTED_${point.name}")
                }
                assertThrows(point.name, IOException::class.java) { store.publish("record.json", first, second) }
                val afterRename = point in setOf(LinuxStorageIoStage.RENAMED, LinuxStorageIoStage.DIRECTORY_SYNCED, LinuxStorageIoStage.READBACK)
                assertArrayEquals(point.name, if (afterRename) second else first, setup.read("record.json"))
                assertFalse(Files.exists(f.directory.resolve("linux-storage-v3/record.json.tmp")))
            }
        }
    }

    @Test fun failedDirectoryCreationLeavesWitnessAndNeverBecomesMissing(): Unit = HostLinuxStorageFixture().use { f ->
        val store = f.files("linux-storage-v3") { stage, _ ->
            if (stage == LinuxStorageIoStage.DIRECTORY_CREATED) throw IOException("INJECTED_DIRECTORY_FAILURE")
        }
        assertThrows(IOException::class.java) { store.create() }
        assertTrue(f.files("linux-storage-v3").exists())
        assertThrows(IllegalArgumentException::class.java) { f.files("linux-storage-v3").create() }
    }

    @Test fun expectedBytesAreRecheckedAfterTempSyncAndBeforeRename() {
        for (point in listOf(LinuxStorageIoStage.BEFORE_EXPECTED_RECHECK, LinuxStorageIoStage.BEFORE_RENAME)) {
            HostLinuxStorageFixture().use { f ->
                val setup = f.files("linux-storage-v3"); setup.create(); setup.publish("record.json", null, first)
                val third = "another writer".toByteArray()
                val store = f.files("linux-storage-v3") { stage, _ ->
                    if (stage == point) write(f.directory.resolve("linux-storage-v3/record.json"), third)
                }
                assertThrows(IllegalArgumentException::class.java) { store.publish("record.json", first, second) }
                assertArrayEquals(third, setup.read("record.json"))
            }
        }
    }

    @Test fun directoryReplacementIsDetectedAndDoesNotPublishIntoNewDirectory(): Unit = HostLinuxStorageFixture().use { f ->
        val setup = f.files("linux-storage-v3"); setup.create(); setup.publish("record.json", null, first)
        val original = f.directory.resolve("linux-storage-v3")
        val displaced = f.directory.resolve("old-directory")
        val store = f.files("linux-storage-v3") { stage, _ ->
            if (stage == LinuxStorageIoStage.BEFORE_RENAME) {
                Files.move(original, displaced)
                Files.createDirectory(original); permission(original, "rwx------")
            }
        }
        assertThrows(Exception::class.java) { store.publish("record.json", first, second) }
        assertFalse(Files.exists(original.resolve("record.json")))
        assertArrayEquals(first, Files.readAllBytes(displaced.resolve("record.json")))
        assertTrue(Files.exists(displaced.resolve("record.json.tmp")))
    }

    @Test fun readbackCorruptionRejectsSuccessWithoutRestoringOldBytes(): Unit = HostLinuxStorageFixture().use { f ->
        val setup = f.files("linux-storage-v3"); setup.create(); setup.publish("record.json", null, first)
        val corrupt = "corrupt".toByteArray()
        val store = f.files("linux-storage-v3") { stage, _ ->
            if (stage == LinuxStorageIoStage.DIRECTORY_SYNCED) write(f.directory.resolve("linux-storage-v3/record.json"), corrupt)
        }
        assertThrows(IllegalArgumentException::class.java) { store.publish("record.json", first, second) }
        assertArrayEquals(corrupt, setup.read("record.json"))
    }

    @Test fun twoInstancesAreSerializedAndOnlyOneSameExpectedWriterCanWin(): Unit = HostLinuxStorageFixture().use { f ->
        val setup = f.files("linux-storage-v3"); setup.create(); setup.publish("record.json", null, first)
        val start = CountDownLatch(1)
        val pool = Executors.newFixedThreadPool(2)
        try {
            val writes = listOf("two", "three").map { value ->
                pool.submit<Boolean> {
                    assertTrue(start.await(5, TimeUnit.SECONDS))
                    try { f.files("linux-storage-v3").publish("record.json", first, value.toByteArray()); true }
                    catch (_: IllegalArgumentException) { false }
                }
            }
            start.countDown()
            val results = writes.map { it.get(5, TimeUnit.SECONDS) }
            assertEquals(1, results.count { it })
            assertTrue(setOf("two", "three").contains(setup.read("record.json")!!.toString(Charsets.UTF_8)))
        } finally {
            pool.shutdownNow()
            assertTrue(pool.awaitTermination(5, TimeUnit.SECONDS))
        }
    }

    @Test fun invalidNamesAndOversizedReplacementNeverCreateFiles(): Unit = HostLinuxStorageFixture().use { f ->
        for (name in listOf("../outside", "a/b", ".", "..", "record.json.tmp", "a\u0000b", "/absolute")) {
            assertThrows(IllegalArgumentException::class.java) { f.root.read(name) }
            assertThrows(IllegalArgumentException::class.java) { f.root.publish(name, null, first) }
        }
        for (value in listOf(byteArrayOf(), ByteArray(16 * 1024 + 1))) {
            assertThrows(IllegalArgumentException::class.java) { f.root.publish("record.json", null, value) }
        }
        assertNull(f.root.read("record.json"))
    }
}
