package io.github.tinmachine.eidolon

import android.os.Build
import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.view.KeyEvent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.ComposeView
import com.meta.spatial.compose.ComposeFeature
import com.meta.spatial.compose.ComposeViewPanelRegistration
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Pose
import com.meta.spatial.core.SpatialFeature
import com.meta.spatial.core.Vector3
import com.meta.spatial.runtime.SceneObject
import com.meta.spatial.runtime.ReferenceSpace
import com.meta.spatial.runtime.SessionState
import com.meta.spatial.toolkit.AppSystemActivity
import com.meta.spatial.toolkit.DpPerMeterDisplayOptions
import com.meta.spatial.toolkit.Panel
import com.meta.spatial.toolkit.PanelRegistration
import com.meta.spatial.toolkit.PanelStyleOptions
import com.meta.spatial.toolkit.QuadShapeOptions
import com.meta.spatial.toolkit.Transform
import com.meta.spatial.toolkit.UIPanelSettings
import com.meta.spatial.vr.VRFeature
import org.json.JSONArray

/** XR診断パネル。nativeとAPKの版整合をここでも表示する。 */
class MainActivity : AppSystemActivity() {
    private class GripBinding(val obj: SceneObject, val grip: PanelGrip) {
        private val listener = obj.addInputListener(grip)
        fun close() {
            grip.dispose()
            obj.removeInputListener(listener)
        }
    }
    private val fixedPanelGrips = mutableMapOf<Int, GripBinding>()
    private var staticPanelsFaced = false
    private fun bindGrip(id: Int, obj: SceneObject) {
        val entity = obj.entity ?: return
        fixedPanelGrips.remove(id)?.close()
        val grip = PanelGrip(entity, { runCatching { scene.getViewerPose().t }.getOrNull() },
            { ownsScene() && resumed && observedXr == "FOCUSED" && fixedPanelGrips[id]?.obj === obj })
        fixedPanelGrips[id] = GripBinding(obj, grip)
    }
    private var panel: Entity? = null
    private var launcher: Entity? = null
    private val keyboardPanels = mutableListOf<PanelKeyboardBridge>()
    private val deviceMetrics by lazy { DeviceMetrics(applicationContext,NativeSessionHost::publishDeviceMetrics) }
    private val activityOwner = NativeSessionHost.registerActivityOwner()
    private var sceneEpoch=0L
    private var resumed=false
    private val activityHandler = android.os.Handler(android.os.Looper.getMainLooper())
    private val guiHeartbeat = object : Runnable {
        override fun run() {
            if (!resumed) return
            if (ownsScene()) NativeSessionHost.guiLiveness(sceneEpoch, activityOwner)
            activityHandler.postDelayed(this, 10_000)
        }
    }
    private var xrScene: com.meta.spatial.runtime.Scene?=null
    private var observedXr="IDLE"
    private fun ownsScene(): Boolean = !isDestroyed && NativeSessionHost.ownsActivityScene(activityOwner,sceneEpoch)
    private val hud by lazy { QuestStatusHud(applicationContext, activityOwner) }
    private val mousepadAnchor by lazy { MousepadAnchor(applicationContext, activityOwner) }
    private val nativePanels by lazy { SpatialPanelAdapter(mousepadAnchor = mousepadAnchor) }
    private val keyboard: PhysicalKeyboard by lazy {
        PhysicalKeyboard(this, onOpenLauncher = { spatialLauncher.open() }) { TerminalImeController.toggle() }
    }
    private val mouse by lazy { PhysicalMouse(applicationContext, nativePanels) }
    private val openDetails = mutableIntStateOf(0)
    private var handExtensions = emptySet<String>()
    private val spatialLauncher: SpatialLauncher by lazy {
        SpatialLauncher(applicationContext, ::ownsScene, { view, window ->
            mouse.track(view)
            PanelKeyboardBridge(view, window, linuxKeys = true) { event ->
                ownsScene() && (mouse.key(event) || keyboard.dispatch(event, "spatial-launcher"))
            }
        }, { handExtensions }) { openDetails.intValue += 1 }
    }
    private var themePickerToken: String? = null

    override fun registerFeatures(): List<SpatialFeature> =
        listOf(VRFeature(this), ComposeFeature())

