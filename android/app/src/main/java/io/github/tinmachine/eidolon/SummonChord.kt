package io.github.tinmachine.eidolon

/** 追跡と所有権を確認済みの同じ右 Touch の状態だけを渡す。 */
internal class SummonChord<T : Any> {
    sealed interface Action<out T> {
        data class Summon<T>(val target: T) : Action<T>
        data object Return : Action<Nothing>
    }

    data class Decision<T>(val action: Action<T>? = null, val consumeSecondary: Boolean = false)

    private enum class Phase { NEED_NEUTRAL, IDLE, ARMED, CONSUMED }

    private var phase = Phase.NEED_NEUTRAL
    private var source = 0L
    private var aWasDown = false
    private var bWasDown = false
    private var captured: T? = null
    private var consumeUntilRelease = false

    /** 追跡喪失時の全ボタン解放を物理的な A up とみなさない。 */
    fun invalidate() {
        phase = Phase.NEED_NEUTRAL
        captured = null
    }

    fun sample(rightSource: Long, aDown: Boolean, bDown: Boolean, hit: T?): Decision<T> {
        require(rightSource > 0)
        if (rightSource != source) {
            invalidate()
            source = rightSource
            consumeUntilRelease = false
            aWasDown = false
            bWasDown = false
        }
        val pressedA = aDown && !aWasDown
        val pressedB = bDown && !bWasDown
        val releasedA = !aDown && aWasDown
        val secondaryWasDown = bWasDown
        aWasDown = aDown
        bWasDown = bDown

        val decision: Decision<T> = when (phase) {
            Phase.NEED_NEUTRAL -> Decision(consumeSecondary = consumeUntilRelease && (bDown || secondaryWasDown))
            Phase.IDLE -> if (pressedA && !secondaryWasDown) {
                if (pressedB) {
                    consumeUntilRelease = true
                    phase = Phase.CONSUMED
                    Decision(Action.Return, consumeSecondary = true)
                } else {
                    captured = hit
                    phase = Phase.ARMED
                    Decision()
                }
            } else Decision()
            Phase.ARMED -> when {
                aDown && pressedB -> {
                    captured = null
                    consumeUntilRelease = true
                    phase = Phase.CONSUMED
                    Decision(Action.Return, consumeSecondary = true)
                }
                releasedA -> {
                    val target = captured
                    captured = null
                    phase = Phase.NEED_NEUTRAL
                    Decision(target?.let { Action.Summon(it) })
                }
                else -> Decision()
            }
            Phase.CONSUMED -> Decision(consumeSecondary = bDown || secondaryWasDown)
        }
        if (!aDown && !bDown) {
            phase = Phase.IDLE
            consumeUntilRelease = false
        }
        return decision
    }
}
