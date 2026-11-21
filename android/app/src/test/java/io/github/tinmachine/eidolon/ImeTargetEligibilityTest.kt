package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class ImeTargetEligibilityTest {
    private val target=ImeSdkTarget("2","3","4","5")
    private fun key()=JSONObject().put("session","2").put("window","3").put("scene","4").put("generation","5")
    private fun snapshot():JSONObject=JSONObject().put("state","Ready").put("scene","4")
        .put("panels",JSONArray().put(JSONObject().put("action","create").put("applied",true).put("error",JSONObject.NULL).put("target",key())))
        .put("render",JSONObject().put("targets",JSONArray().put(JSONObject().put("key",key()).put("frames",1).put("presentation_paused",false))))
        .put("app",JSONObject().put("session","2").put("nonce","0123456789abcdef").put("mode","foot").put("state","running").put("error",JSONObject.NULL)
            .put("windows",JSONArray().put(JSONObject().put("window","3").put("life","Mapped")
                .put("app_id","io.github.tinmachine.eidolon.foot.t-0123456789abcdef-1"))))
        .put("transport",JSONObject().put("state","Ready").put("session","2").put("app_forward",JSONObject().put("state","running").put("nonce","0123456789abcdef")
            .put("remote",JSONObject().put("phase","running").put("error",JSONObject.NULL).put("cleanup_pending",true).put("eof",false).put("exit_signal",false).put("exit_status",JSONObject.NULL).put("finished",JSONObject.NULL)
                .put("terminal_launches",JSONArray().put(JSONObject().put("terminal_id","1").put("accepted",true).put("mode","terminal"))))))
    private fun remote(s:JSONObject)=s.getJSONObject("transport").getJSONObject("app_forward").getJSONObject("remote")
    @Test fun onlyTheCapturedSessionFootWithAcceptedReceiptIsEligible() {
        assertTrue(ImeTargetEligibility.allows(snapshot(),target))
        for(other in listOf(target.copy(session="9"),target.copy(scene="9"),target.copy(window="9"),target.copy(generation="9"))) assertFalse(ImeTargetEligibility.allows(snapshot(),other))
        val paused=snapshot();paused.getJSONObject("render").getJSONArray("targets").getJSONObject(0).put("presentation_paused",true)
        assertFalse(ImeTargetEligibility.allows(paused,target))
    }
    @Test fun applicationIdAloneAndDuplicateClaimAreInsufficient() {
        for(accepted in listOf(JSONObject.NULL,false,"true")) {
            val s=snapshot();remote(s).getJSONArray("terminal_launches").getJSONObject(0).put("accepted",accepted)
            assertFalse(ImeTargetEligibility.allows(s,target))
        }
        val missing=snapshot();remote(missing).put("terminal_launches",JSONArray());assertFalse(ImeTargetEligibility.allows(missing,target))
        val duplicate=snapshot();val rows=duplicate.getJSONObject("app").getJSONArray("windows")
        rows.put(JSONObject(rows.getJSONObject(0).toString()).put("window","9"));assertFalse(ImeTargetEligibility.allows(duplicate,target))
    }
    @Test fun guiReceiptCannotUseAFootMarkerToBecomeAnImeTerminal() {
        for(mode in listOf("gui","unknown",JSONObject.NULL,true,1)) {
            val s=snapshot();remote(s).getJSONArray("terminal_launches").getJSONObject(0).put("mode",mode)
            assertFalse(ImeTargetEligibility.allows(s,target))
        }
        val missing=snapshot();remote(missing).getJSONArray("terminal_launches").getJSONObject(0).remove("mode")
        assertFalse(ImeTargetEligibility.allows(missing,target))
    }
    @Test fun oldNonceOtherGuiAndCleanupNeverBecomeTheNewTarget() {
        for(id in listOf("foot","io.github.tinmachine.eidolon.gtkfixture.rfixture","io.github.tinmachine.eidolon.foot.t-0000000000000000-1")) {
            val s=snapshot();s.getJSONObject("app").getJSONArray("windows").getJSONObject(0).put("app_id",id)
            assertFalse(ImeTargetEligibility.allows(s,target))
        }
        val pending=snapshot();remote(pending).put("phase","finishing");assertFalse(ImeTargetEligibility.allows(pending,target))
        val eof=snapshot();remote(eof).put("eof",true);assertFalse(ImeTargetEligibility.allows(eof,target))
        val done=snapshot();remote(done).put("finished",JSONObject());assertFalse(ImeTargetEligibility.allows(done,target))
        val unmap=snapshot();unmap.getJSONObject("app").getJSONArray("windows").getJSONObject(0).put("life","Unmapped")
        assertFalse(ImeTargetEligibility.allows(unmap,target))
    }

    @Test fun desktopRequiresLiveOwnedWindowAndAnActiveTextInput() {
        val s = snapshot()
        val ime = JSONObject().put("active", true)
        val input = JSONObject().put("focus_window", "3").put("focus_token", "7").put("ime", ime)
        val owned = JSONArray().put("3")
        val app = s.getJSONObject("app").put("mode", "linux_desktop").put("ready", true)
            .put("gui_windows", owned).put("input", input)
        val launch = JSONObject().put("session", "2").put("launch_request", "6")
            .put("started_observed", true).put("terminal_observed", false).put("error", JSONObject.NULL)
            .put("transport", JSONObject().put("phase", "started"))
        s.getJSONObject("transport").put("desktop_v3_transport", launch)
        s.put("desktop_v3_presentation", JSONObject().put("startup_completed", true).put("state", "ready"))
        assertTrue(ImeTargetEligibility.allows(s, target))
        ime.put("active", false)
        assertFalse(ImeTargetEligibility.allows(s, target))
        ime.put("active", true)
        input.put("focus_window", "8")
        assertFalse(ImeTargetEligibility.allows(s, target))
        input.put("focus_window", "3")
        app.put("gui_windows", JSONArray().put("8"))
        assertFalse(ImeTargetEligibility.allows(s, target))
        app.put("gui_windows", owned)
        launch.put("terminal_observed", true)
        assertFalse(ImeTargetEligibility.allows(s, target))
    }
}
