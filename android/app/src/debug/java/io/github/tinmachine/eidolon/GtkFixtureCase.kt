package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.KeyEvent
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicLong

/** 固定GUI synthetic入力。IME/物理filter/SDK rayの合格にはしない。 */
object GtkFixtureCase {
    private val ui=Handler(Looper.getMainLooper())
    private val requests=AtomicLong(9_400_000)
    private var active:Run?=null
    fun command(name:String,nonce:String) {
        check(Looper.myLooper()==Looper.getMainLooper())
        val current=active
        if(name in setOf("gtk-close","gtk-cancel","gtk-dialog-dismiss")) {
            if(current?.nonce!=nonce) return
            when(name) {
                "gtk-cancel" -> current.finish("cancelled","GTK_HOST_CANCEL")
                "gtk-dialog-dismiss" -> if(current.name=="dialog" && current.phase=="DIALOG_READY") current.dismissRequested=true
                else -> current.closeRequested=true
            }
        } else if(current==null) {
            Run(name.removePrefix("gtk-"),nonce).also {active=it;ui.post(it)}
        }
    }
    private class Run(val name:String,val nonce:String):Runnable {
        val appId="io.github.tinmachine.eidolon.gtkfixture.r$nonce"
        val dialogId="$appId.dialog"
        val started=SystemClock.elapsedRealtime()
        val source=0x600000000000000L+nonce.take(8).toLong(16)
        val rows=JSONArray()
        var phase="START";var closing=false;var done=false;var closeRequested=false;var dismissRequested=false
        var owner:GtkFixtureBinding.Owner?=null
        var mainWindow="";var foot=setOf<String>();var theme=0L
        var currentTarget:GtkFixtureBinding.Target?=null
        var epoch=0L;var token="";var keys=0L;var buttons=0L;var stageStarted=started
        fun publish(result:String="running",reason:String?=null) {
            val binding=owner
            NativeSessionHost.publishCheck(JSONObject().put("case","gtk-$name").put("nonce",nonce).put("result",result).put("phase",phase)
                .put("reason",reason ?: JSONObject.NULL).put("scope","target_bound_synthetic_native")
                .put("physical_input_verified",false).put("ime_verified",false).put("host_physical_filter_verified",false)
                .put("runtime",binding?.handle?.toString() ?: JSONObject.NULL).put("session",binding?.session ?: JSONObject.NULL)
                .put("scene",binding?.scene ?: JSONObject.NULL).put("main_window",mainWindow.ifEmpty {null} ?: JSONObject.NULL)
                .put("target",currentTarget?.json() ?: JSONObject.NULL).put("input_epoch",epoch)
                .put("foot_windows",JSONArray(foot.sorted())).put("steps",rows))
        }
        fun record(stage:String,runtime:JSONObject,target:GtkFixtureBinding.Target?=currentTarget) {
            check(rows.length()<24) {"GTK_STEP_LIMIT"}
            rows.put(JSONObject().put("stage",stage).put("target",target?.json() ?: JSONObject.NULL)
                .put("input_epoch",runtime.getLong("input_resets")).put("delivered",runtime.getJSONObject("app").getJSONObject("input").getLong("delivered")))
            publish()
        }
        fun windows(runtime:JSONObject):Set<String> {
            val rows=runtime.getJSONObject("app").getJSONArray("windows")
            return (0 until rows.length()).map {rows.getJSONObject(it)}.filter {it.optString("life")=="Mapped"}.map {it.getString("window")}.toSet()
        }
        fun command(type:String,target:GtkFixtureBinding.Target,fields:JSONObject=JSONObject()) {
            val envelope=JSONObject().put("schema",1).put("type",type).put("request_id",requests.incrementAndGet().toString()).put("target",target.json())
            for(key in fields.keySet()) envelope.put(key,fields.get(key))
            check(NativeBridge.nativeCommand(checkNotNull(owner).handle,envelope.toString())==NativeBridge.ACCEPTED) {"GTK_ENQUEUE_REJECTED"}
        }
        fun click(runtime:JSONObject,target:GtkFixtureBinding.Target,u:Double,v:Double) {
            check(NativeSessionHost.currentHandle()==checkNotNull(owner).handle && runtime.getLong("input_resets")==epoch) {"GTK_EPOCH_CHANGED"}
            val ticket=checkNotNull(NativeSessionHost.inputTarget(target.json().toString())) {"GTK_INPUT_UNAVAILABLE"}
            check(ticket.runtime==checkNotNull(owner).handle && ticket.epoch==epoch) {"GTK_EPOCH_CHANGED"}
            currentTarget=target
            check(NativeSessionHost.sendPointer(ticket,source,listOf(u,v),JSONObject().put("type","buttons").put("changed",1).put("state",1))) {"GTK_POINTER_REJECTED"}
            check(NativeSessionHost.sendPointer(ticket,source,listOf(u,v),JSONObject().put("type","buttons").put("changed",1).put("state",0))) {"GTK_POINTER_REJECTED"}
        }
        fun focused(runtime:JSONObject,target:GtkFixtureBinding.Target):Boolean {
            val input=runtime.getJSONObject("app").getJSONObject("input")
            return runtime.getLong("input_resets")==epoch && input.optString("focus_window")==target.window && !input.isNull("focus_token") &&
                NativeSessionHost.keyboardFocusMatches(target.json(),input.getString("focus_token"))
        }
        fun key(runtime:JSONObject,target:GtkFixtureBinding.Target,code:Int,scan:Int,down:Boolean) {
            check(focused(runtime,target) && runtime.getJSONObject("app").getJSONObject("input").getString("focus_token")==token) {"GTK_FOCUS_CHANGED"}
            // 不変target/epoch/tokenをnative側でも検査。現在focusへ選択し直すAPIは使わない。
            val key=JSONObject().put("device",Int.MAX_VALUE-25).put("scan_code",scan).put("key_code",code)
                .put("action",if(down) "pressed" else "released").put("repeat",0).put("meta",0)
                .put("time_ms",SystemClock.uptimeMillis() and 0xffffffffL)
            command("key",target,JSONObject().put("input_epoch",epoch).put("focus_token",token).put("key",key))
        }
        fun resize(target:GtkFixtureBinding.Target,width:Int,height:Int) =
            command("resize_logical",target,JSONObject().put("width",width).put("height",height))
        fun resized(runtime:JSONObject,width:Int,height:Int):Boolean {
            val row=GtkFixtureBinding.window(runtime,appId) ?: return false
            val size=row.optJSONObject("logical_size") ?: return false
            val resize=row.optJSONObject("resize") ?: return false
            return size.optInt("width")==width && size.optInt("height")==height && resize.optString("phase")=="applied" &&
                GtkFixtureBinding.targetFor(runtime,mainWindow)!=null
        }
        fun transition(value:String) {phase=value;stageStarted=SystemClock.elapsedRealtime();publish()}
        fun finish(result:String,reason:String?=null) {
            if(done) return
            done=true;ui.removeCallbacks(this)
            // このsourceだけを取消。外部intent後のfocusや全inputをreset/restoreしない。
            currentTarget?.let {target->owner?.let {binding->runCatching {
                NativeBridge.nativeCommand(binding.handle,JSONObject().put("schema",1).put("type","pointer")
                    .put("request_id",requests.incrementAndGet().toString()).put("target",target.json()).put("input_epoch",epoch)
                    .put("pointer",JSONObject().put("source",source.toString()).put("uv",JSONObject.NULL)
                        .put("event",JSONObject().put("type","cancel")).put("time_ms",SystemClock.uptimeMillis() and 0xffffffffL)).toString())
            }}}
            phase=if(result=="completed") "COMPLETE" else if(result=="unsupported") "UNSUPPORTED" else "CANCELLED"
            publish(result,reason);if(active===this) active=null
        }
        override fun run() {
            if(done || active!==this) return
            try {
                check(SystemClock.elapsedRealtime()-started<35000) {"GTK_CASE_TIMEOUT"}
                val handle=NativeSessionHost.currentHandle()
                val envelope=JSONObject(NativeBridge.nativeSnapshot(handle));check(envelope.getInt("code")==NativeBridge.ACCEPTED) {"GTK_RUNTIME_UNAVAILABLE"}
                val runtime=envelope.getJSONObject("runtime");val binding=GtkFixtureBinding.owner(runtime,handle)
                check(NativeSessionHost.debugImeCanRequestFocus()) {"GTK_INPUT_UNAVAILABLE"}
                val input=runtime.getJSONObject("app").getJSONObject("input")
                if(owner==null) {
                    check(!BuildHooks.imeProbeActive() && NativeSessionHost.debugImeCanRequestFocus()) {"GTK_INPUT_UNAVAILABLE"}
                    val main=checkNotNull(GtkFixtureBinding.window(runtime,appId)) {"GTK_WINDOW_UNAVAILABLE"}
                    mainWindow=main.getString("window");foot=windows(runtime)-mainWindow
                    check(foot.size==1 && GtkFixtureBinding.window(runtime,dialogId)==null) {"GTK_BASELINE_INVALID"}
                    owner=binding;theme=runtime.getJSONObject("render").optLong("theme_revision",0)
                    epoch=runtime.getLong("input_resets");keys=input.getLong("delivered");buttons=input.getJSONObject("pointer").getLong("buttons")
                    val target=checkNotNull(GtkFixtureBinding.targetFor(runtime,mainWindow)) {"GTK_TARGET_UNAVAILABLE"};currentTarget=target
                    record("captured",runtime)
                    if(name=="resize") {resize(target,800,500);transition("WAIT_LARGE")}
                    else {
                        val height=main.getJSONObject("logical_size").getInt("height");check(height in 240..4096) {"GTK_GEOMETRY_UNSUPPORTED"}
                        click(runtime,target,.5,if(name=="input") 84.0/height else 1.0-68.0/height)
                        transition(if(name=="input") "WAIT_FOCUS" else "WAIT_DIALOG")
                    }
                } else {
                    check(binding==owner && runtime.getJSONObject("render").optLong("theme_revision",0)==theme) {"GTK_OWNER_CHANGED"}
                    check(windows(runtime).containsAll(foot)) {"GTK_BASELINE_LOST"}
                    val dialog=GtkFixtureBinding.window(runtime,dialogId)
                    val allowed=foot+mainWindow+listOfNotNull(dialog?.getString("window"))
                    check((windows(runtime)-allowed).isEmpty()) {"GTK_OTHER_WINDOW"}
                    when(phase) {
                        "WAIT_FOCUS" -> {
                          check(runtime.getLong("input_resets")==epoch && input.getLong("delivered")==keys &&
                              input.getJSONObject("pointer").getLong("buttons")<=buttons+2) {"GTK_OTHER_INPUT"}
                          if(focused(runtime,checkNotNull(currentTarget))) {
                            check(input.getLong("delivered")==keys && input.getJSONObject("pointer").getLong("buttons")==buttons+2) {"GTK_OTHER_INPUT"}
                            token=input.getString("focus_token");val target=checkNotNull(currentTarget);record("focused",runtime)
                            key(runtime,target,KeyEvent.KEYCODE_A,30,true);key(runtime,target,KeyEvent.KEYCODE_A,30,false)
                            key(runtime,target,KeyEvent.KEYCODE_DEL,14,true);key(runtime,target,KeyEvent.KEYCODE_DEL,14,false)
                            transition("WAIT_KEYS")
                          }
                        }
                        "WAIT_KEYS" -> {
                            check(focused(runtime,checkNotNull(currentTarget)) && input.getString("focus_token")==token) {"GTK_FOCUS_CHANGED"}
                            check(input.getLong("delivered")<=keys+4 && input.getJSONObject("pointer").getLong("buttons")==buttons+2) {"GTK_OTHER_INPUT"}
                            if(input.getLong("delivered")==keys+4 && input.getInt("pressed")==0) {
                                val height=checkNotNull(GtkFixtureBinding.window(runtime,appId)).getJSONObject("logical_size").getInt("height")
                                check(height in 240..4096) {"GTK_GEOMETRY_UNSUPPORTED"}
                                record("typed",runtime);click(runtime,checkNotNull(currentTarget),.5,1.0-68.0/height);transition("WAIT_CLICK")
                            }
                        }
                        "WAIT_CLICK" -> if(input.getJSONObject("pointer").getLong("buttons")==buttons+4 && input.getJSONObject("pointer").getInt("pressed")==0) {
                            record("clicked",runtime);transition("OPERATIONS_COMPLETE")
                        }
                        "WAIT_DIALOG" -> {
                            check(runtime.getLong("input_resets")==epoch && input.getLong("delivered")==keys &&
                                input.getJSONObject("pointer").getLong("buttons")<=buttons+2) {"GTK_OTHER_INPUT"}
                            val target=dialog?.let {GtkFixtureBinding.targetFor(runtime,it.getString("window"))}
                            if(target!=null) {
                                check(input.getJSONObject("pointer").getLong("buttons")==buttons+2) {"GTK_OTHER_INPUT"}
                                record("dialog_mapped",runtime,target)
                                click(runtime,target,.5,.3);transition("WAIT_DIALOG_FOCUS")
                            } else if(SystemClock.elapsedRealtime()-stageStarted>6000) {finish("unsupported","DIALOG_TARGET_UNAVAILABLE");return}
                        }
                        "WAIT_DIALOG_FOCUS" -> {
                            check(runtime.getLong("input_resets")==epoch && input.getLong("delivered")==keys &&
                                input.getJSONObject("pointer").getLong("buttons")<=buttons+4) {"GTK_OTHER_INPUT"}
                            if(focused(runtime,checkNotNull(currentTarget))) {
                                check(input.getJSONObject("pointer").getLong("buttons")==buttons+4) {"GTK_OTHER_INPUT"}
                                token=input.getString("focus_token");record("dialog_focused",runtime);transition("DIALOG_READY")
                            }
                        }
                        "DIALOG_READY" -> {
                            check(focused(runtime,checkNotNull(currentTarget)) && input.getString("focus_token")==token &&
                                input.getLong("delivered")==keys && input.getJSONObject("pointer").getLong("buttons")==buttons+4) {"GTK_FOCUS_CHANGED"}
                            if(dismissRequested) {
                                val target=checkNotNull(currentTarget)
                                key(runtime,target,KeyEvent.KEYCODE_ESCAPE,1,true);key(runtime,target,KeyEvent.KEYCODE_ESCAPE,1,false)
                                record("dialog_escape",runtime);transition("WAIT_DIALOG_CLOSED")
                            }
                        }
                        "WAIT_DIALOG_CLOSED" -> if(dialog==null) {record("dialog_retired",runtime);transition("OPERATIONS_COMPLETE")}
                        "WAIT_LARGE" -> if(resized(runtime,800,500)) {
                            currentTarget=checkNotNull(GtkFixtureBinding.targetFor(runtime,mainWindow));epoch=runtime.getLong("input_resets")
                            record("large",runtime);resize(checkNotNull(currentTarget),640,360);transition("WAIT_RETURN")
                        }
                        "WAIT_RETURN" -> if(resized(runtime,640,360)) {
                            currentTarget=checkNotNull(GtkFixtureBinding.targetFor(runtime,mainWindow));epoch=runtime.getLong("input_resets")
                            record("returned",runtime);transition("OPERATIONS_COMPLETE")
                        }
                        "OPERATIONS_COMPLETE" -> if(closeRequested) {
                            check(GtkFixtureBinding.window(runtime,appId)?.getString("window")==mainWindow) {"GTK_WINDOW_CHANGED"}
                            currentTarget=checkNotNull(GtkFixtureBinding.targetFor(runtime,mainWindow)) {"GTK_TARGET_UNAVAILABLE"}
                            command("close_window_target",checkNotNull(currentTarget),JSONObject().put("controls_revision",theme))
                            closing=true;transition("CLOSING")
                        }
                        "CLOSING" -> if(GtkFixtureBinding.window(runtime,appId)==null && GtkFixtureBinding.window(runtime,dialogId)==null) {
                            check(input.getInt("pressed")==0 && input.getJSONObject("pointer").getInt("pressed")==0) {"GTK_INPUT_HELD"}
                            record("retired",runtime);finish("completed");return
                        }
                    }
                }
                ui.postDelayed(this,32)
            } catch(failure:Exception) {
                val reason=failure.message?.takeIf {it.matches(Regex("(?:GTK_[A-Z_]+|DIALOG_TARGET_UNAVAILABLE)"))} ?: "GTK_CASE_FAILED"
                finish("failed",reason)
            }
        }
    }
}
