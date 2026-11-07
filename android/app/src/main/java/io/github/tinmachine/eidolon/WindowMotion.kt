package io.github.tinmachine.eidolon

import kotlin.math.abs
import kotlin.math.sqrt

/** Immutable rigid poses. Quaternion order is xyzw; no SDK/Android object enters the capture model. */
data class WindowMotionPose(val p: List<Double>,val q: List<Double>) {
    fun valid(window:Boolean=false):Boolean=p.size==3 && q.size==4 && p.all {it.isFinite() && abs(it)<=100} &&
        q.all {it.isFinite()} && abs(q.sumOf {it*it}-1.0)<.001 && (!window || p[0] in -10.0..10.0 && p[1] in 0.0..4.0 && p[2] in -10.0..10.0)
    fun inverse():WindowMotionPose {check(valid());val inverse=listOf(-q[0],-q[1],-q[2],q[3]);return WindowMotionPose(rotate(inverse,p.map {-it}),inverse)}
    operator fun times(other:WindowMotionPose):WindowMotionPose {
        check(valid() && other.valid())
        val r=multiply(q,other.q);val norm=sqrt(r.sumOf {it*it})
        return WindowMotionPose(p.zip(rotate(q,other.p)).map {(a,b)->a+b},r.map {it/norm})
    }
    fun near(other:WindowMotionPose,epsilon:Double=.0001)=valid() && other.valid() && p.zip(other.p).all {(a,b)->abs(a-b)<=epsilon} &&
        listOf(1.0,-1.0).any {sign -> q.zip(other.q).all {(a,b)->abs(a-sign*b)<=epsilon}}
    companion object {
        fun at(x:Double=0.0,y:Double=0.0,z:Double=0.0)=WindowMotionPose(listOf(x,y,z),listOf(0.0,0.0,0.0,1.0))
        private fun multiply(a:List<Double>,b:List<Double>)=listOf(
            a[3]*b[0]+a[0]*b[3]+a[1]*b[2]-a[2]*b[1],
            a[3]*b[1]-a[0]*b[2]+a[1]*b[3]+a[2]*b[0],
            a[3]*b[2]+a[0]*b[1]-a[1]*b[0]+a[2]*b[3],
            a[3]*b[3]-a[0]*b[0]-a[1]*b[1]-a[2]*b[2])
        private fun rotate(q:List<Double>,p:List<Double>):List<Double> {
            val a=multiply(q,listOf(p[0],p[1],p[2],0.0));return multiply(a,listOf(-q[0],-q[1],-q[2],q[3])).take(3)
        }
    }
}

data class WindowMoveIdentity(val runtime:Long,val target:String,val revision:Long,val intent:Long,val source:Long,val downTime:Long)

/** Remember a bounded set of source press epochs across completed moves; old DOWN cannot start again. */
class WindowMoveStarts {
    private val latest=linkedMapOf<Long,Long>()
    fun accept(source:Long,downTime:Long):Boolean {
        if(source==0L || downTime<0 || latest[source]?.let {downTime<=it}==true || source !in latest && latest.size>=8) return false
        latest[source]=downTime;return true
    }
}

