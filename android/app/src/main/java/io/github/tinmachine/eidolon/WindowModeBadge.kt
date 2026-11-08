package io.github.tinmachine.eidolon

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Pose
import com.meta.spatial.core.Vector3
import com.meta.spatial.runtime.*
import com.meta.spatial.toolkit.*
import java.nio.ByteBuffer
import java.util.concurrent.CompletableFuture
import org.json.JSONObject

/** One directly owned quad/texture. Mode updates upload pixels without replacing any SDK object. */
class WindowModeBadge(parent:Entity,layout:WindowControlLayout,width:Double,height:Double,private val sdk:Scene,
    private val objects:SceneObjectSystem,private val live:()->Boolean):AutoCloseable {
    private var closeRequested=false
    private var disposed=false
    private var mode=WindowMode.PREPARING
    private var lastPose:Pose?=null
    private var lastVisible:Boolean?=null
    private var failures=0L
    private var updates=0L
    private var entity:Entity?=null
    private var obj:SceneObject?=null
    private var mesh:SceneMesh?=null
    private var material:SceneMaterial?=null
    private var texture:SceneTexture?=null
    private val slot:PixelSlot
    init {
        slot=synchronized(owners) {
            check(owners.size<MAX) {"MODE_OWNER_LIMIT"}
            val selected=slots.firstOrNull {!it.used} ?: run {check(slots.size<MAX);PixelSlot().also {slots.add(it)}}
            selected.used=true;owners.add(this);selected
        }
        try {
            texture=SceneTexture(WIDTH,HEIGHT,1,SamplerConfig(),Color.valueOf(Color.TRANSPARENT)).also {textureCreates++}
            material=SceneMaterial(checkNotNull(texture),AlphaMode.PREMULTIPLIED,SceneMaterial.UNLIT_SHADER).also {materialCreates++}
            checkNotNull(material).apply {setUnlit(true);setSidedness(MaterialSidedness.DOUBLE_SIDED);setDepthWrite(DepthWrite.DISABLE)}
            mesh=createMesh(checkNotNull(material)).also {meshCreates++}
            val positions=layout.positions(width,height)
            val vertical=if(layout.anchor.startsWith("top")) 1 else -1
            val x=positions.map {it.x}.average();val y=positions.first().y+vertical*(layout.diameter/2+.047)
            entity=Entity.create(listOf(TransformParent(parent),Transform(Pose()),Scale(Vector3(1f)),Hittable(MeshCollision.NoCollision),Visible(true))).also {entityCreates++}
            setAbsoluteTransform(checkNotNull(entity),getAbsoluteTransform(parent)*Pose(Vector3(x.toFloat(),y.toFloat(),-.035f)))
            setAbsoluteScale(checkNotNull(entity),Vector3(.42f,.064f,1f))
            obj=SceneObject(sdk,checkNotNull(mesh),"eidolon-window-mode",checkNotNull(entity)).also {objectCreates++}
            objects.addSceneObject(checkNotNull(entity),CompletableFuture.completedFuture(checkNotNull(obj)))
            update(mode,true,true)
        } catch(error:Exception) {runCatching {close()};throw error}
    }
    fun update(value:WindowMode,visible:Boolean,force:Boolean=false) {
        if(closeRequested || !live()) return
        val e=checkNotNull(entity);val sceneObject=checkNotNull(obj)
        val pose=getAbsoluteTransform(e)
        if(lastPose?.isApproximatelyEqual(pose,.00001f,.00001f)!=true) {
            sceneObject.setPosition(pose.t);sceneObject.setRotationQuat(pose.q);sceneObject.setScale(getAbsoluteScale(e));lastPose=pose.copy()
        }
        if(lastVisible!=visible) {sceneObject.setIsVisible(visible);lastVisible=visible}
        if(!force && value==mode) return
        mode=value
        val canvas=Canvas(slot.bitmap);canvas.drawColor(Color.TRANSPARENT,android.graphics.PorterDuff.Mode.CLEAR)
        val paint=slot.paint
        paint.color=0xee24364b.toInt();canvas.drawRoundRect(0f,0f,WIDTH.toFloat(),HEIGHT.toFloat(),22f,22f,paint)
        paint.color=0xfffff0cf.toInt();paint.textSize=38f;paint.textAlign=Paint.Align.CENTER
        val baseline=HEIGHT/2f-(paint.fontMetrics.ascent+paint.fontMetrics.descent)/2f
        canvas.drawText(value.label,WIDTH/2f,baseline,paint)
        slot.buffer.clear();slot.bitmap.copyPixelsToBuffer(slot.buffer);slot.buffer.rewind()
        checkNotNull(texture).update(slot.buffer,WIDTH,HEIGHT,slot.bitmap.rowBytes);updates++;textureUpdates++
    }
    fun metadata()=JSONObject().put("entity",entity?.id?.toString() ?: JSONObject.NULL).put("mode",mode.name).put("label",mode.label)
        .put("visible",lastVisible ?: false).put("object_ready",!closeRequested && obj?.handle?.let {it!=0L}==true).put("non_hit",true).put("updates",updates).put("failures",failures)
    override fun close() {
        if(disposed) return
        closeRequested=true
        // Detach and destroy the render object before the resources it references. The SDK methods are idempotent.
        try {
            entity?.let {objects.removeSceneObject(it)}
            obj?.let {it.setIsVisible(false);it.destroy();objectDestroys++;obj=null}
            entity?.let {it.destroy();entityDestroys++;entity=null}
            mesh?.let {it.destroy();meshDestroys++;mesh=null}
            material?.let {it.destroy();materialDestroys++;material=null}
            texture?.let {it.destroy();textureDestroys++;texture=null}
            synchronized(owners) {slot.used=false;owners.remove(this)}
            disposed=true
        } catch(error:Exception) {
            failures++
            // A failed owner stays bounded in the registry; cleanup is never reported as zero via GC.
            throw error
        }
    }
    companion object {
        private const val WIDTH=512
        private const val HEIGHT=64
        private const val MAX=9
        private class PixelSlot {
            val bitmap=Bitmap.createBitmap(WIDTH,HEIGHT,Bitmap.Config.ARGB_8888)
            val buffer=ByteBuffer.allocateDirect(WIDTH*HEIGHT*4)
            val paint=Paint(Paint.ANTI_ALIAS_FLAG)
            var used=false
        }
        private val owners=linkedSetOf<WindowModeBadge>()
        private val slots=mutableListOf<PixelSlot>() // Fixed-capacity process pool; no per-mode buffer churn.
        private var entityCreates=0L;private var entityDestroys=0L
        private var objectCreates=0L;private var objectDestroys=0L
        private var meshCreates=0L;private var meshDestroys=0L
        private var materialCreates=0L;private var materialDestroys=0L
        private var textureCreates=0L;private var textureDestroys=0L;private var textureUpdates=0L
        fun resources():JSONObject=synchronized(owners) {
            JSONObject().put("entity_create_calls",entityCreates).put("entity_destroy_calls",entityDestroys).put("entity_owners",entityCreates-entityDestroys)
                .put("object_owners",objectCreates-objectDestroys).put("mesh_owners",meshCreates-meshDestroys).put("material_owners",materialCreates-materialDestroys)
                .put("texture_create_calls",textureCreates).put("texture_destroy_calls",textureDestroys).put("texture_owners",textureCreates-textureDestroys).put("texture_updates",textureUpdates)
                .put("cleanup_pending_owners",owners.count {it.closeRequested}).put("owner_limit",MAX)
                .put("pixel_slots_allocated",slots.size).put("pixel_slots_in_use",slots.count {it.used}).put("pixel_bytes_per_slot",WIDTH*HEIGHT*8)
                .put("rgba_bytes_per_texture",WIDTH*HEIGHT*4).put("mip_levels",1).put("extra_android_displays",0).put("extra_surfaces",0)
        }
        private fun createMesh(material:SceneMaterial):SceneMesh=SceneMesh.meshWithMaterials(
            floatArrayOf(-.5f,.5f,0f,.5f,.5f,0f,.5f,-.5f,0f,-.5f,-.5f,0f),
            floatArrayOf(0f,0f,-1f,0f,0f,-1f,0f,0f,-1f,0f,0f,-1f),floatArrayOf(0f,0f,1f,0f,1f,1f,0f,1f),
            IntArray(4){Color.WHITE},intArrayOf(0,1,2,0,2,3),intArrayOf(0,6),arrayOf(material),true)
    }
}
