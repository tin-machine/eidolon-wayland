package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.Process
import android.os.SystemClock
import java.io.File
import org.json.JSONArray
import org.json.JSONObject

/** 実foot/通常Host/SDKでdisconnectとruntime停止を反復する。装着や物理入力の代替ではない。 */
object LifecycleSdkCase {
    fun start(nonce:String,adapter:SpatialPanelAdapter,complete:(JSONObject)->Unit) = Check(nonce,adapter,complete).start()
    private class Check(val nonce:String,val adapter:SpatialPanelAdapter,val complete:(JSONObject)->Unit):Runnable {
        val ui=Handler(Looper.getMainLooper())
        val rows=JSONArray()
        val result=JSONObject().put("case","stop").put("nonce",nonce).put("result","running")
            .put("consumer","SDK").put("uid",Process.myUid()).put("pid",Process.myPid())
            .put("synthetic_visibility",false).put("physical_input_verified",false).put("hmd_verified",false)
        var stage="ready"
        var deadline=SystemClock.elapsedRealtime()+60000
        var handle=0L
        var started=0L
        var current=JSONObject()
        var beforeSession=0L
        var disconnectedAt=0L
        fun start() {NativeSessionHost.publishCheck(result);ui.post(this)}
        fun items(a:JSONArray)=(0 until a.length()).map {a.getJSONObject(it)}
        fun visible(s:JSONObject)=s.getString("state")=="Ready" && s.getBoolean("activity_visible") && s.getString("xr_state")=="FOCUSED"
        fun ready(s:JSONObject):Boolean {
            val app=s.optJSONObject("app") ?: return false
            val targets=items(s.getJSONObject("render").getJSONArray("targets"))
            val windows=items(app.getJSONArray("windows"))
            return visible(s) && app.getString("state")=="running" && app.isNull("error") && app.getString("mode")=="foot" &&
                s.getJSONObject("transport").getString("state")=="Ready" && windows.size==1 && targets.size==1 &&
                targets[0].getString("content_frame")==windows[0].getString("frame") && targets[0].getLong("frames")>=1 &&
                adapter.metrics().getInt("bindings")==1 && adapter.metrics().getInt("listeners")==1 &&
                s.getJSONObject("shutdown").getInt("sdk_bindings")==1
        }
        fun pick(s:JSONObject,vararg fields:String)=JSONObject().also {out->fields.forEach {out.put(it,s.opt(it) ?: JSONObject.NULL)}}
        fun nativeResources(s:JSONObject):JSONObject {
            val app=s.optJSONObject("app")
            val transport=s.getJSONObject("transport")
            val render=s.getJSONObject("render")
            val compactApp=app?.let {pick(it,"state","session","mode","clients","objects","surfaces","pixel_bytes","pool_bytes","pool_count","memfd_fds_after","memfd_maps_after","directory_cleaned","error")
                .put("local",it.getJSONObject("local")).put("windows",JSONArray(items(it.getJSONArray("windows")).map {w->pick(w,"window","frame","life")}))}
            val compactTransport=pick(transport,"state","session","owned_connections","workers","tokio_threads","pending_cleanup","error")
                .put("forward_active",transport.getJSONObject("forward").getInt("active"))
                .put("app_forward",transport.optJSONObject("app_forward")?.let {pick(it,"uid","remote_cleaned","forward_cancelled","pending_cleanup","nonce")})
            return pick(s,"state","helper_workers","dependency_workers","compositor_workers","shutdown")
                .put("app",compactApp).put("transport",compactTransport).put("render",pick(render,"mode","workers","owner_tid","executor","contexts","latest_images","error")
                    .put("targets",JSONArray(items(render.getJSONArray("targets")).map {pick(it,"key","role","frames","content_frame")})))
        }
        fun resources(s:JSONObject):JSONObject {
            val fds=GpuFdProbe.snapshot()
            return nativeResources(s)
                .put("handle",handle.toString()).put("session",s.getJSONObject("transport").optString("session"))
                .put("adapter",JSONObject()
                    .put("bindings",adapter.metrics().getInt("bindings")).put("listeners",adapter.metrics().getInt("listeners"))
                    .put("decoration_entities",adapter.metrics().getInt("decoration_entities")))
                .put("fds",fds.getInt("fds")).put("fd_kinds",fds.getJSONObject("fd_kinds"))
                .put("gpu_fds",fds.getJSONArray("gpu"))
        }
        fun open(next:String) {
            NativeSessionHost.openTerminal();NativeSessionHost.openTerminal()
            stage=next;deadline=SystemClock.elapsedRealtime()+60000
        }
        override fun run() {
            try {
                check(SystemClock.elapsedRealtime()<deadline) {"LIFECYCLE_TIMEOUT"}
                handle=NativeSessionHost.currentHandle()
                if(handle==0L) {ui.postDelayed(this,32);return}
                val s=JSONObject(NativeBridge.nativeSnapshot(handle)).optJSONObject("runtime")
                if(s==null) {ui.postDelayed(this,32);return}
                when(stage) {
                    "ready" -> if(visible(s)) {current=JSONObject().put("cycle",rows.length()+1);open("opened")}
                    "opened" -> if(ready(s)) {
                        val session=s.getJSONObject("transport").getString("session").toLong()
                        check(session>beforeSession);beforeSession=session
                        current.put("before_disconnect",resources(s))
                        NativeSessionHost.disconnect();NativeSessionHost.disconnect()
                        started=SystemClock.elapsedRealtime();deadline=started+10000;stage="disconnected"
                    }
                    "disconnected" -> {
                        val app=s.optJSONObject("app");val t=s.getJSONObject("transport")
                        val metrics=adapter.metrics()
                        if(visible(s) && app?.getString("state")=="stopped" && t.getString("state")=="Disconnected" &&
                            t.getInt("owned_connections")==0 && t.getJSONObject("forward").getInt("active")==0 &&
                            s.getJSONObject("render").getJSONArray("targets").length()==0 &&
                            s.getJSONArray("panels").length()==0 && metrics.getInt("bindings")==0 && metrics.getInt("listeners")==0) {
                            check(t.getJSONArray("pending_cleanup").length()==0)
                            check(t.getJSONObject("app_forward").getBoolean("remote_cleaned"))
                            disconnectedAt=SystemClock.elapsedRealtime()
                            current.put("disconnect_ms",disconnectedAt-started).put("after_disconnect_immediate",resources(s))
                            stage="disconnect_settle"
                        }
                    }
                    "disconnect_settle" -> {
                        // SDKの非同期解放を一律1秒待つ。最小FDになる瞬間を選ばない。
                        val t=s.getJSONObject("transport")
                        check(visible(s) && t.getString("state")=="Disconnected" && t.getInt("owned_connections")==0 &&
                            s.getJSONObject("app").getString("state")=="stopped" && s.getJSONArray("panels").length()==0 &&
                            s.getJSONObject("render").getJSONArray("targets").length()==0 && adapter.metrics().getInt("bindings")==0)
                        val waited=SystemClock.elapsedRealtime()-disconnectedAt
                        if(waited>=1000) {
                            current.put("disconnect_settle_ms",waited).put("after_disconnect",resources(s))
                            open("reopened")
                        }
                    }
                    "reopened" -> if(ready(s)) {
                        val session=s.getJSONObject("transport").getString("session").toLong()
                        check(session>beforeSession);beforeSession=session
                        current.put("before_stop",resources(s))
                        val old=handle
                        stage="restarting";deadline=SystemClock.elapsedRealtime()+15000
                        NativeSessionHost.withStoppedRuntime({
                            val receipt=checkNotNull(NativeSessionHost.stopReceipt())
                            check(receipt.getString("handle")==old.toString())
                            val stale=JSONObject(NativeBridge.nativeSnapshot(old))
                            check(stale.getInt("code")==NativeBridge.STALE && stale.getJSONObject("registry").getInt("runtimes")==0)
                            val saved=pick(receipt,"handle","accept_ms","stop_ms").put("runtime",nativeResources(receipt.getJSONObject("snapshot").getJSONObject("runtime")))
                            synchronized(current) {current.put("stop",saved).put("old_handle_rejected",true)}
                        },{})
                    }
                    "restarting" -> if(visible(s) && handle>current.getJSONObject("before_stop").getString("handle").toLong()) {
                        val targets=items(s.getJSONObject("render").getJSONArray("targets"))
                        if(s.getJSONObject("transport").getString("state")=="Idle" && targets.size==1 &&
                            targets[0].getString("role")=="diagnostic" && targets[0].getLong("frames")>=3 &&
                            adapter.metrics().getInt("bindings")==1 && adapter.metrics().getInt("listeners")==1) {
                            synchronized(current) {check(current.has("stop"));current.put("after_restart",resources(s));rows.put(current)}
                            result.put("cycles",rows);NativeSessionHost.publishCheck(result)
                            if(rows.length()==20) {result.put("result","pass");complete(result);return}
                            stage="ready";deadline=SystemClock.elapsedRealtime()+60000
                        }
                    }
                }
                ui.postDelayed(this,32)
            } catch(failure:Exception) {
                result.put("result","fail").put("stage",stage).put("cycles",rows).put("last_cycle",current)
                    .put("failure_class",failure.javaClass.simpleName).put("failure_site",failure.stackTrace.firstOrNull()?.toString())
                if(handle!=0L) result.put("failure_state",JSONObject(NativeBridge.nativeSnapshot(handle)))
                complete(result)
            }
        }
    }
}
