package io.github.tinmachine.eidolon

/** Product controller metadata only. Never retains InputConnection text, key events or focus tokens. */
data class ImeProductObservation(val normal:Boolean,val run:String,val runtime:Long,val target:ImeSdkTarget,
    val active:Boolean,val stopped:Boolean,val ownerBeforeView:Boolean,val phase:String,val connectionPhase:String,
    val nativeBlocked:Boolean,val androidReleased:Boolean,val viewFocus:Boolean,val windowFocus:Boolean,val connectionActive:Boolean,
    val rawReturn:String,val rawFocusSameTarget:Boolean,val runtimeCurrent:Boolean=true,val inputCurrent:Boolean=true) {
    fun valid()=normal && runtimeCurrent && inputCurrent && runtime>0 && run.matches(Regex("[1-9][0-9]{0,18}")) && run.toLongOrNull()!=null && target.valid()
    fun sameOwner(other:ImeProductObservation)=run==other.run && runtime==other.runtime && target==other.target && normal==other.normal
}
class ImeProductProbeState(val initial:ImeProductObservation,val on:Boolean,private val started:Long) {
    enum class Outcome { RUNNING, OBSERVED, FAILED, TIMED_OUT }
    var outcome=Outcome.RUNNING;private set
    var reason="pending";private set
    var polls=0;private set
    private var lastTime=started
    init {require(initial.valid() && started>=0)}
    fun observe(current:ImeProductObservation?,now:Long) {
        if(outcome!=Outcome.RUNNING) return
        polls++
        if(now<lastTime) {outcome=Outcome.FAILED;reason="clock_reversed";return}
        lastTime=now
        if(now-started>=15000 || polls>180) {outcome=Outcome.TIMED_OUT;reason="observation_timeout";return}
        if(current==null || !current.valid() || !initial.sameOwner(current)) {outcome=Outcome.FAILED;reason="owner_changed";return}
        if(on) {
            if(current.active && !current.stopped && current.connectionPhase=="BOUND" && current.nativeBlocked &&
                current.viewFocus && current.windowFocus && current.connectionActive) {
                outcome=Outcome.OBSERVED;reason="normal_bound"
            } else if(!current.active || current.stopped || current.connectionPhase in setOf("CLOSING","CLOSED") || current.phase in setOf("FAILED","SDK_CLEANUP")) {
                outcome=Outcome.FAILED;reason="closed_before_bound"
            }
        } else {
            if(!current.active && current.stopped && current.connectionPhase=="CLOSED" && !current.nativeBlocked && current.androidReleased &&
                current.rawReturn=="COMPLETE" && current.rawFocusSameTarget) {
                outcome=Outcome.OBSERVED;reason="normal_direct_restored"
            } else if(current.rawReturn in setOf("FAILED","CANCELLED") || current.phase=="FAILED" || current.stopped && current.rawReturn!="COMPLETE") {
                outcome=Outcome.FAILED;reason="raw_return_unconfirmed"
            }
        }
    }
    companion object {
        /** A replayed last report is insufficient to prove the ON path was started. */
        fun admit(before:ImeProductObservation?,after:ImeProductObservation?,accepted:Boolean,now:Long):ImeProductProbeState? {
            if(!accepted || after?.valid()!=true || now<0) return null
            val on=before?.active!=true
            if(on) {
                if(!after.active || after.stopped || !after.ownerBeforeView || before?.run==after.run) return null
            } else if(before?.valid()!=true || !before.sameOwner(after)) return null
            return ImeProductProbeState(after,on,now)
        }
    }
}

/** At most 64 operations per process. Full budget rejects new work instead of permitting an old replay. */
class ImeProductNonceBudget {
    private val consumed=linkedSetOf<String>()
    fun claim(nonce:String):Boolean {
        if(!nonce.matches(Regex("[0-9a-f]{32}")) || nonce in consumed || consumed.size>=64) return false
        consumed.add(nonce);return true
    }
}
