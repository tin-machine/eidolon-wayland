package io.github.tinmachine.eidolon

/** IDを補完せず、runtimeと同一windowの一度だけの世代交換を追跡する。 */
internal data class InputWindowKey(val runtime:Long,val session:String,val window:String,val scene:String,val generation:String) {
    val identity:String get()="$session:$window:$scene:$generation"
    val retirement:InputRetirement get()=InputRetirement(runtime,identity)
    fun sameWindow(other:InputWindowKey)=runtime==other.runtime && session==other.session && window==other.window && scene==other.scene
}
internal class InputResizeReplacement(val request:Long,val old:InputWindowKey,val epoch:Long,val width:Int,val height:Int,val deadline:Long) {
    data class Proof(val target:InputWindowKey,val epoch:Long,val receiptMatches:Boolean,val sizesApplied:Boolean,
        val serialsPresent:Boolean,val producerAdvanced:Boolean,val inputUnchanged:Boolean,val resourcesReady:Boolean)
    var acknowledged=false;private set
    var retirementSeen=false;private set
    var failure:String?=null;private set
    var completed=false;private set
    private var receiptSeen=false
    fun live(now:Long):Boolean {
        if(now>=deadline && !completed && failure==null) failure="RESIZE_DEADLINE"
        return failure==null && !completed
    }
    fun acknowledge(accepted:Boolean,now:Long) {
        if(live(now)) {if(accepted) acknowledged=true else failure="RESIZE_COMMAND_REJECTED"}
    }
    fun retiring(key:InputRetirement,now:Long):Boolean {
        if(!live(now) || retirementSeen || key!=old.retirement) return false
        retirementSeen=true;return true
    }
    fun observe(proof:Proof,now:Long):Boolean {
        if(!live(now)) return false
        if(!proof.receiptMatches) {
            if(receiptSeen) failure="RESIZE_REQUEST_CHANGED"
            return false // command completion and snapshot publication may be separate ticks.
        }
        receiptSeen=true
        if(!proof.inputUnchanged) {failure="RESIZE_OTHER_INPUT";return false}
        if(!old.sameWindow(proof.target)) {failure="RESIZE_TARGET_CHANGED";return false}
        val replaced=proof.target!=old
        if(replaced && (proof.target.generation.toLongOrNull() ?: 0)<=(old.generation.toLongOrNull() ?: Long.MAX_VALUE)) {
            failure="RESIZE_GENERATION_REUSED";return false
        }
        val delta=proof.epoch-epoch
        if(!replaced && retirementSeen) return false // old snapshot can remain while SDK is retiring.
        if((!replaced && delta!=0L) || (replaced && (delta !in 1L..2L))) {
            // During retirement the new target may precede the reset snapshot; wait for that bounded transition.
            if(replaced && delta==0L) return false
            failure="RESIZE_EPOCH_CHANGED";return false
        }
        if(!acknowledged || (replaced && !retirementSeen) || !proof.sizesApplied || !proof.serialsPresent ||
            !proof.producerAdvanced || !proof.resourcesReady) return false
        completed=true;return true
    }
}
