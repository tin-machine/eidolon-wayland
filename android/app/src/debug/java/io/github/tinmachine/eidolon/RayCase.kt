package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.Process
import android.os.SystemClock
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Vector3
import com.meta.spatial.runtime.ButtonBits
import com.meta.spatial.runtime.HitInfo
import com.meta.spatial.runtime.Scene
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.abs

/** 実SDKのquad交差とlistenerを合成ray/専用source Entityで検査する。物理操作の代用とはしない。 */
object RayCase {
    fun start(nonce: String, adapter: SpatialPanelAdapter, scene: () -> Scene, complete: () -> Unit) {
        Check(nonce,adapter,scene,complete).start()
    }
    private class Check(val nonce:String,val adapter:SpatialPanelAdapter,val scene:()->Scene,val complete:()->Unit) : Runnable {
        val ui=Handler(Looper.getMainLooper())
        val result=JSONObject().put("case","ray").put("nonce",nonce).put("result","running")
            .put("synthetic_input",true).put("synthetic_visibility",false).put("physical_input_verified",false)
            .put("dispatch","Scene.onInput").put("raycast","Scene.lineSegmentIntersect")
        val rows=JSONArray()
        val inputs=JSONArray()
        val hits=mutableListOf<HitInfo>()
        var sent=0
        val points=listOf(Triple("top_left",0.01,0.01),Triple("top_right",0.99,0.01),
            Triple("bottom_left",0.01,0.99),Triple("bottom_right",0.99,0.99),Triple("center",0.5,0.5))
        var source:Entity?=null
        var stage="ready"
        var key:JSONObject?=null
        var handle=0L
        val deadline=SystemClock.elapsedRealtime()+45000
        fun start() { NativeSessionHost.publishCheck(result); ui.post(this) }
        override fun run() {
            try {
                check(SystemClock.elapsedRealtime()<deadline)
                val current=NativeSessionHost.currentHandle()
                if (current>0) {
                    val snapshot=JSONObject(NativeBridge.nativeSnapshot(current))
                    val runtime=snapshot.optJSONObject("runtime")
                    val render=runtime?.getJSONObject("render")
                    val targets=render?.getJSONArray("targets")
                    val target=if (targets?.length()==1) targets.getJSONObject(0) else null
                    when(stage) {
                        "ready" -> if (runtime?.getString("state")=="Ready" && runtime.getBoolean("activity_visible") &&
                            runtime.getString("xr_state") in setOf("VISIBLE","FOCUSED") && target?.optString("role")=="diagnostic" && target.getLong("frames")>=3) {
                            handle=current; key=checkNotNull(adapter.currentKey())
                            val entity=Entity.create(emptyList()); source=entity
                            check(adapter.beginInputCapture(entity.id) { if(inputs.length()<16) inputs.put(it) })
                            result.put("source",entity.id.toString()).put("target",key).put("ready",runtime).put("ui_thread",Process.myTid())
                            stage="points"
                        }
                        "points" -> {
                            check(current==handle && adapter.currentKey().toString()==key.toString())
                            if (rows.length()==points.size) {
                                stage="inputs"
                            } else {
                                val (label,u,v)=points[rows.length()]
                                val x=(-0.8+(u-0.5)*1.2).toFloat()
                                val y=(1.4+(0.5-v)*0.9).toFloat()
                                if (label=="center") result.put("perpendicular_center_hit",scene().lineSegmentIntersect(Vector3(x,y,0.2f),Vector3(x,y,2.5f))!=null)
                                // 既知のpanel上の同じ点を、視点側からの斜めのsegmentで狙う。
                                val factor=(2.5f-0.2f)/(1.6f-0.2f)
                                val from=Vector3(0f,1.4f,0.2f)
                                val to=Vector3(x*factor,1.4f+(y-1.4f)*factor,2.5f)
                                val hit=scene().lineSegmentIntersect(from,to)
                                if (hit==null) { ui.postDelayed(this,32); return }
                                val hitEntity=checkNotNull(hit.entity)
                                check(hitEntity.id==adapter.currentEntityId())
                                hits.add(hit)
                                rows.put(JSONObject().put("label",label).put("expected",JSONArray(listOf(u,v)))
                                    .put("uv",JSONArray(listOf(hit.textureCoordinate.x.toDouble(),hit.textureCoordinate.y.toDouble())))
                                    .put("world",JSONArray(listOf(x.toDouble(),y.toDouble(),1.6)))
                                    .put("hit_point",JSONArray(listOf(hit.point.x.toDouble(),hit.point.y.toDouble(),hit.point.z.toDouble())))
                                    .put("ray_start",JSONArray(listOf(from.x.toDouble(),from.y.toDouble(),from.z.toDouble())))
                                    .put("ray_end",JSONArray(listOf(to.x.toDouble(),to.y.toDouble(),to.z.toDouble())))
                                    .put("entity",hitEntity.id.toString()))
                            }
                        }
                        "inputs" -> {
                            check(current==handle && adapter.currentKey().toString()==key.toString())
                            if (inputs.length()<sent*2) { ui.postDelayed(this,32); return }
                            if (sent==hits.size) {
                                scene().onNoIntersectionInput(checkNotNull(source),0,0)
                                scene().resolveInput(); stage="stop"
                            } else {
                                val hit=hits[sent++]
                                scene().onInput(hit,checkNotNull(source),ButtonBits.ButtonTriggerR,ButtonBits.ButtonTriggerR)
                                scene().onInput(hit,checkNotNull(source),ButtonBits.ButtonTriggerR,0)
                            }
                        }
                        "stop" -> if ((0 until inputs.length()).any { inputs.getJSONObject(it).getString("event")=="stop" }) {
                            check(rows.length()==5 && inputs.length()==11)
                            val top=rows.getJSONObject(0).getJSONArray("uv").getDouble(1)
                            val bottom=rows.getJSONObject(2).getJSONArray("uv").getDouble(1)
                            val origin=when {
                                abs(top-0.01)<0.02 && abs(bottom-0.99)<0.02 -> "top_left"
                                abs(top-0.99)<0.02 && abs(bottom-0.01)<0.02 -> "bottom_left"
                                else -> error("UNKNOWN_UV_ORIGIN")
                            }
                            for(index in 0 until rows.length()) {
                                val row=rows.getJSONObject(index)
                                val uv=row.getJSONArray("uv"); val expected=row.getJSONArray("expected")
                                check(abs(uv.getDouble(0)-expected.getDouble(0))<0.02)
                                check(abs((if(origin=="top_left") uv.getDouble(1) else 1.0-uv.getDouble(1))-expected.getDouble(1))<0.02)
                            }
                            cleanup()
                            result.put("cycles",rows).put("inputs",inputs).put("uv_origin",origin)
                            stage="visible"
                        }
                        "visible" -> if(runtime?.getString("state")=="Ready" && runtime.getBoolean("activity_visible") &&
                            runtime.getString("xr_state") in setOf("VISIBLE","FOCUSED") && target?.optString("role")=="diagnostic" &&
                            target.getLong("frames")>result.getJSONObject("ready").getJSONObject("render").getJSONArray("targets").getJSONObject(0).getLong("frames")) {
                            check(current==handle && adapter.currentKey().toString()==key.toString())
                            val renderedKey=target.getJSONObject("key")
                            check(listOf("session","window","scene","generation").all { renderedKey.getString(it)==checkNotNull(key).getString(it) })
                            result.put("after",snapshot).put("result","pass")
                            NativeSessionHost.publishCheck(result); complete(); return
                        }
                    }
                }
                ui.postDelayed(this,32)
            } catch(failure:Exception) {
                result.put("result","fail").put("stage",stage).put("failure_class",failure.javaClass.simpleName)
                    .put("failure_site",failure.stackTrace.firstOrNull()?.toString()).put("cycles",rows).put("inputs",inputs)
                val current=NativeSessionHost.currentHandle()
                if(current>0) result.put("snapshot",JSONObject(NativeBridge.nativeSnapshot(current)))
                try { cleanup() } catch(_:Exception) { result.put("cleanup_failed",true) }
                NativeSessionHost.publishCheck(result); complete()
            }
        }
        private fun cleanup() {
            val entity=source; source=null
            try {
                entity?.let { scene().onNoIntersectionInput(it,0,0); scene().resolveInput() }
            } finally {
                adapter.endInputCapture(); entity?.destroy()
            }
            result.put("capture_detached",true).put("source_destroyed",true)
        }
    }
}
