package io.github.tinmachine.eidolon

import android.os.SystemClock
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Pose
import com.meta.spatial.runtime.ButtonBits
import com.meta.spatial.runtime.Scene
import com.meta.spatial.toolkit.Controller
import com.meta.spatial.toolkit.Transform
import com.meta.spatial.toolkit.getAbsoluteTransform
import com.meta.spatial.toolkit.setAbsoluteTransform
import org.json.JSONArray
import org.json.JSONObject

/** Window-local preview owner. The SDK source is borrowed and is never destroyed by this object. */
class WindowMoveController(private val parent:Entity,private val target:String,private val runtime:Long,private val revision:Long,
    private val sdk:Scene,private val live:()->Boolean):AutoCloseable {
    private var capture:WindowMoveCapture?=null
    private var ticket:NativeSessionHost.WindowMoveTicket?=null
    private var source:Entity?=null
    private var closed=false
    private var lastReason="idle"
    private var lastPhase="IDLE"
    private var lastRequest:String?=null
    private var rejected=0L
    private var completed=0L
    private var cancelled=0L
    private var started=0L
    private val starts=WindowMoveStarts()
    val phase:WindowMoveCapture.Phase? get()=capture?.phase
    fun blocksPose():Boolean=phase in setOf(WindowMoveCapture.Phase.DRAGGING,WindowMoveCapture.Phase.NEEDS_SUBMIT,WindowMoveCapture.Phase.COMMITTING)
    fun owns(sourceId:Long,downTime:Long?=null):Boolean=capture?.identity?.let {it.source==sourceId && (downTime==null || downTime==it.downTime)}==true
    private fun controller(entity:Entity):Controller?=runCatching {
        if(!entity.hasComponent<Controller>() || !entity.hasComponent<Transform>()) null else entity.getComponent<Controller>()
    }.getOrNull()
    private fun sourcePose(entity:Entity):WindowMotionPose?=runCatching {
        if(!entity.hasComponent<Transform>()) null else motion(getAbsoluteTransform(entity))
    }.getOrNull()
    fun begin(entity:Entity,downTime:Long):Boolean {
        if(closed || !live() || capture!=null || downTime<0) return false
        val state=controller(entity)
        val from=sourcePose(entity)
        val body=runCatching {motion(getAbsoluteTransform(parent))}.getOrNull()
        if(state?.isActive!=true || state.buttonState and PRIMARY==0 || from?.valid()!=true || body?.valid(true)!=true) {
            rejected++;lastReason="source_pose_unavailable";return false
        }
        if(!starts.accept(entity.id,downTime)) {rejected++;lastReason="old_or_excess_source";return false}
        val owner=NativeSessionHost.beginWindowMove(JSONObject(target),runtime,revision,entity.id)
        if(owner==null) {rejected++;lastReason="owner_unavailable";return false}
        ticket=owner;source=entity
        capture=WindowMoveCapture(WindowMoveIdentity(runtime,target,revision,owner.control.intent,entity.id,downTime),from,body,SystemClock.elapsedRealtime())
        started++;lastReason="dragging";return true
    }
    fun release(sourceId:Long,downTime:Long) {
        val c=capture ?: return
        if(!owns(sourceId,downTime) || c.phase!=WindowMoveCapture.Phase.DRAGGING) return
        val owner=ticket ?: return
        val current=runCatching {JSONObject(NativeBridge.nativeSnapshot(runtime)).optJSONObject("runtime")}.getOrNull()
        val entity=source ?: return
        if(current==null || !currentOwner(owner,current) || controller(entity)?.isActive!=true) {c.cancel("owner_changed");return}
        val next=sourcePose(entity)
        if(next==null) {c.cancel("source_pose_unavailable");return}
        c.preview(c.identity,next,SystemClock.elapsedRealtime())?.let {setAbsoluteTransform(parent,sdkPose(it))}
        c.release(c.identity,SystemClock.elapsedRealtime())
    }
    fun pointerLost(sourceId:Long,downTime:Long?=null) {
        if(!owns(sourceId,downTime)) return
        val state=source?.let(::controller)
        // Ray hover is allowed to leave the handle while the same tracked controller still holds trigger.
        if(state?.isActive==true && state.buttonState and PRIMARY!=0) return
        capture?.cancel("input_capture_lost")
    }
    fun cancelSource(sourceId:Long,reason:String) {if(owns(sourceId)) capture?.cancel(reason)}
    fun tick(runtimeState:JSONObject,placement:JSONObject) {
        val c=capture ?: return
        val owner=ticket ?: return
        val now=SystemClock.elapsedRealtime()
        if(!currentOwner(owner,runtimeState)) c.cancel("owner_changed")
        c.current(c.identity,now)
        val entity=source
        if(c.phase==WindowMoveCapture.Phase.DRAGGING) {
            val state=entity?.let(::controller)
            val from=entity?.let(::sourcePose)
            when {
                state?.isActive!=true || from==null -> c.cancel("source_pose_unavailable")
                state.buttonState and PRIMARY==0 -> {c.preview(c.identity,from,now)?.let {setAbsoluteTransform(parent,sdkPose(it))};c.release(c.identity,now)}
                else -> c.preview(c.identity,from,now)?.let {setAbsoluteTransform(parent,sdkPose(it))}
            }
        }
        if(c.phase==WindowMoveCapture.Phase.NEEDS_SUBMIT) {
            val request=NativeSessionHost.submitWindowMovePose(owner,json(c.pose))
            if(request==null || !c.submitted(request,now)) c.cancel("pose_enqueue_rejected")
        }
        val model=motion(placement.getJSONObject("pose"))
        if(model==null) c.cancel("model_pose_unavailable")
        else if(c.phase in setOf(WindowMoveCapture.Phase.COMMITTING,WindowMoveCapture.Phase.GEOMETRY)) {
            val command=if(placement.isNull("pose_command")) null else placement.getString("pose_command")
            val applied=placement.optJSONObject("applied_pose")
            val appliedMatches=applied!=null && !applied.isNull("pose_command") && applied.getString("pose_command")==c.request &&
                same(applied.getJSONObject("target"),JSONObject(target)) && motion(applied.getJSONObject("pose"))?.near(c.pose)==true
            c.observe(c.identity,now,c.request?.let(NativeSessionHost::poseCompletion),command,model,appliedMatches)
        }
        if(c.phase==WindowMoveCapture.Phase.COMPLETE) {
            completed++;end(true)
        } else if(c.phase==WindowMoveCapture.Phase.CANCELLED) {
            // Only a native-confirmed model pose is restored; a rejected preview never becomes a saved pose.
            if(live() && model?.valid(true)==true) setAbsoluteTransform(parent,sdkPose(model))
            cancelled++;end(false)
        }
    }
    private fun end(restore:Boolean) {
        val c=capture
        lastReason=c?.reason ?: "idle";lastPhase=c?.phase?.name ?: "IDLE";lastRequest=c?.request
        c?.request?.let(NativeSessionHost::forgetPose)
        val owner=ticket
        capture=null;ticket=null
        val entity=source;source=null
        if(owner!=null) NativeSessionHost.finishWindowMove(owner,restore)
        entity?.let {runCatching {sdk.onNoIntersectionInput(it,PRIMARY,0);sdk.resolveInput()}}
    }
    private fun currentOwner(owner:NativeSessionHost.WindowMoveTicket,state:JSONObject):Boolean {
        if(!live() || !NativeSessionHost.windowMoveCurrent(owner) || state.optString("state")!="Ready" || !state.optBoolean("activity_visible") ||
            state.optString("xr_state")!="FOCUSED" || state.optString("scene")!=JSONObject(target).getString("scene") ||
            state.optJSONObject("render")?.optLong("theme_revision")!=revision) return false
        val panels=state.optJSONArray("panels") ?: return false
        val frames=state.getJSONObject("render").optJSONArray("targets") ?: return false
        if((0 until frames.length()).none {i -> frames.getJSONObject(i).let {same(it.getJSONObject("key"),JSONObject(target)) && it.optLong("frames")>0}}) return false
        return (0 until panels.length()).any {i -> panels.getJSONObject(i).let {
            it.optString("action")=="create" && it.optBoolean("applied") && it.isNull("error") && same(it.getJSONObject("target"),JSONObject(target))
        }}
    }
    fun metadata():JSONObject=JSONObject().put("phase",phase?.name ?: lastPhase).put("reason",capture?.reason ?: lastReason)
        .put("source",source?.id?.toString() ?: JSONObject.NULL).put("request_id",capture?.request ?: lastRequest ?: JSONObject.NULL)
        .put("started",started).put("completed",completed).put("cancelled",cancelled).put("rejected",rejected)
    override fun close() {
        if(closed) return
        closed=true;capture?.cancel("owner_disposed");end(false)
    }
    companion object {
        private val PRIMARY=ButtonBits.ButtonTriggerL or ButtonBits.ButtonTriggerR or ButtonBits.ButtonA or ButtonBits.ButtonX
        private fun same(a:JSONObject,b:JSONObject)=listOf("session","window","scene","generation").all {a.optString(it)==b.optString(it)}
        fun motion(pose:Pose):WindowMotionPose? {
            val q=listOf(pose.q.x.toDouble(),pose.q.y.toDouble(),pose.q.z.toDouble(),pose.q.w.toDouble())
            val norm=kotlin.math.sqrt(q.sumOf {it*it})
            if(!norm.isFinite() || norm<1e-6) return null
            return WindowMotionPose(listOf(pose.t.x.toDouble(),pose.t.y.toDouble(),pose.t.z.toDouble()),q.map {it/norm}).takeIf {it.valid()}
        }
        fun motion(pose:JSONObject):WindowMotionPose?=runCatching {
            WindowMotionPose((0..2).map {pose.getJSONArray("position_m").getDouble(it)},(0..3).map {pose.getJSONArray("rotation_xyzw").getDouble(it)}).takeIf {it.valid()}
        }.getOrNull()
        fun json(pose:WindowMotionPose)=JSONObject().put("position_m",JSONArray(pose.p)).put("rotation_xyzw",JSONArray(pose.q))
        fun sdkPose(pose:WindowMotionPose):Pose=PanelPoseCodec.toSdk(json(pose))
    }
}
