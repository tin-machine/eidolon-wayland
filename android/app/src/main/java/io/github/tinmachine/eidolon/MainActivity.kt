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

/** XR診断パネル。nativeとAPKの版整合をここでも表示する。 */
class MainActivity : AppSystemActivity() {
    private var panel: Entity? = null
    private var launcher: Entity? = null
    private val keyboardPanels = mutableListOf<PanelKeyboardBridge>()
    private val deviceMetrics by lazy { DeviceMetrics(applicationContext,NativeSessionHost::publishDeviceMetrics) }
    private var sceneEpoch=0L
    private fun ownsScene(): Boolean = !isDestroyed && (sceneEpoch==0L || sceneEpoch==NativeSessionHost.currentSceneEpoch())
    private val nativePanels by lazy { SpatialPanelAdapter() }
    private val keyboard by lazy { PhysicalKeyboard(this) { BuildHooks.imeTerminalToggle() } }
    private var themePickerToken: String? = null

    override fun registerFeatures(): List<SpatialFeature> =
        listOf(VRFeature(this), ComposeFeature())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        themePickerToken = savedInstanceState?.getString("theme_picker_token")
        Log.i(TAG, "activity_created version=${BuildConfig.VERSION_CODE} api=${Build.VERSION.SDK_INT}")
        val native = NativeBridge.status
        NativeSessionHost.initialize(applicationContext)
        nativePanels.initialize(this)
        if (native.ready) {
            Log.i(TAG, "native_ready version=${BuildConfig.VERSION_CODE} info=${native.info}")
        } else {
            Log.e(TAG, "native_failed version=${BuildConfig.VERSION_CODE} error=${native.error}")
        }
        BuildHooks.imeProbeActivity(this)
        BuildHooks.accept(intent,nativePanels,applicationContext,{scene},{recreate()})
    }

    override fun onSceneReady() {
        super.onSceneReady()
        val epoch = NativeSessionHost.sceneReady()
        sceneEpoch=epoch
        scene.setReferenceSpace(ReferenceSpace.LOCAL_FLOOR)
        scene.setViewOrigin(0f, 0f, 0f, 0f)
        if (panel == null) {
            panel = Entity.create(listOf(
                Panel(R.id.phase0_panel),
                Transform(Pose(Vector3(-1.45f, 1.4f, 1.4f))),
            ))
        }
        if(launcher==null) {
            launcher=Entity.create(listOf(
                Panel(R.id.terminal_launcher_panel),Transform(Pose(Vector3(-.80f,1.05f,1.75f))),
            ))
        }
        nativePanels.sceneReady(epoch, scene)
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
                            ControlPanel(::chooseTheme,::allowTermux,::openTermux)
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
                    keyboardPanels.add(PanelKeyboardBridge(view,window) {event -> ownsScene() && keyboard.dispatch(event,"control")})
                }
            } },
        ),
        ComposeViewPanelRegistration(
            R.id.terminal_launcher_panel,
            composeViewCreator={_,context -> ComposeView(context).apply {setContent {
                MaterialTheme {
                    LauncherStrip()
                }
            }}},
            settingsCreator={UIPanelSettings(shape=QuadShapeOptions(width=1.05f,height=.22f),
                display=DpPerMeterDisplayOptions(),style=PanelStyleOptions(themeResourceId=R.style.EidolonTheme))},
            panelSetupWithComposeView = { view, obj, _ -> view.post {
                if(ownsScene()) obj.display?.presentation?.window?.let { window ->
                    keyboardPanels.add(PanelKeyboardBridge(view,window) {event -> ownsScene() && keyboard.dispatch(event,"launcher")})
                }
            } },
        ),
    ) + nativePanels.registration + nativePanels.decorationRegistration + BuildHooks.additionalPanels()

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
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
        if(ownsScene()) {keyboard.resume();NativeSessionHost.setActivityVisible(true,sceneEpoch.takeIf {it>0})}
        Log.i(TAG, "activity_resumed version=${BuildConfig.VERSION_CODE}")
    }

    override fun onPause() {
        deviceMetrics.stop()
        val current=ownsScene()
        if(current) BuildHooks.imeTerminalPaused()
        keyboard.pause(resetInput=current)
        if(current) NativeSessionHost.setActivityVisible(false,sceneEpoch.takeIf {it>0})
        Log.i(TAG, "activity_paused version=${BuildConfig.VERSION_CODE}")
        super.onPause()
    }

    fun dispatchImePanelKey(event: KeyEvent, source: String): Boolean = ownsScene() && keyboard.dispatch(event,source)
    fun resetImeShortcut() { keyboard.resetShortcutState() }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean = (ownsScene() && keyboard.dispatch(event)) || super.dispatchKeyEvent(event)

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (!hasFocus && ownsScene() && !BuildHooks.imeTerminalDeferFocusLoss()) {
            keyboard.resetShortcutState()
            BuildHooks.imeTerminalExternalLoss()
            NativeSessionHost.resetInput()
        }
    }

    override fun onSessionStateChanged(state: SessionState) {
        super.onSessionStateChanged(state)
        if(!ownsScene()) return
        Log.i(TAG, "xr_state=$state version=${BuildConfig.VERSION_CODE}")
        if(state != SessionState.FOCUSED) { keyboard.resetShortcutState();BuildHooks.imeTerminalExternalLoss() }
        NativeSessionHost.setXrState(when (state) {
            SessionState.FOCUSED -> "FOCUSED"
            SessionState.VISIBLE -> "VISIBLE"
            else -> "IDLE"
        },sceneEpoch.takeIf {it>0})
    }

    override fun onSpatialShutdown() {
        BuildHooks.sceneClosing()
        keyboardPanels.forEach {it.close()};keyboardPanels.clear()
        nativePanels.close()
        launcher?.destroy();launcher=null
        panel?.destroy()
        panel = null
        Log.i(TAG, "spatial_shutdown version=${BuildConfig.VERSION_CODE}")
        super.onSpatialShutdown()
    }

    companion object { private const val TAG = "EidolonPhase0"; private const val THEME_PICKER = 4201 }
}
