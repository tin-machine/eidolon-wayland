package io.github.tinmachine.eidolon.probe

import android.view.Surface

/** 検証専用JNI。製品のNativeBridge契約とは独立。 */
object NativeProbe {
    init { System.loadLibrary("eidolon_probe") }
    @JvmStatic external fun buildInfo(): String
    @JvmStatic external fun helper(libraryDirectory: String, filesDirectory: String): String
    @JvmStatic external fun transport(directory: String, nonce: String): String
    @JvmStatic external fun compositor(): String
    @JvmStatic external fun start(surface: Surface): Long
    @JvmStatic external fun pause(id: Long, paused: Boolean)
    @JvmStatic external fun stop(id: Long)
    @JvmStatic external fun snapshot(id: Long): String
    @JvmStatic external fun collect(id: Long): Int
}
