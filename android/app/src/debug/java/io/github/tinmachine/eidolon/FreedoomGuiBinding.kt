package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject

/** GUI receiptと専用cookieの一対一対応。先に現れた窓や通常footのcookieを流用しない。 */
internal object FreedoomGuiBinding {
    private fun rows(array:JSONArray)=(0 until array.length()).map {array.getJSONObject(it)}
    fun launch(runtime:JSONObject,request:String):Pair<String,String>? {
        check(request.matches(Regex("[1-9][0-9]{0,18}"))) {"FREEDOOM_REQUEST_INVALID"}
        val app=runtime.getJSONObject("app");val nonce=app.getString("nonce")
        check(nonce.matches(Regex("[0-9a-f]{16}"))) {"FREEDOOM_BINDING_INVALID"}
        val remote=runtime.getJSONObject("transport").getJSONObject("app_forward").getJSONObject("remote")
        val matched=rows(remote.getJSONArray("terminal_launches")).filter {it.optString("request_id")==request}
        check(matched.size<=1) {"FREEDOOM_RECEIPT_AMBIGUOUS"}
        val row=matched.singleOrNull() ?: return null
        check(row.getString("mode")=="gui") {"FREEDOOM_RECEIPT_MODE"}
        if(row.isNull("accepted")) return null
        check(row.getBoolean("accepted")) {"FREEDOOM_LAUNCH_REJECTED"}
        val id=row.getString("terminal_id");check(id.matches(Regex("[1-9][0-9]{0,18}"))) {"FREEDOOM_BINDING_INVALID"}
        return id to "io.github.tinmachine.eidolon.gui.g-$nonce-$id"
    }
    fun receipt(runtime:JSONObject,request:String,launch:String,appId:String):JSONObject? {
        val app=runtime.getJSONObject("app");val remote=runtime.getJSONObject("transport").getJSONObject("app_forward").getJSONObject("remote")
        val matches=rows(remote.getJSONArray("gui_receipts")).filter {it.optString("request_id")==request}
        check(matches.size<=1) {"FREEDOOM_RECEIPT_AMBIGUOUS"}
        val row=matches.singleOrNull() ?: return null
        check(row.getString("session")==app.getString("session") && row.getString("nonce")==app.getString("nonce") &&
            row.getInt("uid")==10158 && row.getString("terminal_id")==launch && row.getString("app_id")==appId) {"FREEDOOM_BINDING_INVALID"}
        return row
    }
    fun natural(row:JSONObject):Boolean = row.optString("reason")=="natural" && !row.isNull("child_returncode") && row.getInt("child_returncode")==0 &&
        row.getInt("worker_returncode")==0 && row.getBoolean("cleanup_complete") && row.getBoolean("pipes_closed") &&
        row.getBoolean("assets_runtime_unchanged") && row.getInt("open_pidfds")==0 && row.getInt("term_signals")==0 && row.getInt("kill_signals")==0 && row.getInt("reaped")>=1
    fun stopped(runtime:JSONObject,adapter:JSONObject?):Boolean {
        if(adapter==null || adapter.optString("scene")!=runtime.optString("scene")) return false
        val transport=runtime.getJSONObject("transport");val board=runtime.getJSONObject("window_slots")
        if(transport.optString("state")!="Idle" || transport.getJSONArray("pending_cleanup").length()!=0 || runtime.getJSONArray("panels").length()!=0 ||
            runtime.getJSONObject("render").getJSONArray("targets").length()!=0 || runtime.getJSONObject("shutdown").getInt("sdk_bindings")!=0 ||
            adapter.getInt("bindings")!=0 || adapter.getJSONArray("targets").length()!=0 || adapter.getLong("created")!=adapter.getLong("destroyed") ||
            adapter.getInt("listeners")!=0 || adapter.getInt("decoration_entities")!=0 || board.getInt("occupied")!=0 || board.getBoolean("cleanup_required") ||
            board.getJSONArray("entries").length()!=0 || board.getJSONArray("unassigned").length()!=0) return false
        val controls=adapter.getJSONObject("window_controls");if(controls.getInt("entity_owners")!=0 || controls.getInt("listener_owners")!=0) return false
        val modes=adapter.getJSONObject("window_mode_badges");val menus=adapter.getJSONObject("window_menus")
        return listOf("entity_owners","object_owners","mesh_owners","material_owners","texture_owners","cleanup_pending_owners","pixel_slots_in_use").all {modes.getInt(it)==0} &&
            listOf("owners","cleanup_pending_owners","entity_owners","listener_owners","object_owners","mesh_owners","material_owners","texture_owners").all {menus.getInt(it)==0}
    }
}
