package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.Process
import com.meta.spatial.core.Entity
import com.meta.spatial.runtime.ButtonBits
import com.meta.spatial.runtime.HitInfo
import com.meta.spatial.runtime.InputListener
import com.meta.spatial.runtime.SceneObject
import org.json.JSONArray
import org.json.JSONObject

/** SDK境界の小さいadapter。Waylandへの配送はI03で接続し、未接続の入力を消費しない。 */
class InputAdapter(target: JSONObject) : InputListener {
    private val ui = Handler(Looper.getMainLooper())
    @Volatile private var targetJson = target.toString()
    private data class Capture(val source: Long, val target: String, val receive: (JSONObject) -> Unit)
    @Volatile private var capture: Capture? = null
    fun updateTarget(target: JSONObject) { capture = null; targetJson = target.toString() }
    fun beginCapture(source: Long, receive: (JSONObject) -> Unit) { capture = Capture(source, targetJson, receive) }
    fun endCapture() { capture = null }

    private fun symbols(bits: Int): JSONArray = JSONArray(listOf(
        "trigger_left" to ButtonBits.ButtonTriggerL, "trigger_right" to ButtonBits.ButtonTriggerR,
        "a" to ButtonBits.ButtonA, "b" to ButtonBits.ButtonB, "x" to ButtonBits.ButtonX, "y" to ButtonBits.ButtonY,
    ).filter { bits and it.second != 0 }.map { it.first })

    override fun onInput(receiver: SceneObject, hitInfo: HitInfo, sourceOfInput: Entity,
        changed: Int, state: Int, downTime: Long): Boolean {
        val probe = capture ?: return false
        val target = targetJson
        if (probe.source != sourceOfInput.id || probe.target != target) return false
        val u = hitInfo.textureCoordinate.x.toDouble()
        val v = hitInfo.textureCoordinate.y.toDouble()
        if (!u.isFinite() || !v.isFinite()) return false
        publish(probe, JSONObject().put("event", "input").put("target", JSONObject(target))
            .put("uv", JSONArray(listOf(u,v))).put("changed", symbols(changed)).put("state", symbols(state))
            .put("callback_thread", Process.myTid()).put("source", sourceOfInput.id.toString()))
        return false
    }
    override fun stopInput(receiver: SceneObject, sourceOfInput: Entity, downTime: Long) {
        val probe = capture ?: return
        val target = targetJson
        if (probe.source != sourceOfInput.id || probe.target != target) return
        publish(probe, JSONObject().put("event", "stop").put("target", JSONObject(target))
            .put("callback_thread", Process.myTid()).put("source", sourceOfInput.id.toString()))
    }
    private fun publish(probe: Capture, value: JSONObject) {
        // 生の物理入力は採取しない。debugの専用source Entityだけを限定採取する。
        ui.post { if (capture === probe) probe.receive(value.put("delivery_thread", Process.myTid())) }
    }
}
