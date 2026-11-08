package io.github.tinmachine.eidolon

import org.json.JSONObject

/** Activation follows the Quest viewer sampling gate; preparation alone does not expose a new action. */
object WindowRecenterPolicy { const val ENABLED=true }

object WindowRecenterWire {
    fun sameTarget(a:JSONObject,b:JSONObject)=listOf("session","window","scene","generation").all {key ->
        (a.opt(key) as? String)?.let {it==b.opt(key) && it.toLongOrNull()?.let {id->id>0}==true}==true
    }
    private fun row(array:org.json.JSONArray?,matches:(JSONObject)->Boolean):JSONObject? {
        if(array==null || array.length()>9) return null
        return (0 until array.length()).mapNotNull {array.optJSONObject(it)}.singleOrNull(matches)
    }
    fun placement(state:JSONObject,target:JSONObject):JSONObject?=row(state.optJSONObject("workspace")?.optJSONArray("windows")) {
        it.opt("session")==target.opt("session") && it.opt("window")==target.opt("window")
    }?.optJSONObject("placement")
    fun read(state:JSONObject,target:JSONObject):WindowRecenterGeometry?=runCatching {
        val app=state.getJSONObject("app");check(app.opt("session")==target.opt("session"))
        val window=checkNotNull(row(app.optJSONArray("windows")) {it.opt("window")==target.opt("window") && it.optString("life")=="Mapped"})
        val plan=checkNotNull(row(state.optJSONArray("panels")) {sameTarget(it.getJSONObject("target"),target) && it.optString("action")=="create" && it.optBoolean("applied") && it.isNull("error")})
        val placement=checkNotNull(placement(state,target));val sizing=window.getJSONObject("sizing")
        val physical=sizing.getJSONObject("physical_size_m")
        val pixels=JSONObject().put("logical",dimensions(window.getJSONObject("logical_size"))).put("producer",dimensions(sizing.getJSONObject("producer_size_px")))
            .put("canvas",plan.optJSONObject("decoration")?.getJSONObject("canvas")?.let(::dimensions)).toString()
        WindowRecenterGeometry(pose(placement.getJSONObject("pose")),
            command(placement,"pose_command"),WindowPhysicalSize(physical.getDouble("width_m").toFloat(),physical.getDouble("height_m").toFloat()),
            command(sizing,"size_command"),pixels).takeIf {it.valid()}
    }.getOrNull()
    fun applied(state:JSONObject,target:JSONObject,request:String?,desired:WindowMotionPose):Boolean=runCatching {
        val applied=placement(state,target)?.optJSONObject("applied_pose") ?: return false
        val sameCommand=if(request==null) applied.isNull("pose_command") else applied.opt("pose_command")==request
        sameCommand && sameTarget(applied.getJSONObject("target"),target) && pose(applied.getJSONObject("pose")).near(desired)
    }.getOrDefault(false)
    private fun command(value:JSONObject,key:String):String?=if(value.isNull(key)) null else value.getString(key)
    private fun dimensions(value:JSONObject):JSONObject {
        for(key in listOf("width","height")) {
            val size=value.opt(key) as? Number ?: error("dimension_unavailable")
            check(size.toLong() in 1..Int.MAX_VALUE && size.toDouble()==size.toLong().toDouble())
        }
        return JSONObject().put("width",value.getLong("width")).put("height",value.getLong("height"))
    }
    private fun pose(value:JSONObject):WindowMotionPose {
        val p=value.getJSONArray("position_m");val q=value.getJSONArray("rotation_xyzw");check(p.length()==3 && q.length()==4)
        return WindowMotionPose((0..2).map {p.getDouble(it)},(0..3).map {q.getDouble(it)}).also {check(it.valid(true))}
    }
}
