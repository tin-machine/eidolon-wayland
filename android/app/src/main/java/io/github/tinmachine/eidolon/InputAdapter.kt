package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.Process
import android.os.SystemClock
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Vector2
import com.meta.spatial.runtime.ButtonBits
import com.meta.spatial.runtime.HitInfo
import com.meta.spatial.runtime.InputListener
import com.meta.spatial.runtime.SceneObject
import com.meta.spatial.runtime.SemanticType
import com.meta.spatial.runtime.PointerEventType
import org.json.JSONArray
import org.json.JSONObject

/** SDKから小DTOだけを渡す。描画済み製品target以外は入力を消費しない。 */
class InputAdapter(target: JSONObject, private val editSource: (Entity,Int)->Unit = { _, _ -> }) : InputListener {
    @Volatile private var suppressed=false
    fun setSuppressed(value: Boolean) { if(suppressed!=value) {suppressed=value;capture=null;synchronized(inputLock) {clearLocal()} } }
    private val ui = Handler(Looper.getMainLooper())
    @Volatile private var targetJson = target.toString()
    private data class Capture(val source: Long, val target: String, val receive: (JSONObject) -> Unit)
    @Volatile private var capture: Capture? = null
    @Volatile private var closed = false
    private val inputLock = Any()
    private data class Source(var pressed: Int=0, var armedThumb: Int=0, var xAxis: Boolean=false, var yAxis: Boolean=false,
        var pointerAxes: List<Double>?=null, var lastScroll: Long=0)
    private val sources = linkedMapOf<Long,Source>()
    private var stamp = ""
    private val thumbMask = ButtonBits.ButtonThumbLU or ButtonBits.ButtonThumbLD or ButtonBits.ButtonThumbLL or ButtonBits.ButtonThumbLR or
        ButtonBits.ButtonThumbRU or ButtonBits.ButtonThumbRD or ButtonBits.ButtonThumbRL or ButtonBits.ButtonThumbRR
    fun updateTarget(target: JSONObject) { capture = null; targetJson = target.toString(); synchronized(inputLock) { clearLocal() } }
    fun beginCapture(source: Long, receive: (JSONObject) -> Unit) { capture = Capture(source, targetJson, receive) }
    fun endCapture() { capture = null }
    fun close() { closed=true; capture=null; synchronized(inputLock) { clearLocal() } }
    private fun clearLocal() { sources.clear(); stamp="" }
    private fun source(ticket: NativeSessionHost.InputTarget, id: Long): Source? {
        val next="${ticket.runtime}:${ticket.epoch}:${ticket.identity}"
        if (stamp!=next) { clearLocal();stamp=next }
        if (id !in sources && sources.size>=8) { clearLocal(); NativeSessionHost.resetInput(); return null }
        return sources.getOrPut(id) { Source() }
    }
    private fun buttons(bits: Int): Int =
        (if (bits and (ButtonBits.ButtonTriggerL or ButtonBits.ButtonTriggerR)!=0) 1 else 0) or
        (if (bits and (ButtonBits.ButtonB or ButtonBits.ButtonY)!=0) 2 else 0) or
        (if (bits and (ButtonBits.ButtonThumbLClick or ButtonBits.ButtonThumbRClick)!=0) 4 else 0)
    private fun uv(receiver: SceneObject, hit: HitInfo): List<Double>? {
        // SDKはdrag中に別objectのHitInfoを元receiverへ渡す。別面のUVを再利用しない。
        val receiverId=receiver.entity?.id ?: return null
        if (hit.entity?.id!=receiverId) return null
        val u=hit.textureCoordinate.x.toDouble(); val v=hit.textureCoordinate.y.toDouble()
        return if (u.isFinite() && v.isFinite() && kotlin.math.abs(u)<=16 && kotlin.math.abs(v)<=16) listOf(u,v) else null
    }
    private fun scrollEvent(source: Source, now: Long): JSONObject? {
        val supplied=source.pointerAxes; source.pointerAxes=null
        fun direction(positive: Int, negative: Int): Double =
            ((if(source.armedThumb and positive!=0) 1 else 0)-(if(source.armedThumb and negative!=0) 1 else 0)).toDouble()
        val axis=supplied ?: listOf(direction(ButtonBits.ButtonThumbLR or ButtonBits.ButtonThumbRR,ButtonBits.ButtonThumbLL or ButtonBits.ButtonThumbRL),
            direction(ButtonBits.ButtonThumbLU or ButtonBits.ButtonThumbRU,ButtonBits.ButtonThumbLD or ButtonBits.ButtonThumbRD))
        if (axis.any { !it.isFinite() || kotlin.math.abs(it)>16 }) { NativeSessionHost.resetInput();return null }
        val elapsed=if(source.lastScroll==0L) 16L else (now-source.lastScroll).coerceIn(0,100)
        source.lastScroll=if(axis.any { it!=0.0 }) now else 0
        // 実axis/方向buttonをcontinuous rateへ変換する。長いcallback間隔の分を一度に再生しない。
        val factor=240.0*elapsed/1000.0
        // SDK/AndroidのVSCROLLは上が正、Waylandのvertical axisは下が正。
        val x=axis[0]*factor; val y=-axis[1]*factor
        if (!x.isFinite() || !y.isFinite() || kotlin.math.abs(x)>4096 || kotlin.math.abs(y)>4096) { NativeSessionHost.resetInput(); return null }
        // elapsed=0は入力停止ではない。実axisがneutralになったときだけstopする。
        val stopX=source.xAxis && axis[0]==0.0; val stopY=source.yAxis && axis[1]==0.0
        source.xAxis=axis[0]!=0.0; source.yAxis=axis[1]!=0.0
        return if (x!=0.0 || y!=0.0 || stopX || stopY) JSONObject().put("type","scroll")
            .put("delta",JSONArray(listOf(x,y))).put("stop",JSONArray(listOf(stopX,stopY))) else null
    }
    private fun leave(id: Long, cancel: Boolean=false) {
        synchronized(inputLock) { sources.remove(id) }
        val ticket=if (closed) null else NativeSessionHost.inputTarget(targetJson)
        if (ticket!=null) NativeSessionHost.sendPointer(ticket,id,null,JSONObject().put("type",if(cancel)"cancel" else "leave"))
    }

