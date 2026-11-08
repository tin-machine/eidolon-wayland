package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class NormalLinuxGtkRequestsTest {
    private fun queued(owner:NormalLinuxGtkRequests,kind:NormalLinuxGtkRequests.Kind,request:String) {
        owner.begin(kind);assertNull(owner.enqueued(kind,request))
    }
    @Test fun coreRejectionRollsBackOnlyItsCurrentEnqueueAcceptedOperation() {
        for(kind in NormalLinuxGtkRequests.Kind.entries) {
            val owner=NormalLinuxGtkRequests();queued(owner,kind,"9")
            assertNull(owner.completed("8","BUSY",false))
            val rejection=owner.completed("9","NOT_READY",false)!!
            assertEquals(kind,rejection.kind)
            assertEquals(kind==NormalLinuxGtkRequests.Kind.LAUNCH,rejection.mayForgetLaunch)
            assertNull(owner.completed("9","NOT_READY",false))
        }
    }
    @Test fun acceptedOrActuallyStartedLaunchCannotBeDiscardedByLateRejection() {
        val accepted=NormalLinuxGtkRequests();queued(accepted,NormalLinuxGtkRequests.Kind.LAUNCH,"9")
        assertNull(accepted.completed("9","ACCEPTED",false))
        assertNull(accepted.completed("9","BUSY",false))
        val observed=NormalLinuxGtkRequests();queued(observed,NormalLinuxGtkRequests.Kind.LAUNCH,"10")
        val rejection=observed.completed("10","BUSY",true)!!
        assertEquals(NormalLinuxGtkRequests.Kind.LAUNCH,rejection.kind);assertFalse(rejection.mayForgetLaunch)
    }
    @Test fun staleKindAndRepeatedEnqueueCannotBeReassignedToAnotherOperation() {
        val owner=NormalLinuxGtkRequests();queued(owner,NormalLinuxGtkRequests.Kind.CONNECT,"1")
        queued(owner,NormalLinuxGtkRequests.Kind.INSPECT,"2");queued(owner,NormalLinuxGtkRequests.Kind.LAUNCH,"3")
        assertEquals(NormalLinuxGtkRequests.Kind.CONNECT,owner.completed("1","BUSY",false)!!.kind)
        assertEquals(NormalLinuxGtkRequests.Kind.INSPECT,owner.completed("2","STALE",false)!!.kind)
        assertTrue(owner.completed("3","NOT_READY",false)!!.mayForgetLaunch)
        assertThrows(IllegalStateException::class.java) {owner.enqueued(NormalLinuxGtkRequests.Kind.LAUNCH,"4")}
        val fresh=NormalLinuxGtkRequests()
        assertNull(fresh.completed("3","NOT_READY",false))
    }
    @Test fun earlyCompletionIsBoundedAndReconcilesOnlyTheCapturedRequestAndKind() {
        val owner=NormalLinuxGtkRequests();owner.begin(NormalLinuxGtkRequests.Kind.CONNECT)
        assertNull(owner.completed("98","FAILED",false))
        assertNull(owner.completed("99","BUSY",false))
        val rejected=owner.enqueued(NormalLinuxGtkRequests.Kind.CONNECT,"99")!!
        assertEquals(NormalLinuxGtkRequests.Kind.CONNECT,rejected.kind)
        assertEquals("99",owner.request(NormalLinuxGtkRequests.Kind.CONNECT)) // 診断の履歴はrollbackで消さない。
        owner.begin(NormalLinuxGtkRequests.Kind.INSPECT)
        assertNull(owner.enqueued(NormalLinuxGtkRequests.Kind.INSPECT,"98")) // 前kindのbufferを再利用しない。
        val bounded=NormalLinuxGtkRequests();bounded.begin(NormalLinuxGtkRequests.Kind.LAUNCH)
        for(id in 1..8) assertNull(bounded.completed(id.toString(),"ACCEPTED",false))
        assertThrows(IllegalStateException::class.java) {bounded.completed("9","BUSY",false)}
    }
    @Test fun earlyAcceptedReceiptAndRejectedEnqueueCannotBecomeLaterRollback() {
        val owner=NormalLinuxGtkRequests();owner.begin(NormalLinuxGtkRequests.Kind.LAUNCH)
        assertNull(owner.completed("9","ACCEPTED",false));assertNull(owner.enqueued(NormalLinuxGtkRequests.Kind.LAUNCH,"9"))
        assertNull(owner.completed("9","NOT_READY",false))
        val rejected=NormalLinuxGtkRequests();rejected.begin(NormalLinuxGtkRequests.Kind.CONNECT)
        assertNull(rejected.completed("8","FAILED",false));rejected.enqueueRejected(NormalLinuxGtkRequests.Kind.CONNECT)
        rejected.begin(NormalLinuxGtkRequests.Kind.INSPECT)
        assertNull(rejected.enqueued(NormalLinuxGtkRequests.Kind.INSPECT,"8"))
    }
}
