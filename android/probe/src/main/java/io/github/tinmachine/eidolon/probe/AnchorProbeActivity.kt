package io.github.tinmachine.eidolon.probe

import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.graphics.Color
import android.net.Uri
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Pose
import com.meta.spatial.core.Quaternion
import com.meta.spatial.core.Vector3
import com.meta.spatial.runtime.AlphaMode
import com.meta.spatial.runtime.MaterialSidedness
import com.meta.spatial.runtime.ReferenceSpace
import com.meta.spatial.runtime.SceneMaterial
import com.meta.spatial.runtime.SceneMesh
import com.meta.spatial.runtime.SceneTexture
import com.meta.spatial.toolkit.Mesh
import com.meta.spatial.toolkit.MeshCollision
import com.meta.spatial.toolkit.Scale
import com.meta.spatial.toolkit.Transform
import com.meta.spatial.toolkit.TransformParent
import kotlin.math.atan2
import kotlin.math.hypot
import kotlin.math.roundToInt
import org.json.JSONArray
import com.meta.spatial.core.SpatialFeature
import com.meta.spatial.core.SpatialSDKExperimentalAPI
import com.meta.spatial.runtime.SessionState
import com.meta.spatial.toolkit.AppSystemActivity
import com.meta.spatial.vr.VRFeature
import org.json.JSONObject
import java.util.UUID

/** Standalone Quest probe for one local Anchor and its lifecycle. */
class AnchorProbeActivity : AppSystemActivity() {
    private val handler = Handler(Looper.getMainLooper())
    private val requestId = UUID.randomUUID().toString()
    private var ready = false
    private var visible = false
    private var started = false
    private var finished = false
    private var trial: PersistenceTrial? = null
    private var marker: Entity? = null
    private val markerParts = mutableListOf<Entity>()
    private var markerPose: Pose? = null
    private val bearing = object : Runnable {
        override fun run() {
            val p = markerPose ?: return
            if (ready && visible) {
                val v = (scene.getViewerPose().inverse() * p).t
                val yaw = Math.toDegrees(atan2(v.x.toDouble(), v.z.toDouble()))
                val pitch = Math.toDegrees(atan2(v.y.toDouble(), hypot(v.x.toDouble(), v.z.toDouble())))
                if (yaw.isFinite() && pitch.isFinite()) record("bearing", JSONObject()
                    .put("right_degrees", (yaw / 5).roundToInt() * 5).put("up_degrees", (pitch / 5).roundToInt() * 5))
            }
            handler.postDelayed(this, 2000)
        }
    }
    private var restoredFrom: String? = null
    private var recreating = false
    private val visual get() = intent.getStringExtra("trial_action") in setOf("visual", "visual_recreate")

    override fun registerFeatures(): List<SpatialFeature> = listOf(VRFeature(this))

    private fun registerMarkerMeshes() {
        for ((name, color) in listOf("white" to Color.WHITE, "top" to Color.YELLOW, "right" to Color.CYAN)) {
            registerMeshCreator("mesh://sa0-marker-$name") { _: Entity ->
                val material = SceneMaterial(SceneTexture(Color.valueOf(Color.WHITE)), AlphaMode.OPAQUE, SceneMaterial.UNLIT_SHADER)
                material.setAlbedoColor(Color.valueOf(color))
                material.setUnlit(true)
                material.setSidedness(MaterialSidedness.DOUBLE_SIDED)
                SceneMesh.sphere(.5f, material)
            }
        }
    }

    private fun showMarker(v: JSONArray) {
        require(v.length() == 7)
        fun value(i: Int) = v.getDouble(i).toFloat()
        // Reflect OpenXR's Z axis into Spatial SDK coordinates. Quaternions use w,x,y,z here.
        val pose = Pose(Vector3(value(0), value(1), -value(2)),
            Quaternion(value(6), -value(3), -value(4), value(5)))
        check(marker == null)
        val parent = Entity.create(listOf(Transform(pose)))
        marker = parent
        fun part(name: String, center: Vector3, size: Vector3) {
            val mesh = Mesh(Uri.parse("mesh://sa0-marker-$name"), MeshCollision.NoCollision)
            markerParts += Entity.create(listOf(mesh, TransformParent(parent), Transform(Pose(center)), Scale(size)))
        }
        part("white", Vector3(0f), Vector3(.26f, .022f, .022f))
        part("white", Vector3(0f), Vector3(.022f, .26f, .022f))
        part("right", Vector3(.13f, 0f, 0f), Vector3(.040f))
        part("top", Vector3(0f, .13f, 0f), Vector3(.050f))
        markerPose = pose
        handler.post(bearing)
        record("marker_created")
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        if (recreating) outState.putString("anchor_recreated_from", requestId)
    }

    private fun trialFinished(outcome: String) {
        if (outcome == "located" && visual) {
            if (intent.getStringExtra("trial_action") == "visual_recreate" && restoredFrom == null) {
                recreating = true
                record("recreate_requested")
                handler.postDelayed({ if (!isFinishing && !isDestroyed && recreating) recreate() }, 1200)
            } else {
                finishCase(if (restoredFrom == null) "marker_located" else "marker_restored")
            }
        } else finishCase(outcome)
    }