    private fun symbols(bits: Int): JSONArray = JSONArray(listOf(
        "trigger_left" to ButtonBits.ButtonTriggerL, "trigger_right" to ButtonBits.ButtonTriggerR,
        "a" to ButtonBits.ButtonA, "b" to ButtonBits.ButtonB, "x" to ButtonBits.ButtonX, "y" to ButtonBits.ButtonY,
    ).filter { bits and it.second != 0 }.map { it.first })

    override fun onInput(receiver: SceneObject, hitInfo: HitInfo, sourceOfInput: Entity,
        changed: Int, state: Int, downTime: Long): Boolean {
        if (closed) return false
        if(suppressed || NativeSessionHost.isLayoutEditing()) {ui.post { if(!closed) editSource(sourceOfInput,state) };return false}
        val probe = capture
        val target = targetJson
        if (probe!=null) {
            if (probe.source != sourceOfInput.id || probe.target != target) return false
            val u=hitInfo.textureCoordinate.x.toDouble(); val v=hitInfo.textureCoordinate.y.toDouble()
            if (!u.isFinite() || !v.isFinite()) return false
            publish(probe, JSONObject().put("event", "input").put("target", JSONObject(target))
                .put("uv", JSONArray(listOf(u,v))).put("changed", symbols(changed)).put("state", symbols(state))
                .put("callback_thread", Process.myTid()).put("source", sourceOfInput.id.toString()))
            return false
        }
        val ticket=NativeSessionHost.inputTarget(target) ?: return false
        if (changed and (ButtonBits.ButtonMenu or ButtonBits.ButtonSystem)!=0) { NativeSessionHost.resetInput();return false }
        val maskChanged=buttons(changed); val maskState=buttons(state)
        val scrolling=synchronized(inputLock) {
            val local=source(ticket,sourceOfInput.id) ?: return false
            local.pressed=(local.pressed and maskChanged.inv()) or (maskState and maskChanged)
            // 新epochで旧thumb heldからscrollを再生しない。新しい方向downだけをarmする。
            local.armedThumb=((local.armedThumb and changed.inv()) or (state and changed)) and thumbMask
            scrollEvent(local,SystemClock.uptimeMillis())
        }
        val position=uv(receiver,hitInfo)
        val consumed=NativeSessionHost.sendPointer(ticket,sourceOfInput.id,position,JSONObject().put("type","buttons")
            .put("changed",maskChanged).put("state",maskState))
        if (scrolling!=null) NativeSessionHost.sendPointer(ticket,sourceOfInput.id,position,scrolling)
        return consumed
    }
    override fun stopInput(receiver: SceneObject, sourceOfInput: Entity, downTime: Long) {
        if(suppressed || NativeSessionHost.isLayoutEditing()) {ui.post { if(!closed) editSource(sourceOfInput,0) };return}
        val probe = capture
        val target = targetJson
        if (probe!=null) {
            if (probe.source != sourceOfInput.id || probe.target != target) return
            publish(probe, JSONObject().put("event", "stop").put("target", JSONObject(target))
                .put("callback_thread", Process.myTid()).put("source", sourceOfInput.id.toString()))
        } else leave(sourceOfInput.id)
    }
    override fun onPointerEvent(receiver: SceneObject, hitInfo: HitInfo, type: Int, sourceOfInput: Entity, scrollInfo: Vector2, semanticType: Int) {
        if (closed || suppressed || NativeSessionHost.isLayoutEditing() || capture!=null) return
        if (type==PointerEventType.Cancel.id) { leave(sourceOfInput.id,true);return }
        if (type==PointerEventType.Unhover.id) { onHoverStop(receiver,sourceOfInput);return }
        val ticket=NativeSessionHost.inputTarget(targetJson) ?: return
        synchronized(inputLock) {
            val local=source(ticket,sourceOfInput.id) ?: return
            local.pointerAxes=if(semanticType==SemanticType.Scroll.id) listOf(scrollInfo.x.toDouble(),scrollInfo.y.toDouble()) else listOf(0.0,0.0)
        }
        val position=uv(receiver,hitInfo)
        NativeSessionHost.sendPointer(ticket,sourceOfInput.id,position,JSONObject().put("type","move"))
    }
    override fun onHoverStop(receiver: SceneObject, sourceOfInput: Entity) {
        if (closed || suppressed || NativeSessionHost.isLayoutEditing() || capture!=null) return
        if (synchronized(inputLock) { (sources[sourceOfInput.id]?.pressed ?: 0)==0 }) leave(sourceOfInput.id)
    }
    override fun onDragMove(receiver: SceneObject, hitInfo: HitInfo, sourceOfInput: Entity, state: Int) {
        if (closed || suppressed || NativeSessionHost.isLayoutEditing() || capture!=null) return
        val ticket=NativeSessionHost.inputTarget(targetJson) ?: return
        NativeSessionHost.sendPointer(ticket,sourceOfInput.id,uv(receiver,hitInfo),JSONObject().put("type","move"))
    }
    private fun publish(probe: Capture, value: JSONObject) {
        // 生の物理入力は採取しない。debugの専用source Entityだけを限定採取する。
        ui.post { if (capture === probe) probe.receive(value.put("delivery_thread", Process.myTid())) }
    }
}
