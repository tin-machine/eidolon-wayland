package io.github.tinmachine.eidolon

/** One immutable terminal destination; never substitutes a newly selected window. */
data class ImeRawReturnFrame(val runtime: Long, val ticket: Long, val epoch: Long, val available: Boolean,
    val focus: ImeSessionFocus?, val cleanupConfirmed: Boolean, val androidReleased: Boolean)
enum class ImeRawReturnPhase { WAITING_CLEANUP, WAITING_FOCUS, COMPLETE, CANCELLED, FAILED }
data class ImeRawReturnMetadata(val phase: ImeRawReturnPhase, val failure: String?)

/** UI thread owner. Enqueue, delivery ACK, and observed focus must all agree; no automatic resend. */
class ImeRawFocusReturn(val runtime: Long, val target: ImeSdkTarget, val ticket: Long,
    private val initialEpoch: Long, private val previousToken: String?,
    private val clock: () -> Long, private val requestId: () -> String,
    private val enqueue: (String, Long) -> ImeEnqueueResult,
    timeoutMillis: Long = 8000,
) {
    private var phase = ImeRawReturnPhase.WAITING_CLEANUP
    private var failure: String? = null
    private val deadline = clock() + timeoutMillis
    private var pending: String? = null
    private var sentEpoch: Long? = null
    private var applied = false
    init { require(runtime > 0 && target.valid() && ticket >= 0 && initialEpoch >= 0 && timeoutMillis in 1..60000) }
    fun metadata() = ImeRawReturnMetadata(phase, failure)
    fun blocksRaw() = phase !in setOf(ImeRawReturnPhase.COMPLETE, ImeRawReturnPhase.CANCELLED)
    fun cancel() { if (phase != ImeRawReturnPhase.COMPLETE) { phase = ImeRawReturnPhase.CANCELLED; failure = "owner_changed" } }
    fun expire() { if (phase in setOf(ImeRawReturnPhase.WAITING_CLEANUP, ImeRawReturnPhase.WAITING_FOCUS) && clock() >= deadline) fail("return_timeout") }
    private fun fail(reason: String) { phase = ImeRawReturnPhase.FAILED; failure = reason }
    fun observe(frame: ImeRawReturnFrame, completions: List<ImeSessionCompletion>) {
        if (phase in setOf(ImeRawReturnPhase.COMPLETE, ImeRawReturnPhase.CANCELLED, ImeRawReturnPhase.FAILED)) return
        // A late ACK never converts expiry to success.
        expire(); if (phase == ImeRawReturnPhase.FAILED) return
        if (frame.runtime != runtime || frame.ticket != ticket || !frame.available) { cancel(); return }
        if (phase == ImeRawReturnPhase.WAITING_CLEANUP) {
            if (!frame.cleanupConfirmed || !frame.androidReleased) return
            if (frame.epoch <= initialEpoch) return
            sentEpoch = frame.epoch; pending = requestId(); phase = ImeRawReturnPhase.WAITING_FOCUS
            val result = try { enqueue(checkNotNull(pending), frame.epoch) } catch (_: Exception) { ImeEnqueueResult.UNAVAILABLE }
            if (result != ImeEnqueueResult.ACCEPTED) fail("focus_enqueue_rejected")
            return
        }
        if (frame.epoch != sentEpoch) { cancel(); return }
        for (completion in completions) if (completion.request == pending) {
            if (completion.result != ImeDeliveryResult.APPLIED) { fail("focus_delivery_rejected"); return }
            applied = true
        }
        val focus = frame.focus
        if (applied && focus != null && focus.runtime == runtime && focus.target == target && focus.epoch == sentEpoch &&
            focus.token != previousToken && focus.token.matches(Regex("[1-9][0-9]*")) && focus.token.toLongOrNull() != null) {
            phase = ImeRawReturnPhase.COMPLETE
        }
    }
}
