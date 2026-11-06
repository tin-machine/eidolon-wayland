package io.github.tinmachine.eidolon

import android.app.Activity
import android.app.Instrumentation
import android.os.Bundle
import android.os.PowerManager

/** SDKをloadせず、同じAPK UIDでplatform EGLだけを測る対照実験。 */
class GpuFdInstrumentation : Instrumentation() {
    private var nonce = ""
    override fun onCreate(arguments: Bundle?) {
        super.onCreate(arguments)
        nonce = arguments?.getString("nonce") ?: ""
        if (!nonce.matches(Regex("[0-9a-f]{32}")) || arguments?.keySet() != setOf("nonce")) {
            finish(Activity.RESULT_CANCELED, Bundle()); return
        }
        start()
    }
    override fun onStart() {
        val context = targetContext.applicationContext
        val lock = context.getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Eidolon:GpuFdProbe")
        lock.acquire(240000)
        var code = Activity.RESULT_CANCELED
        try { if (GpuFdProbe.run(context, nonce, false).getString("status") == "observed") code = Activity.RESULT_OK }
        finally { if (lock.isHeld) lock.release(); finish(code, Bundle().apply { putString("nonce", nonce) }) }
    }
}
