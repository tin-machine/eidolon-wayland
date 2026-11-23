package io.github.tinmachine.eidolon

/** Worker-owned. A focused Linux editor may request one paired Termux connection per outage. */
internal class TermuxImeReconnect<T : Any> {
    private var stopped = false
    private var attempted: T? = null

    fun request(disconnectedFocus: T?, canOpen: Boolean): Boolean {
        if (disconnectedFocus == null) {
            attempted = null
            return false
        }
        if (stopped || !canOpen || attempted == disconnectedFocus) return false
        attempted = disconnectedFocus
        return true
    }

    fun stop() {
        stopped = true
    }

    fun resume() {
        stopped = false
        attempted = null
    }
}
