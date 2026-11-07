package io.github.tinmachine.eidolon

/** SDK IDs remain exact decimal strings throughout private transport. */
data class ImeSdkTarget(val session: String, val window: String, val scene: String, val generation: String) {
    fun valid() = listOf(session, window, scene, generation).all(::imeId)
}
private fun imeId(value: String) = value.length <= 19 && value.matches(Regex("[1-9][0-9]*")) && value.toLongOrNull() != null
private fun imeInteger(value: Any?): Long? = when (value) { is Int -> value.toLong(); is Long -> value; else -> null }
data class ImeDecodedSnapshot(val valid: Boolean, val snapshot: ImeBridgeSnapshot?)

/** Input is selected JSON fields only. The foot profile never imports surrounding text. */
object ImePrivateSnapshot {
    fun decode(envelope: Map<String, Any?>): ImeDecodedSnapshot {
        fun invalid() = ImeDecodedSnapshot(false, null)
        if (imeInteger(envelope["schema"]) != 1L || imeInteger(envelope["code"]) != 0L || !envelope.containsKey("ime")) return invalid()
        val value = envelope["ime"] ?: return ImeDecodedSnapshot(true, null)
        val ime = value as? Map<*, *> ?: return invalid()
        val target = ime["target"] as? Map<*, *> ?: return invalid()
        fun id(key: String) = (target[key] as? String)?.takeIf(::imeId)
        val parsed = ImeNativeTarget(id("session") ?: return invalid(), id("window") ?: return invalid(),
            id("surface") ?: return invalid(), id("focus_token") ?: return invalid(),
            imeInteger(target["input_epoch"]) ?: return invalid(), imeInteger(target["activation"]) ?: return invalid(),
            imeInteger(target["serial"]) ?: return invalid())
        val revision = imeInteger(ime["edit_revision"]) ?: return invalid()
        val sequence = imeInteger(ime["last_sequence"]) ?: return invalid()
        if (!parsed.valid() || revision <= 0 || sequence < 0) return invalid()
        return ImeDecodedSnapshot(true, ImeBridgeSnapshot(parsed, revision, sequence))
    }
}

data class ImeSessionFocus(val runtime: Long, val epoch: Long, val target: ImeSdkTarget, val token: String)
data class ImeSessionFrame(val runtime: Long, val epoch: Long, val targetAvailable: Boolean,
    val focus: ImeSessionFocus?, val privateSnapshot: ImeDecodedSnapshot)
data class ImeSessionCompletion(val request: String, val result: ImeDeliveryResult)
sealed class ImeSessionCommand {
    class Backend(val enabled: Boolean) : ImeSessionCommand()
    object Reset : ImeSessionCommand()
    class Focus(val epoch: Long) : ImeSessionCommand()
    class Transaction(val focus: ImeSessionFocus, val payload: ImeNativeTransaction) : ImeSessionCommand()
}
enum class ImeSessionPhase { IDLE, ENABLING, NEEDS_RESET, RESETTING, NEEDS_FOCUS, FOCUSING, WAITING_CLIENT, BOUND, SUSPENDED, CLOSING, CLOSED }
data class ImeSessionMetadata(val phase: ImeSessionPhase, val editorGeneration: Long?, val pendingAcks: Int,
    val enqueued: Long, val applied: Long, val rawBlocked: Boolean, val failure: String?)

