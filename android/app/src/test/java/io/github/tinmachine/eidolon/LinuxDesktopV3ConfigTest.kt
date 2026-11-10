package io.github.tinmachine.eidolon

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class LinuxDesktopV3ConfigTest {
    private val fixture = checkNotNull(javaClass.classLoader?.getResourceAsStream("linux-desktop-v3-configuration.json"))
        .bufferedReader().use { JSONObject(it.readText()) }
    private fun raw(case: JSONObject): ByteArray {
        val bytes = if (case.has("raw_hex")) case.getString("raw_hex").chunked(2).map { it.toInt(16).toByte() }.toByteArray()
            else case.getString("raw").toByteArray(Charsets.UTF_8)
        return if (case.has("pad_to_bytes")) bytes + ByteArray(case.getInt("pad_to_bytes") - bytes.size) { 32 } else bytes
    }
    @Test fun sharedCanonicalAndDigest() {
        val cases = fixture.getJSONArray("valid")
        for (i in 0 until cases.length()) {
            val case = cases.getJSONObject(i)
            val value = LinuxDesktopV3Config.parse(raw(case))
            assertEquals(case.getString("id"), case.getString("canonical_utf8"), value.json())
            assertEquals(case.getString("sha256"), value.sha256())
            assertEquals("LinuxDesktopV3Config(<private>)", value.toString())
            val mutable = value.canonical(); mutable[0] = 0
            assertEquals(case.getString("canonical_utf8"), value.json())
        }
    }
    @Test fun sharedRawRejections() {
        val cases = fixture.getJSONArray("invalid")
        for (i in 0 until cases.length()) {
            val case = cases.getJSONObject(i)
            val error = assertThrows(case.getString("id"), IllegalArgumentException::class.java) {
                LinuxDesktopV3Config.parse(raw(case))
            }
            assertEquals("DESKTOP_V3_CONFIG_INVALID", error.message)
            assertNull(error.cause)
        }
    }
    @Test fun storageCodecRetainsItsSmallerDefaultLimit() {
        assertThrows(IllegalArgumentException::class.java) {
            LinuxStorageJson.decode("{}".toByteArray() + ByteArray(16383) { 32 })
        }
    }
}
