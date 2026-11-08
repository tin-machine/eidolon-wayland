package io.github.tinmachine.eidolon

/** enqueue受理とcoreの実command受理を分け、現在caseの一度だけの要求に束縛する。 */
internal class NormalLinuxGtkRequests {
    enum class Kind { CONNECT, INSPECT, LAUNCH }
    data class Rejected(val kind:Kind,val mayForgetLaunch:Boolean)
    private class Ticket(val request:String,var completed:Boolean=false)
    private val tickets=linkedMapOf<Kind,Ticket>()
    private var awaiting:Kind?=null
    private val early=linkedMapOf<String,String>()
    fun begin(kind:Kind) {
        check(awaiting==null && kind !in tickets) {"LINUX_REQUEST_REPLAY"}
        awaiting=kind;early.clear()
    }
    fun enqueueRejected(kind:Kind) {
        check(awaiting==kind) {"LINUX_REQUEST_SCOPE"}
        awaiting=null;early.clear()
    }
    fun request(kind:Kind)=tickets[kind]?.request
    fun enqueued(kind:Kind,request:String,launchObserved:Boolean=false):Rejected? {
        require(request.matches(Regex("[1-9][0-9]{0,18}")) && request.toLongOrNull()!=null)
        check(awaiting==kind && kind !in tickets && tickets.values.none {it.request==request}) {"LINUX_REQUEST_REPLAY"}
        tickets[kind]=Ticket(request)
        val completion=early[request];awaiting=null;early.clear()
        return completion?.let {completed(request,it,launchObserved)}
    }
    fun completed(request:String,code:String,launchObserved:Boolean):Rejected? {
        val entry=tickets.entries.singleOrNull {it.value.request==request}
        if(entry==null) {
            if(awaiting!=null && request.matches(Regex("[1-9][0-9]{0,18}")) && request !in early) {
                check(early.size<8) {"LINUX_COMPLETION_OVERFLOW"}
                early[request]=code
            }
            return null
        }
        val (kind,ticket)=entry
        if(ticket.completed) return null
        ticket.completed=true
        if(code=="ACCEPTED") return null
        return Rejected(kind,kind==Kind.LAUNCH && !launchObserved)
    }
}
