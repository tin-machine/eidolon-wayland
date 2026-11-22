package io.github.tinmachine.eidolon

import java.util.concurrent.ConcurrentHashMap

internal class WindowPoseRequests {
    private data class Request(val runtime: Long, val code: String)
    private val requests = ConcurrentHashMap<String, Request>()
    val size: Int get() = requests.size
    fun start(id: String, runtime: Long) { requests[id] = Request(runtime, "pending") }
    fun complete(runtime: Long, id: String, code: String) {
        requests.computeIfPresent(id) { _, request ->
            if (request.runtime == runtime) request.copy(code = code) else request
        }
    }
    fun result(id: String): String? {
        val request = requests[id] ?: return null
        if (request.code != "pending") requests.remove(id, request)
        return request.code
    }
    fun remove(id: String) { requests.remove(id) }
    fun clear() = requests.clear()
}
