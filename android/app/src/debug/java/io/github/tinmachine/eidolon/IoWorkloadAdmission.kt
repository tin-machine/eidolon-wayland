package io.github.tinmachine.eidolon

/** 固定fixture専用。最新transportと遅れて届くnative表示を、同じ対象で照合してから採用する。 */
internal class IoWorkloadAdmission(private val window: String, private val deadlineMs: Long) {
    enum class Kind { WAIT, ACCEPT, FAIL }
    enum class Reason(val wire: String) {
        READY("ready"), REGISTRATION_PENDING("registration_pending"), DISPLAY_PENDING("native_display_pending"),
        OLDER_SEQUENCE("older_sequence"), SCOPE_CHANGED("scope_changed"), EPOCH_CHANGED("epoch_changed"),
        INVALID_SEQUENCE("sequence_invalid"), TIMEOUT("timeout"),
    }
    data class Input(
        val epoch: String, val sequence: Long, val expectedIdentity: String,
        val nativeIdentity: String?, val nativeWindow: String?, val nativeState: String?,
        val scope: String?, val displayMatches: Boolean,
    )
    data class Decision(val kind: Kind, val reason: Reason, val fresh: Boolean = false)
    private var identity: String? = null
    private var epoch: String? = null
    private var failed: Reason? = null
    var lastSequence: Long = 0
        private set
    val adopted: Boolean get() = identity != null

    fun observe(value: Input, nowMs: Long): Decision {
        failed?.let { return Decision(Kind.FAIL, it) }
        if (nowMs >= deadlineMs) return fail(Reason.TIMEOUT)
        if (value.sequence <= 0) return fail(Reason.INVALID_SEQUENCE)
        if (value.nativeWindow != window || value.scope != "shell_tree") return fail(Reason.SCOPE_CHANGED)
        if (adopted) {
            if (value.epoch != epoch) return fail(Reason.EPOCH_CHANGED)
            if (value.expectedIdentity != identity || value.nativeIdentity != identity || value.nativeState != "registered") {
                return fail(Reason.SCOPE_CHANGED)
            }
        } else {
            // pending等でidentityがまだ無い初回のみ待つ。明確な失効/別対象を待機へ変換しない。
            if (value.nativeIdentity == null && value.nativeState in setOf("pending", "unavailable", "registered")) {
                return Decision(Kind.WAIT, Reason.REGISTRATION_PENDING)
            }
            if (value.nativeState != "registered" || value.nativeIdentity != value.expectedIdentity) return fail(Reason.SCOPE_CHANGED)
        }
        if (value.sequence < lastSequence) return Decision(Kind.WAIT, Reason.OLDER_SEQUENCE)
        if (!value.displayMatches) return Decision(Kind.WAIT, Reason.DISPLAY_PENDING)
        val fresh = value.sequence > lastSequence
        // 未照合のepoch/sequenceを先に消費しない。同じsequenceを表示到着後にもう一度照合できる。
        identity = value.expectedIdentity
        epoch = value.epoch
        lastSequence = value.sequence
        return Decision(Kind.ACCEPT, Reason.READY, fresh)
    }

    /** identity本文は含めず、固定state/reasonと不一致boolだけを保存する。 */
    fun metadata(value: Input, decision: Decision): Map<String, Any> = linkedMapOf(
        "decision" to decision.kind.name.lowercase(), "reason" to decision.reason.wire,
        "adopted" to adopted, "fresh" to decision.fresh,
        "native_state" to (value.nativeState?.takeIf { it in setOf("registered", "pending", "unavailable", "identity_changed", "ambiguous", "registration_timeout", "disconnected") } ?: "unknown"),
        "native_identity_present" to (value.nativeIdentity != null),
        "identity_matches" to (value.nativeIdentity == value.expectedIdentity),
        "window_matches" to (value.nativeWindow == window), "scope_matches" to (value.scope == "shell_tree"),
        "native_io_matches" to value.displayMatches,
    )
    private fun fail(reason: Reason): Decision { failed = reason; return Decision(Kind.FAIL, reason) }
}
