package io.github.tinmachine.eidolon

import android.content.SharedPreferences

@JvmInline
internal value class GuiSessionPolicy private constructor(val minutes: Int) {
    val seconds: Int get() = minutes * 60

    companion object {
        val Default = GuiSessionPolicy(60)
        const val WireField = "gui_unresponsive_grace_seconds"
        const val PreferenceKey = "gui_unresponsive_grace_minutes"

        fun fromSaved(value: Any?): GuiSessionPolicy = fromMinutes(value as? Int) ?: Default

        fun parse(input: String): GuiSessionPolicy? = fromMinutes(
            input.trim().takeIf { it.all { digit -> digit in '0'..'9' } }?.toIntOrNull()
        )

        private fun fromMinutes(value: Int?): GuiSessionPolicy? =
            value?.takeIf { it in 1..1440 }?.let(::GuiSessionPolicy)
    }
}

internal class GuiSessionPolicyStore(saved: Any?, private val persist: (Int) -> Boolean) {
    enum class SaveResult { Saved, InvalidInput, WriteFailed }

    @Volatile
    var current = GuiSessionPolicy.fromSaved(saved)
        private set

    @Synchronized
    fun save(input: String): SaveResult {
        val next = GuiSessionPolicy.parse(input) ?: return SaveResult.InvalidInput
        if (next == current) return SaveResult.Saved
        if (!persist(next.minutes)) return SaveResult.WriteFailed
        current = next
        return SaveResult.Saved
    }

    companion object {
        fun fromPreferences(preferences: SharedPreferences): GuiSessionPolicyStore {
            val key = GuiSessionPolicy.PreferenceKey
            return GuiSessionPolicyStore(preferences.all[key]) { minutes ->
                val previous = preferences.all[key] as? Int
                if (preferences.edit().putInt(key, minutes).commit()) {
                    true
                } else {
                    // SharedPreferences edits its memory cache before the disk write.
                    val rollback = preferences.edit()
                    if (previous == null) rollback.remove(key) else rollback.putInt(key, previous)
                    rollback.apply()
                    false
                }
            }
        }
    }
}
