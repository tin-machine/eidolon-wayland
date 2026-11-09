package io.github.tinmachine.eidolon

import java.security.SecureRandom
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/** Reads the actual B1 golden. Its synthetic values do not prove filesystem or remote recovery. */
class LinuxStorageIdentityTest {
    private val fixture: JSONObject = checkNotNull(javaClass.classLoader?.getResourceAsStream("linux-desktop-v3-contract.json"))
        .bufferedReader(Charsets.UTF_8).use { JSONObject(it.readText()) }
    private fun JSONArray.rows(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }
    private val records get() = fixture.getJSONArray("records").rows().associateBy { it.getString("id") }
    private fun raw(name: String) = records.getValue(name).getString("raw").toByteArray(Charsets.UTF_8)
    private fun profile(): ConnectionProfileV2.Value = LinuxConnectionProfile.decode(
        fixture.getJSONObject("profile").toString().toByteArray(Charsets.UTF_8), "a".repeat(32))
    private fun record() = LinuxStorageIdentityCodec.decode(raw("storage"))

    @Test fun canonicalBytesAndHashesMatchSharedStorageCaptureAndJsonVectors() {
        val cases = records.values.filter { it.getString("kind") in setOf("storage", "capture", "json") }
        assertEquals(6, cases.size)
        cases.forEach { case ->
            val bytes = case.getString("raw").toByteArray(Charsets.UTF_8)
            val actual = when (case.getString("kind")) {
                "storage" -> LinuxStorageIdentityCodec.decode(bytes).canonical()
                "capture" -> LinuxStorageIdentityCodec.decodeCapture(bytes).canonical()
                else -> LinuxStorageJson.canonical(LinuxStorageJson.decode(bytes))
            }
            assertArrayEquals(case.getString("id"), case.getString("canonical_utf8").toByteArray(Charsets.UTF_8), actual)
            assertEquals(case.getString("id"), case.getString("sha256"), LinuxStorageIdentity.sha256(actual))
            assertFalse(actual.last() == '\n'.code.toByte())
        }
    }

    @Test fun rawRejectionsUseSharedFixtureBeforeNormalizing() {
        val cases = fixture.getJSONArray("invalid").rows().filter { it.getString("kind") in setOf("storage", "capture", "json") }
        assertEquals(69, cases.size)
        cases.forEach { case ->
            val bytes = if (case.has("raw_hex")) case.getString("raw_hex").chunked(2).map { it.toInt(16).toByte() }.toByteArray()
                else case.getString("raw").toByteArray(Charsets.UTF_8)
            val error = assertThrows(case.getString("id"), IllegalArgumentException::class.java) {
                when (case.getString("kind")) {
                    "storage" -> LinuxStorageIdentityCodec.decode(bytes)
                    "capture" -> LinuxStorageIdentityCodec.decodeCapture(bytes)
                    else -> LinuxStorageJson.decode(bytes)
                }
            }
            assertEquals("CONTRACT_INVALID", error.message)
            assertNull(error.cause)
        }
    }

    @Test fun hostUserPreimageUsesExistingCanonicalKeyUserAndProfileBinding() {
        val profile = profile()
        assertEquals(fixture.getString("profile_binding"), profile.binding())
        assertArrayEquals(records.getValue("host_user_input").getString("canonical_utf8").toByteArray(Charsets.UTF_8),
            LinuxStorageJson.canonical(LinuxStorageIdentity.hostUserInput(profile)))
        val capture = StorageCapture.from(profile.installation, profile)
        assertEquals(LinuxStorageIdentityCodec.decodeCapture(raw("capture")), capture)
        record().matchesCapture(capture)
        assertEquals(records.getValue("host_user_input").getString("sha256"), capture.hostUserBinding)
    }

    @Test fun sharedRelationsRejectForeignInstallationProfileBindingAndHostUser() {
        val cases = fixture.getJSONArray("relations").rows().filter { it.getString("kind") == "storage_capture" }
        assertEquals(4, cases.size)
        cases.forEach { case ->
            assertThrows(case.getString("id"), IllegalArgumentException::class.java) {
                record().matchesCapture(LinuxStorageIdentityCodec.decodeCapture(case.getString("raw").toByteArray(Charsets.UTF_8)))
            }
        }
    }

    @Test fun everySharedStorageTransitionRequiresDigestCaptureAndReclamation() {
        val cases = fixture.getJSONArray("storage_transitions").rows()
        assertEquals(11, cases.size)
        cases.forEach { case ->
            val next = LinuxStorageIdentityCodec.decode(case.getJSONObject("new").toString().toByteArray(Charsets.UTF_8))
            val capture = LinuxStorageIdentityCodec.decodeCapture(case.getJSONObject("capture").toString().toByteArray(Charsets.UTF_8))
            val validate = { LinuxStorageIdentity.validateTransition(record(), next, case.getString("expected_old_sha256"),
                capture, case.getBoolean("explicit_save"), case.getBoolean("fully_reclaimed")) }
            if (case.isNull("expected_change")) assertThrows(case.getString("id"), IllegalArgumentException::class.java) { validate() }
            else assertEquals(case.getString("id"), case.getString("expected_change"), validate().wire)
        }
    }

