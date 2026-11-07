package io.github.tinmachine.eidolon

/** Numeric IDs use the native positive signed-64 decimal string representation. */
data class ImeNativeTarget(
    val session: String,
    val window: String,
    val surface: String,
    val focusToken: String,
    val inputEpoch: Long,
    val activation: Long,
    val serial: Long,
) {
    internal fun valid() = listOf(session, window, surface, focusToken).all {
        it.length <= 19 && it.matches(Regex("[1-9][0-9]*")) && it.toLongOrNull() != null
    } && inputEpoch >= 0 && activation > 0 && serial in 0..0xffff_ffffL

    internal fun identity() = copy(serial = 0)
}

/** editRevision must come from the native owner, never be inferred from serial or cached text. */
data class ImeBridgeSnapshot(val target: ImeNativeTarget, val editRevision: Long, val lastSequence: Long?)

class ImeNativeTransaction internal constructor(
    val target: ImeNativeTarget,
    val editRevision: Long,
    val sequence: Long,
    val commit: String?,
    val preedit: ImePreedit?,
) {
    val deleteBefore = 0
    val deleteAfter = 0

    override fun toString() = "ImeNativeTransaction(target=$target,editRevision=$editRevision,sequence=$sequence,commitBytes=${commit?.toByteArray(Charsets.UTF_8)?.size ?: 0},preedit=$preedit)"

    /** Private transport payload only. Never put its result in diagnostics, logs, or exception text. */
    fun toPayloadJson(): String = buildString {
        append("{\"target\":{\"session\":"); appendJson(target.session)
        append(",\"window\":"); appendJson(target.window)
        append(",\"surface\":"); appendJson(target.surface)
        append(",\"focus_token\":"); appendJson(target.focusToken)
        append(",\"input_epoch\":").append(target.inputEpoch)
        append(",\"activation\":").append(target.activation)
        append(",\"serial\":").append(target.serial).append('}')
        append(",\"edit_revision\":").append(editRevision)
        append(",\"sequence\":").append(sequence)
        append(",\"commit\":"); if (commit == null) append("null") else appendJson(commit)
        append(",\"preedit\":")
        if (preedit == null) append("null") else {
            append("{\"text\":"); appendJson(preedit.text)
            append(",\"cursor_begin\":").append(preedit.cursorBeginUtf8)
            append(",\"cursor_end\":").append(preedit.cursorEndUtf8).append('}')
        }
        append(",\"delete_before\":0,\"delete_after\":0}")
    }

    private fun StringBuilder.appendJson(text: String) {
        append('"')
        for (char in text) when (char) {
            '"' -> append("\\\"")
            '\\' -> append("\\\\")
            '\b' -> append("\\b")
            '\u000c' -> append("\\f")
            '\n' -> append("\\n")
            '\r' -> append("\\r")
            '\t' -> append("\\t")
            else -> if (char < ' ') append("\\u").append(char.code.toString(16).padStart(4, '0')) else append(char)
        }
        append('"')
    }
}

enum class ImeEnqueueResult { ACCEPTED, BUSY, INVALID_ARGUMENT, UNAVAILABLE }
enum class ImeDeliveryResult { APPLIED, STALE, NOT_READY, INVALID_ARGUMENT, FAILED }
enum class ImeBridgeFailure {
    INVALID_BINDING, UNKNOWN_SEQUENCE_FLOOR, TARGET_CHANGED, EDIT_STATE_CHANGED, TARGET_UNAVAILABLE,
    SOURCE_SEQUENCE, INVALID_PAYLOAD, QUEUE_FULL, SEQUENCE_EXHAUSTED, ENQUEUE_REJECTED,
    DELIVERY_REJECTED, INVALIDATED,
}

/** Contains no text; the whole receipt identifies an ACK, including replaced bindings. */
class ImeBridgeReceipt internal constructor(
    val bindingId: Long,
    val editorGeneration: Long,
    val sourceSequence: Long,
    val nativeSequence: Long,
) {
    override fun toString() = "ImeBridgeReceipt(binding=$bindingId,generation=$editorGeneration,sourceSequence=$sourceSequence,nativeSequence=$nativeSequence)"
}

