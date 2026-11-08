package io.github.tinmachine.eidolon

import android.content.Context
import android.graphics.Color
import android.graphics.Rect
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.InputDevice
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Pose
import com.meta.spatial.core.Vector3
import com.meta.spatial.runtime.PanelDisplay
import com.meta.spatial.runtime.Scene
import com.meta.spatial.toolkit.*
import java.lang.ref.WeakReference
import org.json.JSONObject

/** 通常/固定検査で共用するPresentation IME owner。入力本文は診断へ出さない。 */
object TerminalImeController {
    private val ui = Handler(Looper.getMainLooper())
    private val panelId = View.generateViewId()
    private var activity = WeakReference<MainActivity>(null)
    private var sceneReady = false
    private var active: Run? = null
    private var sceneProvider:(()->Scene)?=null
    private var lastProductReport:JSONObject?=null // debug metadata only; no editor body or key events
    val activeState=androidx.compose.runtime.mutableStateOf(false)
    class Probe(val nonce:String,val verifyEditor:(Context)->Boolean,val publish:(JSONObject)->Unit,val done:()->Unit)
    fun activity(value: android.app.Activity) { activity = WeakReference(value as? MainActivity) }
    fun sceneReady(provider:()->Scene) {sceneProvider=provider;sceneReady=true}
    fun sceneClosing(owner:MainActivity) { if(activity.get()===owner) {sceneReady = false;sceneProvider=null; active?.stop("scene_closed", restore = false)} }
    fun pause() { active?.externalLoss("activity_paused") }
    fun toggle():Boolean {
        active?.let {return it.toggle()}
        val scene=sceneProvider?.takeIf {sceneReady && activity.get()!=null} ?: return false
        val owner=NativeSessionHost.beginImeOwner() ?: return false
        Run(null,scene,owner).also {active=it;activeState.value=true;ui.post(it)}
        return true
    }
    fun externalLoss() { active?.externalLoss("external_focus_lost") }
    fun deferActivityFocusLoss(): Boolean = active?.deferActivityFocusLoss() ?: false
    fun startProbe(probe:Probe,scene:()->Scene) {
        check(BuildConfig.DEBUG && active==null)
        Run(probe,scene,null).also {active=it;activeState.value=true;ui.post(it)}
    }
    fun command(name: String, nonce: String) {
        val run = active?.takeIf { it.probe?.nonce == nonce } ?: return
        when(name) {
            "ime-terminal-focus" -> run.focus()
            "ime-terminal-toggle" -> run.toggle()
            "ime-terminal-stop" -> run.stop("operator_stop", restore = true)
            "ime-terminal-status" -> run.publish()
        }
    }
    /** debug receiver only. The regular UI does not poll or retain diagnostic input metadata. */
    fun debugProductState():JSONObject? {
        check(Looper.myLooper()==Looper.getMainLooper())
        if(!BuildConfig.DEBUG) return null
        val current=active
        val value=if(current!=null) {if(current.probe==null) current.debugReport() else JSONObject().put("scope","debug_terminal_ime").put("active",true)}
            else lastProductReport?.let {JSONObject(it.toString())}
        if(value?.optString("scope")=="normal_terminal_ime") {
            val target=value.optJSONObject("target")?.let {ImeSdkTarget(it.getString("session"),it.getString("window"),it.getString("scene"),it.getString("generation"))}
            val runtimeMatches=value.optString("runtime").toLongOrNull()==NativeSessionHost.currentHandle()
            value.put("current_runtime_matches",runtimeMatches)
                .put("captured_input_current",runtimeMatches && NativeSessionHost.imeCaptureCurrent())
                .put("raw_focus_same_target",runtimeMatches && target!=null && NativeSessionHost.debugImeFocusedTarget()==target)
        }
        return value
    }
    fun registrations(): List<PanelRegistration> = listOf(ViewPanelRegistration(panelId,
        dynamicViewCreator = { entity, context -> active?.takeIf { it.entity?.id == entity.id }?.create(context) ?: TextView(context) },
        settingsCreator = { UIPanelSettings(shape = QuadShapeOptions(width=1.05f,height=.48f), display=DpPerMeterDisplayOptions(),
            style=PanelStyleOptions(themeResourceId=R.style.EidolonTheme)) },
        panelSetupWithRootView = { view, obj, entity ->
            val run = active?.takeIf { it.entity?.id == entity.id }
            view.post { if (active === run && run != null) run.ready(view, obj.display) }
        }))

