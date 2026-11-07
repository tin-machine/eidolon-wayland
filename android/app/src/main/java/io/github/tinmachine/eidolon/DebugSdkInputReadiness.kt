package io.github.tinmachine.eidolon

/** 最初の入力前だけcache/描画を待つ。owner/inputが変わった後の新しい値へ追従しない。 */
internal class DebugSdkInputReadiness(val baseline: Stamp, private val started: Long) {
    data class Stamp(val runtime:Long,val target:String,val epoch:Long,val intent:Long,
        val keys:Long,val buttons:Long,val focusWindow:String?,val focusToken:String?)
    enum class State { WAITING, READY, CAPTURED, CANCELLED }
    var state=State.WAITING;private set
    var reason="WAIT_READINESS";private set
    private var previous=started
    private var polls=0
    private var adapterReady=false
    private var frameReady=false
    private var waitedAdapter=false
    private var waitedFrame=false
    init {require(started>=0 && baseline.runtime>0 && baseline.epoch>=0 && baseline.intent>0 && baseline.keys>=0 && baseline.buttons>=0)}

    fun observe(now:Long,current:Stamp?,ownerCurrent:Boolean,intentCurrent:Boolean,unpressed:Boolean,
        adapter:Boolean,producerFrame:Boolean):State {
        if(state==State.CAPTURED || state==State.CANCELLED) return state
        polls++
        if(now<previous || now<started) return cancel("PROBE_INPUT_CLOCK_CHANGED")
        previous=now
        if(now-started>=3000 || polls>512) return cancel("PROBE_INPUT_PREPARE_TIMEOUT")
        if(!ownerCurrent || current==null) return cancel("PROBE_INPUT_OWNER_CHANGED")
        if(current.runtime!=baseline.runtime || current.target!=baseline.target) return cancel("PROBE_INPUT_TARGET_CHANGED")
        if(!intentCurrent || current!=baseline || !unpressed) return cancel("PROBE_INPUT_CHANGED_DURING_WAIT")
        adapterReady=adapter;frameReady=producerFrame
        waitedAdapter=waitedAdapter || !adapter;waitedFrame=waitedFrame || !producerFrame
        state=if(adapter && producerFrame) State.READY else State.WAITING
        reason=when {state==State.READY->"READY";!adapter && !producerFrame->"WAIT_ADAPTER_AND_FRAME";!adapter->"WAIT_ADAPTER";else->"WAIT_PRODUCER_FRAME"}
        return state
    }
    fun capture():Boolean = (state==State.READY).also {if(it) {state=State.CAPTURED;reason="CAPTURED"}}
    fun cancel(token:String):State {
        require(token in setOf("PROBE_INPUT_CLOCK_CHANGED","PROBE_INPUT_PREPARE_TIMEOUT","PROBE_INPUT_OWNER_CHANGED",
            "PROBE_INPUT_TARGET_CHANGED","PROBE_INPUT_CHANGED_DURING_WAIT","PROBE_INPUT_ALREADY_CLAIMED"))
        if(state!=State.CAPTURED && state!=State.CANCELLED) {state=State.CANCELLED;reason=token}
        return state
    }
    fun metadata():Map<String,Any> = linkedMapOf("state" to state.name.lowercase(),"reason" to reason,
        "polls" to polls,"elapsed_ms" to (previous-started).coerceIn(0,3000),
        "adapter_ready" to adapterReady,"producer_frame_ready" to frameReady,
        "wait_adapter_observed" to waitedAdapter,"wait_frame_observed" to waitedFrame)
}
