package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class WindowGroupPresentationTest {
    private fun target(window:String="1",scene:String="3",generation:String="4")=JSONObject().put("session","2").put("window",window).put("scene",scene).put("generation",generation)
    private fun report():JSONObject {
        val pose=JSONObject().put("position_m",JSONArray(listOf(0.0,2.0,1.5))).put("rotation_xyzw",JSONArray(listOf(0.0,0.0,0.0,1.0)))
        val window=JSONObject().put("target",target()).put("pose",pose).put("physical_size",JSONObject().put("width_m",1.2).put("height_m",.9))
            .put("required_frame","12").put("geometry_applied",false).put("front",true).put("input_allowed",false)
        val group=JSONObject().put("id","5").put("revision","6").put("phase","settling").put("source_ready",true)
            .put("scope",JSONObject().put("runtime","7").put("session","2").put("scene","3")).put("windows",JSONArray().put(window))
        return JSONObject().put("groups",JSONArray().put(group)).put("candidates",JSONArray()).put("error",JSONObject.NULL)
    }
    @Test fun exactTargetOwnsDisplayPoseAndKeepsAckIdentitySeparateFromWorkspace() {
        val state=WindowGroupPresentation.select(7,target(),report()) as WindowGroupPresentation.Owned
        assertEquals("7:5:6:2:1:3:4",state.stamp);assertEquals(12L,state.requiredFrame);assertTrue(state.settling)
        assertFalse(state.geometryApplied);assertFalse(state.inputAllowed)
    }
    @Test fun changedGenerationOrSceneHidesUntilCurrentGroupCaptureArrives() {
        assertEquals(WindowGroupPresentation.Pending,WindowGroupPresentation.select(7,target(generation="8"),report()))
        assertEquals(WindowGroupPresentation.Pending,WindowGroupPresentation.select(7,target(scene="8"),report()))
        assertEquals(WindowGroupPresentation.Pending,WindowGroupPresentation.select(8,target(),report()))
    }
    @Test fun pendingClassifiedNewMemberIsHiddenButUnknownGuiIsNotMoved() {
        val report=report();report.getJSONArray("candidates").put(JSONObject().put("session","2").put("window","9"))
        assertEquals(WindowGroupPresentation.Pending,WindowGroupPresentation.select(7,target("9"),report))
        assertEquals(WindowGroupPresentation.Unowned,WindowGroupPresentation.select(7,target("8"),report))
        report.put("groups",JSONArray());assertEquals(WindowGroupPresentation.Unowned,WindowGroupPresentation.select(7,target("9"),report))
    }
    @Test fun unfinishedSourceOrAnimationDoesNotPermitGeometryAck() {
        for(phase in listOf("animating","stable","blocked")) {
            val report=report();report.getJSONArray("groups").getJSONObject(0).put("phase",phase)
            val state=WindowGroupPresentation.select(7,target(),report) as WindowGroupPresentation.Owned
            assertFalse(state.settling)
        }
        val report=report();report.getJSONArray("groups").getJSONObject(0).put("source_ready",false)
        assertFalse((WindowGroupPresentation.select(7,target(),report) as WindowGroupPresentation.Owned).settling)
    }
    @Test fun sdkNormalRayRotatesWithYawInsteadOfStayingOnWorldZ() {
        for(sign in listOf(-1.0,1.0)) {
            val value=JSONObject().put("position_m",JSONArray(listOf(0.0,2.0,1.5)))
                .put("rotation_xyzw",JSONArray(listOf(0.0,sign*kotlin.math.sin(Math.PI/4),0.0,kotlin.math.cos(Math.PI/4))))
            val pose=PanelPoseCodec.toSdk(value)
            val layer=pose*com.meta.spatial.core.Vector3(0f,0f,.025f)-pose.t
            val before=pose*com.meta.spatial.core.Vector3(0f,0f,-.005f)-pose.t
            val after=pose*com.meta.spatial.core.Vector3(0f,0f,.005f)-pose.t
            assertEquals(sign*.025,layer.x.toDouble(),.00001);assertEquals(0.0,layer.z.toDouble(),.00001)
            assertEquals(sign*.010,(after.x-before.x).toDouble(),.00001);assertEquals(0.0,(after.z-before.z).toDouble(),.00001)
        }
    }
    @Test fun malformedOrFailedGroupNeverFallsBackToBaseGrid() {
        for(change in listOf<(JSONObject)->Unit>(
            {it.put("error","GROUP_OBSERVATION_FAILED")},
            {it.getJSONArray("groups").getJSONObject(0).getJSONArray("windows").getJSONObject(0).put("required_frame",12)},
            {it.getJSONArray("groups").getJSONObject(0).getJSONArray("windows").getJSONObject(0).getJSONObject("pose").getJSONArray("position_m").put(1,20)},
            {it.getJSONArray("groups").getJSONObject(0).getJSONArray("windows").getJSONObject(0).getJSONObject("physical_size").put("width_m",4)})) {
            val report=report();change(report);assertEquals(WindowGroupPresentation.Pending,WindowGroupPresentation.select(7,target(),report))
        }
    }
}
