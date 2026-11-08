package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.KeyEvent
import com.meta.spatial.core.Vector3
import com.meta.spatial.runtime.Scene
import com.meta.spatial.toolkit.getAbsoluteTransform
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicLong

/** First group gate: two owned foot windows. Native actions are not normal menu/physical acceptance. */
object WindowGroupsCase {
    private val ids=AtomicLong(9_600_000)
    @Volatile private var active:Check?=null
    fun start(nonce:String,adapter:SpatialPanelAdapter,scene:()->Scene,complete:(JSONObject)->Unit) {
        check(active==null);Check(nonce,adapter,scene,complete).also {active=it;it.ui.post(it)}
    }
    fun commandComplete(request:String,code:String) {active?.let {if(it.completions.containsKey(request)) it.completions[request]=code}}
    private class Check(val nonce:String,val adapter:SpatialPanelAdapter,val scene:()->Scene,val complete:(JSONObject)->Unit):Runnable {
        val ui=Handler(Looper.getMainLooper());val completions=ConcurrentHashMap<String,String>()
        val result=JSONObject().put("case","window-groups").put("nonce",nonce).put("fixture_revision","window-groups-v1-twofoot")
            .put("result","running").put("scope","fixed_native_sdk_group").put("consumer","SDK")
            .put("normal_menu_verified",false).put("physical_input_verified",false).put("hmd_verified",false).put("text_verified",false)
            .put("raycast","Scene.lineSegmentIntersect").put("single_wayland_client",true)
        val steps=JSONArray();val saved=linkedMapOf<String,JSONObject>();val sizes=linkedMapOf<String,JSONObject>()
        val overallDeadline=SystemClock.elapsedRealtime()+180000
        var stage="ready";var deadline=overallDeadline;var handle=0L;var sceneEpoch=""
        var first="";var second="";var firstKey=JSONObject();var secondKey=JSONObject();var revision=0L
        var request="";var focusRequest="";var collapseStarted=0L;var baseKeys=0L;var baseButtons=0L;var heldEpoch=0L
        val motion=JSONArray();var priorMotion:Vector3?=null
        var hiddenStart=0L;var backClock=0L;var backFrames=0L;var frontClock=0L
        var done=false
        fun rows(value:JSONArray)=(0 until value.length()).map {value.getJSONObject(it)}
        fun windows(s:JSONObject)=s.optJSONObject("app")?.optJSONArray("windows")?.let(::rows) ?: emptyList()
        fun same(a:JSONObject,b:JSONObject)=listOf("session","window","scene","generation").all {a.opt(it)==b.opt(it)}
        fun sdk(window:String)=rows(adapter.metrics().getJSONArray("targets")).singleOrNull {it.getJSONObject("target").optString("window")==window}
        fun native(s:JSONObject,window:String)=rows(s.getJSONObject("render").getJSONArray("targets")).singleOrNull {it.getJSONObject("key").optString("window")==window}
        fun group(s:JSONObject)=rows(s.getJSONObject("window_groups").getJSONArray("groups")).singleOrNull()
        fun workspace(s:JSONObject,window:String)=rows(s.getJSONObject("workspace").getJSONArray("windows")).single {it.getString("window")==window}.getJSONObject("placement")
        fun input(s:JSONObject)=s.getJSONObject("app").getJSONObject("input")
        fun advance(value:String,ms:Long=10000) {stage=value;deadline=minOf(overallDeadline,SystemClock.elapsedRealtime()+ms);publish()}
        fun publish() {result.put("stage",stage).put("steps",steps).put("command_completions",JSONObject(completions.toMap()));NativeSessionHost.publishCheck(result)}
        fun record(label:String,s:JSONObject) {
            check(steps.length()<16)
            val a=adapter.metrics()
            steps.put(JSONObject().put("stage",label).put("runtime",handle.toString()).put("scene",s.opt("scene"))
                .put("epoch",s.getLong("input_resets")).put("group",s.optJSONObject("window_groups"))
                .put("input",s.optJSONObject("app")?.optJSONObject("input")?.let {i->JSONObject().put("delivered",i.opt("delivered"))
                    .put("pressed",i.opt("pressed")).put("focus_window",i.opt("focus_window"))
                    .put("pointer_buttons",i.optJSONObject("pointer")?.opt("buttons"))} ?: JSONObject.NULL)
                .put("render_targets",JSONArray(rows(s.getJSONObject("render").getJSONArray("targets")).map {t->JSONObject()
                    .put("key",t.getJSONObject("key")).put("frames",t.getLong("frames")).put("content_frame",t.opt("content_frame"))
                    .put("presentation_paused",t.opt("presentation_paused")).put("player_clock_ms",t.opt("player_clock_ms"))}))
                .put("sdk_targets",JSONArray(rows(a.getJSONArray("targets")).map {t->JSONObject().put("target",t.getJSONObject("target"))
                    .put("group",t.opt("group")).put("input_collision",t.opt("input_collision"))
                    .put("controls",t.optJSONObject("controls")?.let {JSONObject().put("enabled",it.getBoolean("enabled")).put("visible",it.opt("visible"))})
                    .put("mode",t.optJSONObject("mode_badge")?.opt("mode"))}))
                .put("group_geometry_pending",a.opt("group_geometry_pending")).put("control_resources",a.getJSONObject("window_controls"))
                .put("mode_resources",a.getJSONObject("window_mode_badges")).put("menu_resources",a.getJSONObject("window_menus")))
            publish()
        }
        fun command(type:String,target:JSONObject,fields:JSONObject=JSONObject()):String {
            check(completions.size<32);val id=ids.incrementAndGet().toString();completions[id]="pending"
            val envelope=JSONObject().put("schema",1).put("type",type).put("request_id",id).put("target",target)
            for(name in fields.keys().asSequence())envelope.put(name,fields.get(name))
            check(NativeBridge.nativeCommand(handle,envelope.toString())==NativeBridge.ACCEPTED) {"GROUP_ENQUEUE_REJECTED"}
            return id
        }
        fun action(s:JSONObject,value:String,target:JSONObject):String {
            val g=group(s)
            return command("window_group",target,JSONObject().put("controls_revision",revision).put("action",value)
                .put("expected_group",g?.getString("id") ?: JSONObject.NULL).put("expected_revision",g?.getString("revision") ?: JSONObject.NULL))
        }
        fun ready(s:JSONObject,count:Int):Boolean {
            val appWindows=windows(s)
            val plans=rows(s.getJSONArray("panels"));val bindings=rows(adapter.metrics().getJSONArray("targets"))
            val global=JSONObject().put("lifecycle",s.optString("state")=="Ready" && s.optBoolean("activity_visible") && s.optString("xr_state")=="FOCUSED")
                .put("theme",s.getJSONObject("render").optString("theme_id")=="cyan-orbit")
                .put("window_count",appWindows.size==count).put("sdk_count",bindings.size==count)
            val targetGates=bindings.take(9).map {b->
                val key=b.getJSONObject("target");val name=key.getString("window")
                val window=appWindows.singleOrNull {it.getString("window")==name}
                val placement=window?.let {workspace(s,name)};val c=b.optJSONObject("controls")
                JSONObject().put("target",key).put("mapped",window?.optString("life")=="Mapped")
                    .put("panel",plans.any {it.optString("action")=="create" && same(it.getJSONObject("target"),key) && it.optBoolean("applied") && it.isNull("error")})
                    .put("frame",native(s,name)?.let {same(it.getJSONObject("key"),key) && it.getLong("frames")>0 &&
                        it.optString("content_frame").toLongOrNull()?.let {f->window?.optString("frame")?.toLongOrNull()?.let {p->f>=p}==true}==true}==true)
                    .put("pose",placement?.optJSONObject("applied_pose")?.let {same(it.getJSONObject("target"),key) && PanelPoseCodec.near(it.getJSONObject("pose"),placement.getJSONObject("pose"))}==true)
                    .put("physical",window?.getJSONObject("sizing")?.let {it.optJSONObject("applied_physical_size_m")?.toString()==it.getJSONObject("physical_size_m").toString()}==true)
                    .put("controls",c!=null && c.optBoolean("enabled") && c.optLong("failures")==0L &&
                        rows(c.getJSONArray("buttons")).let {it.size==4 && it.all {button->button.optBoolean("ready") && same(button.getJSONObject("target"),key)}})
                    .put("mode",b.optJSONObject("mode_badge")?.optBoolean("object_ready")==true)
                    .put("input_target",NativeSessionHost.inputTarget(key.toString())!=null)
            }
            val ready=global.keys().asSequence().all {global.getBoolean(it)} && targetGates.all {g->g.keys().asSequence().filter {it!="target"}.all {g.getBoolean(it)}}
            result.put("last_readiness",JSONObject().put("expected_count",count).put("global",global).put("targets",JSONArray(targetGates)).put("ready",ready))
            return ready
        }
        fun unchanged(s:JSONObject) {
            for((window,pose) in saved)check(PanelPoseCodec.near(workspace(s,window).getJSONObject("pose"),pose)) {"GROUP_PREVIEW_PERSISTED"}
            for((window,size) in sizes)check(windows(s).single {it.getString("window")==window}.getJSONObject("sizing").getJSONObject("physical_size_m").toString()==size.toString()) {"GROUP_SIZE_CHANGED"}
        }
        fun nearHit(window:String):com.meta.spatial.runtime.HitInfo? {
            val entity=adapter.windowEntity(window) ?: return null;val pose=getAbsoluteTransform(entity)
            val x=0f;val y=0f
            return scene().lineSegmentIntersect(pose*Vector3(x,y,-.005f),pose*Vector3(x,y,.005f))
        }
        fun moving(s:JSONObject) {
            val g=group(s) ?: return
            if(g.getString("phase")!="animating" || motion.length()>=2)return
            val entity=adapter.windowEntity(second) ?: return;val p=getAbsoluteTransform(entity);val hit=nearHit(second) ?: return
            if(hit.entity?.id!=entity.id)return
            val size=checkNotNull(sizes[second]);val local=Vector3(((hit.textureCoordinate.x-.5f)*size.getDouble("width_m")).toFloat(),
                ((.5f-hit.textureCoordinate.y)*size.getDouble("height_m")).toFloat(),0f)
            // Private defaults use the same rotation; UV + actual hit reconstructs the SDK quad centre.
            val centre=hit.point-(p*local-p.t)
            val begin=checkNotNull(saved[second]).getJSONArray("position_m");val anchor=checkNotNull(saved[first]).getJSONArray("position_m")
            fun distance(a:Vector3,b:JSONArray)=kotlin.math.sqrt((a.x-b.getDouble(0)).let {it*it}+(a.y-b.getDouble(1)).let {it*it}+(a.z-b.getDouble(2)).let {it*it})
            if(distance(centre,begin)<=.01 || distance(centre,anchor)<=.08)return
            if(priorMotion?.let {(centre-it).length()<=.01f}==true)return
            priorMotion=centre;motion.put(JSONObject().put("elapsed_ms",SystemClock.elapsedRealtime()-collapseStarted)
                .put("sdk_hit_centre",JSONArray(listOf(centre.x,centre.y,centre.z))))
        }
        fun completeAction(s:JSONObject,layout:String):Boolean {
            check(completions[request] in setOf("pending","ACCEPTED")) {"GROUP_COMMAND_REJECTED"}
            val completion=rows(s.getJSONObject("window_groups").getJSONArray("completions")).singleOrNull {it.getString("request")==request} ?: return false
            check(completion.getBoolean("completed") && completion.getString("layout")==layout) {"GROUP_ACTION_CANCELLED"}
            return completions[request]=="ACCEPTED"
        }
        fun collapsed(s:JSONObject,front:String,back:String):Boolean {
            val g=group(s) ?: return false
            if(g.getString("phase")!="stable" || g.getString("layout")!="collapsed" || !g.getBoolean("source_ready") || !completeAction(s,"collapsed"))return false
            val members=rows(g.getJSONArray("windows"));check(members.size==2 && members.all {it.getBoolean("geometry_applied")})
            check(members.single {it.getBoolean("input_allowed")}.getJSONObject("target").getString("window")==front)
            val f=sdk(front) ?: return false;val b=sdk(back) ?: return false
            return f.optJSONObject("group")?.optString("phase")=="stable" && b.optJSONObject("group")?.optString("phase")=="stable" &&
                f.getJSONObject("controls").getBoolean("enabled") && !b.getJSONObject("controls").getBoolean("enabled") &&
                !b.getJSONObject("controls").getBoolean("visible") && b.optString("input_collision")=="NoCollision" &&
                NativeSessionHost.inputTarget(f.getJSONObject("target").toString())!=null && NativeSessionHost.inputTarget(b.getJSONObject("target").toString())==null &&
                nearHit(front)?.entity?.id==adapter.windowEntity(front)?.id && nearHit(back)?.entity?.id!=adapter.windowEntity(back)?.id &&
                native(s,back)?.optBoolean("presentation_paused")==true && NativeSessionHost.windowGroupGeometryPending()==0
        }
        fun finish() {if(done)return;done=true;active=null;publish();complete(result)}
        override fun run() {
            if(done)return
            try {
                val now=SystemClock.elapsedRealtime();check(now<deadline) {"GROUP_CASE_TIMEOUT"}
                val current=NativeSessionHost.currentHandle()
                if(current<=0) {ui.postDelayed(this,16);return}
                val envelope=JSONObject(NativeBridge.nativeSnapshot(current));val s=envelope.optJSONObject("runtime")
                if(s==null) {check(stage=="stopped" && envelope.optInt("code")==NativeBridge.STALE);ui.postDelayed(this,16);return}
                check(s.getString("state")!="Failed") {"GROUP_RUNTIME_FAILED"}
                if(handle>0 && stage!="stopped") {
                    check(current==handle && s.getString("scene")==sceneEpoch && s.getBoolean("activity_visible") && s.getString("xr_state")=="FOCUSED") {"GROUP_OWNER_CHANGED"}
                    s.optJSONObject("app")?.let {check(it.isNull("error")) {"GROUP_APP_FAILED"}}
                    for(key in listOf(firstKey,secondKey).filter {it.length()>0}) {
                        check(native(s,key.getString("window"))?.getJSONObject("key")?.let {same(it,key)}==true) {"GROUP_CAPTURED_TARGET_LOST"}
                    }
                }
                when(stage) {
                    "ready" -> if(s.optString("state")=="Ready" && s.optBoolean("activity_visible") && s.optString("xr_state")=="FOCUSED") {
                        check(s.getJSONObject("transport").getString("state")=="Idle");handle=current;sceneEpoch=s.getString("scene")
                        NativeSessionHost.setTheme("builtin",false);NativeSessionHost.openTerminal();advance("first",120000)
                    }
                    "first" -> if(ready(s,1)) {
                        first=windows(s).single().getString("window");firstKey=JSONObject(checkNotNull(sdk(first)).getJSONObject("target").toString())
                        NativeSessionHost.openTerminal();advance("two",120000)
                    }
                    "two" -> if(ready(s,2)) {
                        second=windows(s).single {it.getString("window")!=first}.getString("window");secondKey=JSONObject(checkNotNull(sdk(second)).getJSONObject("target").toString())
                        revision=s.getJSONObject("render").getLong("theme_revision")
                        for(window in listOf(first,second)) {
                            saved[window]=JSONObject(workspace(s,window).getJSONObject("pose").toString())
                            sizes[window]=JSONObject(windows(s).single {it.getString("window")==window}.getJSONObject("sizing").getJSONObject("physical_size_m").toString())
                        }
                        check(saved[first]!!.getJSONArray("rotation_xyzw").toString()==saved[second]!!.getJSONArray("rotation_xyzw").toString()) {"GROUP_ROTATION_PRECONDITION"}
                        baseKeys=input(s).getLong("delivered");baseButtons=input(s).getJSONObject("pointer").getLong("buttons")
                        check(input(s).getInt("pressed")==0 && input(s).getJSONObject("pointer").getInt("pressed")==0)
                        record("two_ready",s);request=action(s,"collapse",firstKey);collapseStarted=now;advance("collapse")
                    }
                    "collapse" -> {
                        unchanged(s);moving(s)
                        if(collapsed(s,first,second)) {
                            check(motion.length()==2) {"GROUP_INTERMEDIATE_GEOMETRY_UNOBSERVED"}
                            result.put("intermediate_geometry",motion).put("collapse_geometry_and_ack",true).put("workspace_unchanged",true)
                            record("collapsed",s);advance("clock")
                        }
                    }
                    "clock" -> {
                        unchanged(s);check(collapsed(s,first,second)) {"GROUP_COLLAPSED_STATE_LOST"}
                        val back=checkNotNull(native(s,second));val front=checkNotNull(native(s,first))
                        val b=(back.opt("player_clock_ms") as? Number)?.toLong() ?: error("GROUP_CLOCK_UNAVAILABLE")
                        val f=(front.opt("player_clock_ms") as? Number)?.toLong() ?: error("GROUP_CLOCK_UNAVAILABLE")
                        val frames=back.getLong("frames")
                        if(hiddenStart==0L || b!=backClock || frames!=backFrames) {hiddenStart=now;backClock=b;backFrames=frames;frontClock=f}
                        else if(now-hiddenStart>=200) {
                            check(f>frontClock) {"GROUP_FRONT_CLOCK_STOPPED"}
                            result.put("back_clock_and_present_paused",true).put("front_clock_running",true);record("hidden_clock",s)
                            focusRequest=command("focus_window",firstKey,JSONObject().put("input_epoch",s.getLong("input_resets")));advance("focus")
                        }
                    }
                    "focus" -> if(completions[focusRequest]=="ACCEPTED" && input(s).optString("focus_window")==first && !input(s).isNull("focus_token") && NativeSessionHost.keyboardFocusMatches(firstKey,input(s).getString("focus_token"))) {
                        check(input(s).getLong("delivered")==baseKeys && input(s).getJSONObject("pointer").getLong("buttons")==baseButtons)
                        command("key",firstKey,JSONObject().put("input_epoch",s.getLong("input_resets")).put("focus_token",input(s).getString("focus_token"))
                            .put("key",JSONObject().put("device",Int.MAX_VALUE-26).put("scan_code",42).put("key_code",KeyEvent.KEYCODE_SHIFT_LEFT)
                                .put("action","pressed").put("repeat",0).put("meta",0).put("time_ms",SystemClock.uptimeMillis() and 0xffffffffL)))
                        advance("held")
                    }
                    "held" -> if(input(s).getInt("pressed")==1 && input(s).getLong("delivered")==baseKeys+1) {
                        heldEpoch=s.getLong("input_resets");record("held_key",s);request=action(s,"next_front",firstKey);advance("front")
                    }
                    "front" -> {
                        unchanged(s)
                        if(collapsed(s,second,first)) {
                            check(input(s).getInt("pressed")==0 && input(s).getLong("delivered")==baseKeys+2 && s.getLong("input_resets")>heldEpoch) {"GROUP_HELD_KEY_NOT_RELEASED"}
                            result.put("front_switched",true).put("held_key_released",true);record("next_front",s)
                            request=action(s,"expand",secondKey);advance("expand")
                        }
                    }
                    "expand" -> if(rows(s.getJSONObject("window_groups").getJSONArray("groups")).isEmpty() && completeAction(s,"expanded") && ready(s,2)) {
                        unchanged(s)
                        for(window in listOf(first,second)) {
                            val actual=PanelPoseCodec.fromSdk(getAbsoluteTransform(checkNotNull(adapter.windowEntity(window))))
                            check(actual!=null && PanelPoseCodec.near(actual,checkNotNull(saved[window]))) {"GROUP_EXPAND_POSE_CHANGED"}
                            check(native(s,window)?.optBoolean("presentation_paused")==false)
                            check(native(s,window)?.optString("content_frame")?.toLongOrNull()?.let {it>=windows(s).single {row->row.getString("window")==window}.getString("frame").toLong()}==true)
                        }
                        result.put("expanded_originals",true).put("latest_frames_resumed",true);record("expanded",s)
                        NativeSessionHost.withStoppedRuntime({},{});advance("stopped",30000)
                    }
                    "stopped" -> if(current>handle && s.optString("state")=="Ready" && s.optBoolean("activity_visible") && s.optString("xr_state")=="FOCUSED" &&
                        s.getJSONObject("transport").getString("state")=="Idle" && rows(s.getJSONObject("render").getJSONArray("targets")).let {it.size==1 && it.single().optString("role")=="diagnostic" && it.single().getLong("frames")>=3}) {
                        check(JSONObject(NativeBridge.nativeSnapshot(handle)).getInt("code")==NativeBridge.STALE)
                        check(s.getJSONObject("transport").getJSONArray("pending_cleanup").length()==0 && s.getJSONObject("window_groups").getJSONArray("groups").length()==0)
                        val a=adapter.metrics();check(a.getLong("group_geometry_pending")==0L)
                        val controls=a.getJSONObject("window_controls");check(controls.getLong("entity_owners")==0L && controls.getLong("listener_owners")==0L)
                        for(name in listOf("entity_owners","object_owners","mesh_owners","material_owners","texture_owners","pixel_slots_in_use","cleanup_pending_owners"))check(a.getJSONObject("window_mode_badges").getLong(name)==0L)
                        for(name in listOf("owners","cleanup_pending_owners","entity_owners","listener_owners","object_owners","mesh_owners","material_owners","texture_owners"))check(a.getJSONObject("window_menus").getLong(name)==0L)
                        result.put("old_runtime_destroyed",true).put("all_sdk_owners_zero",true).put("group_ack_owners_zero",true).put("result","pass");record("stopped",s);finish();return
                    }
                }
                ui.postDelayed(this,16)
            } catch(failure:Exception) {
                result.put("result","fail").put("stage",stage).put("failure_class",failure.javaClass.simpleName)
                    .put("failure_reason",failure.message?.takeIf {it.matches(Regex("GROUP_[A-Z_]+"))} ?: "GROUP_CASE_FAILED")
                    .put("failure_line",failure.stackTrace.firstOrNull {it.className.startsWith("io.github.tinmachine.eidolon.WindowGroupsCase")}?.lineNumber ?: JSONObject.NULL)
                runCatching {val h=NativeSessionHost.currentHandle();if(h>0) JSONObject(NativeBridge.nativeSnapshot(h)).optJSONObject("runtime")?.let {record("failed",it)}}
                finish()
            }
        }
    }
}
