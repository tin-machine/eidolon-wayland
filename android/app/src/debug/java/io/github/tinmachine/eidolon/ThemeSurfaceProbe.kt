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
                layoutWidthInPx=256;layoutHeightInPx=256
                width=if(registrationId==R.id.theme_probe_input) 1.2f else 1.5f
                height=if(registrationId==R.id.theme_probe_input) .9f else 1.5f
                alphaMode=if(front) AlphaMode.PREMULTIPLIED else AlphaMode.OPAQUE
                enableTransparent=front;forceSceneTexture=true
                layerConfig=if(layer) LayerConfig(alphaBlend=LayerAlphaBlend(
                    BlendFactor.ONE,BlendFactor.ONE_MINUS_SOURCE_ALPHA,
                    BlendFactor.ONE,BlendFactor.ONE_MINUS_SOURCE_ALPHA)) else null
                layerBlendType=PanelShapeLayerBlendType.ALPHA_BLEND
            }
            val obj=PanelSceneObject(scene,entity,options)
            val owner=active
            ui.post {if(active===owner) owner?.ready(entity,obj)}
            obj
        }
    }
    fun registrations():List<PanelRegistration> = listOf(
        Registration(R.id.theme_probe_back,false,false),
        Registration(R.id.theme_probe_mesh,true,false),
        Registration(R.id.theme_probe_input,false,false),
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
        var back:Entity?=null;var front:Entity?=null;var input:Entity?=null
        var painter:ThemeProbePainter?=null
        var mode=0;var stage="ready";var sample=JSONObject()
        var notBefore=0L
        var closing=false
        var contentDeadline=0L;var candidateIndex=0
        val candidates=JSONArray()
        var pendingFailure:Exception?=null
        val deadline=SystemClock.elapsedRealtime()+45000
        fun start() {NativeSessionHost.publishCheck(result);ui.post(this)}
        fun ready(entity:Entity,obj:PanelSceneObject) {if(!closing && entity.id in listOfNotNull(front?.id,back?.id)) objects[entity.id]=obj}
        // 既存RayCaseと同じく、既知の交点を保った斜めsegmentを使う。
        // SDK 0.15の垂直segmentがnullになる実測はray-coordinate-checks.mdに記録済み。
        private fun intersect(x:Float,y:Float=1.4f):HitInfo? {
            val from=Vector3(x+.13f,y+.11f,.2f)
            val factor=(2.5f-.2f)/(1.4f-.2f)
            return scene().lineSegmentIntersect(from,Vector3(from.x+(x-from.x)*factor,from.y+(y-from.y)*factor,2.5f))
        }
        private fun hit(x:Float):Long? = intersect(x)?.entity?.id
        private fun clearEntities() {
            val owned=listOfNotNull(front,back,input)
            front=null;back=null;input=null;objects.clear()
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
                        // 操作UIはx=-1.45、診断quadはx=0。右側へ置き両方との重なりを避ける。
                        val id=if(mode==0) R.id.theme_probe_mesh else R.id.theme_probe_layer
                        back=Entity.create(listOf(Panel(R.id.theme_probe_back),Transform(Pose(Vector3(1.2f,1.4f,1.6f))),Hittable(MeshCollision.LineTest)))
                        front=Entity.create(listOf(Panel(id),Transform(Pose(Vector3(1.2f,1.4f,1.4f))),Hittable(MeshCollision.LineTest)))
                        stage="surfaces"
                    }
                    "surfaces" -> {
                        val b=objects[checkNotNull(back).id];val f=objects[checkNotNull(front).id]
                        if(b!=null && f!=null && b.surface.isValid && f.surface.isValid) {
                            val shape=checkNotNull(f.panelShapeConfig)
                            sample=JSONObject().put("mode",if(mode==0) "mesh" else "layer")
                                .put("front",checkNotNull(front).id.toString()).put("back",checkNotNull(back).id.toString())
                                .put("alpha_mode",shape.alphaMode.name).put("scene_texture",f.texture!=null)
                                .put("layer_enabled",shape.enableLayer).put("layer_blend_type",shape.layerBlendType.name)
                            val blend=shape.layerConfig?.alphaBlend
                            sample.put("layer_blend",blend?.let { JSONArray(listOf(it.sourceFactorColor.name,it.destinationFactorColor.name,
                                it.sourceFactorAlpha.name,it.destinationFactorAlpha.name)) } ?: JSONObject.NULL)
                            // SDK 0.15はlayerのALPHA_BLENDをhole-punch用mesh alphaへ正規化する。
                            val alpha=if(mode==0) AlphaMode.PREMULTIPLIED else AlphaMode.TRANSLUCENT_HOLE_PUNCH
                            check(shape.alphaMode==alpha && shape.forceSceneTexture && shape.enableLayer==(mode==1))
                            check(shape.layerBlendType==PanelShapeLayerBlendType.ALPHA_BLEND)
                            check(if(mode==0) blend==null else blend==LayerAlphaBlend(BlendFactor.ONE,BlendFactor.ONE_MINUS_SOURCE_ALPHA,
                                BlendFactor.ONE,BlendFactor.ONE_MINUS_SOURCE_ALPHA))
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
                        val center=hit(1.2f);val margin=hit(1.85f)
                        sample.put("default_center_hit",center?.toString() ?: JSONObject.NULL)
                            .put("default_margin_hit",margin?.toString() ?: JSONObject.NULL)
                        // Surfaceの準備とcollision geometryの準備は別。期限内で実交差を待つ。
                        if(center==checkNotNull(front).id && margin in listOf(checkNotNull(front).id,checkNotNull(back).id)) {
                            sample.put("margin_blocks_back",margin==checkNotNull(front).id)
                            checkNotNull(front).setComponent(Hittable(MeshCollision.NoCollision))
                            stage="no_collision";notBefore=SystemClock.elapsedRealtime()+200
                        }
                    }
                    "no_collision" -> if(visible && SystemClock.elapsedRealtime()>=notBefore) {
                        val center=hit(1.2f);val margin=hit(1.85f)
                        if(center==checkNotNull(back).id && margin==checkNotNull(back).id) {
                            sample.put("no_collision_center_hit",center.toString()).put("no_collision_margin_hit",margin.toString())
                            input=Entity.create(listOf(Mesh(android.net.Uri.parse(ThemeHitMesh.URI),MeshCollision.LineTest),Transform(Pose(Vector3(1.2f,1.4f,1.4f)))))
                            stage="content_visibility";notBefore=SystemClock.elapsedRealtime()+1000
                        }
                    }
                    "content_visibility" -> if(visible && SystemClock.elapsedRealtime()>=notBefore) {
                        sample.put("visible_only_content_hit",hit(1.2f)?.toString() ?: JSONObject.NULL)
                        checkNotNull(input).setComponent(Hittable(ThemeHitMesh.COLLISION))
                        sample.put("content_collision",ThemeHitMesh.COLLISION.name)
                        stage="content_input";notBefore=SystemClock.elapsedRealtime()+200
                        contentDeadline=SystemClock.elapsedRealtime()+2000
                    }
                    "content_input" -> if(visible && SystemClock.elapsedRealtime()>=notBefore) {
                        val center=hit(1.2f);val margin=hit(1.85f)
                        val sampleHit=intersect(.84f,1.535f)
                        sample.put("expected_content_entity",checkNotNull(input).id.toString())
                            .put("actual_content_center",center?.toString() ?: JSONObject.NULL)
                            .put("actual_content_margin",margin?.toString() ?: JSONObject.NULL)
                            .put("actual_content_sample",sampleHit?.entity?.id?.toString() ?: JSONObject.NULL)
                        if(center==checkNotNull(input).id && margin==checkNotNull(back).id && sampleHit?.entity?.id==checkNotNull(input).id) {
                            val uv=sampleHit.textureCoordinate
                            sample.put("content_uv",JSONArray(listOf(uv.x,uv.y)))
                                .put("content_hit_point",JSONArray(listOf(sampleHit.point.x,sampleHit.point.y,sampleHit.point.z)))
                            check(kotlin.math.abs(uv.x-.2f)<.005f && kotlin.math.abs(uv.y-.35f)<.005f)
                            sample.put("content_input",center.toString()).put("content_margin_hit",margin.toString())
                            rows.put(sample);clearEntities();stage="removed";notBefore=SystemClock.elapsedRealtime()+200
                        } else if(SystemClock.elapsedRealtime()>contentDeadline) {
                            input?.destroy();input=null;candidateIndex=0
                            sample.put("input_candidates",candidates)
                            stage="candidate_create";notBefore=SystemClock.elapsedRealtime()+200
                        }
                    }
                    "candidate_create" -> if(visible && SystemClock.elapsedRealtime()>=notBefore) {
                        check(candidateIndex<=ThemeInputCandidates.names.size) {"THEME_CONTENT_INPUT_UNRESOLVED"}
                        val parts=if(candidateIndex<ThemeInputCandidates.names.size) listOf(
                            Mesh(android.net.Uri.parse(ThemeInputCandidates.uri(ThemeInputCandidates.names[candidateIndex])),MeshCollision.LineTest_IgnoreVisible),
                            Transform(Pose(Vector3(1.2f,1.4f,1.4f)))) else listOf(
                            Panel(R.id.theme_probe_input),Transform(Pose(Vector3(1.2f,1.4f,1.4f))),Visible(false),Hittable(MeshCollision.LineTest_IgnoreVisible))
                        input=Entity.create(parts);stage="candidate_sample";notBefore=SystemClock.elapsedRealtime()+700
                    }
                    "candidate_sample" -> if(visible && SystemClock.elapsedRealtime()>=notBefore) {
                        val name=ThemeInputCandidates.names.getOrNull(candidateIndex) ?: "hidden_panel"
                        val point=intersect(.84f,1.535f)
                        val row=JSONObject().put("name",name).put("entity",checkNotNull(input).id.toString())
                            .put("center",hit(1.2f)?.toString() ?: JSONObject.NULL)
                            .put("margin",hit(1.85f)?.toString() ?: JSONObject.NULL)
                            .put("sample",point?.entity?.id?.toString() ?: JSONObject.NULL)
                            .put("mesh_bounds",ThemeInputCandidates.metadata[name] ?: JSONObject.NULL)
                        point?.let {row.put("uv",JSONArray(listOf(it.textureCoordinate.x,it.textureCoordinate.y)))
                            .put("point",JSONArray(listOf(it.point.x,it.point.y,it.point.z)))}
                        candidates.put(row);input?.destroy();input=null;candidateIndex++
                        stage="candidate_create";notBefore=SystemClock.elapsedRealtime()+200
                    }
                    "removed" -> if(SystemClock.elapsedRealtime()>=notBefore) {
                        val old=rows.getJSONObject(rows.length()-1)
                        val ids=setOf(old.getString("front"),old.getString("back"),old.getString("content_input"))
                        if(hit(1.2f)?.toString() !in ids && hit(1.85f)?.toString() !in ids) {
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
                result.put("stage",stage).put("cycles",rows).put("sample",sample)
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
