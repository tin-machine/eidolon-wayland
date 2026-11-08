package io.github.tinmachine.eidolon

/** Menu permission is separate from application input. Only a verified native report may construct it. */
internal class WindowGroupMenuPermit private constructor(val target:InputWindowKey,val controlsRevision:Long,
    val group:String?,val revision:String?,val layout:String,private val members:Set<InputWindowKey>) {
    val actions:Set<String> get()=if(group==null || layout=="expanded") setOf("collapse") else if(members.size>1) setOf("expand","next_front") else setOf("expand")
    fun same(other:WindowGroupMenuPermit)=target==other.target && controlsRevision==other.controlsRevision &&
        group==other.group && revision==other.revision && layout==other.layout && members==other.members
    companion object {
        private fun id(value:String)=value.matches(Regex("[1-9][0-9]{0,18}")) && value.toLongOrNull()!=null
        fun create(target:InputWindowKey,controlsRevision:Long,group:String?,revision:String?,layout:String,
            phase:String,sourceReady:Boolean,front:Boolean,members:List<InputWindowKey>):WindowGroupMenuPermit? {
            if(controlsRevision<0 || !sourceReady || !front || members.size !in 1..9 || group==null && members.size<2 || members.toSet().size!=members.size || target !in members ||
                members.map {it.window}.toSet().size!=members.size || members.any {key->key.runtime!=target.runtime || key.runtime<=0 ||
                    key.session!=target.session || key.scene!=target.scene || !listOf(key.session,key.window,key.scene,key.generation).all(::id)}) return null
            if((group==null)!=(revision==null) || layout !in setOf("expanded","collapsed") || phase !in setOf("stable","animating","settling")) return null
            if(group==null && (layout!="expanded" || phase!="stable") || group!=null && (!id(group) || !id(checkNotNull(revision)))) return null
            return WindowGroupMenuPermit(target,controlsRevision,group,revision,layout,members.toSet())
        }
    }
}

internal data class WindowGroupMenuIdentity(val target:InputWindowKey,val controlsRevision:Long,val intent:Long)

/** One reset, one menu selection and one enqueue. Command acceptance is separate from group geometry completion. */
internal class WindowGroupMenuCapture(val identity:WindowGroupMenuIdentity,val permit:WindowGroupMenuPermit,val initialEpoch:Long,now:Long) {
    enum class Phase { RESETTING, MENU, CLEANUP, SUBMITTING, COMMITTING, ACCEPTED, DISMISSED, CANCELLED }
    var phase=Phase.RESETTING;private set
    var reason="reset_pending";private set
    var action:String?=null;private set
    var request:String?=null;private set
    private var acknowledged=false
    private var lastNow=now
    private var deadline=limit(now,15000)
    private fun limit(now:Long,interval:Long)=if(now>Long.MAX_VALUE-interval) Long.MAX_VALUE else now+interval
    init {require(identity.target==permit.target && identity.controlsRevision==permit.controlsRevision && identity.intent>=0 && initialEpoch>=0 && now>=0)}
    private fun active()=phase !in setOf(Phase.ACCEPTED,Phase.DISMISSED,Phase.CANCELLED)
    fun cancel(reason:String) {if(active()) {phase=Phase.CANCELLED;this.reason=reason}}
    fun current(owner:WindowGroupMenuIdentity,now:Long,current:WindowGroupMenuPermit?):Boolean {
        if(owner!=identity || current==null || !permit.same(current)) cancel("owner_changed")
        if(now<lastNow) cancel("time_reversed")
        if(now>=deadline) cancel("timeout")
        lastNow=maxOf(lastNow,now)
        return active()
    }
    fun resetObserved(owner:WindowGroupMenuIdentity,now:Long,current:WindowGroupMenuPermit?,code:String?,androidReleased:Boolean,epoch:Long):Boolean {
        if(!current(owner,now,current) || phase!=Phase.RESETTING) return false
        if(code!=null && code!="pending" && code!="ACCEPTED") {cancel("reset_rejected");return false}
        if(code=="ACCEPTED") acknowledged=true
        if(acknowledged && androidReleased && epoch>initialEpoch) {phase=Phase.MENU;reason="menu";return true}
        return false
    }
    fun choose(owner:WindowGroupMenuIdentity,now:Long,current:WindowGroupMenuPermit?,action:String):Boolean {
        if(!current(owner,now,current) || phase!=Phase.MENU || action !in permit.actions+"back") return false
        this.action=action;phase=Phase.CLEANUP;reason="menu_cleanup_pending";deadline=limit(now,10000);return true
    }
    fun cleaned(owner:WindowGroupMenuIdentity,now:Long,current:WindowGroupMenuPermit?,released:Boolean):Boolean {
        if(!current(owner,now,current) || phase!=Phase.CLEANUP || !released) return false
        if(action=="back") {phase=Phase.DISMISSED;reason="dismissed";return false}
        phase=Phase.SUBMITTING;reason="enqueue_pending";return true
    }
    fun submitted(owner:WindowGroupMenuIdentity,now:Long,current:WindowGroupMenuPermit?,request:String?):Boolean {
        if(!current(owner,now,current) || phase!=Phase.SUBMITTING) return false
        if(request==null || !request.matches(Regex("[1-9][0-9]{0,18}")) || request.toLongOrNull()==null) {cancel("enqueue_rejected");return false}
        this.request=request;phase=Phase.COMMITTING;reason="native_ack_pending";return true
    }
    /** Once submitted, a native group revision change is expected; only the same owner/request may consume its receipt. */
    fun commandObserved(owner:WindowGroupMenuIdentity,now:Long,ownerCurrent:Boolean,request:String,code:String?):Boolean {
        if(owner!=identity || !ownerCurrent) cancel("owner_changed")
        if(now<lastNow) cancel("time_reversed")
        if(now>=deadline) cancel("timeout")
        lastNow=maxOf(lastNow,now)
        if(!active() || phase!=Phase.COMMITTING || request!=this.request) return false
        if(code==null || code=="pending") return false
        if(code!="ACCEPTED") {cancel("native_rejected");return false}
        phase=Phase.ACCEPTED;reason="native_accepted_geometry_pending";return true
    }
}
