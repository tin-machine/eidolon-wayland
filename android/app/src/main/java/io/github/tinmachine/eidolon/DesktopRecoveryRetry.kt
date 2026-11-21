package io.github.tinmachine.eidolon

/** Monotonic deadlines. A successful reconnect can immediately run the recovery command. */
internal class DesktopRecoveryRetry {
    private var nextConnect = 0L
    private var nextRecover = 0L
    private var connectDelay = 1000L
    private var recoverDelay = 1000L

    fun reset() {
        nextConnect = 0L
        nextRecover = 0L
        connectDelay = 1000L
        recoverDelay = 1000L
    }

    fun admit(connect: Boolean, now: Long): Boolean {
        if (now < if (connect) nextConnect else nextRecover) return false
        if (connect) {
            nextConnect = now + connectDelay
            connectDelay = (connectDelay * 2).coerceAtMost(60000L)
        } else {
            nextRecover = now + recoverDelay
            recoverDelay = (recoverDelay * 2).coerceAtMost(60000L)
        }
        return true
    }
}
