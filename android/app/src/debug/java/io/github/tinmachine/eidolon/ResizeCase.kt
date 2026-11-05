package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.InputDevice
import android.view.KeyEvent
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Vector3
import com.meta.spatial.runtime.ButtonBits
import com.meta.spatial.runtime.Scene
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicReference
import kotlin.math.abs

/** 通常Hostのlogical resize/SDK physical sizeと、その後の固定入力を検査する。 */
object ResizeCase {
    fun start(nonce:String,adapter:SpatialPanelAdapter,scene:()->Scene,complete:()->Unit) { Check(nonce,adapter,scene,complete).start() }
    private class Check(val nonce:String,val adapter:SpatialPanelAdapter,val scene:()->Scene,val complete:()->Unit):Runnable {
        val ui=Handler(Looper.getMainLooper())
        val result=JSONObject().put("case","resize").put("nonce",nonce).put("result","running").put("consumer","SDK")
            .put("product_host_path",true).put("synthetic_input",true).put("synthetic_visibility",false)
            .put("physical_input_verified",false).put("text_verified",false)
        val rows=JSONArray(); val stop=AtomicReference<String?>(null)
        var stage="ready"; var handle=0L; var target=JSONObject(); var source:Entity?=null
        var configures=0L; var serial=0L; var buttons=0L; var keys=0L; var copies=0L
        var deadline=SystemClock.elapsedRealtime()+60000
        fun start() { NativeSessionHost.publishCheck(result); ui.post(this) }
        fun record(name:String,state:JSONObject) { rows.put(JSONObject().put("stage",name).put("runtime",state));result.put("cycles",rows);NativeSessionHost.publishCheck(result) }
        fun key(action:Int,code:Int,scan:Int) {
            val now=SystemClock.uptimeMillis()
            check(NativeSessionHost.sendPhysicalKey(KeyEvent(now,now,action,code,0,0,Int.MAX_VALUE-9,scan,0,InputDevice.SOURCE_KEYBOARD)))
        }
        fun cleanup() { source?.let { scene().onNoIntersectionInput(it,0,0);scene().resolveInput();it.destroy() };source=null;result.put("source_destroyed",true) }
        override fun run() {
            try {
                check(SystemClock.elapsedRealtime()<deadline) { "RESIZE_TIMEOUT" }
                val current=NativeSessionHost.currentHandle()
                if(current>0) {
                    val state=JSONObject(NativeBridge.nativeSnapshot(current)).getJSONObject("runtime")
                    check(state.getString("state")!="Failed")
                    val focused=state.getBoolean("activity_visible") && state.getString("xr_state")=="FOCUSED"
                    val app=state.optJSONObject("app");if(app!=null) check(app.isNull("error"))
                    val windows=app?.optJSONArray("windows"); val window=if(windows?.length()==1) windows.getJSONObject(0) else null
                    val resize=window?.optJSONObject("resize"); val sizing=window?.optJSONObject("sizing")
                    val targets=state.getJSONObject("render").getJSONArray("targets");val rendered=if(targets.length()==1) targets.getJSONObject(0) else null
                    fun applied(w:Int,h:Int):Boolean = resize?.optString("phase")=="applied" && resize.getJSONObject("committed_size").getInt("width")==w && resize.getJSONObject("committed_size").getInt("height")==h &&
                        rendered?.optString("content_frame")?.toLongOrNull()?.let { it>=checkNotNull(window).getString("frame").toLong() }==true
                    when(stage) {
                        "ready" -> if(state.getString("state")=="Ready" && focused) {
                            check(state.getJSONObject("transport").getString("state")=="Idle");handle=current;NativeSessionHost.openTerminal();stage="display"
                        }
                        "display" -> if(focused && rendered?.optString("role")=="windows" && rendered.getLong("frames")>=3 && sizing?.optJSONObject("applied_physical_size_m")!=null) {
                            target=checkNotNull(adapter.currentKey());check(NativeSessionHost.inputTarget(target.toString())!=null)
                            record("before",state);NativeSessionHost.resizeTerminal(800,500);stage="small"
                        }
                        "small" -> if(focused && applied(800,500)) {
                            record("small",state);NativeSessionHost.resizeTerminal(960,640);NativeSessionHost.resizeTerminal(1280,720);stage="wide"
                        }
                        "wide" -> if(focused && applied(1280,720)) {
                            record("wide",state);configures=checkNotNull(app).getLong("configures");serial=checkNotNull(resize).getLong("configured_serial")
                            NativeSessionHost.setPhysicalSize(1.44,1.08);stage="physical"
                        }
                        "physical" -> if(focused && sizing?.optJSONObject("applied_physical_size_m")?.let { abs(it.getDouble("width_m")-1.44)<1e-4 && abs(it.getDouble("height_m")-1.08)<1e-4 }==true) {
                            check(checkNotNull(app).getLong("configures")==configures && checkNotNull(resize).getLong("configured_serial")==serial)
                            check(sizing.getJSONObject("producer_size_px").getInt("width")==1024 && sizing.getJSONObject("producer_size_px").getInt("height")==768)
                            record("physical",state)
                            val from=Vector3(.3f,1.7f,.2f);val x=(.03*1.44).toFloat();val y=(1.4+.03*1.08).toFloat();val factor=2.3f/1.3f
                            val hit=scene().lineSegmentIntersect(from,Vector3(.3f+(x-.3f)*factor,1.7f+(y-1.7f)*factor,2.5f))
                            checkNotNull(hit);check(hit.entity?.id==adapter.currentEntityId())
                            check(abs(hit.textureCoordinate.x-.53)<.02 && abs(hit.textureCoordinate.y-.47)<.02)
                            check(abs(hit.point.x-x)<1e-4 && abs(hit.point.y-y)<1e-4 && abs(hit.point.z-1.5)<1e-4)
                            result.put("hit",JSONObject().put("uv",JSONArray(listOf(hit.textureCoordinate.x,hit.textureCoordinate.y)))
                                .put("world",JSONArray(listOf(hit.point.x,hit.point.y,hit.point.z))))
                            source=Entity.create(emptyList());buttons=app.getJSONObject("input").getJSONObject("pointer").getLong("buttons")
                            scene().onInput(hit,checkNotNull(source),ButtonBits.ButtonTriggerR,ButtonBits.ButtonTriggerR)
                            scene().onInput(hit,checkNotNull(source),ButtonBits.ButtonTriggerR,0);stage="clicked"
                        }
                        "clicked" -> if(focused && NativeSessionHost.keyboardFocused() && app?.getJSONObject("input")?.getJSONObject("pointer")?.getLong("buttons")==buttons+2) {
                            record("clicked",state);keys=app.getJSONObject("input").getLong("delivered");copies=app.getLong("copies")
                            key(KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_A,30);key(KeyEvent.ACTION_UP,KeyEvent.KEYCODE_A,30)
                            key(KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_DEL,14);key(KeyEvent.ACTION_UP,KeyEvent.KEYCODE_DEL,14);stage="typed"
                        }
                        "typed" -> if(focused && app?.getJSONObject("input")?.getLong("delivered")==keys+4 && app.getLong("copies")>copies) {
                            check(app.getJSONObject("input").getInt("pressed")==0);record("typed",state);cleanup();stage="restore";deadline=SystemClock.elapsedRealtime()+20000
                            val started=SystemClock.elapsedRealtime();val copy=result.toString()
                            NativeSessionHost.withStoppedRuntime({ store ->
                                check(JSONObject(NativeBridge.nativeSnapshot(handle)).getInt("code")==NativeBridge.STALE)
                                stop.set(JSONObject().put("old_runtime_destroyed",true).put("stop_ms",SystemClock.elapsedRealtime()-started).toString())
                                store.publishCheck(JSONObject(copy).put("stop",JSONObject(checkNotNull(stop.get()))))
                            },{})
                        }
                        "restore" -> if(current>handle && focused && rendered?.optString("role")=="diagnostic" && rendered.getLong("frames")>=3) {
                            check(state.getJSONObject("transport").getString("state")=="Idle" && state.getJSONObject("transport").getJSONArray("pending_cleanup").length()==0)
                            result.put("target",target).put("old_handle",handle.toString()).put("new_handle",current.toString()).put("restored",state)
                                .put("stop",JSONObject(checkNotNull(stop.get()))).put("result","pass")
                            NativeSessionHost.publishCheck(result);complete();return
                        }
                    }
                }
                ui.postDelayed(this,32)
            } catch(failure:Exception) {
                result.put("result","fail").put("stage",stage).put("failure_class",failure.javaClass.simpleName)
                    .put("failure_site",failure.stackTrace.firstOrNull()?.toString()).put("snapshot",JSONObject(NativeBridge.nativeSnapshot(NativeSessionHost.currentHandle())))
                try { cleanup() } catch(_:Exception) { result.put("cleanup_failed",true) }
                val copy=result.toString();NativeSessionHost.withStoppedRuntime({store->store.publishCheck(JSONObject(copy))},complete)
            }
        }
    }
}
