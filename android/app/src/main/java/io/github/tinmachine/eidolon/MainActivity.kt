package io.github.tinmachine.eidolon

import android.os.Build
import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.view.KeyEvent
import androidx.compose.foundation.background
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.unit.dp
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
    private var sceneEpoch=0L
    private fun ownsScene(): Boolean = !isDestroyed && (sceneEpoch==0L || sceneEpoch==NativeSessionHost.currentSceneEpoch())
    private val nativePanels by lazy { SpatialPanelAdapter() }
    private val keyboard by lazy { PhysicalKeyboard(this) }
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
                            var clicks by remember { mutableIntStateOf(0) }
                            Column(
                                Modifier.fillMaxSize().background(Color(0xFF102331)).padding(32.dp).verticalScroll(rememberScrollState()),
                                verticalArrangement = Arrangement.spacedBy(20.dp, Alignment.CenterVertically),
                                horizontalAlignment = Alignment.CenterHorizontally,
                            ) {
                                Text("Eidolon", color = Color.White, style = MaterialTheme.typography.headlineLarge)
                                Text("Build ${BuildConfig.VERSION_CODE}", color = Color(0xFF67E8C3))
                                val native = NativeBridge.status
                                Text(if (native.ready) "Rust ready · Build ${BuildConfig.VERSION_CODE}" else "Rust load failed: ${native.error}", color = if (native.ready) Color(0xFF67E8C3) else Color.Red)
                                Text("Runtime: ${NativeSessionHost.summary.value}", color = Color.White)
                                Text(NativeSessionHost.credentialSummary.value, color = Color.White)
                                Text("SSH: ${NativeSessionHost.connectionSummary.value}", color = Color.White)
                                ConnectionSettings()
                                Button(onClick = { NativeSessionHost.openTerminal() }) { Text("ターミナルを開く") }
                                for (window in NativeSessionHost.windowChoices.value) {
                                    Text("ウィンドウ ${window.window} · ${window.slot?.let { "表示枠 ${it+1}" } ?: "一覧に保持"}",color=Color.White)
                                    if(window.ambiguous) Text("同じアプリを複数開いているため、配置は今回の接続中に保持します",color=Color.White)
                                    Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                                        Button(onClick={NativeSessionHost.selectWindow(window)}) { Text("選択") }
                                        Button(onClick={NativeSessionHost.showWindow(window,0)}) { Text("枠1へ表示") }
                                        Button(onClick={NativeSessionHost.showWindow(window,1)}) { Text("枠2へ表示") }
                                        Button(onClick={NativeSessionHost.closeWindow(window)}) { Text("閉じる") }
                                    }
                                }
                                Text(NativeSessionHost.workspaceSummary.value,color=Color.White)
                                val observing = NativeSessionHost.workloadObservation.value
                                Button(onClick={NativeSessionHost.setWorkloadObservation(!observing)}) {
                                    Text(if(observing) "負荷の計測を停止" else "負荷の計測を再開")
                                }
                                val theme = NativeSessionHost.themeUi.value
                                Text(theme.message, color=Color.White)
                                Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                                    Button(onClick={NativeSessionHost.setTheme("off")}) { Text("装飾なし") }
                                    Button(onClick={NativeSessionHost.setTheme("builtin")}) { Text("Cyan Orbit") }
                                    Button(enabled=theme.imported,onClick={NativeSessionHost.setTheme("imported")}) { Text("読み込んだテーマ") }
                                }
                                Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                                    Button(onClick={chooseTheme()}) { Text("テーマZIPを読み込む") }
                                    Button(enabled=!theme.loading,onClick={NativeSessionHost.setTheme(theme.selection,!theme.reduced)}) {
                                        Text(if(theme.reduced) "アニメーションを有効に" else "動きを抑える")
                                    }
                                }
                                Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                                    Button(onClick={NativeSessionHost.setBackground("passthrough")}) { Text("現実を背景に") }
                                    Button(onClick={NativeSessionHost.setBackground("virtual")}) { Text("単色の背景に") }
                                }
                                Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                                    Button(onClick={NativeSessionHost.setKeyboardLayout("us")}) { Text("US配列") }
                                    Button(onClick={NativeSessionHost.setKeyboardLayout("jis")}) { Text("JIS配列") }
                                }
                                Button(enabled=NativeSessionHost.layoutAvailable.value,onClick={NativeSessionHost.setLayoutEditing(!NativeSessionHost.layoutEditing.value)}) {
                                    Text(if(NativeSessionHost.layoutEditing.value) "配置を確定して入力へ戻る" else "配置を編集")
                                }
                                if(NativeSessionHost.layoutEditing.value) {
                                    Text("グリップで移動できます。ボタンは選択したパネルに適用します。",color=Color.White)
                                    Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                                        Button(onClick={NativeSessionHost.nudgePose(x=-.1)}) { Text("左") }
                                        Button(onClick={NativeSessionHost.nudgePose(x=.1)}) { Text("右") }
                                        Button(onClick={NativeSessionHost.nudgePose(y=.1)}) { Text("上") }
                                        Button(onClick={NativeSessionHost.nudgePose(y=-.1)}) { Text("下") }
                                    }
                                    Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                                        Button(onClick={NativeSessionHost.nudgePose(z=-.1)}) { Text("手前") }
                                        Button(onClick={NativeSessionHost.nudgePose(z=.1)}) { Text("奥") }
                                        Button(onClick={NativeSessionHost.nudgePose(yaw=-15.0)}) { Text("角度 −15°") }
                                        Button(onClick={NativeSessionHost.nudgePose(yaw=15.0)}) { Text("角度 +15°") }
                                    }
                                }
                                Text(NativeSessionHost.layoutSummary.value,color=Color.White)
                                Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                                    Button(enabled=NativeSessionHost.layoutAvailable.value,onClick={NativeSessionHost.resizeTerminal(800,600)}) { Text("800×600") }
                                    Button(enabled=NativeSessionHost.layoutAvailable.value,onClick={NativeSessionHost.resizeTerminal(1024,768)}) { Text("1024×768") }
                                    Button(enabled=NativeSessionHost.layoutAvailable.value,onClick={NativeSessionHost.resizeTerminal(1280,720)}) { Text("1280×720") }
                                }
                                Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                                    Button(enabled=NativeSessionHost.layoutAvailable.value,onClick={NativeSessionHost.setPhysicalSize(.96,.72)}) { Text("小さく表示") }
                                    Button(enabled=NativeSessionHost.layoutAvailable.value,onClick={NativeSessionHost.setPhysicalSize(1.2,.9)}) { Text("標準表示") }
                                    Button(enabled=NativeSessionHost.layoutAvailable.value,onClick={NativeSessionHost.setPhysicalSize(1.44,1.08)}) { Text("大きく表示") }
                                }
                                Text(NativeSessionHost.keyboardSummary.value, color = Color.White)
                                Button(onClick = { NativeSessionHost.focusTerminal() }) { Text("ターミナルへキー入力") }
                                Button(onClick = { NativeSessionHost.resetInput() }) { Text("操作パネルへ戻る") }
                                Button(onClick = { NativeSessionHost.disconnect() }) { Text("接続を閉じる") }
                                Text("Spatial SDK 0.15.0 · Android API ${Build.VERSION.SDK_INT}", color = Color.White)
                                Button(onClick = {
                                    clicks++
                                    Log.i(TAG, "panel_click count=$clicks version=${BuildConfig.VERSION_CODE}")
                                }) { Text("確認 / Verify ($clicks)") }
                                Button(onClick = { NativeSessionHost.requestSnapshot() }) { Text("診断を保存 / Snapshot") }
                            }
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
        ),
    ) + nativePanels.registration + nativePanels.decorationRegistration + BuildHooks.additionalPanels()

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
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

    override fun onResume() {
        super.onResume()
        if(ownsScene()) {keyboard.resume();NativeSessionHost.setActivityVisible(true,sceneEpoch.takeIf {it>0})}
        Log.i(TAG, "activity_resumed version=${BuildConfig.VERSION_CODE}")
    }

    override fun onPause() {
        val current=ownsScene();keyboard.pause(resetInput=current)
        if(current) NativeSessionHost.setActivityVisible(false,sceneEpoch.takeIf {it>0})
        Log.i(TAG, "activity_paused version=${BuildConfig.VERSION_CODE}")
        super.onPause()
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean = (ownsScene() && keyboard.dispatch(event)) || super.dispatchKeyEvent(event)

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (!hasFocus && ownsScene()) NativeSessionHost.resetInput()
    }

    override fun onSessionStateChanged(state: SessionState) {
        super.onSessionStateChanged(state)
        if(!ownsScene()) return
        Log.i(TAG, "xr_state=$state version=${BuildConfig.VERSION_CODE}")
        NativeSessionHost.setXrState(when (state) {
            SessionState.FOCUSED -> "FOCUSED"
            SessionState.VISIBLE -> "VISIBLE"
            else -> "IDLE"
        },sceneEpoch.takeIf {it>0})
    }

    override fun onSpatialShutdown() {
        BuildHooks.sceneClosing()
        nativePanels.close()
        panel?.destroy()
        panel = null
        Log.i(TAG, "spatial_shutdown version=${BuildConfig.VERSION_CODE}")
        super.onSpatialShutdown()
    }

    companion object { private const val TAG = "EidolonPhase0"; private const val THEME_PICKER = 4201 }
}
