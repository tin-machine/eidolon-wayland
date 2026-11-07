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
import org.json.JSONObject

/** B4 virtual clockとは別の固定SDK検査。入力/任意GUI/通常profile切替を許可しない。 */
object LinuxShmSdkCase {
    @Volatile private var active: Check? = null
    fun start(nonce: String, context: Context, adapter: SpatialPanelAdapter, completed: () -> Unit) {
        check(active == null)
        Check(nonce,context.applicationContext,adapter,completed).also { active=it; it.start() }
    }
    fun stop(nonce: String) { active?.takeIf { it.nonce==nonce }?.let { it.cancelled=true; it.close() } }
    fun commandComplete(runtime: Long, request: String, code: String) {
        active?.let { check ->
            if (check.completions.size < 64) check.completions["$runtime/$request"]=code else check.eventOverflow=true
        }
    }
    private fun objects(array: org.json.JSONArray?) = if(array==null) emptyList() else (0 until array.length()).map { array.getJSONObject(it) }
    private fun same(a: JSONObject,b: JSONObject) = listOf("session","window","scene","generation").all {a.optString(it)==b.optString(it)}
    private class Check(val nonce:String,val context:Context,val adapter:SpatialPanelAdapter,val completed:()->Unit):Runnable {
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
        var target:JSONObject?=null
        var firstFrame=0L
        var lastFrame=0L
        var observedContent=0L
        var sdkObserved=false
        var verified=false
        var result=JSONObject().put("case","linux-shm-sdk").put("fixture_revision","linux-shm-sdk-v1").put("nonce",nonce)
            .put("result","running").put("apk_uid",Process.myUid()).put("virtual_frame_clock",false).put("normal_gui_enabled",false)
            .put("physical_hmd",false).put("physical_input",false).put("remote_cleanup_confirmed",false)
        fun publish() { NativeSessionHost.publishCheck(result.put("stage",stage)) }
        fun start() {
            publish()
            NativeSessionHost.debugBeginSdkProbe(ownerToken,{ normal ->
                base=normal
                val config=JSONObject(normal)
                originals=listOf(File(context.noBackupFilesDir,"credentials/profile.json"),File(context.noBackupFilesDir,"workspace/layout.json"),
                    File(context.noBackupFilesDir,"themes/state.bin"),File(config.getJSONObject("paths").getString("files_dir"),"transport-state/transport-pending.json")).map {it to digest(it)}
                val selected=DebugProbeProfileStore(context).importConfirmed(config.getString("installation"));profile=selected
                val pins=DebugLinuxShmPinStore(context).importConfirmed(selected)
                val files=File(context.noBackupFilesDir,"lx")
                if(!files.exists()) Os.mkdir(files.absolutePath,448)
                Os.lstat(files.absolutePath).let {check(OsConstants.S_ISDIR(it.st_mode) && it.st_uid==Process.myUid() && it.st_mode and 511==448) {"PROBE_DIRECTORY_INVALID"}}
                config.getJSONObject("paths").put("files_dir",files.absolutePath)
                // 無永続preferencesならdefault Off。通常のtheme/layout storeへ一切書き込まない。
                config.remove("preferences_dir");config.put("profiles",org.json.JSONArray())
                config.put("debug_probe_profile_v2",ConnectionProfileV2Codec.encode(selected).toString(Charsets.UTF_8)).put("debug_linux_shm_pins",pins.nativeJson())
                NativeSessionHost.DebugSdkProbeConfig(config.toString(),selected)
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
                val app=value.optJSONObject("app");val transport=value.getJSONObject("transport");val forward=transport.optJSONObject("linux_forward")
                result.put("failure_metadata",JSONObject().put("state",value.optString("state")).put("app_state",app?.optString("state"))
                    .put("app_error",app?.opt("error")).put("source_hashes",app?.opt("verified_hashes")).put("callbacks",app?.opt("callbacks"))
                    .put("transport_state",transport.optString("state")).put("transport_error",transport.opt("error"))
                    .put("linux_state",forward?.optString("state")).put("linux_error",forward?.opt("error")).put("journal_pending",forward?.opt("journal_pending"))
                    .put("render_error",value.getJSONObject("render").opt("error")).put("sdk_bindings",value.getJSONObject("shutdown").optInt("sdk_bindings")))
            } catch(_:Exception) { }
            close()
        }
        fun command(type:String,nonce:String?) {
            stage="enqueue_$type"
            NativeSessionHost.debugSdkProbeCommand(ownerToken,type,session,nonce) { receipt ->
                if(active!==this || closing) return@debugSdkProbeCommand
                if(receipt.runtime!=handle || receipt.code!=NativeBridge.ACCEPTED || receipt.request==null) fail("PROBE_ENQUEUE_REJECTED")
                else {request=receipt.request;stage=if(type=="disconnect") "disconnecting" else "pattern";publish()}
            }
        }
        fun accepted() = completions["$handle/$request"]?.also {check(it=="ACCEPTED") {"PROBE_COMMAND_REJECTED"}}=="ACCEPTED"
        fun observeSdk(s:JSONObject) {
            val app=s.optJSONObject("app") ?: return
            if(app.optString("session")!=session || app.optString("nonce")!=nonce.take(16)) return
            check(s.getJSONObject("render").isNull("theme_id") && !s.getJSONObject("theme").getBoolean("loading")) {"PROBE_DECORATION_ENABLED"}
            val plans=objects(s.getJSONArray("panels")).filter {it.optString("action")=="create" && it.isNull("error") && it.getJSONObject("target").optString("session")==session}
            if(plans.isEmpty()) return
            check(plans.size==1) {"PROBE_TARGET_CHANGED"}
            val plan=plans.single();val key=plan.getJSONObject("target")
            check(key.getString("scene")==scene && (target==null || same(checkNotNull(target),key))) {"PROBE_TARGET_CHANGED"}
            val draw=objects(s.getJSONObject("render").getJSONArray("targets")).singleOrNull {same(it.getJSONObject("key"),key)} ?: return
            val sdk=objects(adapter.metrics().getJSONArray("targets")).singleOrNull {it.optString("role")=="windows" && same(it.getJSONObject("target"),key)} ?: return
            val placement=objects(s.getJSONObject("workspace").getJSONArray("windows")).singleOrNull {it.optString("session")==session && it.optString("window")==key.getString("window")}?.getJSONObject("placement") ?: return
            val applied=placement.optJSONObject("applied_pose") ?: return
            val entity=adapter.windowEntity(key.getString("window")) ?: return
            val pose=PanelPoseCodec.fromSdk(getAbsoluteTransform(entity)) ?: return
            if(!plan.getBoolean("applied") || !same(applied.getJSONObject("target"),key) || !PanelPoseCodec.near(pose,applied.getJSONObject("pose"),.001) ||
                draw.getLong("frames")<=0 || draw.isNull("content_frame")) return
            check(plan.isNull("decoration") && sdk.isNull("decoration") && draw.isNull("canvas")) {"PROBE_DECORATION_ENABLED"}
            val frame=draw.getLong("frames");val content=draw.getString("content_frame").toLong()
            check(frame>=lastFrame && content>=observedContent) {"PROBE_DRAW_REGRESSED"}
            if(target==null) {target=JSONObject(key.toString());firstFrame=frame}
            lastFrame=frame;observedContent=content;sdkObserved=true
            result.put("sdk_target",target).put("sdk_entity",sdk.getString("entity")).put("first_draw_frames",firstFrame)
                .put("last_draw_frames",lastFrame).put("last_content_frame",content.toString()).put("sdk_pose_applied",true)
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
                    session=transport.getString("session");result.put("probe_ready",true).put("remote_uid",probe.getInt("uid"));command("linux_shm_sdk_probe",nonce.take(16))
                } else if(stage=="pattern") {
                    check(transport.getString("session")==session && transport.getString("state")=="ProbeReady") {"PROBE_TARGET_CHANGED"}
                    observeSdk(s)
                    val forward=transport.optJSONObject("linux_forward");val app=s.optJSONObject("app")
                    check((forward==null || forward.isNull("error")) && (app==null || app.isNull("error"))) {"PROBE_SHM_FAILED"}
                    if(accepted() && forward?.optString("state")=="pass" && app?.optString("state")=="pass") {
                        check(forward.getString("session")==session && forward.getString("nonce")==nonce.take(16) && forward.getInt("uid")==result.getInt("remote_uid") &&
                            app.getString("session")==session && app.getString("nonce")==nonce.take(16)) {"PROBE_TARGET_CHANGED"}
                        check(app.getString("callback_clock")=="target-driven" && app.getString("mode")=="pattern" && app.getInt("verified_hashes")==100 &&
                            listOf("copies","callbacks","releases").all {app.getLong(it)==100L}) {"PROBE_PIXEL_REPORT_INVALID"}
                        check(listOf("tools_verified","registered","forward_cancelled","remote_cleaned","channel_closed","pattern_verified").all {forward.getBoolean(it)} &&
                            !forward.getBoolean("journal_pending") && forward.getInt("frames")==100) {"PROBE_REMOTE_REPORT_INVALID"}
                        check(sdkObserved && lastFrame>firstFrame && observedContent>0) {"PROBE_SDK_DRAW_MISSING"}
                        result.put("source_verified_hashes",100).put("target_driven_callbacks",100).put("sdk_successful_draw_observed",true)
                            .put("remote_cleanup_confirmed",true).put("journal_cleared",true)
                        verified=true;command("disconnect",null)
                    }
                } else if(stage=="disconnecting" && accepted() && transport.getString("state")=="Disconnected" && transport.getInt("owned_connections")==0) {
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
                    result.put("normal_runtime_restored",true).put("product_settings_untouched",base.isNotEmpty() && originals.size==4)
                        .put("old_runtime_destroyed",handle>0L).put("probe_had_no_owner",handle==0L)
                        .put("result",if(verified && result.optBoolean("local_native_sdk_cleaned") && !result.has("reason")) "pass" else if(cancelled) "cancelled" else "fail")
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
