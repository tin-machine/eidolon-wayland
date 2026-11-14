package io.github.tinmachine.eidolon

import java.nio.CharBuffer
import java.nio.charset.CodingErrorAction
import org.json.JSONObject

/** Assembly of already validated, captured settings. Import and JNI bounds intentionally differ. */
object LinuxRuntimeConfiguration {
    const val MAX_RUNTIME_BYTES = 16 * 1024
    private val desktopFields = setOf("linux_profile_v2", "linux_desktop_pins", "linux_desktop_v3")

    fun build(base: JSONObject, profile: ConnectionProfileV2.Value? = null, v2Pins: LinuxDesktopPins? = null,
        configuration: LinuxDesktopV3Config? = null, storage: LinuxStorageRecord? = null): String {
        require(desktopFields.none { base.has(it) }) { "DESKTOP_CONFIGURATION_MISMATCH" }
        require(!(v2Pins != null && configuration != null) && (configuration != null) == (storage != null) &&
            (profile != null) == (v2Pins != null || configuration != null)) { "DESKTOP_CONFIGURATION_MISMATCH" }
        val result = JSONObject(base.toString())
        if (profile != null) {
            val installation = result.getString("installation")
            LinuxStorageIdentity.checkedProfile(installation, profile)
            result.put("linux_profile_v2", ConnectionProfileV2Codec.encode(profile).toString(Charsets.UTF_8))
            if (v2Pins != null) result.put("linux_desktop_pins", v2Pins.json())
            else result.put("linux_desktop_v3", v3Binding(profile, checkNotNull(configuration), checkNotNull(storage)))
        }
        return bounded(result.toString())
    }

    fun v3Binding(profile: ConnectionProfileV2.Value, configuration: LinuxDesktopV3Config,
        storage: LinuxStorageRecord): String {
        storage.matchesCapture(StorageCapture.from(profile.installation, profile))
        val envelope = mapOf("configuration" to LinuxStorageJson.parse(configuration.canonical(), LinuxDesktopV3Config.MAX_BYTES),
            "storage" to LinuxStorageJson.parse(storage.canonical()))
        return bounded(LinuxStorageJson.canonical(envelope).toString(Charsets.UTF_8))
    }

    /** Host calls this again after BuildHooks has added any permitted debug configuration. */
    fun bounded(raw: String): String {
        val count = try {
            Charsets.UTF_8.newEncoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).encode(CharBuffer.wrap(raw)).remaining()
        } catch (_: Exception) { throw IllegalArgumentException("RUNTIME_CONFIG_INVALID") }
        require(count in 1..MAX_RUNTIME_BYTES) { "RUNTIME_CONFIG_TOO_LARGE" }
        return raw
    }
}
