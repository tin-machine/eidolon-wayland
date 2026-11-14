package io.github.tinmachine.eidolon

import android.os.SystemClock
import com.meta.spatial.core.Entity
import com.meta.spatial.runtime.Scene
import com.meta.spatial.toolkit.SceneObjectSystem
import com.meta.spatial.toolkit.getAbsoluteTransform
import org.json.JSONObject

/** Survives the controls rebuild caused by a size change; it never borrows the global selected window. */
class WindowMenuController(private val parent:Entity,private val target:String,private val runtime:Long,val revision:Long,
    private val layout:WindowControlLayout,private val sdk:Scene,private val objects:SceneObjectSystem,private val live:()->Boolean,private val sdkScope:SdkResourceScope?=null):AutoCloseable {
    private var owner:NativeSessionHost.WindowOperationTicket?=null
    private var capture:WindowSizeCapture?=null
    private var recenter:WindowRecenterCapture?=null
    private var groupMenu:WindowGroupMenuController?=null
    private var lastGroup:JSONObject?=null
    private var groupAccepted=0L
    private var lastAction="menu"
    private var recenterReadMs:Long?=null
    private var recenterReadReason:String?=null
    private var view:WindowMenuView?=null
    private var resetRequest:String?=null
    private var dimensions=""
    private var closed=false
    private var lastPhase="IDLE";private var lastReason="idle";private var lastRequest:String?=null
    private var opened=0L;private var completed=0L;private var cancelled=0L;private var rejected=0L
    val phase get()=groupMenu?.menuPhase ?: recenter?.phase?.let {WindowSizeCapture.Phase.valueOf(it.name)} ?: capture?.phase
    val recentering get()=recenter!=null
    val groupMenuActive get()=groupMenu!=null
    fun open(control:NativeSessionHost.WindowControlTicket):Boolean {
        if(closed || owner!=null || capture!=null || recenter!=null || groupMenu!=null || view!=null || !live() || control.runtime!=runtime || control.revision!=revision || !same(JSONObject(control.target),JSONObject(target))) return false
        val ticket=NativeSessionHost.beginWindowMenu(control) ?: return false
        owner=ticket;lastAction="menu";recenterReadMs=null;recenterReadReason=null
        try {
            val state=JSONObject(NativeBridge.nativeSnapshot(runtime)).getJSONObject("runtime")
            check(current(ticket,state)) {"owner_changed"}
            val window=window(state) ?: error("window_missing")
            val sizing=window.getJSONObject("sizing")
            control.groupMenuPermit?.takeIf {it.group!=null}?.let {permit->startGroup(ticket,state,permit);opened++;return true}
            capture=WindowSizeCapture(identity(ticket),size(sizing.getJSONObject("physical_size_m")),command(sizing),state.getLong("input_resets"),SystemClock.elapsedRealtime())
            dimensions=pixels(window,plan(state))
            resetRequest=NativeSessionHost.resetWindowOperation(ticket) ?: error("reset_enqueue_rejected")
            opened++;return true
        } catch(_:Exception) {capture?.cancel("open_failed");rejected++;end();return false}
    }
    fun tick(state:JSONObject,controlsReady:Boolean) {
        groupMenu?.let {group->group.tick(state);if(group.terminal) end();return}
        val c=capture ?: return;val ticket=owner ?: return;val now=SystemClock.elapsedRealtime()
        if(!current(ticket,state)) {c.cancel("owner_changed");recenter?.cancel("owner_changed")}
        if(recenter!=null) {tickRecenter(ticket,state,controlsReady,now);return}
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
                    {NativeSessionHost.windowOperationCurrent(ticket)},physical.step(true)!=null,physical.step(false)!=null,
                    if(ticket.control.groupMenuPermit!=null) buildList {
                        add(WindowMenuItem("larger","大きく",physical.step(true)!=null));add(WindowMenuItem("smaller","小さく",physical.step(false)!=null))
                        if(WindowRecenterPolicy.ENABLED) add(WindowMenuItem("recenter","正面へ"))
                        add(WindowMenuItem("group","窓をまとめる"));add(WindowMenuItem("back","戻る"))
                    } else null,sdkScope) {choose(it)}
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
        if(c.phase!=WindowSizeCapture.Phase.MENU || recenter!=null) return
        val state=runCatching {JSONObject(NativeBridge.nativeSnapshot(runtime)).getJSONObject("runtime")}.getOrNull()
        if(state==null || !current(ticket,state)) {c.cancel("owner_changed");return}
        if(action=="group") {
            val permit=NativeSessionHost.windowGroupMenuPermit(ticket,state)
            if(permit==null) {c.cancel("group_source_changed");return}
            startGroup(ticket,state,permit);return
        }
        if(action=="back") {lastAction="back";beginReturn(state);return}
        if(action=="recenter") {chooseRecenter(ticket,state);return}
        if(action !in setOf("larger","smaller")) return
        val window=window(state) ?: return
        if(pixels(window,plan(state))!=dimensions) {c.cancel("pixel_dimensions_changed");return}
        val sizing=window.getJSONObject("sizing")
        lastAction=action
        c.choose(identity(ticket),SystemClock.elapsedRealtime(),action=="larger",command(sizing),size(sizing.getJSONObject("physical_size_m")))
    }
    private fun startGroup(ticket:NativeSessionHost.WindowOperationTicket,state:JSONObject,permit:WindowGroupMenuPermit) {
        closeView()
        if(view!=null) return
        capture?.cancel("group_page");capture=null
        val physical=size(checkNotNull(window(state)).getJSONObject("sizing").getJSONObject("physical_size_m"))
        lastAction="group"
        groupMenu=WindowGroupMenuController(parent,layout,physical.width.toDouble(),physical.height.toDouble(),sdk,objects,ticket,permit,state.getLong("input_resets"),sdkScope) {!closed && live()}
        checkNotNull(groupMenu).initialize()
    }
    private fun chooseRecenter(ticket:NativeSessionHost.WindowOperationTicket,before:JSONObject) {
        if(!WindowRecenterPolicy.ENABLED || !NativeSessionHost.windowOperationAndroidReleased(ticket)) return
        val key=JSONObject(target);val started=SystemClock.elapsedRealtime()
        lastAction="recenter";var stage="baseline_unavailable"
        try {
            val baseline=WindowRecenterWire.read(before,key) ?: error("recenter_baseline_unavailable")
            val occupied=WindowRecenterScene.read(before,key) ?: error("recenter_scene_unavailable")
            check(WindowRecenterWire.applied(before,key,baseline.poseCommand,baseline.pose) &&
                WindowMoveController.motion(getAbsoluteTransform(parent))?.near(baseline.pose)==true) {"recenter_geometry_pending"}
            stage="viewer_read_unavailable"
            val sceneHandle=sdk.handle;check(sceneHandle!=0L && NativeSessionHost.currentSceneEpoch().toString()==key.getString("scene"))
            val viewer=sdk.getViewerPose()
            val value=WindowMotionPose(listOf(viewer.t.x.toDouble(),viewer.t.y.toDouble(),viewer.t.z.toDouble()),
                listOf(viewer.q.x.toDouble(),viewer.q.y.toDouble(),viewer.q.z.toDouble(),viewer.q.w.toDouble()))
            stage="viewer_pose_unavailable"
            val choice=occupied.choose(value)
            val desired=choice.pose ?: error(choice.reason)
            val sampledAt=SystemClock.elapsedRealtime();stage="viewer_owner_changed"
            val after=JSONObject(NativeBridge.nativeSnapshot(runtime)).getJSONObject("runtime")
            val fresh=WindowRecenterWire.read(after,key) ?: error("recenter_baseline_unavailable")
            val now=SystemClock.elapsedRealtime();recenterReadMs=now-started
            check(now>=started && now-started<=100 && sdk.handle==sceneHandle && current(ticket,after) &&
                NativeSessionHost.currentSceneEpoch().toString()==key.getString("scene") &&
                baseline.sameDimensions(fresh) && baseline.samePlacement(fresh) && after.getLong("input_resets")==before.getLong("input_resets")) {"viewer_owner_changed"}
            check(occupied.matches(after,key)) {"recenter_scene_changed"}
            recenter=WindowRecenterCapture(identity(ticket),baseline,desired,after.getLong("input_resets"),sampledAt,occupied)
            recenterReadReason="captured"
        } catch(_:Exception) {recenterReadReason=stage;capture?.cancel(stage);rejected++}
    }
    private fun tickRecenter(ticket:NativeSessionHost.WindowOperationTicket,state:JSONObject,controlsReady:Boolean,now:Long) {
        val c=recenter ?: return;val key=JSONObject(target)
        c.current(identity(ticket),now)
        val geometry=WindowRecenterWire.read(state,key)
        if(geometry==null) c.cancel("recenter_geometry_unavailable")
        if(c.phase==WindowRecenterCapture.Phase.NEEDS_SUBMIT) {
            closeView()
            if(c.phase!=WindowRecenterCapture.Phase.CANCELLED) {
                val request=NativeSessionHost.submitWindowRecenter(ticket,c)
                if(request==null || !c.submitted(request,SystemClock.elapsedRealtime())) c.cancel("recenter_enqueue_rejected")
                else {NativeSessionHost.windowOperationPhase(ticket,"recentering");return} // Next tick owns the post-enqueue observation time.
            }
        }
        val applied=controlsReady && WindowRecenterWire.applied(state,key,c.request,c.desired) &&
            runCatching {WindowMoveController.motion(getAbsoluteTransform(parent))?.near(c.desired)==true}.getOrDefault(false)
        if(geometry!=null && c.phase in setOf(WindowRecenterCapture.Phase.COMMITTING,WindowRecenterCapture.Phase.GEOMETRY) &&
            c.observed(identity(ticket),now,c.request?.let(NativeSessionHost::windowOperationCompletion),geometry,state.getLong("input_resets"),applied)) {
            if(!c.beginReturn(identity(ticket),now,state.getLong("input_resets")) || !NativeSessionHost.returnWindowOperation(ticket)) c.cancel("raw_return_rejected")
        }
        if(geometry!=null && c.phase==WindowRecenterCapture.Phase.RETURNING) {
            val input=state.optJSONObject("app")?.optJSONObject("input")
            val focused=input!=null && input.optString("focus_window")==key.getString("window") && !input.isNull("focus_token") &&
                NativeSessionHost.keyboardFocusMatches(key,input.getString("focus_token"))
            c.focusObserved(identity(ticket),now,state.getLong("input_resets"),focused && applied,geometry)
        }
        if(c.phase in setOf(WindowRecenterCapture.Phase.COMPLETE,WindowRecenterCapture.Phase.CANCELLED)) end()
    }
    private fun beginReturn(state:JSONObject) {
        val c=capture ?: return;val ticket=owner ?: return
        closeView()
        if(c.phase==WindowSizeCapture.Phase.CANCELLED) return
        if(!c.beginReturn(identity(ticket),SystemClock.elapsedRealtime(),state.getLong("input_resets")) || !NativeSessionHost.returnWindowOperation(ticket)) c.cancel("raw_return_rejected")
    }
    private fun closeView() {
        val old=view ?: return
        try {old.close();view=null} catch(_:Exception) {capture?.cancel("menu_cleanup_failed");recenter?.cancel("menu_cleanup_failed");owner?.let {NativeSessionHost.windowOperationPhase(it,"cleanup")}}
    }
    private fun end() {
        groupMenu?.let {group->
            group.cancel("owner_disposed")
            if(!group.released()) return
            lastGroup=group.metadata();lastPhase="GROUP_${group.phase.name}";lastReason=group.reason;lastRequest=group.request
            if(group.phase==WindowGroupMenuCapture.Phase.ACCEPTED) groupAccepted++ else if(group.phase==WindowGroupMenuCapture.Phase.CANCELLED) cancelled++
            group.forgetRequests();resetRequest?.let(NativeSessionHost::forgetWindowOperation);owner?.let(NativeSessionHost::finishWindowOperation)
            groupMenu=null;owner=null;capture=null;recenter=null;resetRequest=null;return
        }
        closeView()
        if(view!=null) return // Keep the single owner and block direct input until SDK resources are actually released.
        val c=capture
        if(phase==WindowSizeCapture.Phase.COMPLETE) completed++ else if(c!=null) cancelled++
        lastPhase=phase?.name ?: "CANCELLED";lastReason=recenter?.reason ?: c?.reason ?: "open_failed";lastRequest=recenter?.request ?: c?.request
        recenter?.request?.let(NativeSessionHost::forgetWindowOperation)
        c?.request?.let(NativeSessionHost::forgetWindowOperation);resetRequest?.let(NativeSessionHost::forgetWindowOperation)
        owner?.let(NativeSessionHost::finishWindowOperation)
        owner=null;capture=null;recenter=null;resetRequest=null
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
    fun cancelForGroup(state:JSONObject):Boolean {
        groupMenu?.let {return it.canApplyGroup(state)}
        if(capture==null && recenter==null && view==null) return true
        capture?.cancel("group_owned");recenter?.cancel("group_owned");end();return view==null
    }
    fun metadata()=JSONObject().put("target",JSONObject(target)).put("revision",revision).put("phase",phase?.name ?: lastPhase).put("reason",recenter?.reason ?: capture?.reason ?: lastReason).put("request_id",recenter?.request ?: capture?.request ?: lastRequest ?: JSONObject.NULL).put("action",lastAction).put("recenter_enabled",WindowRecenterPolicy.ENABLED)
        .put("recenter_read_ms",recenterReadMs ?: JSONObject.NULL).put("recenter_read_reason",recenterReadReason ?: JSONObject.NULL).put("tracking_freshness","not_exposed")
        .put("group_menu",groupMenu?.metadata() ?: lastGroup ?: JSONObject.NULL).put("group_actions_accepted",groupAccepted)
        .put("opened",opened).put("completed",completed).put("cancelled",cancelled).put("rejected",rejected).put("view",view?.metadata())
    override fun close() {closed=true;capture?.cancel("owner_disposed");recenter?.cancel("owner_disposed");end();check(view==null && groupMenu==null) {"MENU_CLEANUP_PENDING"}}
    companion object {
        private fun same(a:JSONObject,b:JSONObject)=listOf("session","window","scene","generation").all {a.optString(it)==b.optString(it)}
        private fun identity(ticket:NativeSessionHost.WindowOperationTicket)=WindowSizeIdentity(ticket.control.runtime,ticket.control.target,ticket.control.revision,ticket.control.intent)
        private fun size(value:JSONObject)=WindowPhysicalSize(value.getDouble("width_m").toFloat(),value.getDouble("height_m").toFloat())
        private fun command(sizing:JSONObject)=if(sizing.isNull("size_command")) null else sizing.getString("size_command")
        private fun pixels(window:JSONObject,plan:JSONObject?)=JSONObject().put("logical",window.opt("logical_size")).put("producer",window.getJSONObject("sizing").opt("producer_size_px"))
            .put("canvas",plan?.optJSONObject("decoration")?.opt("canvas")).toString()
    }
}
