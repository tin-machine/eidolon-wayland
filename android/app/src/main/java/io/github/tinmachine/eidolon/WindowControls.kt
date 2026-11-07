package io.github.tinmachine.eidolon

import android.net.Uri
import android.os.Handler
import android.os.Looper
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Pose
import com.meta.spatial.core.Vector2
import com.meta.spatial.core.Vector3
import com.meta.spatial.runtime.*
import com.meta.spatial.toolkit.*
import org.json.JSONArray
import org.json.JSONObject

/** One immutable SDK binding owns its controls. The visual sphere is also the exact input mesh. */
class WindowControls(private val parent: Entity, private val target: String, private val runtime: Long,
    private val sdk: Scene, private val objects: SceneObjectSystem, private val live: ()->Boolean,
    private val layout: WindowControlLayout, private val width: Double, private val height: Double,
    private val revision: Long,private val move:WindowMoveController) : AutoCloseable {
    private val ui=Handler(Looper.getMainLooper())
    private var closed=false
    private var enabled=true
    private val buttons=mutableListOf<Button>()
    private var submitted=0L
    private var rejected=0L
    private var failures=0L
    private inner class Button(val action: String,val entity: Entity,val face: Entity,val icon: Entity,val diameter: Float) {
        var obj: SceneObject?=null
        var listener: Long?=null
        val press=WindowControlPress()
        val tickets=linkedMapOf<Long,Pair<Long,NativeSessionHost.WindowControlTicket>>()
        val sources=linkedMapOf<Long,Pair<Entity,Int>>()
    }
    init {
        check(Looper.myLooper()==Looper.getMainLooper())
        try {
            for(position in layout.positions(width,height)) {
                val d=position.diameter.toFloat()
                val bodyPose=getAbsoluteTransform(parent)
                fun create(mesh: String,collision: MeshCollision,z: Float,scale: Vector3): Entity {
                    val entity=Entity.create(listOf(Mesh(Uri.parse(mesh),collision),TransformParent(parent),Transform(Pose()),Scale(Vector3(1f))))
                    entityCreates++
                    try {
                        // SDK helpers account for scaleHierarchyApplied; the physical dimensions do not depend on its setting.
                        setAbsoluteTransform(entity,bodyPose * Pose(Vector3(position.x.toFloat(),position.y.toFloat(),z)))
                        setAbsoluteScale(entity,scale)
                    } catch(error: Exception) {destroyEntity(entity);throw error}
                    return entity
                }
                val root=create(WindowControlMeshes.OUTLINE,MeshCollision.LineTest,-.018f,Vector3(d,d,d*.25f))
                var face: Entity?=null
                var icon: Entity?=null
                var adopted=false
                try {
                    face=create(when(position.action) {"move"->WindowControlMeshes.MOVE;"focus"->WindowControlMeshes.FOCUS;else->WindowControlMeshes.CLOSE},
                        MeshCollision.NoCollision,-.018f-d*.0125f,Vector3(d*.86f,d*.86f,d*.2375f))
                    icon=create(when(position.action) {"move"->WindowControlMeshes.MOVE_ICON;"focus"->WindowControlMeshes.FOCUS_ICON;else->WindowControlMeshes.CLOSE_ICON},
                        MeshCollision.NoCollision,-.018f-d*.14f,Vector3(d,d,d*.25f))
                    val button=Button(position.action,root,checkNotNull(face),checkNotNull(icon),d);buttons.add(button);adopted=true
                    (objects.getSceneObject(root) ?: objects.replaceOrReuseSceneObjectFuture(root)).whenComplete {obj,error -> ui.post {
                        if(!closed && live() && buttons.contains(button)) {
                            if(error==null && obj!=null) {
                                runCatching {obj.addInputListener(listener(button))}.onSuccess {
                                    button.obj=obj;button.listener=it;listenerAdds++
                                }.onFailure {failures++;setEnabled(false)}
                            } else {failures++;setEnabled(false)}
                        }
                    } }
                } catch(error: Exception) {
                    if(!adopted) for(entity in listOfNotNull(icon,face,root)) runCatching {destroyEntity(entity)}
                    throw error
                }
            }
        } catch(error: Exception) {close();throw error}
    }
    private fun point(hit: HitInfo)=listOf(hit.point.x.toDouble(),hit.point.y.toDouble(),hit.point.z.toDouble())
    private fun listener(button: Button)=object: InputListener {
        override fun onInput(receiver: SceneObject,hitInfo: HitInfo,sourceOfInput: Entity,changed: Int,state: Int,downTime: Long): Boolean {
            if(closed || !enabled || !live() || changed and PRIMARY==0) return false
            val source=sourceOfInput.id;val at=point(hitInfo)
            if(button.action!="move" && NativeSessionHost.windowMoveSourceOwned(source)) return true
            ui.post {
                if(closed || !enabled || !live()) return@post
                if(button.action=="move") {
                    if(state and PRIMARY!=0) {if(move.begin(sourceOfInput,downTime)) pressed(button,true)}
                    else {move.release(source,downTime);pressed(button,false)}
                    return@post
                }
                if(state and PRIMARY!=0) {
                    if(button.press.down(source,downTime,"$target:$revision",at)) {
                        button.tickets.remove(source);button.sources.remove(source)
                        val ticket=NativeSessionHost.beginWindowControl(JSONObject(target),runtime,revision)
                        if(ticket==null) {button.press.cancel(source);rejected++}
                        else {button.tickets[source]=downTime to ticket;button.sources[source]=sourceOfInput to state;pressed(button,true)}
                    }
                } else {
                    if(button.press.contains(source,downTime)) button.press.move(source,at,layout.diameter*.3)
                    val ticket=button.tickets[source]?.takeIf {it.first==downTime}
                    val clicked=button.press.up(source,downTime,"$target:$revision")
                    if(ticket!=null) {button.tickets.remove(source);button.sources.remove(source);pressed(button,false)}
                    if(clicked && ticket!=null && live()) {NativeSessionHost.windowControl(ticket.second,button.action);submitted++}
                }
            }
            return true // Consume only our separate control sphere; no Wayland pointer/key event.
        }
        override fun onDragMove(receiver: SceneObject,hitInfo: HitInfo,sourceOfInput: Entity,state: Int) {
            val p=point(hitInfo);ui.post {if(!closed) button.press.move(sourceOfInput.id,p,layout.diameter*.3)}
        }
        override fun onHoverStop(receiver: SceneObject,sourceOfInput: Entity) {
            if(button.action=="move") ui.post {move.pointerLost(sourceOfInput.id)} else cancel(button,sourceOfInput.id)
        }
        override fun stopInput(receiver: SceneObject,sourceOfInput: Entity,downTime: Long) {
            if(button.action=="move") ui.post {move.pointerLost(sourceOfInput.id,downTime)} else cancel(button,sourceOfInput.id)
        }
        override fun onPointerEvent(receiver: SceneObject,hitInfo: HitInfo,type: Int,sourceOfInput: Entity,scrollInfo: Vector2,semanticType: Int) {
            if(button.action=="move") {
                if(type==PointerEventType.Cancel.id) ui.post {move.cancelSource(sourceOfInput.id,"pointer_cancel")}
                else if(type==PointerEventType.Unhover.id) ui.post {move.pointerLost(sourceOfInput.id)}
            } else if(type==PointerEventType.Cancel.id || type==PointerEventType.Unhover.id) cancel(button,sourceOfInput.id)
        }
    }
    private fun cancel(button: Button,source: Long) {ui.post {if(!closed) {
        button.press.cancel(source);button.tickets.remove(source);button.sources.remove(source);pressed(button,false)
    }}}
    private fun pressed(button: Button,pressed: Boolean) {
        val scale=if(pressed && !NativeSessionHost.themeUi.value.reduced) .78f else .86f
        setAbsoluteScale(button.face,Vector3(button.diameter*scale,button.diameter*scale,button.diameter*.2375f))
    }
    fun setEnabled(value: Boolean) {
        val next=value && failures==0L
        if(closed || enabled==next) return
        enabled=next
        buttons.forEach {button ->
            button.entity.setComponent(Hittable(if(next) MeshCollision.LineTest else MeshCollision.NoCollision))
            if(!next) {cancelSources(button);pressed(button,false)}
        }
    }
    private fun cancelSources(button: Button) {
        for((source,state) in button.sources.values.toList()) runCatching {sdk.onNoIntersectionInput(source,state,0)}
        if(button.sources.isNotEmpty()) runCatching {sdk.resolveInput()}
        button.sources.clear();button.tickets.clear();button.press.clear()
    }
    fun metadata(): JSONObject=JSONObject().put("revision",revision).put("enabled",enabled).put("requests_submitted",submitted).put("rejected",rejected)
        .put("failures",failures).put("entities",buttons.size*3).put("extra_android_displays",0).put("extra_surfaces",0)
        .put("buttons",JSONArray(buttons.map {JSONObject().put("action",it.action).put("entity",it.entity.id.toString())
            .put("ready",it.listener!=null).put("target",JSONObject(target))}))
    override fun close() {
        if(closed) return
        closed=true
        buttons.forEach {button ->
            cancelSources(button)
            runCatching {button.listener?.let {token -> checkNotNull(button.obj).removeInputListener(token);listenerRemoves++}}.onFailure {failures++}
            for(entity in listOf(button.icon,button.face,button.entity)) runCatching {destroyEntity(entity)}.onFailure {failures++}
        }
        buttons.clear()
    }
    private fun destroyEntity(entity: Entity) {entity.destroy();entityDestroys++}
    companion object {
        private var entityCreates=0L
        private var entityDestroys=0L
        private var listenerAdds=0L
        private var listenerRemoves=0L
        /** Successful SDK owner calls; actual deferred ray retirement is a separate fixture observation. */
        fun resources(): JSONObject=JSONObject().put("entity_create_calls",entityCreates).put("entity_destroy_calls",entityDestroys)
            .put("entity_owners",entityCreates-entityDestroys).put("listener_add_calls",listenerAdds).put("listener_remove_calls",listenerRemoves)
            .put("listener_owners",listenerAdds-listenerRemoves)
        private val PRIMARY=ButtonBits.ButtonTriggerL or ButtonBits.ButtonTriggerR or ButtonBits.ButtonA or ButtonBits.ButtonX
        fun parse(value: JSONObject?): WindowControlLayout? {
            if(value==null) return WindowControlLayout()
            if(value.keys().asSequence().toSet()!=setOf("anchor","offset_m","diameter_m","gap_m")) return null
            val offset=value.optJSONArray("offset_m") ?: return null
            fun number(v: Any?)=(v as? Number)?.toDouble()
            if(offset.length()!=2) return null
            return WindowControlLayout(value.opt("anchor") as? String ?: return null,
                number(offset.opt(0)) ?: return null,number(offset.opt(1)) ?: return null,
                number(value.opt("diameter_m")) ?: return null,number(value.opt("gap_m")) ?: return null).takeIf {it.valid()}
        }
    }
}
