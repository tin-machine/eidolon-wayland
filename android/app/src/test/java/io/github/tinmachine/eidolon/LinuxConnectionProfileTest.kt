package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class LinuxConnectionProfileTest {
    private val installation = "1".repeat(32)
    private val key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB"
    private fun draft(host: String = "192.0.2.10", name: String = "Linux desktop", user: String = "linux-user", port: String = "22", pin: String = key) =
        LinuxConnectionDraft(name, host, port, user, pin)
    private class Memory : LinuxProfileRepository.Storage {
        var bytes: ByteArray? = null
        var failBefore = false
        var failAfter = false
        var beforePublish: (() -> Unit)? = null
        var publications = 0
        override fun read() = bytes?.copyOf()
        override fun publish(expected: ByteArray?, replacement: ByteArray) {
            beforePublish?.invoke()
            require(if (expected == null) bytes == null else bytes?.contentEquals(expected) == true) { "PROFILE_CHANGED" }
            check(!failBefore) { "WRITE_FAILED" }
            bytes = replacement.copyOf(); publications++
            check(!failAfter) { "READBACK_FAILED" }
        }
    }
    private fun profile(draft: LinuxConnectionDraft = draft()) = LinuxConnectionProfile.propose(draft, installation, null)
    private fun bytes(p: ConnectionProfileV2.Value) = ConnectionProfileV2Codec.encode(p)

    @Test fun firstSaveRequiresPinConfirmationAndKeepsDebugAndTermuxNamespacesSeparate() {
        val memory = Memory(); val store = LinuxProfileRepository(installation, memory)
        assertNull(store.load())
        assertThrows(IllegalArgumentException::class.java) { store.save(draft(), null, false) }
        assertEquals(0, memory.publications)
        val saved = store.save(draft(), null, true)
        assertEquals("remote-linux", saved.profileId); assertEquals("1", saved.revision)
        assertEquals(ConnectionProfileV2.Backend.LINUX_SSH, saved.backend)
        assertNull(saved.legacyRuntimeFields())
        assertEquals(saved.toFields(), store.load()!!.toFields())
        val debug = ConnectionProfileV2.parse(saved.toFields() + ("profile_id" to "linux-probe"), installation)
        memory.bytes = bytes(debug)
        assertThrows(IllegalArgumentException::class.java) { store.load() }
        val termux = ConnectionProfileV2.parse(saved.toFields() + mapOf("profile_id" to "termux-local", "backend" to "termux_local",
            "host" to "127.0.0.1", "port" to 8022, "username" to "u0_a1"), installation)
        memory.bytes = bytes(termux)
        assertThrows(IllegalArgumentException::class.java) { store.load() }
        assertEquals(1, memory.publications)
    }

    @Test fun staleScreenCannotReplaceNewEndpointAndDisplayOnlyChangeAdvancesRevision() {
        val memory = Memory(); val store = LinuxProfileRepository(installation, memory)
        val first = store.save(draft(), null, true)
        val second = store.save(draft(host = "192.0.2.11"), first.binding(), true)
        assertEquals("2", second.revision)
        assertThrows(IllegalArgumentException::class.java) { store.save(draft(), first.binding(), true) }
        assertThrows(IllegalArgumentException::class.java) { store.save(draft(), null, true) }
        val third = store.save(draft(host = second.host, name = "Renamed"), second.binding(), true)
        assertEquals("3", third.revision)
        assertNotEquals(second.binding(), third.binding())
        assertEquals(third.toFields(), store.load()!!.toFields())
    }

    @Test fun secondStoreRaceIsRejectedAtPublicationAndNeverReplacesTheOtherResult() {
        val memory = Memory(); val first = LinuxProfileRepository(installation, memory)
        val second = LinuxProfileRepository(installation, memory)
        val original = first.save(draft(), null, true)
        memory.beforePublish = {
            memory.beforePublish = null
            second.save(draft(host = "192.0.2.12"), original.binding(), true)
        }
        assertThrows(IllegalArgumentException::class.java) { first.save(draft(host = "192.0.2.11"), original.binding(), true) }
        assertEquals("192.0.2.12", first.load()!!.host)
        assertEquals("2", first.load()!!.revision)
    }

    @Test fun failureBeforePublicationRetainsOldBytesAndAfterPublicationRequiresExplicitReload() {
        val memory = Memory(); val store = LinuxProfileRepository(installation, memory)
        val original = store.save(draft(), null, true)
        val before = memory.bytes!!.copyOf()
        memory.failBefore = true
        assertThrows(IllegalStateException::class.java) { store.save(draft(host = "192.0.2.11"), original.binding(), true) }
        assertArrayEquals(before, memory.bytes)
        memory.failBefore = false; memory.failAfter = true
        assertThrows(IllegalStateException::class.java) { store.save(draft(host = "192.0.2.11"), original.binding(), true) }
        memory.failAfter = false
        assertThrows(IllegalArgumentException::class.java) { store.save(draft(), original.binding(), true) }
        assertEquals("192.0.2.11", store.load()!!.host)
        assertEquals("2", store.load()!!.revision)
        assertEquals(2, memory.publications)
    }

    @Test fun unknownOrMalformedSavedFileDoesNotBecomeMissingOrGetAutomaticallyOverwritten() {
        val memory = Memory(); val store = LinuxProfileRepository(installation, memory)
        val valid = bytes(profile())
        for (invalid in listOf(byteArrayOf(0xc3.toByte()), ByteArray(16 * 1024 + 1) { 32 },
            valid.toString(Charsets.UTF_8).replace("\"schema\":2", "\"schema\":2,\"schema\":2").toByteArray(),
            bytes(ConnectionProfileV2.parse(profile().toFields() + ("installation" to "2".repeat(32)), "2".repeat(32))))) {
            memory.bytes = invalid.copyOf()
            assertThrows(IllegalArgumentException::class.java) { store.load() }
            assertThrows(IllegalArgumentException::class.java) { store.save(draft(), null, true) }
            assertArrayEquals(invalid, memory.bytes)
        }
        assertEquals(0, memory.publications)
    }

    @Test fun dnsCanBeSavedButIsExplicitlyUnsupportedWithoutResolutionOrFallback() {
        val named = profile(draft(host = "DESKTOP.example"))
        assertEquals("desktop.example", named.host)
        assertEquals(LinuxConnectionProfile.EndpointSupport.DNS_UNSUPPORTED, LinuxConnectionProfile.endpointSupport(named))
        assertEquals(LinuxConnectionProfile.EndpointSupport.IPV4, LinuxConnectionProfile.endpointSupport(profile()))
        for (port in listOf("0", "65536", "022", "22 ", "22.0", "+22")) {
            assertThrows(IllegalArgumentException::class.java) { profile(draft(port = port)) }
        }
    }

    @Test fun revisionOverflowAndInputDataNeverAppearInDiagnosticString() {
        val max = ConnectionProfileV2.parse(profile().toFields() + ("revision" to Long.MAX_VALUE.toString()), installation)
        assertThrows(IllegalArgumentException::class.java) { LinuxConnectionProfile.propose(draft(), installation, max) }
        val input = draft()
        assertFalse(input.toString().contains(input.host))
        assertFalse(input.toString().contains(input.username))
        assertFalse(input.toString().contains(input.hostKey))
        assertFalse(max.toString().contains(key))
    }
}
