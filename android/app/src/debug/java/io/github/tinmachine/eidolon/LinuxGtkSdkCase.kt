package io.github.tinmachine.eidolon

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.Process
import android.os.SystemClock
import android.system.Os
import android.system.OsConstants
import com.meta.spatial.toolkit.getAbsoluteTransform
import java.io.File
import java.nio.file.Files
import java.nio.file.LinkOption
import java.security.MessageDigest
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicLong
import org.json.JSONObject

/** 固定Linux GTK input/dialog。通常input gateを開かずlease所有trialだけが合成入力する。 */
object LinuxGtkSdkCase {
    @Volatile private var active: Check? = null
    private val requests=AtomicLong(9_600_000)
    fun start(name:String, nonce: String, context: Context, adapter: SpatialPanelAdapter, completed: () -> Unit) {
        check(active == null)
        Check(name.removePrefix("linux-gtk-"),nonce,context.applicationContext,adapter,completed).also { active=it; it.start() }
    }
    fun stop(nonce: String) { active?.takeIf { it.nonce==nonce }?.let { it.cancelled=true; it.close() } }
    fun commandComplete(runtime: Long, request: String, code: String) {
        active?.let { check ->
            if (check.completions.size < 64) check.completions["$runtime/$request"]=code else check.eventOverflow=true
        }
    }
    private fun objects(array: org.json.JSONArray?) = if(array==null) emptyList() else (0 until array.length()).map { array.getJSONObject(it) }
    private fun same(a: JSONObject,b: JSONObject) = listOf("session","window","scene","generation").all {a.optString(it)==b.optString(it)}
    private class Check(val caseName:String,val nonce:String,val context:Context,val adapter:SpatialPanelAdapter,val completed:()->Unit):Runnable {
        val recoveryOnly=caseName.endsWith("-recover")
        val name=caseName.removeSuffix("-recover")
        private val ownerToken=Any()
        val ui=Handler(Looper.getMainLooper())
        val completions=ConcurrentHashMap<String,String>()
        @Volatile var eventOverflow=false
        var cancelled=false
        var starting=true
        var closing=false
        var released=false
        var handle=0L
        var scene=""
        var session=""
        var request=""
        var stage="preparing"
        var deadline=SystemClock.elapsedRealtime()+90000
        var base=""
        var profile:ConnectionProfileV2.Value?=null
        var originals:List<Pair<File,ByteArray?>> = emptyList()
        val appId="io.github.tinmachine.eidolon.gtkfixture.r${nonce.take(16).repeat(2)}"
        val dialogId="$appId.dialog"
        val source=0x600000000000000L+nonce.take(8).toLong(16)
        val inputRequests=linkedSetOf<String>()
        val steps=org.json.JSONArray()
        val targets=linkedMapOf<String,JSONObject>()
        val firstFrames=linkedMapOf<String,Long>()
        val lastFrames=linkedMapOf<String,Long>()
        val contentFrames=linkedMapOf<String,Long>()
        var trial:NativeSessionHost.DebugInputTrial?=null
        var preparation:NativeSessionHost.DebugSdkInputPreparation?=null
        var preparationPublished=0L
        var preparationReason=""
        var mainWindow=""
        var mainTarget:JSONObject?=null
        var currentTarget:JSONObject?=null
        var epoch=0L
        var token=""
        var baseKeys=0L
        var baseButtons=0L
        var progress:GtkInputProgress?=null
        var inputPhase="WAIT_MAIN"
        var rowSequence=0L
        var verified=false
        var result=JSONObject().put("case","linux-gtk-$caseName").put("fixture_revision","linux-gtk-sdk-v1").put("nonce",nonce)
            .put("result","running").put("apk_uid",Process.myUid()).put("virtual_frame_clock",false).put("normal_gui_enabled",false)
            .put("physical_hmd",false).put("physical_input",false).put("remote_cleanup_confirmed",false)
            .put("synthetic_input",!recoveryOnly).put("recovery_only",recoveryOnly).put("ime_verified",false).put("steps",steps)
        fun publish() { NativeSessionHost.publishCheck(result.put("stage",stage)) }
        fun start() {
            publish()
            NativeSessionHost.debugBeginSdkProbe(ownerToken,{ normal ->
                base=normal
                val config=JSONObject(normal)
                originals=listOf(File(context.noBackupFilesDir,"credentials/profile.json"),File(context.noBackupFilesDir,"workspace/layout.json"),
                    File(context.noBackupFilesDir,"themes/state.bin"),File(config.getJSONObject("paths").getString("files_dir"),"transport-state/transport-pending.json")).map {it to digest(it)}
                val selected=DebugProbeProfileStore(context).importConfirmed(config.getString("installation"));profile=selected
                val pins=DebugLinuxGtkPinStore(context).importConfirmed(selected)
                val files=File(context.noBackupFilesDir,"lx")
                if(!files.exists()) Os.mkdir(files.absolutePath,448)
                Os.lstat(files.absolutePath).let {check(OsConstants.S_ISDIR(it.st_mode) && it.st_uid==Process.myUid() && it.st_mode and 511==448) {"PROBE_DIRECTORY_INVALID"}}
                config.getJSONObject("paths").put("files_dir",files.absolutePath)
                // 無永続preferencesならdefault Off。通常のtheme/layout storeへ一切書き込まない。
                config.remove("preferences_dir");config.put("profiles",org.json.JSONArray())
                config.put("debug_probe_profile_v2",ConnectionProfileV2Codec.encode(selected).toString(Charsets.UTF_8)).put("debug_linux_gtk_pins",pins.nativeJson())
                NativeSessionHost.DebugSdkProbeConfig(config.toString(),selected,"linux_gtk_$name")
            }) { receipt ->
                if(active!==this) return@debugBeginSdkProbe
                starting=false
                handle=receipt.runtime;request=receipt.connectRequest ?: ""
                result.put("runtime_adopted",receipt.phase==DebugSdkRuntimeLease.Phase.ACTIVE)
                scene=NativeSessionHost.currentSceneEpoch().toString()
                if(receipt.error!=null && NativeSessionHost.debugSdkProbeStatus(ownerToken)==null) {
                    result.put("result","fail").put("reason",receipt.error);stage="rejected";publish();active=null;completed()
                } else if(receipt.error!=null) fail(receipt.error)
                else if(cancelled) close()
                else { stage="connecting";publish();ui.post(this) }
            }
        }
        fun fail(reason:String) {
            result.put("reason",reason.takeIf {it.matches(Regex("PROBE_[A-Z_]+"))} ?: "PROBE_FAILED")
            try {
                val value=JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("runtime")
                val app=value.optJSONObject("app");val transport=value.getJSONObject("transport");val forward=transport.optJSONObject("linux_forward");val recovery=transport.optJSONObject("linux_recovery")
                result.put("failure_metadata",JSONObject().put("state",value.optString("state")).put("app_state",app?.optString("state"))
                    .put("app_error",app?.opt("error")).put("source_hashes",app?.opt("verified_hashes")).put("callbacks",app?.opt("callbacks"))
                    .put("transport_state",transport.optString("state")).put("transport_error",transport.opt("error"))
                    .put("linux_state",forward?.optString("state")).put("linux_error",forward?.opt("error")).put("journal_pending",forward?.opt("journal_pending"))
                    .put("recovery_error",recovery?.opt("error")).put("recovery_pending",recovery?.opt("pending")).put("recovery_queried",recovery?.opt("queried"))
                    .put("render_error",value.getJSONObject("render").opt("error")).put("sdk_bindings",value.getJSONObject("shutdown").optInt("sdk_bindings"))
                    .put("input_progress",result.opt("input_progress") ?: JSONObject.NULL)
                    .put("input_target_continuity",trial?.let {NativeSessionHost.debugInputContinuityStatus(it)} ?: JSONObject.NULL))
            } catch(_:Exception) { }
            close()
        }
        fun command(type:String,nonce:String?) {
            stage="enqueue_$type"
            NativeSessionHost.debugSdkProbeCommand(ownerToken,type,session,nonce,if(type in setOf("linux_gtk_probe","linux_gtk_recover")) name else null) { receipt ->
                if(active!==this || closing) return@debugSdkProbeCommand
                if(receipt.runtime!=handle || receipt.code!=NativeBridge.ACCEPTED || receipt.request==null) fail("PROBE_ENQUEUE_REJECTED")
                else {request=receipt.request;stage=when(type) {"disconnect"->"disconnecting";"linux_gtk_recover"->"recovering";else->"gtk"};publish()}
            }
        }
        fun accepted() = completions["$handle/$request"]?.also {check(it=="ACCEPTED") {"PROBE_COMMAND_REJECTED"}}=="ACCEPTED"
        fun record(label:String,s:JSONObject) {
            check(steps.length()<24) {"PROBE_STEP_BOUND"}
            steps.put(JSONObject().put("stage",label).put("input_epoch",s.getLong("input_resets"))
                .put("target",currentTarget ?: JSONObject.NULL).put("gtk_sequence",rowSequence))
            result.put("input_phase",inputPhase);publish()
        }
        fun observeSdk(s:JSONObject) {
            check(s.getJSONObject("render").isNull("theme_id") && !s.getJSONObject("theme").getBoolean("loading")) {"PROBE_DECORATION_ENABLED"}
            for(plan in objects(s.getJSONArray("panels")).filter {it.optString("action")=="create" && it.isNull("error") && it.getJSONObject("target").optString("session")==session}) {
                val key=plan.getJSONObject("target");val window=key.getString("window")
                val row=objects(s.getJSONObject("app").getJSONArray("windows")).singleOrNull {it.optString("window")==window && it.optString("life")=="Mapped"} ?: continue
                check(row.optString("app_id") in (if(name=="dialog") setOf(appId,dialogId) else setOf(appId))) {"PROBE_OTHER_WINDOW"}
                check(key.getString("scene")==scene && (targets[window]==null || same(checkNotNull(targets[window]),key))) {"PROBE_TARGET_CHANGED"}
                val draw=objects(s.getJSONObject("render").getJSONArray("targets")).singleOrNull {same(it.getJSONObject("key"),key)} ?: continue
                val sdk=objects(adapter.metrics().getJSONArray("targets")).singleOrNull {it.optString("role")=="windows" && it.optString("runtime")==handle.toString() && same(it.getJSONObject("target"),key)} ?: continue
                val placement=objects(s.getJSONObject("workspace").getJSONArray("windows")).singleOrNull {it.optString("session")==session && it.optString("window")==window}?.getJSONObject("placement") ?: continue
                val applied=placement.optJSONObject("applied_pose") ?: continue
                val entity=adapter.windowEntity(window) ?: continue
                val pose=PanelPoseCodec.fromSdk(getAbsoluteTransform(entity)) ?: continue
                if(!plan.getBoolean("applied") || !same(applied.getJSONObject("target"),key) || !PanelPoseCodec.near(pose,applied.getJSONObject("pose"),.001) ||
                    draw.getLong("frames")<=0 || draw.isNull("content_frame")) continue
                check(plan.isNull("decoration") && sdk.isNull("decoration") && draw.isNull("canvas")) {"PROBE_DECORATION_ENABLED"}
                val frame=draw.getLong("frames");check(frame>=(lastFrames[window] ?: 0)) {"PROBE_DRAW_REGRESSED"}
                if(targets[window]==null) {check(targets.size<2) {"PROBE_OTHER_WINDOW"};targets[window]=JSONObject(key.toString());firstFrames[window]=frame}
                lastFrames[window]=frame;val content=draw.getString("content_frame").toLong()
                check(content>=(contentFrames[window] ?: 0)) {"PROBE_DRAW_REGRESSED"};contentFrames[window]=content
            }
            result.put("draw_evidence",org.json.JSONArray(targets.map {(window,key)->JSONObject().put("target",key)
                .put("first_frames",firstFrames[window]).put("last_frames",lastFrames[window]).put("content_frame",contentFrames[window]?.toString())}))
            result.put("sdk_targets",org.json.JSONArray(targets.values.toList())).put("sdk_pose_applied",targets.isNotEmpty())
        }
        fun currentRow(s:JSONObject):JSONObject? {
            val forward=s.getJSONObject("transport").optJSONObject("linux_forward") ?: return null
            if(forward.optString("session")!=session || forward.optString("nonce")!=nonce.take(16)) return null
            val gtk=forward.optJSONObject("gtk") ?: return null
            check(gtk.getString("case")==name) {"PROBE_TARGET_CHANGED"}
            val row=gtk.optJSONObject("last") ?: return null
            check(gtk.getBoolean("process_bound") && row.getString("run_id")==nonce.take(16).repeat(2) && row.getString("case")==name &&
                row.getString("app_id")==appId && row.getString("dialog_app_id")==dialogId && !row.getBoolean("host_selftest") && !row.getBoolean("physical_input_verified")) {"PROBE_COUNTER_SCOPE"}
            val sequence=row.getLong("sequence");check(sequence>=rowSequence) {"PROBE_COUNTER_REGRESSED"};rowSequence=sequence
            return row
        }
        fun target(s:JSONObject,app:String):JSONObject? {
            val window=GtkFixtureBinding.window(s,app)?.getString("window") ?: return null
            return GtkFixtureBinding.targetFor(s,window)?.json()?.takeIf {targets[window]?.let {saved->same(saved,it)}==true}
        }
        fun send(type:String,key:JSONObject,fields:JSONObject=JSONObject(),focus:String?=null,down:Boolean=false):String {
            val captured=checkNotNull(trial) {"PROBE_INPUT_OWNER_LOST"}
            val candidate=NativeSessionHost.debugInputTarget(captured,key)
            result.put("input_target_continuity",NativeSessionHost.debugInputContinuityStatus(captured) ?: JSONObject.NULL)
            val ticket=checkNotNull(candidate) {"PROBE_INPUT_TARGET_LOST"}
            check(ticket.runtime==handle && ticket.epoch==epoch) {"PROBE_INPUT_EPOCH_CHANGED"}
            val id=requests.incrementAndGet().toString();check(inputRequests.size<24) {"PROBE_INPUT_BOUND"};inputRequests.add(id)
            val envelope=JSONObject().put("schema",1).put("type",type).put("request_id",id).put("target",key)
            for(field in fields.keys().asSequence()) envelope.put(field,fields.get(field))
            val accepted=NativeSessionHost.debugInputDispatch(captured,ticket,focus,down) {NativeBridge.nativeCommand(handle,envelope.toString())}
            result.put("input_target_continuity",NativeSessionHost.debugInputContinuityStatus(captured) ?: JSONObject.NULL)
            check(accepted) {"PROBE_INPUT_REJECTED"}
            return id
        }
        fun allInputAccepted():Boolean = inputRequests.all {id->completions["$handle/$id"]?.let {check(it=="ACCEPTED") {"PROBE_INPUT_REJECTED"};true} ?: false}
        fun click(s:JSONObject,key:JSONObject,u:Double,v:Double) {
            check(s.getLong("input_resets")==epoch && u>0 && u<1 && v>0 && v<1) {"PROBE_GEOMETRY_INVALID"}
            currentTarget=key
            for(state in listOf(1,0)) send("pointer",key,JSONObject().put("input_epoch",epoch).put("pointer",JSONObject()
                .put("source",source.toString()).put("uv",org.json.JSONArray(listOf(u,v)))
                .put("event",JSONObject().put("type","buttons").put("changed",1).put("state",state)).put("time_ms",SystemClock.uptimeMillis() and 0xffffffffL)),down=state==1)
        }
        fun clickRect(s:JSONObject,key:JSONObject,row:JSONObject,field:String) {
            val size=row.getJSONArray("logical_size");val rect=row.getJSONArray(field)
            val window=checkNotNull(GtkFixtureBinding.window(s,appId));val logical=window.getJSONObject("logical_size")
            val w=size.getInt(0);val h=size.getInt(1);val x=rect.getInt(0);val y=rect.getInt(1);val rw=rect.getInt(2);val rh=rect.getInt(3)
            check(w==logical.getInt("width") && h==logical.getInt("height") && w in 1..4096 && h in 1..4096 && x>=0 && y>=0 && rw>0 && rh>0 && x+rw<=w && y+rh<=h) {"PROBE_GEOMETRY_INVALID"}
            click(s,key,(x+rw/2.0)/w,(y+rh/2.0)/h)
        }
        fun focused(s:JSONObject,key:JSONObject):Boolean {
            val input=s.getJSONObject("app").getJSONObject("input")
            return s.getLong("input_resets")==epoch && !input.isNull("focus_token") && NativeSessionHost.debugInputFocusMatches(checkNotNull(trial),key,input.getString("focus_token"))
        }
        fun key(s:JSONObject,target:JSONObject,code:Int,scan:Int,down:Boolean) {
            check(focused(s,target) && s.getJSONObject("app").getJSONObject("input").getString("focus_token")==token) {"PROBE_FOCUS_CHANGED"}
            send("key",target,JSONObject().put("input_epoch",epoch).put("focus_token",token).put("key",JSONObject().put("device",Int.MAX_VALUE-26)
                .put("scan_code",scan).put("key_code",code).put("action",if(down) "pressed" else "released").put("repeat",0).put("meta",0)
                .put("time_ms",SystemClock.uptimeMillis() and 0xffffffffL)),focus=token)
        }
        fun seal(retiring:JSONObject?=null) {
            check(NativeSessionHost.debugSealInput(checkNotNull(trial))) {"PROBE_INPUT_OWNER_LOST"}
            if(retiring!=null) check(NativeSessionHost.debugExpectRetirement(checkNotNull(trial),retiring)) {"PROBE_INPUT_OWNER_LOST"}
        }
        fun closeMain(s:JSONObject) {
            check(allInputAccepted()) {"PROBE_INPUT_ACK_PENDING"}
            val key=checkNotNull(mainTarget);check(target(s,appId)?.let {same(it,key)}==true) {"PROBE_TARGET_CHANGED"}
            val id=requests.incrementAndGet().toString();inputRequests.add(id)
            check(NativeSessionHost.debugCloseInputTarget(checkNotNull(trial),key) {
                NativeBridge.nativeCommand(handle,JSONObject().put("schema",1).put("type","close_window_target").put("request_id",id).put("target",key)
                    .put("controls_revision",s.getJSONObject("render").getLong("theme_revision")).toString())
            }) {"PROBE_CLOSE_REJECTED"}
            inputPhase="WAIT_RETIRED";record("close_requested",s)
        }
        fun input(s:JSONObject,row:JSONObject?) {
            val nativeInput=s.getJSONObject("app").getJSONObject("input");val pointer=nativeInput.getJSONObject("pointer")
            val counters=progress?.let {gate->
                result.put("input_progress",GtkInputDiagnostics.observe(gate,inputPhase,SystemClock.elapsedRealtime(),s,epoch,
                    currentTarget?.optString("window"),token.takeIf {it.isNotEmpty()}))
                check(gate.state!=GtkInputProgress.State.REJECTED) {"PROBE_INPUT_PROGRESS_${gate.reason}"}
                gate.state==GtkInputProgress.State.COMPLETE
            } ?: false
            if(trial!=null) check(NativeSessionHost.debugInputCurrent(checkNotNull(trial))) {"PROBE_INPUT_OWNER_LOST"}
            if(inputPhase !in setOf("WAIT_MAIN","WAIT_DIALOG_CLOSED","WAIT_RETIRED")) check(s.getLong("input_resets")==epoch) {"PROBE_INPUT_EPOCH_CHANGED"}
            when(inputPhase) {
                "WAIT_MAIN" -> {
                    // 準備開始後はproducer frameの一時的先行でtargetForを失敗させない。
                    // 固定targetの構造/owner喪失はHostの同monitor内で永久取消し、frameだけを待つ。
                    val key=if(preparation==null) target(s,appId) else mainTarget
                    if(key==null || row==null || row.getString("phase") !in setOf("ready","state")) {
                        check(preparation==null) {"PROBE_INPUT_OWNER_CHANGED"};return
                    }
                    check(targets.size==1 && nativeInput.getInt("pressed")==0 && pointer.getInt("pressed")==0) {"PROBE_INPUT_HELD"}
                    if(preparation==null) {
                        mainWindow=key.getString("window");mainTarget=JSONObject(key.toString());currentTarget=key
                        epoch=s.getLong("input_resets");baseKeys=nativeInput.getLong("delivered");baseButtons=pointer.getLong("buttons")
                        preparation=checkNotNull(NativeSessionHost.debugPrepareSdkInput(ownerToken,key,epoch,baseKeys,baseButtons)) {"PROBE_INPUT_BASELINE_INVALID"}
                    }
                    val polled=NativeSessionHost.debugPollSdkInput(checkNotNull(preparation))
                    result.put("input_preparation",polled.metadata)
                    val reason=polled.metadata.getString("reason");val now=SystemClock.elapsedRealtime()
                    if(reason!=preparationReason || now-preparationPublished>=250 || polled.trial!=null) {
                        preparationReason=reason;preparationPublished=now;publish()
                    }
                    check(polled.metadata.getString("state")!="cancelled") {reason}
                    trial=polled.trial ?: return
                    check(checkNotNull(trial).epoch==epoch) {"PROBE_INPUT_EPOCH_CHANGED"}
                    check(s.getLong("input_resets")==epoch) {"PROBE_INPUT_EPOCH_CHANGED"};record("captured",s)
                    progress=GtkInputProgress(baseKeys,baseButtons,name=="dialog")
                    clickRect(s,key,row,if(name=="input") "entry_rect" else "button_rect")
                    inputPhase=if(name=="input") "WAIT_FOCUS" else "WAIT_DIALOG"
                }
                "WAIT_FOCUS" -> if(counters && focused(s,checkNotNull(mainTarget)) && allInputAccepted()) {
                    token=nativeInput.getString("focus_token");val targetKey=checkNotNull(mainTarget)
                    key(s,targetKey,android.view.KeyEvent.KEYCODE_A,30,true);key(s,targetKey,android.view.KeyEvent.KEYCODE_A,30,false)
                    key(s,targetKey,android.view.KeyEvent.KEYCODE_DEL,14,true);key(s,targetKey,android.view.KeyEvent.KEYCODE_DEL,14,false)
                    inputPhase="WAIT_KEYS";record("keys_enqueued",s)
                }
                "WAIT_KEYS" -> if(counters && row!=null && row.getBoolean("token_seen") && row.getBoolean("delete_seen") && row.getInt("entry_changes")==2 && allInputAccepted()) {
                    check(focused(s,checkNotNull(mainTarget)) && nativeInput.getString("focus_token")==token) {"PROBE_FOCUS_CHANGED"}
                    clickRect(s,checkNotNull(mainTarget),row,"button_rect");seal();inputPhase="WAIT_CLICK";record("button_enqueued",s)
                }
                "WAIT_CLICK" -> if(counters && row?.optBoolean("complete")==true && allInputAccepted()) {
                    if(target(s,appId)!=null) closeMain(s)
                }
                "WAIT_DIALOG" -> {
                    val key=target(s,dialogId) ?: return
                    if(!counters || row?.optBoolean("dialog_parent_set")!=true || !allInputAccepted()) return
                    check(row.getInt("dialog_opened")==1) {"PROBE_OTHER_INPUT"}
                    result.put("dialog_parent_reported",true).put("native_parent_verified",false)
                    click(s,key,.5,.3);inputPhase="WAIT_DIALOG_FOCUS";record("dialog_visible",s)
                }
                "WAIT_DIALOG_FOCUS" -> if(counters && focused(s,checkNotNull(currentTarget)) && allInputAccepted()) {
                    token=nativeInput.getString("focus_token");val targetKey=checkNotNull(currentTarget)
                    key(s,targetKey,android.view.KeyEvent.KEYCODE_ESCAPE,1,true);key(s,targetKey,android.view.KeyEvent.KEYCODE_ESCAPE,1,false)
                    seal(targetKey);inputPhase="WAIT_DIALOG_CLOSED";record("dialog_escape",s)
                }
                "WAIT_DIALOG_CLOSED" -> if(counters && row?.optBoolean("complete")==true && GtkFixtureBinding.window(s,dialogId)==null && allInputAccepted()) {
                    check(nativeInput.getInt("pressed")==0 && pointer.getInt("pressed")==0) {"PROBE_INPUT_HELD"};if(target(s,appId)!=null) {record("dialog_closed",s);closeMain(s)}
                }
            }
            result.put("input_phase",inputPhase)
        }
        override fun run() {
            if(active!==this || closing) return
            try {
                check(!cancelled) {"PROBE_CANCELLED"};check(!eventOverflow) {"PROBE_EVENT_BOUND"}
                check(SystemClock.elapsedRealtime()<deadline) {"PROBE_TIMEOUT"}
                check(NativeSessionHost.currentHandle()==handle && NativeSessionHost.currentSceneEpoch().toString()==scene) {"PROBE_OWNER_CHANGED"}
                val owner=checkNotNull(NativeSessionHost.debugSdkProbeStatus(ownerToken));check(owner.error==null) {"PROBE_CREDENTIAL_REJECTED"}
                val s=JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("runtime")
                check(s.getString("state")=="Ready") {"PROBE_RUNTIME_FAILED"}
                val transport=s.getJSONObject("transport")
                check(transport.getString("state") !in setOf("Failed","CleanupPending","Stopped")) {"PROBE_SSH_FAILED"}
                val visible=s.getBoolean("activity_visible") && s.getString("xr_state")=="FOCUSED" && s.optString("scene")==scene
                if(stage=="connecting" && !visible) {ui.postDelayed(this,16);return}
                check(visible) {"PROBE_VISIBILITY_LOST"}
                if(stage=="connecting" && accepted() && transport.getString("state")=="ProbeReady") {
                    val selected=checkNotNull(profile);check(transport.getString("profile_id")==selected.profileId && transport.getString("profile_revision")==selected.revision) {"PROBE_TARGET_CHANGED"}
                    val probe=transport.getJSONObject("probe");check(probe.getBoolean("token_matched") && probe.getBoolean("eof") && probe.getInt("exit_status")==0 && !probe.getBoolean("exit_signal")) {"PROBE_SSH_INVALID"}
                    session=transport.getString("session");result.put("probe_ready",true).put("remote_uid",probe.getInt("uid"));command(if(recoveryOnly) "linux_gtk_recover" else "linux_gtk_probe",nonce.take(16))
                } else if(stage=="recovering" && accepted()) {
                    val recovery=transport.optJSONObject("linux_recovery")
                    if(recovery!=null) {
                        check(transport.getString("state")=="ProbeReady" && transport.getString("session")==session && recovery.getString("session")==session &&
                            recovery.getString("nonce")==nonce.take(16) && recovery.getString("gtk_case")==name && recovery.getInt("uid")==result.getInt("remote_uid") && recovery.isNull("error")) {"PROBE_RECOVERY_FAILED"}
                        val outcome=LinuxRecoveryOutcome.classify(recovery.getBoolean("queried"),recovery.getBoolean("pending"),recovery.getBoolean("receipt_verified"),recovery.getBoolean("channel_closed"))
                        check(outcome!=LinuxRecoveryOutcome.INVALID) {"PROBE_RECOVERY_REPORT_INVALID"}
                        result.put("recovery_queried",recovery.getBoolean("queried")).put("receipt_verified",recovery.getBoolean("receipt_verified"))
                            .put("origin_session",recovery.opt("origin_session")).put("remote_cleanup_confirmed",outcome==LinuxRecoveryOutcome.VERIFIED)
                            .put("journal_cleared",true).put("recovery_not_required",outcome==LinuxRecoveryOutcome.NOT_REQUIRED)
                            .put("remote_gui_verified",false).put("sdk_successful_draw_observed",false)
                        verified=outcome==LinuxRecoveryOutcome.VERIFIED;command("disconnect",null)
                    }
                } else if(stage=="gtk") {
                    check(transport.getString("session")==session && transport.getString("state")=="ProbeReady") {"PROBE_TARGET_CHANGED"}
                    if(!accepted()) {ui.postDelayed(this,16);return}
                    val app=s.optJSONObject("app")
                    // enqueue receiptよりowner snapshot反映が一tick遅れる場合だけdeadline内で待つ。
                    if(app==null) {ui.postDelayed(this,16);return}
                    check(app.getString("session")==session && app.getString("nonce")==nonce.take(16) && app.getString("mode")=="linux_gtk_$name") {"PROBE_TARGET_CHANGED"}
                    val forward=transport.optJSONObject("linux_forward")
                    check((forward==null || forward.isNull("error")) && app.isNull("error")) {"PROBE_SHM_FAILED"}
                    observeSdk(s)
                    val row=currentRow(s)
                    input(s,row)
                    if(accepted() && forward?.optString("state")=="pass" && app.optString("state")=="pass") {
                        check(forward.getString("session")==session && forward.getString("nonce")==nonce.take(16) && forward.getInt("uid")==result.getInt("remote_uid") &&
                            app.getString("session")==session && app.getString("nonce")==nonce.take(16)) {"PROBE_TARGET_CHANGED"}
                        check(app.getString("callback_clock")=="target-driven" && app.getString("mode")=="linux_gtk_$name" && app.getInt("verified_hashes")==0 && app.getBoolean("remote_gui_verified") &&
                            listOf("copies","callbacks","releases").all {app.getLong(it)>0L}) {"PROBE_PIXEL_REPORT_INVALID"}
                        val gtk=forward.getJSONObject("gtk")
                        check(listOf("tools_verified","registered","forward_cancelled","remote_cleaned","channel_closed").all {forward.getBoolean(it)} &&
                            !forward.getBoolean("pattern_verified") && !forward.getBoolean("journal_pending") && forward.getInt("frames")==0 && gtk.getString("case")==name &&
                            gtk.getBoolean("helper_verified") && gtk.getBoolean("process_bound") && gtk.isNull("report_error") && !gtk.getBoolean("forced_stop")) {"PROBE_REMOTE_REPORT_INVALID"}
                        check(inputPhase=="WAIT_RETIRED" && targets.size==(if(name=="dialog") 2 else 1) && lastFrames[mainWindow]!!>firstFrames[mainWindow]!! && allInputAccepted()) {"PROBE_SDK_DRAW_MISSING"}
                        val owned=targets.values.map {GtkFixtureBinding.target(it)}.toSet()
                        if(!GtkFixtureBinding.retired(s,adapter.metrics(),handle,scene,owned,emptySet())) {ui.postDelayed(this,16);return}
                        check(NativeSessionHost.debugFinishInput(checkNotNull(trial))) {"PROBE_INPUT_OWNER_LOST"}
                        result.put("remote_gui_verified",true).put("target_driven_callbacks",app.getLong("callbacks")).put("shm_copies",app.getLong("copies"))
                            .put("sdk_successful_draw_observed",true).put("gtk_counter_reports",gtk.getInt("reports")).put("input_requests_accepted",inputRequests.size)
                            .put("remote_cleanup_confirmed",true).put("journal_cleared",true).put("targets_retired",true)
                        verified=true;command("disconnect",null)
                    }
                } else if(stage=="disconnecting" && accepted() && transport.getString("state")=="Disconnected" && transport.getInt("owned_connections")==0) {
                    if(recoveryOnly) {
                        check(s.isNull("app") && s.getJSONObject("window_slots").getInt("occupied")==0 && objects(adapter.metrics().getJSONArray("targets")).none {it.optString("role")=="windows"}) {"PROBE_RECOVERY_CREATED_GUI"}
                        check(JSONObject(NativeBridge.nativeCredentialScope(handle)).isNull("scope")) {"PROBE_SCOPE_RETAINED"}
                        result.put("disconnected",true).put("scope_cleared",true).put("no_gui_session_created",true);close();return
                    }
                    val app=s.getJSONObject("app")
                    if(app.getString("state")=="stopped" && app.getBoolean("directory_cleaned") &&
                        listOf("clients","objects","surfaces","pixel_bytes","pool_bytes","pool_count","memfd_fds_after","memfd_maps_after").all {!app.isNull(it) && app.getLong(it)==0L} &&
                        s.getJSONObject("window_slots").getInt("occupied")==0 && s.getJSONObject("shutdown").getInt("sdk_bindings")==0 &&
                        objects(adapter.metrics().getJSONArray("targets")).none {it.optString("role")=="windows"}) {
                        check(JSONObject(NativeBridge.nativeCredentialScope(handle)).isNull("scope")) {"PROBE_SCOPE_RETAINED"}
                        result.put("disconnected",true).put("local_native_sdk_cleaned",true).put("scope_cleared",true);close();return
                    }
                }
                ui.postDelayed(this,16)
            } catch(failure:Exception) {fail(failure.message ?: "PROBE_FAILED")}
        }
        fun close() {
            if(starting || closing || released) return
            closing=true;stage="restoring";publish()
            trial?.let {NativeSessionHost.debugReleaseInput(it)}
            NativeSessionHost.debugEndSdkProbe(ownerToken,{
                // normal stop自体の失敗ならprepareは未実行で、private設定を適用していない。
                base.isEmpty() || (NativeSessionHost.runtimeConfigJson()==base && originals.all {(file,old)->
                    val current=digest(file);if(old==null || current==null) old==null && current==null else old.contentEquals(current)
                })
            }) { receipt ->
                if(receipt.error!=null) {
                    closing=false;stage="cleanup_unconfirmed";result.put("result","cleanup_unconfirmed").put("cleanup_reason",receipt.error);publish();return@debugEndSdkProbe
                }
                try {
                    check(receipt.phase==DebugSdkRuntimeLease.Phase.RESTORING && receipt.runtime>0 && receipt.runtime!=handle) {"PROBE_RESTORE_FAILED"}
                    val old=JSONObject(NativeBridge.nativeSnapshot(handle));check(handle==0L || old.getInt("code")==NativeBridge.STALE) {"PROBE_OLD_RUNTIME_RETAINED"}
                    if(recoveryOnly) result.put("local_native_sdk_cleaned",true)
                    result.put("normal_runtime_restored",true).put("product_settings_untouched",base.isNotEmpty() && originals.size==4)
                        .put("old_runtime_destroyed",handle>0L).put("probe_had_no_owner",handle==0L)
                        .put("result",when {
                            cancelled->"cancelled"
                            result.has("reason") || !result.optBoolean("local_native_sdk_cleaned")->"fail"
                            recoveryOnly && result.optBoolean("recovery_not_required")->"not_required"
                            verified->"pass"
                            else->"fail"
                        })
                } catch(failure:Exception) {result.put("result","fail").put("restore_reason",failure.message?.takeIf {it.matches(Regex("PROBE_[A-Z_]+"))} ?: "PROBE_RESTORE_FAILED")}
                waitForNormalSdk(receipt.runtime)
            }
        }
        private fun waitForNormalSdk(normal:Long) {
            stage="restoring_sdk"
            val finalResult=result.getString("result");result.put("result","running");publish()
            val restoreDeadline=SystemClock.elapsedRealtime()+15000
            val restoreScene=NativeSessionHost.currentSceneEpoch().toString()
            ui.post(object:Runnable {
                override fun run() {
                    try {
                        check(SystemClock.elapsedRealtime()<restoreDeadline && NativeSessionHost.currentHandle()==normal &&
                            NativeSessionHost.currentSceneEpoch().toString()==restoreScene) {"PROBE_RESTORE_FAILED"}
                        val value=JSONObject(NativeBridge.nativeSnapshot(normal)).getJSONObject("runtime")
                        check(value.getString("state")!="Failed") {"PROBE_RESTORE_FAILED"}
                        val targets=objects(value.getJSONObject("render").getJSONArray("targets"))
                        val sdk=objects(adapter.metrics().getJSONArray("targets"))
                        if(value.getString("state")=="Ready" && value.getJSONObject("transport").getString("state")=="Idle" &&
                            value.getBoolean("activity_visible") && value.getString("xr_state")=="FOCUSED" && value.optString("scene")==restoreScene && targets.size==1 &&
                            targets.single().getString("role")=="diagnostic" && targets.single().getLong("frames")>=3 &&
                            sdk.size==1 && sdk.single().getString("role")=="diagnostic" && same(sdk.single().getJSONObject("target"),targets.single().getJSONObject("key"))) {
                            NativeSessionHost.debugFinalizeSdkProbe(ownerToken,normal,restoreScene,targets.single().getJSONObject("key")) { finalized ->
                                if(finalized.error!=null || finalized.phase!=DebugSdkRuntimeLease.Phase.IDLE) {
                                    closing=false;stage="cleanup_unconfirmed";result.put("result","cleanup_unconfirmed").put("cleanup_reason",finalized.error ?: "PROBE_RESTORE_FAILED");publish()
                                } else {
                                    released=true;result.put("normal_sdk_restored",true).put("owner_lease_released",true).put("result",if(cancelled) "cancelled" else finalResult)
                                    stage="finished";publish();active=null;completed()
                                }
                            }
                            return
                        }
                        ui.postDelayed(this,32)
                    } catch(_:Exception) {
                        closing=false;stage="cleanup_unconfirmed";result.put("result","cleanup_unconfirmed").put("restore_reason","PROBE_RESTORE_FAILED");publish()
                    }
                }
            })
        }
        private fun digest(file:File):ByteArray? {
            if(!Files.exists(file.toPath(),LinkOption.NOFOLLOW_LINKS)) return null
            check(Files.isRegularFile(file.toPath(),LinkOption.NOFOLLOW_LINKS) && file.length() in 1..(16*1024*1024)) {"PROBE_OLD_STATE_INVALID"}
            return MessageDigest.getInstance("SHA-256").digest(file.readBytes())
        }
    }
}
