package io.github.tinmachine.eidolon

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.os.Trace
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.unit.dp
import com.meta.spatial.compose.ComposeViewPanelRegistration
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Pose
import com.meta.spatial.core.SpatialSDKExperimentalAPI
import com.meta.spatial.core.Vector3
import com.meta.spatial.runtime.ControllerPose
import com.meta.spatial.runtime.ButtonBits
import com.meta.spatial.runtime.HitInfo
import com.meta.spatial.runtime.InputListener
import com.meta.spatial.runtime.PointerEventType
import com.meta.spatial.runtime.SceneObject
import com.meta.spatial.core.Vector2
import com.meta.spatial.toolkit.Controller
import com.meta.spatial.toolkit.ControllerType
import com.meta.spatial.runtime.Scene
import com.meta.spatial.toolkit.DpPerMeterDisplayOptions
import com.meta.spatial.toolkit.Grabbable
import com.meta.spatial.toolkit.GrabbableType
import com.meta.spatial.toolkit.Panel
import com.meta.spatial.toolkit.PanelStyleOptions
import com.meta.spatial.toolkit.QuadShapeOptions
import com.meta.spatial.toolkit.Transform
import com.meta.spatial.toolkit.UIPanelSettings
import com.meta.spatial.toolkit.Visible
import com.meta.spatial.toolkit.getAbsoluteTransform

private const val HAND_SOURCE = -1L

internal enum class LauncherPreset(val title: String) {
    STANDARD("標準"), SUMMON("前方に呼ぶ"), PULL("手元から引き出す"), PINNED("棚を置いておく")
}

