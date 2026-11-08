package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject

/** 既存SDK bindingの表示だけを選ぶ。workspace/BoardやSurfaceの所有を変更しない。 */
internal object WindowGroupPresentation {
    sealed interface Selection
    data object Unowned:Selection
    data object Pending:Selection
    data class Owned(val runtime:Long,val key:InputWindowKey,val group:String,val revision:String,val phase:String,
        val sourceReady:Boolean,val pose:JSONObject,val size:JSONObject,val requiredFrame:Long?,val geometryApplied:Boolean,
        val front:Boolean,val inputAllowed:Boolean):Selection {
        val stamp get()="$runtime:$group:$revision:${key.identity}"
        val settling get()=phase=="settling" && sourceReady
    }
    private fun rows(value:JSONArray):List<JSONObject> {check(value.length()<=9);return (0 until value.length()).map {value.getJSONObject(it)}}
    private fun id(value:JSONObject,name:String):String=(value.get(name) as? String)?.takeIf {
        it.matches(Regex("[1-9][0-9]{0,18}")) && it.toLongOrNull()!=null
    } ?: error("GROUP_ID_INVALID")
    private fun flag(value:JSONObject,name:String)=value.get(name) as? Boolean ?: error("GROUP_FLAG_INVALID")
    fun select(runtime:Long,target:JSONObject,report:JSONObject?):Selection=try {
        val wanted=WindowGroupWire.key(runtime,target);check(report!=null)
        val groups=rows(report.getJSONArray("groups"));val all=groups.flatMap {group ->
            val scope=group.getJSONObject("scope");check(id(scope,"runtime")==runtime.toString())
            val session=id(scope,"session");val scene=id(scope,"scene");val groupId=id(group,"id");val revision=id(group,"revision")
            val phase=group.getString("phase");check(phase in setOf("stable","animating","settling","blocked"))
            rows(group.getJSONArray("windows")).also {check(it.isNotEmpty())}.map {window ->
                val key=WindowGroupWire.key(runtime,window.getJSONObject("target"));check(key.session==session && key.scene==scene)
                val pose=window.getJSONObject("pose");check(PanelPoseCodec.valid(pose))
                val size=window.getJSONObject("physical_size");check(size.keys().asSequence().toSet()==setOf("width_m","height_m"))
                check(listOf("width_m","height_m").all {(size.get(it) as? Number)?.toDouble()?.let {n->n.isFinite() && n in .25..3.0}==true})
                val frame=if(window.isNull("required_frame")) null else id(window,"required_frame").toLong()
                Owned(runtime,key,groupId,revision,phase,flag(group,"source_ready"),JSONObject(pose.toString()),JSONObject(size.toString()),frame,
                    flag(window,"geometry_applied"),flag(window,"front"),flag(window,"input_allowed"))
            }
        }
        check(all.size<=9 && all.map {it.key.session to it.key.window}.toSet().size==all.size)
        val member=all.singleOrNull {it.key.runtime==wanted.runtime && it.key.session==wanted.session && it.key.window==wanted.window}
        if(member!=null) {
            if(member.key!=wanted || !report.isNull("error")) Pending else member
        } else {
            val pending=groups.isNotEmpty() && rows(report.getJSONArray("candidates")).any {id(it,"session")==wanted.session && id(it,"window")==wanted.window}
            if(pending) Pending else Unowned
        }
    } catch(_:Exception) {Pending}
}
