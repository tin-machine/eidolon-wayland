package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.InputDevice
import android.view.KeyEvent
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Vector2
import com.meta.spatial.core.Vector3
import com.meta.spatial.runtime.ButtonBits
import com.meta.spatial.runtime.HitInfo
import com.meta.spatial.runtime.PointerEventType
import com.meta.spatial.runtime.Scene
import com.meta.spatial.runtime.SemanticType
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicReference
import kotlin.math.abs

/** 専用sourceの合成入力を通常SDK listener→Host→Waypipe→footへ通す。 */
object PointerCase {
    fun start(nonce: String, adapter: SpatialPanelAdapter, scene: () -> Scene, complete: () -> Unit) {
        Check(nonce, adapter, scene, complete).start()
    }
    private class Check(val nonce: String, val adapter: SpatialPanelAdapter, val scene: () -> Scene, val complete: () -> Unit) : Runnable {
        val ui = Handler(Looper.getMainLooper())
        val result = JSONObject().put("case", "pointer").put("nonce", nonce).put("result", "running")
            .put("consumer", "SDK").put("synthetic_input", true).put("synthetic_menu", true)
            .put("synthetic_visibility", false).put("physical_input_verified", false).put("os_menu_verified", false)
            .put("product_host_path", true).put("dispatch", "Scene.onInput").put("raycast", "Scene.lineSegmentIntersect")
        val rows = JSONArray()
        val points = JSONArray()
        val hits = mutableListOf<HitInfo>()
        val sources = mutableListOf<Entity>()
        val stopped = AtomicReference<String?>(null)
        val trigger = ButtonBits.ButtonTriggerR
        var stage = "ready"
        var handle = 0L
        var target = JSONObject()
        var baseButtons = 0L
        var baseAxes = 0L
        var baseKeys = 0L
        var epoch = 0L
        var token = ""
        var capture = ""
        var motions = 0L
        var deadline = SystemClock.elapsedRealtime()+60000
        fun start() { NativeSessionHost.publishCheck(result); ui.post(this) }
        fun record(name: String, state: JSONObject) {
            rows.put(JSONObject().put("stage", name).put("runtime", state))
            result.put("cycles", rows); NativeSessionHost.publishCheck(result)
        }
        fun input(index: Int, changed: Int, state: Int, source: Int = 0) {
            scene().onInput(hits[index], sources[source], changed, state)
        }
        fun scroll(x: Float, y: Float) {
            checkNotNull(adapter.currentSceneObject()).onPointerEvent(hits[1], PointerEventType.Move.id, sources[0], Vector2(x,y), SemanticType.Scroll.id)
            input(1, 0, 0)
        }
        fun shift() {
            val now = SystemClock.uptimeMillis()
            check(NativeSessionHost.sendPhysicalKey(KeyEvent(now,now,KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_SHIFT_LEFT,0,0,
                Int.MAX_VALUE-8,42,0,InputDevice.SOURCE_KEYBOARD)))
        }
        fun cleanup() {
            try { sources.forEach { scene().onNoIntersectionInput(it,0,0) }; scene().resolveInput() }
            finally { sources.forEach { it.destroy() }; sources.clear() }
            result.put("sources_destroyed",true)
        }
        override fun run() {
            try {
                check(SystemClock.elapsedRealtime()<deadline) { "POINTER_TIMEOUT" }
                val current = NativeSessionHost.currentHandle()
                if (current>0) {
                    val state = JSONObject(NativeBridge.nativeSnapshot(current)).getJSONObject("runtime")
                    check(state.getString("state")!="Failed")
                    val app = state.optJSONObject("app")
                    if (app!=null) check(app.isNull("error"))
                    val transport = state.getJSONObject("transport")
                    val input = app?.optJSONObject("input")
                    val pointer = input?.optJSONObject("pointer")
                    val targets = state.getJSONObject("render").getJSONArray("targets")
                    val panel = if(targets.length()==1) targets.getJSONObject(0) else null
                    val focused = state.getBoolean("activity_visible") && state.getString("xr_state")=="FOCUSED"
                    when(stage) {
                        "ready" -> if(state.getString("state")=="Ready" && focused) {
                            check(transport.getString("state")=="Idle")
                            handle=current; NativeSessionHost.openTerminal(); NativeSessionHost.disconnect(); stage="cancelled"
                        }
                        "cancelled" -> if(transport.getString("state")=="Disconnected" && transport.getInt("owned_connections")==0 && transport.getInt("tokio_threads")==0) {
                            check(app==null && transport.getJSONArray("pending_cleanup").length()==0)
                            result.put("cancelled",state).put("credential_base",transport.getLong("credential_requests"))
                            NativeSessionHost.openTerminal(); stage="display"
                        }
                        "display" -> if(focused && app?.optString("mode")=="foot" && panel?.optString("role")=="windows" && panel.getLong("frames")>=3) {
                            val key=checkNotNull(adapter.currentKey())
                            if(NativeSessionHost.inputTarget(key.toString())!=null && adapter.currentSceneObject()!=null) {
                                target=key
                                if(sources.isEmpty()) { sources.add(Entity.create(emptyList())); sources.add(Entity.create(emptyList())) }
                                if(points.length()<2) {
                                    val (u,v)=listOf(.23 to .31,.73 to .61)[points.length()]
                                    val x=((u-.5)*1.2).toFloat(); val y=(1.4+(.5-v)*.9).toFloat()
                                    val from=Vector3(.3f,1.7f,.2f); val factor=(2.5f-.2f)/(1.5f-.2f)
                                    val to=Vector3(.3f+(x-.3f)*factor,1.7f+(y-1.7f)*factor,2.5f)
                                    val hit=scene().lineSegmentIntersect(from,to)
                                    if(hit!=null) {
                                        check(hit.entity?.id==adapter.currentEntityId())
                                        check(abs(hit.textureCoordinate.x-u)<.02 && abs(hit.textureCoordinate.y-v)<.02)
                                        check(abs(hit.point.x-x)<.0001 && abs(hit.point.y-y)<.0001 && abs(hit.point.z-1.5)<.0001)
                                        hits.add(hit)
                                        points.put(JSONObject().put("expected",JSONArray(listOf(u,v)))
                                            .put("uv",JSONArray(listOf(hit.textureCoordinate.x,hit.textureCoordinate.y)))
                                            .put("world",JSONArray(listOf(x,y,1.5))).put("entity",checkNotNull(hit.entity).id.toString())
                                            .put("hit_point",JSONArray(listOf(hit.point.x,hit.point.y,hit.point.z))))
                                    }
                                }
                                if(points.length()==2) {
                                    result.put("points",points).put("sources",JSONArray(sources.map { it.id.toString() }))
                                    baseButtons=checkNotNull(pointer).getLong("buttons"); baseAxes=pointer.getLong("axis_frames")
                                    baseKeys=checkNotNull(input).getLong("delivered"); record("before",state)
                                    input(0,0,0); stage="hover"
                                }
                            }
                        }
                        "hover" -> if(pointer?.optString("hover_window")==target.getString("window")) {
                            check(checkNotNull(input).isNull("focus_window") && pointer.getInt("pressed")==0)
                            record("hover",state); input(0,trigger,trigger); stage="pressed"
                        }
                        "pressed" -> if(pointer?.getInt("pressed")==1 && NativeSessionHost.keyboardFocused()) {
                            check(pointer.getLong("buttons")==baseButtons+1)
                            capture=pointer.getString("capture_surface"); token=checkNotNull(input).getString("focus_token")
                            record("pressed",state)
                            input(1,trigger,trigger,1); input(1,trigger,0,1)
                            scene().onNoIntersectionInput(sources[1],0,0)
                            scene().onNoIntersectionInput(sources[0],0,trigger)
                            stage="foreign"
                        }
                        "foreign" -> {
                            check(checkNotNull(pointer).getInt("pressed")==1 && pointer.getLong("buttons")==baseButtons+1 && pointer.getString("capture_surface")==capture)
                            record("foreign",state); motions=pointer.getLong("motions")
                            checkNotNull(adapter.currentSceneObject()).onDragMove(hits[1],sources[0],trigger); stage="drag"
                        }
                        "drag" -> if(checkNotNull(pointer).getLong("motions")>motions) {
                            check(pointer.getInt("pressed")==1 && pointer.getString("capture_surface")==capture)
                            record("drag",state); scene().onNoIntersectionInput(sources[0],trigger,0); stage="released"
                        }
                        "released" -> if(pointer?.getInt("pressed")==0 && pointer.getLong("buttons")==baseButtons+2) {
                            check(pointer.isNull("capture_surface") && checkNotNull(input).getString("focus_token")==token)
                            record("released",state); scroll(.25f,-.5f); stage="scroll"
                        }
                        "scroll" -> if(checkNotNull(pointer).getLong("axis_frames")==baseAxes+1) {
                            record("scroll",state); scroll(0f,0f); stage="scroll_stop"
                        }
                        "scroll_stop" -> if(checkNotNull(pointer).getLong("axis_frames")==baseAxes+2) {
                            record("scroll_stop",state); input(1,trigger,trigger); shift(); stage="held"
                        }
                        "held" -> if(pointer?.getInt("pressed")==1 && checkNotNull(input).getInt("pressed")==1) {
                            check(pointer.getLong("buttons")==baseButtons+3 && input.getLong("delivered")==baseKeys+1)
                            record("held",state); epoch=state.getLong("input_resets")
                            input(1,ButtonBits.ButtonMenu,ButtonBits.ButtonMenu or trigger); stage="reset"
                        }
                        "reset" -> if(state.getLong("input_resets")>epoch && pointer?.getInt("pressed")==0 && checkNotNull(input).getInt("pressed")==0 && input.isNull("focus_window")) {
                            check(pointer.getLong("buttons")==baseButtons+4 && input.getLong("delivered")==baseKeys+2)
                            if(NativeSessionHost.inputTarget(target.toString())?.epoch==state.getLong("input_resets")) {
                                record("reset",state); motions=pointer.getLong("motions"); input(1,0,trigger); stage="old_held"
                            }
                        }
                        "old_held" -> if(checkNotNull(pointer).getLong("motions")>motions) {
                            check(pointer.getInt("pressed")==0 && pointer.getLong("buttons")==baseButtons+4 && checkNotNull(input).isNull("focus_window"))
                            record("old_held",state); scene().onNoIntersectionInput(sources[0],trigger,0)
                            input(1,trigger,trigger); input(1,trigger,0); stage="refocused"
                        }
                        "refocused" -> if(pointer?.getLong("buttons")==baseButtons+6 && pointer.getInt("pressed")==0 && NativeSessionHost.keyboardFocused()) {
                            check(checkNotNull(input).getString("focus_token").toLong()>token.toLong())
                            record("refocused",state); cleanup(); stage="restore"; deadline=SystemClock.elapsedRealtime()+20000
                            val started=SystemClock.elapsedRealtime(); val copy=result.toString()
                            NativeSessionHost.withStoppedRuntime({ store ->
                                check(JSONObject(NativeBridge.nativeSnapshot(handle)).getInt("code")==NativeBridge.STALE)
                                stopped.set(JSONObject().put("old_runtime_destroyed",true).put("stop_ms",SystemClock.elapsedRealtime()-started).toString())
                                store.publishCheck(JSONObject(copy).put("stop",JSONObject(checkNotNull(stopped.get()))))
                            }, {})
                        }
                        "restore" -> if(current>handle && focused && panel?.optString("role")=="diagnostic" && panel.getLong("frames")>=3) {
                            check(transport.getString("state")=="Idle" && transport.getJSONArray("pending_cleanup").length()==0)
                            result.put("old_handle",handle.toString()).put("new_handle",current.toString()).put("target",target)
                                .put("stop",JSONObject(checkNotNull(stopped.get()))).put("restored",state).put("result","pass")
                            NativeSessionHost.publishCheck(result); complete(); return
                        }
                    }
                }
                ui.postDelayed(this,32)
            } catch(failure: Exception) {
                result.put("result","fail").put("stage",stage).put("failure_class",failure.javaClass.simpleName)
                    .put("failure_site",failure.stackTrace.firstOrNull()?.toString()).put("snapshot",JSONObject(NativeBridge.nativeSnapshot(NativeSessionHost.currentHandle())))
                try { cleanup() } catch(_: Exception) { result.put("cleanup_failed",true) }
                val copy=result.toString()
                NativeSessionHost.withStoppedRuntime({ store -> store.publishCheck(JSONObject(copy)) },complete)
            }
        }
    }
}
