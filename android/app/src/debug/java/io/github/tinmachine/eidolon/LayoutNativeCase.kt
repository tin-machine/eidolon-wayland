package io.github.tinmachine.eidolon

import android.content.Context
import android.graphics.PixelFormat
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.system.Os
import android.view.KeyEvent
import java.io.File
import java.security.MessageDigest
import org.json.JSONArray
import org.json.JSONObject

/** 専用noBackup fixtureだけを変更し、実footとCore保存/復元を検査する。SDK pose ackは合成と明記する。 */
object LayoutNativeCase {
    fun run(nonce:String,context:Context,configuration:String,store:DiagnosticsStore) { Check(nonce,context,configuration,store).run() }
    private data class Consumer(val plan:JSONObject,val reader:ImageReader,val pixels:FootCase.Pixels) {
        val target:JSONObject get()=plan.getJSONObject("target")
        val window:String get()=target.getString("window")
    }
    private class Check(val nonce:String,val context:Context,val configuration:String,val store:DiagnosticsStore) {
        val result=JSONObject().put("case","layout").put("nonce",nonce).put("result","running").put("consumer","ImageReader")
            .put("synthetic_visibility",true).put("synthetic_input",true).put("synthetic_pose_ack",true).put("synthetic_scene",true)
            .put("physical_input_verified",false).put("sdk_pose_verified",false).put("product_settings_untouched",true)
        val rows=JSONArray();val stops=JSONArray();val corruption=JSONArray()
        val thread=HandlerThread("eidolon-layout-consumer").apply { start() };val handler=Handler(thread.looper)
        val consumers=linkedMapOf<String,Consumer>()
        var handle=0L;var request=0L;var stage="create";var scene=1L;var runNumber=0
        lateinit var directory:File
        val settings:File get()=File(directory,"workspace/layout.json")
        fun command(type:String)=JSONObject().put("schema",1).put("type",type).put("request_id",(++request).toString())
        fun raw()=JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("runtime")
        fun state():JSONObject { val s=raw();check(s.getString("state")=="Ready" && s.isNull("error"));check(s.getJSONObject("render").isNull("error"));s.optJSONObject("app")?.let {check(it.isNull("error"))};check(s.getJSONObject("transport").getString("state")!="Failed");return s }
        fun send(packet:JSONObject,expected:String="ACCEPTED") {
            check(NativeBridge.nativeCommand(handle,packet.toString())==NativeBridge.ACCEPTED)
            waitFor {
                val events=JSONObject(NativeBridge.nativePoll(handle,64)).getJSONArray("events");var done=false
                for(i in 0 until events.length()) {val e=events.getJSONObject(i);if(e.optString("type")=="command_complete" && e.optString("request_id")==packet.getString("request_id")) {check(e.getString("code")==expected);done=true}}
                done
            }
        }
        fun create() {
            scene=1;runNumber++
            handle=NativeBridge.nativeCreate(JSONObject(configuration).put("preferences_dir",directory.absolutePath).toString());check(handle>0)
            waitFor {raw().getString("state")=="Ready"}
            send(command("set_activity_state").put("visible",true));send(command("set_xr_state").put("state","FOCUSED"));send(command("scene_ready").put("scene",scene.toString()))
        }
        fun windows(s:JSONObject=state())=s.getJSONObject("app").getJSONArray("windows").let {a -> (0 until a.length()).map {a.getJSONObject(it)}.filter {it.getString("life")=="Mapped"} }
        fun plans(s:JSONObject=state())=s.getJSONArray("panels").let {a -> (0 until a.length()).map {a.getJSONObject(it)} }
        fun placement(s:JSONObject,window:String):JSONObject {
            val values=s.getJSONObject("workspace").getJSONArray("windows")
            return (0 until values.length()).map {values.getJSONObject(it)}.first {it.getString("window")==window}.getJSONObject("placement")
        }
        fun same(a:JSONObject,b:JSONObject)=listOf("session","window","scene","generation").all {a.getString(it)==b.getString(it)}
        fun presented(s:JSONObject,c:Consumer):Boolean {
            val window=windows(s).firstOrNull {it.getString("window")==c.window} ?: return false
            val targets=s.getJSONObject("render").getJSONArray("targets")
            return (0 until targets.length()).any {i -> val t=targets.getJSONObject(i);same(t.getJSONObject("key"),c.target) && t.getLong("frames")>=3 && t.optString("content_frame")==window.getString("frame")}
        }
        fun connect():String {
            check(NativeBridge.nativeCommand(handle,command("connect").put("profile_id","termux-local").toString())==NativeBridge.ACCEPTED)
            val credentials=CredentialStore(context);var count=0
            waitFor(30000) {
                val events=JSONObject(NativeBridge.nativePoll(handle,64)).getJSONArray("events")
                for(i in 0 until events.length()) {val e=events.getJSONObject(i);if(e.optString("type")=="credential_required") {count++;check(credentials.provide(handle,e.getString("session").toLong(),e.getString("request_id").toLong())==NativeBridge.ACCEPTED)}}
                state().getJSONObject("transport").getString("state")=="Ready"
            }
            check(count==1);val session=state().getJSONObject("transport").getString("session")
            val token=MessageDigest.getInstance("SHA-256").digest("$nonce:$runNumber".toByteArray()).take(8).joinToString("") {"%02x".format(it.toInt() and 255)}
            send(command("start_app").put("session",session).put("nonce",token).put("mode","foot_fixture"))
            waitFor(60000) {state().optJSONObject("app")?.optJSONArray("windows")?.let {it.length()==1 && it.getJSONObject(0).optString("life")=="Mapped"}==true}
            return session
        }
        fun attach(window:String):Consumer {
            var plan=JSONObject()
            waitFor {plan=plans().firstOrNull {it.getString("action")=="create" && it.getJSONObject("target").getString("window")==window && it.getJSONObject("target").getString("scene")==scene.toString()} ?: JSONObject();plan.has("target")}
            val pixels=FootCase.Pixels();val reader=ImageReader.newInstance(1024,768,PixelFormat.RGBA_8888,3);val c=Consumer(plan,reader,pixels);consumers[window]=c
            reader.setOnImageAvailableListener({source ->source.acquireLatestImage()?.use {pixels.add(it)}},handler)
            val key=c.target;check(NativeBridge.nativeAttachWindowSurface(handle,key.getString("session").toLong(),window.toLong(),scene,key.getString("generation").toLong(),reader.surface,1024,768)==NativeBridge.ACCEPTED)
            waitFor {presented(state(),c) && pixels.summary().getInt("distinct_images")>=3}
            send(command("adapter_applied").put("target",key).put("action","create").put("command_seq",plan.getString("command_seq")))
            waitFor {plans().any {same(it.getJSONObject("target"),key) && it.optBoolean("applied")}}
            return c
        }
        fun retire(c:Consumer) {
            send(command("surface_lost").put("target",c.target))
            waitFor {state().getJSONObject("render").getJSONArray("targets").let {a -> (0 until a.length()).none {same(a.getJSONObject(it).getJSONObject("key"),c.target)}}}
            FootCase.closeReader(c.reader,handler);consumers.remove(c.window)
            var plan=JSONObject();waitFor {plan=plans().firstOrNull {same(it.getJSONObject("target"),c.target) && it.getString("action")=="destroy"} ?: JSONObject();plan.has("target")}
            send(command("adapter_applied").put("target",c.target).put("action","destroy").put("command_seq",plan.getString("command_seq")))
        }
        fun record(label:String):JSONObject {
            var s=state();waitFor {s=state();consumers.values.all {presented(s,it)}}
            rows.put(JSONObject().put("stage",label).put("runtime",s).put("pixels",JSONObject().also {p ->consumers.forEach {(w,c)->p.put(w,c.pixels.summary())}}));result.put("cycles",rows);store.publishCheck(result);return s
        }
        fun click(c:Consumer) {
            val epoch=state().getLong("input_resets")
            for(button in listOf(1,0)) send(command("pointer").put("target",c.target).put("input_epoch",epoch).put("pointer",JSONObject().put("source","779").put("uv",JSONArray(listOf(.5,.5))).put("time_ms",SystemClock.uptimeMillis() and 0xffffffffL).put("event",JSONObject().put("type","buttons").put("changed",1).put("state",button))))
        }
        fun key(c:Consumer,action:String,scan:Int,code:Int) {
            val s=state();send(command("key").put("target",c.target).put("input_epoch",s.getLong("input_resets")).put("focus_token",s.getJSONObject("app").getJSONObject("input").getString("focus_token"))
                .put("key",JSONObject().put("device",Int.MAX_VALUE-12).put("action",action).put("scan_code",scan).put("key_code",code).put("repeat",0).put("meta",0).put("time_ms",SystemClock.uptimeMillis() and 0xffffffffL)))
        }
        fun stop(label:String) {
            val started=SystemClock.elapsedRealtime();check(NativeBridge.nativeRequestStop(handle)==NativeBridge.ACCEPTED);val accepted=SystemClock.elapsedRealtime()-started
            check(accepted<100);waitFor {raw().getString("state")=="Stopped"};val after=raw()
            check(after.getJSONObject("transport").getJSONArray("pending_cleanup").length()==0)
            if(!after.isNull("app")) check(after.getJSONObject("app").getBoolean("directory_cleaned"))
            waitFor {NativeBridge.nativeDestroy(handle)==NativeBridge.ACCEPTED}
            stops.put(JSONObject().put("stage",label).put("runtime",after).put("accept_ms",accepted).put("stop_ms",SystemClock.elapsedRealtime()-started))
            result.put("stops",stops).put("last_handle",handle.toString());handle=0
            consumers.values.forEach {FootCase.closeReader(it.reader,handler)};consumers.clear()
        }
        fun run() {
            try {
                val parent=File(context.noBackupFilesDir.canonicalFile,"layout-fixtures");parent.mkdirs();Os.chmod(parent.absolutePath,448)
                directory=File(parent,nonce);check(directory.mkdir());Os.chmod(directory.absolutePath,448)
                check(directory.canonicalFile==directory.absoluteFile);store.publishCheck(result)
                create();val firstSession=connect();var first=attach(windows()[0].getString("window"));val window=first.window
                result.put("first_session",firstSession).put("window",window);record("before")
                stage="pose"
                val pose=JSONObject().put("position_m",JSONArray(listOf(.2,1.65,1.9))).put("rotation_xyzw",JSONArray(listOf(0.0,kotlin.math.sin(Math.PI/24),0.0,kotlin.math.cos(Math.PI/24))))
                val move=command("set_pose").put("target",first.target).put("pose",pose);val poseCommand=move.getString("request_id");send(move)
                waitFor {placement(state(),window).optString("pose_command")==poseCommand}
                check(placement(state(),window).isNull("applied_pose"))
                send(command("pose_applied").put("target",first.target).put("pose_command","9223372036854775807").put("pose",pose),"STALE")
                send(command("pose_applied").put("target",first.target).put("pose_command",poseCommand).put("pose",pose));record("pose")
                stage="size"
                val size=JSONObject().put("width_m",1.44).put("height_m",1.08)
                send(command("set_physical_size").put("target",first.target).put("size",size))
                send(command("resize_logical").put("target",first.target).put("width",800).put("height",500))
                waitFor {windows().first().getJSONObject("resize").optString("phase")=="applied" && windows().first().getJSONObject("logical_size").getInt("width")==800}
                record("size")
                stage="background"
                val configures=state().getJSONObject("app").getLong("configures")
                for(mode in listOf("virtual","passthrough","virtual")) {
                    val change=command("set_background").put("background",mode);val id=change.getString("request_id");send(change)
                    send(command("background_applied").put("scene","999").put("background_command",id).put("background",mode),"STALE")
                    send(command("background_applied").put("scene",scene.toString()).put("background_command",id).put("background",mode))
                    check(state().getJSONObject("transport").getString("session")==firstSession && state().getJSONObject("app").getLong("configures")==configures)
                    record("background_$mode")
                }
                stage="keyboard"
                click(first);key(first,"pressed",42,KeyEvent.KEYCODE_SHIFT_LEFT);check(state().getJSONObject("app").getJSONObject("input").getInt("pressed")==1)
                send(command("set_keyboard_layout").put("layout","jis"))
                waitFor {state().getJSONObject("app").getJSONObject("input").optString("layout")=="jis" && state().getJSONObject("app").getJSONObject("input").getInt("pressed")==0}
                click(first);key(first,"pressed",30,KeyEvent.KEYCODE_A);key(first,"released",30,KeyEvent.KEYCODE_A);record("keyboard")
                waitFor {val w=state().getJSONObject("workspace");!w.getBoolean("pending_save") && w.getJSONObject("storage").getInt("saved")>0}
                val saved=JSONObject(settings.readText());check(settings.length()<=65536);check(saved.getJSONArray("placements").length()==1)
                result.put("settings",saved).put("saved_pose",pose).put("saved_size",size)
                val digest=MessageDigest.getInstance("SHA-256").digest(settings.readBytes()).toList()
                stage="duplicate-app"
                send(command("open_terminal").put("session",firstSession));waitFor {windows().size==2}
                val second=attach(windows().first {it.getString("window")!=window}.getString("window"))
                waitFor {state().getJSONObject("workspace").getJSONArray("windows").let {a -> (0 until a.length()).all {a.getJSONObject(it).getBoolean("ambiguous")}}}
                send(command("set_pose").put("target",first.target).put("pose",PanelPoseCodec.nudge(pose,.1,0.0,0.0,0.0)))
                click(second);key(second,"pressed",30,KeyEvent.KEYCODE_A);key(second,"released",30,KeyEvent.KEYCODE_A)
                record("duplicate")
                check(MessageDigest.getInstance("SHA-256").digest(settings.readBytes()).toList()==digest)
                stop("first")
                stage="restore"
                File(directory,"workspace/.layout-interrupted").writeText("incomplete")
                create();check(state().getJSONObject("workspace").getJSONObject("storage").getBoolean("loaded"));val secondSession=connect();check(secondSession.toLong()>firstSession.toLong())
                first=attach(windows()[0].getString("window"));result.put("second_session",secondSession)
                waitFor {val w=windows()[0];w.getJSONObject("resize").optString("phase")=="applied" && w.getJSONObject("logical_size").getInt("width")==800 && PanelPoseCodec.near(placement(state(),first.window).getJSONObject("pose"),pose)}
                record("restored")
                stage="new-scene"
                val old=JSONObject(first.target.toString());scene=2;send(command("scene_ready").put("scene","2"));retire(first);first=attach(first.window)
                send(command("pose_applied").put("target",old).put("pose_command",JSONObject.NULL).put("pose",pose),"STALE")
                send(command("background_applied").put("scene","1").put("background_command",JSONObject.NULL).put("background","virtual"),"STALE")
                check(placement(state(),first.window).isNull("applied_pose"));check(state().getJSONObject("workspace").isNull("applied_background"))
                record("new_scene");stop("restored")
                stage="corrupt-settings"
                for((name,text) in listOf("unknown_schema" to JSONObject(saved.toString()).put("schema",999).toString(),"invalid_pose" to JSONObject(saved.toString()).also {it.getJSONArray("placements").getJSONObject(0).getJSONObject("pose").put("position_m",JSONArray(listOf(11,1,1)))}.toString(),"truncated" to "{broken")) {
                    settings.writeText(text);create();val s=state();check(s.getJSONObject("workspace").getString("background")=="passthrough");check(!s.getJSONObject("workspace").getJSONObject("storage").isNull("error"))
                    corruption.put(JSONObject().put("stage",name).put("workspace",s.getJSONObject("workspace")));stop(name)
                }
                result.put("corruption",corruption).put("result","pass")
            } catch(failure:Exception) {
                result.put("result","fail").put("stage",stage).put("failure_class",failure.javaClass.simpleName).put("failure_site",failure.stackTrace.firstOrNull()?.toString())
                if(handle>0) result.put("snapshot",JSONObject(NativeBridge.nativeSnapshot(handle)))
            } finally {
                if(handle>0) {NativeBridge.nativeRequestStop(handle);try {waitFor {NativeBridge.nativeDestroy(handle)!=NativeBridge.BUSY}} catch(_:Exception) {result.put("cleanup_failed",true)}}
                consumers.values.forEach {FootCase.closeReader(it.reader,handler)};consumers.clear();thread.quitSafely();thread.join(2000)
                result.put("consumer_worker_stopped",!thread.isAlive);store.publishCheck(result)
            }
        }
        fun waitFor(ms:Long=20000,test:()->Boolean) {val deadline=SystemClock.elapsedRealtime()+ms;while(!test()) {check(SystemClock.elapsedRealtime()<deadline);Thread.sleep(5)}}
    }
}
