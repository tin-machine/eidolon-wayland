package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.os.Process
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Pose
import com.meta.spatial.core.SpatialContext
import com.meta.spatial.core.Vector3
import com.meta.spatial.runtime.*
import com.meta.spatial.toolkit.*
import org.json.JSONArray
import org.json.JSONObject

/** producer alpha、SDK設定、実rayを分離する小probe。RGB readbackでSDK alphaを合格にしない。 */
object ThemeSurfaceProbe {
    private val ui=Handler(Looper.getMainLooper())
    private var active: Check?=null
    private val retired=mutableListOf<ThemeProbePainter>()
    private class Registration(id:Int, val front:Boolean, val layer:Boolean):PanelRegistration(id) {
        override fun panelCreator(scene:Scene, spatialContext:SpatialContext):(Entity)->PanelSceneObject = {entity ->
            val options=PanelConfigOptions().apply {
                layoutWidthInPx=256;layoutHeightInPx=256;width=1.5f;height=1.5f
                alphaMode=if(front) AlphaMode.PREMULTIPLIED else AlphaMode.OPAQUE
                enableTransparent=front;forceSceneTexture=true
                layerConfig=if(layer) LayerConfig(alphaBlend=LayerAlphaBlend(
                    BlendFactor.ONE,BlendFactor.ONE_MINUS_SOURCE_ALPHA,
                    BlendFactor.ONE,BlendFactor.ONE_MINUS_SOURCE_ALPHA)) else null
                layerBlendType=PanelShapeLayerBlendType.ALPHA_BLEND
            }
            val obj=PanelSceneObject(scene,entity,options)
            ui.post {active?.ready(entity,obj)}
            obj
        }
    }
    fun registrations():List<PanelRegistration> = listOf(
        Registration(R.id.theme_probe_back,false,false),
        Registration(R.id.theme_probe_mesh,true,false),
        Registration(R.id.theme_probe_layer,true,true))
    fun start(nonce:String,scene:()->Scene,done:()->Unit) {
        retired.removeAll {it.finished()}
        if(active!=null || retired.isNotEmpty()) {
            NativeSessionHost.publishCheck(JSONObject().put("case","theme-surface").put("nonce",nonce).put("result","fail").put("stage","prior_painter_pending"))
            done();return
        }
        Check(nonce,scene,done).also {active=it;it.start()}
    }
    fun sceneClosing() {active?.abort();active=null}
    private class Check(val nonce:String,val scene:()->Scene,val done:()->Unit):Runnable {
        val result=JSONObject().put("case","theme-surface").put("nonce",nonce).put("result","running")
            .put("sdk_alpha_verified",false).put("hmd_verified",false).put("physical_input_verified",false)
            .put("synthetic_visibility",false).put("synthetic_raycast",true).put("ui_thread",Process.myTid())
        val rows=JSONArray()
        val objects=mutableMapOf<Long,PanelSceneObject>()
        var back:Entity?=null;var front:Entity?=null
        var painter:ThemeProbePainter?=null
        var mode=0;var stage="ready";var sample=JSONObject()
        var notBefore=0L
        var closing=false
        var pendingFailure:Exception?=null
        val deadline=SystemClock.elapsedRealtime()+45000
        fun start() {NativeSessionHost.publishCheck(result);ui.post(this)}
        fun ready(entity:Entity,obj:PanelSceneObject) {if(!closing) objects[entity.id]=obj}
        private fun hit(x:Float):Long? = scene().lineSegmentIntersect(Vector3(x,1.4f,0.2f),Vector3(x,1.4f,2.5f))?.entity?.id
        private fun clearEntities() {
            val owned=listOfNotNull(front,back)
            front=null;back=null;objects.clear()
            owned.forEach {runCatching {it.destroy()}.onFailure {result.put("cleanup_failed",true)}}
        }
        fun abort() {
            closing=true;ui.removeCallbacks(this);painter?.stop();painter?.let {if(!it.finished()) retired.add(it)};clearEntities()
            result.put("result","fail").put("stage",stage).put("reason","scene_shutdown")
            NativeSessionHost.publishCheck(result)
            done()
        }
        override fun run() {
            if(closing) return
            try {
                pendingFailure?.let {throw it}
                check(SystemClock.elapsedRealtime()<deadline) {"THEME_PROBE_TIMEOUT"}
                val handle=NativeSessionHost.currentHandle()
                val runtime=if(handle>0) JSONObject(NativeBridge.nativeSnapshot(handle)).optJSONObject("runtime") else null
                val visible=runtime?.optBoolean("activity_visible")==true && runtime.optString("xr_state") in setOf("VISIBLE","FOCUSED")
                when(stage) {
                    "ready" -> if(visible && runtime?.optString("state")=="Ready") {
                        result.put("ready",runtime)
                        stage="create"
                    }
                    "create" -> {
                        val id=if(mode==0) R.id.theme_probe_mesh else R.id.theme_probe_layer
                        back=Entity.create(listOf(Panel(R.id.theme_probe_back),Transform(Pose(Vector3(-2.5f,1.4f,1.6f))),Hittable(MeshCollision.LineTest)))
                        front=Entity.create(listOf(Panel(id),Transform(Pose(Vector3(-2.5f,1.4f,1.4f))),Hittable(MeshCollision.LineTest)))
                        stage="surfaces"
                    }
                    "surfaces" -> {
                        val b=objects[checkNotNull(back).id];val f=objects[checkNotNull(front).id]
                        if(b!=null && f!=null && b.surface.isValid && f.surface.isValid) {
                            val shape=checkNotNull(f.panelShapeConfig)
                            check(shape.alphaMode==AlphaMode.PREMULTIPLIED && shape.forceSceneTexture)
                            sample=JSONObject().put("mode",if(mode==0) "mesh" else "layer")
                                .put("front",checkNotNull(front).id.toString()).put("back",checkNotNull(back).id.toString())
                                .put("alpha_mode",shape.alphaMode.name).put("scene_texture",f.texture!=null)
                            painter=ThemeProbePainter(listOf(b.surface to false,f.surface to true)).also {it.start()}
                            stage="paint"
                        }
                    }
                    "paint" -> if(painter?.finished()==true) {
                        val proof=checkNotNull(painter?.report)
                        sample.put("producer",proof);check(proof.getString("result")=="pass" && proof.getBoolean("owned_egl_released"))
                        stage="default_ray";notBefore=SystemClock.elapsedRealtime()+200
                    }
                    "default_ray" -> if(visible && SystemClock.elapsedRealtime()>=notBefore) {
                        val center=hit(-2.5f);val margin=hit(-1.85f)
                        check(center==checkNotNull(front).id)
                        check(margin in listOf(checkNotNull(front).id,checkNotNull(back).id))
                        sample.put("default_center_hit",center.toString()).put("default_margin_hit",margin.toString())
                            .put("margin_blocks_back",margin==checkNotNull(front).id)
                        checkNotNull(front).setComponent(Hittable(MeshCollision.NoCollision))
                        stage="no_collision";notBefore=SystemClock.elapsedRealtime()+200
                    }
                    "no_collision" -> if(visible && SystemClock.elapsedRealtime()>=notBefore) {
                        val center=hit(-2.5f);val margin=hit(-1.85f)
                        if(center==checkNotNull(back).id && margin==checkNotNull(back).id) {
                            sample.put("no_collision_center_hit",center.toString()).put("no_collision_margin_hit",margin.toString())
                            rows.put(sample);clearEntities();stage="removed";notBefore=SystemClock.elapsedRealtime()+200
                        }
                    }
                    "removed" -> if(SystemClock.elapsedRealtime()>=notBefore) {
                        val old=rows.getJSONObject(rows.length()-1)
                        val ids=setOf(old.getString("front"),old.getString("back"))
                        if(hit(-2.5f)?.toString() !in ids && hit(-1.85f)?.toString() !in ids) {
                            old.put("removed_from_raycast",true)
                            if(mode++==0) stage="create" else stage="complete"
                        }
                    }
                    "complete" -> if(visible) {
                        check(!result.optBoolean("cleanup_failed"))
                        result.put("cycles",rows).put("result","pass").put("after",runtime).put("probe_entities_remaining",0)
                        NativeSessionHost.publishCheck(result);active=null;done();return
                    }
                }
                result.put("stage",stage).put("cycles",rows)
                NativeSessionHost.publishCheck(result);ui.postDelayed(this,32)
            } catch(failure:Exception) {
                painter?.stop()
                // GL ownerの終了を待ち、sceneが有効なこのcallbackでSDK entityを回収する。
                if(painter?.finished()==false) {pendingFailure=failure;ui.postDelayed(this,32);return}
                clearEntities();closing=true;active=null
                result.put("result","fail").put("stage",stage).put("cycles",rows).put("sample",sample)
                    .put("failure_class",failure.javaClass.simpleName).put("failure_site",failure.stackTrace.firstOrNull()?.toString())
                NativeSessionHost.publishCheck(result);done()
            }
        }
    }
}
