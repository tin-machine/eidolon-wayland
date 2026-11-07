package io.github.tinmachine.eidolon

import org.json.JSONObject

/** GUI固定検査の不変owner/target。UI選択状態から別windowを補完しない。 */
internal object GtkFixtureBinding {
    data class Owner(val handle:Long,val scene:String,val session:String,val nonce:String,val pid:Int,val start:String,val group:Int)
    data class Target(val session:String,val window:String,val scene:String,val generation:String) {
        fun json()=JSONObject().put("session",session).put("window",window).put("scene",scene).put("generation",generation)
    }
    private fun id(value:JSONObject,key:String):String = (value.get(key) as? String)?.also {
        check(it.matches(Regex("[1-9][0-9]{0,18}")) && it.toLongOrNull()!=null) {"GTK_ID_INVALID"}
    } ?: error("GTK_ID_INVALID")
    fun target(value:JSONObject):Target {
        check(value.keys().asSequence().toSet()==setOf("session","window","scene","generation")) {"GTK_TARGET_INVALID"}
        return Target(id(value,"session"),id(value,"window"),id(value,"scene"),id(value,"generation"))
    }
    fun owner(runtime:JSONObject,handle:Long):Owner {
        check(handle>0 && runtime.getString("state")=="Ready" && runtime.getBoolean("activity_visible") && runtime.getString("xr_state")=="FOCUSED") {"GTK_RUNTIME_UNAVAILABLE"}
        val app=runtime.getJSONObject("app");val transport=runtime.getJSONObject("transport")
        check(app.getString("mode")=="foot" && app.getString("state")=="running" && app.isNull("error") &&
            transport.getString("state")=="Ready" && transport.getString("session")==app.getString("session")) {"GTK_SESSION_CHANGED"}
        val forward=transport.getJSONObject("app_forward");val remote=forward.getJSONObject("remote")
        val nonce=app.getString("nonce");check(nonce.matches(Regex("[0-9a-f]{16}")) && forward.getString("nonce")==nonce &&
            forward.getString("state")=="running" && remote.getString("phase")=="running" && remote.isNull("error") &&
            remote.getJSONObject("tools").getInt("uid")==10158) {"GTK_LEASE_CHANGED"}
        val child=remote.getJSONObject("child");val pid=child.getInt("pid");val group=child.getInt("group")
        check(pid>0 && pid==group) {"GTK_LEASE_CHANGED"}
        return Owner(handle,id(runtime,"scene"),id(app,"session"),nonce,pid,id(child,"start"),group)
    }
    fun targetFor(runtime:JSONObject,window:String):Target? {
        val app=runtime.getJSONObject("app");val session=app.getString("session");val scene=runtime.getString("scene")
        val plans=runtime.getJSONArray("panels");val candidates=mutableSetOf<Target>()
        for(i in 0 until plans.length()) {
            val p=plans.getJSONObject(i)
            if(p.optString("action")!="create" || !p.optBoolean("applied") || !p.isNull("error")) continue
            val key=target(p.getJSONObject("target"))
            if(key.window==window && key.session==session && key.scene==scene) candidates.add(key)
        }
        check(candidates.size<=1) {"GTK_TARGET_AMBIGUOUS"}
        val candidate=candidates.singleOrNull() ?: return null
        val frames=runtime.getJSONObject("render").getJSONArray("targets")
        val matching=(0 until frames.length()).map {frames.getJSONObject(it)}.filter {
            val key=it.getJSONObject("key")
            key.optString("session")==candidate.session && key.optString("window")==candidate.window && target(key)==candidate
        }
        check(matching.size<=1) {"GTK_TARGET_AMBIGUOUS"}
        val windows=app.getJSONArray("windows")
        val producer=(0 until windows.length()).map {windows.getJSONObject(it)}.singleOrNull {it.optString("window")==window}
            ?.optString("frame")?.toLongOrNull() ?: return null
        return candidate.takeIf {matching.singleOrNull()?.let {it.getLong("frames")>0 && it.optString("content_frame").toLongOrNull()?.let {n->n>0 && n>=producer}==true}==true}
    }
    /** Mapped消滅のほか、native panel/BoardとSDK所有物の成功回収receiptを照合する。 */
    fun retired(runtime:JSONObject,adapter:JSONObject?,owner:Owner,owned:Set<Target>,foot:Set<String>):Boolean =
        retired(runtime,adapter,owner.handle,owner.scene,owned,foot)
    fun retired(runtime:JSONObject,adapter:JSONObject?,runtimeHandle:Long,scene:String,owned:Set<Target>,foot:Set<String>):Boolean {
        if(adapter==null || adapter.optString("scene")!=scene || owned.isEmpty()) return false
        val windows=owned.map {it.window}.toSet()
        fun rows(a:org.json.JSONArray)=(0 until a.length()).map {a.getJSONObject(it)}
        if(rows(runtime.getJSONArray("panels")).any {it.getJSONObject("target").optString("window") in windows}) return false
        if(rows(runtime.getJSONObject("render").getJSONArray("targets")).any {it.getJSONObject("key").optString("window") in windows}) return false
        val targets=rows(adapter.getJSONArray("targets"))
        if(targets.size!=foot.size || targets.any {it.getJSONObject("target").optString("window") !in foot ||
            it.optBoolean("retiring") || it.optBoolean("sdk_disposed") || it.optBoolean("disposal_failed") || !it.optBoolean("listener_owned")}) return false
        val receipts=rows(adapter.optJSONArray("retired") ?: return false)
        if(!owned.all {key->receipts.any {it.optString("runtime")==runtimeHandle.toString() && target(it.getJSONObject("target"))==key &&
            it.optBoolean("sdk_disposed") && it.optBoolean("sdk_binding_released") && !it.optBoolean("disposal_failed")}}) return false
        if(runtime.getJSONObject("shutdown").getInt("sdk_bindings")!=foot.size) return false
        val board=runtime.optJSONObject("window_slots") ?: return false
        val entries=rows(board.getJSONArray("entries"))
        return !board.getBoolean("cleanup_required") && board.getInt("occupied")==foot.size && entries.size==foot.size &&
            entries.all {val state=it.getJSONObject("state");state.optString("phase")=="bound" && state.optString("window") in foot} &&
            board.getJSONArray("unassigned").length()==0
    }
    fun window(runtime:JSONObject,appId:String):JSONObject? {
        val rows=runtime.getJSONObject("app").getJSONArray("windows")
        val matching=(0 until rows.length()).map {rows.getJSONObject(it)}.filter {it.optString("app_id")==appId && it.optString("life")=="Mapped"}
        check(matching.size<=1) {"GTK_WINDOW_AMBIGUOUS"}
        return matching.singleOrNull()
    }
}