    override fun registerRequiredOpenXRExtensions(): List<String> {
        val defaults = super.registerRequiredOpenXRExtensions()
        if (!NativeBridge.status.ready) return defaults
        val anchorExtensions = try {
            val extensions = JSONArray(NativeBridge.nativeAnchorRequiredExtensions(applicationContext))
            List(extensions.length()) { extensions.getString(it) }
        } catch (_: Exception) {
            emptyList()
        } catch (_: LinkageError) {
            emptyList()
        }
        handExtensions = (defaults + anchorExtensions).filter { it.contains("hand_tracking") }.toSet()
        Log.i("EidolonWrist", "joint_extension=${"XR_EXT_hand_tracking" in handExtensions} " +
            "aim_extension=${"XR_FB_hand_tracking_aim" in handExtensions}")
        return (defaults + anchorExtensions).distinct()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        themePickerToken = savedInstanceState?.getString("theme_picker_token")
        Log.i(TAG, "activity_created version=${BuildConfig.VERSION_CODE} api=${Build.VERSION.SDK_INT}")
        val native = NativeBridge.status
        NativeSessionHost.initialize(applicationContext)
        nativePanels.initialize(this)
        TermuxImeWindows.attach(window)
        mouse.track(window.decorView)
        if (native.ready) {
            Log.i(TAG, "native_ready version=${BuildConfig.VERSION_CODE} info=${native.info}")
        } else {
            Log.e(TAG, "native_failed version=${BuildConfig.VERSION_CODE} error=${native.error}")
        }
        TerminalImeController.activity(this)
        BuildHooks.imeProbeActivity(this)
        BuildHooks.accept(intent,nativePanels,applicationContext,{scene},{recreate()})
    }

    override fun onSceneReady() {
        super.onSceneReady()
        val epoch = NativeSessionHost.sceneReady(activityOwner)
        if (epoch <= 0) return
        sceneEpoch=epoch
        if(resumed) keyboard.resume()
        NativeSessionHost.setActivityVisible(resumed,epoch,activityOwner)
        NativeSessionHost.setXrState(if(xrScene === scene) observedXr else "IDLE",epoch,activityOwner)
        scene.setReferenceSpace(ReferenceSpace.LOCAL_FLOOR)
        scene.setViewOrigin(0f, 0f, 0f, 0f)
        val eye = runCatching { scene.getViewerPose().t }.getOrNull()
        fun initial(center: Vector3) = eye?.let { ViewerPanelPlacement.facing(it, center) } ?: Pose(center)
        if (panel == null) {
            panel = Entity.create(listOf(
                Panel(R.id.phase0_panel),
                Transform(initial(Vector3(CONTROL_X, CONTROL_Y, CONTROL_Z))),
            ))
        }
        if(launcher==null) {
            launcher=Entity.create(listOf(
                Panel(R.id.terminal_launcher_panel),Transform(initial(Vector3(-.80f,1.05f,1.75f))),
            ))
        }
        mousepadAnchor.bind(scene, epoch)
        nativePanels.sceneReady(epoch, scene)
        spatialLauncher.bind(scene)
        hud.bind(scene, epoch)
        hud.setActive(resumed && observedXr in setOf("VISIBLE", "FOCUSED"))
        refreshMouseScene()
        TerminalImeController.sceneReady {scene}
        BuildHooks.sceneReady(nativePanels,{scene},{recreate()})
        Log.i(TAG, "scene_ready version=${BuildConfig.VERSION_CODE} panel=${panel?.id}")
    }

