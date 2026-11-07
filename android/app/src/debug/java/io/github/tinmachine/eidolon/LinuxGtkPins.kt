package io.github.tinmachine.eidolon

/** Debugだけ。本文を通常診断へ載せない、保存profileとbundleのimmutable capture。 */
class LinuxGtkPins private constructor(
    private val binding: String,
    private val revision: String,
    private val bundle: String,
    private val waypipe: String,
    private val fixture: String,
    private val gtkBundle: String, private val source: String, private val binary: String, private val version: String,
) {
    override fun toString() = "LinuxGtkPins(<private>)"
    fun matches(profile: ConnectionProfileV2.Value): Boolean = profile.backend == ConnectionProfileV2.Backend.LINUX_SSH &&
        profile.profileId == "linux-probe" && profile.binding() == binding && profile.revision == revision
    fun nativeJson(): String = """{"schema":1,"base":{"schema":1,"bundle_sha256":"$bundle","waypipe_sha256":"$waypipe","fixture_sha256":"$fixture"},"gtk_bundle_sha256":"$gtkBundle","source_sha256":"$source","binary_sha256":"$binary","gtk_version":"$version"}"""
    fun encode(): ByteArray = """{"schema":1,"profile_binding":"$binding","profile_revision":"$revision","bundle_sha256":"$bundle","waypipe_sha256":"$waypipe","fixture_sha256":"$fixture","gtk_bundle_sha256":"$gtkBundle","source_sha256":"$source","binary_sha256":"$binary","gtk_version":"$version"}""".toByteArray(Charsets.UTF_8)
    companion object {
        const val MAX_BYTES = 4096
        fun decode(bytes: ByteArray): LinuxGtkPins = try {
            val fields = ConnectionProfileV2Codec.decodeFields(bytes, MAX_BYTES)
            require(fields.keys == setOf("schema", "profile_binding", "profile_revision", "bundle_sha256", "waypipe_sha256", "fixture_sha256", "gtk_bundle_sha256", "source_sha256", "binary_sha256", "gtk_version") && fields["schema"] == 1L)
            fun hash(key: String): String = (fields[key] as? String)?.takeIf { it.matches(Regex("[0-9a-f]{64}")) } ?: error("invalid")
            val revision = (fields["profile_revision"] as? String)?.takeIf {
                it.matches(Regex("[1-9][0-9]{0,19}")) && it.toULongOrNull() != null
            } ?: error("invalid")
            val version = (fields["gtk_version"] as? String)?.takeIf { it.matches(Regex("3\\.24\\.[0-9]{1,3}")) } ?: error("invalid")
            LinuxGtkPins(hash("profile_binding"), revision, hash("bundle_sha256"), hash("waypipe_sha256"), hash("fixture_sha256"),
                hash("gtk_bundle_sha256"), hash("source_sha256"), hash("binary_sha256"), version)
        } catch (_: Exception) { throw IllegalArgumentException("PROBE_PINS_INVALID") }
    }
}
