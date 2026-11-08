package io.github.tinmachine.eidolon

import org.json.JSONObject

/** 全窓のSDK適用済み配置を一度捕捉する。候補選択はSDK衝突の成功証拠ではない。 */
class WindowRecenterScene private constructor(private val selected:String,
    private val windows:Map<String,WindowRecenterGeometry>) {
    fun choose(viewer:WindowMotionPose):WindowRecenterChoice.Result = WindowRecenterChoice.choose(viewer,selected,
        checkNotNull(windows[selected]).physical,windows.filterKeys {it!=selected}.map {(id,g)->WindowRecenterOccupant(id,g.pose,g.physical)})
    fun matches(state:JSONObject,target:JSONObject)=read(state,target)?.let {current ->
        selected==current.selected && windows.keys==current.windows.keys && windows.all {(id,g)->
            current.windows[id]?.let {g.sameDimensions(it) && g.samePlacement(it)}==true
        }
    }==true
    companion object {
        private fun identity(target:JSONObject)=listOf("session","window","scene","generation").joinToString("/") {
            (target.get(it) as? String)?.takeIf {id->id.matches(Regex("[1-9][0-9]{0,18}")) && id.toLongOrNull()!=null} ?: error("invalid_target")
        }
        fun read(state:JSONObject,target:JSONObject):WindowRecenterScene?=runCatching {
            check(state.getString("scene")==target.getString("scene"))
            // Group overlay geometry is not the persisted base placement.
            check(state.getJSONObject("window_groups").getJSONArray("groups").length()==0)
            val plans=state.getJSONArray("panels");check(plans.length() in 1..9)
            val mapped=state.getJSONObject("app").getJSONArray("windows")
            val mappedIds=(0 until mapped.length()).map {mapped.getJSONObject(it)}.filter {it.optString("life")=="Mapped"}.map {it.getString("window")}.toSet()
            val captured=linkedMapOf<String,WindowRecenterGeometry>()
            val windowIds=mutableSetOf<String>()
            for(i in 0 until plans.length()) {
                val plan=plans.getJSONObject(i);val key=plan.getJSONObject("target")
                check(plan.getString("action")=="create" && plan.getBoolean("applied") && plan.isNull("error"))
                check(key.getString("session")==target.getString("session") && key.getString("scene")==target.getString("scene"))
                val id=identity(key);check(id !in captured && windowIds.add(key.getString("window")))
                val geometry=checkNotNull(WindowRecenterWire.read(state,key))
                val sizing=(0 until mapped.length()).map {mapped.getJSONObject(it)}.single {it.getString("window")==key.getString("window")}.getJSONObject("sizing")
                val size=sizing.getJSONObject("physical_size_m");val applied=sizing.getJSONObject("applied_physical_size_m")
                check(listOf("width_m","height_m").all {size.getDouble(it)==applied.getDouble(it)})
                check(WindowRecenterWire.applied(state,key,geometry.poseCommand,geometry.pose))
                val draws=state.getJSONObject("render").getJSONArray("targets")
                check((0 until draws.length()).count {j->draws.getJSONObject(j).let {
                    WindowRecenterWire.sameTarget(it.getJSONObject("key"),key) && it.getLong("frames")>0
                }}==1)
                captured[id]=geometry
            }
            check(windowIds==mappedIds && identity(target) in captured)
            WindowRecenterScene(identity(target),captured)
        }.getOrNull()
    }
}
