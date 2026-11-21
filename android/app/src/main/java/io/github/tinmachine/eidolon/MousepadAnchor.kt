package io.github.tinmachine.eidolon

import android.content.Context
import android.content.pm.PackageManager
import android.os.SystemClock
import android.system.Os
import android.system.OsConstants
import android.util.AtomicFile
import androidx.compose.runtime.mutableStateOf
import com.meta.spatial.core.SpatialSDKExperimentalAPI
import com.meta.spatial.runtime.Scene
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileNotFoundException
import java.util.UUID

internal data class AnchorWindow(val runtime: Long, val key: String) {
    companion object {
        fun from(runtime: Long, target: JSONObject): AnchorWindow =
            AnchorWindow(runtime, listOf("session", "window", "scene", "generation")
                .joinToString(":") { target.getString(it) })
    }
}

internal data class MousepadAnchorGate(val required: Boolean = false, val resolved: AnchorWindow? = null) {
    fun allows(target: AnchorWindow, candidate: AnchorWindow?): Boolean =
        !required || target != candidate || target == resolved
}

internal fun anchorPoseApplied(
    target: AnchorWindow,
    command: String,
    desired: JSONObject,
    applied: JSONObject?,
    actual: JSONObject?,
): Boolean =
    applied != null && applied.optString("pose_command") == command &&
        applied.optJSONObject("target")?.let { AnchorWindow.from(target.runtime, it) } == target &&
        applied.optJSONObject("pose")?.let { PanelPoseCodec.near(it, desired) } == true &&
        actual?.let { PanelPoseCodec.near(it, desired) } == true

internal data class MousepadAnchorUi(
    val message: String = "",
    val restoring: Boolean = false,
    val canSave: Boolean = false,
    val canRemove: Boolean = false,
    val canRetry: Boolean = false,
    val canUseTemporaryPosition: Boolean = false,
)

@OptIn(SpatialSDKExperimentalAPI::class)
internal class MousepadAnchor(context: Context, private val owner: Long) {
    private data class Window(val id: AnchorWindow, val target: JSONObject, val applied: JSONObject?, val actual: JSONObject?)
    private enum class Kind { SAVE, FIND, REMOVE, VERIFY_ABSENCE }
    private data class Request(val kind: Kind, val window: AnchorWindow?, val receipt: MousepadAnchorReceipt, val deadline: Long)
    private data class Applying(val window: AnchorWindow, val command: String, val pose: JSONObject, val deadline: Long)

    val ui = mutableStateOf(MousepadAnchorUi())
    private val directory = context.noBackupFilesDir
    private val journal = AtomicFile(File(directory, "mousepad-anchor.json"))
    private val permitted = context.checkSelfPermission("com.oculus.permission.USE_ANCHOR_API") == PackageManager.PERMISSION_GRANTED
    private var scene: Scene? = null
    private var epoch = 0L
    private var available = false
    private var window: Window? = null
    private var resolved: AnchorWindow? = null
    private var request: Request? = null
    private var applying: Applying? = null
    private var attempts = 0
    private var retryAt = 0L
    private var nativePollAt = 0L
    private var focused = false
    private var acceptingPose = false
    private var storageError = false
    private var failure = ""
    private var lastLookup: String? = null
    private var receipt: MousepadAnchorReceipt = MousepadAnchorReceipt.Empty

    fun bind(sdkScene: Scene, sceneEpoch: Long) {
        close()
        scene = sdkScene
        epoch = sceneEpoch
        available = permitted && runCatching {
            NativeBridge.nativeAnchorScope(sdkScene.getOpenXrInstanceHandle(), sdkScene.getOpenXrGetInstanceProcAddrHandle())
        }.getOrDefault(false)
        storageError = false
        failure = ""
        receipt = try {
            readReceipt()
        } catch (_: Exception) {
            storageError = true
            MousepadAnchorReceipt.Empty
        }
        updateGate()
        publish()
    }

    private fun readReceipt(): MousepadAnchorReceipt = try {
        MousepadAnchorReceipt.decode(journal.readFully())
    } catch (missing: FileNotFoundException) {
        if (journal.baseFile.exists() || File(directory, "mousepad-anchor.json.bak").exists()) throw missing
        MousepadAnchorReceipt.Empty
    }

    private fun replaceReceipt(next: MousepadAnchorReceipt) {
        val bytes = next.encode()
        val output = journal.startWrite()
        try {
            output.write(bytes)
            output.fd.sync()
        } catch (failure: Exception) {
            journal.failWrite(output)
            throw failure
        }
        journal.finishWrite(output)
        check(journal.readFully().contentEquals(bytes))
        val descriptor = Os.open(directory.path, OsConstants.O_RDONLY, 0)
        try { Os.fsync(descriptor) } finally { Os.close(descriptor) }
        receipt = next
    }

