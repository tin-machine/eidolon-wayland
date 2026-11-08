package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class FreedoomGuiBindingTest {
    private val appId="io.github.tinmachine.eidolon.gui.g-0123456789abcdef-2"
    private fun state():JSONObject = JSONObject().put("app",JSONObject().put("nonce","0123456789abcdef").put("session","5"))
        .put("transport",JSONObject().put("app_forward",JSONObject().put("remote",JSONObject().put("terminal_launches",JSONArray()) .put("gui_receipts",JSONArray()))))
    private fun remote(value:JSONObject)=value.getJSONObject("transport").getJSONObject("app_forward").getJSONObject("remote")
    private fun launch()=JSONObject().put("request_id","17").put("terminal_id","2").put("mode","gui").put("accepted",true)
    private fun receipt()=JSONObject().put("request_id","17").put("terminal_id","2").put("session","5").put("nonce","0123456789abcdef").put("uid",10158)
        .put("app_id",appId).put("reason","natural").put("child_returncode",0).put("worker_returncode",0).put("cleanup_complete",true).put("pipes_closed",true)
        .put("assets_runtime_unchanged",true).put("open_pidfds",0).put("term_signals",0).put("kill_signals",0).put("reaped",1)
    @Test fun launchRequiresOneExactRequestAcceptedGuiReceipt() {
        val value=state();assertNull(FreedoomGuiBinding.launch(value,"17"));remote(value).getJSONArray("terminal_launches").put(launch())
        assertEquals("2" to appId,FreedoomGuiBinding.launch(value,"17"));assertNull(FreedoomGuiBinding.launch(value,"18"))
        val row=remote(value).getJSONArray("terminal_launches").getJSONObject(0);row.put("accepted",JSONObject.NULL);assertNull(FreedoomGuiBinding.launch(value,"17"))
        row.put("accepted",false);assertThrows(IllegalStateException::class.java) {FreedoomGuiBinding.launch(value,"17")}
        row.put("accepted",true).put("mode","terminal");assertThrows(IllegalStateException::class.java) {FreedoomGuiBinding.launch(value,"17")}
        row.put("mode","gui");remote(value).getJSONArray("terminal_launches").put(launch());assertThrows(IllegalStateException::class.java) {FreedoomGuiBinding.launch(value,"17")}
    }
    @Test fun receiptCannotUseOtherUidSessionNonceRequestLaunchOrAppId() {
        for((field,bad) in listOf("uid" to 10159,"session" to "6","nonce" to "ffffffffffffffff","terminal_id" to "3","app_id" to "foot")) {
            val value=state();remote(value).getJSONArray("gui_receipts").put(receipt().put(field,bad))
            assertThrows(IllegalStateException::class.java) {FreedoomGuiBinding.receipt(value,"17","2",appId)}
        }
        val value=state();remote(value).getJSONArray("gui_receipts").put(receipt().put("request_id","18"));assertNull(FreedoomGuiBinding.receipt(value,"17","2",appId))
    }
    @Test fun cleanSignalOrPendingMustNotBecomeNaturalExit() {
        assertTrue(FreedoomGuiBinding.natural(receipt()))
        for((field,bad) in listOf("reason" to "signal","child_returncode" to -15,"worker_returncode" to 2,"cleanup_complete" to false,"pipes_closed" to false,
            "assets_runtime_unchanged" to false,"open_pidfds" to 1,"term_signals" to 1,"kill_signals" to 1,"reaped" to 0)) {
            assertFalse(FreedoomGuiBinding.natural(receipt().put(field,bad)))
        }
    }
    @Test fun stopRequiresEverySdkAndBoardOwnerZero() {
        val runtime=JSONObject().put("scene","3").put("panels",JSONArray()).put("render",JSONObject().put("targets",JSONArray()))
            .put("shutdown",JSONObject().put("sdk_bindings",0)).put("transport",JSONObject().put("state","Idle").put("pending_cleanup",JSONArray()))
            .put("window_slots",JSONObject().put("occupied",0).put("cleanup_required",false).put("entries",JSONArray()).put("unassigned",JSONArray()))
        val modes=JSONObject();listOf("entity_owners","object_owners","mesh_owners","material_owners","texture_owners","cleanup_pending_owners","pixel_slots_in_use").forEach {modes.put(it,0)}
        val menus=JSONObject();listOf("owners","cleanup_pending_owners","entity_owners","listener_owners","object_owners","mesh_owners","material_owners","texture_owners").forEach {menus.put(it,0)}
        val adapter=JSONObject().put("scene","3").put("bindings",0).put("targets",JSONArray()).put("created",3).put("destroyed",3).put("listeners",0).put("decoration_entities",0)
            .put("window_controls",JSONObject().put("entity_owners",0).put("listener_owners",0)).put("window_mode_badges",modes).put("window_menus",menus)
        assertTrue(FreedoomGuiBinding.stopped(runtime,adapter));assertFalse(FreedoomGuiBinding.stopped(runtime,null))
        for(key in modes.keys().asSequence().toList()) {modes.put(key,1);assertFalse(FreedoomGuiBinding.stopped(runtime,adapter));modes.put(key,0)}
        for(key in menus.keys().asSequence().toList()) {menus.put(key,1);assertFalse(FreedoomGuiBinding.stopped(runtime,adapter));menus.put(key,0)}
        runtime.getJSONObject("window_slots").put("cleanup_required",true);assertFalse(FreedoomGuiBinding.stopped(runtime,adapter))
    }
}
