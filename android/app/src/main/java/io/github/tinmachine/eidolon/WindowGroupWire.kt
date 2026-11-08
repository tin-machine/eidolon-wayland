package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject

/** 通常workerで読むmetadataだけのparser。重いnative snapshotをUIの各key処理で取り直さない。 */
internal object WindowGroupWire {
    private fun rows(value:JSONArray):List<JSONObject> {check(value.length()<=9);return (0 until value.length()).map {value.getJSONObject(it)}}
    private fun id(value:JSONObject,name:String):String=(value.get(name) as? String)?.takeIf {
        it.matches(Regex("[1-9][0-9]{0,18}")) && it.toLongOrNull()!=null
    } ?: error("GROUP_ID_INVALID")
    fun key(runtime:Long,target:JSONObject):InputWindowKey {
        check(target.keys().asSequence().toSet()==setOf("session","window","scene","generation"))
        return InputWindowKey(runtime,id(target,"session"),id(target,"window"),id(target,"scene"),id(target,"generation"))
    }
    fun decode(runtime:Long,value:JSONObject?):WindowGroupInputGate = try {
        check(runtime>0 && value!=null && setOf("groups","candidates","blocked_session","error").all(value::has))
        if(!value.isNull("error")) WindowGroupInputGate.rejected(runtime) else {
            val groups=rows(value.getJSONArray("groups"));check(groups.map {id(it,"id")}.toSet().size==groups.size)
            val members=groups.flatMap {group ->
                val scope=group.getJSONObject("scope");check(id(scope,"runtime")==runtime.toString())
                val session=id(scope,"session");val scene=id(scope,"scene");id(group,"id");id(group,"revision")
                val phase=group.getString("phase");check(phase in setOf("stable","animating","settling","blocked","empty"))
                val ready=group.get("source_ready") as? Boolean ?: error("GROUP_FLAG_INVALID")
                rows(group.getJSONArray("windows")).also {check(it.isNotEmpty() && it.size<=9)}.map {window ->
                    val key=key(runtime,window.getJSONObject("target"));check(key.session==session && key.scene==scene)
                    val allowed=window.get("input_allowed") as? Boolean ?: error("GROUP_FLAG_INVALID")
                    WindowGroupInputGate.Member(key,allowed && ready && phase=="stable")
                }
            }
            val candidates=rows(value.getJSONArray("candidates")).map {WindowGroupInputGate.Window(id(it,"session"),id(it,"window"))}
            WindowGroupInputGate(runtime,groups.isNotEmpty(),if(value.isNull("blocked_session")) null else id(value,"blocked_session"),members,candidates)
        }
    } catch(_:Exception) {WindowGroupInputGate.rejected(runtime.coerceAtLeast(0))}
}
