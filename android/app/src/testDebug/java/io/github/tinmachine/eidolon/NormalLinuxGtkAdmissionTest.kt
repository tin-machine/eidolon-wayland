package io.github.tinmachine.eidolon

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class NormalLinuxGtkAdmissionTest {
    private fun closed() = JSONObject("""{
        "state":"Ready","app":null,"app_sdk_bindings":0,
        "transport":{"state":"Failed","session":"2","profile_id":"remote-linux",
            "owned_connections":0,"desktop_pending":false,"pending_cleanup":[]},
        "shutdown":{"sdk_bindings":1},"render":{"targets":[{"role":"diagnostic"}]},
        "panels":[],"window_slots":{"occupied":0,"cleanup_required":false,"entries":[],"unassigned":[]}
    }""")
    private fun released(runtime:JSONObject, session:String="2", request:String="15", reply:Boolean=true, launched:Boolean=false) =
        NormalLinuxGtkAdmission.beforeLaunchClosed(runtime,session,request,reply,launched)

    @Test fun connectionFailureAndExplicitPrelaunchStopReleaseObserverDespiteDiagnosticPanel() {
        for (state in listOf("Failed","Disconnected")) {
            val runtime=closed();runtime.getJSONObject("transport").put("state",state)
            assertTrue(released(runtime))
            assertEquals(1,runtime.getJSONObject("shutdown").getInt("sdk_bindings"))
        }
    }
    @Test fun stopWaitsForSameSessionAndActualConnectionRetirementBeforeRetry() {
        val runtime=closed();val transport=runtime.getJSONObject("transport")
        transport.put("state","Disconnecting").put("owned_connections",1)
        assertFalse(released(runtime))
        transport.put("state","Disconnected")
        assertFalse(released(runtime))
        transport.put("owned_connections",0)
        assertTrue(released(runtime))
        transport.put("session","3")
        assertFalse(released(runtime))
        transport.put("session","2").put("profile_id","termux-local")
        assertFalse(released(runtime))
    }
    @Test fun unknownConnectResultAndMissingSessionDoNotReleaseAnEnqueuedRequest() {
        assertFalse(released(closed(),reply=false))
        assertFalse(released(closed(),session=""))
        // 未enqueueと判明した場合だけsessionがなくてもよい。
        assertTrue(released(closed(),session="",request=""))
    }
    @Test fun launchAlwaysRequiresTheSeparateFullTargetCleanupPath() {
        assertFalse(released(closed(),launched=true))
        val runtime=closed().put("app_sdk_bindings",0)
        runtime.getJSONObject("render").put("targets",org.json.JSONArray())
        runtime.getJSONObject("shutdown").put("sdk_bindings",0)
        assertFalse(released(runtime,launched=true))
    }
    @Test fun pendingRemoteAppAndBoardOwnersKeepObserverReserved() {
        val changes=listOf<(JSONObject)->Unit>(
            {it.put("app_sdk_bindings",1)},
            {it.put("app",JSONObject().put("state","queued"))},
            {it.getJSONObject("transport").put("desktop_pending",true)},
            {it.getJSONObject("transport").getJSONArray("pending_cleanup").put(JSONObject())},
            {it.getJSONArray("panels").put(JSONObject().put("action","destroy"))},
            {it.getJSONObject("render").getJSONArray("targets").getJSONObject(0).put("role","windows")},
            {it.getJSONObject("window_slots").put("cleanup_required",true)},
            {it.getJSONObject("window_slots").getJSONArray("entries").put(JSONObject())},
            {it.getJSONObject("window_slots").getJSONArray("unassigned").put("7")},
        )
        for(change in changes) assertFalse(released(closed().also(change)))
    }
}
