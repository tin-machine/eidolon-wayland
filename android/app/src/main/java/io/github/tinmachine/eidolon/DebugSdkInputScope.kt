package io.github.tinmachine.eidolon

/** 固定leaseの入力能力。profile/credential本文は保持せず、次runtimeへ付け替えない。 */
internal class DebugSdkInputScope(val owner:Any, val runtime:Long, val session:String, val nonce:String,
    val case:String, val scene:Long) {
    init {
        require(runtime>0 && scene>0 && session.matches(Regex("[1-9][0-9]{0,18}")) && session.toLongOrNull()!=null)
        require(nonce.matches(Regex("[0-9a-f]{16}")) && case in setOf("input","dialog","resize"))
    }
    private var trialClaimed=false
    @Synchronized fun claimTrial():Boolean = (!trialClaimed).also {if(it) trialClaimed=true}
    val mode get()="linux_gtk_$case"
    val appId get()="io.github.tinmachine.eidolon.gtkfixture.r$nonce$nonce"
    fun current(candidate:Any,metadata:DebugSdkRuntimeLease.Metadata,handle:Long,currentScene:Long):Boolean =
        candidate===owner && metadata.phase==DebugSdkRuntimeLease.Phase.ACTIVE && metadata.runtime==runtime &&
            handle==runtime && currentScene==scene
    fun permitsApp(id:String):Boolean = id==appId || (case=="dialog" && id=="$appId.dialog")
    override fun toString()="DebugSdkInputScope(<owner>)"
}
