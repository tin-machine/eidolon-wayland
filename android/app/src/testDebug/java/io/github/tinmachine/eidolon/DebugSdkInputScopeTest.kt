package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class DebugSdkInputScopeTest {
    @Test fun opaqueOwnerCannotFollowRestorationOrDifferentSceneAndTrialIsSingleUse() {
        val lease=DebugSdkRuntimeLease();val owner=Any();val scope=DebugSdkInputScope(owner,22,"7","a".repeat(16),"input",3)
        assertTrue(lease.claim(owner,11));assertFalse(scope.current(owner,lease.metadata(),22,3))
        assertTrue(lease.normalRetired(owner,11,true));assertTrue(lease.created(owner,22))
        assertTrue(scope.current(owner,lease.metadata(),22,3));assertTrue(scope.claimTrial());assertFalse(scope.claimTrial())
        assertFalse(scope.current(Any(),lease.metadata(),22,3));assertFalse(scope.current(owner,lease.metadata(),23,3))
        assertFalse(scope.current(owner,lease.metadata(),22,4));assertEquals(22L,lease.beginClose(owner))
        assertFalse(scope.current(owner,lease.metadata(),22,3));assertTrue(lease.retired(owner,22,true));assertTrue(lease.restoreCreated(owner,33))
        assertFalse(scope.current(owner,lease.metadata(),33,3));assertTrue(lease.restored(owner,33,true));assertFalse(scope.current(owner,lease.metadata(),22,3))
    }
    @Test fun fixedCaseAppIdsDoNotPermitOtherFixtureOrDialogInInputCase() {
        val owner=Any();val input=DebugSdkInputScope(owner,22,"7","a".repeat(16),"input",3)
        val dialog=DebugSdkInputScope(owner,22,"7","a".repeat(16),"dialog",3)
        assertTrue(input.permitsApp(input.appId));assertFalse(input.permitsApp(input.appId+".dialog"))
        assertTrue(dialog.permitsApp(dialog.appId+".dialog"));assertFalse(dialog.permitsApp(dialog.appId+".other"))
        assertFalse(dialog.permitsApp(dialog.appId.replace("a".repeat(32),"b".repeat(32))))
        assertThrows(IllegalArgumentException::class.java) {DebugSdkInputScope(owner,0,"7","a".repeat(16),"input",3)}
        assertThrows(IllegalArgumentException::class.java) {DebugSdkInputScope(owner,22,"07","a".repeat(16),"input",3)}
        assertThrows(IllegalArgumentException::class.java) {DebugSdkInputScope(owner,22,"7","a".repeat(16),"shell",3)}
        assertEquals("DebugSdkInputScope(<owner>)",input.toString())
    }
}
