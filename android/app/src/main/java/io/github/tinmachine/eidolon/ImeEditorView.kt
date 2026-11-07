package io.github.tinmachine.eidolon

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.text.InputType
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputConnection
import android.view.inputmethod.InputMethodManager

/** Visible custom editor for the terminal-capability probe; no production delivery is connected. */
class ImeEditorView(
    context: Context,
    onTransaction: (ImeEditTransaction) -> Boolean = { true },
    onMetadata: (ImeEditorMetadata) -> Unit = {},
    private val onKeyEvent: (KeyEvent) -> Boolean = { false },
    private val onEditorAction: (Int) -> Boolean = { false },
    onInvalidated: (Long, String) -> Unit = { _, _ -> },
    private val onConnectionOpened: (Long) -> Unit = {},
    private val canEditGeneration: (Long) -> Boolean = { true },
) : View(context) {
    private data class HeldKey(val device: Int, val scan: Int, val code: Int)
    private val heldKeys = mutableMapOf<HeldKey, Long>()
    private val editor = ImeEditorState(onTransaction, onMetadata, { generation, reason ->
        heldKeys.clear()
        // A production sink must release its owned raw keys through the urgent-reset route here.
        onInvalidated(generation, reason)
    })
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        textSize = 22f * resources.displayMetrics.scaledDensity
    }
    private var connection: ImeInputConnection? = null
    private val inputMethod get() = context.getSystemService(InputMethodManager::class.java)

    init {
        isFocusable = true
        isFocusableInTouchMode = true
        isClickable = true
        isSaveEnabled = false
        importantForAutofill = IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS
        minimumHeight = (80 * resources.displayMetrics.density).toInt()
        setBackgroundColor(Color.rgb(35, 39, 55))
        contentDescription = "端末相当の変換入力欄"
    }

    fun metadata(): ImeEditorMetadata = editor.metadata()

    fun resetEditor(restartInput: Boolean = true) {
        editor.reset()
        connection = null
        invalidate()
        if (restartInput && isAttachedToWindow && hasFocus()) inputMethod?.restartInput(this)
    }

    fun closeEditor() {
        editor.reset("close")
        connection = null
        inputMethod?.hideSoftInputFromWindow(windowToken, 0)
        clearFocus()
        invalidate()
    }

    override fun onCheckIsTextEditor() = true

    override fun onCreateInputConnection(outAttrs: EditorInfo): InputConnection {
        val generation = editor.openConnection()
        onConnectionOpened(generation)
        outAttrs.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
        outAttrs.imeOptions = EditorInfo.IME_ACTION_NONE or EditorInfo.IME_FLAG_NO_FULLSCREEN or
            EditorInfo.IME_FLAG_NO_PERSONALIZED_LEARNING or EditorInfo.IME_FLAG_NO_ENTER_ACTION
        outAttrs.initialSelStart = 0
        outAttrs.initialSelEnd = 0
        outAttrs.contentMimeTypes = null
        return ImeInputConnection(this, editor, generation, ::editorChanged, ::forwardUnhandledKey, onEditorAction, { canEditGeneration(generation) }).also { connection = it }
    }

    private fun editorChanged() {
        val state = editor.metadata()
        if (state.active && state.batchDepth == 0) {
            inputMethod?.updateSelection(this, state.selectionStart, state.selectionEnd, state.composingStart, state.composingEnd)
        }
        invalidate()
    }

    override fun onFocusChanged(gainFocus: Boolean, direction: Int, previouslyFocusedRect: android.graphics.Rect?) {
        super.onFocusChanged(gainFocus, direction, previouslyFocusedRect)
        if (!gainFocus) {
            editor.reset("focus_lost")
            connection = null
            invalidate()
        }
    }

    override fun onDetachedFromWindow() {
        editor.reset("close")
        connection = null
        super.onDetachedFromWindow()
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (event.action == MotionEvent.ACTION_UP) {
            performClick()
            return true
        }
        return event.action == MotionEvent.ACTION_DOWN || super.onTouchEvent(event)
    }

    override fun performClick(): Boolean {
        super.performClick()
        requestFocus()
        inputMethod?.showSoftInput(this, InputMethodManager.SHOW_IMPLICIT)
        return true
    }

    private fun forwardUnhandledKey(event: KeyEvent): Boolean {
        val key = HeldKey(event.deviceId, event.scanCode, event.keyCode)
        val generation = editor.metadata().generation
        when (event.action) {
            KeyEvent.ACTION_DOWN -> {
                if (!editor.canForwardKey(generation) || (event.repeatCount > 0 && heldKeys[key] != generation)) return false
                if (key !in heldKeys && heldKeys.size >= 64) {
                    editor.reset("key_limit")
                    connection = null
                    invalidate()
                    return false
                }
                if (!onKeyEvent(event)) return false
                heldKeys[key] = generation
                return true
            }
            KeyEvent.ACTION_UP -> {
                val owner = heldKeys.remove(key) ?: return false
                if (!editor.isCurrent(owner)) return false
                // Only the release of a previously accepted down can cross a newly started composition.
                if (!onKeyEvent(event)) {
                    editor.reset("sink_rejected")
                    connection = null
                    invalidate()
                    return false
                }
                return true
            }
            else -> return false // ACTION_MULTIPLE can carry text; do not manufacture commits from it.
        }
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        // Back remains an OS navigation/dismissal operation, not terminal input.
        if (keyCode == KeyEvent.KEYCODE_BACK) return super.onKeyDown(keyCode, event)
        forwardUnhandledKey(event)
        return true
    }

    override fun onKeyUp(keyCode: Int, event: KeyEvent): Boolean {
        if (keyCode == KeyEvent.KEYCODE_BACK) return super.onKeyUp(keyCode, event)
        forwardUnhandledKey(event)
        return true
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val snapshot = editor.snapshot()
        val padding = 12f * resources.displayMetrics.density
        // Private local preedit is rendered, never exposed through diagnostic strings or logs.
        val visible = snapshot.text.ifEmpty { "未確定文字をここに表示" }
        canvas.drawText(visible, padding, padding - paint.ascent(), paint)
    }
}
