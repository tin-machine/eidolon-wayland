package io.github.tinmachine.eidolon

/** 通常Linux専用。接続操作時のimmutable profileへ、要求された鍵を一度だけ供給する。 */
class LinuxCredentialCapture(private val runtime: Long, private val request: String, private val profile: ConnectionProfileV2.Value) {
    private var consumed = false
    class Supply(val session: Long, val request: Long, val binding: String) {
        override fun toString() = "LinuxCredentialSupply(<private>)"
    }
    init {
        require(runtime > 0 && positive(request) != null && profile.profileId == LinuxConnectionProfile.ID &&
            profile.backend == ConnectionProfileV2.Backend.LINUX_SSH) { "CREDENTIAL_CAPTURE_INVALID" }
    }
    @Synchronized fun close() { consumed = true }
    @Synchronized fun claim(actualRuntime: Long, eventSession: Any?, eventRequest: Any?, eventProfile: Any?, envelope: Map<String, Any?>): Supply {
        val wasConsumed = consumed
        consumed = true // 誤った遅延event、拒否でも再供給しない。
        require(!wasConsumed && actualRuntime == runtime && eventRequest == request && eventProfile == profile.profileId &&
            envelope.keys == setOf("schema", "code", "scope") && integer(envelope["schema"]) == 1L && integer(envelope["code"]) == 0L) { "CREDENTIAL_SCOPE_INVALID" }
        val scope = envelope["scope"] as? Map<*, *> ?: throw IllegalArgumentException("CREDENTIAL_SCOPE_INVALID")
        require(scope.keys == setOf("session", "request_id", "profile_id", "revision", "binding") &&
            scope["session"] == eventSession && scope["request_id"] == request && scope["profile_id"] == profile.profileId &&
            scope["revision"] == profile.revision && scope["binding"] == profile.binding()) { "CREDENTIAL_SCOPE_INVALID" }
        return Supply(positive(eventSession) ?: throw IllegalArgumentException("CREDENTIAL_SCOPE_INVALID"), positive(request)!!, profile.binding())
    }
    override fun toString() = "LinuxCredentialCapture(<private>)"
    companion object {
        private fun integer(value: Any?): Long? = when (value) { is Int -> value.toLong(); is Long -> value; else -> null }
        private fun positive(value: Any?): Long? = (value as? String)?.takeIf { it.matches(Regex("[1-9][0-9]{0,18}")) }
            ?.toLongOrNull()?.takeIf { it > 0 }
    }
}
