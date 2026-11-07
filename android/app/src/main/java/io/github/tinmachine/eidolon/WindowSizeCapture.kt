package io.github.tinmachine.eidolon

import kotlin.math.abs

/** The same f32 calculation as native PhysicalStep; neither axis is clamped independently. */
data class WindowPhysicalSize(val width:Float,val height:Float) {
    fun valid()=width.isFinite() && height.isFinite() && width in .25f..3f && height in .25f..3f
    fun step(larger:Boolean):WindowPhysicalSize? {
        if(!valid()) return null
        val factor=if(larger) 1.1f else 1f/1.1f
        return WindowPhysicalSize(width*factor,height*factor).takeIf {it.valid()}
    }
    fun near(other:WindowPhysicalSize)=valid() && other.valid() && abs(width-other.width)<.00001f && abs(height-other.height)<.00001f
}

data class WindowSizeIdentity(val runtime:Long,val target:String,val revision:Long,val intent:Long)

/** UI removal after submit does not end the operation. Native, model, SDK and fresh raw focus have separate gates. */
class WindowSizeCapture(val identity:WindowSizeIdentity,val initial:WindowPhysicalSize,val initialCommand:String?,
    val initialEpoch:Long,now:Long) {
    enum class Phase { RESETTING, MENU, NEEDS_SUBMIT, COMMITTING, GEOMETRY, RETURNING, COMPLETE, CANCELLED }
    var phase=Phase.RESETTING;private set
    var reason="reset_pending";private set
    var request:String?=null;private set
    var desired:WindowPhysicalSize?=null;private set
    var larger:Boolean?=null;private set
    private var deadline=now+15000
    private var acknowledged=false
    private var returnEpoch:Long?=null
    init {require(identity.runtime>0 && identity.revision>=0 && identity.intent>=0 && initial.valid() && initialEpoch>=0 && now>=0)}
    fun cancel(reason:String) {if(phase !in setOf(Phase.COMPLETE,Phase.CANCELLED)) {phase=Phase.CANCELLED;this.reason=reason}}
    fun current(owner:WindowSizeIdentity,now:Long):Boolean {
        if(owner!=identity) cancel("owner_changed")
        if(now>=deadline) cancel("timeout")
        return phase !in setOf(Phase.CANCELLED,Phase.COMPLETE)
    }
    fun resetObserved(owner:WindowSizeIdentity,now:Long,code:String?,androidReleased:Boolean,epoch:Long):Boolean {
        if(!current(owner,now) || phase!=Phase.RESETTING) return false
        if(code!=null && code!="pending" && code!="ACCEPTED") {cancel("reset_rejected");return false}
        if(code=="ACCEPTED") acknowledged=true
        if(acknowledged && androidReleased && epoch>initialEpoch) {phase=Phase.MENU;reason="menu";return true}
        return false
    }
    fun choose(owner:WindowSizeIdentity,now:Long,grow:Boolean,modelCommand:String?,model:WindowPhysicalSize):Boolean {
        if(!current(owner,now) || phase!=Phase.MENU) return false
        if(modelCommand!=initialCommand || !initial.near(model)) {cancel("size_changed");return false}
        val next=initial.step(grow) ?: return false
        larger=grow;desired=next;phase=Phase.NEEDS_SUBMIT;reason="submit_pending";acknowledged=false;deadline=now+10000;return true
    }
    fun submitted(id:String,now:Long):Boolean {
        if(!current(identity,now) || phase!=Phase.NEEDS_SUBMIT || id.toLongOrNull()?.let {it>0}!=true) return false
        request=id;phase=Phase.COMMITTING;reason="native_ack_pending";return true
    }
    fun observed(owner:WindowSizeIdentity,now:Long,code:String?,modelCommand:String?,model:WindowPhysicalSize,geometryApplied:Boolean,dimensionsUnchanged:Boolean):Boolean {
        if(!current(owner,now) || phase !in setOf(Phase.COMMITTING,Phase.GEOMETRY)) return false
        if(!dimensionsUnchanged) {cancel("pixel_dimensions_changed");return false}
        if(code!=null && code!="pending") {if(code!="ACCEPTED") {cancel("native_rejected");return false};acknowledged=true}
        if(acknowledged && modelCommand==request && desired?.near(model)==true) {phase=Phase.GEOMETRY;reason="geometry_pending"}
        if(phase==Phase.GEOMETRY && modelCommand==request && desired?.near(model)==true && geometryApplied) {reason="geometry_applied";return true}
        return false
    }
    fun beginReturn(owner:WindowSizeIdentity,now:Long,epoch:Long):Boolean {
        if(!current(owner,now) || phase !in setOf(Phase.MENU,Phase.GEOMETRY) || epoch<initialEpoch) return false
        returnEpoch=epoch;phase=Phase.RETURNING;reason="raw_focus_pending";deadline=now+10000;return true
    }
    fun focusObserved(owner:WindowSizeIdentity,now:Long,epoch:Long,focused:Boolean):Boolean {
        if(!current(owner,now) || phase!=Phase.RETURNING || !focused || epoch<=checkNotNull(returnEpoch)) return false
        phase=Phase.COMPLETE;reason="raw_focus_restored";return true
    }
}
