package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class ConnectionProfileV2CodecTest {
    private val installation = "1".repeat(32)
    private fun profile(): ConnectionProfileV2.Value {
        val row = checkNotNull(javaClass.classLoader?.getResourceAsStream("connection-profiles-v2.tsv")).bufferedReader()
            .useLines { lines -> lines.first { !it.startsWith('#') && it.startsWith("linux-") } }.split('\t')
        return ConnectionProfileV2.parse(mapOf("schema" to 2,"profile_id" to "linux-probe","installation" to installation,
            "revision" to "9007199254740993","display_name" to "日本語 😀","backend" to "linux_ssh","host" to "192.0.2.1",
            "port" to 22,"username" to "fixture","host_key" to row[7]),installation)
    }
    private fun encoded() = ConnectionProfileV2Codec.encode(profile()).toString(Charsets.UTF_8)
    private fun reject(value: ByteArray) {
        val error = assertThrows(IllegalArgumentException::class.java) { ConnectionProfileV2Codec.decode(value,installation) }
        assertEquals("PROFILE_JSON_INVALID",error.message); assertNull(error.cause)
    }
    @Test fun unicodeAndLargeStringRevisionRoundTripWithoutDoubleRounding() {
        val value = ConnectionProfileV2Codec.decode(ConnectionProfileV2Codec.encode(profile()),installation)
        assertEquals(profile().toFields(),value.toFields()); assertEquals(profile().binding(),value.binding())
        assertEquals("9007199254740993",value.revision)
    }
    @Test fun duplicateLiteralAndEscapedKeysAreRejected() {
        for (key in listOf("schema", "\\u0073chema")) reject(encoded().dropLast(1).plus(",\"$key\":2}").toByteArray())
    }
    @Test fun coercionsCommentsTrailingCommasNestedAndExtraFieldsFailClosed() {
        val json = encoded()
        for (bad in listOf(json + "{}",json.dropLast(1) + ",}",json.replace("\"schema\":2","schema:2"),
            json.replace("\"schema\":2","'schema':2"),json.replace("\"port\":22","\"port\":22.0"),
            json.replace("\"port\":22","\"port\":2.2e1"),json.replace("\"port\":22","\"port\":022"),
            json.replace("\"port\":22","\"port\":\"22\""),json.replace("\"port\":22","\"port\":true"),
            json.replace("\"port\":22","\"port\":null"),json.replace("\"port\":22","\"port\":[]"),
            json.replace("\"port\":22","\"port\":{}"),json.replace("{","{/*comment*/",ignoreCase=false),
            json.dropLast(1)+",\"password\":\"private-fixture\"}")) reject(bad.toByteArray())
    }
    @Test fun invalidUtf8SurrogatesAndOversizeCannotBecomeReplacementCharacters() {
        reject(byteArrayOf(0xC0.toByte(),0x80.toByte())); reject(ByteArray(16385) {32}); reject(byteArrayOf())
        reject(encoded().replace("日本語 😀","\\ud800").toByteArray())
        reject(encoded().replace("日本語 😀","\\udc00").toByteArray())
        reject(encoded().replace(installation,"2".repeat(32)).toByteArray())
    }
    @Test fun failedOwnerBlocksNormalAndUnrelatedRestartUntilItsOwnCleanup() {
        assertTrue(ProbeRuntimeRecovery.registryEmpty(0,0L))
        for (invalid in listOf(null,1,-1,0.0,"0")) {
            assertFalse(ProbeRuntimeRecovery.registryEmpty(invalid,0))
            assertFalse(ProbeRuntimeRecovery.registryEmpty(0,invalid))
        }
        val guard = ProbeRuntimeRecovery(); val owner = Any(); val unrelated = Any()
        assertTrue(guard.canStartNormal()); guard.retain(owner)
        assertTrue(guard.permits(owner)); assertFalse(guard.permits(null)); assertFalse(guard.permits(unrelated)); assertFalse(guard.canStartNormal())
        guard.release(unrelated); assertFalse(guard.canStartNormal())
        assertThrows(IllegalStateException::class.java) { guard.retain(unrelated) }
        guard.retain(owner); guard.release(owner)
        assertTrue(guard.canStartNormal()); assertTrue(guard.permits(unrelated))
    }
    @Test fun credentialCaptureIsSingleUseAndBindsEveryIdentityField() {
        val profile = profile()
        fun scope() = mapOf("schema" to 1,"code" to 0,"scope" to mapOf("session" to "9","request_id" to "7",
            "profile_id" to "linux-probe","revision" to profile.revision,"binding" to profile.binding()))
        val capture = ProbeCredentialCapture(3,"7",profile)
        val supply = capture.claim(3,"9","7","linux-probe",scope())
        assertEquals(9L,supply.session); assertEquals(7L,supply.request); assertEquals(profile.binding(),supply.binding)
        assertFalse(capture.toString().contains(profile.binding())); assertFalse(supply.toString().contains(profile.binding()))
        assertThrows(IllegalArgumentException::class.java) { capture.claim(3,"9","7","linux-probe",scope()) }
        for ((key,value) in listOf("session" to "10","session" to 9,"request_id" to "8","revision" to "9007199254740992",
            "profile_id" to "termux-local","binding" to "0".repeat(64),"extra" to "fixture")) {
            val c = ProbeCredentialCapture(3,"7",profile)
            val fields = scope().toMutableMap(); @Suppress("UNCHECKED_CAST") val original = fields["scope"] as Map<String,Any>
            fields["scope"] = original + (key to value)
            assertThrows(IllegalArgumentException::class.java) { c.claim(3,"9","7","linux-probe",fields) }
            assertThrows(IllegalArgumentException::class.java) { c.claim(3,"9","7","linux-probe",scope()) }
        }
        val closed = ProbeCredentialCapture(3,"7",profile); closed.close()
        assertThrows(IllegalArgumentException::class.java) { closed.claim(3,"9","7","linux-probe",scope()) }
        assertThrows(IllegalArgumentException::class.java) { ProbeCredentialCapture(3,"7",profile).claim(4,"9","7","linux-probe",scope()) }
        assertThrows(IllegalArgumentException::class.java) { ProbeCredentialCapture(3,"7",profile).claim(3,"9","7","linux-probe",scope() + ("code" to 1)) }
    }
}
