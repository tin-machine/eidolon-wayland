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
import com.meta.spatial.toolkit.Transform
import com.meta.spatial.toolkit.Controller
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap

/** Fixed two-foot fixture: actual SDK hit -> ordinary listener/Host -> native ACK and observed state. */
object WindowControlsCase {
    @Volatile private var active: Check?=null
    fun start(nonce:String,adapter:SpatialPanelAdapter,scene:()->Scene,complete:(JSONObject)->Unit) {
        check(active==null)
        Check(nonce,adapter,scene,complete,"window-controls").also {active=it;it.start()}
    }
    fun startMove(nonce:String,adapter:SpatialPanelAdapter,scene:()->Scene,complete:(JSONObject)->Unit) {
        check(active==null)
        Check(nonce,adapter,scene,complete,"window-move").also {active=it;it.start()}
    }
    fun commandComplete(request:String,code:String) {active?.let {if(it.requests.containsKey(request) || request in PROBES) it.completions[request]=code}}
    fun requested(action:String,runtime:Long,target:String,revision:Long,request:String,code:Int) {
        active?.let {if(runtime==it.handle && it.requests.size<32) it.requests[request]=JSONObject()
            .put("action",action).put("runtime",runtime.toString()).put("target",JSONObject(target)).put("revision",revision)
            .put("request_id",request).put("enqueue_code",code).toString()}
    }
    private val PROBES=setOf("9101201","9101202","9101203")
    private class Check(val nonce:String,val adapter:SpatialPanelAdapter,val scene:()->Scene,val complete:(JSONObject)->Unit,val name:String):Runnable {
        val ui=Handler(Looper.getMainLooper())
        val requests=ConcurrentHashMap<String,String>()
        val completions=ConcurrentHashMap<String,String>()
        val rows=JSONArray()
        val result=JSONObject().put("case",name).put("fixture_revision",if(name=="window-move") "window-move-v1" else "window-controls-v2").put("nonce",nonce).put("result","running")
            .put("consumer","SDK").put("synthetic_input",true).put("synthetic_visibility",false)
            .put("physical_input_verified",false).put("hmd_verified",false).put("text_verified",false)
            .put("dispatch","Scene.onInput").put("raycast","Scene.lineSegmentIntersect")
        @Volatile var handle=0L
        var stage="ready"
        var deadline=SystemClock.elapsedRealtime()+120000
        var settled=0L
        var first="";var second=""
        var firstKey=JSONObject();var secondKey=JSONObject()
        var revision=0L;var pointerButtons=0L;var heldEpoch=0L;var keys=0L
        var priorRequests=setOf<String>()
        var source:Entity?=null
        var oldCloseRay:Pair<Vector3,Vector3>?=null
        var oldCloseEntity=""
        var modeTextures=0L;var modeUpdates=0L
        var moveBefore=WindowMotionPose.at()
        var moveExpected=WindowMotionPose.at()
        val controllerStart=WindowMotionPose.at(.2,1.0,.1)
        val controllerEnd=WindowMotionPose(listOf(.35,1.1,.25),listOf(0.0,kotlin.math.sin(Math.PI/24),0.0,kotlin.math.cos(Math.PI/24)))
        fun start() {NativeSessionHost.publishCheck(result);ui.post(this)}
        fun items(a:JSONArray)=(0 until a.length()).map {a.getJSONObject(it)}
        fun windows(s:JSONObject)=s.optJSONObject("app")?.optJSONArray("windows")?.let(::items) ?: emptyList()
        fun same(a:JSONObject,b:JSONObject)=listOf("session","window","scene","generation").all {a.optString(it)==b.optString(it)}
        fun row(window:String)=items(adapter.metrics().getJSONArray("targets")).first {it.getJSONObject("target").getString("window")==window}
        fun controls(window:String)=row(window).getJSONObject("controls")
        fun button(window:String,action:String)=items(controls(window).getJSONArray("buttons")).first {it.getString("action")==action}
        fun ready(s:JSONObject,count:Int):Boolean {
            if(s.optString("state")!="Ready" || !s.getBoolean("activity_visible") || s.getString("xr_state")!="FOCUSED" || windows(s).size!=count) return false
            val rendered=items(s.getJSONObject("render").getJSONArray("targets"))
            val rows=items(adapter.metrics().getJSONArray("targets"))
            return rendered.size==count && rows.size==count && rows.all {row ->
                val target=row.getJSONObject("target")
                val c=row.optJSONObject("controls")
                val badge=row.optJSONObject("mode_badge")
                badge?.optBoolean("object_ready")==true && badge.optLong("failures")==0L && c?.optBoolean("enabled")==true && c.optLong("revision")==s.getJSONObject("render").getLong("theme_revision") &&
                    c.optLong("failures")==0L && c.optInt("entities")==9 &&
                    items(c.getJSONArray("buttons")).size==3 && items(c.getJSONArray("buttons")).all {it.optBoolean("ready") && same(it.getJSONObject("target"),target)} &&
                    rendered.any {same(it.getJSONObject("key"),target) && it.getLong("frames")>0} &&
                    NativeSessionHost.inputTarget(target.toString())?.epoch==s.getLong("input_resets")
            }
        }
        fun record(label:String,s:JSONObject) {
            rows.put(JSONObject().put("stage",label).put("runtime",s).put("adapter",adapter.metrics()))
            result.put("cycles",rows);publish()
        }
        fun publish() {
            result.put("requests",JSONArray(requests.values.map(::JSONObject).sortedBy {it.getString("request_id").toLong()}.map {
                it.put("completion",completions[it.getString("request_id")] ?: JSONObject.NULL)
            })).put("probe_completions",JSONObject(completions.filterKeys {it in PROBES}))
            NativeSessionHost.publishCheck(result)
        }
        fun requestAccepted(action:String,target:JSONObject):Boolean=requests.any {(id,value) -> id !in priorRequests && JSONObject(value).let {
            it.getString("action")==action && same(it.getJSONObject("target"),target) && it.getLong("revision")==revision &&
                it.getInt("enqueue_code")==NativeBridge.ACCEPTED && completions[id]=="ACCEPTED"
        }}
        fun focused(s:JSONObject,target:JSONObject):Boolean {
            val input=s.getJSONObject("app").getJSONObject("input")
            return input.optString("focus_window")==target.getString("window") && !input.isNull("focus_token") &&
                NativeSessionHost.keyboardFocusMatches(target,input.getString("focus_token"))
        }
        fun pose(window:String)=getAbsoluteTransform(checkNotNull(adapter.windowEntity(window)))
        fun ray(pose:Pose,x:Double,y:Double,z:Double=0.0):Pair<Vector3,Vector3> {
            val a=Vector3(.27f,.31f,-1.3f)
            val t=(.8+1.3)/(z+1.3)
            return pose*a to pose*Vector3((a.x+(x-a.x)*t).toFloat(),(a.y+(y-a.y)*t).toFloat(),.8f)
        }
        fun intersect(segment:Pair<Vector3,Vector3>)=scene().lineSegmentIntersect(segment.first,segment.second)
        fun hit(window:String,action:String):HitInfo {
            val entity=Entity(button(window,action).getString("entity").toLong())
            val center=getAbsoluteTransform(entity).t
            // A segment through the real sphere centre; rotation and physical resize come from SDK state.
            val p=pose(window);val local=p.inverse()*center
            val h=checkNotNull(intersect(ray(p,local.x.toDouble(),local.y.toDouble(),local.z.toDouble()))) {"CONTROL_MISSING_HIT"}
            check(h.entity?.id==entity.id) {"CONTROL_WRONG_HIT"}
            return h
        }
        fun geometry(s:JSONObject) {
            val all=items(adapter.metrics().getJSONArray("targets")).flatMap {items(it.getJSONObject("controls").getJSONArray("buttons"))}.map {it.getString("entity")}.toSet()
            val proof=JSONArray()
            for(window in listOf(first,second)) {
                val p=pose(window)
                val body=checkNotNull(intersect(ray(p,0.0,0.0)))
                check(body.entity?.id==adapter.windowEntity(window)?.id) {"BODY_CONTROL_COLLISION"}
                val a=hit(window,"focus");val b=hit(window,"close")
                val diameter=checkNotNull(WindowControls.parse(s.getJSONObject("render").optJSONObject("window_controls"))).diameter
                for(action in listOf("move","focus","close")) {
                    val entity=Entity(button(window,action).getString("entity").toLong())
                    val local=p.inverse()*getAbsoluteTransform(entity).t
                    for(axis in 0..1) for((factor,inside) in listOf(.8 to true,1.3 to false)) {
                        val offset=diameter*.5*factor
                        val sizeHit=intersect(ray(p,local.x+if(axis==0) offset else 0.0,local.y+if(axis==1) offset else 0.0,local.z.toDouble()))
                        check((sizeHit?.entity?.id==entity.id)==inside) {"CONTROL_PHYSICAL_DIAMETER"}
                    }
                }
                val middle=(a.point+b.point)*.5f
                val local=p.inverse()*middle
                val gap=intersect(ray(p,local.x.toDouble(),local.y.toDouble(),local.z.toDouble()))
                check(gap?.entity?.id?.toString() !in all) {"TRANSPARENT_GAP_CONTROL_COLLISION"}
                val badge=row(window).getJSONObject("mode_badge")
                val badgeEntity=Entity(badge.getString("entity").toLong());val badgeLocal=p.inverse()*getAbsoluteTransform(badgeEntity).t
                check(intersect(ray(p,badgeLocal.x.toDouble(),badgeLocal.y.toDouble(),badgeLocal.z.toDouble()))?.entity?.id!=badgeEntity.id) {"MODE_BADGE_INTERCEPTED_INPUT"}
                proof.put(JSONObject().put("mode_badge_non_hit",true).put("window",window).put("body_entity",body.entity?.id.toString())
                    .put("focus_entity",a.entity?.id.toString()).put("close_entity",b.entity?.id.toString())
                    .put("gap_entity",gap?.entity?.id?.toString() ?: JSONObject.NULL).put("diameter_m",diameter).put("diameter_raycast_verified",true))
            }
            val c=Entity(button(first,"close").getString("entity").toLong())
            val p=pose(first);val local=p.inverse()*getAbsoluteTransform(c).t
            oldCloseRay=ray(p,local.x.toDouble(),local.y.toDouble(),local.z.toDouble());oldCloseEntity=c.id.toString()
            result.put("geometry",proof).put("geometry_observed",true)
            val r=adapter.metrics().getJSONObject("window_controls")
            check(r.getLong("entity_owners")==18L && r.getLong("listener_owners")==6L)
            val badges=adapter.metrics().getJSONObject("window_mode_badges")
            for(field in listOf("entity_owners","object_owners","mesh_owners","material_owners","texture_owners")) check(badges.getLong(field)==2L)
            modeTextures=badges.getLong("texture_create_calls");modeUpdates=badges.getLong("texture_updates")
            record("two_ready",s)
        }
        fun dispatch(h:HitInfo,down:Boolean) {
            scene().onInput(h,checkNotNull(source),ButtonBits.ButtonTriggerR,if(down) ButtonBits.ButtonTriggerR else 0)
            scene().resolveInput()
        }
        fun click(window:String,action:String) {
            priorRequests=requests.keys.toSet()
            val h=hit(window,action);dispatch(h,true);dispatch(h,false)
        }
        fun sendKey(action:Int,code:Int,scan:Int) {
            val now=SystemClock.uptimeMillis()
            check(NativeSessionHost.sendPhysicalKey(KeyEvent(now,now,action,code,0,0,Int.MAX_VALUE-14,scan,0,InputDevice.SOURCE_KEYBOARD)))
        }
        fun probe(id:String,target:JSONObject,themeRevision:Long) {
            check(NativeBridge.nativeCommand(handle,JSONObject().put("schema",1).put("type","close_window_target")
                .put("request_id",id).put("target",target).put("controls_revision",themeRevision).toString())==NativeBridge.ACCEPTED)
        }
        fun cleanup() {
            val entity=source;source=null
            try {entity?.let {scene().onNoIntersectionInput(it,0,0);scene().resolveInput()}}
            finally {entity?.destroy()}
            result.put("source_destroyed",true)
        }
        fun finish() {active=null;publish();complete(result)}
        override fun run() {
            try {
                check(SystemClock.elapsedRealtime()<deadline) {"WINDOW_CONTROLS_TIMEOUT"}
                val current=NativeSessionHost.currentHandle()
                if(current>0) {
                    val envelope=JSONObject(NativeBridge.nativeSnapshot(current))
                    val s=envelope.optJSONObject("runtime")
                    if(s==null) {
                        // stopRuntime destroys the registry entry before clearing the Host handle.
                        // Only our explicit stop/restart stage may observe that transient stale handle.
                        check(stage=="stopped" && envelope.optInt("code")==NativeBridge.STALE) {"RUNTIME_SNAPSHOT_UNAVAILABLE"}
                        ui.postDelayed(this,32);return
                    }
                    check(s.getString("state")!="Failed")
                    s.optJSONObject("app")?.let {check(it.isNull("error"))}
                    val input=s.optJSONObject("app")?.optJSONObject("input")
                    if(handle>0 && stage!="stopped") check(current==handle) {"RUNTIME_CHANGED"}
                    when(stage) {
                        "ready" -> if(s.getString("state")=="Ready" && s.getBoolean("activity_visible") && s.getString("xr_state")=="FOCUSED") {
                            check(s.getJSONObject("transport").getString("state")=="Idle")
                            handle=current;NativeSessionHost.openTerminal();stage="first"
                        }
                        "first" -> if(ready(s,1)) {
                            first=windows(s).single().getString("window");firstKey=JSONObject(row(first).getJSONObject("target").toString())
                            NativeSessionHost.openTerminal();stage="two"
                        }
                        "two" -> if(ready(s,2)) {
                            second=windows(s).first {it.getString("window")!=first}.getString("window")
                            secondKey=JSONObject(row(second).getJSONObject("target").toString());revision=s.getJSONObject("render").getLong("theme_revision")
                            source=Entity.create();settled=SystemClock.elapsedRealtime()+500;stage="geometry"
                        }
                        "geometry" -> if(SystemClock.elapsedRealtime()>=settled && ready(s,2)) {
                            geometry(s);pointerButtons=checkNotNull(input).getJSONObject("pointer").getLong("buttons")
                            click(first,"focus");stage="focus_first"
                        }
                        "focus_first" -> if(focused(s,firstKey) && requestAccepted("focus",firstKey)) {
                            record("focus_first_ack",s);keys=checkNotNull(input).getLong("delivered")
                            sendKey(KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_SHIFT_LEFT,42);stage="held"
                        }
                        "held" -> if(checkNotNull(input).getInt("pressed")==1 && input.getLong("delivered")==keys+1) {
                            heldEpoch=s.getLong("input_resets");record("held_key",s);click(second,"focus");stage="focus_second"
                        }
                        "focus_second" -> if(focused(s,secondKey) && requestAccepted("focus",secondKey)) {
                            check(checkNotNull(input).getInt("pressed")==0 && input.getLong("delivered")==keys+2 && s.getLong("input_resets")>heldEpoch)
                            check(input.getJSONObject("pointer").getLong("buttons")==pointerButtons)
                            record("focus_second_ack_and_key_released",s)
                            if(name=="window-move") {
                                heldEpoch=s.getLong("input_resets");moveBefore=checkNotNull(WindowMoveController.motion(pose(second)))
                                dispatch(hit(second,"move"),true);dispatch(hit(second,"move"),false)
                                settled=SystemClock.elapsedRealtime()+300;stage="move_source_rejected"
                            } else {
                                priorRequests=requests.keys.toSet();dispatch(hit(first,"close"),true)
                                scene().onNoIntersectionInput(checkNotNull(source),ButtonBits.ButtonTriggerR,0);scene().resolveInput()
                                settled=SystemClock.elapsedRealtime()+300;stage="cancel"
                            }
                        }
                        "move_source_rejected" -> if(SystemClock.elapsedRealtime()>=settled) {
                            val move=row(second).getJSONObject("move")
                            check(move.getLong("started")==0L && move.getLong("rejected")>=1 && move.getString("reason")=="source_pose_unavailable")
                            check(s.getLong("input_resets")==heldEpoch && focused(s,secondKey))
                            result.put("missing_source_pose_rejected",true);record("source_pose_rejected",s)
                            val controller=Controller().apply {buttonState=ButtonBits.ButtonTriggerR;changedButtons=ButtonBits.ButtonTriggerR;isActive=true}
                            checkNotNull(source).setComponent(Transform(WindowMoveController.sdkPose(controllerStart)))
                            checkNotNull(source).setComponent(controller)
                            priorRequests=requests.keys.toSet();dispatch(hit(second,"move"),true);stage="move_captured"
                        }
                        "move_captured" -> if(row(second).getJSONObject("move").optString("phase")=="DRAGGING") {
                            check(NativeSessionHost.inputTarget(secondKey.toString())==null)
                            moveExpected=controllerEnd*controllerStart.inverse()*moveBefore
                            checkNotNull(source).setComponent(Transform(WindowMoveController.sdkPose(controllerEnd)))
                            stage="move_preview"
                        }
                        "move_preview" -> if(WindowMoveController.motion(pose(second))?.near(moveExpected,.001)==true) {
                            val placement=items(s.getJSONObject("workspace").getJSONArray("windows")).first {it.getString("window")==second}.getJSONObject("placement")
                            check(WindowMoveController.motion(placement.getJSONObject("pose"))?.near(moveBefore)==true) {"PREVIEW_PERSISTED_BEFORE_RELEASE"}
                            check(row(second).getJSONObject("mode_badge").getString("mode")=="MOVING")
                            result.put("whole_window_preview",true).put("mode_moving_observed",true);record("move_preview_not_persisted",s)
                            scene().onNoIntersectionInput(checkNotNull(source),0,ButtonBits.ButtonTriggerR);scene().resolveInput()
                            settled=SystemClock.elapsedRealtime()+120;stage="move_off_handle"
                        }
                        "move_off_handle" -> if(SystemClock.elapsedRealtime()>=settled) {
                            check(row(second).getJSONObject("move").getString("phase")=="DRAGGING") {"OFF_HANDLE_CANCELLED_HELD_MOVE"}
                            result.put("off_handle_capture_retained",true);record("off_handle_capture_retained",s)
                            val controller=checkNotNull(source).getComponent<Controller>();controller.buttonState=0;controller.changedButtons=ButtonBits.ButtonTriggerR
                            checkNotNull(source).setComponent(controller)
                            scene().onNoIntersectionInput(checkNotNull(source),ButtonBits.ButtonTriggerR,0);scene().resolveInput()
                            stage="move_applied"
                        }
                        "move_applied" -> if(ready(s,2) && row(second).getJSONObject("move").optLong("completed")==1L && requestAccepted("move",secondKey) && focused(s,secondKey)) {
                            val placement=items(s.getJSONObject("workspace").getJSONArray("windows")).first {it.getString("window")==second}.getJSONObject("placement")
                            val applied=placement.getJSONObject("applied_pose")
                            check(WindowMoveController.motion(placement.getJSONObject("pose"))?.near(moveExpected,.001)==true &&
                                WindowMoveController.motion(applied.getJSONObject("pose"))?.near(moveExpected,.001)==true && same(applied.getJSONObject("target"),secondKey))
                            check(applied.getString("pose_command")==row(second).getJSONObject("move").getString("request_id"))
                            check(input?.getJSONObject("pointer")?.getLong("buttons")==pointerButtons)
                            val badges=adapter.metrics().getJSONObject("window_mode_badges")
                            check(badges.getLong("texture_create_calls")==modeTextures && badges.getLong("texture_updates")>modeUpdates)
                            val route=row(second).getJSONObject("move").getString("release_route")
                            check(route in setOf("sdk_up","sdk_off_handle_up","controller_state_up"))
                            result.put("move_release_route",route).put("mode_texture_reused",true).put("move_native_ack_and_geometry_applied",true).put("move_raw_focus_restored",true);record("move_applied_and_refocused",s)
                            priorRequests=requests.keys.toSet();dispatch(hit(first,"close"),true)
                            scene().onNoIntersectionInput(checkNotNull(source),ButtonBits.ButtonTriggerR,0);scene().resolveInput()
                            settled=SystemClock.elapsedRealtime()+300;stage="cancel"
                        }
                        "cancel" -> if(SystemClock.elapsedRealtime()>=settled) {
                            check(windows(s).size==2 && requests.keys.all {it in priorRequests}) {"CANCEL_BECAME_CLICK"}
                            result.put("cancel_did_not_close",true);record("cancelled_close",s)
                            probe("9101201",firstKey,revision+1);stage="stale_revision"
                        }
                        "stale_revision" -> if(completions["9101201"]!=null) {
                            check(completions["9101201"]=="STALE" && windows(s).size==2)
                            val old=JSONObject(firstKey.toString()).put("generation",(firstKey.getString("generation").toLong()+1).toString())
                            probe("9101202",old,revision);stage="stale_generation"
                        }
                        "stale_generation" -> if(completions["9101202"]!=null && ready(s,2)) {
                            check(completions["9101202"]=="STALE" && windows(s).size==2)
                            result.put("stale_revision_rejected",true).put("stale_generation_rejected",true)
                            record("native_stale_guards",s);click(first,"close");stage="closed"
                        }
                        "closed" -> if(windows(s).none {it.getString("window")==first} && ready(s,1) && requestAccepted("close",firstKey)) {
                            check(windows(s).single().getString("window")==second && adapter.windowEntity(first)==null)
                            check(same(row(second).getJSONObject("target"),secondKey))
                            val hit=intersect(checkNotNull(oldCloseRay));check(hit?.entity?.id?.toString()!=oldCloseEntity)
                            val resources=adapter.metrics().getJSONObject("window_controls")
                            check(resources.getLong("entity_owners")==9L && resources.getLong("listener_owners")==3L)
                            check(adapter.metrics().getJSONObject("window_mode_badges").getLong("entity_owners")==1L)
                            result.put("closed_only_captured_window",true).put("retired_control_no_hit",true)
                            record("close_ack_and_retired_mesh",s);probe("9101203",firstKey,revision);stage="retired_target"
                        }
                        "retired_target" -> if(completions["9101203"]!=null && ready(s,1)) {
                            check(completions["9101203"]=="STALE" && windows(s).single().getString("window")==second)
                            result.put("retired_target_rejected",true);click(second,"focus");stage="survivor"
                        }
                        "survivor" -> if(focused(s,secondKey) && requestAccepted("focus",secondKey)) {
                            keys=checkNotNull(input).getLong("delivered")
                            sendKey(KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_A,30);sendKey(KeyEvent.ACTION_UP,KeyEvent.KEYCODE_A,30)
                            sendKey(KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_DEL,14);sendKey(KeyEvent.ACTION_UP,KeyEvent.KEYCODE_DEL,14)
                            stage="typed"
                        }
                        "typed" -> if(checkNotNull(input).getLong("delivered")==keys+4 && input.getInt("pressed")==0 && ready(s,1)) {
                            check(input.getJSONObject("pointer").getLong("buttons")==pointerButtons)
                            record("survivor_keys_delivered",s);cleanup();stage="stopped";deadline=SystemClock.elapsedRealtime()+30000
                            NativeSessionHost.withStoppedRuntime({}, {})
                        }
                        "stopped" -> if(current>handle && s.getString("state")=="Ready" && s.getBoolean("activity_visible") && s.getString("xr_state")=="FOCUSED" &&
                            s.getJSONObject("transport").getString("state")=="Idle" && s.getJSONObject("render").getJSONArray("targets").let {it.length()==1 && it.getJSONObject(0).optString("role")=="diagnostic" && it.getJSONObject(0).getLong("frames")>=3}) {
                            check(s.getJSONObject("transport").getJSONArray("pending_cleanup").length()==0)
                            check(JSONObject(NativeBridge.nativeSnapshot(handle)).getInt("code")==NativeBridge.STALE)
                            val resources=adapter.metrics().getJSONObject("window_controls")
                            check(resources.getLong("entity_owners")==0L && resources.getLong("listener_owners")==0L)
                            val badges=adapter.metrics().getJSONObject("window_mode_badges")
                            for(field in listOf("entity_owners","object_owners","mesh_owners","material_owners","texture_owners","pixel_slots_in_use","cleanup_pending_owners")) check(badges.getLong(field)==0L)
                            check(badges.getInt("pixel_slots_allocated")<=9)
                            result.put("mode_resources_after_stop",badges)
                            result.put("old_runtime_destroyed",true).put("control_owners_zero",true).put("mode_badge_owners_zero",true).put("resources_after_stop",resources)
                                .put("result","pass")
                            finish();return
                        }
                    }
                }
                ui.postDelayed(this,32)
            } catch(failure:Exception) {
                result.put("result","fail").put("stage",stage).put("failure_class",failure.javaClass.simpleName)
                    .put("failure_site",failure.stackTrace.firstOrNull {it.className.startsWith("io.github.tinmachine.eidolon.")}?.toString()).put("adapter",adapter.metrics())
                if(NativeSessionHost.currentHandle()>0) result.put("failure_runtime",JSONObject(NativeBridge.nativeSnapshot(NativeSessionHost.currentHandle())))
                try {cleanup()} catch(_:Exception) {result.put("cleanup_failed",true)}
                finish()
            }
        }
    }
}
