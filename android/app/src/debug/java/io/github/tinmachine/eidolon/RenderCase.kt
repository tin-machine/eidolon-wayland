package io.github.tinmachine.eidolon

import android.graphics.PixelFormat
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import org.json.JSONArray
import org.json.JSONObject

/** ImageReaderをconsumerにした実GPU検査。SDK/HMD・実XR可視性の合格とは区別する。 */
object RenderCase {
    fun run(nonce: String, configuration: String, store: DiagnosticsStore) {
        val result = JSONObject().put("case", "render").put("nonce", nonce).put("result", "running")
            .put("consumer", "ImageReader").put("synthetic_visibility", true)
        val rows = JSONArray()
        val handlerThread = HandlerThread("eidolon-render-consumer").apply { start() }
        var active = 0L
        var reader: ImageReader? = null
        var stage = "create"
        store.publishCheck(result)
        try {
            active = NativeBridge.nativeCreate(configuration)
            check(active > 0)
            waitFor { runtime(active).getString("state") == "Ready" }
            send(active, command("set_activity_state", 1).put("visible", true))
            send(active, command("set_xr_state", 2).put("state", "FOCUSED"))
            send(active, command("scene_ready", 3).put("scene", "1"))
            val received = AtomicInteger()
            for (generation in 1..2) {
                val width = if (generation == 1) 64 else 128
                val height = if (generation == 1) 64 else 96
                val target = JSONObject().put("session", "1").put("window", "1").put("scene", "1").put("generation", generation.toString())
                val local = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 3)
                reader = local
                local.setOnImageAvailableListener({ source -> source.acquireLatestImage()?.use { received.incrementAndGet() } }, Handler(handlerThread.looper))
                if (generation == 1) {
                    stage = "pattern"
                    check(NativeBridge.nativeAttachSurface(active, 1, 1, 1, 1, local.surface, width, height) == NativeBridge.ACCEPTED)
                    var pattern = JSONObject()
                    waitFor {
                        pattern = runtime(active).getJSONObject("render")
                        check(pattern.isNull("error"))
                        val targets = pattern.getJSONArray("targets")
                        targets.length() == 1 && targets.getJSONObject(0).getBoolean("readback")
                    }
                    result.put("pattern", pattern)
                }
                for (index in 0 until 18) {
                    stage = "generation=$generation/stage=$index"
                    val request = 100L + (generation-1)*18 + index
                    send(active, command("render_probe", request).put("nonce", nonce.take(16)).put("target", target)
                        .put("stage", index).put("width", width).put("height", height))
                    if (index == 0 && generation == 2) check(NativeBridge.nativeAttachSurface(active, 1, 1, 1, generation.toLong(), local.surface, width, height) == NativeBridge.ACCEPTED)
                    var render = JSONObject()
                    waitFor {
                        val state = runtime(active)
                        check(state.getString("state") == "Ready")
                        render = state.getJSONObject("render")
                        check(render.isNull("error"))
                        val targets = render.getJSONArray("targets")
                        targets.length() == 1 && targets.getJSONObject(0).optString("content_frame") == request.toString()
                    }
                    check(render.getInt("contexts") == 1 && render.getInt("latest_images") == 1)
                    check(render.getJSONArray("targets").getJSONObject(0).getBoolean("readback"))
                    rows.put(JSONObject().put("cycle", rows.length()+1).put("generation", generation).put("stage", index)
                        .put("frame", request.toString()).put("render", render))
                    result.put("cycles", rows)
                    store.publishCheck(result)
                }
                if (generation == 1) {
                    send(active, command("surface_lost", 800).put("target", target))
                    waitFor { runtime(active).getJSONObject("render").getJSONArray("targets").length() == 0 }
                    closeReader(local, Handler(handlerThread.looper)); reader = null
                }
            }
            stage = "stop"
            check(received.get() > 0)
            val started = SystemClock.elapsedRealtime()
            check(NativeBridge.nativeRequestStop(active) == NativeBridge.ACCEPTED)
            val accepted = SystemClock.elapsedRealtime()-started
            check(accepted < 100)
            waitFor { runtime(active).getString("state") == "Stopped" }
            val after = runtime(active)
            val gpu = after.getJSONObject("render")
            check(gpu.getInt("contexts") == 0 && gpu.getInt("latest_images") == 0 && gpu.getInt("workers") == 0)
            check(gpu.getJSONObject("composition").getInt("textures") == 0)
            waitFor { NativeBridge.nativeDestroy(active) == NativeBridge.ACCEPTED }
            result.put("after_stop", after).put("last_handle", active.toString()).put("accept_ms", accepted)
                .put("stop_ms", SystemClock.elapsedRealtime()-started).put("consumer_images", received.get()).put("result", "pass")
            active = 0
        } catch (failure: Exception) {
            result.put("result", "fail").put("stage", stage).put("failure_class", failure.javaClass.simpleName)
                .put("failure_site", failure.stackTrace.firstOrNull()?.toString())
            if (active > 0) result.put("snapshot", JSONObject(NativeBridge.nativeSnapshot(active)))
        } finally {
            if (active > 0) {
                NativeBridge.nativeRequestStop(active)
                try { waitFor { NativeBridge.nativeDestroy(active) != NativeBridge.BUSY } } catch (_: Exception) { result.put("cleanup_failed", true) }
            }
            reader?.let { closeReader(it, Handler(handlerThread.looper)) }
            handlerThread.quitSafely(); handlerThread.join(2000)
            result.put("consumer_worker_stopped", !handlerThread.isAlive)
            store.publishCheck(result)
        }
    }
    private fun runtime(handle: Long): JSONObject = JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("runtime")
    private fun closeReader(reader: ImageReader, handler: Handler) {
        val done = CountDownLatch(1)
        check(handler.post {
            try { reader.setOnImageAvailableListener(null, null); reader.close() } finally { done.countDown() }
        })
        check(done.await(3, TimeUnit.SECONDS))
    }
    private fun command(type: String, request: Long): JSONObject = JSONObject().put("schema", 1).put("type", type).put("request_id", request.toString())
    private fun send(handle: Long, command: JSONObject) {
        check(NativeBridge.nativeCommand(handle, command.toString()) == NativeBridge.ACCEPTED)
        var complete = false
        waitFor {
            val events = JSONObject(NativeBridge.nativePoll(handle, 64)).getJSONArray("events")
            for (index in 0 until events.length()) {
                val event = events.getJSONObject(index)
                if (event.optString("type") == "command_complete" && event.optString("request_id") == command.getString("request_id")) {
                    check(event.getString("code") == "ACCEPTED"); complete = true
                }
            }
            complete
        }
    }
    private fun waitFor(test: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime()+15000
        while (!test()) { check(SystemClock.elapsedRealtime() < deadline); Thread.sleep(5) }
    }
}
