package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.KeyEvent
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicLong

/** 製品launchRegistered→receipt→full target→InputTrial。物理/IME/SDK ray試験にはしない。 */
object FreedoomGuiCase {
    private val ui=Handler(Looper.getMainLooper())
    private val requests=AtomicLong(9_850_000)
    @Volatile private var active:Run?=null
    private val consumed=ArrayDeque<String>()
    fun command(name:String,nonce:String) {
        check(Looper.myLooper()==Looper.getMainLooper())
        if(name=="freedoom-stop") {active?.takeIf {it.nonce==nonce}?.requestStop();return}
        if(name !in setOf("freedoom-keys","freedoom-close") || !nonce.matches(Regex("[0-9a-f]{32}")) || active!=null || nonce in consumed) return
        if(consumed.size==64) consumed.removeFirst()
        consumed.addLast(nonce);Run(name.removePrefix("freedoom-"),nonce).also {active=it;ui.post(it)}
    }
    fun commandComplete(runtime:Long,request:String,code:String) {
        active?.takeIf {it.owner?.handle==runtime && it.pendingCommands.containsKey(request)}?.acks?.put(request,code)
    }
    private class Run(val name:String,val nonce:String):Runnable {
        val began=SystemClock.elapsedRealtime();val source=0x650000000000000L+nonce.take(8).toLong(16)
        val steps=JSONArray();val pendingCommands=ConcurrentHashMap<String,String>();val acks=ConcurrentHashMap<String,String>()
        var phase="START";var stageBegan=began;var outcome="running";var reason:String?=null;var cleanupReason:String?=null;var done=false;var stopRequested=false
        @Volatile var owner:GtkFixtureBinding.Owner?=null;var trial:NativeSessionHost.DebugInputTrial?=null
        var foot=setOf<String>();var target:GtkFixtureBinding.Target?=null;var epoch=0L;var theme=0L
        var previousCatalog="";var revision="";var request="";var launch="";var appId="";var window=""
        var launchAccepted=false;var launchDispatched=false;var token="";var keys=0L;var buttons=0L;var expectedKeys=0L;var content=0L;var keyStage=0
        var firstClose="";var secondClose="";var firstClosePending=false;var natural=false;var cleanup=false;var stopSent=false
        fun rows(array:JSONArray)=(0 until array.length()).map {array.getJSONObject(it)}
        fun windows(runtime:JSONObject)=rows(runtime.getJSONObject("app").getJSONArray("windows")).filter {it.optString("life")=="Mapped"}.map {it.getString("window")}.toSet()
        fun publish() {
            NativeSessionHost.publishCheck(JSONObject().put("case","freedoom-$name").put("nonce",nonce).put("result",outcome).put("phase",phase)
                .put("reason",reason ?: JSONObject.NULL).put("cleanup_reason",cleanupReason ?: JSONObject.NULL).put("scope","production_registered_target_bound_synthetic_native")
                .put("physical_input_verified",false).put("ime_verified",false).put("host_physical_filter_verified",false)
                .put("runtime",owner?.handle?.toString() ?: JSONObject.NULL).put("session",owner?.session ?: JSONObject.NULL).put("scene",owner?.scene ?: JSONObject.NULL)
                .put("foot_windows",JSONArray(foot.sorted())).put("revision",revision.ifEmpty {null} ?: JSONObject.NULL)
                .put("request_id",request.ifEmpty {null} ?: JSONObject.NULL).put("launch_id",launch.ifEmpty {null} ?: JSONObject.NULL)
                .put("app_id",appId.ifEmpty {null} ?: JSONObject.NULL).put("target",target?.json() ?: JSONObject.NULL).put("input_epoch",epoch)
                .put("first_close_pending",firstClosePending).put("natural_exit",natural).put("cleanup_complete",cleanup).put("session_stop_sent",stopSent)
                .put("commands",JSONArray(pendingCommands.entries.sortedBy {it.key.toLong()}.map {JSONObject().put("request_id",it.key).put("kind",it.value).put("completion",acks[it.key] ?: JSONObject.NULL)}))
                .put("steps",steps))
        }
        fun transition(next:String) {phase=next;stageBegan=SystemClock.elapsedRealtime();publish()}
        fun record(stage:String,runtime:JSONObject) {
            check(steps.length()<16) {"FREEDOOM_STEP_LIMIT"}
            steps.put(JSONObject().put("stage",stage).put("target",target?.json() ?: JSONObject.NULL)
                .put("input_epoch",runtime.getLong("input_resets")).put("delivered",runtime.getJSONObject("app").getJSONObject("input").getLong("delivered")))
            publish()
        }
        fun finish(result:String,error:String?=null) {
            if(stopRequested && outcome!="running") cleanupReason=error else {outcome=result;reason=error}
            done=true;phase=if(cleanup) "COMPLETE" else "PENDING_CLEANUP"
            trial?.let {NativeSessionHost.debugReleaseInput(it)};trial=null;publish();ui.removeCallbacks(this)
            if(cleanup || !launchDispatched) {if(active===this) active=null}
        }
        fun requestStop() {
            if(stopRequested || cleanup) return
            stopRequested=true;done=false;stageBegan=SystemClock.elapsedRealtime()
            trial?.let {NativeSessionHost.debugReleaseInput(it)};trial=null
            // 固定caseの取得済みruntime/sessionだけ。遅れたworkerから新sessionを停止しない。
            owner?.let {NativeSessionHost.debugDisconnectSession(it.handle,it.session);stopSent=true}
            phase="SESSION_STOP";publish();ui.post(this)
        }
        fun enqueue(type:String,fields:JSONObject,focus:String?=null,down:Boolean=false):String {
            val key=checkNotNull(target);val captured=checkNotNull(trial);val ticket=checkNotNull(NativeSessionHost.debugInputTarget(captured,key.json())) {"FREEDOOM_TARGET_CHANGED"}
            val id=requests.incrementAndGet().toString();val command=JSONObject().put("schema",1).put("type",type).put("request_id",id).put("target",key.json())
            for(field in fields.keys().asSequence()) command.put(field,fields.get(field))
            pendingCommands[id]=type
            check(NativeSessionHost.debugInputDispatch(captured,ticket,focus,down) {NativeBridge.nativeCommand(checkNotNull(owner).handle,command.toString())}) {"FREEDOOM_INTENT_LOST"}
            return id
        }
        fun click() {
            for(state in listOf(1,0)) enqueue("pointer",JSONObject().put("input_epoch",epoch).put("pointer",JSONObject().put("source",source.toString())
                .put("uv",JSONArray(listOf(.5,.5))).put("event",JSONObject().put("type","buttons").put("changed",1).put("state",state))
                .put("time_ms",SystemClock.uptimeMillis() and 0xffffffffL)),down=state==1)
        }
        fun focused(runtime:JSONObject):Boolean {
            val input=runtime.getJSONObject("app").getJSONObject("input");val key=checkNotNull(target)
            return runtime.getLong("input_resets")==epoch && input.optString("focus_window")==key.window && !input.isNull("focus_token") &&
                NativeSessionHost.debugInputFocusMatches(checkNotNull(trial),key.json(),input.getString("focus_token"))
        }
        fun key(code:Int,scan:Int,runtime:JSONObject) {
            check(focused(runtime) && runtime.getJSONObject("app").getJSONObject("input").getString("focus_token")==token) {"FREEDOOM_FOCUS_CHANGED"}
            for(pressed in listOf(true,false)) enqueue("key",JSONObject().put("input_epoch",epoch).put("focus_token",token).put("key",JSONObject()
                .put("device",Int.MAX_VALUE-25).put("scan_code",scan).put("key_code",code).put("action",if(pressed) "pressed" else "released")
                .put("repeat",0).put("meta",0).put("time_ms",SystemClock.uptimeMillis() and 0xffffffffL)),focus=token)
            expectedKeys+=2
        }
        fun close():String {
            val key=checkNotNull(target);val id=requests.incrementAndGet().toString();pendingCommands[id]="close_window_target"
            check(NativeSessionHost.debugCloseInputTarget(checkNotNull(trial),key.json()) {
                NativeBridge.nativeCommand(checkNotNull(owner).handle,JSONObject().put("schema",1).put("type","close_window_target").put("request_id",id)
                    .put("target",key.json()).put("controls_revision",theme).toString())
            }) {"FREEDOOM_INTENT_LOST"};return id
        }
        fun seal() {val captured=checkNotNull(trial);check(NativeSessionHost.debugSealInput(captured) && NativeSessionHost.debugExpectRetirement(captured,checkNotNull(target).json())) {"FREEDOOM_INTENT_LOST"}}
        fun content(runtime:JSONObject):Long = rows(runtime.getJSONObject("render").getJSONArray("targets"))
            .singleOrNull {GtkFixtureBinding.target(it.getJSONObject("key"))==target}?.optString("content_frame")?.toLongOrNull() ?: 0L
        override fun run() {
            if(done || active!==this) return
            try {
                check(SystemClock.elapsedRealtime()-(if(stopRequested) stageBegan else began) < (if(stopRequested) 20000 else 40000)) {"FREEDOOM_CASE_TIMEOUT"}
                val handle=NativeSessionHost.currentHandle();val envelope=JSONObject(NativeBridge.nativeSnapshot(handle));check(envelope.getInt("code")==NativeBridge.ACCEPTED) {"FREEDOOM_RUNTIME_UNAVAILABLE"}
                val runtime=envelope.getJSONObject("runtime")
                if(stopRequested) {
                    check(handle==checkNotNull(owner).handle && runtime.getString("scene")==checkNotNull(owner).scene) {"FREEDOOM_OWNER_CHANGED"}
                    val adapter=NativeSessionHost.debugAdapterSnapshot()
                    if(FreedoomGuiBinding.stopped(runtime,adapter)) {
                        cleanup=true;finish(if(outcome=="running") "cancelled" else outcome,reason ?: "FREEDOOM_SESSION_STOP");return
                    }
                    publish();ui.postDelayed(this,50);return
                }
                val binding=GtkFixtureBinding.owner(runtime,handle);val input=runtime.getJSONObject("app").getJSONObject("input")
                if(owner==null) {
                    check(NativeSessionHost.debugImeCanRequestFocus() && !BuildHooks.imeProbeActive()) {"FREEDOOM_INPUT_UNAVAILABLE"}
                    foot=windows(runtime);check(foot.size==1) {"FREEDOOM_BASELINE_INVALID"}
                    val base=checkNotNull(GtkFixtureBinding.targetFor(runtime,foot.single())) {"FREEDOOM_BASELINE_INVALID"}
                    epoch=runtime.getLong("input_resets");trial=checkNotNull(NativeSessionHost.debugCaptureInput(checkNotNull(NativeSessionHost.inputTarget(base.json().toString())))) {"FREEDOOM_INPUT_UNAVAILABLE"}
                    check(input.getInt("pressed")==0 && input.getJSONObject("pointer").getInt("pressed")==0) {"FREEDOOM_INPUT_HELD"}
                    owner=binding;theme=runtime.getJSONObject("render").getLong("theme_revision");keys=input.getLong("delivered");buttons=input.getJSONObject("pointer").getLong("buttons")
                    previousCatalog=JSONObject(NativeBridge.nativeCatalogUi(handle)).optJSONObject("catalog")?.optString("request_id") ?: ""
                    NativeSessionHost.reloadLauncher(".config/eidolon/launcher.toml");transition("CATALOG")
                } else {
                    check(binding==owner && NativeSessionHost.debugInputCurrent(checkNotNull(trial))) {"FREEDOOM_INTENT_LOST"}
                    check(windows(runtime).containsAll(foot)) {"FREEDOOM_BASELINE_CHANGED"}
                    val extras=windows(runtime)-foot
                    if(phase=="LAUNCH_REQUESTED") check(extras.size<=1) {"FREEDOOM_OTHER_WINDOW"}
                    else if(phase=="WAIT_WINDOW") check(extras.all {id->GtkFixtureBinding.window(runtime,appId)?.getString("window")==id}) {"FREEDOOM_OTHER_WINDOW"}
                    else check((extras-setOf(window)).isEmpty()) {"FREEDOOM_OTHER_WINDOW"}
                    check(runtime.getJSONObject("render").getLong("theme_revision")==theme) {"FREEDOOM_THEME_CHANGED"}
                    if(phase!="RETIRING") check(runtime.getLong("input_resets")==epoch) {"FREEDOOM_EPOCH_CHANGED"}
                    for((_,code) in acks) check(code=="ACCEPTED") {"FREEDOOM_COMMAND_REJECTED"}
                    when(phase) {
                        "CATALOG" -> {
                            val catalog=JSONObject(NativeBridge.nativeCatalogUi(handle)).getJSONObject("catalog")
                            if(catalog.optString("session")==binding.session && catalog.optString("request_id")!=previousCatalog && !catalog.optBoolean("pending") && !catalog.isNull("reply")) {
                                val reply=catalog.getJSONObject("reply");check(reply.isNull("error") && !reply.getBoolean("builtin")) {"FREEDOOM_CATALOG_UNAVAILABLE"}
                                val available=rows(reply.getJSONArray("entries")).singleOrNull {it.getString("id")=="freedoom"}
                                check(available?.optBoolean("available")==true) {"FREEDOOM_CATALOG_UNAVAILABLE"}
                                revision=reply.getString("revision");check(revision.matches(Regex("[0-9a-f]{64}"))) {"FREEDOOM_CATALOG_UNAVAILABLE"}
                                launchDispatched=true
                                transition("LAUNCH_REQUESTED")
                                NativeSessionHost.launchRegistered(binding.session,revision,"freedoom") {id,code->ui.post {
                                    if(active===this && !done && phase=="LAUNCH_REQUESTED") {request=id;launchAccepted=code==NativeBridge.ACCEPTED;publish()}
                                }}
                            }
                        }
                        "LAUNCH_REQUESTED" -> if(request.isNotEmpty()) {
                            check(launchAccepted) {"FREEDOOM_LAUNCH_REJECTED"}
                            val pair=FreedoomGuiBinding.launch(runtime,request)
                            if(pair!=null) {launch=pair.first;appId=pair.second;transition("WAIT_WINDOW")}
                        }
                        "WAIT_WINDOW" -> {
                            check(FreedoomGuiBinding.receipt(runtime,request,launch,appId)==null) {"FREEDOOM_EXIT_BEFORE_FRAME"}
                            val main=GtkFixtureBinding.window(runtime,appId)
                            if(main!=null) {
                                window=main.getString("window");check(window !in foot) {"FREEDOOM_WINDOW_CHANGED"}
                                val key=GtkFixtureBinding.targetFor(runtime,window)
                                if(key!=null && NativeSessionHost.debugInputTarget(checkNotNull(trial),key.json())!=null) {
                                    target=key;content=content(runtime);record("mapped",runtime);click();transition("WAIT_FOCUS")
                                }
                            }
                        }
                        "WAIT_FOCUS" -> if(focused(runtime)) {
                            check(input.getLong("delivered")==keys && input.getJSONObject("pointer").getLong("buttons")==buttons+2 && input.getJSONObject("pointer").getInt("pressed")==0) {"FREEDOOM_OTHER_INPUT"}
                            token=input.getString("focus_token");record("focused",runtime)
                            if(name=="keys") {key(KeyEvent.KEYCODE_ESCAPE,1,runtime);content=content(runtime);transition("WAIT_KEY")}
                            else {seal();firstClose=close();record("first_close_queued",runtime);content=content(runtime);transition("FIRST_CLOSE")}
                        }
                        "WAIT_KEY" -> {
                            check(focused(runtime) && input.getString("focus_token")==token && input.getLong("delivered")<=keys+expectedKeys &&
                                input.getJSONObject("pointer").getLong("buttons")==buttons+2) {"FREEDOOM_OTHER_INPUT"}
                            if(input.getLong("delivered")==keys+expectedKeys && input.getInt("pressed")==0 && content(runtime)>content && SystemClock.elapsedRealtime()-stageBegan>=300) {
                                when(keyStage++) {
                                    0 -> {record("escape_delivered",runtime);key(KeyEvent.KEYCODE_DPAD_UP,103,runtime)}
                                    1 -> {record("up_delivered",runtime);key(KeyEvent.KEYCODE_ENTER,28,runtime)}
                                    else -> {record("enter_delivered",runtime);key(KeyEvent.KEYCODE_Y,21,runtime);seal();record("confirm_queued",runtime);transition("RETIRING")}
                                }
                                content=content(runtime);stageBegan=SystemClock.elapsedRealtime()
                            }
                        }
                        "FIRST_CLOSE" -> if(acks[firstClose]=="ACCEPTED" && SystemClock.elapsedRealtime()-stageBegan>=500) {
                            if(GtkFixtureBinding.window(runtime,appId)==null) {record("first_close_retired",runtime);transition("RETIRING")}
                            else if(GtkFixtureBinding.targetFor(runtime,window)==target && content(runtime)>content) {
                                firstClosePending=true;record("first_close_pending",runtime);secondClose=close();record("second_close_queued",runtime);transition("RETIRING")
                            }
                        }
                        "RETIRING" -> {
                            val receipt=FreedoomGuiBinding.receipt(runtime,request,launch,appId)
                            if(receipt!=null) {
                                check(FreedoomGuiBinding.natural(receipt)) {"FREEDOOM_NOT_NATURAL"}
                                if(name=="close") check(acks[firstClose]=="ACCEPTED" && (secondClose.isEmpty() || acks[secondClose]=="ACCEPTED")) {"FREEDOOM_CLOSE_ACK_MISSING"}
                                val key=checkNotNull(target)
                                if(GtkFixtureBinding.window(runtime,appId)==null && GtkFixtureBinding.retired(runtime,NativeSessionHost.debugAdapterSnapshot(),binding,setOf(key),foot)) {
                                    check(NativeSessionHost.debugFinishInput(checkNotNull(trial))) {"FREEDOOM_INTENT_LOST"};trial=null
                                    natural=true;cleanup=true;record("retired",runtime);finish("completed");return
                                }
                            }
                        }
                    }
                    publish()
                }
                ui.postDelayed(this,50)
            } catch(error:Exception) {
                val code=error.message?.takeIf {it.matches(Regex("FREEDOOM_[A-Z_]+"))} ?: "FREEDOOM_CASE_FAILED"
                finish("failed",code)
            }
        }
    }
}
