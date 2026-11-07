package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class GtkFixtureBindingTest {
    private fun target()=JSONObject().put("session","2").put("window","5").put("scene","3").put("generation","4")
    private fun state():JSONObject {
        val app=JSONObject().put("mode","foot").put("state","running").put("error",JSONObject.NULL).put("session","2").put("nonce","0123456789abcdef")
            .put("windows",JSONArray().put(JSONObject().put("app_id","fixed.main").put("window","5").put("life","Mapped").put("frame","1")))
        val remote=JSONObject().put("phase","running").put("error",JSONObject.NULL).put("tools",JSONObject().put("uid",10158))
            .put("child",JSONObject().put("pid",17).put("group",17).put("start","19"))
        return JSONObject().put("state","Ready").put("activity_visible",true).put("xr_state","FOCUSED").put("scene","3").put("app",app)
            .put("transport",JSONObject().put("state","Ready").put("session","2").put("app_forward",JSONObject().put("nonce","0123456789abcdef").put("state","running").put("remote",remote)))
            .put("panels",JSONArray().put(JSONObject().put("action","create").put("applied",true).put("error",JSONObject.NULL).put("target",target())))
            .put("render",JSONObject().put("targets",JSONArray().put(JSONObject().put("key",target()).put("frames",1).put("content_frame","1"))))
    }
    @Test fun ownerIncludesRuntimeSceneLeasePidStartAndGroup() {
        val original=GtkFixtureBinding.owner(state(),9)
        assertNotEquals(original,GtkFixtureBinding.owner(state(),10))
        val changed=state();changed.getJSONObject("transport").getJSONObject("app_forward").getJSONObject("remote").getJSONObject("child").put("start","20")
        assertNotEquals(original,GtkFixtureBinding.owner(changed,9))
        assertEquals(original,GtkFixtureBinding.owner(state(),9))
    }
    @Test fun otherUidOrHiddenRuntimeCannotAcquireOwner() {
        for(change in listOf<(JSONObject)->Unit>({it.put("activity_visible",false)}, {it.put("xr_state","VISIBLE")},
            {it.getJSONObject("transport").getJSONObject("app_forward").getJSONObject("remote").getJSONObject("tools").put("uid",10159)})) {
            val value=state();change(value)
            assertThrows(IllegalStateException::class.java) {GtkFixtureBinding.owner(value,9)}
        }
    }
    @Test fun missingUnappliedOrUnrenderedTargetNeverFallsBackToOtherWindow() {
        val value=state();assertNotNull(GtkFixtureBinding.targetFor(value,"5"));assertNull(GtkFixtureBinding.targetFor(value,"6"))
        value.getJSONArray("panels").getJSONObject(0).put("applied",false);assertNull(GtkFixtureBinding.targetFor(value,"5"))
        value.getJSONArray("panels").getJSONObject(0).put("applied",true)
        value.getJSONObject("render").getJSONArray("targets").getJSONObject(0).put("frames",0);assertNull(GtkFixtureBinding.targetFor(value,"5"))
    }
    @Test fun generationMismatchAndDuplicateTargetsAreRejected() {
        val value=state();value.getJSONObject("render").getJSONArray("targets").getJSONObject(0).getJSONObject("key").put("generation","6")
        assertNull(GtkFixtureBinding.targetFor(value,"5"))
        value.getJSONArray("panels").put(JSONObject().put("action","create").put("applied",true).put("error",JSONObject.NULL)
            .put("target",target().put("generation","7")))
        assertThrows(IllegalStateException::class.java) {GtkFixtureBinding.targetFor(value,"5")}
    }
    @Test fun duplicateAppIdIsRejectedAndAbsentDialogStaysAbsent() {
        val value=state();assertNotNull(GtkFixtureBinding.window(value,"fixed.main"));assertNull(GtkFixtureBinding.window(value,"fixed.main.dialog"))
        value.getJSONObject("app").getJSONArray("windows").put(JSONObject().put("app_id","fixed.main").put("window","6").put("life","Mapped"))
        assertThrows(IllegalStateException::class.java) {GtkFixtureBinding.window(value,"fixed.main")}
    }
    @Test fun malformedOrExtraTargetFieldsCannotBeUsed() {
        assertThrows(IllegalStateException::class.java) {GtkFixtureBinding.target(target().put("window","0"))}
        assertThrows(IllegalStateException::class.java) {GtkFixtureBinding.target(target().put("text","PRIVATE"))}
    }
    @Test fun sdkMustConsumeTheCurrentProducerFrameBeforeAnotherOperation() {
        val value=state();value.getJSONObject("app").getJSONArray("windows").getJSONObject(0).put("frame","2")
        assertNull(GtkFixtureBinding.targetFor(value,"5"))
        value.getJSONObject("render").getJSONArray("targets").getJSONObject(0).put("content_frame","2")
        assertNotNull(GtkFixtureBinding.targetFor(value,"5"))
    }
}
