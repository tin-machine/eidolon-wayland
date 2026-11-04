package io.github.tinmachine.eidolon

import org.json.JSONObject

/** JNI入口。未実装の入口は呼ばず、版整合が取れた時だけnative readyとする。 */
object NativeBridge {
    @JvmStatic external fun nativeBuildInfo(): String

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
