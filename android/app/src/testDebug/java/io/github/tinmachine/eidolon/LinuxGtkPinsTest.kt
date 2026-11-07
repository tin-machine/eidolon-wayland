package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class LinuxGtkPinsTest {
    private fun profile(): ConnectionProfileV2.Value {
        val row = checkNotNull(javaClass.classLoader?.getResourceAsStream("connection-profiles-v2.tsv")).bufferedReader()
            .useLines { lines -> lines.first { it.startsWith("linux-") } }.split('\t')
        return ConnectionProfileV2.parse(mapOf("schema" to 2, "installation" to "1".repeat(32), "profile_id" to "linux-probe",
            "revision" to "9007199254740993", "display_name" to "fixture", "backend" to "linux_ssh", "host" to "192.0.2.1",
            "port" to 22, "username" to "fixture", "host_key" to row[7]), "1".repeat(32))
    }
    private fun encoded() = "{\"schema\":1,\"profile_binding\":\"${profile().binding()}\",\"profile_revision\":\"9007199254740993\",\"bundle_sha256\":\"${"b".repeat(64)}\",\"waypipe_sha256\":\"${"c".repeat(64)}\",\"fixture_sha256\":\"${"d".repeat(64)}\",\"gtk_bundle_sha256\":\"${"e".repeat(64)}\",\"source_sha256\":\"${"f".repeat(64)}\",\"binary_sha256\":\"${"a".repeat(64)}\",\"gtk_version\":\"3.24.52\"}"
    private fun reject(bytes: ByteArray) {
        val error = assertThrows(IllegalArgumentException::class.java) { LinuxGtkPins.decode(bytes) }
        assertEquals("PROBE_PINS_INVALID", error.message); assertNull(error.cause)
    }
    @Test fun pinsBindProfileRevisionAndRoundTripWithoutLeakingIdentityInNativePins() {
        val pins = LinuxGtkPins.decode(encoded().toByteArray())
        assertTrue(pins.matches(profile())); assertEquals(encoded(), pins.encode().toString(Charsets.UTF_8))
        assertEquals("LinuxGtkPins(<private>)", pins.toString()); assertFalse(pins.nativeJson().contains(profile().binding()))
        for ((field, value) in listOf("host" to "192.0.2.2", "revision" to "9007199254740994", "username" to "other")) {
            val other = ConnectionProfileV2.parse(profile().toFields() + (field to value), "1".repeat(32))
            assertFalse(pins.matches(other))
        }
    }
    @Test fun duplicateKeysUnknownFieldsAndTypeCoercionsFailClosed() {
        for (key in listOf("schema", "\\u0073chema")) reject((encoded().dropLast(1) + ",\"$key\":1}").toByteArray())
        for (bad in listOf(encoded().replace("\"schema\":1", "\"schema\":true"), encoded().replace("\"schema\":1", "\"schema\":1.0"),
            encoded().replace("\"9007199254740993\"", "9007199254740993"), encoded().dropLast(1) + ",\"path\":\"/tmp/ignored\"}",
            encoded().replace("b".repeat(64), "B".repeat(64)), encoded() + "{}",encoded().replace("3.24.52","4.10.1"),encoded().replace("3.24.52","3.24.52\\n"),encoded().replace("3.24.52","3.24.1000"))) reject(bad.toByteArray())
    }
    @Test fun utf8SizeAndRevisionBoundariesAreStrict() {
        reject(ByteArray(4097) { 32 }); reject(byteArrayOf(0xc0.toByte(), 0x80.toByte())); reject(byteArrayOf())
        for (revision in listOf("0", "01", "+1", "18446744073709551616", "\\ud800")) reject(encoded().replace("9007199254740993", revision).toByteArray())
        assertNotNull(LinuxGtkPins.decode(encoded().replace("9007199254740993", "18446744073709551615").toByteArray()))
    }
}
