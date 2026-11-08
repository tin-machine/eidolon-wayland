package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class WindowGroupInputGateTest {
    private fun target(window:String="1",generation:String="4",runtime:Long=7)=InputWindowKey(runtime,"2",window,"3",generation)
    private fun member(window:String,allowed:Boolean)=WindowGroupInputGate.Member(target(window),allowed)
    @Test fun noActiveGroupKeepsNormalWindowsAndUnknownGuiIndependent() {
        val gate=WindowGroupInputGate(7,false,null,emptyList(),listOf(WindowGroupInputGate.Window("2","1")))
        assertTrue(gate.allows(target()));assertTrue(gate.allows(target("9")))
        assertFalse(gate.allows(target(runtime=8)));assertFalse(gate.allows(target(generation="0")))
    }
    @Test fun movingAndBackMembersCannotReceiveInputOrAdoptAnotherGeneration() {
        val gate=WindowGroupInputGate(7,true,null,listOf(member("1",true),member("2",false)),emptyList())
        assertTrue(gate.allows(target()));assertFalse(gate.allows(target("2")))
        assertFalse(gate.allows(target(generation="5")));assertTrue(gate.allows(target("9")))
    }
    @Test fun classifiedPendingMemberIsBlockedBeforeSdkAdmission() {
        val gate=WindowGroupInputGate(7,true,null,listOf(member("1",false)),listOf(WindowGroupInputGate.Window("2","2")))
        assertFalse(gate.allows(target()));assertFalse(gate.allows(target("2")))
        assertTrue(gate.allows(target("9")))
    }
    @Test fun lostSourceOrRuntimeNeverFallsBackToUiSelectedTarget() {
        val gate=WindowGroupInputGate(7,true,"2",listOf(member("1",true)),emptyList())
        assertFalse(gate.allows(target()));assertFalse(gate.allows(target("9")))
        assertFalse(WindowGroupInputGate.unavailable().allows(target()))
        assertFalse(WindowGroupInputGate.rejected(7).allows(target()))
    }
    @Test fun duplicateMembersCandidatesAndUnboundedListsAreRejected() {
        assertThrows(IllegalArgumentException::class.java) {WindowGroupInputGate(7,true,null,listOf(member("1",true),member("1",false)),emptyList())}
        assertThrows(IllegalArgumentException::class.java) {WindowGroupInputGate(7,true,null,emptyList(),List(2){WindowGroupInputGate.Window("2","1")})}
        assertThrows(IllegalArgumentException::class.java) {WindowGroupInputGate(7,true,null,(1..10).map {member(it.toString(),false)},emptyList())}
    }
}
