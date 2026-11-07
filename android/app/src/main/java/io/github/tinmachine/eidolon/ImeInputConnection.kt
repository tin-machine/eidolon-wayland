package io.github.tinmachine.eidolon

import android.os.Handler
import android.text.Editable
import android.view.KeyEvent
import android.view.View
import android.view.inputmethod.BaseInputConnection
import android.view.inputmethod.ExtractedText
import android.view.inputmethod.ExtractedTextRequest
import android.view.inputmethod.SurroundingText
import android.view.inputmethod.TextAttribute

/** Android facade for the bounded terminal editor. It never writes to a PTY or invents keycodes. */
class ImeInputConnection(
    target: View,
    private val editor: ImeEditorState,
    private val generation: Long,
    private val onChanged: () -> Unit,
    private val onKey: (KeyEvent) -> Boolean = { false },
    private val onAction: (Int) -> Boolean = { false },
    private val canEdit: () -> Boolean = { true },
) : BaseInputConnection(target, true) {
    private val ownerHandler = Handler(target.context.mainLooper)
    private var closed = false
    private var closing = false

    override fun getHandler(): Handler = ownerHandler
    override fun getEditable(): Editable? = null

    private inline fun edit(operation: () -> Boolean): Boolean {
        if (!canEdit()) return false
        val result = operation()
        onChanged()
        return result
    }

    private fun copyText(value: CharSequence?, operation: String): String? {
        if (!canEdit()) return null
        if (value == null || value.length > editor.maxUtf8Bytes) {
            editor.rejectOperation(generation, operation, if (value == null) "invalid_text" else "payload_limit")
            onChanged()
            return null
        }
        return value.toString()
    }

    override fun beginBatchEdit() = edit { editor.beginBatchEdit(generation) }
    override fun endBatchEdit() = edit { editor.endBatchEdit(generation) }
    override fun setComposingText(text: CharSequence?, newCursorPosition: Int): Boolean {
        val value = copyText(text, "setComposingText") ?: return false
        return edit { editor.setComposingText(generation, value, newCursorPosition) }
    }
    override fun setComposingText(text: CharSequence, newCursorPosition: Int, textAttribute: TextAttribute?) =
        setComposingText(text, newCursorPosition)

    override fun commitText(text: CharSequence?, newCursorPosition: Int): Boolean {
        val value = copyText(text, "commitText") ?: return false
        return edit { editor.commitText(generation, value, newCursorPosition) }
    }
    override fun commitText(text: CharSequence, newCursorPosition: Int, textAttribute: TextAttribute?) =
        commitText(text, newCursorPosition)

    override fun finishComposingText() = if (closing) false else edit { editor.finishComposingText(generation) }
    override fun setSelection(start: Int, end: Int) = edit { editor.setSelection(generation, start, end) }
    override fun setComposingRegion(start: Int, end: Int) = edit { editor.setComposingRegion(generation, start, end) }
    override fun setComposingRegion(start: Int, end: Int, textAttribute: TextAttribute?) = setComposingRegion(start, end)
    override fun deleteSurroundingText(beforeLength: Int, afterLength: Int) =
        edit { editor.deleteSurroundingText(generation, beforeLength, afterLength) }
    override fun deleteSurroundingTextInCodePoints(beforeLength: Int, afterLength: Int) =
        edit { editor.deleteSurroundingText(generation, beforeLength, afterLength, codePoints = true) }

    override fun getTextBeforeCursor(length: Int, flags: Int): CharSequence? = editor.textBeforeCursor(generation, length)
    override fun getTextAfterCursor(length: Int, flags: Int): CharSequence? = editor.textAfterCursor(generation, length)
    override fun getSelectedText(flags: Int): CharSequence? = editor.selectedText(generation)
    override fun getCursorCapsMode(reqModes: Int) = 0
    override fun getExtractedText(request: ExtractedTextRequest?, flags: Int): ExtractedText? {
        editor.rejectOperation(generation, "getExtractedText", "surrounding_unavailable")
        return null
    }
    override fun getSurroundingText(beforeLength: Int, afterLength: Int, flags: Int): SurroundingText? {
        editor.rejectOperation(generation, "getSurroundingText", "surrounding_unavailable")
        return null
    }
    override fun replaceText(start: Int, end: Int, text: CharSequence, newCursorPosition: Int, textAttribute: TextAttribute?) =
        edit { editor.rejectOperation(generation, "replaceText", "surrounding_unavailable") }

    override fun sendKeyEvent(event: KeyEvent?): Boolean {
        if (!canEdit()) return false
        if (event == null || !editor.isCurrent(generation) ||
            (event.action != KeyEvent.ACTION_UP && !editor.canForwardKey(generation))) return edit {
            editor.rejectOperation(generation, "sendKeyEvent", "key_route_unavailable")
        }
        // The owner also verifies that releases belong to a down it accepted for this generation.
        return onKey(event)
    }

    override fun performEditorAction(editorAction: Int): Boolean {
        if (!canEdit()) return false
        if (!editor.canForwardKey(generation)) return edit {
            editor.rejectOperation(generation, "performEditorAction", "action_route_unavailable")
        }
        return onAction(editorAction)
    }

    override fun performContextMenuAction(id: Int) = edit { editor.rejectOperation(generation, "performContextMenuAction") }
    override fun clearMetaKeyStates(states: Int) = false
    override fun requestCursorUpdates(cursorUpdateMode: Int) = editor.rejectOperation(generation, "requestCursorUpdates", "cursor_updates_unavailable")
    override fun requestCursorUpdates(cursorUpdateMode: Int, cursorUpdateFilter: Int) = requestCursorUpdates(cursorUpdateMode)

    override fun closeConnection() {
        if (closed) return
        closed = true
        // Invalidate first: BaseInputConnection.closeConnection invokes finishComposingText.
        editor.closeConnection(generation)
        closing = true
        try { super.closeConnection() } finally { closing = false; onChanged() }
    }
}