    private class Run(val probe:Probe?,val scene:()->Scene,initialOwner:NativeSessionHost.ImeEditorOwner?):Runnable {
        val started = SystemClock.elapsedRealtime()
        var entity: Entity? = null
        private var root: LinearLayout? = null
        private var label: TextView? = null
        private var display: PanelDisplay? = null
        private var bridge: PanelKeyboardBridge? = null
        private var editor: ImeEditorView? = null
        private val productTarget=initialOwner?.target
        private val productRuntime=initialOwner?.runtime
        private val productRun=initialOwner?.ticket?.toString()
        private var editorOwner=initialOwner
        private var lastMetadata:ImeSessionMetadata?=null
        private var opened = false
        private var focusRequested = false
        private var focusChoice: NativeSessionHost.WindowChoice? = null
        private var focusRequests = 0L
        private var focusReceipt: NativeSessionHost.DebugImeFocusReceipt? = null
        private var focusPending = false
        private var lastExternalLoss: String? = null
        private var closing = false
        private var stopped = false
        private var reason = "preparing"
        private var phase = "WAITING_TARGET"
        private var handoffUntil = 0L
        private var handoffToken: android.os.IBinder? = null
        private var toggles = 0L
        private var focusConnections = 0L
        private var lastGeneration = 0L
        private var requestedStop = false
        private var stopDeadline = 0L
        private var cleanupNoticeDeadline = 0L
        private var downSubmitted = false
        private var upSubmitted = false
        private var tapFailed = false
        private var closedEditorFixture = false