    private var startupProbe: JSONObject? = null

    override fun registerRequiredOpenXRExtensions(): List<String> {
        val anchorExtensions = listOf(
            "XR_EXT_future", "XR_EXT_spatial_entity", "XR_EXT_spatial_anchor", "XR_EXT_spatial_persistence",
            "XR_EXT_spatial_persistence_operations", "XR_KHR_convert_timespec_time",
        )
        val extra = when (intent.getStringExtra("extensions")) {
            "persistence_caps", "persistence" -> anchorExtensions
            "preflight" -> {
                val observed = JSONObject(NativeProbe.startupExtensions(applicationContext))
                val extensions = observed.optJSONArray("extensions")
                val names = extensions?.let { (0 until it.length()).map { index -> it.getString(index) }.toSet() }
                val missing = anchorExtensions.filter { names?.contains(it) != true }
                startupProbe = JSONObject().put("missing", JSONArray(missing)).put("error", observed.optString("error"))
                if (missing.isEmpty()) anchorExtensions else emptyList()
            }
            else -> emptyList()
        }
        return (super.registerRequiredOpenXRExtensions() + extra).distinct()
    }

    private fun record(phase: String, data: JSONObject = JSONObject()) {
        val run = intent.getStringExtra("run_nonce")?.takeIf { it.matches(Regex("[A-Za-z0-9_-]{1,64}")) }
        Log.i("EidolonAnchorProbe", data.put("phase", phase).put("run", run).put("request", requestId).toString())
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        restoredFrom = savedInstanceState?.getString("anchor_recreated_from")
        record("activity", JSONObject().put("extension_mode", intent.getStringExtra("extensions") ?: "default")
            .put("pid", android.os.Process.myPid()).put("recreated_from", restoredFrom))
    }

    override fun onSceneReady() {
        super.onSceneReady()
        if (visual) {
            scene.setReferenceSpace(ReferenceSpace.STAGE)
            scene.setViewOrigin(0f, 0f, 0f, 0f)
            scene.enablePassthrough(true)
            registerMarkerMeshes()
        }
        ready = true
        tryDiscovery()
    }

    override fun onSessionStateChanged(state: SessionState) {
        super.onSessionStateChanged(state)
        visible = state == SessionState.VISIBLE || state == SessionState.FOCUSED
        record("xr_visibility", JSONObject().put("state", state.name))
        if (visible) tryDiscovery()
    }

    @OptIn(SpatialSDKExperimentalAPI::class)
    private fun tryDiscovery() {
        if (!ready || !visible || started || finished) return
        started = true
        val granted = checkSelfPermission("com.oculus.permission.USE_ANCHOR_API") == PackageManager.PERMISSION_GRANTED
        val capabilities = JSONObject(NativeProbe.anchorExtensions(
            scene.getOpenXrInstanceHandle(), scene.getOpenXrGetInstanceProcAddrHandle()
        ))
        record("capabilities", capabilities.put("permission_granted", granted)
            .put("session_present", scene.getOpenXrSessionHandle() != 0L))
        if (!granted) {
            finishCase("permission_denied")
            return
        }
        if (intent.getStringExtra("extensions") == "persistence") {
            if (capabilities.optJSONObject("persistence_scopes")?.optBoolean("local_anchors") != true) {
                finishCase("no_local_store")
                return
            }
            trial = PersistenceTrial(noBackupFilesDir, handler, if (visual) "lookup" else intent.getStringExtra("trial_action").orEmpty(),
                { command, uuid -> JSONObject(NativeProbe.persistence(scene.getOpenXrInstanceHandle(), scene.getOpenXrSessionHandle(),
                    scene.getOpenXrGetInstanceProcAddrHandle(), requestId, command, uuid)) },
                { phase, details -> record(phase, details) }, ::trialFinished, if (visual) ::showMarker else null)
            trial!!.start()
            return
        }
        if (intent.getStringExtra("extensions") == "preflight") {
            startupProbe?.let { record("startup_preflight", it) }
            val local = capabilities.optJSONObject("persistence_scopes")?.optBoolean("local_anchors") == true
            finishCase(if (local) "preflight_local_store_available" else "preflight_local_store_unavailable")
            return
        }
        if (intent.getStringExtra("extensions") in setOf("advertised", "persistence_caps")) {
            finishCase("capabilities_checked")
            return
        }
        finishCase("unsupported_probe_mode")
    }

    private fun finishCase(outcome: String) {
        if (finished) return
        finished = true
        record("result", JSONObject().put("outcome", outcome))
    }

    override fun onSpatialShutdown() {
        trial?.stop()
        ready = false
        markerPose = null
        handler.removeCallbacks(bearing)
        markerParts.asReversed().forEach { it.destroy() }
        markerParts.clear()
        marker?.destroy()
        marker = null
        if (!recreating) finishCase("scene_ended")
        super.onSpatialShutdown()
    }

    override fun onDestroy() {
        trial?.stop()
        handler.removeCallbacksAndMessages(null)
        if (!recreating) finishCase("activity_ended")
        super.onDestroy()
    }
}
