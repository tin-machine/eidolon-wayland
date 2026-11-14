package io.github.tinmachine.eidolon

/** Worker-owned cancellation. Queue acceptance is not the transport's completion. */
internal class RuntimeOriginStop {
    data class Target(val runtime: Long, val type: String, val field: String, val value: String)
    private data class Pending(val target: Target, val request: String, var result: String? = null)
    private var pending: Pending? = null
    private var staleConnect: Pair<Long, String>? = null

    fun reset() { pending = null; staleConnect = null }

    fun complete(runtime: Long, request: String, result: String) {
        val operation = pending ?: return
        if (operation.target.runtime != runtime || operation.request != request) return
        operation.result = result
        if (result == "STALE" && operation.target.type == "cancel_connect")
            staleConnect = runtime to operation.target.value
    }

    /** Returns true only after the captured command executed successfully; census still proves retirement. */
    fun step(runtime: Long, connectRequest: String?, session: String?, nextRequest: () -> String,
        enqueue: (Target, String) -> Int): Boolean {
        require(runtime > 0)
        val target = when {
            connectRequest != null && staleConnect != (runtime to connectRequest) ->
                Target(runtime, "cancel_connect", "connect_request", connectRequest)
            session != null -> Target(runtime, "disconnect", "session", session)
            else -> return false // A missing session is not proof that a queued Connect allocated nothing.
        }
        val previous = pending?.takeIf { it.target == target }
        if (previous != null) {
            if (previous.result == "ACCEPTED") return true
            if (previous.result == null) return false
        }
        val operation = Pending(target, nextRequest())
        pending = operation
        val code = enqueue(target, operation.request)
        if (code != 0) pending = null // Retry queue pressure; an immediate error proves no retirement.
        return false
    }
}
