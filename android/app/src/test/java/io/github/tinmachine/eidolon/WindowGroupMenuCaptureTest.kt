package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class WindowGroupMenuCaptureTest {
    private fun key(window:String="1",generation:String="4")=InputWindowKey(7,"2",window,"3",generation)
    private fun permit(group:String?=null,revision:String?=null,layout:String="expanded",phase:String="stable",front:Boolean=true,
        members:List<InputWindowKey> = listOf(key(),key("2")))=WindowGroupMenuPermit.create(key(),5,group,revision,layout,phase,true,front,members)
    private fun identity()=WindowGroupMenuIdentity(key(),5,10)
    private fun capture(p:WindowGroupMenuPermit=checkNotNull(permit()))=WindowGroupMenuCapture(identity(),p,8,0)
    private fun menu(c:WindowGroupMenuCapture,p:WindowGroupMenuPermit) {assertTrue(c.resetObserved(identity(),1,p,"ACCEPTED",true,9))}
    private fun submit(c:WindowGroupMenuCapture,p:WindowGroupMenuPermit) {
        menu(c,p);assertTrue(c.choose(identity(),2,p,c.permit.actions.first()))
        assertTrue(c.cleaned(identity(),3,p,true));assertTrue(c.submitted(identity(),4,p,"101"))
    }
    @Test fun menuRequiresResetAckAndroidReleaseAndFreshEpochSeparately() {
        val p=checkNotNull(permit());val c=capture(p)
        assertFalse(c.resetObserved(identity(),1,p,null,true,9))
        assertFalse(c.resetObserved(identity(),2,p,"ACCEPTED",false,9))
        assertFalse(c.resetObserved(identity(),3,p,null,true,8))
        assertTrue(c.resetObserved(identity(),4,p,null,true,9))
        assertEquals(WindowGroupMenuCapture.Phase.MENU,c.phase)
    }
    @Test fun nativePermissionAllowsOnlyFrontMenuDuringAnimationWithoutAppInputPermission() {
        val moving=checkNotNull(permit("20","21","collapsed","animating"))
        assertEquals(setOf("expand","next_front"),moving.actions)
        assertNull(permit("20","21","collapsed","animating",front=false))
        assertTrue(moving.same(checkNotNull(permit("20","21","collapsed","settling"))))
        assertTrue(moving.same(checkNotNull(permit("20","21","collapsed","stable"))))
        val c=capture(moving);menu(c,moving)
        assertTrue(c.choose(identity(),2,moving,"expand"))
        assertFalse(c.choose(identity(),3,moving,"next_front"))
    }
    @Test fun lastSurvivingCollapsedMemberCanExpandButCannotStartAnotherNewGroup() {
        val one=checkNotNull(permit("20","21","collapsed",members=listOf(key())))
        assertEquals(setOf("expand"),one.actions)
        val c=capture(one);menu(c,one);assertTrue(c.choose(identity(),2,one,"expand"))
        assertNull(permit(members=listOf(key())))
    }
    @Test fun oldRevisionRuntimeTargetMembershipAndExternalIntentPermanentlyCancel() {
        val p=checkNotNull(permit("20","21","collapsed"))
        for(current in listOf(permit("20","22","collapsed"),permit("20","21","collapsed",members=listOf(key(),key("2","6"))),null)) {
            val c=capture(p);menu(c,p)
            assertFalse(c.choose(identity(),2,current,"expand"));assertEquals("owner_changed",c.reason)
            assertFalse(c.choose(identity(),3,p,"expand"))
        }
        val c=capture(p);menu(c,p)
        assertFalse(c.choose(identity().copy(intent=11),2,p,"expand"));assertEquals("owner_changed",c.reason)
    }
    @Test fun sameOwnedMenuMustReleaseAllSdkResourcesBeforeOneSubmission() {
        val p=checkNotNull(permit());val c=capture(p);menu(c,p)
        assertTrue(c.choose(identity(),2,p,"collapse"))
        assertFalse(c.cleaned(identity(),3,p,false))
        assertFalse(c.submitted(identity(),4,p,"101"))
        assertTrue(c.cleaned(identity(),5,p,true))
        assertFalse(c.cleaned(identity(),6,p,true))
        assertTrue(c.submitted(identity(),7,p,"101"))
        assertFalse(c.submitted(identity(),8,p,"102"));assertEquals("101",c.request)
    }
    @Test fun busyOrRejectedCommandsNeverRetryOrClaimGeometrySuccess() {
        val p=checkNotNull(permit());val c=capture(p);submit(c,p)
        assertFalse(c.commandObserved(identity(),5,true,"101","BUSY"))
        assertFalse(c.commandObserved(identity(),6,true,"101","ACCEPTED"))
        assertEquals(WindowGroupMenuCapture.Phase.CANCELLED,c.phase)
        val rejected=capture(p);menu(rejected,p);rejected.choose(identity(),2,p,"collapse");rejected.cleaned(identity(),3,p,true)
        assertFalse(rejected.submitted(identity(),4,p,null));assertEquals("enqueue_rejected",rejected.reason)
    }
    @Test fun ownRequestReceiptMayFollowRevisionChangeButExternalLossStillCancels() {
        val p=checkNotNull(permit());val c=capture(p);submit(c,p)
        assertFalse(c.commandObserved(identity(),5,true,"102","ACCEPTED"))
        assertFalse(c.commandObserved(identity(),6,true,"101","pending"))
        assertTrue(c.commandObserved(identity(),7,true,"101","ACCEPTED"))
        assertEquals("native_accepted_geometry_pending",c.reason)
        assertFalse(c.commandObserved(identity(),8,true,"101","ACCEPTED"))
        val lost=capture(p);submit(lost,p)
        assertFalse(lost.commandObserved(identity(),5,false,"101","ACCEPTED"))
        assertEquals("owner_changed",lost.reason)
    }
    @Test fun timeoutAtDeadlineAndReversedClockCancelBeforeReceipt() {
        val p=checkNotNull(permit());val c=capture(p);submit(c,p)
        assertFalse(c.commandObserved(identity(),10002,true,"101","ACCEPTED"));assertEquals("timeout",c.reason)
        val reversed=capture(p);submit(reversed,p)
        assertFalse(reversed.commandObserved(identity(),3,true,"101","ACCEPTED"));assertEquals("time_reversed",reversed.reason)
    }
    @Test fun backReleasesMenuWithoutSendingGroupCommandOrRawFocus() {
        val p=checkNotNull(permit());val c=capture(p);menu(c,p)
        assertTrue(c.choose(identity(),2,p,"back"));assertFalse(c.cleaned(identity(),3,p,true))
        assertEquals(WindowGroupMenuCapture.Phase.DISMISSED,c.phase);assertNull(c.request)
    }
    @Test fun boundedPermitRejectsUnknownFootTargetsAndOwnsAnImmutableMemberCopy() {
        assertNull(permit(members=listOf(key())))
        assertNull(permit(members=listOf(key(),key())))
        assertNull(permit(members=(1..10).map {key(it.toString())}))
        assertNull(permit("20",null,"collapsed"));assertNull(permit("0","21","collapsed"))
        assertNull(permit("20","21","collapsed","blocked"))
        assertNull(permit(members=listOf(key(),key("2").copy(runtime=8))))
        assertNull(permit(members=listOf(key(),key("2").copy(session="9"))))
        val source=mutableListOf(key(),key("2"));val p=checkNotNull(permit(members=source));source.clear()
        assertTrue(p.same(checkNotNull(permit())))
    }
}
