package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject

/** 観測だけを行う。入力本文・profile/pins・credentialを取得せず、物理入力の成功とは扱わない。 */
internal class NormalLinuxGtkTrace(val runtimeHandle:Long,val scene:String,val session:String,val launchRequest:String) {
    private val owned=linkedSetOf<GtkFixtureBinding.Target>()
    private val dialogs=linkedMapOf<GtkFixtureBinding.Target,String>()
    private val observations=linkedSetOf<String>()
    private var main:GtkFixtureBinding.Target?=null
    private var inputBaseline:Long?=null
    private var inputLast=0L
    private var pointerBaseline:Long?=null
    private var pointerLast=0L
    private var firstContent:Long?=null
    private var lastContent=0L
    private var remoteNonce:String?=null
    var mainClosed=false;private set
    var targetsRetired=false;private set
    var cleanupComplete=false;private set
    private var cleanup=JSONObject()
    private var adapterEmpty=false
    private var naturalExit=false
    private var startObserved=false
    init {
        require(runtimeHandle>0 && listOf(scene,session,launchRequest).all {it.matches(Regex("[1-9][0-9]{0,18}")) && it.toLongOrNull()!=null})
    }
    private fun rows(value:JSONArray)=(0 until value.length()).map {value.getJSONObject(it)}
    fun observe(handle:Long,runtime:JSONObject,adapter:JSONObject?) {
        check(handle==runtimeHandle && runtime.getString("scene")==scene) {"LINUX_OWNER_CHANGED"}
        val transport=runtime.getJSONObject("transport")
        check(transport.getString("session")==session && transport.getString("profile_id")==LinuxConnectionProfile.ID) {"LINUX_SESSION_CHANGED"}
        val desktop=transport.optJSONObject("desktop")
        if(desktop!=null && !desktop.isNull("request_id")) check(desktop.getString("request_id")==launchRequest) {"LINUX_REQUEST_CHANGED"}
        if(desktop?.optBoolean("start_requested")==true || desktop?.optBoolean("started")==true) startObserved=true
        desktop?.getString("nonce")?.let {nonce ->
            check(nonce.matches(Regex("[0-9a-f]{16}")) && (remoteNonce==null || remoteNonce==nonce)) {"LINUX_REMOTE_OWNER_CHANGED"}
            remoteNonce=nonce
        }
        val app=runtime.optJSONObject("app") ?: return
        check(app.getString("session")==session && app.getString("mode")=="linux_desktop") {"LINUX_APP_CHANGED"}
        val windows=rows(app.getJSONArray("windows"))
        val admitted=app.getJSONArray("gui_windows").let {values -> (0 until values.length()).map {values.getString(it)}.toSet()}
        val mapped=windows.filter {it.optString("life")=="Mapped" && it.getString("window") in admitted}
        val roots=mapped.filter {it.isNull("parent")}
        check(roots.size<=1) {"LINUX_MAIN_AMBIGUOUS"}
        if(app.optBoolean("remote_gui_verified") && roots.isNotEmpty()) {
            val root=roots.single()
            check(main==null || main?.window==root.getString("window")) {"LINUX_MAIN_CHANGED"}
            for(window in listOf(root)+mapped.filterNot {it===root}) {
                val parent=window.optString("parent").takeUnless {window.isNull("parent")}
                check(parent==null && window===root || parent==root.getString("window")) {"LINUX_DIALOG_PARENT_CHANGED"}
                val target=GtkFixtureBinding.targetFor(runtime,window.getString("window")) ?: continue
                check(target.session==session && target.scene==scene) {"LINUX_TARGET_CHANGED"}
                val sdk=adapter?.takeIf {it.optString("scene")==scene}?.optJSONArray("targets")?.let(::rows)?.singleOrNull {
                    it.optString("runtime")==runtimeHandle.toString() && GtkFixtureBinding.target(it.getJSONObject("target"))==target &&
                        !it.optBoolean("retiring") && !it.optBoolean("sdk_disposed") && !it.optBoolean("disposal_failed") && it.optBoolean("listener_owned")
                } ?: continue
                check(sdk.getJSONObject("target").getString("generation")==target.generation)
                owned.add(target)
                if(parent==null) {
                    main=target;observations.add("main_mapped")
                    val rendered=rows(runtime.getJSONObject("render").getJSONArray("targets")).single {
                        GtkFixtureBinding.target(it.getJSONObject("key"))==target
                    }
                    val content=rendered.getString("content_frame").toLong()
                    check(content>0 && content>=lastContent) {"LINUX_FRAME_REGRESSED"}
                    if(firstContent==null) firstContent=content
                    lastContent=content
                    if(content>checkNotNull(firstContent)) observations.add("frame_progress")
                } else {dialogs[target]=parent;observations.add("dialog_mapped")}
            }
        }
        if(main!=null && app.optString("state")!="stopped") {
            val input=app.getJSONObject("input")
            val delivered=input.getLong("delivered");val buttons=input.getJSONObject("pointer").getLong("buttons")
            check(delivered>=inputLast && buttons>=pointerLast) {"LINUX_INPUT_REGRESSED"}
            if(inputBaseline==null) {inputBaseline=delivered;pointerBaseline=buttons}
            inputLast=delivered;pointerLast=buttons
            if(delivered>checkNotNull(inputBaseline)) observations.add("input_delivered")
        }
        if(main!=null && windows.none {it.optString("window")==main?.window && it.optString("life")=="Mapped"} && app.optString("state")!="running") {
            mainClosed=true;observations.add("main_closed")
        }
        targetsRetired=owned.isNotEmpty() && GtkFixtureBinding.retired(runtime,adapter,runtimeHandle,scene,owned,emptySet())
        if(targetsRetired) observations.add("all_targets_retired")
        val local=app.getJSONObject("local")
        val remote=desktop?.optJSONObject("remote")
        naturalExit=desktop!=null && !desktop.optBoolean("cancelled",true) && desktop.isNull("error") && remote?.optString("reason")=="process_exited"
        val localComplete=app.optString("state")=="stopped" && app.optBoolean("directory_cleaned") &&
            local.optInt("workers",-1)==0 && local.optInt("owned_groups",-1)==0 &&
            (local.optBoolean("waited") || app.optBoolean("startup_failed") && app.optBoolean("startup_cleanup_complete")) &&
            listOf("clients","objects","surfaces","pool_bytes","pool_count","pixel_bytes").all {app.optLong(it,-1)==0L}
        val remoteComplete=desktop?.optBoolean("finished")==true && desktop.optBoolean("channel_complete") && desktop.optBoolean("router_drained") &&
            !desktop.optBoolean("journal_pending") && remote?.optBoolean("process_complete")==true && remote.optBoolean("pipes_eof") &&
            remote.optBoolean("remote_cleaned") && remote.optBoolean("receipt_written") && remote.optBoolean("protocol_valid")
        val disconnected=transport.optString("state")=="Disconnected" && transport.optInt("owned_connections",-1)==0 && !transport.optBoolean("desktop_pending")
        cleanupComplete=mainClosed && targetsRetired && localComplete && remoteComplete && disconnected
        cleanup=JSONObject().put("local_complete",localComplete).put("remote_complete",remoteComplete).put("connection_closed",disconnected)
            .put("desktop_pending",transport.optBoolean("desktop_pending")).put("sdk_bindings",runtime.getJSONObject("shutdown").getInt("sdk_bindings"))
            .put("render_targets",runtime.getJSONObject("render").getJSONArray("targets").length()).put("panels",runtime.getJSONArray("panels").length())
            .put("board_occupied",runtime.getJSONObject("window_slots").getInt("occupied"))
        adapterEmpty=adapter?.optString("scene")==scene && adapter.optJSONArray("targets")?.length()==0
        if(cleanupComplete) observations.add("cleanup_complete")
    }
    /** 取消後のobserver解放用。未観測targetの退役やGUI操作の成功を補完しない。 */
    fun resourcesCleared()=adapterEmpty && cleanup.optBoolean("local_complete") && cleanup.optBoolean("remote_complete") &&
        cleanup.optBoolean("connection_closed") && !cleanup.optBoolean("desktop_pending") &&
        listOf("sdk_bindings","render_targets","panels","board_occupied").all {cleanup.optInt(it,-1)==0}
    fun hasLaunchEvidence()=startObserved || owned.isNotEmpty()
    fun complete()=cleanupComplete && naturalExit && dialogs.isNotEmpty() && observations.containsAll(setOf("main_mapped","frame_progress","input_delivered","dialog_mapped","main_closed","all_targets_retired"))
    fun json():JSONObject=JSONObject().put("main_target",main?.json() ?: JSONObject.NULL)
        .put("remote_nonce",remoteNonce ?: JSONObject.NULL)
        .put("dialog_targets",JSONArray(dialogs.map {(target,parent)->JSONObject().put("target",target.json()).put("parent",parent)}))
        .put("targets",JSONArray(owned.map {it.json()})).put("input_baseline",inputBaseline ?: JSONObject.NULL).put("input_last",inputLast)
        .put("input_delta",inputBaseline?.let {inputLast-it} ?: 0).put("pointer_baseline",pointerBaseline ?: JSONObject.NULL).put("pointer_last",pointerLast)
        .put("pointer_delta",pointerBaseline?.let {pointerLast-it} ?: 0).put("first_content_frame",firstContent ?: JSONObject.NULL).put("last_content_frame",lastContent)
        .put("frame_progress",observations.contains("frame_progress")).put("dialog_parent_verified",dialogs.isNotEmpty())
        .put("main_closed",mainClosed).put("targets_retired",targetsRetired).put("cleanup_complete",cleanupComplete).put("cleanup",cleanup)
        .put("observations",JSONArray(observations.toList()))
}
