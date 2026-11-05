package io.github.tinmachine.eidolon

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.Process
import android.os.SystemClock
import android.view.InputDevice
import android.view.KeyEvent
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Vector3
import com.meta.spatial.runtime.ButtonBits
import com.meta.spatial.runtime.Scene
import java.io.File
import java.security.MessageDigest
import org.json.JSONArray
import org.json.JSONObject

/** 通常Host/SDKで配置・二枚・実Activity再生成を検査し、終了後は製品設定へ戻す。 */
object LayoutSdkCase {
    private var active:Check?=null
    fun start(nonce:String,context:Context,adapter:SpatialPanelAdapter,scene:()->Scene,recreate:()->Unit,name:String="layout",complete:()->Unit) {
        check(active==null);Check(nonce,context,adapter,scene,recreate,name,complete).also {active=it;it.start()}
    }
    fun sceneReady(adapter:SpatialPanelAdapter,scene:()->Scene,recreate:()->Unit) {
        active?.let {it.adapter=adapter;it.scene=scene;it.recreate=recreate}
    }
    private class Check(val nonce:String,val context:Context,var adapter:SpatialPanelAdapter,var scene:()->Scene,var recreate:()->Unit,val name:String,val complete:()->Unit):Runnable {
        val themed=name=="theme-sdk"
        val ui=Handler(Looper.getMainLooper());val rows=JSONArray()
        val result=JSONObject().put("case",name).put("nonce",nonce).put("result","running").put("consumer","SDK")
            .put("uid",Process.myUid()).put("pid",Process.myPid()).put("text_verified",false).put("synthetic_scene",false).put("synthetic_visibility",false).put("synthetic_input",true).put("synthetic_pose_ack",false)
            .put("sdk_pose_verified",true).put("physical_input_verified",false).put("hmd_verified",false).put("background_optical_verified",false)
        var stage="ready";var deadline=SystemClock.elapsedRealtime()+60000
        var originalHandle=0L;var fixtureHandle=0L;var restartHandle=0L;var lastHandle=0L
        var firstSession="";var initialScene=0L;var first="";var second="";var keyTarget=JSONObject();var keys=0L
        var source:Entity?=null;var originalDigest="";var statsUntil=0L;var statsStarted=0L
        val pairRows=JSONArray();var third="";var firstKey=JSONObject();var secondKey=JSONObject();var secondFrames=0L;var stalePoseRequest:String?=null
        var firstRestoredFrames=0L
        var lastState:JSONObject?=null
        val performance=if(themed) ThemeSdkPerformance() else null
        fun start() {NativeSessionHost.publishCheck(result);ui.post(this)}
        fun settingsDigest():String {
            val digest=MessageDigest.getInstance("SHA-256")
            for(path in listOf("workspace/layout.json","themes/state.bin")) {
                val file=File(context.noBackupFilesDir,path)
                digest.update(path.toByteArray())
                if(file.exists()) {check(file.length()<=8*1024*1024+65536);digest.update(file.readBytes())}
                else digest.update("absent".toByteArray())
            }
            return digest.digest().joinToString("") {"%02x".format(it.toInt() and 255)}
        }
        fun state(handle:Long)=JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("runtime")
        fun items(array:JSONArray)= (0 until array.length()).map {array.getJSONObject(it)}
        fun windows(s:JSONObject)=s.getJSONObject("app").getJSONArray("windows").let {items(it)}
        fun workspaceWindow(s:JSONObject,window:String)=items(s.getJSONObject("workspace").getJSONArray("windows")).first {it.getString("window")==window}
        fun key(s:JSONObject,window:String)=items(s.getJSONArray("panels")).first {it.getString("action")=="create" && it.getJSONObject("target").getString("window")==window}.getJSONObject("target")
        fun same(a:JSONObject,b:JSONObject)=listOf("session","window","scene","generation").all {a.getString(it)==b.getString(it)}
        fun ready(s:JSONObject,count:Int,windowCount:Int=count,requireTheme:Boolean=themed,minFrames:Int=if(themed) 3 else 1):Boolean {
            val app=s.optJSONObject("app") ?: return false
            if(app.optString("state")!="running" || !app.isNull("error") || app.optString("mode")!="foot") return false
            val targets=items(s.getJSONObject("render").getJSONArray("targets"));val plans=items(s.getJSONArray("panels")).filterNot {BuildHooks.holdPanelCreation(it.getJSONObject("target").getString("window"))};val ws=s.getJSONObject("workspace")
            if(requireTheme && (s.getJSONObject("theme").optString("selection")!="builtin" || s.getJSONObject("theme").optBoolean("loading") ||
                s.getJSONObject("render").optString("theme_id")!="cyan-orbit" || targets.any {it.optLong("decoration_frames")<minFrames} || plans.any {it.isNull("decoration")})) return false
            if(windows(s).size!=windowCount || windows(s).any {it.optString("life")!="Mapped"} || targets.size!=count || plans.size!=count || adapter.metrics().getInt("bindings")!=count || adapter.metrics().getInt("listeners")!=count) return false
            return targets.all {t ->
                val plan=plans.firstOrNull {same(it.getJSONObject("target"),t.getJSONObject("key"))}
                val window=windows(s).firstOrNull {it.getString("window")==t.getJSONObject("key").getString("window")}
                val pose=items(ws.getJSONArray("windows")).firstOrNull {it.getString("window")==window?.optString("window")}?.getJSONObject("placement")
                // Nativeの新世代とHostの入力ticket公開は非同期。旧epoch/未公開ならclickを送らない。
                val input=NativeSessionHost.inputTarget(t.getJSONObject("key").toString())
                plan?.optBoolean("applied")==true && plan.optString("action")=="create" && plan.isNull("error") && t.getLong("frames")>=minFrames && t.optString("content_frame")==window?.optString("frame") &&
                    input?.runtime==NativeSessionHost.currentHandle() && input.epoch==s.getLong("input_resets") &&
                    pose?.optJSONObject("applied_pose")?.let {same(it.getJSONObject("target"),t.getJSONObject("key")) && PanelPoseCodec.near(it.getJSONObject("pose"),pose.getJSONObject("pose"))}==true && window?.getJSONObject("sizing")?.optJSONObject("applied_physical_size_m")!=null
            } && ws.optJSONObject("applied_background")?.optString("scene")==s.optString("scene")
        }
        fun record(label:String,s:JSONObject) {
            rows.put(JSONObject().put("stage",label).put("runtime",s).put("adapter",adapter.metrics()));result.put("cycles",rows);NativeSessionHost.publishCheck(result)
        }
        fun pairRecord(label:String,s:JSONObject) {
            pairRows.put(JSONObject().put("stage",label).put("runtime",s).put("adapter",adapter.metrics()));result.put("two_window_cycles",pairRows);NativeSessionHost.publishCheck(result)
        }
        fun choice(s:JSONObject,window:String)=NativeSessionHost.WindowChoice(s.getJSONObject("app").getString("session"),s.getString("scene"),window,null)
        fun frameCount(s:JSONObject,window:String)=items(s.getJSONObject("render").getJSONArray("targets")).first {it.getJSONObject("key").getString("window")==window}.getLong("frames")
        fun recreateActivity(s:JSONObject) {initialScene=s.getString("scene").toLong();cleanupSource();stage="recreated";deadline=SystemClock.elapsedRealtime()+60000;recreate()}
        fun cleanupSource() {
            source?.let {scene().onNoIntersectionInput(it,0,0);scene().resolveInput();it.destroy()};source=null;result.put("source_destroyed",true)
        }
        fun click(s:JSONObject,window:String) {
            val entity=checkNotNull(adapter.windowEntity(window));val pose=PanelPoseCodec.toSdk(workspaceWindow(s,window).getJSONObject("placement").getJSONObject("pose"))
            val size=windows(s).first {it.getString("window")==window}.getJSONObject("sizing").getJSONObject("physical_size_m")
            val x=(.03*size.getDouble("width_m")).toFloat();val y=(.03*size.getDouble("height_m")).toFloat()
            val world=pose*Vector3(x,y,0f);val start=pose*Vector3(.27f,.31f,-1.3f)
            val end=pose*Vector3(x+(x-.27f)*(.8f/1.3f),y+(y-.31f)*(.8f/1.3f),.8f)
            val hit=checkNotNull(scene().lineSegmentIntersect(start,end));check(hit.entity?.id==entity.id)
            check(kotlin.math.abs(hit.textureCoordinate.x-.53)<.005 && kotlin.math.abs(hit.textureCoordinate.y-.47)<.005)
            check(kotlin.math.abs(hit.point.x-world.x)<.0001 && kotlin.math.abs(hit.point.y-world.y)<.0001 && kotlin.math.abs(hit.point.z-world.z)<.0001)
            if(source==null) source=Entity.create()
            val trigger=ButtonBits.ButtonTriggerR;scene().onInput(hit,checkNotNull(source),trigger,trigger);scene().resolveInput();scene().onInput(hit,checkNotNull(source),trigger,0);scene().resolveInput()
            keyTarget=key(s,window);keys=s.getJSONObject("app").getJSONObject("input").getLong("delivered")
        }
        fun typeKey() {
            val now=SystemClock.uptimeMillis()
            for((action,code,scan) in listOf(Triple(KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_A,30),Triple(KeyEvent.ACTION_UP,KeyEvent.KEYCODE_A,30),Triple(KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_DEL,14),Triple(KeyEvent.ACTION_UP,KeyEvent.KEYCODE_DEL,14)))
                check(NativeSessionHost.sendPhysicalKey(KeyEvent(now,now,action,code,0,0,Int.MAX_VALUE-13,scan,0,InputDevice.SOURCE_KEYBOARD)))
        }
        fun focusReady(s:JSONObject):Boolean {
            val input=s.getJSONObject("app").getJSONObject("input")
            return input.optString("focus_window")==keyTarget.optString("window") && !input.isNull("focus_token") && NativeSessionHost.keyboardFocusMatches(keyTarget,input.getString("focus_token"))
        }
        fun beginProductRestore() {
            BuildHooks.holdPanel(null)
            cleanupSource();lastHandle=NativeSessionHost.currentHandle();stage="product";deadline=SystemClock.elapsedRealtime()+30000
            NativeSessionHost.withStoppedRuntime({ BuildHooks.preferencesFixture=null },{})
        }
        override fun run() {
            try {
                check(SystemClock.elapsedRealtime()<deadline) {"LAYOUT_SDK_TIMEOUT"}
                val current=NativeSessionHost.currentHandle()
                if(current>0) {
                    val s=state(current);lastState=s;check(s.getString("state")!="Failed")
                    s.optJSONObject("app")?.let {check(it.isNull("error"))}
                    val focused=s.getBoolean("activity_visible") && s.getString("xr_state")=="FOCUSED"
                    when(stage) {
                        "ready" -> if(focused && s.getString("state")=="Ready") {
                            check(s.getJSONObject("transport").getString("state")=="Idle");originalHandle=current;originalDigest=settingsDigest()
                            BuildHooks.preferencesFixture=nonce;stage="isolated";NativeSessionHost.withStoppedRuntime({},{} )
                        }
                        "isolated" -> if(current>originalHandle && focused && s.getString("state")=="Ready" && s.getJSONObject("render").getJSONArray("targets").let {it.length()==1 && it.getJSONObject(0).optString("role")=="diagnostic"}) {
                            fixtureHandle=current
                            if(themed) {NativeSessionHost.setTheme("builtin");stage="theme_selected"}
                            else {NativeSessionHost.openTerminal();stage="first"}
                        }
                        "theme_selected" -> if(current==fixtureHandle && focused && s.getJSONObject("theme").let {it.optString("selection")=="builtin" && !it.optBoolean("loading") && it.isNull("error")}) {
                            NativeSessionHost.openTerminal();stage="first"
                        }
                        "first" -> if(focused && current==fixtureHandle && ready(s,1)) {
                            first=windows(s)[0].getString("window");firstSession=s.getJSONObject("app").getString("session");record("before",s)
                            NativeSessionHost.nudgePose(.2,.1,.25,15.0);NativeSessionHost.setPhysicalSize(1.44,1.08);NativeSessionHost.resizeTerminal(800,500)
                            NativeSessionHost.setBackground("virtual");NativeSessionHost.setKeyboardLayout("jis");stage="changed"
                        }
                        "changed" -> if(focused && ready(s,1)) {
                            val ws=s.getJSONObject("workspace");val pose=workspaceWindow(s,first).getJSONObject("placement").getJSONObject("pose")
                            val p=pose.getJSONArray("position_m");val size=windows(s)[0].getJSONObject("sizing").getJSONObject("applied_physical_size_m")
                            if(kotlin.math.abs(p.getDouble(0)-.2)<.0001 && kotlin.math.abs(p.getDouble(1)-1.5)<.0001 && kotlin.math.abs(p.getDouble(2)-1.75)<.0001 && ws.getString("background")=="virtual" && ws.getString("keyboard_layout")=="jis" && !ws.getBoolean("pending_save") && kotlin.math.abs(size.getDouble("width_m")-1.44)<.0001 && windows(s)[0].getJSONObject("logical_size").getInt("width")==800) {
                                result.put("saved_pose",pose);click(s,first);stage="first_keys"
                            }
                        }
                        "first_keys" -> if(focused && focusReady(s)) {typeKey();stage="first_typed"}
                        "first_typed" -> if(s.getJSONObject("app").getJSONObject("input").getLong("delivered")>=keys+4 && ready(s,1)) {record("changed",s);NativeSessionHost.openTerminal();stage="two"}
                        "two" -> if(focused && ready(s,2)) {second=windows(s).first {it.getString("window")!=first}.getString("window");click(s,second);stage="second_keys"}
                        "second_keys" -> if(focused && focusReady(s)) {typeKey();stage="second_typed"}
                        "second_typed" -> if(s.getJSONObject("app").getJSONObject("input").getLong("delivered")>=keys+4 && ready(s,2)) {
                            if(themed) {cleanupSource();stage="performance";deadline=SystemClock.elapsedRealtime()+60000}
                            else {statsStarted=System.currentTimeMillis();statsUntil=SystemClock.elapsedRealtime()+2200;stage="two_stats"}
                        }
                        "performance" -> {
                            val measurement=checkNotNull(performance)
                            val finished=measurement.step(s) {value,decorated -> ready(value,2,requireTheme=decorated,minFrames=1)}
                            result.put("performance",measurement.rows).put("performance_phase",measurement.phase)
                                .put("performance_scope","SDK draw+swap wall time; process CPU/RSS includes SDK and probe; no GPU timer or HMD latency")
                            if(finished) {statsStarted=System.currentTimeMillis();statsUntil=SystemClock.elapsedRealtime()+2200;stage="two_stats"}
                        }
                        "two_stats" -> {
                            if(!focused) {statsUntil=0}
                            else if(statsUntil==0L) {statsStarted=System.currentTimeMillis();statsUntil=SystemClock.elapsedRealtime()+2200}
                            else if(SystemClock.elapsedRealtime()>=statsUntil && ready(s,2)) {
                                result.put("layer_window",JSONObject().put("start_wall_ms",statsStarted).put("end_wall_ms",System.currentTimeMillis()).put("scene",s.getString("scene")))
                                record("two",s)
                                if(name in setOf("two-windows","theme-sdk")) {
                                    pairRecord("pair",s);firstKey=JSONObject(key(s,first).toString());secondKey=JSONObject(key(s,second).toString());secondFrames=frameCount(s,second)
                                    BuildHooks.holdPanel(first);check(adapter.recreateWindow(first));result.put("surface_loss_trigger","sdk-entity-recreate");stage="pair_lost";deadline=SystemClock.elapsedRealtime()+60000
                                } else recreateActivity(s)
                            }
                        }
                        "pair_lost" -> if(focused && ready(s,1,2) && adapter.windowEntity(first)==null && same(key(s,second),secondKey)) {
                            pairRecord("first_lost",s);click(s,second);stage="pair_loss_keys"
                        }
                        "pair_loss_keys" -> if(focused && focusReady(s)) {typeKey();stage="pair_loss_typed"}
                        "pair_loss_typed" -> if(focused && ready(s,1,2) && s.getJSONObject("app").getJSONObject("input").getLong("delivered")>=keys+4 && frameCount(s,second)>secondFrames) {
                            pairRecord("loss_input",s);BuildHooks.holdPanel(null);stage="pair_restored"
                        }
                        "pair_restored" -> if(focused && ready(s,2) && key(s,first).getString("generation").toLong()>firstKey.getString("generation").toLong()) {
                            check(same(key(s,second),secondKey));pairRecord("first_restored",s)
                            firstRestoredFrames=frameCount(s,first)
                            stalePoseRequest=NativeSessionHost.submitPose(firstKey,workspaceWindow(s,first).getJSONObject("placement").getJSONObject("pose"),current);check(stalePoseRequest!=null);stage="pair_stale"
                        }
                        "pair_stale" -> stalePoseRequest?.let {request -> NativeSessionHost.poseCompletion(request)?.let {status ->
                            if(status!="pending") {
                                check(status=="STALE");NativeSessionHost.forgetPose(request);stalePoseRequest=null;result.put("old_surface_target_rejected",true);NativeSessionHost.openTerminal();stage="pair_third"
                            }
                        } }
                        "pair_third" -> if(focused && ready(s,2,3)) {
                            third=windows(s).first {it.getString("window")!=first && it.getString("window")!=second}.getString("window")
                            check(adapter.windowEntity(third)==null);pairRecord("third_hidden",s);click(s,second);stage="pair_shift"
                        }
                        "pair_shift" -> if(focused && focusReady(s)) {
                            val now=SystemClock.uptimeMillis();check(NativeSessionHost.sendPhysicalKey(KeyEvent(now,now,KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_SHIFT_LEFT,0,0,Int.MAX_VALUE-13,42,0,InputDevice.SOURCE_KEYBOARD)));stage="pair_shift_held"
                        }
                        "pair_shift_held" -> if(focused && ready(s,2,3) && s.getJSONObject("app").getJSONObject("input").getInt("pressed")==1) {
                            pairRecord("shift_held",s);result.put("shift_held_before_switch",true);NativeSessionHost.showWindow(choice(s,third),1);stage="pair_switched"
                        }
                        "pair_switched" -> if(focused && ready(s,2,3) && adapter.windowEntity(third)!=null && adapter.windowEntity(second)==null && s.getJSONObject("app").getJSONObject("input").getInt("pressed")==0) {
                            pairRecord("switched",s);click(s,third);stage="pair_third_keys"
                        }
                        "pair_third_keys" -> if(focused && focusReady(s)) {typeKey();stage="pair_third_typed"}
                        "pair_third_typed" -> if(focused && ready(s,2,3) && s.getJSONObject("app").getJSONObject("input").getLong("delivered")>=keys+4) {
                            pairRecord("third_input",s);NativeSessionHost.closeWindow(choice(s,third));stage="pair_closed"
                        }
                        "pair_closed" -> if(focused && s.getJSONObject("render").getJSONArray("targets").length() in 1..2 && ready(s,s.getJSONObject("render").getJSONArray("targets").length(),2) && windows(s).none {it.getString("window")==third} && adapter.windowEntity(third)==null && items(s.getJSONObject("render").getJSONArray("targets")).none {it.getJSONObject("key").getString("window")==third}) {
                            click(s,first);stage="pair_close_keys"
                        }
                        "pair_close_keys" -> if(focused && focusReady(s)) {typeKey();stage="pair_close_typed"}
                        "pair_close_typed" -> if(focused && ready(s,s.getJSONObject("render").getJSONArray("targets").length(),2) &&
                            s.getJSONObject("app").getJSONObject("input").getLong("delivered")>=keys+4 && frameCount(s,first)>firstRestoredFrames) {
                            pairRecord("third_closed",s);NativeSessionHost.showWindow(choice(s,second),1);stage="pair_final"
                        }
                        "pair_final" -> if(focused && ready(s,2) && adapter.windowEntity(first)!=null && adapter.windowEntity(second)!=null) {
                            pairRecord("pair_restored",s);result.put("source_hold_released",true);recreateActivity(s)
                        }
                        "recreated" -> if(current==fixtureHandle && focused && s.getString("scene").toLong()>initialScene && ready(s,2)) {
                            check(s.getJSONObject("app").getString("session")==firstSession)
                            check(!NativeSessionHost.setXrState("IDLE",initialScene) && !NativeSessionHost.setActivityVisible(false,initialScene))
                            result.put("synthetic_stale_callback",true);record("recreated",s);result.put("activity_recreated",true)
                            restartHandle=current;stage="restarted";NativeSessionHost.withStoppedRuntime({},{} )
                        }
                        "restarted" -> if(current>restartHandle && focused && s.getString("state")=="Ready" && s.getJSONObject("transport").getString("state")=="Idle") {
                            restartHandle=current;check(s.getJSONObject("workspace").getJSONObject("storage").getBoolean("loaded"));NativeSessionHost.openTerminal();stage="restored"
                        }
                        "restored" -> if(current==restartHandle && focused && ready(s,1)) {
                            check(s.getJSONObject("app").getString("session").toLong()>firstSession.toLong())
                            check(PanelPoseCodec.near(workspaceWindow(s,windows(s)[0].getString("window")).getJSONObject("placement").getJSONObject("pose"),result.getJSONObject("saved_pose")))
                            check(s.getJSONObject("workspace").getString("background")=="virtual" && s.getJSONObject("workspace").getString("keyboard_layout")=="jis")
                            check(windows(s)[0].getJSONObject("logical_size").getInt("width")==800);record("restored",s);beginProductRestore()
                        }
                        "product" -> if(current>lastHandle && focused && s.getString("state")=="Ready" && s.getJSONObject("transport").getString("state")=="Idle" && s.getJSONObject("render").getJSONArray("targets").let {it.length()==1 && it.getJSONObject(0).getLong("frames")>=3 && it.getJSONObject(0).optString("role")=="diagnostic"}) {
                            check(s.getJSONObject("transport").getJSONArray("pending_cleanup").length()==0 && originalDigest==settingsDigest())
                            result.put("product_settings_untouched",true).put("product_restored",s).put("original_handle",originalHandle.toString()).put("fixture_handle",fixtureHandle.toString()).put("last_handle",lastHandle.toString()).put("new_handle",current.toString()).put("result","pass")
                            NativeSessionHost.publishCheck(result);active=null;complete();return
                        }
                    }
                }
                if(result.optString("stage")!=stage) {result.put("stage",stage);NativeSessionHost.publishCheck(result)}
                ui.postDelayed(this,32)
            } catch(failure:Exception) {
                BuildHooks.holdPanel(null);stalePoseRequest?.let {NativeSessionHost.forgetPose(it)}
                result.put("result","fail").put("stage",stage).put("failure_class",failure.javaClass.simpleName).put("failure_site",failure.stackTrace.firstOrNull()?.toString())
                    .put("failure_runtime",lastState).put("failure_adapter",adapter.metrics())
                val copy=result.toString();BuildHooks.preferencesFixture=null
                runCatching {cleanupSource()};NativeSessionHost.withStoppedRuntime({it.publishCheck(JSONObject(copy))},{active=null;complete()})
            }
        }
    }
}
