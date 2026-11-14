package io.github.tinmachine.eidolon

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class LinuxRuntimeConfigurationTest {
    private fun fixture(name: String) = checkNotNull(javaClass.classLoader?.getResourceAsStream(name))
        .bufferedReader().use { JSONObject(it.readText()) }
    private val contract = fixture("linux-desktop-v3-contract.json")
    private val configuration = fixture("linux-desktop-v3-configuration.json").getJSONArray("valid").getJSONObject(0)
    private fun profile() = LinuxConnectionProfile.decode(contract.getJSONObject("profile").toString().toByteArray(), "a".repeat(32))
    private fun storage(): LinuxStorageRecord = contract.getJSONArray("records").let { rows ->
        LinuxStorageIdentityCodec.decode((0 until rows.length()).map(rows::getJSONObject)
            .single { it.getString("id") == "storage" }.getString("raw").toByteArray())
    }
    private fun config() = LinuxDesktopV3Config.parse(configuration.getString("raw").toByteArray())
    private fun base() = JSONObject().put("schema", 1).put("installation", "a".repeat(32))
        .put("paths", JSONObject().put("native_library_dir", "/private/native").put("files_dir", "/private/files"))
        .put("preferences_dir", "/private/preferences")
    private fun pins() = LinuxDesktopPins.parse("""{"schema":1,"backend":"linux_desktop","base_bundle_sha256":"${"1".repeat(64)}","desktop_bundle_sha256":"${"2".repeat(64)}","waypipe_sha256":"${"3".repeat(64)}","shm_fixture_sha256":"${"4".repeat(64)}","desktop_binary_sha256":"${"5".repeat(64)}"}""")

    @Test fun normalNoneV2AndV3UseExclusiveFieldsAndSameProfileCapture() {
        val none = JSONObject(LinuxRuntimeConfiguration.build(base()))
        assertFalse(none.has("linux_profile_v2"))
        val old = JSONObject(LinuxRuntimeConfiguration.build(base(), profile(), v2Pins = pins()))
        assertTrue(old.has("linux_desktop_pins"))
        assertFalse(old.has("linux_desktop_v3"))
        val current = JSONObject(LinuxRuntimeConfiguration.build(base(), profile(), configuration = config(), storage = storage()))
        assertFalse(current.has("linux_desktop_pins"))
        val envelope = JSONObject(current.getString("linux_desktop_v3"))
        assertEquals(setOf("configuration", "storage"), envelope.keys().asSequence().toSet())
        assertEquals(configuration.getString("sha256"), LinuxDesktopV3Config.parse(envelope.getJSONObject("configuration").toString().toByteArray()).sha256())
        assertEquals(storage(), LinuxStorageIdentityCodec.decode(envelope.getJSONObject("storage").toString().toByteArray()))
        assertEquals(profile().binding(), LinuxConnectionProfile.decode(current.getString("linux_profile_v2").toByteArray(), "a".repeat(32)).binding())
    }

    @Test fun mixedStorageOnlyMissingProfileAndForeignBindingCannotFallback() {
        val invalid = listOf<() -> Unit>(
            { LinuxRuntimeConfiguration.build(base(), profile(), pins(), config(), storage()) },
            { LinuxRuntimeConfiguration.build(base(), profile(), storage = storage()) },
            { LinuxRuntimeConfiguration.build(base(), configuration = config(), storage = storage()) },
            { LinuxRuntimeConfiguration.build(base(), profile(), configuration = config()) },
            { LinuxRuntimeConfiguration.build(base(), profile()) },
            { LinuxRuntimeConfiguration.build(base(), v2Pins = pins()) },
            { LinuxRuntimeConfiguration.build(base().put("linux_desktop_pins", pins().json()), profile(), configuration = config(), storage = storage()) },
            { LinuxRuntimeConfiguration.build(base().put("installation", "b".repeat(32)), profile(), configuration = config(), storage = storage()) },
            { LinuxRuntimeConfiguration.build(base(), profile(), configuration = config(), storage = storage().copy(profileBinding = "d".repeat(64))) },
        )
        for (case in invalid) assertThrows(IllegalArgumentException::class.java) { case() }
    }

    @Test fun rawImportLimitDoesNotBecomeRuntimeLimitOrNormalizeDuplicatesAway() {
        val raw = configuration.getString("raw").toByteArray()
        val padded = raw + ByteArray(32768 - raw.size) { 32 }
        val parsed = LinuxDesktopV3Config.parse(padded)
        assertEquals(configuration.getString("canonical_utf8"), parsed.json())
        assertTrue(LinuxRuntimeConfiguration.build(base(), profile(), configuration = parsed, storage = storage()).toByteArray().size < 16384)
        assertThrows(IllegalArgumentException::class.java) { LinuxDesktopV3Config.parse(padded + byteArrayOf(32)) }
        val duplicate = configuration.getString("raw").replaceFirst("{", "{\"schema\":1,")
        assertThrows(IllegalArgumentException::class.java) { LinuxDesktopV3Config.parse(duplicate.toByteArray()) }
        assertThrows(IllegalArgumentException::class.java) { LinuxDesktopV3Config.parse(duplicate) }
        assertThrows(IllegalArgumentException::class.java) { LinuxDesktopV3Config.parse("\uD800") }
    }

    @Test fun finalRuntimeByteCheckIncludesOtherFieldsUtf8AndHookAdditions() {
        val exact = "{\"value\":\"" + "a".repeat(16372) + "\"}"
        assertEquals(16384, exact.toByteArray(Charsets.UTF_8).size)
        assertEquals(exact, LinuxRuntimeConfiguration.bounded(exact))
        assertThrows(IllegalArgumentException::class.java) { LinuxRuntimeConfiguration.bounded(exact + " ") }
        val unicode = "{\"value\":\"" + "界".repeat(5457) + "a\"}"
        assertEquals(16384, unicode.toByteArray(Charsets.UTF_8).size)
        assertEquals(unicode, LinuxRuntimeConfiguration.bounded(unicode))
        assertThrows(IllegalArgumentException::class.java) { LinuxRuntimeConfiguration.bounded(unicode.replace("a\"}", "界\"}")) }
        assertThrows(IllegalArgumentException::class.java) { LinuxRuntimeConfiguration.bounded("\uD800") }
        val withV3 = LinuxRuntimeConfiguration.build(base(), profile(), configuration = config(), storage = storage())
        val enlargedByHook = JSONObject(withV3).put("debug_extra", "x".repeat(16384)).toString()
        assertThrows(IllegalArgumentException::class.java) { LinuxRuntimeConfiguration.bounded(enlargedByHook) }
        assertThrows(IllegalArgumentException::class.java) {
            LinuxRuntimeConfiguration.build(base().put("preferences_dir", "/" + "界\\\"".repeat(4000)),
                profile(), configuration = config(), storage = storage())
        }
    }
}
