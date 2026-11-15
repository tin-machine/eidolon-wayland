// Review fixture only; compiled explicitly outside the Android source sets.
package android.os

class Looper {
    companion object { private val main = Looper(); fun getMainLooper() = main }
}
class Handler(@Suppress("UNUSED_PARAMETER") looper: Looper) {
    fun post(action: () -> Unit): Boolean {
        if (reject) return false
        queue.addLast(action)
        return true
    }
    companion object {
        var reject = false
        private val queue = ArrayDeque<() -> Unit>()
        fun drain() { while (queue.isNotEmpty()) queue.removeFirst()() }
        fun reset() { queue.clear(); reject = false }
    }
}