/** UI owner for one explicitly selected terminal; no payload or surrounding text is retained in ACK maps. */
class ImeSessionConnection(
    val runtime: Long,
    val target: ImeSdkTarget,
    initialEpoch: Long,
    private val requestId: () -> String,
    private val enqueue: (String, ImeSessionCommand) -> ImeEnqueueResult,
    private val clock: () -> Long,
    private val onClosed: () -> Unit = {},
    private val timeoutMillis: Long = 8000,
) {
    private enum class Kind { ENABLE, RESET, FOCUS, DELIVERY, DISABLE, CLOSE_RESET }
    private class Pending(val kind: Kind, val deadline: Long, val receipt: ImeBridgeReceipt? = null)
    private val pending = linkedMapOf<String, Pending>()
    private var frame = ImeSessionFrame(runtime, initialEpoch, true, null, ImeDecodedSnapshot(true, null))
    private var frameOrder = 0L
    private var phase = ImeSessionPhase.IDLE
    private var generation: Long? = null
    private var binding: ImeEditorBinding? = null
    private var boundFocus: ImeSessionFocus? = null
    private var enabled = false
    private var resetEpoch = initialEpoch
    private var focusEpoch = initialEpoch
    private var deadline = clock() + timeoutMillis
    private var suppressInvalidation = false
    private var failure: String? = null
    private var resetClosed = false
    private var disabled = false
    private var cleanupEpoch = initialEpoch
    private var rawBlocked = true
    private val bridge = ImeTransactionBridge(::enqueueTransaction, { _, _, _ ->
        if (!suppressInvalidation) close("bridge_invalidated")
    })
    init { require(runtime > 0 && target.valid() && initialEpoch >= 0 && timeoutMillis in 1..60000) }
    fun metadata(): ImeSessionMetadata {
        val values = bridge.metadata()
        return ImeSessionMetadata(phase, generation, pending.size, values.enqueued, values.applied, rawBlocked, failure)
    }
    private fun closing() = phase == ImeSessionPhase.CLOSING || phase == ImeSessionPhase.CLOSED

    /**
     * ownerがraw gateを閉じてから、View/ICを待たずに呼ぶ。
     * Backend command自体がnative urgent reset対象なので、先行Ctrl downを速やかに解除する。
     * ENABLE/RESETのACKは通常どおり追跡し、ICが来るまでfocusを取得しない。
     */
    fun start() {
        if (phase != ImeSessionPhase.IDLE) return
        deadline = clock() + timeoutMillis
        phase = ImeSessionPhase.ENABLING
        send(Kind.ENABLE, ImeSessionCommand.Backend(true))
    }

    fun openEditor(editorGeneration: Long) {
        if (closing() || editorGeneration <= 0) return
        if (generation == null && phase in setOf(ImeSessionPhase.IDLE, ImeSessionPhase.ENABLING,
                ImeSessionPhase.NEEDS_RESET, ImeSessionPhase.RESETTING, ImeSessionPhase.NEEDS_FOCUS)) {
            // 初回ICは進行中の準備へ合流する。startのACK/deadlineを捨てたり作り直したりしない。
            generation = editorGeneration
            start()
            advance()
            return
        }
        clearBinding(); pending.clear()
        generation = editorGeneration
        deadline = clock() + timeoutMillis
        if (enabled) phase = ImeSessionPhase.NEEDS_RESET else {
            phase = ImeSessionPhase.ENABLING
            send(Kind.ENABLE, ImeSessionCommand.Backend(true))
        }
        advance()
    }
    fun editorInvalidated(editorGeneration: Long) {
        if (closing() || generation != editorGeneration) return
        clearBinding(); generation = null; pending.clear()
        phase = ImeSessionPhase.SUSPENDED
        deadline = clock() + timeoutMillis
        // Reopened connections also require their own tracked reset and its fresh epoch.
        if (safeEnqueue(requestId(), ImeSessionCommand.Reset) != ImeEnqueueResult.ACCEPTED) close("reset_rejected")
    }
    /** Reject early IME edits without consuming/queuing text or invalidating the opening connection. */
    fun acceptsEditor(editorGeneration: Long) = phase == ImeSessionPhase.BOUND && generation == editorGeneration && boundFocus == frame.focus

    fun submit(transaction: ImeEditTransaction): Boolean {
        if (phase != ImeSessionPhase.BOUND || transaction.generation != generation) return false
        return binding?.submit(transaction) == true
    }
    /** The returned focus is immutable. Callers must not substitute the current/new window. */
    fun keyFocus(editorGeneration: Long): ImeSessionFocus? = boundFocus.takeIf {
        phase == ImeSessionPhase.BOUND && generation == editorGeneration && it == frame.focus
    }

    /** A worker packet includes completions followed by its fresh snapshot; UI calls are ordered. */
    fun observe(order: Long, next: ImeSessionFrame, completions: List<ImeSessionCompletion>) {
        if (phase == ImeSessionPhase.CLOSED || order <= frameOrder) return
        frameOrder = order; frame = next
        if (!closing() && (next.runtime != runtime || !next.targetAvailable || next.epoch < 0)) { close("target_unavailable"); return }
        if (!closing() && !next.privateSnapshot.valid) { close("invalid_snapshot"); return }
        for (completion in completions) {
            val item = pending.remove(completion.request) ?: continue
            if (item.deadline <= clock()) { timedOut(); return }
            if (item.kind == Kind.DELIVERY) bridge.acknowledge(checkNotNull(item.receipt), completion.result)
            else if (completion.result != ImeDeliveryResult.APPLIED) {
                if (closing()) { pending.clear(); phase = ImeSessionPhase.CLOSED; failure = "cleanup_unconfirmed" }
                else close("control_rejected")
            } else when (item.kind) {
                Kind.ENABLE -> { enabled = true; phase = ImeSessionPhase.NEEDS_RESET }
                Kind.RESET -> phase = ImeSessionPhase.NEEDS_FOCUS
                Kind.FOCUS -> phase = ImeSessionPhase.WAITING_CLIENT
                Kind.DISABLE -> disabled = true
                Kind.CLOSE_RESET -> resetClosed = true
                Kind.DELIVERY -> Unit
            }
            if (phase == ImeSessionPhase.CLOSED) return
        }
        if (pending.values.any { it.deadline <= clock() } || (phase != ImeSessionPhase.BOUND && clock() >= deadline)) {
            timedOut(); return
        }
        if (phase == ImeSessionPhase.CLOSING) {
            if (disabled && resetClosed && next.runtime == runtime && next.epoch > cleanupEpoch) {
                rawBlocked = false; phase = ImeSessionPhase.CLOSED
            }
            return
        }
        if (phase == ImeSessionPhase.BOUND) {
            if (next.focus != boundFocus) { close("focus_changed"); return }
            bridge.updateSnapshot(next.privateSnapshot.snapshot)
        }
        advance()
    }
    /** UI watchdog also calls this when worker polling stalls; no fresh native state is inferred. */
    fun expire() {
        if (phase != ImeSessionPhase.CLOSED && (pending.values.any { it.deadline <= clock() } ||
                (phase != ImeSessionPhase.BOUND && clock() >= deadline))) timedOut()
    }
    private fun timedOut() {
        if (closing()) { pending.clear(); phase = ImeSessionPhase.CLOSED; failure = "cleanup_unconfirmed" }
        else close("ack_timeout")
    }
    private fun advance() {
        when (phase) {
            ImeSessionPhase.NEEDS_RESET -> {
                resetEpoch = frame.epoch; phase = ImeSessionPhase.RESETTING
                send(Kind.RESET, ImeSessionCommand.Reset)
            }
            ImeSessionPhase.NEEDS_FOCUS -> if (generation != null && frame.epoch > resetEpoch) {
                focusEpoch = frame.epoch; phase = ImeSessionPhase.FOCUSING
                send(Kind.FOCUS, ImeSessionCommand.Focus(focusEpoch))
            }
            ImeSessionPhase.WAITING_CLIENT -> {
                val focus = frame.focus ?: return
                if (focus.runtime != runtime || focus.target != target || focus.epoch != focusEpoch || !imeId(focus.token)) { close("focus_changed"); return }
                val snapshot = frame.privateSnapshot.snapshot ?: return
                if (snapshot.target.session != target.session || snapshot.target.window != target.window ||
                    snapshot.target.inputEpoch != focus.epoch || snapshot.target.focusToken != focus.token) { close("snapshot_target_changed"); return }
                boundFocus = focus
                binding = bridge.bind(checkNotNull(generation), snapshot)
                if (binding == null) close("binding_rejected") else phase = ImeSessionPhase.BOUND
            }
            else -> Unit
        }
    }
    private fun enqueueTransaction(payload: ImeNativeTransaction, receipt: ImeBridgeReceipt): ImeEnqueueResult {
        val focus = boundFocus ?: return ImeEnqueueResult.UNAVAILABLE
        if (phase != ImeSessionPhase.BOUND || frame.focus != focus || payload.target.inputEpoch != focus.epoch ||
            payload.target.focusToken != focus.token) return ImeEnqueueResult.UNAVAILABLE
        return send(Kind.DELIVERY, ImeSessionCommand.Transaction(focus, payload), receipt)
    }
    private fun send(kind: Kind, command: ImeSessionCommand, receipt: ImeBridgeReceipt? = null): ImeEnqueueResult {
        if (pending.size >= 32) { close("ack_limit"); return ImeEnqueueResult.BUSY }
        val request = requestId()
        if (request in pending) { close("request_collision"); return ImeEnqueueResult.INVALID_ARGUMENT }
        // Reserve before enqueue, including when a fixture delivers completion synchronously.
        pending[request] = Pending(kind, clock() + timeoutMillis, receipt)
        val result = safeEnqueue(request, command)
        if (result != ImeEnqueueResult.ACCEPTED) {
            pending.remove(request)
            if (closing()) { pending.clear(); phase = ImeSessionPhase.CLOSED; failure = "cleanup_unconfirmed" }
            else close("enqueue_rejected")
        }
        return result
    }
    private fun safeEnqueue(request: String, command: ImeSessionCommand) =
        try { enqueue(request, command) } catch (_: Exception) { ImeEnqueueResult.UNAVAILABLE }
    private fun clearBinding() {
        suppressInvalidation = true
        try { binding?.invalidate() } finally { binding = null; boundFocus = null; suppressInvalidation = false }
    }
    fun close(reason: String = "closed") {
        if (closing()) return
        phase = ImeSessionPhase.CLOSING
        failure = reason.takeIf { it in setOf("closed", "bridge_invalidated", "reset_rejected", "target_unavailable", "invalid_snapshot",
            "ack_timeout", "control_rejected", "focus_changed", "snapshot_target_changed", "binding_rejected", "ack_limit",
            "request_collision", "enqueue_rejected", "key_rejected") } ?: "closed"
        clearBinding(); generation = null; pending.clear()
        cleanupEpoch = frame.epoch; deadline = clock() + timeoutMillis
        onClosed()
        send(Kind.CLOSE_RESET, ImeSessionCommand.Reset)
        // No automatic retry. If cleanup cannot be confirmed, the raw-key gate stays closed.
        if (phase == ImeSessionPhase.CLOSING) send(Kind.DISABLE, ImeSessionCommand.Backend(false))
    }
}
