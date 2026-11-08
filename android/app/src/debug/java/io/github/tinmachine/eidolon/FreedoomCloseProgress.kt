package io.github.tinmachine.eidolon

/** base epochを取り直さず、送信したclose最大2件と自身target退役1回だけを待つ。 */
internal class FreedoomCloseProgress(private val baseEpoch:Long) {
    enum class State { WAITING, COMPLETE, REJECTED }
    private val requests=mutableListOf<String>()
    private var began=0L;private var previousTime=0L;private var observed=baseEpoch;private var retiring=false;private var acked=false
    var state=State.WAITING;private set
    var reason="WAIT_CLOSE";private set
    init {require(baseEpoch in 0..Long.MAX_VALUE-3)}
    fun queued(request:String,now:Long) {
        check(state!=State.REJECTED && requests.size<2 && (requests.isEmpty() || state==State.COMPLETE) && request.matches(Regex("[1-9][0-9]{0,18}")) && request !in requests && now>=previousTime)
        requests.add(request);began=now;previousTime=now;state=State.WAITING;reason="WAIT_CLOSE"
    }
    fun observe(now:Long,epoch:Long,completions:Map<String,String>,ownTargetRetiring:Boolean):State {
        if(state==State.REJECTED) return state
        fun reject(code:String):State {state=State.REJECTED;reason=code;return state}
        if(requests.isEmpty() || now<previousTime) return reject("CLOSE_CLOCK_OR_REQUEST")
        previousTime=now
        if(epoch<observed || epoch<baseEpoch) return reject("CLOSE_EPOCH_REGRESSED")
        // 退役はsealed InputTrialに事前登録した一つのfull target消滅だけを呼出元が渡す。
        if(retiring && !ownTargetRetiring) return reject("CLOSE_RETIREMENT_REGRESSED")
        retiring=ownTargetRetiring
        if(epoch>baseEpoch+requests.size+(if(retiring) 1 else 0)) return reject("CLOSE_EPOCH_EXCESS")
        if(requests.any {completions[it]!=null && completions[it]!="ACCEPTED"}) return reject("CLOSE_ACK_REJECTED")
        if(state!=State.COMPLETE && now-began>=6000) return reject("CLOSE_PROGRESS_TIMEOUT")
        observed=epoch;acked=requests.all {completions[it]=="ACCEPTED"}
        state=if(acked && epoch>=baseEpoch+requests.size) State.COMPLETE else State.WAITING
        reason=if(state==State.COMPLETE) "CLOSE_COMPLETE" else "WAIT_CLOSE"
        return state
    }
    fun metadata():Map<String,Any> = linkedMapOf("schema" to 1,"state" to state.name.lowercase(),"reason" to reason,
        "base_epoch" to baseEpoch,"observed_epoch" to observed,"close_requests" to requests.size,"retirement_bound" to retiring,"all_acknowledged" to acked,
        "elapsed_ms" to (previousTime-began).coerceIn(0,6000))
}