    fun awaitingPlacement(target: AnchorWindow): Boolean =
        !gate().allows(target, target)

    fun isApplyingPlacement(runtime: Long, target: JSONObject, command: String?): Boolean =
        applying?.let { it.command == command && it.window == AnchorWindow.from(runtime, target) } == true

    fun tick(runtime: Long, snapshot: JSONObject, actualPose: JSONObject?) {
        if (!NativeSessionHost.ownsActivityScene(owner, epoch)) return
        focused = snapshot.optBoolean("activity_visible") && snapshot.optString("xr_state") == "FOCUSED"
        acceptingPose = focused && !NativeSessionHost.isLayoutEditing()
        val target = snapshot.optJSONObject("anchor_placement_target")
            ?.takeIf { it.optString("placement") == "mousepad_main" }?.optJSONObject("target")
        val placement = target?.let { placement(snapshot, it) }
        val next = target?.let { Window(AnchorWindow.from(runtime, it), it, placement?.optJSONObject("applied_pose"), actualPose) }
        if (next != null && next.id != window?.id) {
            cancelLookup()
            attempts = 0
            retryAt = 0
            lastLookup = null
        }
        if (next == null && window != null) cancelLookup()
        window = next
        val now = SystemClock.elapsedRealtime()
        try {
            poll(now)
            val pending = applying
            if (pending != null && next?.id == pending.window && acceptingPose) {
                if (anchorPoseApplied(next.id, pending.command, pending.pose, next.applied, actualPose)) {
                    resolved = next.id
                    lastLookup = "placed"
                    failure = ""
                    clearApply()
                    updateGate()
                } else if (now >= pending.deadline ||
                    NativeSessionHost.poseCompletion(pending.command) in setOf("STALE", "FAILED", "NOT_READY", "BUSY", "INVALID_ARGUMENT")
                ) {
                    clearApply()
                    lastLookup = "pose_not_applied"
                    failure = "復元した位置を適用できませんでした"
                }
            }
            val saved = receipt as? MousepadAnchorReceipt.Saved
            if (available && !storageError && saved != null && next != null &&
                acceptingPose && resolved != next.id && attempts < MAX_ATTEMPTS &&
                request == null && applying == null && now >= retryAt
            ) {
                attempts++
                lastLookup = "searching"
                begin(Kind.FIND, next.id, saved)
            }
        } catch (_: Exception) {
            storageError = true
            lastLookup = "processing_error"
            failure = "固定位置の処理を完了できませんでした"
            stopNative()
        }
        publish()
    }

    fun controlPinned(): Boolean? = if (available) {
        receipt is MousepadAnchorReceipt.Saved || receipt is MousepadAnchorReceipt.Removing
    } else null

    fun toggleForWindow(target: AnchorWindow): Boolean {
        if (window?.id != target || request != null || applying != null) return false
        return when {
            ui.value.canSave -> { save(); true }
            ui.value.canRemove -> { remove(); true }
            else -> false
        }
    }

    fun save() {
        val current = window ?: return
        val actual = current.actual ?: return
        if (!ui.value.canSave || !NativeSessionHost.ownsActivityScene(owner, epoch)) return
        try {
            check(readReceipt() == MousepadAnchorReceipt.Empty)
            val pending = MousepadAnchorReceipt.Pending(UUID.randomUUID().toString().replace("-", ""))
            replaceReceipt(pending)
            begin(Kind.SAVE, current.id, pending, actual)
        } catch (_: Exception) {
            storageError = true
            stopNative()
        }
        publish()
    }

    fun remove() {
        if (!ui.value.canRemove || !NativeSessionHost.ownsActivityScene(owner, epoch)) return
        try {
            val uuid = when (val current = receipt) {
                is MousepadAnchorReceipt.Saved -> current.uuid
                is MousepadAnchorReceipt.Removing -> current.uuid
                else -> return
            }
            val deleting = MousepadAnchorReceipt.Removing(uuid)
            replaceReceipt(deleting)
            updateGate()
            begin(Kind.REMOVE, null, deleting)
        } catch (_: Exception) {
            storageError = true
            stopNative()
        }
        publish()
    }

    fun retry() {
        if (!ui.value.canRetry) return
        attempts = 0
        retryAt = 0
        failure = ""
        publish()
    }

    fun useTemporaryPosition() {
        if (!ui.value.canUseTemporaryPosition) return
        resolved = window?.id
        failure = "今回の窓は現在の位置で操作できます"
        updateGate()
        publish()
    }

