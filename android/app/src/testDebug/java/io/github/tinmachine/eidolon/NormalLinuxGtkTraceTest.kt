package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class NormalLinuxGtkTraceTest {
    private fun target(window:String="7",generation:String="1")=JSONObject().put("session","2").put("scene","3").put("window",window).put("generation",generation)
    private fun remote(clean:Boolean)=JSONObject().put("process_complete",clean).put("pipes_eof",clean).put("remote_cleaned",clean).put("receipt_written",clean).put("protocol_valid",true)
        .put("reason",if(clean) "process_exited" else JSONObject.NULL)
    private fun runtime(frame:Long=1,input:Long=0,dialog:Boolean=false,generation:String="1"):JSONObject {
        val keys=if(dialog) listOf(target("7",generation),target("8")) else listOf(target("7",generation))
        val app=JSONObject().put("session","2").put("mode","linux_desktop").put("state","running").put("remote_gui_verified",true)
            .put("gui_windows",JSONArray(keys.map {it.getString("window")}))
            .put("windows",JSONArray(keys.map {key ->JSONObject().put("window",key.getString("window")).put("life","Mapped").put("frame",frame.toString())
                .put("parent",if(key.getString("window")=="7") JSONObject.NULL else "7")}))
            .put("input",JSONObject().put("delivered",input).put("pointer",JSONObject().put("buttons",input)))
            .put("local",JSONObject().put("workers",1).put("owned_groups",1).put("waited",false)).put("directory_cleaned",false)
        for(name in listOf("clients","objects","surfaces","pool_bytes","pool_count","pixel_bytes")) app.put(name,1)
        return JSONObject().put("scene","3").put("state","Ready").put("app",app)
            .put("transport",JSONObject().put("session","2").put("profile_id","remote-linux").put("state","DesktopReady").put("owned_connections",1).put("desktop_pending",true)
                .put("desktop",JSONObject().put("request_id","4").put("nonce","a".repeat(16)).put("finished",false).put("channel_complete",false).put("router_drained",false)
                    .put("journal_pending",true).put("cancelled",false).put("error",JSONObject.NULL).put("remote",remote(false))))
            .put("panels",JSONArray(keys.map {key->JSONObject().put("action","create").put("applied",true).put("error",JSONObject.NULL).put("target",key)}))
            .put("render",JSONObject().put("targets",JSONArray(keys.map {key->JSONObject().put("key",key).put("frames",frame).put("content_frame",frame.toString())})))
            .put("shutdown",JSONObject().put("sdk_bindings",keys.size))
            .put("window_slots",JSONObject().put("occupied",keys.size).put("cleanup_required",false).put("entries",JSONArray()).put("unassigned",JSONArray()))
    }
    private fun adapter(keys:List<JSONObject>,retired:List<JSONObject> = emptyList())=JSONObject().put("scene","3")
        .put("targets",JSONArray(keys.map {key->JSONObject().put("runtime","1").put("target",key).put("retiring",false).put("sdk_disposed",false)
            .put("disposal_failed",false).put("listener_owned",true)}))
        .put("retired",JSONArray(retired.map {key->JSONObject().put("runtime","1").put("target",key).put("sdk_disposed",true).put("sdk_binding_released",true).put("disposal_failed",false)}))
    private fun closed():JSONObject {
        val value=runtime(2,2,true);val app=value.getJSONObject("app");val transport=value.getJSONObject("transport")
        app.put("state","stopped").put("windows",JSONArray()).put("gui_windows",JSONArray()).put("directory_cleaned",true)
        app.getJSONObject("local").put("workers",0).put("owned_groups",0).put("waited",true)
        for(name in listOf("clients","objects","surfaces","pool_bytes","pool_count","pixel_bytes")) app.put(name,0)
        transport.put("state","Disconnected").put("owned_connections",0).put("desktop_pending",false)
        transport.getJSONObject("desktop").put("finished",true).put("channel_complete",true).put("router_drained",true).put("journal_pending",false).put("remote",remote(true))
        value.put("panels",JSONArray());value.getJSONObject("render").put("targets",JSONArray())
        value.getJSONObject("shutdown").put("sdk_bindings",0);value.getJSONObject("window_slots").put("occupied",0)
        return value
    }
    @Test fun manualObservationNeedsInputFrameParentedDialogAndAllRetirementProofs() {
        val trace=NormalLinuxGtkTrace(1,"3","2","4")
        trace.observe(1,runtime(),adapter(listOf(target())))
        assertFalse(trace.complete())
        trace.observe(1,runtime(2,2,true),adapter(listOf(target(),target("8"))))
        assertEquals(2L,trace.json().getLong("input_delta"));assertTrue(trace.json().getBoolean("dialog_parent_verified"))
        val stopped=closed();val receipts=adapter(emptyList(),listOf(target(),target("8")))
        stopped.getJSONObject("transport").put("desktop_pending",true)
        trace.observe(1,stopped,receipts);assertFalse(trace.cleanupComplete)
        stopped.getJSONObject("transport").put("desktop_pending",false)
        trace.observe(1,stopped,adapter(emptyList(),listOf(target())))
        assertFalse(trace.targetsRetired);assertFalse(trace.complete())
        trace.observe(1,stopped,receipts)
        assertTrue(trace.mainClosed);assertTrue(trace.targetsRetired);assertTrue(trace.cleanupComplete);assertTrue(trace.complete())
    }
    @Test fun sessionRuntimeRequestAndRemoteOwnerChangesAreRejected() {
        for(change in listOf<(JSONObject)->Unit>(
            {it.put("scene","4")}, {it.getJSONObject("transport").put("session","5")},
            {it.getJSONObject("transport").getJSONObject("desktop").put("request_id","5")},
            {it.getJSONObject("transport").getJSONObject("desktop").put("nonce","b".repeat(16))})) {
            val trace=NormalLinuxGtkTrace(1,"3","2","4");trace.observe(1,runtime(),adapter(listOf(target())))
            assertThrows(IllegalStateException::class.java) {trace.observe(1,runtime().also(change),adapter(listOf(target())))}
        }
        assertThrows(IllegalStateException::class.java) {NormalLinuxGtkTrace(1,"3","2","4").observe(2,runtime(),adapter(listOf(target())))}
    }
    @Test fun inputAndFrameRegressionOrUnrelatedDialogCannotBeEvidence() {
        val trace=NormalLinuxGtkTrace(1,"3","2","4");trace.observe(1,runtime(2,4),adapter(listOf(target())))
        assertThrows(IllegalStateException::class.java) {trace.observe(1,runtime(3,3),adapter(listOf(target())))}
        assertThrows(IllegalStateException::class.java) {trace.observe(1,runtime(1,4),adapter(listOf(target())))}
        val wrong=runtime(3,4,true).also {it.getJSONObject("app").getJSONArray("windows").getJSONObject(1).put("parent","99")}
        assertThrows(IllegalStateException::class.java) {trace.observe(1,wrong,adapter(listOf(target(),target("8"))))}
    }
    @Test fun oldGenerationReceiptCannotRetireAReplacementTarget() {
        val trace=NormalLinuxGtkTrace(1,"3","2","4");trace.observe(1,runtime(),adapter(listOf(target())))
        trace.observe(1,runtime(2,2,true,"2"),adapter(listOf(target("7","2"),target("8"))))
        trace.observe(1,closed(),adapter(emptyList(),listOf(target(),target("8"))))
        assertFalse(trace.targetsRetired);assertFalse(trace.complete())
        trace.observe(1,closed(),adapter(emptyList(),listOf(target(),target("7","2"),target("8"))))
        assertTrue(trace.complete())
    }
    @Test fun authenticationOrMissingSdkTargetDoesNotProveGuiReadiness() {
        val trace=NormalLinuxGtkTrace(1,"3","2","4")
        trace.observe(1,runtime(),adapter(emptyList()))
        assertTrue(trace.json().isNull("main_target"));assertFalse(trace.complete())
        val raw=runtime().also {it.getJSONObject("app").put("remote_gui_verified",false)}
        trace.observe(1,raw,adapter(listOf(target())))
        assertTrue(trace.json().isNull("main_target"));assertFalse(trace.complete())
    }
    @Test fun explicitStopLeaseExpiryAndFailureCanCleanUpWithoutPassingCloseObservation() {
        for(change in listOf<(JSONObject)->Unit>(
            {it.put("cancelled",true)}, {it.put("error","DESKTOP_FAILED")},
            {it.getJSONObject("remote").put("reason","stop")}, {it.getJSONObject("remote").put("reason","lease_expired")},
            {it.getJSONObject("remote").put("reason","input_eof")})) {
            val trace=NormalLinuxGtkTrace(1,"3","2","4")
            trace.observe(1,runtime(),adapter(listOf(target())))
            trace.observe(1,runtime(2,2,true),adapter(listOf(target(),target("8"))))
            val value=closed().also {change(it.getJSONObject("transport").getJSONObject("desktop"))}
            trace.observe(1,value,adapter(emptyList(),listOf(target(),target("8"))))
            assertTrue(trace.cleanupComplete);assertTrue(trace.resourcesCleared());assertFalse(trace.complete())
        }
    }
    @Test fun neverMappedCleanupCanReleaseObserverWithoutProvingGuiOrTargetRetirement() {
        val trace=NormalLinuxGtkTrace(1,"3","2","4")
        trace.observe(1,closed(),adapter(emptyList()))
        assertTrue(trace.resourcesCleared());assertFalse(trace.mainClosed);assertFalse(trace.targetsRetired)
        assertFalse(trace.cleanupComplete);assertFalse(trace.complete())
    }
    @Test fun noChildStartupFailureNeedsExplicitCleanupProofAndAllResourceCountsZero() {
        fun failed()=closed().also {value ->
            val app=value.getJSONObject("app")
            app.getJSONObject("local").put("waited",false)
            app.put("startup_failed",true).put("startup_cleanup_complete",true)
        }
        val trace=NormalLinuxGtkTrace(1,"3","2","4")
        trace.observe(1,failed(),adapter(emptyList()))
        assertTrue(trace.resourcesCleared());assertFalse(trace.cleanupComplete);assertFalse(trace.complete())
        for(change in listOf<(JSONObject)->Unit>(
            {it.put("startup_failed",false)}, {it.put("startup_cleanup_complete",false)}, {it.put("state","fail")},
            {it.put("directory_cleaned",false)}, {it.put("objects",1)}, {it.put("pool_bytes",1)},
            {it.getJSONObject("local").put("workers",1)}, {it.getJSONObject("local").put("owned_groups",1)})) {
            val incomplete=failed().also {change(it.getJSONObject("app"))}
            val rejected=NormalLinuxGtkTrace(1,"3","2","4")
            rejected.observe(1,incomplete,adapter(emptyList()))
            assertFalse(rejected.resourcesCleared());assertFalse(rejected.cleanupComplete);assertFalse(rejected.complete())
        }
    }
}
