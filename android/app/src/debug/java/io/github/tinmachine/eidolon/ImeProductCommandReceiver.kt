package io.github.tinmachine.eidolon

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import org.json.JSONObject

/** DUMP-only, explicit component, fixed operation. Does not launch an Activity, focus/open a terminal, or type text. */
class ImeProductCommandReceiver:BroadcastReceiver() {
    override fun onReceive(context:Context,intent:Intent) {
        if(!BuildConfig.DEBUG || intent.component?.packageName!=context.packageName || intent.component?.className!=javaClass.name ||
            intent.action!="io.github.tinmachine.eidolon.IME_PRODUCT_COMMAND" || intent.extras?.keySet()!=setOf("case","nonce")) return
        val name=runCatching {intent.getStringExtra("case")}.getOrNull() ?: return
        val nonce=runCatching {intent.getStringExtra("nonce")}.getOrNull() ?: return
        if(name!="ime-product-toggle" || active!=null || !budget.claim(nonce)) return
        val result=JSONObject().put("case",name).put("nonce",nonce).put("result","running")
            .put("toggle_calls",0).put("auto_open",false).put("auto_focus",false).put("text_injected",false)
            .put("actual_ime_verified",false).put("physical_shortcut_verified",false).put("hmd_verified",false)
        try {
            if(!BuildHooks.imeProductProbeAllowed()) {
                result.put("result","rejected").put("reason","other_probe_active");NativeSessionHost.publishCheck(result);return
            }
            val beforeJson=TerminalImeController.debugProductState()
            if(beforeJson?.optBoolean("active")==true && beforeJson.optString("scope")!="normal_terminal_ime") {
                result.put("result","rejected").put("reason","other_probe_active");NativeSessionHost.publishCheck(result);return
            }
            val before=beforeJson?.let(::decode)
            // Exactly one invocation, even if accepted but later observation/cleanup fails.
            result.put("toggle_calls",1)
            val accepted=TerminalImeController.toggle()
            val afterJson=TerminalImeController.debugProductState()
            val after=afterJson?.let(::decode)
            result.put("accepted",accepted).put("initial",afterJson ?: JSONObject.NULL)
            val state=ImeProductProbeState.admit(before,after,accepted,SystemClock.elapsedRealtime())
            if(state==null) {
                result.put("result",if(accepted) "fail" else "rejected").put("reason",if(accepted) "initial_owner_unconfirmed" else "normal_target_not_ready")
                NativeSessionHost.publishCheck(result);return
            }
            result.put("operation",if(state.on) "on" else "off").put("owner_before_view",state.initial.ownerBeforeView)
            Check(result,state).also {active=it;ui.post(it)}
            NativeSessionHost.publishCheck(result)
        } catch(_:Exception) {
            result.put("result","fail").put("reason","metadata_unavailable");NativeSessionHost.publishCheck(result)
        }
    }
    private class Check(val result:JSONObject,val state:ImeProductProbeState):Runnable {
        override fun run() {
            if(active!==this) return
            try {
                val row=TerminalImeController.debugProductState()
                val observation=row?.let(::decode)
                state.observe(observation,SystemClock.elapsedRealtime())
                result.put("current",row ?: JSONObject.NULL).put("polls",state.polls).put("reason",state.reason)
                result.put("result",when(state.outcome) {
                    ImeProductProbeState.Outcome.RUNNING->"running"
                    ImeProductProbeState.Outcome.OBSERVED->"observed"
                    ImeProductProbeState.Outcome.FAILED->"fail"
                    ImeProductProbeState.Outcome.TIMED_OUT->"timeout"
                })
                val owned=observation?.takeIf {it.valid() && state.initial.sameOwner(it)}
                result.put("same_owner",owned!=null)
                result.put("native_cleanup_confirmed",owned?.let {it.connectionPhase=="CLOSED" && !it.nativeBlocked} ?: false)
                    .put("android_released",owned?.androidReleased ?: false)
                    .put("controller_stopped",owned?.stopped ?: false)
                    .put("raw_restored",owned?.let {it.rawReturn=="COMPLETE" && it.rawFocusSameTarget && !it.active && it.stopped && !it.nativeBlocked && it.androidReleased} ?: false)
                NativeSessionHost.publishCheck(result)
                if(state.outcome==ImeProductProbeState.Outcome.RUNNING) ui.postDelayed(this,100) else active=null
            } catch(_:Exception) {result.put("result","fail").put("reason","metadata_unavailable");NativeSessionHost.publishCheck(result);active=null}
        }
    }
    companion object {
        private val ui=Handler(Looper.getMainLooper())
        private val budget=ImeProductNonceBudget()
        private var active:Check?=null
        fun probeActive():Boolean=active!=null
        private fun decode(v:JSONObject):ImeProductObservation?=runCatching {
            fun flag(name:String)=v.opt(name) as? Boolean ?: error("invalid_flag")
            val target=v.getJSONObject("target")
            ImeProductObservation(v.getString("scope")=="normal_terminal_ime",v.getString("run"),v.getString("runtime").toLong(),
                ImeSdkTarget(target.getString("session"),target.getString("window"),target.getString("scene"),target.getString("generation")),
                flag("active"),flag("stopped"),flag("owner_before_view"),v.getString("phase"),v.getString("connection_phase"),
                flag("native_raw_blocked"),flag("android_released"),flag("editor_view_focus"),flag("editor_window_focus"),flag("editor_connection_active"),
                v.getString("raw_return"),flag("raw_focus_same_target"),flag("current_runtime_matches"),flag("captured_input_current"))
        }.getOrNull()
    }
}
