package io.github.tinmachine.eidolon

/** Payloads are private editor data. Their diagnostic representation never includes text. */
class ImePreedit(val text: String, val cursorBeginUtf8: Int, val cursorEndUtf8: Int) {
    override fun toString() = "ImePreedit(bytes=${text.toByteArray(Charsets.UTF_8).size},cursor=$cursorBeginUtf8:$cursorEndUtf8)"
}

class ImeEditTransaction(
    val generation: Long,
    val sequence: Long,
    val commit: String?,
    val preedit: ImePreedit?,
) {
    override fun toString() = "ImeEditTransaction(generation=$generation,sequence=$sequence,commitBytes=${commit?.toByteArray(Charsets.UTF_8)?.size ?: 0},preedit=$preedit)"
}

data class ImeEditorMetadata(
    val generation: Long,
    val sequence: Long,
    val active: Boolean,
    val utf16Length: Int,
    val utf8Bytes: Int,
    val selectionStart: Int,
    val selectionEnd: Int,
    val composingStart: Int,
    val composingEnd: Int,
    val batchDepth: Int,
    val acceptedTransactions: Long,
    val rejectedOperations: Long,
    val staleOperations: Long,
    val lastOperation: String,
    val lastFailure: String?,
)

class ImeEditorSnapshot(
    val text: String,
    val selectionStart: Int,
    val selectionEnd: Int,
    val composingStart: Int,
    val composingEnd: Int,
) {
    override fun toString() = "ImeEditorSnapshot(length=${text.length},selection=$selectionStart:$selectionEnd,composition=$composingStart:$composingEnd)"
}

/**
 * Terminal capability profile: only uncommitted local text is editable.
 * Accepted commits leave this buffer; previously committed terminal text is unavailable.
 * Calls and callbacks belong to the editor/UI owner. A false sink result means nothing was enqueued.
 */
