package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class WindowRecenterWireTest {
    private fun target()=JSONObject("""{"session":"2","window":"3","scene":"4","generation":"5"}""")
    private fun pose()=JSONObject("""{"position_m":[0,1.4,1.9],"rotation_xyzw":[0,0,0,1]}""")
    private fun state():JSONObject=JSONObject().put("app",JSONObject().put("session","2").put("windows",JSONArray().put(
        JSONObject().put("window","3").put("life","Mapped").put("logical_size",JSONObject().put("width",800).put("height",600))
            .put("sizing",JSONObject().put("physical_size_m",JSONObject().put("width_m",1.2).put("height_m",.9))
                .put("size_command",JSONObject.NULL).put("producer_size_px",JSONObject().put("width",800).put("height",600))))))
        .put("panels",JSONArray().put(JSONObject().put("target",target()).put("action","create").put("applied",true).put("error",JSONObject.NULL)
            .put("decoration",JSONObject().put("canvas",JSONObject().put("width",1280).put("height",960)))))
        .put("workspace",JSONObject().put("windows",JSONArray().put(JSONObject().put("session","2").put("window","3").put("placement",
            JSONObject().put("pose",pose()).put("pose_command",JSONObject.NULL).put("applied_pose",JSONObject().put("target",target()).put("pose",pose()).put("pose_command",JSONObject.NULL))))))
    @Test fun capturedGeometryAndNativeAppliedPoseNeedSameTargetAndCommand() {
        val s=state();val g=checkNotNull(WindowRecenterWire.read(s,target()))
        assertTrue(g.valid());assertTrue(WindowRecenterWire.applied(s,target(),null,g.pose))
        assertFalse(WindowRecenterWire.applied(s,target(),"10",g.pose))
        WindowRecenterWire.placement(s,target())!!.getJSONObject("applied_pose").getJSONObject("target").put("generation","6")
        assertFalse(WindowRecenterWire.applied(s,target(),null,g.pose))
    }
    @Test fun duplicateRetiredOrOtherTargetsCannotProvideGeometry() {
        val duplicate=state();duplicate.getJSONArray("panels").put(JSONObject(duplicate.getJSONArray("panels").getJSONObject(0).toString()))
        assertNull(WindowRecenterWire.read(duplicate,target()))
        val retired=state();retired.getJSONArray("panels").getJSONObject(0).put("action","destroy");assertNull(WindowRecenterWire.read(retired,target()))
        assertNull(WindowRecenterWire.read(state(),target().put("generation","9")))
        assertNull(WindowRecenterWire.read(state(),target().put("generation",5)))
    }
    @Test fun invalidPoseAndArrayBoundsAreRejected() {
        val invalid=state();WindowRecenterWire.placement(invalid,target())!!.getJSONObject("pose").getJSONArray("rotation_xyzw").put(3,2)
        assertNull(WindowRecenterWire.read(invalid,target()))
        val many=state();repeat(9) {many.getJSONArray("panels").put(JSONObject())};assertNull(WindowRecenterWire.read(many,target()))
        val missing=state();missing.getJSONObject("app").getJSONArray("windows").getJSONObject(0).put("logical_size",JSONObject.NULL)
        assertNull(WindowRecenterWire.read(missing,target()))
    }
    @Test fun pixelChangeNeverEqualsCapturedBaseline() {
        val s=state();val before=checkNotNull(WindowRecenterWire.read(s,target()))
        s.getJSONArray("panels").getJSONObject(0).getJSONObject("decoration").getJSONObject("canvas").put("width",1300)
        val after=checkNotNull(WindowRecenterWire.read(s,target()));assertFalse(before.sameDimensions(after))
    }
    @Test fun enabledAfterViewerProbeAndModeDescribesOperation() {
        assertTrue(WindowRecenterPolicy.ENABLED)
        assertEquals(WindowMode.RECENTERING,WindowMode.from(false,true,false,null,"READY",false,WindowSizeCapture.Phase.GEOMETRY,true))
        assertEquals(WindowMode.RETURNING,WindowMode.from(false,true,false,null,"READY",false,WindowSizeCapture.Phase.RETURNING,true))
        assertEquals(WindowMode.CLEANUP,WindowMode.from(false,true,false,null,"READY",false,WindowSizeCapture.Phase.CANCELLED,true))
    }
}
