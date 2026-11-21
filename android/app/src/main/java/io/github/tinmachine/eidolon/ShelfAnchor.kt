package io.github.tinmachine.eidolon

import android.content.SharedPreferences
import android.os.SystemClock
import androidx.compose.runtime.mutableStateOf
import com.meta.spatial.core.Pose
import com.meta.spatial.core.SpatialSDKExperimentalAPI
import com.meta.spatial.runtime.Scene
import org.json.JSONObject
import java.util.UUID

/** Owns only the anchor created by the launcher shelf. Runs on the Activity thread. */
@OptIn(SpatialSDKExperimentalAPI::class)
internal class ShelfAnchor(private val preferences: SharedPreferences) {
    private val owner = "shelf-" + UUID.randomUUID()
    private var operation: String? = null
    private var started = false
    private var deadline = 0L
    private var nextPoll = 0L
    private var pose: JSONObject? = null
    val status = mutableStateOf("")
    val fixed = mutableStateOf(!preferences.getString("shelfUuid", null).isNullOrEmpty())
    val busy get() = operation != null
    fun save(location: Pose) {
        if (busy || fixed.value) return
        pose = PanelPoseCodec.fromSdk(location) ?: return
        begin("save")
    }
    fun find() { if (!busy && fixed.value) begin("find") }
    fun erase() { if (!busy && fixed.value) begin("erase") }
    private fun begin(command: String) {
        started = false
        operation = command
        deadline = SystemClock.elapsedRealtime() + 12000L
        nextPoll = 0L
        status.value = when (command) { "save" -> "棚の位置を固定中"; "erase" -> "固定を解除中"; else -> "固定位置を確認中" }
    }
    private fun send(scene: Scene, command: String): JSONObject = JSONObject(NativeBridge.nativeAnchorStep(
        scene.getOpenXrInstanceHandle(), scene.getOpenXrSessionHandle(), scene.getOpenXrGetInstanceProcAddrHandle(),
        owner, command, preferences.getString("shelfUuid", "").orEmpty(), pose?.toString().orEmpty()))
    fun tick(scene: Scene, place: (Pose) -> Unit) {
        val action = operation ?: return
        val now = SystemClock.elapsedRealtime()
        if (now < nextPoll) return
        nextPoll = now + 75L
        if (now >= deadline) {
            cancel(scene)
            status.value = "固定位置を確認できません。「呼び戻す」で棚を前方に表示できます"
            return
        }
        val result = send(scene, if (started) "poll" else action)
        if (result.optString("error") == "different trial owner" && !started) return
        when (result.optString("stage")) {
            "pending" -> { started = true; return }
            "saved" -> {
                val uuid = result.optString("uuid")
                if (action == "save" && MousepadAnchorReceipt.validUuid(uuid) && preferences.edit().putString("shelfUuid", uuid).commit()) {
                    fixed.value = true; status.value = "位置を固定しました"
                } else status.value = "固定位置を保存できませんでした"
            }
            "erased", "already_absent" -> {
                if (action == "erase" && preferences.edit().remove("shelfUuid").commit()) {
                    fixed.value = false; status.value = "固定を解除しました"
                }
            }
            else -> {
                val rows = result.optJSONArray("rows")
                val uuid = preferences.getString("shelfUuid", "")
                val row = rows?.let { list -> (0 until list.length()).mapNotNull(list::optJSONObject)
                    .singleOrNull { it.optString("uuid") == uuid && it.optBoolean("pose_valid") &&
                        it.optString("tracking") == "TRACKING" && it.optString("persist") == "LOADED" } }
                val found = row?.optJSONArray("pose")?.let(::panelPoseFromXr)
                if (action == "find" && found != null) {
                    place(PanelPoseCodec.toSdk(found)); status.value = "固定位置に戻しました"
                } else status.value = "固定位置を確認できません。「呼び戻す」で前方に表示できます"
            }
        }
        operation = null
        pose = null
        started = false
    }
    fun cancel(scene: Scene?) {
        if (scene != null && started) runCatching { send(scene, "cancel") }
        operation = null; started = false; pose = null
    }
}
