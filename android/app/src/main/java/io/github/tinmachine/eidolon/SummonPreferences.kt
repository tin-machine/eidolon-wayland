package io.github.tinmachine.eidolon

internal data class SummonPreferences(val distanceCm: Int = 100, val belowEyeCm: Int = 12) {
    val valid: Boolean get() = distanceCm in 75..200 && belowEyeCm in 0..40
    val distance: Double get() = distanceCm / 100.0
    val heightOffset: Double get() = -belowEyeCm / 100.0
}
