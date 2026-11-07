package io.github.tinmachine.eidolon

/** 既存trialを維持したまま、新dialog一枚の最初の配送準備だけを有限に待つ。 */
internal class GtkDialogTargetWait(val baseline:Stamp,private val started:Long) {
    data class Stamp(val key:InputWindowKey,val epoch:Long,val keys:Long,val buttons:Long,
        val focusWindow:String?,val focusToken:String?,val geometry:DebugSdkInputContinuity.Geometry)
    private var cancelled=false
    private var polls=0
    private var elapsed=0L
    private var waited=false
    private var ready=false
    private var reason="WAIT_DIALOG_TARGET"

    fun observe(now:Long,value:Stamp,owned:Boolean,unpressed:Boolean,targetReady:Boolean,permanentFailure:Boolean):Boolean {
        if(cancelled) return false
        polls++;elapsed=(now-started).coerceAtLeast(0)
        if(!owned || permanentFailure) return cancel("PROBE_DIALOG_OWNER_CHANGED")
        if(value!=baseline) return cancel("PROBE_DIALOG_BASELINE_CHANGED")
        if(!unpressed) return cancel("PROBE_INPUT_HELD")
        if(now<started || elapsed>=3000 || polls>512) return cancel("PROBE_DIALOG_TARGET_TIMEOUT")
        ready=targetReady;waited=waited || !targetReady
        reason=if(ready) "DIALOG_TARGET_READY" else "WAIT_DIALOG_TARGET"
        return ready
    }
    private fun cancel(value:String):Boolean {cancelled=true;ready=false;reason=value;return false}
    fun metadata():Map<String,Any> = linkedMapOf("schema" to 1,"reason" to reason,"cancelled" to cancelled,
        "ready" to ready,"polls" to polls,"elapsed_ms" to elapsed,"wait_observed" to waited)
}