/** Scene owner for the common app shelf and the controller-side controls. */
internal class SpatialLauncher(
    context: Context,
    private val live: () -> Boolean,
    private val setupKeyboard: (ComposeView, android.view.Window) -> PanelKeyboardBridge,
    private val handsExtensions: () -> Set<String>,
    private val openSettings: () -> Unit,
) : AutoCloseable {
    private val preferences = context.getSharedPreferences("spatial_launcher", Context.MODE_PRIVATE)
    private val shelfAnchor = ShelfAnchor(preferences)
    private val handler = Handler(Looper.getMainLooper())
    private val preset = mutableStateOf(LauncherPreset.entries.firstOrNull {
        it.name == preferences.getString("preset", null)
    } ?: LauncherPreset.SUMMON)
    private var shelfWasGrabbed = false
    private val movingShelf = mutableStateOf(false)
    private var scene: Scene? = null
    private val visibility = WristVisibility()
    private val press = WristPress()
    private val hands = WristHandTracking()
    private var input = mutableStateOf(readSetting("wristInput", WristInput.AUTO))
    private var side = mutableStateOf(readSetting("wristSide", WristSide.LEFT))
    private val skin = mutableStateOf(readSetting("wristSkin", WristSkin.CIRCLE))
    private val wristStatus = mutableStateOf("追跡を待っています")
    private val calibrationStatus = mutableStateOf("")
    private val hover = mutableStateOf<WristAction?>(null)
    private val pressed = mutableStateOf<WristAction?>(null)
    private var wristPose: Pose? = null
    private var wristListener: Pair<SceneObject, Long>? = null
    @Volatile private var actualInput: WristInput? = null
    private var handNeutral = false
    private var handPinching = false
    private var handDragOffset: Pose? = null
    private var handDragOrigin: Pose? = null
    private var lastTheme = preferences.getString("lastDecor", "builtin") ?: "builtin"
    private var preview: Entity? = null

    private inline fun <reified T : Enum<T>> readSetting(key: String, fallback: T): T =
        runCatching { enumValueOf<T>(preferences.getString(key, null).orEmpty()) }.getOrDefault(fallback)

    private fun upKey() = if (side.value.left) "controllerUpLeft" else "controllerUpRight"
    private fun configuredUp(): Vector3? {
        val values = preferences.getString(upKey(), null)?.split(",")?.map { it.toFloatOrNull() } ?: return null
        if (values.size != 3 || values.any { it == null || !it.isFinite() }) return null
        val vector = Vector3(values[0]!!, values[1]!!, values[2]!!)
        return vector.takeIf { it.length() > .01f }?.normalize()
    }

    private fun calibrate() {
        val pose = controller(side.value.left)
        if (!active || !live() || pose == null) {
            calibrationStatus.value = "${side.value.title}のコントローラーを追跡できません"
            return
        }
        val up = pose.q.inverse() * Vector3(0f, 1f, 0f)
        preferences.edit().putString(upKey(), "${up.x},${up.y},${up.z}").apply()
        resetWrist()
        calibrationStatus.value = "${side.value.title}の向きを登録しました"
    }

    private var shelf: Entity? = null
    private var wrist: Entity? = null
    private var handle: Entity? = null
    private var shelfListener: Pair<SceneObject, Long>? = null
    private var shelfKeyboard: PanelKeyboardBridge? = null
    private var pinnedPose: Pose? = null
    private var explicitlyPinned = false
    private var poseBeforeMove: Pose? = null
    private data class ShelfTriggerDrag(val source: Entity, val button: Int, val offset: Pose)
    private var shelfTriggerDrag: ShelfTriggerDrag? = null
    private var handleOrigin: Pose? = null
    private var controllerDrag: Entity? = null
    private var controllerOffset: Pose? = null
    private var handleListener: Pair<SceneObject, Long>? = null
    private data class Placement(val source: LauncherSource, val binding: String, val snapshot: LauncherUi, val entry: LauncherEntry, val shortcut: Boolean)
    private val placement = mutableStateOf<Placement?>(null)
    private data class Shortcut(val entity: Entity, val selection: Placement)
    private val shortcuts = androidx.compose.runtime.mutableStateMapOf<Long, Shortcut>()
    private var active = false
    private var reportedInputError = false
    private var lastInputFrameMs = 0L
    private val frame = object : Runnable {
        override fun run() {
            if (!active || !live()) return
            try {
                val now = SystemClock.elapsedRealtime()
                if (lastInputFrameMs != 0L && now - lastInputFrameMs > 150L) {
                    cancelAppPlacement()
                    resetWrist()
                }
                lastInputFrameMs = now
                scene?.let { sdk -> shelfAnchor.tick(sdk) { restored ->
                    pinnedPose = restored
                    explicitlyPinned = true
                    showShelf(restored)
                } }
                if (movingShelf.value) updateShelfMove()
                traceWrist("Eidolon.wrist.update") { updateWrist() }
                reportedInputError = false
            } catch (e: RuntimeException) {
                if (!reportedInputError) android.util.Log.w("EidolonWrist", e.javaClass.simpleName)
                reportedInputError = true
                wristStatus.value = "手首の入力を確認できません"
                runCatching { resetWrist() }
            }
            val searching = actualInput == null && placement.value == null && !movingShelf.value && !shelfAnchor.busy
            handler.postDelayed(this, if (searching) 75L else 25L)
        }
    }

    val registrations = listOf(
        ComposeViewPanelRegistration(
            R.id.spatial_app_shelf,
            composeViewCreator = { _, context ->
                ComposeView(context).apply { setContent {
                    MaterialTheme {
                        LauncherSettings(
                            modifier = Modifier.fillMaxSize(),
                            close = ::hideShelf,
                            placeShelf = if (!shelfAnchor.fixed.value && !movingShelf.value) ::moveShelf else null,
                            finishShelf = if (movingShelf.value) ::finishShelfMove else null,
                            fixed = shelfAnchor.fixed.value,
                            anchorStatus = shelfAnchor.status.value,
                            toggleFix = {
                                if (shelfAnchor.fixed.value) {
                                    explicitlyPinned = false
                                    shelfAnchor.erase()
                                } else {
                                    if (movingShelf.value) finishShelfMove()
                                    shelf?.let { shelfAnchor.save(getAbsoluteTransform(it)) }
                                }
                            },
                            placeApp = ::beginAppPlacement,
                            launcherControls = { this@SpatialLauncher.Presets() },
                            openConnectionSettings = { hideShelf(); openSettings() },
                        )
                    }
                } }
            },
            settingsCreator = { settings(2.4f, 1.5f, 540f) },
            panelSetupWithComposeView = { view, object3d, _ ->
                val owner = object3d.entity?.id
                view.post {
                    if (owner != null && shelf?.id == owner && live()) {
                        shelfListener?.let { (obj, token) -> obj.removeInputListener(token) }
                        shelfListener = object3d to object3d.addInputListener(object : InputListener {
                            override fun onInput(receiver: SceneObject, hitInfo: HitInfo, sourceOfInput: Entity,
                                changed: Int, state: Int, downTime: Long): Boolean {
                                val trigger = changed and (ButtonBits.ButtonTriggerL or ButtonBits.ButtonTriggerR)
                                if (movingShelf.value && trigger != 0 && physicalController(sourceOfInput) != null) {
                                    val pressed = state and trigger
                                    if (pressed == ButtonBits.ButtonTriggerL || pressed == ButtonBits.ButtonTriggerR) {
                                        handler.post { startShelfTriggerMove(owner, sourceOfInput, pressed) }
                                    }
                                    return true
                                }
                                if (changed and ButtonBits.ButtonThumbRU == 0 || ShelfReasonFocus.hovered == null) return false
                                if (state and ButtonBits.ButtonThumbRU != 0) handler.post { ShelfReasonFocus.request.intValue++ }
                                return true
                            }
                        })
                        object3d.display?.presentation?.window?.let {
                            shelfKeyboard?.close()
                            shelfKeyboard = setupKeyboard(view, it)
                        }
                    }
                }
            },
        ),
        ComposeViewPanelRegistration(
            R.id.spatial_launcher_wrist,
            composeViewCreator = { _, context -> ComposeView(context).apply { setContent {
                MaterialTheme { this@SpatialLauncher.WristMenu() }
            } } },
            settingsCreator = { settings(.40f, .40f, 400f) },
            panelSetupWithComposeView = { _, obj, _ ->
                val id = obj.entity?.id
                handler.post {
                    if (id != null && wrist?.id == id && live()) {
                        val token = obj.addInputListener(wristInputListener(id))
                        wristListener = obj to token
                    }
                }
            },
        ),
        ComposeViewPanelRegistration(
            R.id.spatial_launcher_handle,
            composeViewCreator = { _, context -> ComposeView(context).apply { setContent {
                MaterialTheme {
                    Box(Modifier.fillMaxSize().background(Color(0xFF394B68)), contentAlignment = Alignment.Center) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(placement.value?.entry?.label ?: "棚を引く", color = Color.White, maxLines = 1)
                            if (placement.value != null) TextButton(onClick = ::cancelAppPlacement) { Text("取消") }
                        }
                    }
                }
            } } },
            settingsCreator = { settings(.22f, .075f) },
            panelSetupWithComposeView = { _, obj, _ ->
                val id = obj.entity?.id
                handler.post {
                    if (id != null && handle?.id == id && live()) {
                        handleListener = obj to obj.addInputListener(handleInputListener(id))
                    }
                }
            },
        ),
        ComposeViewPanelRegistration(
            R.id.spatial_launcher_preview,
            composeViewCreator = { _, context -> ComposeView(context).apply { setContent {
                Box(Modifier.fillMaxSize().padding(3.dp).border(2.dp, Color(0xFFD6B2E7)), contentAlignment = Alignment.TopCenter) {
                    Text(placement.value?.let { "${it.entry.label} · Ubuntu" } ?: "棚をここに置く", color = Color.White)
                }
            } } },
            settingsCreator = { settings(2.4f, 1.5f, 540f) },
        ),
        ComposeViewPanelRegistration(
            R.id.spatial_launcher_shortcut,
            composeViewCreator = { entity, context -> ComposeView(context).apply { setContent {
                MaterialTheme {
                    val shortcut = shortcuts[entity.id]
                    if (shortcut != null) {
                        Row(Modifier.fillMaxSize().background(Color(0xFF182B38)),
                            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.Center) {
                            Button(onClick = { launchShortcut(shortcut.selection) }) { Text("${shortcut.selection.entry.label} · Ubuntu", maxLines = 1) }
                            TextButton(onClick = { shortcuts.remove(entity.id)?.entity?.destroy() }) { Text("削除") }
                        }
                    }
                }
            } } },
            settingsCreator = { settings(.28f, .094f) },
        ),
    )

    private fun settings(width: Float, height: Float, dpPerMeter: Float? = null) = UIPanelSettings(
        shape = QuadShapeOptions(width = width, height = height),
        display = dpPerMeter?.let { DpPerMeterDisplayOptions(dpPerMeter = it) } ?: DpPerMeterDisplayOptions(),
        style = PanelStyleOptions(themeResourceId = R.style.EidolonTheme),
    )

    @Composable
    fun Presets() {
        val expanded = remember { mutableStateOf(false) }
        val colors = ButtonDefaults.outlinedButtonColors(
            contentColor = Color(0xFFD6C6FF),
            disabledContentColor = Color(0xFFAEB6C7),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            OutlinedButton(onClick = { expanded.value = !expanded.value }, colors = colors) { Text("棚の表示方式") }
            Text(preset.value.title, color = Color.White)
        }
        WristSettings()
        if (expanded.value) {
            for (row in LauncherPreset.entries.chunked(2)) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    for (option in row) {
                        OutlinedButton(enabled = preset.value != option, onClick = { choose(option) }, colors = colors) {
                            Text(option.title)
                        }
                    }
                }
            }
        }
    }

    @OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
    @Composable
    private fun WristSettings() {
        val expanded = remember { mutableStateOf(false) }
        OutlinedButton(onClick = { expanded.value = !expanded.value },
            colors = ButtonDefaults.outlinedButtonColors(contentColor = Color(0xFFD6C6FF))) { Text("手首ランチャー") }
        if (!expanded.value) return
        Text(wristStatus.value, color = Color.White)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            for (value in WristInput.entries) {
                FilterChip(selected = input.value == value, label = { Text(value.title) }, onClick = {
                    input.value = value
                    preferences.edit().putString("wristInput", value.name).apply()
                    resetWrist()
                })
            }
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            for (value in WristSide.entries) {
                FilterChip(selected = side.value == value, label = { Text("${value.title}手首") }, onClick = {
                    side.value = value
                    preferences.edit().putString("wristSide", value.name).apply()
                    calibrationStatus.value = ""
                    resetWrist()
                })
            }
            OutlinedButton(onClick = ::calibrate) { Text("今の向きを登録") }
            OutlinedButton(onClick = {
                preferences.edit().remove(upKey()).apply()
                calibrationStatus.value = "${side.value.title}を標準の向きに戻しました"
                resetWrist()
            }) { Text("標準に戻す") }
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            for (value in WristSkin.entries) {
                FilterChip(selected = skin.value == value, label = { Text(value.title) }, onClick = {
                    skin.value = value
                    preferences.edit().putString("wristSkin", value.name).apply()
                    resetWrist()
                })
            }
        }
        Text(calibrationStatus.value.ifEmpty {
            if (configuredUp() == null) "標準の向き" else "${side.value.title}に登録した向き"
        }, color = Color.White)
    }

    @Composable
    private fun WristMenu() = WristWheel(hover.value, pressed.value, skin.value)

    private fun dispatch(action: WristAction) {
        if (!active || !live() || !visibility.shown) return
        val theme = NativeSessionHost.themeUi.value
        when (action) {
            WristAction.APPS -> open()
            WristAction.SETTINGS -> openSettings()
            WristAction.RECALL -> if (preset.value == LauncherPreset.STANDARD) NativeSessionHost.showLauncher() else summon(force = true)
            WristAction.MOTION -> if (!theme.loading) NativeSessionHost.setTheme(theme.selection, !theme.reduced)
            WristAction.DECOR -> if (!theme.loading) {
                if (theme.selection == "off") NativeSessionHost.setTheme(lastTheme)
                else {
                    lastTheme = theme.selection
                    preferences.edit().putString("lastDecor", lastTheme).apply()
                    NativeSessionHost.setTheme("off")
                }
            }
        }
    }

    private fun wristInputListener(id: Long) = object : InputListener {
        override fun onInput(receiver: SceneObject, hitInfo: HitInfo, sourceOfInput: Entity,
            changed: Int, state: Int, downTime: Long): Boolean {
            if (movingShelf.value || actualInput != WristInput.CONTROLLERS) return false
            val button = selectionButton()
            if (changed and button == 0) return false
            val controller = runCatching { sourceOfInput.getComponent<Controller>() }.getOrNull()
            if (controller?.type != ControllerType.CONTROLLER) return false
            handler.post {
                if (!active || !live() || movingShelf.value || wrist?.id != id || actualInput != WristInput.CONTROLLERS) return@post
                if (this@SpatialLauncher.controller(side.value.left) == null ||
                    this@SpatialLauncher.controller(side.value.opposite.left) == null) {
                    resetWrist()
                    return@post
                }
                val target = targetAt(hitInfo.point)
                hover.value = target
                if (state and button != 0) {
                    press.start(sourceOfInput.id, target)
                    pressed.value = press.action
                    if (press.held) WristInputOwnership.controller = sourceOfInput.id
                } else {
                    val action = press.release(sourceOfInput.id, target)
                    pressed.value = press.action
                    if (WristInputOwnership.controller == sourceOfInput.id) WristInputOwnership.finishController(sourceOfInput.id)
                    action?.let(::dispatch)
                }
            }
            return true
        }

        override fun onPointerEvent(receiver: SceneObject, hitInfo: HitInfo, type: Int,
            sourceOfInput: Entity, scrollInfo: Vector2, semanticType: Int) {
            handler.post {
                if (wrist?.id != id || actualInput != WristInput.CONTROLLERS) return@post
                if (type == PointerEventType.Cancel.id) cancelPress()
                else if (type == PointerEventType.Unhover.id) hover.value = null
                else hover.value = targetAt(hitInfo.point)
            }
        }

        override fun stopInput(receiver: SceneObject, sourceOfInput: Entity, downTime: Long) {
            handler.post { if (wrist?.id == id && WristInputOwnership.controller == sourceOfInput.id) cancelPress() }
        }
    }

    private fun targetAt(point: Vector3): WristAction? {
        val local = wristPose?.inverse()?.times(point) ?: return null
        // WristWheel uses a 160dp circle in a 400dp/m panel (see settings below).
        return WristAction.hit(.5f + local.x / .40f, .5f - local.y / .40f, skin.value)
    }

    fun bind(scene: Scene) {
        close()
        this.scene = scene
    }

    fun setActive(value: Boolean) {
        if (active == value) return
        active = value
        handler.removeCallbacks(frame)
        lastInputFrameMs = 0L
        if (value) {
            shelf?.setComponent(Visible(true))
            shortcuts.values.forEach { it.entity.setComponent(Visible(true)) }
            handler.post(frame)
        } else {
            shelfAnchor.cancel(scene)
            NativeSessionHost.cancelLauncherPlacement()
            cancelAppPlacement()
            finishShelfMove(save = false)
            shelf?.setComponent(Visible(false))
            shortcuts.values.forEach { it.entity.setComponent(Visible(false)) }
            resetWrist()
        }
    }

    fun open() {
        if (!active || !live() || preset.value == LauncherPreset.STANDARD) {
            NativeSessionHost.showLauncher()
            return
        }
        if (preset.value == LauncherPreset.PINNED && shelf != null) hideShelf() else summon()
    }

    private fun choose(value: LauncherPreset) {
        NativeSessionHost.cancelLauncherPlacement()
        cancelAppPlacement()
        hideShelf()
        resetWrist()
        preset.value = value
        preferences.edit().putString("preset", value.name).apply()
        handler.removeCallbacks(frame)
        if (active) handler.post(frame)
        if (value == LauncherPreset.STANDARD) {
            NativeSessionHost.showLauncher()
        } else {
            summon()
        }
    }

    private fun summon(force: Boolean = false) {
        if (!active || !live() || movingShelf.value) return
        if (!force && shelfAnchor.fixed.value && pinnedPose == null) {
            shelfAnchor.find()
            return
        }
        if (force && shelfAnchor.busy) shelfAnchor.cancel(scene)
        val pose = if ((explicitlyPinned || preset.value == LauncherPreset.PINNED) && !force) pinnedPose else null
        val location = pose ?: viewer()?.times(Pose(Vector3(0f, -.05f, 1.55f))) ?: return
        if (preset.value == LauncherPreset.PINNED || explicitlyPinned) pinnedPose = location
        showShelf(location)
    }

    private fun showShelf(location: Pose) {
        if (!active || !live() || !valid(location)) return
        val existing = shelf
        if (existing != null) {
            NativeSessionHost.refreshLauncherMenu()
            existing.setComponent(Transform(location))
            return
        }
        NativeSessionHost.launcherExpanded.value = false
        shelf = Entity.create(listOf(Panel(R.id.spatial_app_shelf), Transform(location), Grabbable(false)))
        NativeSessionHost.spatialShelfOpen = true
        NativeSessionHost.refreshLauncherMenu()
    }

    private fun hideShelf() {
        cancelAppPlacement()
        finishShelfMove(save = false)
        shelfAnchor.cancel(scene)
        shelfListener?.let { (obj, token) -> runCatching { obj.removeInputListener(token) } }
        shelfListener = null
        ShelfReasonFocus.clear()
        if (shelf != null) NativeSessionHost.appDialog.value = null
        shelfKeyboard?.close()
        shelfKeyboard = null
        shelf?.destroy()
        shelf = null
        NativeSessionHost.spatialShelfOpen = false
    }

    private fun moveShelf() {
        val entity = shelf ?: return
        if (movingShelf.value || shelfAnchor.fixed.value) return
        cancelAppPlacement()
        cancelPress()
        poseBeforeMove = getAbsoluteTransform(entity)
        entity.setComponent(Grabbable(true, GrabbableType.FACE, false, .45f, 3f))
        shelfWasGrabbed = false
        movingShelf.value = true
    }

    private fun physicalController(source: Entity): Controller? =
        runCatching { source.getComponent<Controller>() }.getOrNull()
            ?.takeIf { it.isActive && it.type == ControllerType.CONTROLLER }

    private fun startShelfTriggerMove(id: Long, source: Entity, button: Int) {
        val panel = shelf?.takeIf { it.id == id } ?: return
        val tracking = physicalController(source) ?: return
        if (!active || !live() || !movingShelf.value || shelfTriggerDrag != null ||
            panel.getComponent<Grabbable>().isGrabbed || tracking.buttonState and button == 0) return
        val aim = controller(button == ButtonBits.ButtonTriggerL) ?: return
        val start = getAbsoluteTransform(panel)
        if (!valid(start)) return
        shelfTriggerDrag = ShelfTriggerDrag(source, button, aim.inverse() * start)
        WristInputOwnership.controller = source.id
        // The controller captured by the trigger owns the transform until it releases.
        panel.setComponent(Grabbable(false))
    }

    private fun updateShelfMove() {
        val panel = shelf ?: return
        val drag = shelfTriggerDrag
        if (drag == null) {
            val grabbed = panel.getComponent<Grabbable>().isGrabbed
            if (shelfWasGrabbed && !grabbed) finishShelfMove()
            shelfWasGrabbed = grabbed
            return
        }
        val tracking = physicalController(drag.source)
        val aim = controller(drag.button == ButtonBits.ButtonTriggerL)
        val original = poseBeforeMove
        if (tracking == null || aim == null || original == null) {
            finishShelfMove(save = false)
            return
        }
        val candidate = Pose((aim * drag.offset).t, original.q)
        val accepted = validDrop(candidate)
        if (accepted) panel.setComponent(Transform(candidate))
        if (tracking.buttonState and drag.button == 0) finishShelfMove(save = accepted, releasedPose = candidate)
    }

    private fun finishShelfMove() = finishShelfMove(save = true)

    private fun finishShelfMove(save: Boolean, releasedPose: Pose? = null) {
        if (!movingShelf.value) return
        val panel = shelf
        if (panel != null) {
            val pose = releasedPose ?: getAbsoluteTransform(panel)
            val accepted = save && !panel.getComponent<Grabbable>().isGrabbed && valid(pose)
            if (accepted) { pinnedPose = pose; explicitlyPinned = true }
            else poseBeforeMove?.let { panel.setComponent(Transform(it)) }
            panel.setComponent(Grabbable(false))
        }
        shelfTriggerDrag?.source?.let { source ->
            if (WristInputOwnership.controller == source.id) WristInputOwnership.finishController(source.id)
        }
        shelfTriggerDrag = null
        poseBeforeMove = null
        shelfWasGrabbed = false
        movingShelf.value = false
    }

    private fun viewer(): Pose? = runCatching { scene?.getViewerPose() }.getOrNull()?.takeIf(::valid)

    @OptIn(SpatialSDKExperimentalAPI::class)
    private fun controller(left: Boolean): Pose? {
        val sample = runCatching { scene?.getControllerPoseAtTime(left, SystemClock.elapsedRealtimeNanos()) }.getOrNull()
        if (sample == null || sample.flags and ControllerPose.TrackedBits != ControllerPose.TrackedBits) return null
        val pose = sample.pose.takeIf(::valid) ?: return null
        val norm = pose.q.norm()
        return if (norm in .9f..1.1f) Pose(pose.t, pose.q.normalize()) else null
    }

    private inline fun <T> traceWrist(section: String, block: () -> T): T {
        if (!Trace.isEnabled()) return block()
        Trace.beginSection(section)
        return try { block() } finally { Trace.endSection() }
    }

    private fun updateWrist() {
        val sdk = scene ?: return
        val menuSide = side.value
        val selectedInput = input.value
        val extensions = handsExtensions()
        val canSampleHands = "XR_FB_hand_tracking_aim" in extensions && "XR_EXT_hand_tracking" in extensions
        // With the wrist off, the shelf can still provide a controller placement handle.
        val mode = if (selectedInput == WristInput.OFF && placement.value != null) WristInput.CONTROLLERS else selectedInput
        var handPair: WristHandTracking.Pair? = null
        var supportController: Pose? = null
        val next = selectTrackedWristInput(mode, actualInput) { source ->
            if (source == WristInput.CONTROLLERS) {
                traceWrist("Eidolon.wrist.controllers") {
                    supportController = controller(menuSide.left)
                    supportController != null && controller(menuSide.opposite.left) != null
                }
            } else {
                canSampleHands && traceWrist("Eidolon.wrist.hands") {
                    val pair = hands.sample(sdk, "XR_EXT_hand_tracking_data_source" in extensions)
                    handPair = pair
                    pair.get(menuSide) != null && pair.get(menuSide.opposite)?.aim != null
                }
            }
        }
        if (next != actualInput) {
            if (handleOrigin != null || handDragOrigin != null) cancelAppPlacement()
            resetWrist()
            actualInput = next
        }
        wristStatus.value = when {
            selectedInput == WristInput.OFF -> "手首表示はオフです"
            next != null -> "${next.title} · ${menuSide.title}手首"
            selectedInput == WristInput.HANDS && !canSampleHands -> "この環境では素手の追跡を開始できません"
            selectedInput == WristInput.HANDS && !hands.usable -> "素手の追跡を利用できません"
            selectedInput == WristInput.CONTROLLERS -> "両方のコントローラーを追跡できません"
            else -> "両手を追跡できる位置で呼び出してください"
        }
        if (next == null) return
        val view = viewer() ?: run { resetWrist(); return }
        val handMode = next == WristInput.HANDS
        val support = if (handMode) checkNotNull(handPair?.get(menuSide)).palm else checkNotNull(supportController)
        val relativeUp = if (handMode) Vector3(0f, -1f, 0f) else configuredUp() ?: Vector3(0f, 1f, 0f)
        val trackedPosition = support.t + Vector3(0f, .14f, .025f)
        val menuPose = Pose(trackedPosition, view.q)
        val now = SystemClock.elapsedRealtime()
        val holding = press.held || handleOrigin != null || handDragOrigin != null
        val shown = selectedInput != WristInput.OFF && visibility.update((support.q * relativeUp).y, now, holding)
        if (shown) {
            if (!holding || wristPose == null) wristPose = menuPose
            val pose = checkNotNull(wristPose)
            val menu = wrist
            if (menu == null) wrist = Entity.create(listOf(Panel(R.id.spatial_launcher_wrist), Transform(pose)))
            else menu.setComponent(Transform(pose))
        } else if (wrist != null) dropWrist(keepAppHandle = true)

        val app = placement.value
        if (app != null) {
            val latest = NativeSessionHost.launcherUi.value
            if (latest.runtime != app.snapshot.runtime || latest.capture != app.snapshot.capture ||
                latest.session != app.snapshot.session || latest.revision != app.snapshot.revision ||
                latest.entries.none { it.id == app.entry.id }) {
                cancelAppPlacement()
            }
        }
        val shelfPose = if (placement.value == null) null else shelf?.let(::getAbsoluteTransform)
        val home = when {
            shelfPose != null -> shelfPose * Pose(Vector3(.65f, .10f, 0f))
            shown && preset.value == LauncherPreset.PULL -> menuPose * Pose(Vector3(0f, -.26f, 0f))
            else -> null
        }
        if (home != null) updateHandle(home, handMode)
        else clearHandle()
        if (handMode) {
            val hand = checkNotNull(handPair?.get(menuSide.opposite))
            updateHandInput(sdk, checkNotNull(hand.aim), hand.pinching, view)
        }
    }

    private fun updateHandle(home: Pose, handMode: Boolean) {
        val item = handle ?: Entity.create(listOf(
            Panel(R.id.spatial_launcher_handle), Transform(home),
        )).also { handle = it }
        if (handMode) {
            if (handDragOrigin == null) item.setComponent(Transform(home))
            return
        }
        val source = controllerDrag
        val origin = handleOrigin
        val offset = controllerOffset
        if (source == null || origin == null || offset == null) {
            item.setComponent(Transform(home))
            return
        }
        val controller = runCatching { source.getComponent<Controller>() }.getOrNull()
        val sourcePose = runCatching { getAbsoluteTransform(source) }.getOrNull()
        if (controller?.isActive != true || controller.type != ControllerType.CONTROLLER ||
            sourcePose == null || !valid(sourcePose)) {
            clearHandle()
            return
        }
        val candidate = sourcePose * offset
        item.setComponent(Transform(candidate))
        if (controller.buttonState and selectionButton() != 0) showPreview(candidate)
        else {
            endControllerDrag()
            clearPreview()
            finishDrop(origin, candidate)
        }
    }

    private fun selectionButton() = if (side.value.left) ButtonBits.ButtonTriggerR else ButtonBits.ButtonTriggerL

    private fun handleInputListener(id: Long) = object : InputListener {
        override fun onInput(receiver: SceneObject, hitInfo: HitInfo, sourceOfInput: Entity,
            changed: Int, state: Int, downTime: Long): Boolean {
            if (actualInput != WristInput.CONTROLLERS || changed and selectionButton() == 0) return false
            if (state and selectionButton() != 0) handler.post {
                val item = handle?.takeIf { it.id == id }
                if (!active || !live() || actualInput != WristInput.CONTROLLERS || item == null ||
                    controllerDrag != null || press.held) return@post
                val controller = runCatching { sourceOfInput.getComponent<Controller>() }.getOrNull()
                if (controller?.isActive != true || controller.type != ControllerType.CONTROLLER ||
                    controller.buttonState and selectionButton() == 0) return@post
                val from = runCatching { getAbsoluteTransform(sourceOfInput) }.getOrNull() ?: return@post
                if (!valid(from)) return@post
                val start = getAbsoluteTransform(item)
                controllerDrag = sourceOfInput
                handleOrigin = start
                controllerOffset = from.inverse() * start
                WristInputOwnership.controller = sourceOfInput.id
            }
            return true
        }

        override fun onPointerEvent(receiver: SceneObject, hitInfo: HitInfo, type: Int,
            sourceOfInput: Entity, scrollInfo: Vector2, semanticType: Int) {
            if (type == PointerEventType.Cancel.id) handler.post {
                if (handle?.id == id && controllerDrag?.id == sourceOfInput.id) clearHandle()
            }
        }
    }

    private fun endControllerDrag() {
        val source = controllerDrag
        if (source != null && WristInputOwnership.controller == source.id) {
            WristInputOwnership.finishController(source.id)
        }
        controllerDrag = null
        controllerOffset = null
        handleOrigin = null
    }

    private fun updateHandInput(sdk: Scene, aim: Pose, pinching: Boolean, view: Pose) {
        val ownsPinch = press.held || handDragOrigin != null
        if (ownsPinch) WristInputOwnership.handTailUntil = SystemClock.elapsedRealtime() + 100L
        // A pinch that began while the menu was hidden still has to reach neutral.
        val hit = if (wrist != null || handle != null) sdk.lineSegmentIntersect(aim.t, aim * Vector3(0f, 0f, 8f)) else null
        val target = if (wrist?.id != null && wrist?.id == hit?.entity?.id) hit?.point?.let(::targetAt) else null
        hover.value = target
        if (!movingShelf.value && pinching && !handPinching && handNeutral) {
            val item = handle
            if (item != null && hit?.entity?.id == item.id) {
                val start = getAbsoluteTransform(item)
                handDragOrigin = start
                handDragOffset = aim.inverse() * start
                WristInputOwnership.handTailUntil = SystemClock.elapsedRealtime() + 100L
            } else {
                press.start(HAND_SOURCE, target)
                if (press.held) WristInputOwnership.handTailUntil = SystemClock.elapsedRealtime() + 100L
            }
        }
        val origin = handDragOrigin
        val offset = handDragOffset
        if (origin != null && offset != null) {
            val moved = aim * offset
            val candidate = Pose(moved.t, view.q.removePitchAndRoll())
            handle?.setComponent(Transform(candidate))
            showPreview(candidate)
            if (!pinching) {
                handDragOffset = null
                handDragOrigin = null
                clearPreview()
                finishDrop(origin, candidate)
            }
        } else if (!pinching && handPinching) {
            press.release(HAND_SOURCE, target)?.let(::dispatch)
        }
        pressed.value = press.action
        handPinching = pinching
        if (!pinching) handNeutral = true
    }

    private fun showPreview(candidate: Pose) {
        if (!valid(candidate)) { clearPreview(); return }
        val pose = Pose(candidate.t, viewer()?.q?.removePitchAndRoll() ?: candidate.q)
        val entity = preview
        if (entity == null) preview = Entity.create(listOf(Panel(R.id.spatial_launcher_preview), Transform(pose)))
        else entity.setComponent(Transform(pose))
    }

    private fun finishDrop(origin: Pose, candidate: Pose) {
        if (candidate.t.distanceTo(origin.t) < .20f || !validDrop(candidate)) return
        val pose = Pose(candidate.t, viewer()?.q?.removePitchAndRoll() ?: candidate.q)
        val app = placement.value
        if (app == null) showShelf(pose) else commitAppPlacement(app, pose)
    }

    private fun clearPreview() {
        preview?.destroy()
        preview = null
    }

    private fun clearHandle() {
        endControllerDrag()
        handleListener?.let { (obj, token) -> runCatching { obj.removeInputListener(token) } }
        handleListener = null
        handle?.destroy()
        handle = null
        handDragOrigin = null
        handDragOffset = null
        clearPreview()
    }

    private fun beginAppPlacement(state: LauncherUi, entry: LauncherEntry, shortcut: Boolean) {
        if (!active || !live() || shelf == null) return
        if (shortcut && shortcuts.size >= 4) {
            NativeSessionHost.launcherPlacementMessage.value = "起動ボタンは4個まで置けます。"
            return
        }
        cancelAppPlacement()
        NativeSessionHost.launcherPlacementMessage.value = if (shortcut) "取っ手をつかみ、起動ボタンを置く場所で離してください。"
            else "取っ手をつかみ、窓を開く場所で離してください。"
        val source = if (state.linux) LauncherSource.UBUNTU else LauncherSource.TERMUX
        placement.value = Placement(source, shortcutBinding(source), state, entry, shortcut)
    }

    private fun cancelAppPlacement() {
        if (placement.value == null) return
        clearHandle()
        placement.value = null
        NativeSessionHost.launcherPlacementMessage.value = ""
    }

    private fun commitAppPlacement(selection: Placement, pose: Pose) {
        cancelAppPlacement()
        if (selection.shortcut) {
            val entity = Entity.create(listOf(Panel(R.id.spatial_launcher_shortcut), Transform(pose)))
            shortcuts[entity.id] = Shortcut(entity, selection)
        } else {
            val latest = NativeSessionHost.shelfState(selection.source)
            val snapshot = selection.snapshot
            if (latest.runtime != snapshot.runtime || latest.capture != snapshot.capture ||
                latest.session != snapshot.session || latest.revision != snapshot.revision || latest.busy) return
            val wire = PanelPoseCodec.fromSdk(pose) ?: return
            val scene = NativeSessionHost.currentSceneEpoch()
            NativeSessionHost.guardAppLaunch(selection.source, snapshot, selection.entry, onShelf = true) {
                NativeSessionHost.launchInPlace(snapshot, selection.entry.id, wire, scene)
            }
        }
    }

    private fun shortcutBinding(source: LauncherSource): String = when (source) {
        LauncherSource.UBUNTU -> NativeSessionHost.linuxConnectionUi.value.savedBinding
        LauncherSource.TERMUX -> NativeSessionHost.connectionUi.value.savedToken
    }

    private fun launchShortcut(selection: Placement) {
        if (!active || !live()) return
        val latest = NativeSessionHost.shelfState(selection.source)
        val entry = latest.entries.firstOrNull {
            val desktopId = selection.entry.desktopId
            if (desktopId == null) it.id == selection.entry.id else it.desktopId == desktopId
        }
        if (selection.binding.isBlank() || shortcutBinding(selection.source) != selection.binding || !latest.connected ||
            latest.menuRefreshFailed || entry?.available != true || latest.busy) {
            NativeSessionHost.launcherPlacementMessage.value = "接続先と一覧を更新し、対象のアプリを確認してください。"
            return
        }
        NativeSessionHost.launchShelfApp(selection.source, latest, entry)
    }

    private fun validDrop(pose: Pose): Boolean {
        if (!valid(pose)) return false
        val viewer = viewer() ?: return false
        return pose.t.distanceTo(viewer.t) in .60f..4.0f
    }

    private fun valid(pose: Pose): Boolean = listOf(
        pose.t.x, pose.t.y, pose.t.z, pose.q.x, pose.q.y, pose.q.z, pose.q.w,
    ).all(Float::isFinite)

    private fun cancelPress() {
        press.clear()
        pressed.value = null
        hover.value = null
        if (controllerDrag == null && shelfTriggerDrag == null)
            WristInputOwnership.controller?.let(WristInputOwnership::finishController)
    }

    private fun resetWrist() {
        actualInput = null
        visibility.reset()
        dropWrist()
        handNeutral = false
        handPinching = false
    }

    private fun dropWrist(keepAppHandle: Boolean = false) {
        cancelPress()
        wristListener?.let { (obj, token) -> runCatching { obj.removeInputListener(token) } }
        wristListener = null
        wrist?.destroy()
        wrist = null
        wristPose = null
        if (!keepAppHandle || placement.value == null) clearHandle()
    }

    override fun close() {
        setActive(false)
        hands.close()
        hideShelf()
        dropWrist()
        shortcuts.values.forEach { it.entity.destroy() }
        shortcuts.clear()
        pinnedPose = null
        scene = null
    }
}
