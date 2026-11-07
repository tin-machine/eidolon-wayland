package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import org.json.JSONArray
import org.json.JSONObject

/** 主操作と同じ入口を検証する。物理キー/HMDの受入れとは区別する。 */
object ExtensionsSdkCase {
    fun start(nonce:String, complete:(JSONObject)->Unit) {
        val ui=Handler(Looper.getMainLooper())
        val result=JSONObject().put("case","extensions").put("nonce",nonce).put("result","running")
            .put("consumer","SDK").put("synthetic_visibility",false).put("hmd_verified",false).put("physical_input",false)
        var stage="ready"
        var first:NativeSessionHost.WindowChoice?=null
        var second:NativeSessionHost.WindowChoice?=null
        val deadline=SystemClock.elapsedRealtime()+80000
        NativeSessionHost.publishCheck(result)
        ui.post(object:Runnable {
            override fun run() {
                try {
                    check(SystemClock.elapsedRealtime()<deadline) {"EXTENSIONS_TIMEOUT"}
                    val handle=NativeSessionHost.currentHandle()
                    if(handle==0L) {ui.postDelayed(this,32);return}
                    if(result.optString("stage")!=stage) {result.put("stage",stage);NativeSessionHost.publishCheck(result)}
                    val runtime=JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("runtime")
                    check(runtime.getString("state")!="Failed")
                    val focused=runtime.getBoolean("activity_visible") && runtime.getString("xr_state")=="FOCUSED"
                    val app=runtime.optJSONObject("app")
                    val windows=NativeSessionHost.windowChoices.value.filter {it.slot!=null}
                    val render=runtime.getJSONObject("render").getJSONArray("targets")
                    fun rendered(n:Int):Boolean {
                        val logical=app?.optJSONArray("windows") ?: return false
                        return focused && windows.size==n && render.length()==n &&
                            (0 until render.length()).all {index ->
                                val target=render.getJSONObject(index)
                                target.optString("role")=="windows" && target.getLong("frames")>=1 &&
                                    (0 until logical.length()).any {i ->
                                        val window=logical.getJSONObject(i)
                                        window.getString("window")==target.getJSONObject("key").getString("window") &&
                                            window.optString("frame")==target.optString("content_frame")
                                    }
                            }
                    }
                    fun inputWindow()=app?.optJSONObject("input")?.optString("focus_window")
                    when(stage) {
                        "ready" -> if(focused && runtime.getString("state")=="Ready") {
                            check(runtime.getJSONObject("transport").getString("state")=="Idle")
                            NativeSessionHost.openTerminal();NativeSessionHost.openTerminal();stage="first"
                        }
                        "first" -> if(rendered(1)) {
                            first=windows.single();NativeSessionHost.setLayoutEditing(true);stage="editing"
                        }
                        "editing" -> if(NativeSessionHost.inputStatus.value.reason=="EDITING") {
                            check(!NativeSessionHost.keyboardFocused())
                            NativeSessionHost.selectWindow(checkNotNull(first));NativeSessionHost.finishLayoutEditing();stage="returned"
                        }
                        "returned" -> if(NativeSessionHost.keyboardFocused() && inputWindow()==first?.window) {
                            check(!NativeSessionHost.layoutEditing.value)
                            result.put("editing_return_focus",true)
                            NativeSessionHost.openTerminal();NativeSessionHost.openTerminal();stage="two"
                        }
                        "two" -> if(rendered(2)) {
                            second=windows.single {it.window!=first?.window}
                            NativeSessionHost.focusTerminal(second);stage="second"
                        }
                        "second" -> if(NativeSessionHost.keyboardFocused() && inputWindow()==second?.window) {
                            NativeSessionHost.resetInput();stage="reset"
                        }
                        "reset" -> if(!NativeSessionHost.keyboardFocused() && inputWindow() !in windows.map {it.window}) {
                            NativeSessionHost.focusTerminal(first);stage="selected"
                        }
                        "selected" -> if(NativeSessionHost.keyboardFocused() && inputWindow()==first?.window && rendered(2)) {
                            val metrics=NativeSessionHost.deviceMetrics.value
                            check(metrics.batteryPercent in 0..100 && (metrics.totalRam ?: 0)>0 && metrics.availableRam!=null)
                            check(metrics.sampledMs>0 && SystemClock.elapsedRealtime()-metrics.sampledMs<10000)
                            check(metrics.network=="connected" && metrics.addresses.isNotEmpty())
                            result.put("explicit_window_focus",true).put("input_reset",true).put("coalesced_open",true)
                                .put("device",metrics.json()).put("cycles",JSONArray().put(JSONObject().put("runtime",runtime)))
                                .put("result","pass")
                            complete(result);return
                        }
                    }
                    ui.postDelayed(this,32)
                } catch(failure:Exception) {
                    result.put("result","fail").put("stage",stage).put("failure_class",failure.javaClass.simpleName)
                        .put("failure_site",failure.stackTrace.firstOrNull()?.toString())
                    complete(result)
                }
            }
        })
    }
}