data class ImeBridgeMetadata(
    val bindingId: Long?,
    val editorGeneration: Long?,
    val active: Boolean,
    val enqueued: Long,
    val applied: Long,
    val rejected: Long,
    val ignoredCallbacks: Long,
    val pendingAcks: Int,
    val lastFailure: ImeBridgeFailure?,
)

/** Capture this handle in one editor's sink. Do not route an old sink through the latest handle. */
class ImeEditorBinding internal constructor(
    private val bridge: ImeTransactionBridge,
    val bindingId: Long,
    val editorGeneration: Long,
) {
    fun submit(transaction: ImeEditTransaction) = bridge.submit(this, transaction)
    fun invalidate() = bridge.invalidate(this)
}

/**
 * Private transport adapter. Backend adoption and NativeSessionHost/JNI enablement belong to the owner.
 * One UI owner serializes calls. ACCEPTED only acknowledges enqueue; acknowledge() handles delivery.
 * Queued payloads keep their original serial/edit revision. Only the native owner can decide whether
 * an older serial is geometry-only; a rejection cancels the binding and is never retried.
 */
class ImeTransactionBridge(
    private val enqueue: (ImeNativeTransaction, ImeBridgeReceipt) -> ImeEnqueueResult,
    private val onInvalidated: (bindingId: Long, editorGeneration: Long, reason: ImeBridgeFailure) -> Unit = { _, _, _ -> },
    private val maxPendingAcks: Int = 32,
) {
    init { require(maxPendingAcks in 1..64) }
    private class Active(val handle: ImeEditorBinding, var snapshot: ImeBridgeSnapshot, var sourceSequence: Long = 0)
    private var active: Active? = null
    private var nextBinding = 0L
    private var nativeIdentity: ImeNativeTarget? = null
    private var lastIssued = 0L
    private val pending = linkedMapOf<Long, ImeBridgeReceipt>()
    private var enqueued = 0L
    private var applied = 0L
    private var rejected = 0L
    private var ignored = 0L
    private var lastFailure: ImeBridgeFailure? = null

    fun metadata() = ImeBridgeMetadata(active?.handle?.bindingId, active?.handle?.editorGeneration,
        active != null, enqueued, applied, rejected, ignored, pending.size, lastFailure)

    /** The floor must be known from the native owner. null is never guessed as zero. */
    fun bind(editorGeneration: Long, snapshot: ImeBridgeSnapshot): ImeEditorBinding? {
        val nativeSequenceFloor = snapshot.lastSequence
        if (nativeSequenceFloor == null) { failBinding(ImeBridgeFailure.UNKNOWN_SEQUENCE_FLOOR); return null }
        if (editorGeneration <= 0 || !snapshot.target.valid() || snapshot.editRevision <= 0 || nativeSequenceFloor < 0 || nextBinding == Long.MAX_VALUE) {
            failBinding(ImeBridgeFailure.INVALID_BINDING)
            return null
        }
        dropActive(ImeBridgeFailure.INVALIDATED)
        if (active != null) return null // A reset callback installed a newer binding synchronously.
        val identity = snapshot.target.identity()
        lastIssued = if (identity == nativeIdentity) maxOf(lastIssued, nativeSequenceFloor) else nativeSequenceFloor
        nativeIdentity = identity
        val handle = ImeEditorBinding(this, ++nextBinding, editorGeneration)
        active = Active(handle, snapshot)
        lastFailure = null
        return handle
    }

    /** Only fresh, ordered snapshots from the native owner may enter here. */
    fun updateSnapshot(snapshot: ImeBridgeSnapshot?): Boolean {
        val current = active ?: return false
        val reason = when {
            snapshot == null -> ImeBridgeFailure.TARGET_UNAVAILABLE
            !snapshot.target.valid() || snapshot.editRevision <= 0 || snapshot.lastSequence == null || snapshot.lastSequence < 0 -> ImeBridgeFailure.INVALID_BINDING
            snapshot.target.identity() != current.snapshot.target.identity() -> ImeBridgeFailure.TARGET_CHANGED
            snapshot.editRevision != current.snapshot.editRevision -> ImeBridgeFailure.EDIT_STATE_CHANGED
            else -> null
        }
        if (reason != null) { dropActive(reason); return false }
        current.snapshot = checkNotNull(snapshot)
        lastIssued = maxOf(lastIssued, checkNotNull(snapshot.lastSequence))
        return true
    }

    internal fun submit(handle: ImeEditorBinding, transaction: ImeEditTransaction): Boolean {
        val current = active
        if (current == null || current.handle !== handle || transaction.generation != handle.editorGeneration) {
            ignored++
            return false
        }
        val reason = when {
            current.sourceSequence == Long.MAX_VALUE || transaction.sequence != current.sourceSequence + 1 -> ImeBridgeFailure.SOURCE_SEQUENCE
            !valid(transaction) -> ImeBridgeFailure.INVALID_PAYLOAD
            pending.size == maxPendingAcks -> ImeBridgeFailure.QUEUE_FULL
            lastIssued == Long.MAX_VALUE -> ImeBridgeFailure.SEQUENCE_EXHAUSTED
            else -> null
        }
        if (reason != null) { rejected++; dropActive(reason); return false }
        val receipt = ImeBridgeReceipt(handle.bindingId, handle.editorGeneration, transaction.sequence, ++lastIssued)
        val payload = ImeNativeTransaction(current.snapshot.target, current.snapshot.editRevision, receipt.nativeSequence, transaction.commit, transaction.preedit)
        // Reserve before enqueue to allow an immediate ACK; pending stores metadata only.
        pending[receipt.nativeSequence] = receipt
        current.sourceSequence = transaction.sequence
        val result = try { enqueue(payload, receipt) } catch (_: Exception) { ImeEnqueueResult.UNAVAILABLE }
        if (result == ImeEnqueueResult.ACCEPTED) enqueued++
        if (active !== current) return false
        if (result != ImeEnqueueResult.ACCEPTED) {
            rejected++
            dropActive(ImeBridgeFailure.ENQUEUE_REJECTED)
            return false
        }
        return true
    }

    fun acknowledge(receipt: ImeBridgeReceipt, result: ImeDeliveryResult): Boolean {
        val current = active
        if (current == null || current.handle.bindingId != receipt.bindingId || pending[receipt.nativeSequence] != receipt) {
            ignored++
            return false
        }
        pending.remove(receipt.nativeSequence)
        if (result == ImeDeliveryResult.APPLIED) applied++ else {
            rejected++
            dropActive(ImeBridgeFailure.DELIVERY_REJECTED)
        }
        return true
    }

    internal fun invalidate(handle: ImeEditorBinding) {
        if (active?.handle !== handle) { ignored++; return }
        dropActive(ImeBridgeFailure.INVALIDATED)
    }

    private fun failBinding(reason: ImeBridgeFailure) {
        rejected++
        dropActive(reason)
    }

    private fun dropActive(reason: ImeBridgeFailure) {
        val old = active
        active = null
        pending.clear()
        lastFailure = reason
        if (old != null) onInvalidated(old.handle.bindingId, old.handle.editorGeneration, reason)
    }

    private fun valid(transaction: ImeEditTransaction): Boolean {
        val commit = transaction.commit
        val preedit = transaction.preedit
        if (commit != null && !validText(commit)) return false
        if (preedit != null && (!validText(preedit.text) || !validCursor(preedit))) return false
        return (commit?.toByteArray(Charsets.UTF_8)?.size ?: 0) +
            (preedit?.text?.toByteArray(Charsets.UTF_8)?.size ?: 0) <= 8192
    }

    private fun validText(text: String): Boolean {
        if (text.length > ImeEditorState.MAX_TEXT_UTF8_BYTES) return false
        var index = 0
        while (index < text.length) {
            val char = text[index++]
            if (char == '\u0000' || char.isLowSurrogate()) return false
            if (char.isHighSurrogate() && (index == text.length || !text[index++].isLowSurrogate())) return false
        }
        return text.toByteArray(Charsets.UTF_8).size <= ImeEditorState.MAX_TEXT_UTF8_BYTES
    }

    private fun validCursor(preedit: ImePreedit): Boolean {
        if (preedit.cursorBeginUtf8 == -1 && preedit.cursorEndUtf8 == -1) return true
        val bytes = preedit.text.toByteArray(Charsets.UTF_8)
        fun boundary(offset: Int) = offset in 0..bytes.size && (offset == bytes.size || (bytes[offset].toInt() and 0xc0) != 0x80)
        return boundary(preedit.cursorBeginUtf8) && boundary(preedit.cursorEndUtf8)
    }
}
