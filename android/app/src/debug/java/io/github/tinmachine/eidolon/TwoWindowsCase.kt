package io.github.tinmachine.eidolon

import android.content.Context
import android.graphics.PixelFormat
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.view.KeyEvent
import java.security.MessageDigest
import org.json.JSONArray
import org.json.JSONObject

/** 二つの実foot windowを独立ImageReaderへ表示する。SDK layer/HMD/物理入力は別受入れ。 */
object TwoWindowsCase {
    private data class Consumer(val plan:JSONObject,val reader:ImageReader,val pixels:FootCase.Pixels) {
        val target:JSONObject get()=plan.getJSONObject("target")
        val window:String get()=target.getString("window")
    }
    fun run(nonce:String,context:Context,configuration:String,store:DiagnosticsStore) {
        val result=JSONObject().put("case","two-windows").put("nonce",nonce).put("result","running")
            .put("consumer","ImageReader").put("synthetic_visibility",true).put("synthetic_input",true)
            .put("physical_input_verified",false).put("text_verified",false).put("sdk_layers_verified",false)
        val rows=JSONArray();val consumers=linkedMapOf<String,Consumer>()
        val thread=HandlerThread("eidolon-two-consumers").apply { start() };val handler=Handler(thread.looper)
        var handle=0L;var stage="create";var request=0L
        fun command(type:String)=JSONObject().put("schema",1).put("type",type).put("request_id",(++request).toString())
        fun state():JSONObject {
            val runtime=JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("runtime")
            check(runtime.getString("state")=="Ready" && runtime.isNull("error"))
            check(runtime.getJSONObject("render").isNull("error"))
            runtime.optJSONObject("app")?.let { check(it.isNull("error")) }
            check(runtime.getJSONObject("transport").getString("state")!="Failed")
            return runtime
        }
        fun send(packet:JSONObject,expected:String="ACCEPTED") {
            check(NativeBridge.nativeCommand(handle,packet.toString())==NativeBridge.ACCEPTED)
            waitFor {
                val events=JSONObject(NativeBridge.nativePoll(handle,64)).getJSONArray("events")
                var done=false
                for(i in 0 until events.length()) { val e=events.getJSONObject(i);if(e.optString("type")=="command_complete" && e.optString("request_id")==packet.getString("request_id")) { check(e.getString("code")==expected);done=true } }
                done
            }
        }
        fun windows(runtime:JSONObject)=runtime.getJSONObject("app").getJSONArray("windows").let { a -> (0 until a.length()).map { a.getJSONObject(it) }.filter { it.getString("life")=="Mapped" } }
        fun plans(runtime:JSONObject)=runtime.getJSONArray("panels").let { a -> (0 until a.length()).map { a.getJSONObject(it) } }
        fun same(a:JSONObject,b:JSONObject)=listOf("session","window","scene","generation").all { a.getString(it)==b.getString(it) }
        fun presented(runtime:JSONObject,c:Consumer):Boolean {
            val w=windows(runtime).firstOrNull { it.getString("window")==c.window } ?: return false
            val targets=runtime.getJSONObject("render").getJSONArray("targets")
            return (0 until targets.length()).any { i -> val t=targets.getJSONObject(i);same(t.getJSONObject("key"),c.target) && t.getLong("frames")>=3 && t.optString("content_frame")==w.getString("frame") }
        }
        fun attach(window:String):Consumer {
            var plan=JSONObject()
            waitFor { plan=plans(state()).firstOrNull { it.getString("action")=="create" && it.getJSONObject("target").getString("window")==window } ?: JSONObject();plan.has("target") }
            val pixels=FootCase.Pixels();val reader=ImageReader.newInstance(1024,768,PixelFormat.RGBA_8888,3)
            val consumer=Consumer(plan,reader,pixels);consumers[window]=consumer
            reader.setOnImageAvailableListener({ source -> source.acquireLatestImage()?.use { pixels.add(it) } },handler)
            val key=consumer.target
            check(NativeBridge.nativeAttachWindowSurface(handle,key.getString("session").toLong(),window.toLong(),key.getString("scene").toLong(),key.getString("generation").toLong(),reader.surface,1024,768)==NativeBridge.ACCEPTED)
            waitFor { presented(state(),consumer) && pixels.summary().getInt("distinct_images")>=3 }
            send(command("adapter_applied").put("target",key).put("command_seq",plan.getString("command_seq")).put("action","create"))
            waitFor { plans(state()).any { same(it.getJSONObject("target"),key) && it.optBoolean("applied") } }
            return consumer
        }
        fun retire(consumer:Consumer) {
            send(command("surface_lost").put("target",consumer.target))
            waitFor { state().getJSONObject("render").getJSONArray("targets").let { a -> (0 until a.length()).none { same(a.getJSONObject(it).getJSONObject("key"),consumer.target) } } }
            FootCase.closeReader(consumer.reader,handler);consumers.remove(consumer.window)
            var plan=JSONObject()
            waitFor { plan=plans(state()).firstOrNull { same(it.getJSONObject("target"),consumer.target) && it.getString("action")=="destroy" } ?: JSONObject();plan.has("target") }
            send(command("adapter_applied").put("target",consumer.target).put("command_seq",plan.getString("command_seq")).put("action","destroy"))
        }
        fun record(label:String):JSONObject {
            var runtime=state()
            waitFor { runtime=state();consumers.values.all { presented(runtime,it) } }
            rows.put(JSONObject().put("stage",label).put("runtime",runtime).put("pixels",JSONObject().also { p -> consumers.forEach { (w,c) -> p.put(w,c.pixels.summary()) } }))
            result.put("cycles",rows);store.publishCheck(result)
            return runtime
        }
        fun click(consumer:Consumer) {
            val epoch=state().getLong("input_resets")
            for((changed,buttons) in listOf(1 to 1,1 to 0)) {
                val pointer=JSONObject().put("source","778").put("uv",JSONArray(listOf(.5,.5))).put("time_ms",SystemClock.uptimeMillis() and 0xffffffffL)
                    .put("event",JSONObject().put("type","buttons").put("changed",changed).put("state",buttons))
                send(command("pointer").put("target",consumer.target).put("input_epoch",epoch).put("pointer",pointer))
            }
        }
        fun key(consumer:Consumer,action:String,scan:Int,code:Int) {
            val runtime=state();val input=runtime.getJSONObject("app").getJSONObject("input")
            val packet=JSONObject().put("device",Int.MAX_VALUE-11).put("action",action).put("scan_code",scan).put("key_code",code).put("repeat",0).put("meta",0).put("time_ms",SystemClock.uptimeMillis() and 0xffffffffL)
            send(command("key").put("target",consumer.target).put("input_epoch",runtime.getLong("input_resets")).put("focus_token",input.getString("focus_token")).put("key",packet))
        }
        try {
            store.publishCheck(result)
            handle=NativeBridge.nativeCreate(configuration);check(handle>0)
            waitFor { JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("runtime").getString("state")=="Ready" }
            send(command("set_activity_state").put("visible",true));send(command("set_xr_state").put("state","FOCUSED"));send(command("scene_ready").put("scene","1"))
            stage="connect"
            check(NativeBridge.nativeCommand(handle,command("connect").put("profile_id","termux-local").toString())==NativeBridge.ACCEPTED)
            val credentials=CredentialStore(context);var credentialRequests=0
            waitFor(30000) {
                val events=JSONObject(NativeBridge.nativePoll(handle,64)).getJSONArray("events")
                for(i in 0 until events.length()) { val e=events.getJSONObject(i);if(e.optString("type")=="credential_required") { credentialRequests++;check(credentials.provide(handle,e.getString("session").toLong(),e.getString("request_id").toLong())==NativeBridge.ACCEPTED) } }
                state().getJSONObject("transport").getString("state")=="Ready"
            }
            check(credentialRequests==1)
            val session=state().getJSONObject("transport").getString("session")
            val token=MessageDigest.getInstance("SHA-256").digest(nonce.toByteArray()).take(8).joinToString("") { "%02x".format(it.toInt() and 255) }
            result.put("session",session).put("token",token).put("credential_requests",credentialRequests)
            stage="first"
            val runningStarted=SystemClock.elapsedRealtime()
            send(command("start_app").put("session",session).put("nonce",token).put("mode","foot_fixture"))
            waitFor(60000) { state().optJSONObject("app")?.optJSONArray("windows")?.let { it.length()==1 && it.getJSONObject(0).optString("life")=="Mapped" }==true }
            var first=attach(windows(state())[0].getString("window"));val firstWindow=first.window
            stage="second"
            send(command("open_terminal").put("session",session))
            waitFor { windows(state()).size==2 }
            val second=attach(windows(state()).first { it.getString("window")!=firstWindow }.getString("window"))
            result.put("first",firstWindow).put("second",second.window)
            record("two")
            stage="one-surface-missing"
            send(command("resize_logical").put("target",first.target).put("width",1280).put("height",720))
            retire(first)
            val before=second.pixels.summary().getInt("distinct_images")
            click(second);key(second,"pressed",30,KeyEvent.KEYCODE_A);key(second,"released",30,KeyEvent.KEYCODE_A)
            waitFor { second.pixels.summary().getInt("distinct_images")>=before+5 }
            record("one_surface_missing")
            first=attach(firstWindow)
            waitFor { windows(state()).first { it.getString("window")==firstWindow }.getJSONObject("resize").getString("phase")=="applied" }
            record("resized")
            stage="third"
            send(command("open_terminal").put("session",session));waitFor { windows(state()).size==3 }
            val third=windows(state()).first { it.getString("window") !in setOf(firstWindow,second.window) }.getString("window")
            result.put("third",third)
            record("third_hidden")
            click(first);key(first,"pressed",42,KeyEvent.KEYCODE_SHIFT_LEFT)
            check(state().getJSONObject("app").getJSONObject("input").getInt("pressed")==1)
            stage="switch"
            send(command("show_window").put("session",session).put("scene","1").put("window",third).put("slot",0))
            waitFor { state().getJSONObject("app").getJSONObject("input").getInt("pressed")==0 }
            val stale=JSONObject(first.target.toString());retire(first)
            val thirdConsumer=attach(third)
            send(command("resize_logical").put("target",stale).put("width",640).put("height",480),"STALE")
            result.put("stale_resize_rejected",true)
            click(thirdConsumer);key(thirdConsumer,"pressed",30,KeyEvent.KEYCODE_A);key(thirdConsumer,"released",30,KeyEvent.KEYCODE_A)
            record("switched")
            stage="close-one"
            send(command("close_window").put("session",session).put("scene","1").put("window",third))
            waitFor { windows(state()).size==2 && windows(state()).none { it.getString("window")==third } }
            retire(thirdConsumer);first=attach(firstWindow)
            record("closed_one")
            stage="long-running"
            waitFor(80000) { state();SystemClock.elapsedRealtime()-runningStarted>=73000 }
            result.put("running_observed_ms",SystemClock.elapsedRealtime()-runningStarted)
            record("long_running")
            stage="stop"
            val started=SystemClock.elapsedRealtime();check(NativeBridge.nativeRequestStop(handle)==NativeBridge.ACCEPTED)
            val accepted=SystemClock.elapsedRealtime()-started;check(accepted<100)
            var after=JSONObject()
            waitFor { after=JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("runtime");after.getString("state")=="Stopped" }
            check(after.getJSONObject("app").getBoolean("directory_cleaned") && after.getJSONObject("transport").getJSONArray("pending_cleanup").length()==0)
            waitFor { NativeBridge.nativeDestroy(handle)==NativeBridge.ACCEPTED }
            result.put("after_stop",after).put("last_handle",handle.toString()).put("accept_ms",accepted).put("stop_ms",SystemClock.elapsedRealtime()-started).put("result","pass")
            handle=0
        } catch(failure:Exception) {
            result.put("result","fail").put("stage",stage).put("failure_class",failure.javaClass.simpleName).put("failure_site",failure.stackTrace.firstOrNull()?.toString())
            if(handle>0) result.put("snapshot",JSONObject(NativeBridge.nativeSnapshot(handle)))
        } finally {
            if(handle>0) { NativeBridge.nativeRequestStop(handle);try { waitFor { NativeBridge.nativeDestroy(handle)!=NativeBridge.BUSY } } catch(_:Exception) { result.put("cleanup_failed",true) } }
            consumers.values.forEach { FootCase.closeReader(it.reader,handler) };consumers.clear()
            thread.quitSafely();thread.join(2000);result.put("consumer_worker_stopped",!thread.isAlive)
            store.publishCheck(result)
        }
    }
    private fun waitFor(ms:Long=20000,test:()->Boolean) {
        val deadline=SystemClock.elapsedRealtime()+ms
        while(!test()) { check(SystemClock.elapsedRealtime()<deadline);Thread.sleep(5) }
    }
}
