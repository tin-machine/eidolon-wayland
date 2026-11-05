package io.github.tinmachine.eidolon

import android.os.Debug
import android.os.Process
import android.os.SystemClock
import java.io.File
import org.json.JSONArray
import org.json.JSONObject

/** 通常SDKを動かしたまま四条件を測る。UI threadをsleepせず、可視性喪失は失敗にする。 */
class ThemeSdkPerformance {
    val rows=JSONArray()
    private val modes=listOf("off","reduced","animated","animated_metrics")
    private var index=-1
    private var settledAt=0L
    private var started=0L
    private var cpu=0L
    private var heap=0L
    private var rss=0L
    private var before:JSONObject?=null
    val phase:String get()=modes.getOrElse(index) {"setup"}
    private fun configure(next:Int) {
        index=next;settledAt=0;started=0
        NativeSessionHost.setWorkloadObservation(next==3)
        NativeSessionHost.setTheme(if(next==0) "off" else "builtin",next==1)
    }
    private fun rssBytes():Long {
        val line=File("/proc/self/status").useLines { lines -> lines.firstOrNull {it.startsWith("VmRSS:")} }
        return checkNotNull(Regex("VmRSS:\\s+([0-9]+) kB").matchEntire(checkNotNull(line))).groupValues[1].toLong()*1024
    }
    private fun sample(s:JSONObject):JSONObject = JSONObject()
        .put("state",s.getString("state")).put("activity_visible",s.getBoolean("activity_visible"))
        .put("xr_state",s.getString("xr_state")).put("scene",s.getString("scene"))
        .put("theme",s.getJSONObject("theme")).put("render",s.getJSONObject("render"))
        .put("workspace",JSONObject().put("workload_observation",s.getJSONObject("workspace").getBoolean("workload_observation")))
        .put("app",JSONObject().put("session",s.getJSONObject("app").getString("session"))
            .put("windows",s.getJSONObject("app").getJSONArray("windows")).put("workloads",s.getJSONObject("app").getJSONArray("workloads")))
        .put("transport",JSONObject().put("app_forward",JSONObject().put("remote",JSONObject()
            .put("workload",s.getJSONObject("transport").getJSONObject("app_forward").getJSONObject("remote").getJSONObject("workload")))))
    fun step(s:JSONObject,ready:(JSONObject,Boolean)->Boolean):Boolean {
        if(index<0) {configure(0);return false}
        check(s.getBoolean("activity_visible") && s.getString("xr_state")=="FOCUSED") {"SDK_PERFORMANCE_NOT_FOCUSED"}
        val now=SystemClock.elapsedRealtime()
        val theme=s.getJSONObject("theme")
        val render=s.getJSONObject("render")
        val observing=index==3
        val workloads=s.getJSONObject("app").getJSONArray("workloads")
        val fresh=workloads.length()==2 && (0 until workloads.length()).all { i ->
            val display=workloads.getJSONObject(i).getJSONObject("display")
            display.optBoolean("stale")==!observing && (!observing || display.optString("coverage")=="complete")
        }
        val remote=s.getJSONObject("transport").getJSONObject("app_forward").getJSONObject("remote").getJSONObject("workload")
        val valid=ready(s,index!=0) && !theme.getBoolean("loading") && theme.isNull("error") && theme.isNull("fallback_reason") &&
            theme.getString("selection")==if(index==0) "off" else "builtin"
        val configured=valid && theme.getBoolean("reduced_motion")== (index==1) &&
            render.isNull("theme_id")== (index==0) && s.getJSONObject("workspace").getBoolean("workload_observation")==observing && fresh &&
            (observing || remote.getString("capability")=="unavailable" && remote.getDouble("scan_seconds")==0.0)
        if(started==0L) {
            if(!configured) {settledAt=0;return false}
            if(settledAt==0L) {settledAt=now;return false}
            if(now-settledAt<1000) return false
            before=sample(s);heap=Debug.getNativeHeapAllocatedSize();rss=rssBytes()
            cpu=Process.getElapsedCpuTime();started=SystemClock.elapsedRealtime()
            return false
        }
        check(configured) {"SDK_PERFORMANCE_STATE_CHANGED"}
        if(now-started<6000) return false
        rows.put(JSONObject().put("mode",phase).put("elapsed_ms",now-started).put("process_cpu_ms",Process.getElapsedCpuTime()-cpu)
            .put("native_heap_before",heap).put("native_heap_after",Debug.getNativeHeapAllocatedSize())
            .put("rss_before",rss).put("rss_after",rssBytes()).put("before",before).put("after",sample(s)))
        if(index==3) return true
        configure(index+1);return false
    }
}
