package io.github.tinmachine.eidolon

import android.content.Context
import android.graphics.PixelFormat
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import java.security.MessageDigest
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import org.json.JSONArray
import org.json.JSONObject

/** 実SSH/foot→SHM→Smithay→EGL→ImageReader。SDK/HMD可視性・物理入力は別に確認する。 */
object FootCase {
    fun run(nonce: String, context: Context, configuration: String, store: DiagnosticsStore) {
        val result = JSONObject().put("case", "foot").put("nonce", nonce).put("result", "running")
            .put("consumer", "ImageReader").put("synthetic_visibility", true)
        val thread = HandlerThread("eidolon-foot-consumer").apply { start() }
        val handler = Handler(thread.looper)
        val pixels = Pixels()
        var reader: ImageReader? = null
        var active = 0L
        var stage = "create"
        store.publishCheck(result)
        try {
            active = NativeBridge.nativeCreate(configuration)
            check(active > 0)
            waitFor { runtime(active).getString("state") == "Ready" }
            send(active, command("set_activity_state", 1).put("visible", true))
            send(active, command("set_xr_state", 2).put("state", "FOCUSED"))
            send(active, command("scene_ready", 3).put("scene", "1"))
            stage = "connect"
            check(NativeBridge.nativeCommand(active, command("connect", 4).put("profile_id", "termux-local").toString()) == NativeBridge.ACCEPTED)
            val credentials = CredentialStore(context)
            var requests = 0
            var session = 0L
            waitFor(30000) {
                val events = JSONObject(NativeBridge.nativePoll(active, 64)).getJSONArray("events")
                for (index in 0 until events.length()) {
                    val event = events.getJSONObject(index)
                    if (event.optString("type") == "credential_required") {
                        requests++; session = event.getString("session").toLong()
                        check(credentials.provide(active, session, event.getString("request_id").toLong()) == NativeBridge.ACCEPTED)
                    }
                }
                val transport = runtime(active).getJSONObject("transport")
                check(transport.getString("state") != "Failed")
                transport.getString("state") == "Ready"
            }
            check(requests == 1 && session > 0)
            val token = MessageDigest.getInstance("SHA-256").digest(nonce.toByteArray()).take(8).joinToString("") { "%02x".format(it.toInt() and 255) }
            result.put("session", session.toString()).put("credential_requests", requests).put("token", token)
            stage = "start"
            send(active, command("start_app", 5).put("session", session.toString()).put("nonce", token).put("mode", "foot_fixture"))
            var before = JSONObject()
            waitFor(60000) {
                before = healthy(active)
                val app = before.optJSONObject("app") ?: return@waitFor false
                val windows = app.getJSONArray("windows")
                app.optLong("copies") > 0 && windows.length() == 1 && windows.getJSONObject(0).getString("life") == "Mapped" &&
                    before.getJSONObject("render").getInt("latest_images") == 1
            }
            Thread.sleep(300)
            before = healthy(active)
            check(before.getJSONObject("app").getLong("callbacks") == 0L)
            check(before.getJSONObject("render").getJSONArray("targets").length() == 0)
            result.put("before_target", before)
            val window = before.getJSONObject("app").getJSONArray("windows").getJSONObject(0).getString("window").toLong()
            result.put("window", window.toString())
            val mappedAt = SystemClock.elapsedRealtime()
            val rows = JSONArray()
            var previousGeneration = 0L
            for (generation in 1..2) {
                stage = "surface-$generation"
                val width = if (generation == 1) 1024 else 800
                val height = if (generation == 1) 768 else 600
                val local = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 3)
                reader = local
                local.setOnImageAvailableListener({ source ->
                    source.acquireLatestImage()?.use { image -> pixels.add(image) }
                }, handler)
                val callbacks = healthy(active).getJSONObject("app").getLong("callbacks")
                val hashes = pixels.summary().getInt("distinct_images")
                var plan = JSONObject()
                waitFor {
                    val plans = healthy(active).getJSONArray("panels")
                    plan = (0 until plans.length()).map { plans.getJSONObject(it) }.firstOrNull {
                        it.getString("action") == "create" && it.getJSONObject("target").getString("window") == window.toString() &&
                            it.getJSONObject("target").getString("generation").toLong() > previousGeneration
                    } ?: JSONObject()
                    plan.has("target")
                }
                val target = plan.getJSONObject("target")
                val targetGeneration = target.getString("generation").toLong()
                check(NativeBridge.nativeAttachWindowSurface(active, session, window, 1, targetGeneration, local.surface, width, height) == NativeBridge.ACCEPTED)
                var current = JSONObject()
                waitFor {
                    current = healthy(active)
                    val app = current.getJSONObject("app")
                    val render = current.getJSONObject("render")
                    val targets = render.getJSONArray("targets")
                    app.getLong("callbacks") >= callbacks+5 && targets.length() == 1 &&
                        targets.getJSONObject(0).optString("content_frame") == app.getJSONArray("windows").getJSONObject(0).optString("frame") &&
                        pixels.summary().getInt("distinct_images") >= hashes+4 && pixels.summary().getInt("non_uniform_images") > 0
                }
                send(active, command("adapter_applied", 20L+generation).put("command_seq", plan.getString("command_seq")).put("target", target).put("action", "create"))
                waitFor {
                    current = healthy(active)
                    current.getJSONArray("panels").getJSONObject(0).getBoolean("applied") &&
                        current.getJSONObject("render").getJSONArray("targets").getJSONObject(0).optString("content_frame") ==
                        current.getJSONObject("app").getJSONArray("windows").getJSONObject(0).optString("frame")
                }
                rows.put(JSONObject().put("generation", generation).put("target_generation", targetGeneration.toString()).put("runtime", current).put("pixels", pixels.summary()))
                result.put("cycles", rows); store.publishCheck(result)
                if (generation == 1) {
                    stage = "pause"
                    send(active, command("set_activity_state", 6).put("visible", false))
                    waitFor { healthy(active).getJSONObject("render").getBoolean("paused") }
                    Thread.sleep(250)
                    val paused = healthy(active)
                    Thread.sleep(400)
                    val held = healthy(active)
                    check(paused.getJSONObject("app").getLong("callbacks") == held.getJSONObject("app").getLong("callbacks"))
                    result.put("pause", paused).put("pause_held", held)
                    send(active, command("set_activity_state", 7).put("visible", true))
                    waitFor { healthy(active).getJSONObject("app").getLong("callbacks") >= held.getJSONObject("app").getLong("callbacks")+3 }
                    result.put("resumed", healthy(active))
                    send(active, command("surface_lost", 8).put("target", target))
                    waitFor { healthy(active).getJSONObject("render").getJSONArray("targets").length() == 0 }
                    closeReader(local, handler); reader = null
                    var retire = JSONObject()
                    waitFor {
                        retire = healthy(active).getJSONArray("panels").getJSONObject(0)
                        retire.getString("action") == "destroy"
                    }
                    send(active, command("adapter_applied", 9).put("command_seq", retire.getString("command_seq")).put("target", target).put("action", "destroy"))
                    previousGeneration = targetGeneration
                }
            }
            stage = "scroll"
            val beforeScroll = healthy(active).getJSONObject("app").getLong("callbacks")
            var scrolling = JSONObject()
            waitFor {
                scrolling = healthy(active)
                // 固定50ms/行の出力を4秒以上観察し、初期画面を超える行と画面下部の変化を検査する。
                SystemClock.elapsedRealtime()-mappedAt >= 4000 &&
                    scrolling.getJSONObject("app").getLong("callbacks") >= beforeScroll+8 &&
                    pixels.summary().getInt("lower_distinct_images") >= 5 && pixels.summary().getInt("lower_non_uniform_images") > 0
            }
            result.put("scroll", JSONObject().put("observed_ms", SystemClock.elapsedRealtime()-mappedAt)
                .put("callbacks_before", beforeScroll).put("runtime", scrolling).put("pixels", pixels.summary()))
            stage = "stop"
            val started = SystemClock.elapsedRealtime()
            check(NativeBridge.nativeRequestStop(active) == NativeBridge.ACCEPTED)
            val accepted = SystemClock.elapsedRealtime()-started
            check(accepted < 100)
            waitFor { runtime(active).getString("state") == "Stopped" }
            val after = runtime(active)
            check(after.getJSONObject("app").getBoolean("directory_cleaned"))
            check(after.getJSONObject("transport").getJSONArray("pending_cleanup").length() == 0)
            waitFor { NativeBridge.nativeDestroy(active) == NativeBridge.ACCEPTED }
            result.put("after_stop", after).put("last_handle", active.toString()).put("accept_ms", accepted)
                .put("stop_ms", SystemClock.elapsedRealtime()-started).put("pixels", pixels.summary()).put("result", "pass")
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
            reader?.let { closeReader(it, handler) }
            thread.quitSafely(); thread.join(2000)
            result.put("consumer_worker_stopped", !thread.isAlive)
            store.publishCheck(result)
        }
    }
    private class Pixels {
        private var count = 0
        private var nonUniform = 0
        private val hashes = mutableSetOf<String>()
        private val lowerHashes = mutableSetOf<String>()
        private var lowerNonUniform = 0
        @Synchronized fun add(image: android.media.Image) {
            val plane = image.planes[0]
            val bytes = plane.buffer
            val digest = MessageDigest.getInstance("SHA-256")
            val lowerDigest = MessageDigest.getInstance("SHA-256")
            val first = bytes.getInt(0)
            var varied = false
            var lowerVaried = false
            for (y in 0 until image.height step 4) for (x in 0 until image.width step 4) {
                val pixel = bytes.getInt(y*plane.rowStride+x*plane.pixelStride)
                if (pixel != first) varied = true
                for (shift in 0..24 step 8) digest.update((pixel ushr shift).toByte())
                if (y >= image.height*3/4) {
                    if (pixel != first) lowerVaried = true
                    for (shift in 0..24 step 8) lowerDigest.update((pixel ushr shift).toByte())
                }
            }
            count++; if (varied) nonUniform++
            if (hashes.size < 256) hashes.add(digest.digest().joinToString("") { "%02x".format(it.toInt() and 255) })
            if (lowerVaried) lowerNonUniform++
            if (lowerHashes.size < 256) lowerHashes.add(lowerDigest.digest().joinToString("") { "%02x".format(it.toInt() and 255) })
        }
        @Synchronized fun summary(): JSONObject = JSONObject().put("images", count).put("non_uniform_images", nonUniform)
            .put("distinct_images", hashes.size).put("sample_step", 4).put("hashes", JSONArray(hashes.toList()))
            .put("lower_distinct_images", lowerHashes.size).put("lower_non_uniform_images", lowerNonUniform).put("lower_hashes", JSONArray(lowerHashes.toList()))
    }
    private fun healthy(handle: Long): JSONObject {
        val value = runtime(handle)
        check(value.getString("state") == "Ready")
        check(value.getJSONObject("render").isNull("error"))
        value.optJSONObject("app")?.let { check(it.isNull("error")) }
        check(value.getJSONObject("transport").getString("state") != "Failed")
        return value
    }
    private fun runtime(handle: Long): JSONObject = JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("runtime")
    private fun closeReader(reader: ImageReader, handler: Handler) {
        val done = CountDownLatch(1)
        check(handler.post { try { reader.setOnImageAvailableListener(null, null); reader.close() } finally { done.countDown() } })
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
    private fun waitFor(milliseconds: Long = 20000, test: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime()+milliseconds
        while (!test()) { check(SystemClock.elapsedRealtime() < deadline); Thread.sleep(5) }
    }
}
