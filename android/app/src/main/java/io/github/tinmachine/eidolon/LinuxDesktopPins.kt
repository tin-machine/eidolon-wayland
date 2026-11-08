package io.github.tinmachine.eidolon

import java.security.MessageDigest

/** 固定builderが出すpinsだけを受ける。profileや任意commandをJSONから追加しない。 */
class LinuxDesktopPins private constructor(private val values: Map<String, Any>) {
    fun json(): String = values.entries.joinToString(",", "{", "}") { (key, value) ->
        "\"$key\":" + if (value is String) "\"$value\"" else value.toString()
    }
    override fun toString() = "LinuxDesktopPins(<private>)"
    companion object {
        private val hashes = setOf("base_bundle_sha256", "desktop_bundle_sha256", "waypipe_sha256",
            "shm_fixture_sha256", "desktop_binary_sha256")
        fun parse(raw: String): LinuxDesktopPins = try {
            val fields = ConnectionProfileV2Codec.decodeFields(raw.toByteArray(Charsets.UTF_8), 4096)
            require(fields.keys == hashes + setOf("schema", "backend") && fields["schema"] == 1L &&
                fields["backend"] == "linux_desktop" && hashes.all { (fields[it] as? String)?.matches(Regex("[0-9a-f]{64}")) == true })
            LinuxDesktopPins(fields)
        } catch (_: Exception) { throw IllegalArgumentException("DESKTOP_PIN_INVALID") }
    }
}

/** 保存pinsは確認したprofile revision/bindingから独立して再採用しない。 */
class LinuxDesktopPinsRepository(private val storage: LinuxProfileRepository.Storage) {
    class Saved internal constructor(val profileBinding: String, val pins: LinuxDesktopPins, val token: String) {
        fun forProfile(profile: ConnectionProfileV2.Value?): LinuxDesktopPins? =
            pins.takeIf { profile != null && profile.profileId == LinuxConnectionProfile.ID && profile.binding() == profileBinding }
        override fun toString() = "SavedLinuxDesktopPins(<private>)"
    }
    @Synchronized fun load(): Saved? = storage.read()?.let(::decode)
    @Synchronized fun save(profile: ConnectionProfileV2.Value, raw: String, expectedToken: String?, confirmed: Boolean): Saved {
        require(confirmed) { "PIN_CONFIRMATION_REQUIRED" }
        require(profile.profileId == LinuxConnectionProfile.ID && profile.backend == ConnectionProfileV2.Backend.LINUX_SSH) { "PROFILE_INVALID" }
        val before = storage.read()
        require(before?.let(::decode)?.token == expectedToken) { "PROFILE_CHANGED" }
        val pins = LinuxDesktopPins.parse(raw)
        // pins.json()の文字集合は固定keys・lower hex・固定backendに限られる。
        val escaped = pins.json().replace("\"", "\\\"")
        val bytes = "{\"schema\":1,\"profile_binding\":\"${profile.binding()}\",\"pins\":\"$escaped\"}".toByteArray(Charsets.UTF_8)
        storage.publish(before, bytes)
        val saved = checkNotNull(storage.read()) { "PROFILE_READBACK_INVALID" }
        require(saved.contentEquals(bytes)) { "PROFILE_READBACK_INVALID" }
        return decode(saved)
    }
    private fun decode(bytes: ByteArray): Saved {
        val fields = ConnectionProfileV2Codec.decodeFields(bytes, 8192)
        require(fields.keys == setOf("schema", "profile_binding", "pins") && fields["schema"] == 1L) { "DESKTOP_PIN_INVALID" }
        val binding = fields["profile_binding"] as? String ?: throw IllegalArgumentException("DESKTOP_PIN_INVALID")
        require(binding.matches(Regex("[0-9a-f]{64}"))) { "DESKTOP_PIN_INVALID" }
        val pins = LinuxDesktopPins.parse(fields["pins"] as? String ?: throw IllegalArgumentException("DESKTOP_PIN_INVALID"))
        val token = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
        return Saved(binding, pins, token)
    }
}
