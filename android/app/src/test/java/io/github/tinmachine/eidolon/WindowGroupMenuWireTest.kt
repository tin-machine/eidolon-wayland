package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class WindowGroupMenuWireTest {
    private fun target(window:String="1")=JSONObject().put("session","2").put("window",window).put("scene","3").put("generation","4")
    private fun pose()=JSONObject().put("position_m",JSONArray(listOf(0.0,2.0,1.5))).put("rotation_xyzw",JSONArray(listOf(0.0,0.0,0.0,1.0)))
    private fun size()=JSONObject().put("width_m",1.6).put("height_m",0.9)
    private fun state():JSONObject {
        val keys=listOf(target(),target("2"))
        return JSONObject().put("state","Ready").put("scene","3").put("activity_visible",true).put("xr_state","FOCUSED")
            .put("app",JSONObject().put("session","2").put("error",JSONObject.NULL).put("windows",JSONArray(keys.map {k->JSONObject()
                .put("window",k.getString("window")).put("life","Mapped").put("sizing",JSONObject().put("physical_size_m",size()).put("applied_physical_size_m",size()))})))
            .put("panels",JSONArray(keys.map {JSONObject().put("target",it).put("action","create").put("applied",true).put("error",JSONObject.NULL)}))
            .put("render",JSONObject().put("theme_revision",5).put("targets",JSONArray(keys.map {JSONObject().put("key",it).put("frames",3)})))
            .put("workspace",JSONObject().put("windows",JSONArray(keys.map {k->JSONObject().put("session","2").put("window",k.getString("window"))
                .put("placement",JSONObject().put("pose",pose()).put("pose_command",JSONObject.NULL)
                    .put("applied_pose",JSONObject().put("target",k).put("pose",pose()).put("pose_command",JSONObject.NULL)))})))
            .put("window_groups",JSONObject().put("error",JSONObject.NULL).put("blocked_session",JSONObject.NULL).put("groups",JSONArray())
                .put("candidates",JSONArray(keys.map {JSONObject().put("session","2").put("window",it.getString("window"))})))
    }
    private fun active(s:JSONObject,phase:String="animating"):JSONObject {
        s.getJSONObject("window_groups").put("groups",JSONArray().put(JSONObject().put("id","20").put("revision","21")
            .put("scope",JSONObject().put("runtime","7").put("session","2").put("scene","3").put("application",JSONObject().put("kind","foot")))
            .put("phase",phase).put("layout","collapsed").put("source_ready",true)
            .put("windows",JSONArray(listOf(target(),target("2")).mapIndexed {i,k->JSONObject().put("target",k).put("front",i==0).put("input_allowed",false)}))))
        return s
    }
    @Test fun initialCaptureRequiresAllVerifiedFootBaseGeometryAndDraws() {
        val s=state();assertEquals(setOf("collapse"),WindowGroupMenuWire.capture(7,target(),s)?.actions)
        s.getJSONObject("workspace").getJSONArray("windows").getJSONObject(1).getJSONObject("placement").put("applied_pose",JSONObject.NULL)
        assertNull(WindowGroupMenuWire.capture(7,target(),s))
        val pending=state();pending.getJSONObject("workspace").getJSONArray("windows").getJSONObject(0).getJSONObject("placement").put("pose_command","10")
        assertNull(WindowGroupMenuWire.capture(7,target(),pending))
    }
    @Test fun movingFrontMenuUsesSeparatePermissionAndNeverRequiresBaseAckAgain() {
        val s=active(state());s.getJSONObject("workspace").getJSONArray("windows").getJSONObject(0).getJSONObject("placement").put("applied_pose",JSONObject.NULL)
        val permit=checkNotNull(WindowGroupMenuWire.capture(7,target(),s));assertEquals(setOf("expand","next_front"),permit.actions)
        assertFalse(WindowGroupWire.decode(7,s.getJSONObject("window_groups")).allows(InputWindowKey(7,"2","1","3","4")))
        assertNull(WindowGroupMenuWire.capture(7,target("2"),s))
        assertTrue(permit.same(checkNotNull(WindowGroupMenuWire.capture(7,target(),active(state(),"settling")))))
    }
    @Test fun staleOrMissingTargetRuntimeAndExternalLifecycleNeverAdoptLatest() {
        val s=active(state())
        assertNull(WindowGroupMenuWire.capture(8,target(),s))
        assertNull(WindowGroupMenuWire.capture(7,target().put("generation","5"),s))
        s.put("activity_visible",false);assertNull(WindowGroupMenuWire.capture(7,target(),s))
        s.put("activity_visible",true).put("xr_state","VISIBLE");assertNull(WindowGroupMenuWire.capture(7,target(),s))
    }
    @Test fun nonFootUnknownSourceFailureAndBlockedGroupDoNotOfferControls() {
        val s=active(state());val g=s.getJSONObject("window_groups").getJSONArray("groups").getJSONObject(0)
        g.getJSONObject("scope").put("application",JSONObject().put("kind","catalog").put("id","gui"))
        assertNull(WindowGroupMenuWire.capture(7,target(),s))
        assertNull(WindowGroupMenuWire.capture(7,target(),active(state(),"blocked")))
        val lost=active(state());lost.getJSONObject("window_groups").getJSONArray("groups").getJSONObject(0).put("source_ready",false)
        assertNull(WindowGroupMenuWire.capture(7,target(),lost))
        lost.getJSONObject("window_groups").put("error","GROUP_SOURCE_LOST");assertNull(WindowGroupMenuWire.capture(7,target(),lost))
        val blocked=state();blocked.getJSONObject("window_groups").put("blocked_session","2");assertNull(WindowGroupMenuWire.capture(7,target(),blocked))
    }
    @Test fun duplicateUnboundedAndCoercedSchemaIsRejected() {
        val s=state();s.getJSONObject("window_groups").getJSONArray("candidates").put(JSONObject().put("session","2").put("window","2"))
        assertNull(WindowGroupMenuWire.capture(7,target(),s))
        val large=state();large.getJSONObject("window_groups").put("groups",JSONArray(List(10){JSONObject()}))
        assertNull(WindowGroupMenuWire.capture(7,target(),large))
        val fractional=state();fractional.getJSONObject("render").put("theme_revision",5.5);assertNull(WindowGroupMenuWire.capture(7,target(),fractional))
        val bool=state();bool.put("activity_visible","true");assertNull(WindowGroupMenuWire.capture(7,target(),bool))
    }
}