/** Preview is never persisted. One release can submit once; ACK/model/SDK geometry are separate observations. */
class WindowMoveCapture(val identity:WindowMoveIdentity,sourcePose:WindowMotionPose,val initial:WindowMotionPose,now:Long) {
    enum class Phase { DRAGGING, NEEDS_SUBMIT, COMMITTING, GEOMETRY, COMPLETE, CANCELLED }
    var phase=Phase.DRAGGING;private set
    var reason="dragging";private set
    var pose=initial;private set
    var request:String?=null;private set
    private val relative:WindowMotionPose
    private var deadline=now+60000
    private var acknowledged=false
    init {require(identity.runtime>0 && identity.source!=0L && identity.downTime>=0 && now>=0 && sourcePose.valid() && initial.valid(true));relative=sourcePose.inverse()*initial}
    fun cancel(reason:String) {if(phase !in setOf(Phase.COMPLETE,Phase.CANCELLED)) {phase=Phase.CANCELLED;this.reason=reason}}
    fun current(owner:WindowMoveIdentity,now:Long):Boolean {
        if(identity!=owner) cancel("owner_changed")
        if(now>=deadline) cancel("deadline")
        return phase !in setOf(Phase.CANCELLED,Phase.COMPLETE)
    }
    fun preview(owner:WindowMoveIdentity,sourcePose:WindowMotionPose,now:Long):WindowMotionPose? {
        if(!current(owner,now) || phase!=Phase.DRAGGING) return null
        if(!sourcePose.valid()) {cancel("source_pose_unavailable");return null}
        val next=sourcePose*relative
        if(!next.valid(true)) {cancel("invalid_window_pose");return null}
        pose=next;return pose
    }
    fun release(owner:WindowMoveIdentity,now:Long):Boolean {
        if(!current(owner,now) || phase!=Phase.DRAGGING) return false
        phase=Phase.NEEDS_SUBMIT;reason="release";deadline=now+5000;return true
    }
    fun submitted(id:String,now:Long):Boolean {
        if(!current(identity,now) || phase!=Phase.NEEDS_SUBMIT || id.toLongOrNull()?.let {it>0}!=true) return false
        request=id;phase=Phase.COMMITTING;reason="native_ack_pending";return true
    }
    fun observe(owner:WindowMoveIdentity,now:Long,code:String?,modelCommand:String?,modelPose:WindowMotionPose,geometryApplied:Boolean):Boolean {
        if(!current(owner,now)) return false
        if(phase !in setOf(Phase.COMMITTING,Phase.GEOMETRY)) return false
        if(code!=null && code!="pending") {
            if(code!="ACCEPTED") {cancel("native_rejected");return false}
            acknowledged=true
        }
        if(acknowledged && modelCommand==request && pose.near(modelPose)) {phase=Phase.GEOMETRY;reason="geometry_pending"}
        if(phase==Phase.GEOMETRY && modelCommand==request && pose.near(modelPose) && geometryApplied) {phase=Phase.COMPLETE;reason="pose_applied";return true}
        return false
    }
}

/** A badge describes this window's delivery gate, without copying text or candidate content. */
enum class WindowMode(val label:String) {
    PREPARING("準備中"), PAUSED("入力停止"), EDITING("配置編集"), MOVING("移動中"), PLACING("配置を確認中"),
    IME("IME入力の検証中"), IME_WAIT("IME終了待ち"), PANEL("設定欄へ入力"), NO_KEYBOARD("キーボード未検出"), SELECT("入力先を選択"), RAW("キー入力先");
    companion object {
        fun from(targetReady:Boolean,visible:Boolean,editing:Boolean,move:WindowMoveCapture.Phase?,globalReason:String,focused:Boolean):WindowMode=when {
            !visible -> PAUSED
            editing -> EDITING
            move==WindowMoveCapture.Phase.DRAGGING -> MOVING
            move in setOf(WindowMoveCapture.Phase.NEEDS_SUBMIT,WindowMoveCapture.Phase.COMMITTING,WindowMoveCapture.Phase.GEOMETRY) -> PLACING
            globalReason in setOf("IME_CONNECTING","IME_CLOSING","IME_RECOVERY") -> IME_WAIT
            globalReason=="IME_CONNECTED" && focused -> IME
            globalReason in setOf("PANEL_INPUT","IME_PROBE") -> PANEL
            globalReason in setOf("DISCONNECTED","BACKGROUND") -> PAUSED
            globalReason in setOf("CONNECTING","PREPARING","TARGET_NOT_READY","NO_WINDOW") -> PREPARING
            !targetReady -> PREPARING
            globalReason=="NO_KEYBOARD" -> NO_KEYBOARD
            focused && globalReason in setOf("READY","DEVICE_UNKNOWN") -> RAW
            else -> SELECT
        }
    }
}
