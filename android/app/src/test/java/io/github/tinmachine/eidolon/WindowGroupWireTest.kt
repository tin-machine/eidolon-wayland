package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class WindowGroupWireTest {
    private fun target(window:String="1")=JSONObject().put("session","2").put("window",window).put("scene","3").put("generation","4")
    private fun key(window:String="1")=InputWindowKey(7,"2",window,"3","4")
    private fun state():JSONObject {
        val group=JSONObject().put("id","5").put("revision","6").put("phase","stable").put("source_ready",true)
            .put("scope",JSONObject().put("runtime","7").put("session","2").put("scene","3"))
            .put("windows",JSONArray().put(JSONObject().put("target",target()).put("input_allowed",true))
                .put(JSONObject().put("target",target("2")).put("input_allowed",false)))
        return JSONObject().put("groups",JSONArray().put(group)).put("candidates",JSONArray())
            .put("blocked_session",JSONObject.NULL).put("error",JSONObject.NULL)
    }
    @Test fun nativeFlatPresentationUsesSameRuntimeAndFullTarget() {
        val gate=WindowGroupWire.decode(7,state());assertTrue(gate.allows(key()));assertFalse(gate.allows(key("2")))
        assertFalse(gate.allows(key().copy(generation="8")));assertFalse(gate.allows(key().copy(runtime=8)))
        assertTrue(gate.allows(key("9")))
    }
    @Test fun unfinishedNativePhaseOrSourceAlwaysStopsMemberInput() {
        for(phase in listOf("animating","settling","blocked")) {
            val state=state();state.getJSONArray("groups").getJSONObject(0).put("phase",phase)
            assertFalse(WindowGroupWire.decode(7,state).allows(key()))
        }
        val state=state();state.getJSONArray("groups").getJSONObject(0).put("source_ready",false)
        assertFalse(WindowGroupWire.decode(7,state).allows(key()))
    }
    @Test fun unknownOldMissingOrCoercedWireCannotFallBackToAllowed() {
        assertFalse(WindowGroupWire.decode(7,null).allows(key()))
        for(change in listOf<(JSONObject)->Unit>(
            {it.remove("error")}, {it.put("error","GROUP_OBSERVATION_FAILED")},
            {it.getJSONArray("groups").getJSONObject(0).getJSONObject("scope").put("runtime","8")},
            {it.getJSONArray("groups").getJSONObject(0).getJSONObject("scope").put("runtime",7)},
            {it.getJSONArray("groups").getJSONObject(0).put("source_ready",1)},
            {it.getJSONArray("groups").getJSONObject(0).getJSONArray("windows").getJSONObject(0).put("input_allowed",1)},
            {it.getJSONArray("groups").getJSONObject(0).getJSONArray("windows").getJSONObject(0).getJSONObject("target").put("generation","0")})) {
            val state=state();change(state);assertFalse(WindowGroupWire.decode(7,state).allows(key()))
        }
    }
    @Test fun onlyActiveGroupBlocksPendingCandidateAndInvalidatedSession() {
        val state=state();state.getJSONArray("candidates").put(JSONObject().put("session","2").put("window","9"))
        assertFalse(WindowGroupWire.decode(7,state).allows(key("9")))
        state.put("groups",JSONArray());assertTrue(WindowGroupWire.decode(7,state).allows(key("9")))
        state.put("blocked_session","2");assertFalse(WindowGroupWire.decode(7,state).allows(key("9")))
    }
    @Test fun duplicateTargetsAndOverBudgetInputAreRejectedBeforeUse() {
        val state=state();val windows=state.getJSONArray("groups").getJSONObject(0).getJSONArray("windows")
        windows.put(windows.getJSONObject(0));assertFalse(WindowGroupWire.decode(7,state).allows(key()))
        val excessive=state();val candidates=excessive.getJSONArray("candidates")
        for(i in 1..10)candidates.put(JSONObject().put("session","2").put("window",i.toString()))
        assertFalse(WindowGroupWire.decode(7,excessive).allows(key()))
    }
}
