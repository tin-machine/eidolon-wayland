package io.github.tinmachine.eidolon

import android.util.Log
import android.view.InputDevice
import android.view.KeyEvent
import org.json.JSONObject

/** Debug-only counters for one editor. Text goes straight to the explicitly enabled log tag. */
internal class ImeInputTrace {
    enum class Event {
        PRE_IME, PANEL, EDITOR, IME_KEY, COMPOSING, COMMIT, FINISH, REJECTED, SURROUNDING_UNAVAILABLE, EDITOR_UNAVAILABLE
    }

    private val totals = LongArray(Event.entries.size)

    fun record(event: Event) {
        totals[event.ordinal]++
    }

    fun key(stage: Event, event: KeyEvent) {
        if (event.action != KeyEvent.ACTION_DOWN) return
        record(stage)
        if (!textLoggingEnabled() || event.repeatCount != 0 || event.isCanceled ||
            !event.isFromSource(InputDevice.SOURCE_KEYBOARD) || event.isCtrlPressed || event.isMetaPressed) return
        val codePoint = event.unicodeChar
        if (Character.isValidCodePoint(codePoint) && !Character.isISOControl(codePoint)) {
            logText(stage, String(Character.toChars(codePoint)))
        }
    }

    fun text(stage: Event, value: String) {
        record(stage)
        if (textLoggingEnabled()) logText(stage, value)
    }

    fun counts(): String = Event.entries.joinToString(" ") { "${it.name.lowercase()}=${totals[it.ordinal]}" }

    private fun logText(stage: Event, value: String) {
        val length = value.codePointCount(0, value.length)
        val sample = value.substring(0, value.offsetByCodePoints(0, length.coerceAtMost(MAX_TEXT_CODE_POINTS)))
        Log.v(TEXT_TAG, "${stage.name.lowercase()} text=${JSONObject.quote(sample)} truncated=${length > MAX_TEXT_CODE_POINTS}")
    }

    private fun textLoggingEnabled() = BuildConfig.DEBUG && Log.isLoggable(TEXT_TAG, Log.VERBOSE)

    companion object {
        private const val TEXT_TAG = "EidolonImeText"
        private const val MAX_TEXT_CODE_POINTS = 80
    }
}
