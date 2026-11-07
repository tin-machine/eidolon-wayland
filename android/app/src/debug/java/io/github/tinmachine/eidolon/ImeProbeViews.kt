package io.github.tinmachine.eidolon

import android.content.Context
import android.graphics.Color
import android.text.InputType
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.ExtractedTextRequest
import android.view.inputmethod.InputConnection
import android.view.inputmethod.InputConnectionWrapper
import android.view.inputmethod.TextAttribute
import android.widget.EditText
import android.widget.LinearLayout

/** Viewまで届いたpointerだけを数える。SDKで落ちたeventの存在を推測しない。 */
internal class ImeProbeLayout(context: Context, private val observe: (String) -> Unit) : LinearLayout(context) {
    override fun dispatchTouchEvent(event: MotionEvent): Boolean {
        if (event.actionMasked == MotionEvent.ACTION_DOWN) observe("touch_down")
        if (event.actionMasked == MotionEvent.ACTION_UP) observe("touch_up")
        return super.dispatchTouchEvent(event)
    }
    override fun dispatchGenericMotionEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_HOVER_ENTER -> observe("hover_enter")
            MotionEvent.ACTION_SCROLL -> observe("scroll")
        }
        return super.dispatchGenericMotionEvent(event)
    }
}

/** 標準EditTextの実編集はAndroidに任せ、callback種類と長さだけ比較する。 */
internal class ImeProbeEditText(context: Context, private val observe: (String, Int?) -> Unit) : EditText(context) {
    init {
        inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
        imeOptions = EditorInfo.IME_FLAG_NO_FULLSCREEN or
            EditorInfo.IME_FLAG_NO_PERSONALIZED_LEARNING or EditorInfo.IME_FLAG_NO_ENTER_ACTION
        setTextColor(Color.WHITE)
        setBackgroundColor(Color.rgb(52, 58, 68))
        textSize = 21f
        isSingleLine = false
        importantForAutofill = IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS
        isSaveEnabled = false
    }
    override fun onCreateInputConnection(outAttrs: EditorInfo): InputConnection? {
        val connection = super.onCreateInputConnection(outAttrs) ?: return null
        observe("create_connection", null)
        return object : InputConnectionWrapper(connection, false) {
            override fun setComposingText(text: CharSequence?, newCursorPosition: Int): Boolean {
                observe("set_composing_text", text?.length ?: 0)
                return super.setComposingText(text, newCursorPosition)
            }
            override fun setComposingText(text: CharSequence, newCursorPosition: Int, textAttribute: TextAttribute?): Boolean {
                observe("set_composing_text_attribute", text.length)
                return super.setComposingText(text, newCursorPosition, textAttribute)
            }
            override fun commitText(text: CharSequence?, newCursorPosition: Int): Boolean {
                observe("commit_text", text?.length ?: 0)
                return super.commitText(text, newCursorPosition)
            }
            override fun commitText(text: CharSequence, newCursorPosition: Int, textAttribute: TextAttribute?): Boolean {
                observe("commit_text_attribute", text.length)
                return super.commitText(text, newCursorPosition, textAttribute)
            }
            override fun finishComposingText(): Boolean {
                observe("finish_composing_text", null)
                return super.finishComposingText()
            }
            override fun setComposingRegion(start: Int, end: Int): Boolean {
                observe("set_composing_region", (end - start).coerceAtLeast(0))
                return super.setComposingRegion(start, end)
            }
            override fun setSelection(start: Int, end: Int): Boolean {
                observe("set_selection", null)
                return super.setSelection(start, end)
            }
            override fun beginBatchEdit(): Boolean {
                observe("begin_batch_edit", null)
                return super.beginBatchEdit()
            }
            override fun endBatchEdit(): Boolean {
                observe("end_batch_edit", null)
                return super.endBatchEdit()
            }
            override fun deleteSurroundingText(beforeLength: Int, afterLength: Int): Boolean {
                observe("delete_surrounding_utf16", null)
                return super.deleteSurroundingText(beforeLength, afterLength)
            }
            override fun deleteSurroundingTextInCodePoints(beforeLength: Int, afterLength: Int): Boolean {
                observe("delete_surrounding_codepoints", null)
                return super.deleteSurroundingTextInCodePoints(beforeLength, afterLength)
            }
            override fun getTextBeforeCursor(length: Int, flags: Int): CharSequence? =
                super.getTextBeforeCursor(length, flags).also { observe("get_text_before", it?.length) }
            override fun getTextAfterCursor(length: Int, flags: Int): CharSequence? =
                super.getTextAfterCursor(length, flags).also { observe("get_text_after", it?.length) }
            override fun getSelectedText(flags: Int): CharSequence? =
                super.getSelectedText(flags).also { observe("get_selected_text", it?.length) }
            override fun getExtractedText(request: ExtractedTextRequest?, flags: Int) =
                super.getExtractedText(request, flags).also { observe("get_extracted_text", it?.text?.length) }
            override fun requestCursorUpdates(cursorUpdateMode: Int): Boolean {
                observe("request_cursor_updates", null)
                return super.requestCursorUpdates(cursorUpdateMode)
            }
            override fun requestCursorUpdates(cursorUpdateMode: Int, cursorUpdateFilter: Int): Boolean {
                observe("request_cursor_updates_filter", null)
                return super.requestCursorUpdates(cursorUpdateMode, cursorUpdateFilter)
            }
            override fun sendKeyEvent(event: KeyEvent?): Boolean {
                observe("send_key_event", null)
                return super.sendKeyEvent(event)
            }
            override fun performEditorAction(editorAction: Int): Boolean {
                observe("editor_action", null)
                return super.performEditorAction(editorAction)
            }
            override fun closeConnection() {
                observe("close_connection", null)
                super.closeConnection()
            }
        }
    }
}
