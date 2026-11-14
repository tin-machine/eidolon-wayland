package io.github.tinmachine.eidolon

import java.security.MessageDigest
import java.nio.CharBuffer
import java.nio.charset.CodingErrorAction

/** 静的bundle設定。受理は接続・実行・表示の成功を意味しない。 */
class LinuxDesktopV3Config private constructor(private val bytes: ByteArray) {
    fun canonical(): ByteArray = bytes.copyOf()
    fun json(): String = bytes.toString(Charsets.UTF_8)
    fun sha256(): String = hash(bytes)
    override fun toString() = "LinuxDesktopV3Config(<private>)"

    companion object {
        const val MAX_BYTES = 32768
        private val hashes = setOf("base_bundle_sha256", "desktop_bundle_sha256", "waypipe_sha256",
            "shm_fixture_sha256", "desktop_binary_sha256", "app_manifest_sha256")
        private val manifestHashes = setOf("executable_sha256", "dependency_fingerprint", "settings_module_sha256")
        private val fixed = mapOf("app_id" to "mousepad-editor-v1", "package" to "mousepad",
            "executable" to "/usr/bin/mousepad", "settings_kind" to "eidolon-keyfile-v1",
            "settings_policy" to "mousepad-single-main-no-restore-v1",
            "settings_relative_path" to "config/glib-2.0/settings/keyfile",
            "launch_policy" to "mousepad-private-bus-v1", "state_policy" to "persistent-keyfile-locked-v1")
        private fun hash(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes)
            .joinToString("") { (it.toInt() and 255).toString(16).padStart(2, '0') }
        private fun fields(value: Any?, keys: Set<String>): Map<*, *> {
            val map = value as? Map<*, *> ?: error("CONTRACT_INVALID")
            require(map.keys == keys)
            return map
        }
        private fun hashValue(value: Any?) = (value as? String)?.matches(Regex("[0-9a-f]{64}")) == true
        /** A pasted Kotlin String must not replace an unpaired surrogate before the strict byte parser. */
        fun parse(raw: String): LinuxDesktopV3Config = try {
            val encoded = Charsets.UTF_8.newEncoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).encode(CharBuffer.wrap(raw))
            parse(ByteArray(encoded.remaining()).also { encoded.get(it) })
        } catch (_: Exception) { throw IllegalArgumentException("DESKTOP_V3_CONFIG_INVALID") }
        fun parse(raw: ByteArray): LinuxDesktopV3Config = try {
            val value = fields(LinuxStorageJson.parse(raw, MAX_BYTES), setOf("schema", "kind", "pins", "app_manifest"))
            require(value["schema"] == 1L && value["kind"] == "linux-desktop-v3-connection")
            val pins = fields(value["pins"], hashes + setOf("schema", "backend", "protocol_revision"))
            require(pins["schema"] == 2L && pins["backend"] == "linux_desktop" &&
                pins["protocol_revision"] == "eidolon-linux-desktop-v3" && hashes.all { hashValue(pins[it]) })
            require(LinuxStorageJson.canonical(pins).size <= 4096)
            val manifest = fields(value["app_manifest"], fixed.keys + manifestHashes +
                setOf("schema", "package_version", "architecture", "window_app_ids"))
            require(manifest["schema"] == 1L && fixed.all { (key, expected) -> manifest[key] == expected } &&
                manifestHashes.all { hashValue(manifest[it]) } && manifest["window_app_ids"] == listOf("mousepad"))
            require(listOf("package_version", "architecture").all { key ->
                val text = manifest[key] as? String
                text != null && text.length in 1..128 && text.all { it.code in 33..126 }
            })
            val manifestBytes = LinuxStorageJson.canonical(manifest)
            require(manifestBytes.size <= 16384 && hash(manifestBytes) == pins["app_manifest_sha256"])
            LinuxDesktopV3Config(LinuxStorageJson.canonical(value))
        } catch (_: Exception) { throw IllegalArgumentException("DESKTOP_V3_CONFIG_INVALID") }
    }
}
