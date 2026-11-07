package io.github.tinmachine.eidolon

/** PCが実際のlarge snapshotを採取するまでreturnを送らない、一回だけの固定handshake。 */
internal class GtkResizeObservation {
    enum class State { WAITING_LARGE, LARGE_READY, WAIT_RETURN, COMPLETE, CANCELLED }
    var state=State.WAITING_LARGE;private set
    var returnRequests=0;private set
    private var largeTarget:String?=null
    fun largeReady(target:String) {
        check(state==State.WAITING_LARGE && target.isNotEmpty())
        largeTarget=target;state=State.LARGE_READY
    }
    fun requestReturn():Boolean {
        if(state!=State.LARGE_READY || returnRequests!=0) return false
        returnRequests=1;return true
    }
    fun beginReturn(target:String):Boolean {
        if(state!=State.LARGE_READY || returnRequests!=1) return false
        if(target!=largeTarget) {cancel();return false}
        state=State.WAIT_RETURN;return true
    }
    fun complete() {check(state==State.WAIT_RETURN);state=State.COMPLETE}
    fun cancel() {if(state!=State.COMPLETE) state=State.CANCELLED}
}