    override fun registerPanels(): List<PanelRegistration> = listOf(
        ComposeViewPanelRegistration(
            R.id.phase0_panel,
            composeViewCreator = { _, context ->
                Log.i(TAG, "panel_created version=${BuildConfig.VERSION_CODE}")
                ComposeView(context).apply {
                    setContent {
                        MaterialTheme {
                            ControlPanel(
                                ::chooseTheme, ::allowTermux, ::openTermux, mouse.enabled.value,
                                { if (ownsScene()) mouse.toggle() },
                                { hud.enabled.value }, hud::toggle, hud::recenter, { hud.rotation.value }, hud::toggleRotation,
                                hud.preset.value, hud::selectPreset,
                                launcherControls = { spatialLauncher.Presets() },
                                openDetailsRequest = openDetails.intValue,
                            ) { restoring -> MousepadAnchorControls(mousepadAnchor, restoring) }
                        }
                    }
                }
            },
            settingsCreator = {
                UIPanelSettings(
                    shape = QuadShapeOptions(width = 1.2f, height = 0.8f),
                    display = DpPerMeterDisplayOptions(),
                    style = PanelStyleOptions(themeResourceId = R.style.EidolonTheme),
                )
            },
            panelSetupWithComposeView = { view, obj, _ -> view.post {
                if(ownsScene()) obj.display?.presentation?.window?.let { window ->
                    bindGrip(R.id.phase0_panel, obj)
                    mouse.track(view)
                    keyboardPanels.add(PanelKeyboardBridge(view,window, linuxKeys = true) {event -> ownsScene() && (mouse.key(event) || keyboard.dispatch(event,"control"))})
                }
            } },
        ),
        ComposeViewPanelRegistration(
            R.id.terminal_launcher_panel,
            composeViewCreator={_,context -> ComposeView(context).apply {setContent {
                MaterialTheme {
                    LauncherStrip(spatialLauncher::open)
                }
            }}},
            settingsCreator={UIPanelSettings(shape=QuadShapeOptions(width=1.05f,height=.22f),
                display=DpPerMeterDisplayOptions(),style=PanelStyleOptions(themeResourceId=R.style.EidolonTheme))},
            panelSetupWithComposeView = { view, obj, _ -> view.post {
                if(ownsScene()) obj.display?.presentation?.window?.let { window ->
                    bindGrip(R.id.terminal_launcher_panel, obj)
                    mouse.track(view)
                    keyboardPanels.add(PanelKeyboardBridge(view,window, linuxKeys = true) {event -> ownsScene() && (mouse.key(event) || keyboard.dispatch(event,"launcher"))})
                }
            } },
        ),
    ) + hud.registration + spatialLauncher.registrations + nativePanels.registration + nativePanels.decorationRegistration + TerminalImeController.registrations() + BuildHooks.additionalPanels()

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        TerminalImeController.activity(this)
        BuildHooks.imeProbeActivity(this)
        BuildHooks.accept(intent,nativePanels,applicationContext,{scene},{recreate()})
    }

    @Suppress("DEPRECATION")
    private fun chooseTheme() {
        if (themePickerToken != null) return
        NativeSessionHost.beginThemeImport { token ->
            if (isDestroyed || isFinishing) { NativeSessionHost.cancelThemeImport(token); return@beginThemeImport }
            themePickerToken = token
            try {
                startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                    addCategory(Intent.CATEGORY_OPENABLE); type = "application/zip"
                    putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("application/zip", "application/x-zip-compressed", "application/octet-stream"))
                }, THEME_PICKER)
            } catch (_: android.content.ActivityNotFoundException) {
                themePickerToken = null; NativeSessionHost.cancelThemeImport(token)
            }
        }
    }
    override fun onSaveInstanceState(outState: Bundle) {
        outState.putString("theme_picker_token", themePickerToken)
        super.onSaveInstanceState(outState)
    }
    @Deprecated("Activity result bridge required by Spatial activity")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != THEME_PICKER) return
        val token = themePickerToken ?: return
        themePickerToken = null
        val uri = data?.data
        if (resultCode == RESULT_OK && uri != null) NativeSessionHost.readTheme(applicationContext,uri,token)
        else NativeSessionHost.cancelThemeImport(token)
    }

    private fun allowTermux() {
        NativeSessionHost.resetInput()
        if(checkSelfPermission(TermuxStarter.PERMISSION)!=android.content.pm.PackageManager.PERMISSION_GRANTED)
            requestPermissions(arrayOf(TermuxStarter.PERMISSION),4202)
    }
    private fun openTermux() {
        NativeSessionHost.resetInput()
        packageManager.getLaunchIntentForPackage("com.termux")?.let {
            try {startActivity(it)} catch(_:android.content.ActivityNotFoundException) { }
        }
    }
    override fun onResume() {
        super.onResume()
        deviceMetrics.start()
        resumed=true
        hud.setActive(ownsScene() && observedXr in setOf("VISIBLE", "FOCUSED"))
        if(ownsScene()) {keyboard.resume();NativeSessionHost.setActivityVisible(true,sceneEpoch,activityOwner)}
        activityHandler.removeCallbacks(guiHeartbeat)
        activityHandler.post(guiHeartbeat)
        refreshMouseScene()
        Log.i(TAG, "activity_resumed version=${BuildConfig.VERSION_CODE}")
    }

    override fun onPause() {
        fixedPanelGrips.values.forEach { it.grip.cancel() }
        deviceMetrics.stop()
        resumed=false
        activityHandler.removeCallbacks(guiHeartbeat)
        hud.setActive(false)
        spatialLauncher.setActive(false)
        mouse.setFocusedScene(null)
        val current=ownsScene()
        if(current) TerminalImeController.pause()
        keyboard.pause(resetInput=current)
        if(current) NativeSessionHost.setActivityVisible(false,sceneEpoch,activityOwner)
        Log.i(TAG, "activity_paused version=${BuildConfig.VERSION_CODE}")
        super.onPause()
    }

    fun dispatchImePanelKey(event: KeyEvent, source: String): Boolean = ownsScene() && keyboard.dispatch(event,source)
    fun resetImeShortcut() { keyboard.resetShortcutState() }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean =
        (ownsScene() && (mouse.key(event) || keyboard.dispatch(event))) || super.dispatchKeyEvent(event)

    override fun onPointerCaptureChanged(hasCapture: Boolean) {
        super.onPointerCaptureChanged(hasCapture)
        mouse.focusChanged()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        mouse.focusChanged()
        if (!hasFocus && ownsScene() && !TerminalImeController.awaitingEditorFocus()) {
            keyboard.resetShortcutState()
            TerminalImeController.externalLoss()
            NativeSessionHost.resetInput()
        }
    }

    override fun onSessionStateChanged(state: SessionState) {
        super.onSessionStateChanged(state)
        xrScene=runCatching {scene}.getOrNull()
        observedXr=when (state) {
            SessionState.FOCUSED -> "FOCUSED"
            SessionState.VISIBLE -> "VISIBLE"
            else -> "IDLE"
        }
        if(!ownsScene()) return
        hud.setActive(resumed && observedXr in setOf("VISIBLE", "FOCUSED"))
        Log.i(TAG, "xr_state=$state version=${BuildConfig.VERSION_CODE}")
        if(state != SessionState.FOCUSED) { keyboard.resetShortcutState();TerminalImeController.externalLoss() }
        NativeSessionHost.setXrState(observedXr,sceneEpoch,activityOwner)
        refreshMouseScene()
    }

    private fun refreshMouseScene() {
        val focused = ownsScene() && resumed && observedXr == "FOCUSED"
        if (focused && !staticPanelsFaced) {
            val eye = runCatching { scene.getViewerPose().t }.getOrNull()
            if (eye != null && panel != null && launcher != null) {
                val panels = listOfNotNull(panel, launcher)
                val poses = panels.map { ViewerPanelPlacement.facing(eye, it.getComponent<Transform>().transform.t) }
                if (poses.all { it != null }) {
                    panels.zip(poses).forEach { (body, pose) -> body.setComponent(Transform(checkNotNull(pose))) }
                    staticPanelsFaced = true
                }
            }
        }
        if (!focused) fixedPanelGrips.values.forEach { it.grip.cancel() }
        mouse.setFocusedScene(xrScene.takeIf { focused })
        spatialLauncher.setActive(focused)
    }

    override fun onSpatialShutdown() {
        fixedPanelGrips.values.forEach { it.close() }
        fixedPanelGrips.clear()
        staticPanelsFaced = false
        mousepadAnchor.close()
        hud.close()
        spatialLauncher.close()
        mouse.setFocusedScene(null)
        NativeSessionHost.sceneClosing(activityOwner,sceneEpoch)
        xrScene=null;observedXr="IDLE"
        TerminalImeController.sceneClosing(this)
        BuildHooks.sceneClosing()
        keyboardPanels.forEach {it.close()};keyboardPanels.clear()
        nativePanels.close()
        launcher?.destroy();launcher=null
        panel?.destroy()
        panel = null
        Log.i(TAG, "spatial_shutdown version=${BuildConfig.VERSION_CODE}")
        super.onSpatialShutdown()
    }

    companion object {
        internal const val CONTROL_X = -1.45f
        internal const val CONTROL_Y = 1.4f
        internal const val CONTROL_Z = 1.4f
        private const val TAG = "EidolonPhase0"
        private const val THEME_PICKER = 4201
    }
}
