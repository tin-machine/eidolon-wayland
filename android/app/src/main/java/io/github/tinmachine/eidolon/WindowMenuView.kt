package io.github.tinmachine.eidolon

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.net.Uri
import android.os.Handler
import android.os.Looper
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Pose
import com.meta.spatial.core.Vector2
import com.meta.spatial.core.Vector3
import com.meta.spatial.runtime.*
import com.meta.spatial.toolkit.*
import org.json.JSONObject
import org.json.JSONArray
import java.nio.ByteBuffer
import java.util.concurrent.CompletableFuture

/** A single owned menu: bounded small hit spheres and one non-hit, explicitly owned label quad. */
class WindowMenuView(private val parent:Entity,private val layout:WindowControlLayout,private val width:Double,private val height:Double,
    private val sdk:Scene,private val objects:SceneObjectSystem,private val live:()->Boolean,private val ownsInput:()->Boolean,
    private val larger:Boolean,private val smaller:Boolean,private val action:(String)->Unit):AutoCloseable {
    private class Button(val name:String,val root:Entity,val face:Entity,val icon:Entity,val enabled:Boolean) {
        var obj:SceneObject?=null;var listener:Long?=null
        val press=WindowControlPress();val sources=linkedMapOf<Long,Pair<Entity,Int>>()
        val owned=mutableListOf(icon,face,root)
    }
    private val ui=Handler(Looper.getMainLooper())
    private val buttons=mutableListOf<Button>()
    private val starts=WindowMoveStarts()
    private val unadopted=mutableListOf<Entity>()
    private var closing=false
    private var disposed=false
    private var label:Entity?=null;private var obj:SceneObject?=null;private var mesh:SceneMesh?=null
    private var material:SceneMaterial?=null;private var texture:SceneTexture?=null
    private var bitmap:Bitmap?=null;private var pixels:ByteBuffer?=null
    private var lastPose:Pose?=null
    private var failures=0L
    init {check(Looper.myLooper()==Looper.getMainLooper());check(owners.isEmpty()) {"MENU_OWNER_LIMIT"};owners.add(this)}
    private var initialized=false
    /** Caller must retain this owner before the first native allocation. Failure is cleaned through that same reference. */
    fun initialize() {
        check(!initialized && !closing && !disposed);initialized=true
        try {
            val first=layout.positions(width,height).first();val side=if(layout.anchor.endsWith("right")) 1 else -1
            val vertical=if(layout.anchor.startsWith("top")) 1 else -1
            val x=first.x-side*(layout.diameter/2+.025+.21);val y=first.y+vertical*.12
            val pose=getAbsoluteTransform(parent)
            val rows=buildList {
                add(Triple("larger","大きく",larger));add(Triple("smaller","小さく",smaller))
                if(WindowRecenterPolicy.ENABLED) add(Triple("recenter","正面へ",true))
                add(Triple("back","戻る",true))
            }
            val menuHeight=.32*rows.size/3
            val image=pixelSlot.bitmap.also {bitmap=it}
            val canvas=Canvas(image);canvas.drawColor(Color.TRANSPARENT,android.graphics.PorterDuff.Mode.CLEAR);val paint=Paint(Paint.ANTI_ALIAS_FLAG)
            paint.color=0xf4fbe8f2.toInt();canvas.drawRoundRect(0f,0f,WIDTH.toFloat(),HEIGHT.toFloat(),30f,30f,paint)
            for((index,row) in rows.withIndex()) {
                paint.color=if(row.third) 0xff27354b.toInt() else 0xff8b8790.toInt();paint.textSize=37f;paint.textAlign=Paint.Align.LEFT
                val baseline=(index+.5f)*HEIGHT/rows.size-(paint.fontMetrics.ascent+paint.fontMetrics.descent)/2f
                canvas.drawText(row.second,155f,baseline,paint)
                val py=y+(.5-(index+.5)/rows.size)*menuHeight
                fun entity(uri:String,collision:MeshCollision,scale:Vector3,z:Float=-.058f):Entity {
                    val e=Entity.create(listOf(Mesh(Uri.parse(uri),collision),TransformParent(parent),Transform(Pose()),Scale(Vector3(1f))));entityCreates++;unadopted.add(e)
                    setAbsoluteTransform(e,pose*Pose(Vector3((x-.15).toFloat(),py.toFloat(),z)));setAbsoluteScale(e,scale)
                    return e
                }
                val root=entity(WindowControlMeshes.OUTLINE,if(row.third) MeshCollision.LineTest else MeshCollision.NoCollision,Vector3(.08f,.08f,.02f))
                var face:Entity?=null;var icon:Entity?=null
                try {
                    face=entity(when(row.first) {"larger"->WindowControlMeshes.FOCUS;"smaller"->WindowControlMeshes.CLOSE;else->WindowControlMeshes.MOVE},MeshCollision.NoCollision,Vector3(.069f,.069f,.018f),-.059f)
                    icon=entity(when(row.first) {"larger"->WindowControlMeshes.SIZE_UP_ICON;"smaller"->WindowControlMeshes.SIZE_DOWN_ICON;"recenter"->WindowControlMeshes.FOCUS_ICON;else->WindowControlMeshes.MENU_BACK_ICON},MeshCollision.NoCollision,Vector3(.08f,.08f,.02f),-.070f)
                    val b=Button(row.first,root,face,icon,row.third);buttons.add(b);unadopted.removeAll(b.owned)
                    (objects.getSceneObject(root) ?: objects.replaceOrReuseSceneObjectFuture(root)).whenComplete {value,error -> ui.post {
                        if(!closing && live() && buttons.contains(b)) {
                            if(error!=null || value==null) {failures++;return@post}
                            runCatching {val token=value.addInputListener(listener(b));b.obj=value;b.listener=token;listenerAdds++}.onFailure {failures++}
                        }
                    } }
                } catch(failure:Exception) {throw failure}
            }
            texture=SceneTexture(WIDTH,HEIGHT,1,SamplerConfig(),Color.valueOf(Color.TRANSPARENT)).also {textureCreates++}
            material=SceneMaterial(checkNotNull(texture),AlphaMode.PREMULTIPLIED,SceneMaterial.UNLIT_SHADER).also {materialCreates++}
            checkNotNull(material).apply {setUnlit(true);setSidedness(MaterialSidedness.DOUBLE_SIDED);setDepthWrite(DepthWrite.DISABLE)}
            mesh=SceneMesh.meshWithMaterials(floatArrayOf(-.5f,.5f,0f,.5f,.5f,0f,.5f,-.5f,0f,-.5f,-.5f,0f),
                floatArrayOf(0f,0f,-1f,0f,0f,-1f,0f,0f,-1f,0f,0f,-1f),floatArrayOf(0f,0f,1f,0f,1f,1f,0f,1f),IntArray(4){Color.WHITE},
                intArrayOf(0,1,2,0,2,3),intArrayOf(0,6),arrayOf(checkNotNull(material)),true).also {meshCreates++}
            label=Entity.create(listOf(TransformParent(parent),Transform(Pose()),Scale(Vector3(1f)),Hittable(MeshCollision.NoCollision),Visible(true))).also {entityCreates++}
            setAbsoluteTransform(checkNotNull(label),pose*Pose(Vector3(x.toFloat(),y.toFloat(),-.055f)));setAbsoluteScale(checkNotNull(label),Vector3(.42f,menuHeight.toFloat(),1f))
            obj=SceneObject(sdk,checkNotNull(mesh),"eidolon-window-menu",checkNotNull(label)).also {objectCreates++}
            val actual=getAbsoluteTransform(checkNotNull(label));checkNotNull(obj).apply {setPosition(actual.t);setRotationQuat(actual.q);setScale(getAbsoluteScale(checkNotNull(label)));setIsVisible(true)}
            lastPose=actual.copy()
            objects.addSceneObject(checkNotNull(label),CompletableFuture.completedFuture(checkNotNull(obj)))
            val buffer=pixelSlot.buffer.also {pixels=it};buffer.clear();image.copyPixelsToBuffer(buffer);buffer.rewind()
            checkNotNull(texture).update(buffer,WIDTH,HEIGHT,image.rowBytes)
        } catch(failure:Exception) {closing=true;throw failure}
    }
    fun update() {
        if(closing || !live()) return
        val entity=checkNotNull(label);val pose=getAbsoluteTransform(entity)
        if(lastPose?.isApproximatelyEqual(pose,.00001f,.00001f)!=true) {
            checkNotNull(obj).apply {setPosition(pose.t);setRotationQuat(pose.q);setScale(getAbsoluteScale(entity))};lastPose=pose.copy()
        }
    }
    private fun listener(button:Button)=object:InputListener {
        override fun onInput(receiver:SceneObject,hitInfo:HitInfo,sourceOfInput:Entity,changed:Int,buttonState:Int,downTime:Long):Boolean {
            if(closing || !live() || !button.enabled || failures>0 || changed and PRIMARY==0) return false
            val source=sourceOfInput.id;val point=listOf(hitInfo.point.x.toDouble(),hitInfo.point.y.toDouble(),hitInfo.point.z.toDouble())
            ui.post {
                if(closing || !live() || !button.enabled || failures>0) return@post
                if(buttonState and PRIMARY!=0) {
                    if(starts.accept(source,downTime) && button.press.down(source,downTime,button.name,point)) button.sources[source]=sourceOfInput to buttonState
                } else {
                    if(button.press.contains(source,downTime)) button.press.move(source,point,.024)
                    val held=button.press.contains(source,downTime)
                    val clicked=button.press.up(source,downTime,button.name);if(held) button.sources.remove(source)
                    if(clicked) action(button.name)
                }
            };return true
        }
        override fun onDragMove(receiver:SceneObject,hitInfo:HitInfo,sourceOfInput:Entity,buttonState:Int) {ui.post {button.press.move(sourceOfInput.id,listOf(hitInfo.point.x.toDouble(),hitInfo.point.y.toDouble(),hitInfo.point.z.toDouble()),.024)}}
        override fun onHoverStop(receiver:SceneObject,sourceOfInput:Entity) {ui.post {button.press.cancel(sourceOfInput.id)}}
        override fun stopInput(receiver:SceneObject,sourceOfInput:Entity,downTime:Long) {ui.post {button.press.cancel(sourceOfInput.id)}}
        override fun onPointerEvent(receiver:SceneObject,hitInfo:HitInfo,type:Int,sourceOfInput:Entity,scrollInfo:Vector2,semanticType:Int) {if(type==PointerEventType.Cancel.id || type==PointerEventType.Unhover.id) ui.post {button.press.cancel(sourceOfInput.id)}}
    }
    fun metadata()=JSONObject().put("closing",closing).put("disposed",disposed).put("buttons",buttons.size).put("listeners_ready",buttons.count {it.listener!=null})
        .put("label_ready",obj?.handle?.let {it!=0L}==true).put("failures",failures).put("label_non_hit",true)
        .put("button_targets",JSONArray(buttons.map {JSONObject().put("action",it.name).put("entity",it.root.id.toString()).put("ready",it.listener!=null).put("enabled",it.enabled)}))
    override fun close() {
        if(disposed) return
        closing=true
        try {
            for(b in buttons) {
                if(ownsInput()) for((source,state) in b.sources.values.toList()) runCatching {sdk.onNoIntersectionInput(source,state,0)}
                b.sources.clear();b.press.clear()
                b.listener?.let {checkNotNull(b.obj).removeInputListener(it);listenerRemoves++;b.listener=null}
                b.root.setComponent(Hittable(MeshCollision.NoCollision))
            }
            if(buttons.isNotEmpty() && ownsInput()) sdk.resolveInput()
            while(buttons.isNotEmpty()) {val b=buttons.last();while(b.owned.isNotEmpty()) {b.owned.first().destroy();entityDestroys++;b.owned.removeAt(0)};buttons.removeAt(buttons.lastIndex)}
            while(unadopted.isNotEmpty()) {unadopted.first().destroy();entityDestroys++;unadopted.removeAt(0)}
            label?.let {objects.removeSceneObject(it)}
            obj?.let {it.setIsVisible(false);it.destroy();objectDestroys++;obj=null}
            label?.let {it.destroy();entityDestroys++;label=null}
            mesh?.let {it.destroy();meshDestroys++;mesh=null};material?.let {it.destroy();materialDestroys++;material=null};texture?.let {it.destroy();textureDestroys++;texture=null}
            bitmap=null;pixels=null;disposed=true;owners.remove(this)
        } catch(failure:Exception) {failures++;throw failure}
    }
    companion object {
        private const val WIDTH=512;private const val HEIGHT=256
        private class PixelSlot {
            val bitmap=Bitmap.createBitmap(WIDTH,HEIGHT,Bitmap.Config.ARGB_8888)
            val buffer=ByteBuffer.allocateDirect(WIDTH*HEIGHT*4)
        }
        private val pixelSlot by lazy {PixelSlot()} // One bounded process slot; no direct-buffer churn on reopen.
        private val PRIMARY=ButtonBits.ButtonTriggerL or ButtonBits.ButtonTriggerR or ButtonBits.ButtonA or ButtonBits.ButtonX
        private val owners=linkedSetOf<WindowMenuView>()
        private var entityCreates=0L;private var entityDestroys=0L;private var listenerAdds=0L;private var listenerRemoves=0L
        private var objectCreates=0L;private var objectDestroys=0L;private var meshCreates=0L;private var meshDestroys=0L
        private var materialCreates=0L;private var materialDestroys=0L;private var textureCreates=0L;private var textureDestroys=0L
        fun resources()=JSONObject().put("owner_limit",1).put("owners",owners.size).put("cleanup_pending_owners",owners.count {it.closing})
            .put("entity_owners",entityCreates-entityDestroys).put("listener_owners",listenerAdds-listenerRemoves).put("object_owners",objectCreates-objectDestroys)
            .put("mesh_owners",meshCreates-meshDestroys).put("material_owners",materialCreates-materialDestroys).put("texture_owners",textureCreates-textureDestroys)
            .put("gpu_rgba_bytes_per_menu",WIDTH*HEIGHT*4).put("cpu_pixel_bytes_per_menu",WIDTH*HEIGHT*8).put("extra_android_displays",0).put("extra_surfaces",0)
    }
}
