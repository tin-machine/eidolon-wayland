package io.github.tinmachine.eidolon

/** 最初のframe同期済みgeometryを保持する。repaintは許し、世代/geometry/epochへ追従しない。 */
internal class DebugSdkInputContinuity(private val runtime:Long,private val epoch:Long,private val session:String,private val scene:String) {
    data class Geometry(val logicalWidth:Int,val logicalHeight:Int,val pixelWidth:Int,val pixelHeight:Int,
        val physicalWidth:Double,val physicalHeight:Double,val pose:List<Double>,val theme:Long) {
        override fun toString()="Geometry(redacted)"
    }
    data class Proof(val key:InputWindowKey,val epoch:Long,val geometry:Geometry,val interactive:Boolean,
        val geometryApplied:Boolean,val sdkOwned:Boolean,val frameReady:Boolean)
    private val captured=linkedMapOf<String,Pair<InputWindowKey,Geometry>>()
    private var failed=false
    private var repaints=0
    private var reason="WAIT_INITIAL_FRAME"
    fun accept(value:Proof):Boolean {
        if(failed) return false
        if(value.key.runtime!=runtime || value.epoch!=epoch || value.key.session!=session || value.key.scene!=scene) return fail("OWNER_CHANGED")
        val slot="${value.key.session}:${value.key.window}:${value.key.scene}"
        val old=captured[slot]
        if(old!=null && (old.first!=value.key || old.second!=value.geometry)) return fail("GEOMETRY_CHANGED")
        if(!value.interactive || !value.geometryApplied || !value.sdkOwned) {
            reason="WAIT_TARGET_READY"
            // 所有済みtargetの喪失は、元に戻っても復活させない。
            return if(old!=null) fail("TARGET_LOST") else false
        }
        if(old==null) {
            if(!value.frameReady) {reason="WAIT_INITIAL_FRAME";return false}
            if(captured.size>=2) return fail("TARGET_BOUND")
            captured[slot]=value.key to value.geometry
        } else if(!value.frameReady) repaints=(repaints+1).coerceAtMost(1000000)
        reason=if(value.frameReady) "TARGET_READY" else "SAME_GEOMETRY_REPAINT"
        return true
    }
    private fun fail(value:String):Boolean {failed=true;reason=value;return false}
    fun metadata():Map<String,Any> = linkedMapOf("schema" to 1,"reason" to reason,"failed" to failed,
        "targets" to captured.size,"repaint_checks" to repaints)
}
