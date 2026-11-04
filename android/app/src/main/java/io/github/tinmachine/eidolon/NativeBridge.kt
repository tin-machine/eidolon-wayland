package io.github.tinmachine.eidolon

import org.json.JSONObject

/** JNI入口。未実装の入口は呼ばず、版整合が取れた時だけnative readyとする。 */
object NativeBridge {
    const val ACCEPTED = 0
    const val STALE = 1
    const val BUSY = 2
    const val INVALID_ARGUMENT = 3
    const val NOT_READY = 4
    const val FAILED = 5
    @JvmStatic external fun nativeBuildInfo(): String
    @JvmStatic external fun nativeCreate(configJson: String): Long
    @JvmStatic external fun nativeCommand(handle: Long, commandJson: String): Int
    @JvmStatic external fun nativePoll(handle: Long, maxEvents: Int): String
    @JvmStatic external fun nativeSnapshot(handle: Long): String
    @JvmStatic external fun nativeRequestStop(handle: Long): Int
    @JvmStatic external fun nativeDestroy(handle: Long): Int

    data class Status(val ready: Boolean, val info: String?, val error: String?)

    val status: Status by lazy {
        try {
            System.loadLibrary("eidolon_native")
            val info = nativeBuildInfo()
            val actual = JSONObject(info)
            val expected = JSONObject(BuildConfig.NATIVE_BUILD_INFO_JSON)
            check(actual.keys().asSequence().toSet() == expected.keys().asSequence().toSet())
            expected.keys().forEach { key -> check(actual.get(key) == expected.get(key)) { "Native identity mismatch: $key" } }
            check(actual.getInt("schema") == 1 && actual.getInt("apk_version_code") == BuildConfig.VERSION_CODE)
            Status(true, info, null)
        } catch (error: Exception) {
            Status(false, null, error.javaClass.simpleName)
        } catch (error: LinkageError) {
            Status(false, null, error.javaClass.simpleName)
        }
    }
}
