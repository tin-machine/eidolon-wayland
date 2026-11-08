package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import org.json.JSONObject

/** 通常Host APIを一度ずつ呼び、operatorの入力・Dialog・Closeを観測するdebug専用case。 */
object NormalLinuxGtkCase {
    private val ui=Handler(Looper.getMainLooper())
    private var active:Run?=null
    private val consumed=ArrayDeque<String>()
    fun commandComplete(runtime:Long,request:String,code:String) {ui.post {
        active?.takeIf {it.handle==runtime && request in setOf(it.connectRequest,it.inspectRequest,it.launchRequest)}?.let {run ->
            if(!run.done && !run.stopRequested && code!="ACCEPTED") run.finish("failed","LINUX_COMMAND_REJECTED")
        }
    }}
    fun command(name:String,nonce:String) {
        check(Looper.myLooper()==Looper.getMainLooper())
        if(name=="normal-linux-gtk-stop") {active?.takeIf {it.nonce==nonce}?.stop();return}
        if(name!="normal-linux-gtk" || !nonce.matches(Regex("[0-9a-f]{32}")) || active!=null || nonce in consumed) return
        if(consumed.size==64) consumed.removeFirst()
        consumed.addLast(nonce);Run(nonce).also {active=it;ui.post(it)}
    }
    private class Run(val nonce:String):Runnable {
        val began=SystemClock.elapsedRealtime()
        var handle=0L;var scene="";var session="";var previousSession=""
        var connectRequest="";var inspectRequest="";var launchRequest=""
        var phase="START";var result="running";var reason:String?=null;var done=false
        var stopRequested=false;var stopBegan=0L
        var catalogVerified=false;var launched=false
        var connectDispatched=false;var connectReplyKnown=false;var stopSent=false
        var trace:NormalLinuxGtkTrace?=null
        fun publish() {
            NativeSessionHost.publishCheck(JSONObject().put("case","normal-linux-gtk").put("nonce",nonce).put("scope","production_linux_manual_observation")
                .put("result",result).put("phase",phase).put("reason",reason ?: JSONObject.NULL)
                .put("runtime",handle.takeIf {it>0}?.toString() ?: JSONObject.NULL).put("session",session.ifEmpty {null} ?: JSONObject.NULL)
                .put("scene",scene.ifEmpty {null} ?: JSONObject.NULL).put("connect_request_id",connectRequest.ifEmpty {null} ?: JSONObject.NULL)
                .put("inspect_request_id",inspectRequest.ifEmpty {null} ?: JSONObject.NULL).put("launch_request_id",launchRequest.ifEmpty {null} ?: JSONObject.NULL)
                .put("catalog_verified",catalogVerified).put("launched",launched).put("stop_requested",stopRequested)
                .put("physical_input_verified",false).put("visual_input_verified",false).put("ime_verified",false)
                .put("trace",trace?.json() ?: JSONObject.NULL))
        }
        fun finish(outcome:String,error:String?=null) {
            done=true;result=outcome;reason=error
            phase=if(trace?.cleanupComplete==true) "COMPLETE" else "PENDING_CLEANUP"
            publish();ui.removeCallbacks(this)
            val beforeLaunch=!launched && runCatching {
                val runtime=JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("runtime")
                beforeLaunchClosed(runtime)
            }.getOrDefault(false)
            if(trace?.resourcesCleared()==true || !connectDispatched || connectReplyKnown && connectRequest.isEmpty() || beforeLaunch) {if(active===this) active=null}
        }
        fun beforeLaunchClosed(runtime:JSONObject):Boolean {
            val transport=runtime.getJSONObject("transport")
            return !launched && connectReplyKnown && (connectRequest.isEmpty() || session.isNotEmpty() && transport.optString("session")==session) &&
                transport.optString("state") in setOf("Idle","Disconnected","Failed") && transport.optInt("owned_connections")==0 &&
                !transport.optBoolean("desktop_pending") && (runtime.optJSONObject("app")?.optString("state") ?: "stopped")=="stopped" &&
                runtime.getJSONArray("panels").length()==0 && runtime.getJSONObject("render").getJSONArray("targets").length()==0 &&
                runtime.getJSONObject("shutdown").getInt("sdk_bindings")==0 && runtime.getJSONObject("window_slots").getInt("occupied")==0
        }
        fun stop() {
            if(stopRequested || trace?.cleanupComplete==true) return
            stopRequested=true;stopBegan=SystemClock.elapsedRealtime();done=false
            phase="STOPPING";publish();ui.post(this)
        }
        fun transition(value:String) {phase=value;publish()}
        override fun run() {
            if(done || active!==this) return
            try {
                val now=SystemClock.elapsedRealtime()
                check(now-(if(stopRequested) stopBegan else began)<if(stopRequested) 25000 else 300000) {"LINUX_OBSERVATION_TIMEOUT"}
                val current=NativeSessionHost.currentHandle()
                check(current>0 && (handle==0L || handle==current)) {"LINUX_RUNTIME_CHANGED"}
                val envelope=JSONObject(NativeBridge.nativeSnapshot(current))
                check(envelope.getInt("code")==NativeBridge.ACCEPTED) {"LINUX_RUNTIME_UNAVAILABLE"}
                val runtime=envelope.getJSONObject("runtime");val transport=runtime.getJSONObject("transport")
                if(phase=="START") {
                    check(NativeSessionHost.selectedConnection.value==LinuxConnectionProfile.ID && NativeSessionHost.launcherUi.value.canConnect &&
                        runtime.getBoolean("activity_visible") && runtime.getString("xr_state")=="FOCUSED") {"LINUX_START_UNAVAILABLE"}
                    handle=current;scene=runtime.getString("scene");previousSession=transport.optString("session")
                    connectDispatched=true
                    transition("CONNECTING")
                    NativeSessionHost.connect {id,code ->ui.post {
                        if(active===this) {
                            connectReplyKnown=true;connectRequest=id.takeIf {code==NativeBridge.ACCEPTED} ?: ""
                            if(!done && !stopRequested && code!=NativeBridge.ACCEPTED) finish("failed","LINUX_CONNECT_REJECTED") else publish()
                        }
                    }}
                } else {
                    check(runtime.getString("scene")==scene) {"LINUX_SCENE_CHANGED"}
                    if(session.isEmpty() && connectRequest.isNotEmpty() && transport.optString("profile_id")==LinuxConnectionProfile.ID &&
                        transport.optString("session")!=previousSession && transport.optString("session").toLongOrNull()!=null) session=transport.getString("session")
                    if(session.isNotEmpty()) check(transport.getString("session")==session && transport.getString("profile_id")==LinuxConnectionProfile.ID) {"LINUX_SESSION_CHANGED"}
                    trace?.observe(current,runtime,NativeSessionHost.debugAdapterSnapshot())
                    if(stopRequested) {
                        if(!stopSent && connectReplyKnown && connectRequest.isNotEmpty()) {
                            // session未公開でも元Connect要求だけを取消。新しい接続を補完して停止しない。
                            NativeSessionHost.debugCancelConnect(handle,connectRequest);stopSent=true
                        }
                        if(trace?.cleanupComplete==true) {finish("cancelled","LINUX_EXPLICIT_STOP");return}
                        if(trace?.resourcesCleared()==true) {finish("cancelled","LINUX_STOP_OBSERVATION_INCOMPLETE");return}
                        // 起動前の取消でも、未知owner/pendingを成功回収に見せない。
                        if(trace==null && beforeLaunchClosed(runtime)) {
                            finish("cancelled","LINUX_STOP_BEFORE_LAUNCH");if(active===this) active=null;return
                        }
                    } else when(phase) {
                        "CONNECTING" -> if(connectRequest.isNotEmpty() && session.isNotEmpty()) {
                            check(transport.optString("state") !in setOf("Failed","CleanupPending")) {"LINUX_CONNECT_OR_RECOVERY_REQUIRED"}
                            if(transport.optString("state")=="ProbeReady") {
                                transition("INSPECTING")
                                NativeSessionHost.inspectLinuxDesktop {id,code ->ui.post {
                                    if(active===this) {
                                        inspectRequest=id
                                        if(!done && !stopRequested && code!=NativeBridge.ACCEPTED) finish("failed","LINUX_INSPECT_REJECTED") else publish()
                                    }
                                }}
                            }
                        }
                        "INSPECTING" -> {
                            check(transport.optString("state") !in setOf("Failed","CleanupPending","Disconnected")) {"LINUX_INSPECT_FAILED"}
                            val catalog=NativeSessionHost.launcherUi.value
                            if(inspectRequest.isNotEmpty() && catalog.linux && catalog.session==session && catalog.connected && !catalog.busy &&
                                catalog.entries.singleOrNull()?.let {it.id==LinuxDesktopCatalog.APP_ID && it.available}==true) {
                                catalogVerified=true;transition("LAUNCHING")
                                NativeSessionHost.launchRegistered(session,catalog.revision,LinuxDesktopCatalog.APP_ID) {id,code ->ui.post {
                                    if(active===this) {
                                        launchRequest=id
                                        if(code==NativeBridge.ACCEPTED) {launched=true;trace=NormalLinuxGtkTrace(handle,scene,session,id)}
                                        if(!done && !stopRequested) {
                                            if(code!=NativeBridge.ACCEPTED) finish("failed","LINUX_LAUNCH_REJECTED") else transition("OBSERVING")
                                        } else publish()
                                    }
                                }}
                            }
                        }
                        "OBSERVING" -> {
                            val value=checkNotNull(trace)
                            if(value.cleanupComplete) {finish(if(value.complete()) "completed" else "incomplete",if(value.complete()) null else "LINUX_OBSERVATIONS_INCOMPLETE");return}
                            if(value.resourcesCleared()) {finish("incomplete","LINUX_OBSERVATIONS_INCOMPLETE");return}
                            if(transport.optString("state")=="CleanupPending" && transport.optInt("owned_connections")==0) {
                                finish("incomplete","LINUX_CLEANUP_PENDING");return
                            }
                        }
                    }
                }
                publish();ui.postDelayed(this,200)
            } catch(error:Exception) {
                finish("failed",error.message?.takeIf {it.matches(Regex("[A-Z][A-Z0-9_]{0,63}"))} ?: "LINUX_TRACE_INVALID")
            }
        }
    }
}
