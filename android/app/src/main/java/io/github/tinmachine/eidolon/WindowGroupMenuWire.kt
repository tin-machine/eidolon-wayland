package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject

/** Metadata-only menu eligibility. Native still validates launch/Board/pose and expected group revision on enqueue. */
internal object WindowGroupMenuWire {
    private fun rows(value:JSONArray):List<JSONObject> {
        check(value.length()<=9);return (0 until value.length()).map {value.getJSONObject(it)}
    }
    private fun id(value:JSONObject,name:String):String=(value.get(name) as? String)?.takeIf {
        it.matches(Regex("[1-9][0-9]{0,18}")) && it.toLongOrNull()!=null
    } ?: error("GROUP_MENU_ID_INVALID")
    private fun flag(value:JSONObject,name:String)=value.get(name) as? Boolean ?: error("GROUP_MENU_FLAG_INVALID")
    fun capture(runtime:Long,target:JSONObject,state:JSONObject):WindowGroupMenuPermit?=try {
        val key=WindowGroupWire.key(runtime,target)
        check(state.getString("state")=="Ready" && flag(state,"activity_visible") && state.getString("xr_state")=="FOCUSED" && id(state,"scene")==key.scene)
        val app=state.getJSONObject("app");check(id(app,"session")==key.session && app.isNull("error"))
        val windows=rows(app.getJSONArray("windows"));val plans=rows(state.getJSONArray("panels"))
        val render=state.getJSONObject("render");val draws=rows(render.getJSONArray("targets"))
        val revision=when(val value=render.get("theme_revision")) {is Int->value.toLong();is Long->value;else->error("GROUP_MENU_THEME_INVALID")}
        check(revision>=0)
        val report=state.getJSONObject("window_groups")
        check(report.has("error") && report.isNull("error") && report.has("blocked_session") && report.isNull("blocked_session"))
        val groups=rows(report.getJSONArray("groups"));check(groups.size<=1)
        fun ready(key:InputWindowKey):Boolean=windows.singleOrNull {id(it,"window")==key.window}?.getString("life")=="Mapped" &&
            plans.any {it.getString("action")=="create" && flag(it,"applied") && it.isNull("error") && WindowGroupWire.key(runtime,it.getJSONObject("target"))==key} &&
            draws.any {WindowGroupWire.key(runtime,it.getJSONObject("key"))==key && it.getLong("frames")>0}
        fun baseReady(key:InputWindowKey):Boolean {
            val pose=rows(state.getJSONObject("workspace").getJSONArray("windows")).singleOrNull {id(it,"session")==key.session && id(it,"window")==key.window}
                ?.getJSONObject("placement") ?: return false
            val applied=pose.optJSONObject("applied_pose") ?: return false
            val size=windows.single {id(it,"window")==key.window}.getJSONObject("sizing")
            return WindowGroupWire.key(runtime,applied.getJSONObject("target"))==key && applied.opt("pose_command")==pose.opt("pose_command") && PanelPoseCodec.near(applied.getJSONObject("pose"),pose.getJSONObject("pose")) &&
                size.optJSONObject("applied_physical_size_m")?.toString()==size.getJSONObject("physical_size_m").toString()
        }
        if(groups.isEmpty()) {
            val candidates=rows(report.getJSONArray("candidates"));check(candidates.size>=2)
            val members=candidates.map {candidate->
                check(id(candidate,"session")==key.session)
                val window=id(candidate,"window")
                val draw=draws.single {id(it.getJSONObject("key"),"session")==key.session && id(it.getJSONObject("key"),"window")==window}
                WindowGroupWire.key(runtime,draw.getJSONObject("key"))
            }
            check(members.all {ready(it) && baseReady(it)})
            WindowGroupMenuPermit.create(key,revision,null,null,"expanded","stable",true,true,members)
        } else {
            val group=groups.single();val scope=group.getJSONObject("scope")
            check(id(scope,"runtime")==runtime.toString() && id(scope,"session")==key.session && id(scope,"scene")==key.scene)
            check(scope.getJSONObject("application").let {it.keys().asSequence().toSet()==setOf("kind") && it.getString("kind")=="foot"})
            val members=rows(group.getJSONArray("windows"));check(members.count {flag(it,"front")}==1)
            val keys=members.map {WindowGroupWire.key(runtime,it.getJSONObject("target"))}
            check(keys.all(::ready))
            WindowGroupMenuPermit.create(key,revision,id(group,"id"),id(group,"revision"),group.getString("layout"),group.getString("phase"),
                flag(group,"source_ready"),members.singleOrNull {WindowGroupWire.key(runtime,it.getJSONObject("target"))==key}?.let {flag(it,"front")}==true,keys)
        }
    } catch(_:Exception) {null}
}
