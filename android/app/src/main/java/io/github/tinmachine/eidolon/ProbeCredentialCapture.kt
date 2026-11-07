package io.github.tinmachine.eidolon

/** NX18固定probe専用。設定本文はログ不可。runtime/requestへ一度だけ鍵を供給する。 */
class ProbeCredentialCapture(val runtime: Long, private val request: String, private val profile: ConnectionProfileV2.Value) {
    private var consumed = false
    class Supply(val session: Long, val request: Long, val binding: String) {
        override fun toString() = "ProbeCredentialSupply(<private>)"
    }
    init {
        require(runtime > 0 && positive(request) != null && profile.profileId == "linux-probe" &&
            profile.backend == ConnectionProfileV2.Backend.LINUX_SSH) { "PROBE_CAPTURE_INVALID" }
    }
    @Synchronized fun close() { consumed = true }
    @Synchronized fun claim(actualRuntime: Long, eventSession: Any?, eventRequest: Any?, eventProfile: Any?, envelope: Map<String, Any?>): Supply {
        val wasConsumed = consumed
        consumed = true // 失敗・拒否でも自動再供給しない。
        require(!wasConsumed && actualRuntime == runtime && eventRequest == request && eventProfile == profile.profileId &&
            envelope.keys == setOf("schema", "code", "scope") && integer(envelope["schema"]) == 1L && integer(envelope["code"]) == 0L) { "PROBE_SCOPE_INVALID" }
        val scope = envelope["scope"] as? Map<*, *> ?: throw IllegalArgumentException("PROBE_SCOPE_INVALID")
        require(scope.keys == setOf("session", "request_id", "profile_id", "revision", "binding") &&
            scope["session"] == eventSession && scope["request_id"] == request && scope["profile_id"] == profile.profileId &&
            scope["revision"] == profile.revision && scope["binding"] == profile.binding()) { "PROBE_SCOPE_INVALID" }
        return Supply(positive(eventSession) ?: throw IllegalArgumentException("PROBE_SCOPE_INVALID"),
            positive(request)!!, profile.binding())
    }
    override fun toString() = "ProbeCredentialCapture(<private>)"
    companion object {
        private fun integer(value: Any?): Long? = when(value) { is Int -> value.toLong(); is Long -> value; else -> null }
        private fun positive(value: Any?): Long? = (value as? String)?.takeIf { it.matches(Regex("[1-9][0-9]{0,18}")) }?.toLongOrNull()?.takeIf { it > 0 }
    }
}

/** Host worker所有。回収未確認の別runtimeを残した再作成/別case開始を禁止する。 */
class ProbeRuntimeRecovery {
    private var owner: Any? = null
    fun permits(candidate: Any?): Boolean = owner == null || owner === candidate
    fun canStartNormal(): Boolean = owner == null
    fun retain(candidate: Any) { check(permits(candidate)); owner = candidate }
    fun release(candidate: Any) { if (owner === candidate) owner = null }
}
