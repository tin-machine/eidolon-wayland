package io.github.tinmachine.eidolon

import android.content.Context
import android.graphics.PixelFormat
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.view.KeyEvent
import java.security.MessageDigest
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import org.json.JSONArray
import org.json.JSONObject

/** 実SSH/foot→SHM→Smithay→EGL→ImageReader。SDK/HMD可視性・物理入力は別に確認する。 */
object FootCase {
    fun run(nonce: String, context: Context, configuration: String, store: DiagnosticsStore, resizing: Boolean = false) {
        val result = JSONObject().put("case", if(resizing) "resize-native" else "foot").put("nonce", nonce).put("result", "running")
            .put("consumer", "ImageReader").put("synthetic_visibility", true)
        if(resizing) result.put("synthetic_input",true).put("physical_input_verified",false).put("text_verified",false)
        val resizeRows=JSONArray()
        var oldTarget:JSONObject?=null
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
                if(resizing && generation==2) {
                    stage="resize-recreated"
                    resizeRows.put(JSONObject().put("stage","recreated").put("runtime",resizedSnapshot(active,1280,720)))
                    send(active,command("resize_logical",80).put("target",checkNotNull(oldTarget)).put("width",640).put("height",480),"STALE")
                    resizedSnapshot(active,1280,720)
                    result.put("stale_resize_rejected",true)
                    stage="resize-input"
                    val baseline=healthy(active).getJSONObject("app").getJSONObject("input")
                    result.put("input_before",baseline)
                    val epoch=healthy(active).getLong("input_resets")
                    fun pointer(request:Long,event:JSONObject) {
                        val packet=JSONObject().put("source","777").put("uv",JSONArray(listOf(.53,.47))).put("event",event).put("time_ms",SystemClock.uptimeMillis() and 0xffffffffL)
                        send(active,command("pointer",request).put("target",target).put("input_epoch",epoch).put("pointer",packet))
                    }
                    pointer(81,JSONObject().put("type","buttons").put("changed",1).put("state",1))
                    pointer(82,JSONObject().put("type","buttons").put("changed",1).put("state",0))
                    waitFor { !healthy(active).getJSONObject("app").getJSONObject("input").isNull("focus_token") }
                    val focus=healthy(active).getJSONObject("app").getJSONObject("input").getString("focus_token")
                    for((index,key) in listOf(Triple("pressed",42,KeyEvent.KEYCODE_SHIFT_LEFT),Triple("pressed",30,KeyEvent.KEYCODE_A),Triple("released",30,KeyEvent.KEYCODE_A),Triple("released",42,KeyEvent.KEYCODE_SHIFT_LEFT),Triple("pressed",14,KeyEvent.KEYCODE_DEL),Triple("released",14,KeyEvent.KEYCODE_DEL)).withIndex()) {
                        val packet=JSONObject().put("device",Int.MAX_VALUE-10).put("action",key.first).put("scan_code",key.second).put("key_code",key.third).put("repeat",0).put("meta",0).put("time_ms",SystemClock.uptimeMillis() and 0xffffffffL)
                        send(active,command("key",90L+index).put("target",target).put("input_epoch",epoch).put("focus_token",focus).put("key",packet))
                    }
                    pointer(97,JSONObject().put("type","scroll").put("delta",JSONArray(listOf(0,24))).put("stop",JSONArray(listOf(false,false))))
                    pointer(98,JSONObject().put("type","scroll").put("delta",JSONArray(listOf(0,0))).put("stop",JSONArray(listOf(false,true))))
                    var inputState=healthy(active)
                    waitFor { inputState=healthy(active);val input=inputState.getJSONObject("app").getJSONObject("input");input.getLong("delivered")==baseline.getLong("delivered")+6 && input.getJSONObject("pointer").getLong("axis_frames")==baseline.getJSONObject("pointer").getLong("axis_frames")+2 && resized(inputState,1280,720) }
                    resizeRows.put(JSONObject().put("stage","input").put("runtime",inputState))
                    result.put("resize_cycles",resizeRows);store.publishCheck(result)
                }
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
                    if(resizing) {
                        stage="resize-first"
                        send(active,command("resize_logical",70).put("target",target).put("width",800).put("height",500))
                        resizeRows.put(JSONObject().put("stage","first").put("runtime",resizedSnapshot(active,800,500)))
                        oldTarget=JSONObject(target.toString())
                        // 同じqueueへ続けて入れ、detachとbuffer commitの順序を実測する。
                        val commands=listOf(command("resize_logical",71).put("target",target).put("width",1280).put("height",720),command("surface_lost",8).put("target",target))
                        commands.forEach { check(NativeBridge.nativeCommand(active,it.toString())==NativeBridge.ACCEPTED) }
                        val pending=commands.map { it.getString("request_id") }.toMutableSet()
                        waitFor {
                            val events=JSONObject(NativeBridge.nativePoll(active,64)).getJSONArray("events")
                            for(index in 0 until events.length()) { val event=events.getJSONObject(index);if(event.optString("type")=="command_complete" && event.optString("request_id") in pending) { check(event.getString("code")=="ACCEPTED");pending.remove(event.getString("request_id")) } }
                            pending.isEmpty()
                        }
                    } else send(active, command("surface_lost", 8).put("target", target))
                    var lostState=healthy(active)
                    waitFor {
                        lostState=healthy(active)
                        lostState.getJSONObject("render").getJSONArray("targets").length()==0 && (!resizing ||
                            lostState.getJSONObject("app").getJSONArray("windows").getJSONObject(0).getJSONObject("resize").optJSONObject("requested_logical_size")?.optInt("width")==1280)
                    }
                    if(resizing) {
                        resizeRows.put(JSONObject().put("stage","lost").put("runtime",lostState))
                        result.put("resize_cycles",resizeRows);store.publishCheck(result)
                    }
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
    internal class Pixels {
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
    internal fun closeReader(reader: ImageReader, handler: Handler) {
        val done = CountDownLatch(1)
        check(handler.post { try { reader.setOnImageAvailableListener(null, null); reader.close() } finally { done.countDown() } })
        check(done.await(3, TimeUnit.SECONDS))
    }
    private fun command(type: String, request: Long): JSONObject = JSONObject().put("schema", 1).put("type", type).put("request_id", request.toString())
    private fun send(handle: Long, command: JSONObject, expected: String = "ACCEPTED") {
        check(NativeBridge.nativeCommand(handle, command.toString()) == NativeBridge.ACCEPTED)
        var complete = false
        waitFor {
            val events = JSONObject(NativeBridge.nativePoll(handle, 64)).getJSONArray("events")
            for (index in 0 until events.length()) {
                val event = events.getJSONObject(index)
                if (event.optString("type") == "command_complete" && event.optString("request_id") == command.getString("request_id")) {
                    check(event.getString("code") == expected); complete = true
                }
            }
            complete
        }
    }
    private fun resized(state:JSONObject,width:Int,height:Int):Boolean {
        val window=state.getJSONObject("app").getJSONArray("windows").getJSONObject(0)
        val resize=window.getJSONObject("resize");val size=window.getJSONObject("logical_size")
        val targets=state.getJSONObject("render").getJSONArray("targets")
        return resize.getString("phase")=="applied" && size.getInt("width")==width && size.getInt("height")==height && targets.length()==1 &&
            targets.getJSONObject(0).optString("content_frame")==window.getString("frame")
    }
    private fun resizedSnapshot(handle:Long,width:Int,height:Int):JSONObject {
        var state=healthy(handle)
        waitFor { state=healthy(handle);resized(state,width,height) }
        return state
    }
    private fun waitFor(milliseconds: Long = 20000, test: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime()+milliseconds
        while (!test()) { check(SystemClock.elapsedRealtime() < deadline); Thread.sleep(5) }
    }
}
