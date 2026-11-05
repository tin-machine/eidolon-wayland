package io.github.tinmachine.eidolon

import android.os.Build
import android.content.Intent
import android.os.Bundle
import android.util.Log
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
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
    private val nativePanels by lazy { SpatialPanelAdapter() }

    override fun registerFeatures(): List<SpatialFeature> =
        listOf(VRFeature(this), ComposeFeature())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Log.i(TAG, "activity_created version=${BuildConfig.VERSION_CODE} api=${Build.VERSION.SDK_INT}")
        val native = NativeBridge.status
        NativeSessionHost.initialize(applicationContext)
        if (native.ready) {
            Log.i(TAG, "native_ready version=${BuildConfig.VERSION_CODE} info=${native.info}")
        } else {
            Log.e(TAG, "native_failed version=${BuildConfig.VERSION_CODE} error=${native.error}")
        }
        BuildHooks.accept(intent, nativePanels) { scene }
    }

    override fun onSceneReady() {
        super.onSceneReady()
        val epoch = NativeSessionHost.sceneReady()
        scene.setReferenceSpace(ReferenceSpace.LOCAL_FLOOR)
        scene.setViewOrigin(0f, 0f, 0f, 0f)
        scene.enablePassthrough(true)
        if (panel == null) {
            panel = Entity.create(listOf(
                Panel(R.id.phase0_panel),
                Transform(Pose(Vector3(0.7f, 1.4f, 1.6f))),
            ))
        }
        nativePanels.sceneReady(epoch)
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
                                Modifier.fillMaxSize().background(Color(0xFF102331)).padding(32.dp),
                                verticalArrangement = Arrangement.spacedBy(20.dp, Alignment.CenterVertically),
                                horizontalAlignment = Alignment.CenterHorizontally,
                            ) {
                                Text("Eidolon", color = Color.White, style = MaterialTheme.typography.headlineLarge)
                                Text("Phase 1 · Build ${BuildConfig.VERSION_CODE}", color = Color(0xFF67E8C3))
                                val native = NativeBridge.status
                                Text(if (native.ready) "Rust ready · Build ${BuildConfig.VERSION_CODE}" else "Rust load failed: ${native.error}", color = if (native.ready) Color(0xFF67E8C3) else Color.Red)
                                Text("Runtime: ${NativeSessionHost.summary.value}", color = Color.White)
                                Text(NativeSessionHost.credentialSummary.value, color = Color.White)
                                Text("SSH: ${NativeSessionHost.connectionSummary.value}", color = Color.White)
                                Button(onClick = { NativeSessionHost.openTerminal() }) { Text("ターミナルを開く") }
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
    ) + nativePanels.registration

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        BuildHooks.accept(intent, nativePanels) { scene }
    }

    override fun onResume() {
        super.onResume()
        NativeSessionHost.setActivityVisible(true)
        Log.i(TAG, "activity_resumed version=${BuildConfig.VERSION_CODE}")
    }

    override fun onPause() {
        NativeSessionHost.setActivityVisible(false)
        Log.i(TAG, "activity_paused version=${BuildConfig.VERSION_CODE}")
        super.onPause()
    }

    override fun onSessionStateChanged(state: SessionState) {
        super.onSessionStateChanged(state)
        Log.i(TAG, "xr_state=$state version=${BuildConfig.VERSION_CODE}")
        NativeSessionHost.setXrState(when (state) {
            SessionState.FOCUSED -> "FOCUSED"
            SessionState.VISIBLE -> "VISIBLE"
            else -> "IDLE"
        })
    }

    override fun onSpatialShutdown() {
        nativePanels.close()
        panel?.destroy()
        panel = null
        Log.i(TAG, "spatial_shutdown version=${BuildConfig.VERSION_CODE}")
        super.onSpatialShutdown()
    }

    companion object { private const val TAG = "EidolonPhase0" }
}
