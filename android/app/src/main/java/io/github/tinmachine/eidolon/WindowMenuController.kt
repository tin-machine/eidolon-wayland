package io.github.tinmachine.eidolon

import android.os.SystemClock
import com.meta.spatial.core.Entity
import com.meta.spatial.runtime.Scene
import com.meta.spatial.toolkit.SceneObjectSystem
import org.json.JSONObject

/** Survives the controls rebuild caused by a size change; it never borrows the global selected window. */
class WindowMenuController(private val parent:Entity,private val target:String,private val runtime:Long,val revision:Long,
    private val layout:WindowControlLayout,private val sdk:Scene,private val objects:SceneObjectSystem,private val live:()->Boolean):AutoCloseable {
    private var owner:NativeSessionHost.WindowOperationTicket?=null
    private var capture:WindowSizeCapture?=null
    private var view:WindowMenuView?=null
    private var resetRequest:String?=null
    private var dimensions=""
    private var closed=false
    private var lastPhase="IDLE";private var lastReason="idle";private var lastRequest:String?=null
    private var opened=0L;private var completed=0L;private var cancelled=0L;private var rejected=0L
    val phase get()=capture?.phase
    fun open(control:NativeSessionHost.WindowControlTicket):Boolean {
        if(closed || capture!=null || !live() || control.runtime!=runtime || control.revision!=revision || !same(JSONObject(control.target),JSONObject(target))) return false
        val ticket=NativeSessionHost.beginWindowMenu(control) ?: return false
        owner=ticket
        try {
            val state=JSONObject(NativeBridge.nativeSnapshot(runtime)).getJSONObject("runtime")
            check(current(ticket,state)) {"owner_changed"}
            val window=window(state) ?: error("window_missing")
            val sizing=window.getJSONObject("sizing")
            capture=WindowSizeCapture(identity(ticket),size(sizing.getJSONObject("physical_size_m")),command(sizing),state.getLong("input_resets"),SystemClock.elapsedRealtime())
            dimensions=pixels(window,plan(state))
            resetRequest=NativeSessionHost.resetWindowOperation(ticket) ?: error("reset_enqueue_rejected")
            opened++;return true
        } catch(_:Exception) {capture?.cancel("open_failed");rejected++;end();return false}
    }
    fun tick(state:JSONObject,controlsReady:Boolean) {
        val c=capture ?: return;val ticket=owner ?: return;val now=SystemClock.elapsedRealtime()
        if(!current(ticket,state)) c.cancel("owner_changed")
        c.current(identity(ticket),now)
        val window=window(state);val sizing=window?.optJSONObject("sizing")
        if(sizing==null) c.cancel("window_missing")
        if(c.phase==WindowSizeCapture.Phase.RESETTING) {
            c.resetObserved(identity(ticket),now,resetRequest?.let(NativeSessionHost::windowOperationCompletion),NativeSessionHost.windowOperationAndroidReleased(ticket),state.getLong("input_resets"))
        }
        if(c.phase==WindowSizeCapture.Phase.MENU && view==null && sizing!=null) {
            val physical=size(sizing.getJSONObject("physical_size_m"))
            if(command(sizing)!=c.initialCommand || !c.initial.near(physical) || pixels(checkNotNull(window),plan(state))!=dimensions) c.cancel("size_changed")
            else try {
                view=WindowMenuView(parent,layout,physical.width.toDouble(),physical.height.toDouble(),sdk,objects,
                    { !closed && live() && capture===c && c.phase==WindowSizeCapture.Phase.MENU && NativeSessionHost.windowOperationCurrent(ticket) },
                    {NativeSessionHost.windowOperationCurrent(ticket)},physical.step(true)!=null,physical.step(false)!=null) {choose(it)}
                checkNotNull(view).initialize()
            } catch(_:Exception) {c.cancel("menu_create_failed")}
        }
        runCatching {view?.update()}.onFailure {c.cancel("menu_pose_failed")}
        if(view?.metadata()?.optLong("failures",0)?.let {it>0}==true) c.cancel("menu_sdk_failed")
        if(c.phase==WindowSizeCapture.Phase.NEEDS_SUBMIT) {
            closeView()
            if(c.phase!=WindowSizeCapture.Phase.CANCELLED) {
                val request=NativeSessionHost.submitWindowSize(ticket,c.initialCommand,c.initial,checkNotNull(c.larger))
                if(request==null || !c.submitted(request,now)) c.cancel("size_enqueue_rejected")
                else NativeSessionHost.windowOperationPhase(ticket,"resizing")
            }
        }
        if(c.phase in setOf(WindowSizeCapture.Phase.COMMITTING,WindowSizeCapture.Phase.GEOMETRY) && sizing!=null) {
            val applied=sizing.optJSONObject("applied_physical_size_m")
            if(c.observed(identity(ticket),now,c.request?.let(NativeSessionHost::windowOperationCompletion),command(sizing),size(sizing.getJSONObject("physical_size_m")),
                    controlsReady && applied!=null && c.desired?.near(size(applied))==true,pixels(checkNotNull(window),plan(state))==dimensions)) beginReturn(state)
        }
        if(c.phase==WindowSizeCapture.Phase.RETURNING) {
            val input=state.optJSONObject("app")?.optJSONObject("input")
            val focused=input!=null && input.optString("focus_window")==JSONObject(target).getString("window") && !input.isNull("focus_token") &&
                NativeSessionHost.keyboardFocusMatches(JSONObject(target),input.getString("focus_token"))
            c.focusObserved(identity(ticket),now,state.getLong("input_resets"),focused)
        }
        when(c.phase) {
            WindowSizeCapture.Phase.COMPLETE,WindowSizeCapture.Phase.CANCELLED -> end()
            else -> Unit
        }
    }
    private fun choose(action:String) {
        val c=capture ?: return;val ticket=owner ?: return
        if(c.phase!=WindowSizeCapture.Phase.MENU) return
        val state=runCatching {JSONObject(NativeBridge.nativeSnapshot(runtime)).getJSONObject("runtime")}.getOrNull()
        if(state==null || !current(ticket,state)) {c.cancel("owner_changed");return}
        if(action=="back") {beginReturn(state);return}
        if(action !in setOf("larger","smaller")) return
        val window=window(state) ?: return
        if(pixels(window,plan(state))!=dimensions) {c.cancel("pixel_dimensions_changed");return}
        val sizing=window.getJSONObject("sizing")
        c.choose(identity(ticket),SystemClock.elapsedRealtime(),action=="larger",command(sizing),size(sizing.getJSONObject("physical_size_m")))
    }
    private fun beginReturn(state:JSONObject) {
        val c=capture ?: return;val ticket=owner ?: return
        closeView()
        if(c.phase==WindowSizeCapture.Phase.CANCELLED) return
        if(!c.beginReturn(identity(ticket),SystemClock.elapsedRealtime(),state.getLong("input_resets")) || !NativeSessionHost.returnWindowOperation(ticket)) c.cancel("raw_return_rejected")
    }
    private fun closeView() {
        val old=view ?: return
        try {old.close();view=null} catch(_:Exception) {capture?.cancel("menu_cleanup_failed");owner?.let {NativeSessionHost.windowOperationPhase(it,"cleanup")}}
    }
    private fun end() {
        closeView()
        if(view!=null) return // Keep the single owner and block direct input until SDK resources are actually released.
        val c=capture
        if(c?.phase==WindowSizeCapture.Phase.COMPLETE) completed++ else if(c!=null) cancelled++
        lastPhase=c?.phase?.name ?: "CANCELLED";lastReason=c?.reason ?: "open_failed";lastRequest=c?.request
        c?.request?.let(NativeSessionHost::forgetWindowOperation);resetRequest?.let(NativeSessionHost::forgetWindowOperation)
        owner?.let(NativeSessionHost::finishWindowOperation)
        owner=null;capture=null;resetRequest=null
    }
    private fun current(ticket:NativeSessionHost.WindowOperationTicket,state:JSONObject):Boolean {
        if(!live() || !NativeSessionHost.windowOperationCurrent(ticket) || state.optString("state")!="Ready" || !state.optBoolean("activity_visible") || state.optString("xr_state")!="FOCUSED" ||
            state.optString("scene")!=JSONObject(target).optString("scene") || state.getJSONObject("render").optLong("theme_revision",-1)!=revision) return false
        val p=plan(state) ?: return false
        if(p.optString("action")!="create" || !p.optBoolean("applied") || !p.isNull("error")) return false
        val frames=state.getJSONObject("render").getJSONArray("targets")
        return (0 until frames.length()).any {same(frames.getJSONObject(it).getJSONObject("key"),JSONObject(target)) && frames.getJSONObject(it).optLong("frames")>0}
    }
    private fun plan(state:JSONObject):JSONObject?=state.optJSONArray("panels")?.let {a -> (0 until a.length()).map {a.getJSONObject(it)}.singleOrNull {same(it.getJSONObject("target"),JSONObject(target))}}
    private fun window(state:JSONObject):JSONObject? {
        val app=state.optJSONObject("app") ?: return null
        if(app.optString("session")!=JSONObject(target).optString("session")) return null
        return app.optJSONArray("windows")?.let {a -> (0 until a.length()).map {a.getJSONObject(it)}.singleOrNull {it.optString("window")==JSONObject(target).optString("window") && it.optString("life")=="Mapped"}}
    }
    fun metadata()=JSONObject().put("target",JSONObject(target)).put("revision",revision).put("phase",phase?.name ?: lastPhase).put("reason",capture?.reason ?: lastReason).put("request_id",capture?.request ?: lastRequest ?: JSONObject.NULL)
        .put("opened",opened).put("completed",completed).put("cancelled",cancelled).put("rejected",rejected).put("view",view?.metadata())
    override fun close() {closed=true;capture?.cancel("owner_disposed");end();check(view==null) {"MENU_CLEANUP_PENDING"}}
    companion object {
        private fun same(a:JSONObject,b:JSONObject)=listOf("session","window","scene","generation").all {a.optString(it)==b.optString(it)}
        private fun identity(ticket:NativeSessionHost.WindowOperationTicket)=WindowSizeIdentity(ticket.control.runtime,ticket.control.target,ticket.control.revision,ticket.control.intent)
        private fun size(value:JSONObject)=WindowPhysicalSize(value.getDouble("width_m").toFloat(),value.getDouble("height_m").toFloat())
        private fun command(sizing:JSONObject)=if(sizing.isNull("size_command")) null else sizing.getString("size_command")
        private fun pixels(window:JSONObject,plan:JSONObject?)=JSONObject().put("logical",window.opt("logical_size")).put("producer",window.getJSONObject("sizing").opt("producer_size_px"))
            .put("canvas",plan?.optJSONObject("decoration")?.opt("canvas")).toString()
    }
}