class ImeEditorState(
    private val onTransaction: (ImeEditTransaction) -> Boolean = { true },
    private val onMetadata: (ImeEditorMetadata) -> Unit = {},
    private val onInvalidated: (Long, String) -> Unit = { _, _ -> },
    val maxUtf8Bytes: Int = MAX_TEXT_UTF8_BYTES,
    val maxBatchDepth: Int = 32,
) {
    init {
        require(maxUtf8Bytes in 1..MAX_TEXT_UTF8_BYTES)
        require(maxBatchDepth in 1..64)
    }

    private var generation = 0L
    private var sequence = 0L
    private var active = false
    private var text = ""
    private var selectionStart = 0
    private var selectionEnd = 0
    private var composingStart = -1
    private var composingEnd = -1
    private var batchDepth = 0
    private var pendingCommit = ""
    private var pendingPreedit = false
    private var acceptedTransactions = 0L
    private var rejectedOperations = 0L
    private var staleOperations = 0L
    private var lastOperation = "created"
    private var lastFailure: String? = null

    fun metadata() = ImeEditorMetadata(generation, sequence, active, text.length, utf8Size(text),
        selectionStart, selectionEnd, composingStart, composingEnd, batchDepth,
        acceptedTransactions, rejectedOperations, staleOperations, lastOperation, lastFailure)

    fun snapshot() = ImeEditorSnapshot(text, selectionStart, selectionEnd, composingStart, composingEnd)
    fun isCurrent(token: Long) = active && token == generation
    fun hasComposition() = text.isNotEmpty()
    fun canForwardKey(token: Long) = isCurrent(token) && text.isEmpty() && pendingCommit.isEmpty() && batchDepth == 0

    fun openConnection(): Long {
        invalidate("connection_replaced")
        generation++
        sequence = 0
        active = true
        lastOperation = "openConnection"
        lastFailure = null
        publish()
        return generation
    }

    fun reset(reason: String = "reset") {
        // Reasons are fixed identifiers, never caller text.
        val cause = if (reason in setOf("reset", "close", "focus_lost", "sink_rejected", "key_limit")) reason else "reset"
        invalidate(cause)
        generation++
        lastOperation = "reset"
        lastFailure = cause.takeIf { it == "sink_rejected" || it == "key_limit" }
        publish()
    }

    fun closeConnection(token: Long) {
        if (!checkToken(token, "closeConnection")) return
        invalidate("close")
        generation++
        publish()
    }

    fun beginBatchEdit(token: Long): Boolean {
        if (!checkToken(token, "beginBatchEdit")) return false
        if (batchDepth == maxBatchDepth) return reject("batch_limit")
        batchDepth++
        publish()
        return true
    }

    /** Android's result is whether a nested batch is still open, not whether this call succeeded. */
    fun endBatchEdit(token: Long): Boolean {
        if (!checkToken(token, "endBatchEdit")) return false
        if (batchDepth == 0) return reject("unbalanced_batch")
        batchDepth--
        if (batchDepth == 0 && !flush()) return false
        publish()
        return batchDepth > 0
    }

    fun setComposingText(token: Long, value: String, newCursorPosition: Int): Boolean {
        if (!checkToken(token, "setComposingText")) return false
        val replacement = replacement(value, newCursorPosition) ?: return false
        if (!fits(replacement.text, pendingCommit)) return reject("payload_limit")
        text = replacement.text
        selectionStart = replacement.cursor
        selectionEnd = replacement.cursor
        composingStart = if (value.isEmpty()) -1 else replacement.start
        composingEnd = if (value.isEmpty()) -1 else replacement.start + value.length
        return changed()
    }

    fun commitText(token: Long, value: String, newCursorPosition: Int): Boolean {
        if (!checkToken(token, "commitText")) return false
        val replacement = replacement(value, newCursorPosition) ?: return false
        // Moving within already committed terminal text cannot be represented by this profile.
        if (replacement.cursor != replacement.text.length) return reject("committed_cursor_unavailable")
        if (!fits("", pendingCommit, replacement.text)) return reject("payload_limit")
        pendingCommit += replacement.text
        clearLocal()
        return changed()
    }

    fun finishComposingText(token: Long): Boolean {
        if (!checkToken(token, "finishComposingText")) return false
        if (text.isEmpty()) { publish(); return true }
        if (selectionStart != text.length || selectionEnd != text.length) return reject("committed_cursor_unavailable")
        if (!fits("", pendingCommit, text)) return reject("payload_limit")
        pendingCommit += text
        clearLocal()
        return changed()
    }

    fun setSelection(token: Long, start: Int, end: Int): Boolean {
        if (!checkToken(token, "setSelection")) return false
        if (!boundary(text, start) || !boundary(text, end)) return reject("selection_unavailable")
        selectionStart = start
        selectionEnd = end
        return changed()
    }

    fun setComposingRegion(token: Long, start: Int, end: Int): Boolean {
        if (!checkToken(token, "setComposingRegion")) return false
        val first = minOf(start, end).coerceIn(0, text.length)
        val last = maxOf(start, end).coerceIn(0, text.length)
        if (!boundary(text, first) || !boundary(text, last)) return reject("invalid_utf16_boundary")
        composingStart = if (first == last) -1 else first
        composingEnd = if (first == last) -1 else last
        return changed()
    }

    fun deleteSurroundingText(token: Long, before: Int, after: Int, codePoints: Boolean = false): Boolean {
        if (!checkToken(token, if (codePoints) "deleteSurroundingTextInCodePoints" else "deleteSurroundingText")) return false
        if (before < 0 || after < 0) return reject("invalid_length")
        val first = minOf(selectionStart, selectionEnd)
        val last = maxOf(selectionStart, selectionEnd)
        val begin: Int
        val end: Int
        if (codePoints) {
            if (before > text.codePointCount(0, first) || after > text.codePointCount(last, text.length)) return reject("surrounding_unavailable")
            begin = text.offsetByCodePoints(first, -before)
            end = text.offsetByCodePoints(last, after)
        } else {
            if (before > first || after > text.length - last) return reject("surrounding_unavailable")
            begin = first - before
            end = last + after
        }
        if (!boundary(text, begin) || !boundary(text, end)) return reject("invalid_utf16_boundary")
        fun remap(position: Int): Int = when {
            position < 0 -> -1
            position <= begin -> position
            position <= first -> begin
            position <= last -> position - (first - begin)
            position <= end -> last - (first - begin)
            else -> position - (first - begin) - (end - last)
        }
        val next = text.substring(0, begin) + text.substring(first, last) + text.substring(end)
        selectionStart = remap(selectionStart)
        selectionEnd = remap(selectionEnd)
        composingStart = remap(composingStart)
        composingEnd = remap(composingEnd)
        if (composingStart == composingEnd) { composingStart = -1; composingEnd = -1 }
        text = next
        return changed()
    }

    fun textBeforeCursor(token: Long, length: Int): String? {
        if (!checkToken(token, "getTextBeforeCursor") || length < 0) return null
        val end = minOf(selectionStart, selectionEnd)
        if (length > end) { publish(); return null }
        var start = maxOf(0, end - length)
        if (!boundary(text, start)) start++
        publish()
        return text.substring(start, end)
    }

    fun textAfterCursor(token: Long, length: Int): String? {
        if (!checkToken(token, "getTextAfterCursor") || length < 0) return null
        val start = maxOf(selectionStart, selectionEnd)
        if (length > text.length - start) { publish(); return null }
        var end = start + minOf(length, text.length - start)
        if (!boundary(text, end)) end--
        publish()
        return text.substring(start, end)
    }

    fun selectedText(token: Long): String? {
        if (!checkToken(token, "getSelectedText")) return null
        publish()
        return text.substring(minOf(selectionStart, selectionEnd), maxOf(selectionStart, selectionEnd))
    }

    fun rejectOperation(token: Long, operation: String, reason: String = "unsupported"): Boolean {
        if (!checkToken(token, operation)) return false
        return reject(reason)
    }

    private class Replacement(val text: String, val cursor: Int, val start: Int)
    private fun replacement(value: String, cursor: Int): Replacement? {
        if (!validText(value)) { reject("invalid_text"); return null }
        if (value.length > maxUtf8Bytes || utf8Size(value) > maxUtf8Bytes) { reject("payload_limit"); return null }
        val start = if (composingStart >= 0) composingStart else minOf(selectionStart, selectionEnd)
        val end = if (composingEnd >= 0) composingEnd else maxOf(selectionStart, selectionEnd)
        val next = text.substring(0, start) + value + text.substring(end)
        val desired = if (cursor > 0) start.toLong() + value.length + cursor - 1 else start.toLong() + cursor
        val position = desired.coerceIn(0L, next.length.toLong()).toInt()
        if (!boundary(next, position)) { reject("invalid_utf16_boundary"); return null }
        return Replacement(next, position, start)
    }

    private fun changed(): Boolean {
        pendingPreedit = true
        val success = batchDepth > 0 || flush()
        publish()
        return success
    }

    private fun flush(): Boolean {
        if (pendingCommit.isEmpty() && !pendingPreedit) return true
        val transaction = ImeEditTransaction(generation, sequence + 1, pendingCommit.takeIf { it.isNotEmpty() },
            if (pendingPreedit) ImePreedit(text, utf8Size(text.substring(0, selectionStart)), utf8Size(text.substring(0, selectionEnd))) else null)
        val accepted = try { onTransaction(transaction) } catch (_: Exception) { false }
        // A sink may synchronously invalidate this generation; never mutate a replacement connection.
        if (!isCurrent(transaction.generation)) return false
        if (!accepted) {
            lastFailure = "sink_rejected"
            rejectedOperations++
            invalidate("sink_rejected")
            generation++
            return false
        }
        sequence++
        acceptedTransactions++
        pendingCommit = ""
        pendingPreedit = false
        return true
    }

    private fun checkToken(token: Long, operation: String): Boolean {
        lastOperation = operation
        if (!isCurrent(token)) {
            staleOperations++
            lastFailure = "stale_connection"
            publish()
            return false
        }
        lastFailure = null
        return true
    }

    private fun reject(reason: String): Boolean {
        rejectedOperations++
        lastFailure = reason
        publish()
        return false
    }

    private fun clearLocal() {
        text = ""
        selectionStart = 0
        selectionEnd = 0
        composingStart = -1
        composingEnd = -1
    }

    private fun invalidate(reason: String) {
        val previous = generation
        val wasActive = active
        active = false
        clearLocal()
        batchDepth = 0
        pendingCommit = ""
        pendingPreedit = false
        if (wasActive) onInvalidated(previous, reason)
    }

    private fun fits(vararg values: String): Boolean = values.sumOf { utf8Size(it).toLong() } <= maxUtf8Bytes
    private fun publish() = onMetadata(metadata())

    companion object {
        const val MAX_TEXT_UTF8_BYTES = 4000
        private fun utf8Size(value: String) = value.toByteArray(Charsets.UTF_8).size
        private fun boundary(value: String, offset: Int): Boolean = offset in 0..value.length &&
            !(offset > 0 && offset < value.length && value[offset - 1].isHighSurrogate() && value[offset].isLowSurrogate())
        private fun validText(value: String): Boolean {
            var index = 0
            while (index < value.length) {
                val char = value[index++]
                if (char == '\u0000' || char.isLowSurrogate()) return false
                if (char.isHighSurrogate() && (index == value.length || !value[index++].isLowSurrogate())) return false
            }
            return true
        }
    }
}