    @Test fun typedConstructorsEnforceSchemaValuesAndDoNotExposePrivateFields() {
        for (bad in listOf("", "A".repeat(32), "f".repeat(31), "f".repeat(33), "g".repeat(32))) {
            assertThrows(IllegalArgumentException::class.java) { StorageInstallationId(bad) }
            assertThrows(IllegalArgumentException::class.java) { ProfileStorageId(bad) }
        }
        assertNotEquals(StorageInstallationId("a".repeat(32)) as Any, ProfileStorageId("a".repeat(32)) as Any)
        assertThrows(IllegalArgumentException::class.java) { record().copy(profileId = "termux-local") }
        assertThrows(IllegalArgumentException::class.java) { record().copy(profileBinding = "a") }
        val capture = StorageCapture.from(profile().installation, profile())
        assertThrows(IllegalArgumentException::class.java) { capture.copy(hostUserBinding = "A".repeat(64)) }
        for (value in listOf(record(), capture, record().installationId, record().profileStorageId))
            assertEquals(value.javaClass.simpleName + "(<private>)", value.toString())
    }

    @Test fun generationConsumesSixteenRandomBytesWithoutUuidBitsOrDerivedFields() {
        val requests = mutableListOf<Int>()
        val source = object : SecureRandom() {
            override fun nextBytes(bytes: ByteArray) {
                requests.add(bytes.size)
                bytes.indices.forEach { bytes[it] = (it + 240).toByte() }
            }
        }
        assertEquals("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff", StorageInstallationId.generate(source).value)
        assertEquals("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff", ProfileStorageId.generate(source).value)
        assertEquals(listOf(16, 16), requests)
        // The production entry also produces the required syntax; entropy quality is provided by SecureRandom.
        assertTrue(StorageInstallationId.generate().value.matches(Regex("[0-9a-f]{32}")))
        assertTrue(ProfileStorageId.generate().value.matches(Regex("[0-9a-f]{32}")))
    }

    @Test fun maximumInputIsBytesAndDecodeReturnsIndependentCanonicalBytes() {
        for (name in listOf("storage", "capture")) {
            val bytes = raw(name)
            val exact = bytes + ByteArray(LinuxStorageIdentityCodec.MAX_BYTES - bytes.size) { 32 }
            if (name == "storage") assertEquals(record(), LinuxStorageIdentityCodec.decode(exact))
            else assertEquals(LinuxStorageIdentityCodec.decodeCapture(bytes), LinuxStorageIdentityCodec.decodeCapture(exact))
            assertThrows(IllegalArgumentException::class.java) { LinuxStorageJson.decode(exact + byteArrayOf(32)) }
        }
        assertThrows(IllegalArgumentException::class.java) { LinuxStorageIdentityCodec.decode(byteArrayOf()) }
        val record = record()
        val bytes = record.canonical(); bytes.fill(0)
        assertEquals(records.getValue("storage").getString("sha256"), record.sha256())
    }

    @Test fun captureRejectsForeignDefaultInstallationAndNonRemoteLinuxProfiles() {
        val current = profile()
        assertThrows(IllegalArgumentException::class.java) { StorageCapture.from("b".repeat(32), current) }
        val other = ConnectionProfileV2.parse(current.toFields() + ("profile_id" to "linux-probe"), current.installation)
        assertThrows(IllegalArgumentException::class.java) { StorageCapture.from(current.installation, other) }
        val noncanonical = ConnectionProfileV2.Value(current.profileId, current.installation, current.revision,
            current.displayName, current.backend, current.host, current.port, current.username, current.hostKey + " comment")
        assertThrows(IllegalArgumentException::class.java) { StorageCapture.from(current.installation, noncanonical) }
    }

    @Test fun addressDisplayRevisionAndClientKeyAreNotHostUserPreimageInputs() {
        val old = profile()
        val next = LinuxConnectionProfile.propose(LinuxConnectionDraft("changed name", "192.0.2.90", "2222", old.username,
            "  " + old.hostKey + " alternate comment  "), old.installation, old)
        assertNotEquals(old.binding(), next.binding())
        assertEquals(StorageCapture.from(old.installation, old).hostUserBinding, StorageCapture.from(old.installation, next).hostUserBinding)
        assertEquals(setOf("domain", "host_key_sha256", "username"), LinuxStorageIdentity.hostUserInput(old).keys)
        val userChanged = LinuxConnectionProfile.propose(LinuxConnectionDraft(old.displayName, old.host, old.port.toString(),
            "another-user", old.hostKey), old.installation, old)
        assertNotEquals(LinuxStorageIdentity.hostUserBinding(old), LinuxStorageIdentity.hostUserBinding(userChanged))
    }

    @Test fun jsonBoundaryKeepsCodePointOrderingAndExactDepth() {
        val fields = linkedMapOf("\ud800\udc00" to 1, "\ue000" to 2)
        assertEquals("{\"\\ue000\":2,\"\\ud800\\udc00\":1}", LinuxStorageJson.canonical(fields).toString(Charsets.UTF_8))
        val exact = "[".repeat(32) + "0" + "]".repeat(32)
        assertArrayEquals(exact.toByteArray(), LinuxStorageJson.canonical(LinuxStorageJson.decode(exact.toByteArray())))
        assertThrows(IllegalArgumentException::class.java) { LinuxStorageJson.decode(("[" + exact + "]").toByteArray()) }
        for (bad in listOf("{\"a\":1,}", "[1,]", "[01]", "{\"x\":truex}", "{\"x\":1,\"\\u0078\":2}", "\ufeff{}"))
            assertThrows(IllegalArgumentException::class.java) { LinuxStorageJson.decode(bad.toByteArray(Charsets.UTF_8)) }
    }
}
