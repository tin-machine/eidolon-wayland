package io.github.tinmachine.eidolon

/** Stable dimensions are separate from placement. A recenter never changes logical pixels or scale. */
data class WindowRecenterGeometry(val pose:WindowMotionPose,val poseCommand:String?,val physical:WindowPhysicalSize,
    val sizeCommand:String?,val pixels:String) {
    fun valid()=pose.valid(true) && physical.valid() && pixels.isNotEmpty() && pixels.length<=4096 &&
        listOf(poseCommand,sizeCommand).all {it==null || it.matches(Regex("[1-9][0-9]{0,18}")) && it.toLongOrNull()!=null}
    fun sameDimensions(other:WindowRecenterGeometry)=physical.near(other.physical) && sizeCommand==other.sizeCommand && pixels==other.pixels
    fun samePlacement(other:WindowRecenterGeometry)=poseCommand==other.poseCommand && pose.near(other.pose)
}

/** One explicit placement after the menu's reset/Android-release gate. Never retries or resamples the viewer. */
class WindowRecenterCapture(val identity:WindowSizeIdentity,val initial:WindowRecenterGeometry,
    val desired:WindowMotionPose,val initialEpoch:Long,now:Long,val scene:WindowRecenterScene?=null) {
    enum class Phase { NEEDS_SUBMIT, COMMITTING, GEOMETRY, RETURNING, COMPLETE, CANCELLED }
    var phase=Phase.NEEDS_SUBMIT;private set
    var reason="recenter_submit_pending";private set
    var request:String?=null;private set
    private var stageStarted=now
    private var lastTime=now
    private var acknowledged=false
    private var returnEpoch:Long?=null
    init {require(identity.runtime>0 && identity.revision>=0 && identity.intent>=0 && identity.target.isNotEmpty() &&
        initial.valid() && desired.valid(true) && initialEpoch in 0..Long.MAX_VALUE-2 && now>=0)}
    fun cancel(reason:String) {if(phase !in setOf(Phase.COMPLETE,Phase.CANCELLED)) {phase=Phase.CANCELLED;this.reason=reason}}
    fun current(owner:WindowSizeIdentity,now:Long):Boolean {
        if(phase in setOf(Phase.COMPLETE,Phase.CANCELLED)) return false
        if(owner!=identity) cancel("owner_changed")
        if(now<lastTime) cancel("clock_reversed")
        if(now>=stageStarted && now-stageStarted>=10000) cancel("recenter_timeout")
        lastTime=now
        return phase !in setOf(Phase.COMPLETE,Phase.CANCELLED)
    }
    fun readyToSubmit(owner:WindowSizeIdentity,now:Long,model:WindowRecenterGeometry,epoch:Long):Boolean {
        if(!current(owner,now) || phase!=Phase.NEEDS_SUBMIT) return false
        if(now-stageStarted>=250) {cancel("viewer_sample_expired");return false}
        if(!model.valid() || !initial.sameDimensions(model) || !initial.samePlacement(model) || epoch!=initialEpoch) {
            cancel("recenter_baseline_changed");return false
        }
        return true
    }
    fun submitted(id:String,now:Long):Boolean {
        if(!current(identity,now) || phase!=Phase.NEEDS_SUBMIT || !id.matches(Regex("[1-9][0-9]{0,18}")) || id.toLongOrNull()==null) return false
        request=id;phase=Phase.COMMITTING;reason="recenter_native_ack_pending";return true
    }
    fun observed(owner:WindowSizeIdentity,now:Long,code:String?,model:WindowRecenterGeometry,epoch:Long,geometryApplied:Boolean):Boolean {
        if(!current(owner,now) || phase !in setOf(Phase.COMMITTING,Phase.GEOMETRY)) return false
        if(!model.valid() || !initial.sameDimensions(model)) {cancel("recenter_dimensions_changed");return false}
        // SetPoseControl owns exactly one urgent input reset; no unrelated new epoch is adopted.
        if(epoch<initialEpoch || epoch>initialEpoch+1) {cancel("recenter_epoch_changed");return false}
        if(code!=null && code!="pending") {if(code!="ACCEPTED") {cancel("recenter_native_rejected");return false};acknowledged=true}
        val acceptedPose=model.poseCommand==request && desired.near(model.pose)
        if(!acceptedPose && !initial.samePlacement(model)) {cancel("recenter_pose_changed");return false}
        if(acknowledged && acceptedPose && epoch==initialEpoch+1) {phase=Phase.GEOMETRY;reason="recenter_geometry_pending"}
        if(phase==Phase.GEOMETRY && acceptedPose && geometryApplied) {reason="recenter_geometry_applied";return true}
        return false
    }
    fun beginReturn(owner:WindowSizeIdentity,now:Long,epoch:Long):Boolean {
        if(!current(owner,now) || phase!=Phase.GEOMETRY || reason!="recenter_geometry_applied" || epoch!=initialEpoch+1) return false
        returnEpoch=epoch;phase=Phase.RETURNING;reason="raw_focus_pending";stageStarted=now;return true
    }
    fun focusObserved(owner:WindowSizeIdentity,now:Long,epoch:Long,focused:Boolean,geometry:WindowRecenterGeometry):Boolean {
        if(!current(owner,now) || phase!=Phase.RETURNING) return false
        if(!geometry.valid() || !initial.sameDimensions(geometry) || geometry.poseCommand!=request || !desired.near(geometry.pose)) {
            cancel("recenter_geometry_changed");return false
        }
        if(epoch<checkNotNull(returnEpoch) || epoch>checkNotNull(returnEpoch)+1) {cancel("recenter_epoch_changed");return false}
        if(!focused || epoch!=checkNotNull(returnEpoch)+1) return false
        phase=Phase.COMPLETE;reason="raw_focus_restored";return true
    }
}
