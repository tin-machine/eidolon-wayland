package io.github.tinmachine.eidolon

import android.os.Bundle
import android.os.PowerManager
import com.meta.spatial.core.SpatialFeature
import com.meta.spatial.runtime.SessionState
import com.meta.spatial.toolkit.AppSystemActivity
import com.meta.spatial.vr.VRFeature

/** SDKと空のSceneだけ。製品runtime、panel、SSH、Waylandは起動しない。 */
class GpuFdSdkActivity : AppSystemActivity() {
    private var nonce = ""
    private var ready = false
    private var started = false
    @Volatile private var focused = false
    @Volatile private var visible = false
    override fun registerFeatures(): List<SpatialFeature> = listOf(VRFeature(this))
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        nonce = intent?.getStringExtra("nonce") ?: ""
        if (!nonce.matches(Regex("[0-9a-f]{32}")) || intent.extras?.keySet() != setOf("nonce")) finish()
    }
    override fun onStart() { super.onStart(); visible = true; maybeStart() }
    override fun onStop() { visible = false; super.onStop() }
    override fun onSceneReady() { super.onSceneReady(); ready = true; maybeStart() }
    override fun onSessionStateChanged(state: SessionState) {
        super.onSessionStateChanged(state); focused = state == SessionState.FOCUSED; maybeStart()
    }
    private fun maybeStart() {
        if (started || !ready || !focused || !visible || !nonce.matches(Regex("[0-9a-f]{32}"))) return
        started = true
        val context = applicationContext
        Thread({
            val lock = context.getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Eidolon:GpuFdSdkProbe")
            lock.acquire(240000)
            try { GpuFdProbe.run(context, nonce, true) { focused && visible } }
            finally { if (lock.isHeld) lock.release() }
        }, "gpu-fd-observer").start()
    }
}