    private fun begin(kind: Kind, target: AnchorWindow?, receipt: MousepadAnchorReceipt, pose: JSONObject? = null) {
        val next = Request(kind, target, receipt, SystemClock.elapsedRealtime() + 12_000)
        request = next
        val uuid = when (receipt) {
            is MousepadAnchorReceipt.Saved -> receipt.uuid
            is MousepadAnchorReceipt.Removing -> receipt.uuid
            else -> ""
        }
        val command = when (kind) {
            Kind.SAVE -> "save"
            Kind.REMOVE -> "erase"
            Kind.FIND, Kind.VERIFY_ABSENCE -> "find"
        }
        val result = command(command, uuid, pose)
        nativePollAt = SystemClock.elapsedRealtime() + 50
        accept(next, result)
    }

    private fun poll(now: Long) {
        val current = request ?: return
        if (now < nativePollAt) return
        nativePollAt = now + 50
        val result = command("poll")
        if (result.optString("stage") == "pending" && now >= current.deadline) {
            stopNative()
            retryAt = now + 1_000
            if (current.kind == Kind.FIND) lastLookup = "search_timed_out"
            failure = "固定位置の処理がタイムアウトしました"
            return
        }
        accept(current, result)
    }

    private fun accept(current: Request, result: JSONObject) {
        val stage = result.optString("stage")
        if (stage == "pending") return
        request = null
        // Confirm a successful persist even if closing its temporary OpenXR resources failed.
        if (current.kind == Kind.SAVE && stage == "saved") {
            val pending = current.receipt as MousepadAnchorReceipt.Pending
            val onDisk = readReceipt() as? MousepadAnchorReceipt.Pending
            val uuid = result.getString("uuid")
            check(onDisk?.operation == pending.operation && MousepadAnchorReceipt.validUuid(uuid))
            replaceReceipt(MousepadAnchorReceipt.Saved(uuid))
            resolved = if (window?.id == current.window) current.window else null
            updateGate()
            failure = ""
            return
        }
        if (current.kind == Kind.SAVE && stage == "error" && result.optBoolean("persist_may_have_started", true) == false) {
            val pending = current.receipt as MousepadAnchorReceipt.Pending
            check((readReceipt() as? MousepadAnchorReceipt.Pending)?.operation == pending.operation)
            replaceReceipt(MousepadAnchorReceipt.Empty)
        }
        if (stage == "error" || (result.optJSONArray("release_errors")?.length() ?: 0) > 0) {
            if (current.kind == Kind.FIND) lastLookup = if (stage == "error") "search_failed" else "xr_release_failed"
            retryAt = SystemClock.elapsedRealtime() + 1_000
            failure = "固定位置を確認できませんでした"
            return
        }
        when (current.kind) {
            Kind.SAVE -> failure = "固定位置の保存結果を確認できませんでした"
            Kind.REMOVE -> {
                if (stage == "erased" || stage == "already_absent") begin(Kind.VERIFY_ABSENCE, null, current.receipt)
                else failure = "固定位置を解除できませんでした"
            }
            Kind.VERIFY_ABSENCE -> {
                val removing = current.receipt as MousepadAnchorReceipt.Removing
                val item = anchorRow(result, removing.uuid)
                if (item?.optString("persist") == "NOT_FOUND" && item.optString("tracking") == "STOPPED") {
                    val onDisk = readReceipt() as? MousepadAnchorReceipt.Removing
                    check(onDisk?.uuid == removing.uuid)
                    replaceReceipt(MousepadAnchorReceipt.Empty)
                    resolved = null
                    updateGate()
                    failure = ""
                } else failure = "解除後の確認が必要です"
            }
            Kind.FIND -> {
                val target = window?.takeIf { it.id == current.window } ?: return
                val row = anchorRow(result, (current.receipt as MousepadAnchorReceipt.Saved).uuid)
                val pose = row?.takeIf {
                    it.optString("persist") == "LOADED" && it.optString("tracking") == "TRACKING" && it.optBoolean("pose_valid")
                }?.optJSONArray("pose")?.let(::panelPoseFromXr)
                lastLookup = when {
                    row == null -> "anchor_missing"
                    row.optString("persist") != "LOADED" -> "anchor_not_loaded"
                    row.optString("tracking") != "TRACKING" -> "anchor_not_tracking"
                    pose == null -> "invalid_anchor_pose"
                    !acceptingPose -> "xr_not_focused"
                    else -> "pose_rejected"
                }
                if (pose != null && acceptingPose) {
                    val id = NativeSessionHost.submitPose(target.target, pose, target.id.runtime)
                    if (id != null) {
                        lastLookup = "pose_requested"
                        applying = Applying(target.id, id, pose, SystemClock.elapsedRealtime() + 5_000)
                        return
                    }
                }
                retryAt = SystemClock.elapsedRealtime() + 1_000
                failure = "固定位置の追跡を確定できません"
            }
        }
    }

    private fun anchorRow(result: JSONObject, uuid: String): JSONObject? =
        result.optJSONArray("rows")?.let { rows ->
            (0 until rows.length()).mapNotNull(rows::optJSONObject).singleOrNull { it.optString("uuid") == uuid }
        }

