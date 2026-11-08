package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class ImeProductProbeStateTest {
    private val target=ImeSdkTarget("1","2","3","4")
    private fun starting()=ImeProductObservation(true,"7",10,target,true,false,true,"WAITING_TARGET","ENABLING",true,true,false,false,false,"not_requested",false)
    private fun bound()=starting().copy(ownerBeforeView=false,phase="BOUND",connectionPhase="BOUND",androidReleased=false,viewFocus=true,windowFocus=true,connectionActive=true)
    private fun stopped()=starting().copy(active=false,stopped=true,ownerBeforeView=false,phase="STOPPED",connectionPhase="CLOSED",nativeBlocked=false,rawReturn="COMPLETE",rawFocusSameTarget=true)
    @Test fun onRequiresInitialOwnerBeforeViewAndAllBoundObservations() {
        val state=checkNotNull(ImeProductProbeState.admit(null,starting(),true,0));assertTrue(state.on)
        state.observe(bound().copy(windowFocus=false),100);assertEquals(ImeProductProbeState.Outcome.RUNNING,state.outcome)
        state.observe(bound().copy(connectionActive=false),200);assertEquals(ImeProductProbeState.Outcome.RUNNING,state.outcome)
        state.observe(bound(),300);assertEquals(ImeProductProbeState.Outcome.OBSERVED,state.outcome);assertEquals("normal_bound",state.reason)
    }
    @Test fun priorClosedReportAndAlreadyAttachedViewDoNotProveNormalStartup() {
        assertNull(ImeProductProbeState.admit(stopped(),starting(),true,0))
        assertNull(ImeProductProbeState.admit(null,bound(),true,0))
        assertNull(ImeProductProbeState.admit(null,starting(),false,0))
        assertNull(ImeProductProbeState.admit(null,starting().copy(normal=false),true,0))
        assertNotNull(ImeProductProbeState.admit(stopped(),starting().copy(run="8"),true,0))
    }
    @Test fun offWaitsForNativeAndroidControllerAndExactRawTargetSeparately() {
        for(partial in listOf(stopped().copy(nativeBlocked=true),stopped().copy(androidReleased=false),
            stopped().copy(active=true,stopped=false),stopped().copy(rawFocusSameTarget=false),stopped().copy(rawReturn="WAITING_FOCUS",stopped=false))) {
            val state=checkNotNull(ImeProductProbeState.admit(bound(),bound(),true,0));assertFalse(state.on)
            state.observe(partial,100);assertEquals(ImeProductProbeState.Outcome.RUNNING,state.outcome)
            state.observe(stopped(),200);assertEquals(ImeProductProbeState.Outcome.OBSERVED,state.outcome)
        }
    }
    @Test fun staleRunRuntimeSceneGenerationAndOtherProbeNeverSupplySuccess() {
        for(wrong in listOf(bound().copy(run="8"),bound().copy(runtime=11),bound().copy(target=target.copy(scene="8")),
            bound().copy(target=target.copy(generation="8")),bound().copy(normal=false))) {
            val state=checkNotNull(ImeProductProbeState.admit(null,starting(),true,0))
            state.observe(wrong,100);state.observe(bound(),200)
            assertEquals(ImeProductProbeState.Outcome.FAILED,state.outcome);assertEquals("owner_changed",state.reason)
        }
    }
    @Test fun retainedCompleteCannotBorrowAnOldRuntimeIntentOrFocusedWindow() {
        for(stale in listOf(stopped().copy(runtimeCurrent=false),stopped().copy(inputCurrent=false))) {
            val state=checkNotNull(ImeProductProbeState.admit(bound(),bound(),true,0))
            state.observe(stale,100);state.observe(stopped(),200)
            assertEquals(ImeProductProbeState.Outcome.FAILED,state.outcome);assertEquals("owner_changed",state.reason)
        }
        val state=checkNotNull(ImeProductProbeState.admit(bound(),bound(),true,0))
        state.observe(stopped().copy(rawFocusSameTarget=false),100)
        assertEquals(ImeProductProbeState.Outcome.RUNNING,state.outcome)
    }
    @Test fun lateBoundAndLateRawReturnNeverTurnTimeoutIntoSuccess() {
        val on=checkNotNull(ImeProductProbeState.admit(null,starting(),true,0))
        on.observe(bound(),15000);on.observe(bound(),15001)
        assertEquals(ImeProductProbeState.Outcome.TIMED_OUT,on.outcome)
        val off=checkNotNull(ImeProductProbeState.admit(bound(),bound(),true,0))
        off.observe(stopped(),15000);assertEquals(ImeProductProbeState.Outcome.TIMED_OUT,off.outcome)
    }
    @Test fun failedAndCancelledCleanupRemainDistinctFromPending() {
        val on=checkNotNull(ImeProductProbeState.admit(null,starting(),true,0))
        on.observe(starting().copy(connectionPhase="CLOSING"),100);assertEquals("closed_before_bound",on.reason)
        for(status in listOf("FAILED","CANCELLED")) {
            val off=checkNotNull(ImeProductProbeState.admit(bound(),bound(),true,0));off.observe(stopped().copy(rawReturn=status),100)
            assertEquals(ImeProductProbeState.Outcome.FAILED,off.outcome);assertEquals("raw_return_unconfirmed",off.reason)
        }
    }
    @Test fun invalidClockAndTightLoopAreFinite() {
        val backwards=ImeProductProbeState(starting(),true,100);backwards.observe(starting(),99)
        assertEquals("clock_reversed",backwards.reason)
        val state=ImeProductProbeState(starting(),true,0);repeat(181) {state.observe(starting(),0)}
        assertEquals(ImeProductProbeState.Outcome.TIMED_OUT,state.outcome)
    }
    @Test fun nonceBudgetRejectsReplayEvenAfterItIsFullWithoutEviction() {
        val budget=ImeProductNonceBudget()
        repeat(64) {assertTrue(budget.claim(it.toString(16).padStart(32,'0')))}
        assertFalse(budget.claim("f".repeat(32)));assertFalse(budget.claim("0".repeat(32)))
        for(value in listOf("a".repeat(31),"A".repeat(32),"x".repeat(32))) assertFalse(budget.claim(value))
    }
}
