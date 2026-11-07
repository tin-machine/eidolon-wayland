package io.github.tinmachine.eidolon

internal enum class LinuxRecoveryOutcome {
    VERIFIED, NOT_REQUIRED, INVALID;
    companion object {
        fun classify(queried: Boolean, pending: Boolean, receiptVerified: Boolean, channelClosed: Boolean): LinuxRecoveryOutcome = when {
            pending -> INVALID
            queried && receiptVerified && channelClosed -> VERIFIED
            !queried && !receiptVerified && !channelClosed -> NOT_REQUIRED
            else -> INVALID
        }
    }
}

/** Debugだけ。本文を通常診断へ載せない、保存profileとbundleのimmutable capture。 */
class LinuxShmPins private constructor(
    private val binding: String,
    private val revision: String,
    private val bundle: String,
    private val waypipe: String,
    private val fixture: String,
) {
    override fun toString() = "LinuxShmPins(<private>)"
    fun matches(profile: ConnectionProfileV2.Value): Boolean = profile.backend == ConnectionProfileV2.Backend.LINUX_SSH &&
        profile.profileId == "linux-probe" && profile.binding() == binding && profile.revision == revision
    fun nativeJson(): String = "{\"schema\":1,\"bundle_sha256\":\"$bundle\",\"waypipe_sha256\":\"$waypipe\",\"fixture_sha256\":\"$fixture\"}"
    fun encode(): ByteArray = "{\"schema\":1,\"profile_binding\":\"$binding\",\"profile_revision\":\"$revision\",\"bundle_sha256\":\"$bundle\",\"waypipe_sha256\":\"$waypipe\",\"fixture_sha256\":\"$fixture\"}".toByteArray(Charsets.UTF_8)
    companion object {
        const val MAX_BYTES = 4096
        fun decode(bytes: ByteArray): LinuxShmPins = try {
            val fields = ConnectionProfileV2Codec.decodeFields(bytes, MAX_BYTES)
            require(fields.keys == setOf("schema", "profile_binding", "profile_revision", "bundle_sha256", "waypipe_sha256", "fixture_sha256") && fields["schema"] == 1L)
            fun hash(key: String): String = (fields[key] as? String)?.takeIf { it.matches(Regex("[0-9a-f]{64}")) } ?: error("invalid")
            val revision = (fields["profile_revision"] as? String)?.takeIf {
                it.matches(Regex("[1-9][0-9]{0,19}")) && it.toULongOrNull() != null
            } ?: error("invalid")
            LinuxShmPins(hash("profile_binding"), revision, hash("bundle_sha256"), hash("waypipe_sha256"), hash("fixture_sha256"))
        } catch (_: Exception) { throw IllegalArgumentException("PROBE_PINS_INVALID") }
    }
}
