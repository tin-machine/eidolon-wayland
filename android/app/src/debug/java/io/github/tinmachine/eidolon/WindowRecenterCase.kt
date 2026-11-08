package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.InputDevice
import android.view.KeyEvent
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Pose
import com.meta.spatial.core.Vector3
import com.meta.spatial.runtime.ButtonBits
import com.meta.spatial.runtime.HitInfo
import com.meta.spatial.runtime.Scene
import com.meta.spatial.toolkit.getAbsoluteTransform
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap

/** One fixture-owned foot: actual SDK menu hit, one recenter, ACK/geometry/raw return, retirement. */
object WindowRecenterCase {
    @Volatile private var active:Check?=null
    fun start(nonce:String,adapter:SpatialPanelAdapter,scene:()->Scene,complete:(JSONObject)->Unit) {
        check(active==null);Check(nonce,adapter,scene,complete).also {active=it;it.start()}
    }
    fun requested(action:String,runtime:Long,target:String,revision:Long,request:String,code:Int) {
        active?.let {if(runtime==it.handle && it.requests.size<32) it.requests[request]=JSONObject()
            .put("action",action).put("runtime",runtime.toString()).put("target",JSONObject(target))
            .put("revision",revision).put("request_id",request).put("enqueue_code",code).toString()}
    }
    fun commandComplete(request:String,code:String) {active?.let {if(it.requests.containsKey(request)) it.completions[request]=code}}
    private class Check(val nonce:String,val adapter:SpatialPanelAdapter,val scene:()->Scene,val complete:(JSONObject)->Unit):Runnable {
        val ui=Handler(Looper.getMainLooper())
        val requests=ConcurrentHashMap<String,String>();val completions=ConcurrentHashMap<String,String>()
        val result=JSONObject().put("case","window-recenter").put("nonce",nonce).put("fixture_revision","window-recenter-v1-single")
            .put("result","running").put("consumer","SDK").put("synthetic_input",true).put("synthetic_visibility",false)
            .put("physical_input_verified",false).put("hmd_verified",false).put("tracking_freshness","not_exposed")
            .put("multi_window_overlap_verified",false).put("fixture_viewer_pose_logged",false)
            .put("dispatch","Scene.onInput").put("raycast","Scene.lineSegmentIntersect")
        val steps=JSONArray()
        @Volatile var handle=0L
        var stage="ready";var deadline=SystemClock.elapsedRealtime()+120000
        var target=JSONObject();var window="";var revision=0L
        var source:Entity?=null;var prepareRequest:String?=null;var prepareAccepted=false
        val preparation=WindowMotionPose.at(.65,2.0,2.2)
        var baseline:WindowRecenterGeometry?=null;var menuEpoch=0L;var heldEpoch=0L;var keys=0L;var buttons=0L
        var priorRequests=setOf<String>();var recenterStart=setOf<String>()
        var recentMode=false;var finished=false
        fun start() {publish();ui.post(this)}
        fun items(a:JSONArray?)=if(a==null) emptyList() else (0 until a.length()).map {a.getJSONObject(it)}
        fun windows(s:JSONObject)=items(s.optJSONObject("app")?.optJSONArray("windows"))
        fun row()=items(adapter.metrics().optJSONArray("targets")).singleOrNull {WindowRecenterWire.sameTarget(it.getJSONObject("target"),target)}
        fun menu()=row()?.optJSONObject("menu")
        fun pose():Pose=getAbsoluteTransform(checkNotNull(adapter.windowEntity(window)))
        fun ready(s:JSONObject):Boolean {
            if(s.optString("state")!="Ready" || !s.optBoolean("activity_visible") || s.optString("xr_state")!="FOCUSED" || windows(s).size!=1) return false
            val rows=items(adapter.metrics().optJSONArray("targets"));if(rows.size!=1) return false
            val r=rows.single();val key=r.getJSONObject("target");val c=r.optJSONObject("controls") ?: return false
            val rendered=items(s.getJSONObject("render").optJSONArray("targets"))
            return r.optJSONObject("mode_badge")?.optBoolean("object_ready")==true && c.optBoolean("enabled") && c.optLong("failures")==0L &&
                c.optLong("revision")==s.getJSONObject("render").getLong("theme_revision") && c.optInt("entities")==12 &&
                items(c.optJSONArray("buttons")).let {it.size==4 && it.all {b->b.optBoolean("ready") && WindowRecenterWire.sameTarget(b.getJSONObject("target"),key)}} &&
                rendered.any {WindowRecenterWire.sameTarget(it.getJSONObject("key"),key) && it.optLong("frames")>0}
        }
        fun focused(s:JSONObject):Boolean {
            val input=s.getJSONObject("app").getJSONObject("input")
            return input.optString("focus_window")==window && !input.isNull("focus_token") &&
                NativeSessionHost.keyboardFocusMatches(target,input.getString("focus_token"))
        }
        fun ray(p:Pose,local:Vector3):Pair<Vector3,Vector3> {
            val a=Vector3(.27f,.31f,-1.3f);val t=(.8+1.3)/(local.z+1.3)
            return p*a to p*Vector3((a.x+(local.x-a.x)*t).toFloat(),(a.y+(local.y-a.y)*t).toFloat(),.8f)
        }
        fun hit(action:String,inMenu:Boolean=false):HitInfo {
            val values=if(inMenu) checkNotNull(menu()).getJSONObject("view").getJSONArray("button_targets") else checkNotNull(row()).getJSONObject("controls").getJSONArray("buttons")
            val button=items(values).single {it.getString("action")==action};check(button.getBoolean("ready"))
            if(inMenu) check(button.getBoolean("enabled"))
            val entity=Entity(button.getString("entity").toLong());val p=pose();val segment=ray(p,p.inverse()*getAbsoluteTransform(entity).t)
            return checkNotNull(scene().lineSegmentIntersect(segment.first,segment.second)).also {check(it.entity?.id==entity.id) {"RECENTER_WRONG_HIT"}}
        }
        fun click(action:String,inMenu:Boolean=false) {
            priorRequests=requests.keys.toSet();val h=hit(action,inMenu)
            for(down in listOf(true,false)) {
                scene().onInput(h,checkNotNull(source),ButtonBits.ButtonTriggerR,if(down) ButtonBits.ButtonTriggerR else 0);scene().resolveInput()
            }
        }
        fun accepted(action:String,after:Set<String> = priorRequests):String? {
            val matching=requests.filter {(id,raw)->id !in after && JSONObject(raw).let {it.getString("action")==action &&
                it.getLong("revision")==revision && WindowRecenterWire.sameTarget(it.getJSONObject("target"),target)}}
            check(matching.size<=1) {"RECENTER_DUPLICATE_REQUEST"}
            val (id,raw)=matching.entries.singleOrNull()?.let {it.key to it.value} ?: return null
            check(JSONObject(raw).getInt("enqueue_code")==NativeBridge.ACCEPTED) {"RECENTER_ENQUEUE_REJECTED"}
            val code=completions[id] ?: return null;check(code=="ACCEPTED") {"RECENTER_COMMAND_REJECTED"};return id
        }
        fun record(label:String,s:JSONObject) {
            val input=s.optJSONObject("app")?.optJSONObject("input")
            steps.put(JSONObject().put("stage",label).put("runtime",handle.toString()).put("target",target)
                .put("epoch",s.optLong("input_resets")).put("keys",input?.optLong("delivered") ?: JSONObject.NULL)
                .put("pressed",input?.optInt("pressed") ?: JSONObject.NULL).put("pointer_buttons",input?.optJSONObject("pointer")?.optLong("buttons") ?: JSONObject.NULL)
                .put("menu_phase",menu()?.optString("phase") ?: JSONObject.NULL).put("menu_reason",menu()?.optString("reason") ?: JSONObject.NULL)
                .put("recenter_read_reason",menu()?.opt("recenter_read_reason") ?: JSONObject.NULL))
            publish()
        }
        fun publish() {
            result.put("stage",stage).put("steps",steps).put("requests",JSONArray(requests.values.map(::JSONObject).sortedBy {it.getString("request_id").toLong()}
                .map {it.put("completion",completions[it.getString("request_id")] ?: JSONObject.NULL)}))
            NativeSessionHost.publishCheck(result)
        }
        fun menusGone():Boolean=adapter.metrics().getJSONObject("window_menus").let {m->
            listOf("owners","cleanup_pending_owners","entity_owners","listener_owners","object_owners","mesh_owners","material_owners","texture_owners").all {m.getLong(it)==0L}}
        fun cleanup() {
            prepareRequest?.let(NativeSessionHost::forgetPose)
            val e=source;source=null
            try {e?.let {scene().onNoIntersectionInput(it,0,0);scene().resolveInput()}} finally {e?.destroy()}
            result.put("source_destroyed",true)
        }
        fun finish() {if(finished)return;finished=true;active=null;publish();complete(result)}
        override fun run() {
            try {
                check(SystemClock.elapsedRealtime()<deadline) {"RECENTER_TIMEOUT"}
                val current=NativeSessionHost.currentHandle()
                if(current>0) {
                    val envelope=JSONObject(NativeBridge.nativeSnapshot(current));val s=envelope.optJSONObject("runtime")
                    if(s==null) {check(stage=="stopped" && envelope.optInt("code")==NativeBridge.STALE);ui.postDelayed(this,32);return}
                    check(s.getString("state")!="Failed");s.optJSONObject("app")?.let {check(it.isNull("error"))}
                    if(handle>0 && stage!="stopped") check(current==handle) {"RECENTER_RUNTIME_CHANGED"}
                    if(window.isNotEmpty() && stage !in setOf("closed","stopped")) {
                        check(s.getString("scene")==target.getString("scene") && s.getJSONObject("render").getLong("theme_revision")==revision)
                        check(windows(s).size==1 && windows(s).single().getString("window")==window)
                        row()?.getJSONObject("mode_badge")?.let {if(it.optString("mode")=="RECENTERING") recentMode=true}
                    }
                    val input=s.optJSONObject("app")?.optJSONObject("input")
                    when(stage) {
                        "ready" -> if(s.optString("state")=="Ready" && s.optBoolean("activity_visible") && s.optString("xr_state")=="FOCUSED") {
                            check(s.getJSONObject("transport").getString("state")=="Idle");handle=current;NativeSessionHost.openTerminal();stage="first"
                        }
                        "first" -> if(ready(s)) {
                            val candidate=JSONObject(items(adapter.metrics().getJSONArray("targets")).single().getJSONObject("target").toString())
                            val geometry=WindowRecenterWire.read(s,candidate)
                            // Initial panel creation and pose ACK are independent. Wait; do not treat readiness lag as a bad operation.
                            if(geometry!=null && WindowRecenterWire.applied(s,candidate,geometry.poseCommand,geometry.pose) && NativeSessionHost.inputTarget(candidate.toString())!=null) {
                                target=candidate;window=target.getString("window");revision=s.getJSONObject("render").getLong("theme_revision");source=Entity.create()
                                prepareRequest=checkNotNull(NativeSessionHost.submitPose(target,JSONObject().put("position_m",JSONArray(preparation.p)).put("rotation_xyzw",JSONArray(preparation.q)),handle))
                                stage="preparing"
                            }
                        }
                        "preparing" -> {
                            NativeSessionHost.poseCompletion(checkNotNull(prepareRequest))?.takeIf {it!="pending"}?.let {check(it=="ACCEPTED");prepareAccepted=true}
                            if(prepareAccepted && ready(s) && WindowRecenterWire.applied(s,target,prepareRequest,preparation) && WindowMoveController.motion(pose())?.near(preparation)==true) {
                                result.put("fixture_preposition_applied",true);record("preposition_applied",s);click("focus");stage="focused"
                            }
                        }
                        "focused" -> if(focused(s) && accepted("focus")!=null && ready(s)) {
                            keys=checkNotNull(input).getLong("delivered");buttons=input.getJSONObject("pointer").getLong("buttons")
                            val now=SystemClock.uptimeMillis()
                            check(NativeSessionHost.sendPhysicalKey(KeyEvent(now,now,KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_SHIFT_LEFT,0,0,Int.MAX_VALUE-14,42,0,InputDevice.SOURCE_KEYBOARD)))
                            stage="held"
                        }
                        "held" -> if(checkNotNull(input).getLong("delivered")==keys+1 && input.getInt("pressed")==1) {
                            heldEpoch=s.getLong("input_resets");click("menu");stage="menu"
                        }
                        "menu" -> if(menu()?.optString("phase")=="MENU") {
                            val view=checkNotNull(menu()).getJSONObject("view")
                            if(view.optInt("listeners_ready")==4 && view.optBoolean("label_ready") && view.getJSONArray("button_targets").length()==4 && input?.optInt("pressed")==0 && input.optLong("delivered")==keys+2) {
                                check(s.getLong("input_resets")>heldEpoch && NativeSessionHost.inputTarget(target.toString())==null)
                                check(checkNotNull(row()).getJSONObject("mode_badge").getString("mode")=="MENU")
                                baseline=checkNotNull(WindowRecenterWire.read(s,target));menuEpoch=s.getLong("input_resets")
                                result.put("menu_released_held_key",true).put("four_item_menu_observed",true);record("menu_ready",s)
                                recenterStart=requests.keys.toSet();click("recenter",true);stage="recentering"
                            }
                        }
                        "recentering" -> {
                            val m=checkNotNull(menu());check(m.optString("phase")!="CANCELLED") {"RECENTER_OPERATION_CANCELLED"}
                            val request=accepted("recenter",recenterStart)
                            if(request!=null && m.optString("phase")=="COMPLETE" && ready(s) && focused(s)) {
                                val model=checkNotNull(WindowRecenterWire.read(s,target));val before=checkNotNull(baseline)
                                check(m.getString("request_id")==request && m.getString("recenter_read_reason")=="captured" && model.poseCommand==request)
                                check(!before.pose.near(model.pose,.001)) {"RECENTER_NO_MOVEMENT"}
                                check(before.sameDimensions(model) && WindowRecenterWire.applied(s,target,request,model.pose) && WindowMoveController.motion(pose())?.near(model.pose)==true)
                                check(s.getLong("input_resets")==menuEpoch+2 && checkNotNull(input).getInt("pressed")==0 && input.getLong("delivered")==keys+2 && input.getJSONObject("pointer").getLong("buttons")==buttons)
                                check(NativeSessionHost.inputTarget(target.toString())?.epoch==s.getLong("input_resets") && menusGone())
                                val segment=ray(pose(),Vector3(0f,0f,0f));check(scene().lineSegmentIntersect(segment.first,segment.second)?.entity?.id==adapter.windowEntity(window)?.id)
                                result.put("actual_pose_changed",true).put("single_recenter_request",true).put("native_ack_and_applied_pose",true)
                                    .put("sdk_body_hit",true).put("dimensions_unchanged",true).put("raw_focus_restored",true).put("mode_recentering_observed",recentMode)
                                record("recenter_applied_and_refocused",s);click("close");stage="closed"
                            }
                        }
                        "closed" -> if(windows(s).isEmpty() && adapter.windowEntity(window)==null && accepted("close")!=null && items(adapter.metrics().optJSONArray("targets")).isEmpty()) {
                            result.put("captured_window_retired",true);record("closed",s);cleanup();stage="stopped";deadline=SystemClock.elapsedRealtime()+30000
                            NativeSessionHost.withStoppedRuntime({}, {})
                        }
                        "stopped" -> if(current!=handle && s.optString("state")=="Ready" && s.optBoolean("activity_visible") && s.optString("xr_state")=="FOCUSED" && s.getJSONObject("transport").getString("state")=="Idle" &&
                            items(s.getJSONObject("render").getJSONArray("targets")).let {it.size==1 && it.single().optString("role")=="diagnostic" && it.single().optLong("frames")>=3}) {
                            check(JSONObject(NativeBridge.nativeSnapshot(handle)).getInt("code")==NativeBridge.STALE && s.getJSONObject("transport").getJSONArray("pending_cleanup").length()==0)
                            val resources=adapter.metrics();val controls=resources.getJSONObject("window_controls");val badges=resources.getJSONObject("window_mode_badges")
                            check(controls.getLong("entity_owners")==0L && controls.getLong("listener_owners")==0L && menusGone())
                            for(field in listOf("entity_owners","object_owners","mesh_owners","material_owners","texture_owners","pixel_slots_in_use","cleanup_pending_owners")) check(badges.getLong(field)==0L)
                            result.put("old_runtime_destroyed",true).put("control_owners_zero",true).put("menu_owners_zero",true).put("mode_owners_zero",true).put("result","pass")
                            finish();return
                        }
                    }
                }
                ui.postDelayed(this,32)
            } catch(failure:Exception) {
                result.put("result","fail").put("stage",stage).put("failure_class",failure.javaClass.simpleName)
                    .put("failure_line",failure.stackTrace.firstOrNull {it.className.startsWith("io.github.tinmachine.eidolon.WindowRecenterCase")}?.lineNumber ?: JSONObject.NULL)
                // Metadata only: never serialize room-derived viewer/window poses or a full runtime snapshot.
                runCatching {val h=NativeSessionHost.currentHandle();if(h>0) JSONObject(NativeBridge.nativeSnapshot(h)).optJSONObject("runtime")?.let {record("failed",it)}}
                try {cleanup()} catch(_:Exception) {result.put("source_cleanup_failed",true)}
                finish()
            }
        }
    }
}