        fun create(context: Context): View = LinearLayout(context).apply {
            closedEditorFixture=probe?.verifyEditor?.invoke(context) ?: true
            orientation=LinearLayout.VERTICAL;setPadding(24,20,24,20);setBackgroundColor(Color.rgb(35,39,55))
            isFocusableInTouchMode=true
            addView(TextView(context).apply {text=if(probe==null) "IME入力 · Ctrl+Spaceで終了" else "端末IMEの検証 · Ctrl+Space";setTextColor(Color.WHITE);textSize=21f})
            addView(TextView(context).apply {text=if(probe==null) "変換方式はQuestで選択したIMEを使用します" else "変換・候補・物理入力の受入れは別途確認";setTextColor(Color.WHITE);textSize=15f})
            label=TextView(context).also { it.setTextColor(Color.WHITE);it.textSize=17f;addView(it) }
            addView(Button(context).apply {text="IME / 直接入力を切替";isFocusable=false;setOnClickListener {toggle()}})
            addView(Button(context).apply {text=if(probe==null) "直接入力へ戻る" else "検証を終了";isFocusable=false;setOnClickListener {stop("panel_stop",true)}})
            root=this
        }
        fun ready(view: View, panel: PanelDisplay?) {
            if(!closedEditorFixture) { stop("closed_editor_fixture_failed",restore=false);return }
            if(stopped || root !== view || panel == null) return
            display=panel
            panel.presentation?.window?.let { window ->
                bridge=PanelKeyboardBridge(view,window) { event -> activity.get()?.dispatchImePanelKey(event,"ime_terminal") ?: false }
            }
            editorOwner?.takeIf {!closing && !requestedStop}?.let {attach(it)}
        }
        private fun captureFocusChoice(): NativeSessionHost.WindowChoice? {
            focusChoice?.let { return it }
            val target=NativeSessionHost.debugImeFocusedTarget()
            val selected=target?.let { NativeSessionHost.WindowChoice(it.session,it.scene,it.window,null) }
                ?: NativeSessionHost.windowChoices.value.singleOrNull()
            if(selected!=null) focusChoice=selected
            return selected
        }
        /** An explicit click-equivalent request only; external loss never retries focus by itself. */
        fun focus(): Boolean {
            if(probe==null || stopped || requestedStop || editorOwner!=null || closing || root?.isAttachedToWindow!=true || display==null || !NativeSessionHost.debugImeCanRequestFocus()) return false
            val choice=captureFocusChoice() ?: return false
            focusRequested=true;focusRequests+=1;focusReceipt=null;focusPending=true
            val requestNumber=focusRequests
            phase="WAITING_TARGET";reason="focus_requested"
            NativeSessionHost.focusTerminal(choice) { receipt -> ui.post {
                if(active===this && !stopped && focusRequests==requestNumber) focusReceipt=receipt
            } }
            publish()
            return true
        }
        fun toggle():Boolean {
            if(stopped || requestedStop || focusPending) return false
            editorOwner?.let {owner->
                toggles+=1
                if(!closing) {closing=true;reason="off_requested";NativeSessionHost.closeImeOwner(owner,true)}
                if(probe==null) requestedStop=true // 遅いPanel/Viewを終了要求後に作らない。
                return true
            }
            if(root?.isAttachedToWindow!=true || display==null || !NativeSessionHost.imeReady()) return false
            val owner=NativeSessionHost.beginImeOwner() ?: return false
            editorOwner=owner;closing=false;toggles+=1;phase="CONNECTING";reason="on_requested"
            attach(owner);publish()
            return true
        }
        private fun attach(owner:NativeSessionHost.ImeEditorOwner) {
            if(editorOwner!==owner || editor!=null || closing || stopped || requestedStop) return
            val container=root ?: return
            var created=WeakReference<ImeEditorView>(null)
            val view=NativeSessionHost.attachImeEditor(owner,container.context,
                preImeKey={event->
                    val sender=created.get()
                    sender!=null && editor===sender && sender.hasFocus() && sender.hasWindowFocus() &&
                        (activity.get()?.dispatchImePanelKey(event,"ime_pre") ?: false)
                },windowFocusChanged={focused->
                    val sender=created.get()
                    if(sender!=null && editor===sender && !focused && !closing && sender.hasFocus()) externalLoss("editor_window_lost")
                })
            if(view==null) {closing=true;reason="editor_attach_failed";NativeSessionHost.closeImeOwner(owner,true);return}
            created=WeakReference(view);editor=view
            container.addView(view,3,LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,104))
            view.post {if(editor===view && !closing && !stopped) tapEditor(view)}
        }
        private fun tapEditor(view: ImeEditorView) {
            val panel=display ?: return
            val location=IntArray(2);view.getLocationOnScreen(location)
            val visible=Rect()
            val x=location[0]+view.width/2;val y=location[1]+view.height/2
            if(!view.isAttachedToWindow || !view.getLocalVisibleRect(visible) || !visible.contains(view.width/2,view.height/2) ||
                view.display?.displayId!=panel.rootView?.display?.displayId || x !in 0 until panel.widthInPx || y !in 0 until panel.heightInPx) {
                tapFailed=true;closing=true;reason="editor_bounds_unavailable";editorOwner?.let {NativeSessionHost.closeImeOwner(it,true)};return
            }
            handoffToken=view.windowToken;handoffUntil=SystemClock.elapsedRealtime()+1000
            val now=SystemClock.uptimeMillis()
            fun send(action: Int) {
                val event=MotionEvent.obtain(now,SystemClock.uptimeMillis(),action,x.toFloat(),y.toFloat(),0)
                try {event.source=InputDevice.SOURCE_CLASS_POINTER;panel.dispatchEvent(event,false)} finally {event.recycle()}
            }
            try {send(MotionEvent.ACTION_DOWN);downSubmitted=true} catch (_: Exception) {tapFailed=true}
            finally {try {send(MotionEvent.ACTION_UP);upSubmitted=true} catch (_: Exception) {tapFailed=true}}
            // No claim that submission means delivery. Probe metadata records the actual window/IC state.
            ui.postDelayed({
                if(editor===view && !closing && (!view.hasWindowFocus() || view.windowToken!==handoffToken || !NativeSessionHost.imeCaptureCurrent()))
                    externalLoss("handoff_not_confirmed")
            },1000)
        }
        fun deferActivityFocusLoss(): Boolean {
            val view=editor ?: return false
            if(closing || stopped || SystemClock.elapsedRealtime()>handoffUntil || handoffToken==null ||
                view.windowToken!==handoffToken || !view.isAttachedToWindow || !NativeSessionHost.imeCaptureCurrent()) return false
            return true // The bounded callback above independently confirms the owned destination.
        }
        fun externalLoss(value: String) {
            if(stopped) return
            reason=value;lastExternalLoss=value;closing=editorOwner!=null;handoffUntil=0;focusRequested=true;focusPending=false;focusReceipt=null
            editorOwner?.let {NativeSessionHost.closeImeOwner(it,false)}
            NativeSessionHost.resetInput() // Also invalidates the captured return ticket.
            activity.get()?.resetImeShortcut()
        }
        fun stop(value: String, restore: Boolean) {
            if(stopped || requestedStop) return
            requestedStop=true;stopDeadline=SystemClock.elapsedRealtime()+9000;reason=value
            closing=true
            editorOwner?.let {NativeSessionHost.closeImeOwner(it,restore)}
            if(!restore) NativeSessionHost.resetInput()
            ui.post(this)
        }
        private fun finish():Boolean {
            if(stopped) return true
            try {
                editor?.closeEditor()
                bridge?.close();bridge=null
                entity?.destroy();entity=null
            } catch(_:Exception) {
                phase="SDK_CLEANUP";reason="sdk_cleanup_pending";publish();return false
            }
            stopped=true;ui.removeCallbacks(this);phase="STOPPED";publish()
            if(BuildConfig.DEBUG && probe==null) lastProductReport=runCatching {debugReport()}.getOrNull()
            if(active===this) {active=null;activeState.value=false;probe?.done?.invoke()}
            return true
        }
        override fun run() {
            if(stopped) return
            try {
                if(probe!=null && SystemClock.elapsedRealtime()-started>600_000) stop("timeout",true)
                if(entity==null && sceneReady && !requestedStop) entity=Entity.create(listOf(Panel(panelId),Transform(Pose(Vector3(1.35f,1.25f,1.55f)))))
                if(probe!=null && editorOwner==null && !requestedStop) {
                    if(focusReceipt?.let(NativeSessionHost::debugImeFocusObserved)==true) focusPending=false
                    if(!focusPending && NativeSessionHost.debugImeReady() && root?.isAttachedToWindow==true && display!=null) {
                        captureFocusChoice();phase="DIRECT_READY";reason="ready"
                    }
                    else if(!opened && NativeSessionHost.windowChoices.value.isEmpty()) {opened=true;NativeSessionHost.openTerminal()}
                    else if(!focusRequested && root?.isAttachedToWindow==true && display!=null && NativeSessionHost.windowChoices.value.isNotEmpty()) focus()
                    if(SystemClock.elapsedRealtime()-started>45_000 && !NativeSessionHost.debugImeReady()) {
                        phase="TARGET_UNAVAILABLE"
                        if(reason in setOf("preparing","ready")) reason="target_not_ready"
                    }
                }
                editorOwner?.let {owner->
                    val view=editor
                    val metadata=NativeSessionHost.imeStatus(owner)
                    if(metadata!=null) lastMetadata=metadata
                    if(view!=null && view.metadata().generation!=lastGeneration) {lastGeneration=view.metadata().generation;focusConnections+=1}
                    phase=metadata?.phase?.name ?: "OWNER_UNAVAILABLE"
                    if(metadata==null) {
                        // Hostは旧runtimeの停止確認後にのみownerを破棄する。
                        view?.closeEditor();root?.removeView(view);editor=null;editorOwner=null;closing=false
                        if(probe==null) {requestedStop=true;stopDeadline=SystemClock.elapsedRealtime()}
                    } else {
                        if(metadata.phase in setOf(ImeSessionPhase.CLOSING,ImeSessionPhase.CLOSED) && !closing) {
                            closing=true
                            val localTimeout=metadata.failure=="ack_timeout" && view?.hasWindowFocus()==true && NativeSessionHost.imeCaptureCurrent()
                            NativeSessionHost.closeImeOwner(owner,localTimeout)
                        }
                        if(metadata.phase==ImeSessionPhase.CLOSED && !metadata.rawBlocked && NativeSessionHost.debugImeAndroidReleased()) {
                            val returning=NativeSessionHost.imeRawReturnStatus();phase=returning?.phase?.name ?: "CLOSED"
                            if(returning==null || returning.phase in setOf(ImeRawReturnPhase.COMPLETE,ImeRawReturnPhase.CANCELLED)) {
                                root?.removeView(view);editor=null;editorOwner=null;closing=false;handoffToken=null
                                activity.get()?.resetImeShortcut()
                                if(probe==null) {requestedStop=true;stopDeadline=SystemClock.elapsedRealtime()}
                            }
                        }
                    }
                }
                if(requestedStop && (editorOwner==null || probe!=null && SystemClock.elapsedRealtime()>=stopDeadline) && finish()) return
                if(probe==null && closing && editorOwner!=null) {
                    if(cleanupNoticeDeadline==0L) cleanupNoticeDeadline=SystemClock.elapsedRealtime()+8000
                    if(SystemClock.elapsedRealtime()>=cleanupNoticeDeadline ||
                        NativeSessionHost.imeRawReturnStatus()?.phase==ImeRawReturnPhase.FAILED ||
                        lastMetadata?.failure=="cleanup_unconfirmed") phase="FAILED"
                }
                label?.text=when(phase) {
                    "DIRECT_READY","COMPLETE" -> "直接入力 · Ctrl+SpaceでIMEへ"
                    "BOUND" -> "IME入力 · Ctrl+Spaceで終了"
                    "WAITING_FOCUS","WAITING_CLEANUP","CLOSING","CLOSED" -> "直接入力への復帰を確認中"
                    "FAILED","TARGET_UNAVAILABLE" -> "入力を停止しています。接続を停止・再開してください"
                    "SDK_CLEANUP" -> "IME画面を閉じています"
                    else -> if(probe==null) "IME入力を準備中" else "準備中 · $phase"
                }
                publish();ui.postDelayed(this,100)
            } catch (_: Exception) {reason="controller_failure";stop("controller_failure",false);ui.postDelayed(this,100)}
        }
        fun publish() {
            val probe=probe ?: return
            probe.publish(debugReport().put("case","ime-terminal").put("nonce",probe.nonce))
        }
        fun debugReport():JSONObject {
            check(BuildConfig.DEBUG)
            val metadata=editorOwner?.let {NativeSessionHost.imeStatus(it)} ?: lastMetadata;val returning=NativeSessionHost.imeRawReturnStatus();val view=editor
            val imm=view?.context?.getSystemService(InputMethodManager::class.java)
            val rawTarget=NativeSessionHost.debugImeFocusedTarget()
            // Capture the Run receiver before JSONObject.apply changes the implicit `this`.
            val productActive=active===this && !stopped
            return JSONObject().put("result",if(stopped) "completed" else "running")
                .put("scope",if(probe==null) "normal_terminal_ime" else "debug_terminal_ime").put("phase",phase).put("reason",reason)
                .apply {if(probe==null) {
                    put("run",productRun ?: JSONObject.NULL).put("runtime",productRuntime?.toString() ?: JSONObject.NULL)
                    put("target",productTarget?.let {JSONObject().put("session",it.session).put("window",it.window).put("scene",it.scene).put("generation",it.generation)} ?: JSONObject.NULL)
                    put("active",productActive).put("stopped",stopped).put("owner_claimed",editorOwner!=null)
                    put("owner_before_view",editorOwner!=null && editor==null && entity==null)
                    put("connection_phase",metadata?.phase?.name ?: JSONObject.NULL)
                    put("raw_focus_same_target",productTarget!=null && productRuntime==NativeSessionHost.currentHandle() && rawTarget==productTarget)
                }}
                .put("toggles",toggles).put("focus_requests",focusRequests)
                .put("focus_observed",focusReceipt?.let(NativeSessionHost::debugImeFocusObserved) ?: false)
                .put("last_external_loss",lastExternalLoss ?: JSONObject.NULL)
                .put("sdk_down_submitted",downSubmitted).put("sdk_up_submitted",upSubmitted).put("sdk_tap_failed",tapFailed)
                .put("editor_attached",view?.isAttachedToWindow ?: false).put("editor_view_focus",view?.hasFocus() ?: false)
                .put("editor_window_focus",view?.hasWindowFocus() ?: false).put("editor_connection_active",view?.metadata()?.active ?: false)
                .put("editor_permanently_closed",view?.isPermanentlyClosed() ?: false).put("closed_editor_fixture_passed",if(probe!=null) closedEditorFixture else JSONObject.NULL)
                .put("editor_imm_active",if(view!=null) imm?.isActive(view) ?: false else false)
                .put("editor_imm_accepting_text",imm?.isAcceptingText ?: false)
                .put("enqueued",metadata?.enqueued ?: 0).put("applied",metadata?.applied ?: 0).put("pending_acks",metadata?.pendingAcks ?: 0)
                .put("native_raw_blocked",metadata?.rawBlocked ?: false).put("android_released",NativeSessionHost.debugImeAndroidReleased())
                .put("raw_return",returning?.phase?.name ?: "not_requested")
                .put("connection_failure",metadata?.failure ?: JSONObject.NULL).put("raw_return_failure",returning?.failure ?: JSONObject.NULL)
                .put("failure",returning?.failure ?: metadata?.failure ?: JSONObject.NULL)
                .put("acceptance",JSONObject().put("actual_ime","not_verified").put("physical_input","not_verified").put("hmd","not_verified"))
        }
    }
}
