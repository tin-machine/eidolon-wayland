package io.github.tinmachine.eidolon

import android.os.SystemClock
import com.meta.spatial.core.Entity
import com.meta.spatial.runtime.Scene
import com.meta.spatial.toolkit.SceneObjectSystem
import org.json.JSONObject

/** Owns only the one menu page. Native owns the subsequent 500ms transition and all geometry receipts. */
internal class WindowGroupMenuController(private val parent:Entity,private val layout:WindowControlLayout,
    private val width:Double,private val height:Double,private val sdk:Scene,private val objects:SceneObjectSystem,
    private val ticket:NativeSessionHost.WindowOperationTicket,permit:WindowGroupMenuPermit,initialEpoch:Long,
    private val live:()->Boolean) {
    private val identity=WindowGroupMenuIdentity(permit.target,permit.controlsRevision,ticket.control.intent)
    private val capture=WindowGroupMenuCapture(identity,permit,initialEpoch,SystemClock.elapsedRealtime())
    private var resetRequest:String?=null
    private var view:WindowMenuView?=null
    private var initialized=false
    private var sdkFailures=0L
    val phase get()=capture.phase
    val reason get()=capture.reason
    val request get()=capture.request
    val menuPhase:WindowSizeCapture.Phase get()=when(capture.phase) {
        WindowGroupMenuCapture.Phase.RESETTING->WindowSizeCapture.Phase.RESETTING
        WindowGroupMenuCapture.Phase.MENU->WindowSizeCapture.Phase.MENU
        WindowGroupMenuCapture.Phase.CLEANUP,WindowGroupMenuCapture.Phase.SUBMITTING,WindowGroupMenuCapture.Phase.COMMITTING->WindowSizeCapture.Phase.COMMITTING
        else->WindowSizeCapture.Phase.CANCELLED // A command ACK is never a geometry-complete size operation.
    }
    val terminal get()=phase in setOf(WindowGroupMenuCapture.Phase.ACCEPTED,WindowGroupMenuCapture.Phase.DISMISSED,WindowGroupMenuCapture.Phase.CANCELLED)
    fun initialize() {
        check(!initialized);initialized=true
        resetRequest=NativeSessionHost.resetWindowOperation(ticket)
        if(resetRequest==null) capture.cancel("reset_enqueue_rejected")
    }
    private fun current(state:JSONObject)=live() && NativeSessionHost.windowOperationCurrent(ticket) &&
        state.optString("state")=="Ready" && state.optBoolean("activity_visible") && state.optString("xr_state")=="FOCUSED" &&
        state.optString("scene")==identity.target.scene && state.optJSONObject("render")?.optLong("theme_revision",-1)==identity.controlsRevision
    fun tick(state:JSONObject) {
        val now=SystemClock.elapsedRealtime();val ownerCurrent=current(state)
        if(!ownerCurrent) capture.cancel("owner_changed")
        if(phase==WindowGroupMenuCapture.Phase.COMMITTING) {
            val id=checkNotNull(capture.request)
            capture.commandObserved(identity,now,ownerCurrent,id,NativeSessionHost.windowOperationCompletion(id))
        } else if(!terminal) {
            val permit=NativeSessionHost.windowGroupMenuPermit(ticket,state)
            capture.current(identity,now,permit)
            if(phase==WindowGroupMenuCapture.Phase.RESETTING) capture.resetObserved(identity,now,permit,
                resetRequest?.let(NativeSessionHost::windowOperationCompletion),NativeSessionHost.windowOperationAndroidReleased(ticket),state.getLong("input_resets"))
            if(phase==WindowGroupMenuCapture.Phase.MENU && view==null) {
                val items=capture.permit.actions.map {action->WindowMenuItem(action,when(action) {
                    "collapse"->"まとめる";"expand"->"展開する";else->"前面を切替"
                })}+WindowMenuItem("back","戻る")
                try {
                    view=WindowMenuView(parent,layout,width,height,sdk,objects,
                        {live() && phase==WindowGroupMenuCapture.Phase.MENU && NativeSessionHost.windowOperationCurrent(ticket)},
                        {NativeSessionHost.windowOperationCurrent(ticket)},false,false,items) {choose(it)}
                    checkNotNull(view).initialize()
                } catch(_:Exception) {capture.cancel("menu_create_failed");sdkFailures++}
            }
            runCatching {view?.update()}.onFailure {capture.cancel("menu_pose_failed");sdkFailures++}
            if(view?.metadata()?.optLong("failures",0)?.let {it>0}==true) capture.cancel("menu_sdk_failed")
            if(phase==WindowGroupMenuCapture.Phase.CLEANUP) {
                val released=closeView()
                if(capture.cleaned(identity,SystemClock.elapsedRealtime(),permit,released) && NativeSessionHost.submitWindowGroupMenu(ticket,capture)==null) capture.cancel("group_enqueue_rejected")
            }
        }
        if(terminal) closeView()
    }
    private fun choose(action:String) {
        val control=ticket.control
        val state=runCatching {JSONObject(NativeBridge.nativeSnapshot(control.runtime)).getJSONObject("runtime")}.getOrNull()
        if(state==null || !current(state)) {capture.cancel("owner_changed");return}
        capture.choose(identity,SystemClock.elapsedRealtime(),NativeSessionHost.windowGroupMenuPermit(ticket,state),action)
    }
    private fun closeView():Boolean {
        val old=view ?: return true
        try {old.close();view=null} catch(_:Exception) {capture.cancel("menu_cleanup_failed");sdkFailures++;NativeSessionHost.windowOperationPhase(ticket,"cleanup")}
        return view==null
    }
    fun canApplyGroup(state:JSONObject):Boolean {
        if(!current(state)) capture.cancel("owner_changed")
        else if(phase!=WindowGroupMenuCapture.Phase.COMMITTING && !terminal) {
            capture.current(identity,SystemClock.elapsedRealtime(),NativeSessionHost.windowGroupMenuPermit(ticket,state))
        }
        if(terminal) closeView()
        return !terminal || view==null
    }
    fun cancel(reason:String):Boolean {capture.cancel(reason);return closeView()}
    fun released()=view==null
    fun forgetRequests() {check(view==null);resetRequest?.let(NativeSessionHost::forgetWindowOperation);capture.request?.let(NativeSessionHost::forgetWindowOperation)}
    fun metadata()=JSONObject().put("phase",phase.name).put("reason",reason).put("action",capture.action ?: JSONObject.NULL)
        .put("request_id",capture.request ?: JSONObject.NULL).put("group",capture.permit.group ?: JSONObject.NULL).put("revision",capture.permit.revision ?: JSONObject.NULL)
        .put("sdk_failures",sdkFailures).put("geometry_complete",false).put("view",view?.metadata() ?: JSONObject.NULL)
}