    private fun command(command: String, uuid: String = "", pose: JSONObject? = null): JSONObject {
        val current = checkNotNull(scene)
        return JSONObject(NativeBridge.nativeAnchorStep(current.getOpenXrInstanceHandle(), current.getOpenXrSessionHandle(),
            current.getOpenXrGetInstanceProcAddrHandle(), "$owner:$epoch", command, uuid, pose?.toString().orEmpty()))
    }

    private fun cancelLookup() {
        if (request?.kind == Kind.FIND) stopNative()
        clearApply()
    }

    private fun clearApply() {
        applying?.let { NativeSessionHost.forgetPose(it.command) }
        applying = null
    }

    private fun stopNative() {
        if (request != null) {
            runCatching { command("cancel") }
            request = null
        }
    }

    fun close() {
        if (request?.kind == Kind.SAVE) {
            // Preserve a result already available on the Activity thread before the scene is torn down.
            runCatching { request?.let { accept(it, command("poll")) } }
        }
        stopNative()
        clearApply()
        window = null
        resolved = null
        available = false
        focused = false
        acceptingPose = false
        scene = null
        epoch = 0L
    }

    private fun gate() = MousepadAnchorGate(available && receipt is MousepadAnchorReceipt.Saved, resolved)

    private fun updateGate() {
        val gate = gate()
        NativeSessionHost.setMousepadAnchorGate(owner, epoch, gate.required, gate.resolved)
    }

    fun diagnostics() = JSONObject()
        .put("activity_scene_current", NativeSessionHost.ownsActivityScene(owner, epoch))
        .put("available", available)
        .put("saved", receipt is MousepadAnchorReceipt.Saved)
        .put("has_current_window", window != null)
        .put("can_apply_pose", acceptingPose)
        .put("attempts", attempts)
        .put("last_lookup", lastLookup ?: JSONObject.NULL)

    private fun publish() {
        val saved = receipt is MousepadAnchorReceipt.Saved
        val busy = request != null || applying != null
        val unsettled = saved && window != null && window?.id != resolved
        val current = window
        val matchingPose = current?.applied?.optJSONObject("pose")?.let { applied ->
            current.actual?.let { PanelPoseCodec.near(it, applied) } == true &&
                current.applied.optJSONObject("target")?.let { AnchorWindow.from(current.id.runtime, it) } == current.id
        } == true
        ui.value = MousepadAnchorUi(
            message = when {
                storageError -> "Mousepad 固定位置の記録を確認できません"
                !available -> ""
                busy -> "Mousepad の固定位置を処理中"
                receipt is MousepadAnchorReceipt.Pending -> "Mousepad の保存結果を確認できません。回収待ち"
                failure.isNotEmpty() -> failure
                receipt is MousepadAnchorReceipt.Removing -> "Mousepad の固定位置は解除確認待ち"
                unsettled -> "Mousepad の固定位置を検索中"
                saved -> "Mousepad の固定位置を保存済み"
                else -> ""
            },
            restoring = available && unsettled,
            canSave = available && !storageError && acceptingPose && !busy && receipt == MousepadAnchorReceipt.Empty && matchingPose,
            canRemove = available && !storageError && focused && !busy && (saved || receipt is MousepadAnchorReceipt.Removing),
            canRetry = available && !storageError && acceptingPose && !busy && unsettled && attempts >= MAX_ATTEMPTS,
            canUseTemporaryPosition = available && acceptingPose && !busy && unsettled && (storageError || attempts >= MAX_ATTEMPTS),
        )
    }

    private fun placement(snapshot: JSONObject, target: JSONObject): JSONObject? {
        val rows = snapshot.optJSONObject("workspace")?.optJSONArray("windows") ?: return null
        return (0 until rows.length()).mapNotNull(rows::optJSONObject)
            .singleOrNull { it.optString("window") == target.optString("window") && it.optString("session") == target.optString("session") }
            ?.optJSONObject("placement")
    }

    private companion object { const val MAX_ATTEMPTS = 3 }
}

/** OpenXR is −Z-forward and the Spatial SDK is +Z-forward. */
internal fun panelPoseFromXr(values: JSONArray): JSONObject? {
    if (values.length() != 7) return null
    val v = DoubleArray(7) { values.optDouble(it, Double.NaN) }
    if (!v.all { it.isFinite() }) return null
    val norm = kotlin.math.sqrt((3..6).sumOf { v[it] * v[it] })
    if (!norm.isFinite() || norm < .999 || norm > 1.001) return null
    return JSONObject().put("position_m", JSONArray(listOf(v[0], v[1], -v[2])))
        .put("rotation_xyzw", JSONArray(listOf(-v[3] / norm, -v[4] / norm, v[5] / norm, v[6] / norm)))
        .takeIf(PanelPoseCodec::valid)
}
