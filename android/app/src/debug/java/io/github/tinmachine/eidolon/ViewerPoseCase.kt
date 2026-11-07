package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import com.meta.spatial.runtime.Scene
import org.json.JSONObject

/** 固定read-only probe。window/input/configを書き換えず、生の室内座標・姿勢を記録しない。 */
object ViewerPoseCase {
    fun start(nonce:String,scene:()->Scene,done:()->Unit) { Check(nonce,scene,done).start() }
    private class Check(val nonce:String,val scene:()->Scene,val done:()->Unit):Runnable {
        private val ui=Handler(Looper.getMainLooper())
        private val started=SystemClock.elapsedRealtime()
        private val result=JSONObject().put("case","viewer-pose").put("nonce",nonce).put("result","running")
            .put("native_commands",0).put("sdk_mutations",0).put("owned_resources",0)
            .put("tracking_timestamp_available",false).put("tracking_valid_flag_available",false)
            .put("tracking_freshness","not_exposed").put("recenter_applied",false).put("hmd_verified",false)
        private var state:ViewerPoseProbeState?=null
        private var renderKey:String?=null
        private var finished=false
        private var maxReadMs=0L
        fun start() {NativeSessionHost.publishCheck(result);ui.post(this)}
        override fun run() {
            if(finished) return
            try {
                val now=SystemClock.elapsedRealtime()
                if(now-started>=10000) {finish(false,"ready_timeout");return}
                val handle=NativeSessionHost.currentHandle()
                if(handle<=0) {if(state==null) ui.postDelayed(this,100) else {result.put("same_runtime_scene",false);finish(false,"runtime_unavailable")};return}
                val runtime=JSONObject(NativeBridge.nativeSnapshot(handle)).optJSONObject("runtime")
                val visible=runtime?.optString("state")=="Ready" && runtime.optBoolean("activity_visible") && runtime.optString("xr_state")=="FOCUSED" &&
                    runtime.optJSONObject("render")?.optBoolean("paused")!=true
                if(state==null && !visible) {ui.postDelayed(this,100);return}
                if(runtime==null) {finish(false,"runtime_unavailable");return}
                val sceneEpoch=runtime.optString("scene").toLongOrNull()
                if(sceneEpoch==null || sceneEpoch<=0 || sceneEpoch!=NativeSessionHost.currentSceneEpoch()) {result.put("same_runtime_scene",false);finish(false,"scene_changed");return}
                val sdk=scene();val owner=ViewerPoseOwner(handle,sceneEpoch,sdk.handle)
                val targets=runtime.getJSONObject("render").getJSONArray("targets")
                val draws=(0 until targets.length()).map {targets.getJSONObject(it)}
                val draw=if(renderKey==null) draws.firstOrNull {it.optLong("frames")>0} else draws.singleOrNull {key(it.getJSONObject("key"))==renderKey}
                if(draw==null) {if(state==null) ui.postDelayed(this,100) else finish(false,"render_target_changed");return}
                if(state==null) {renderKey=key(draw.getJSONObject("key"));state=ViewerPoseProbeState(owner,now,draw.getLong("frames"));result.put("same_runtime_scene",true).put("render_role",draw.optString("role"))}
                val captured=checkNotNull(state)
                if(owner!=captured.owner || !visible) {if(owner!=captured.owner) result.put("same_runtime_scene",false);captured.fail(if(owner!=captured.owner) "owner_changed" else "not_focused");publish();finish(false,captured.reason);return}
                val readStart=SystemClock.elapsedRealtime()
                val pose=sdk.getViewerPose()
                val readEnd=SystemClock.elapsedRealtime();maxReadMs=maxOf(maxReadMs,readEnd-readStart)
                val value=WindowMotionPose(listOf(pose.t.x.toDouble(),pose.t.y.toDouble(),pose.t.z.toDouble()),
                    listOf(pose.q.x.toDouble(),pose.q.y.toDouble(),pose.q.z.toDouble(),pose.q.w.toDouble()))
                val candidate=WindowRecenterPose.candidate(value)
                val matches=if(candidate==null) true else {
                    val yaw=pose.removePitchAndRoll();val forward=yaw.forward()
                    val sdkCandidate=WindowMotionPose(listOf(pose.t.x+forward.x*WindowRecenterPose.DISTANCE_M,pose.t.y.toDouble(),pose.t.z+forward.z*WindowRecenterPose.DISTANCE_M),
                        listOf(yaw.q.x.toDouble(),yaw.q.y.toDouble(),yaw.q.z.toDouble(),yaw.q.w.toDouble()))
                    candidate.near(sdkCandidate,.0001)
                }
                val after=JSONObject(NativeBridge.nativeSnapshot(handle)).optJSONObject("runtime")
                if(NativeSessionHost.currentHandle()!=handle || NativeSessionHost.currentSceneEpoch()!=sceneEpoch || after?.optString("scene")!=sceneEpoch.toString()) {
                    result.put("same_runtime_scene",false);captured.fail("owner_changed");publish();finish(false,captured.reason);return
                }
                val stillFocused=after.optString("state")=="Ready" && after.optBoolean("activity_visible") && after.optString("xr_state")=="FOCUSED" &&
                    after.optJSONObject("render")?.optBoolean("paused")!=true
                captured.observe(owner,stillFocused,readEnd,value,draw.getLong("frames"),matches)
                publish()
                when(captured.phase) {
                    ViewerPoseProbeState.Phase.COMPLETE -> finish(true,captured.reason)
                    ViewerPoseProbeState.Phase.FAILED -> finish(false,captured.reason)
                    ViewerPoseProbeState.Phase.SAMPLING -> ui.postDelayed(this,100)
                }
            } catch(_:Exception) {finish(false,"viewer_read_failed")}
        }
        private fun publish() {
            state?.let {s->result.put("samples",s.samples).put("candidate_samples",s.candidates).put("render_advanced",s.renderAdvanced)
                .put("motion_observed",s.motionObserved).put("origin_observed",s.originObserved).put("phase",s.phase.name)
                .put("max_read_ms",maxReadMs).put("reason",s.reason)}
            result.put("elapsed_ms",SystemClock.elapsedRealtime()-started)
            NativeSessionHost.publishCheck(result)
        }
        private fun finish(pass:Boolean,reason:String) {
            if(finished) return
            finished=true;ui.removeCallbacks(this)
            result.put("result",if(pass) "pass" else "fail").put("reason",reason)
                .put("pose_sampling_verified",pass).put("operation_adoption_verified",false)
            NativeSessionHost.publishCheck(result);done()
        }
        private fun key(target:JSONObject)=listOf("session","window","scene","generation").joinToString(":") {target.getString(it)}
    }
}
