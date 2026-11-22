@file:OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)

package io.github.tinmachine.eidolon

import androidx.compose.foundation.background
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.hoverable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsHoveredAsState
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.unit.dp
import java.util.UUID

private val candy = Color(0xFFFFC4DF)
private val launcherColors = darkColorScheme(
    primary = Color(0xFFE5C5FA),
    onPrimary = Color(0xFF28203D),
    secondaryContainer = Color(0xFFD6C6FF),
    onSecondaryContainer = Color(0xFF28203D),
    surface = Color(0xFF281D30),
    onSurface = Color.White,
    onSurfaceVariant = Color(0xFFFFE5F3),
    outline = Color(0xFFB996B9),
)

internal enum class LauncherSource(val profile: String, val title: String, val linux: Boolean) {
    TERMUX("termux-local", "このQuestのTermux", false),
    UBUNTU(LinuxConnectionProfile.ID, "Ubuntu", true),
}

private fun icon(name: String): String = when(name) {
    "builtin:terminal" -> ">_"
    "builtin:editor" -> "✎"
    "builtin:game" -> "★"
    else -> "▶"
}

private fun launchUnavailableReason(code: String?): String? = when (code) {
    "terminal_required" -> "端末が必要"
    "dbus_activation_required" -> "D-Bus による起動が必要"
    "executable_unavailable" -> "実行ファイルが見つかりません"
    "exec_format_unsupported" -> "起動コマンドの形式が未対応"
    "workdir_unavailable" -> "作業ディレクトリを開けません"
    else -> null
}

/** The visible snapshot's immutable identity travels with the click, including across delayed UI dispatch. */
private fun launch(state: LauncherUi, id: String) {
    if (state.v3) NativeSessionHost.launchRegistered(state.runtime, state.capture, state.session, state.revision, id)
    else NativeSessionHost.launchRegistered(state.session, state.revision, id)
}

@Composable
fun LauncherStrip(open: () -> Unit = NativeSessionHost::showLauncher) {
    val state=NativeSessionHost.launcherUi.value
    val focus=LocalFocusManager.current
    Row(Modifier.fillMaxSize().padding(8.dp),horizontalArrangement=Arrangement.spacedBy(8.dp,Alignment.CenterHorizontally),
        verticalAlignment=Alignment.CenterVertically) {
        if(!state.linux) Button(modifier=Modifier.weight(1f),onClick={focus.clearFocus();NativeSessionHost.openTerminal()},colors=ButtonDefaults.buttonColors(containerColor=candy,contentColor=Color(0xFF28203D))) {
            Text(">_ ターミナル")
        } else Button(modifier=Modifier.weight(1f),enabled=state.canConnect || state.canInspect || state.canRecover || state.canStop,
            onClick={focus.clearFocus();when {
                state.canRecover -> NativeSessionHost.recoverLinuxDesktop()
                state.canInspect -> NativeSessionHost.inspectLinuxDesktop()
                state.canConnect -> NativeSessionHost.connect()
                state.canStop -> NativeSessionHost.disconnect()
            }}) {Text(when {state.canRecover -> "前回の回収";state.canInspect -> "アプリを確認";state.canConnect -> "Linuxへ接続";else -> "停止・切断"})}
        for(entry in state.entries.filter {it.id!="terminal"}.take(2)) {
            Button(modifier=Modifier.weight(1f),enabled=state.connected && entry.available && !state.busy && !NativeSessionHost.terminalBusy.value,
                onClick={launch(state,entry.id)},
                colors=ButtonDefaults.buttonColors(containerColor=Color(0xFFD6C6FF),contentColor=Color(0xFF28203D))) {
                Text("${icon(entry.icon)} ${entry.label}",maxLines=1,overflow=androidx.compose.ui.text.style.TextOverflow.Ellipsis)
            }
        }
        Button(modifier=Modifier.weight(1f),onClick=open,colors=ButtonDefaults.buttonColors(containerColor=Color(0xFFFFDF9E),contentColor=Color(0xFF28203D))) {
            Text(if(state.linux) "Linuxアプリ" else "＋ アプリ")
        }
    }
}

@Composable
private fun LauncherField(label: String, value: String, change: (String)->Unit, modifier: Modifier=Modifier) {
    val token=remember {UUID.randomUUID().toString()}
    DisposableEffect(token) {onDispose {NativeSessionHost.panelTextInput(token,false)}}
    OutlinedTextField(value=value,onValueChange=change,label={Text(label)},singleLine=true,
        modifier=modifier.fillMaxWidth().onFocusChanged {NativeSessionHost.panelTextInput(token,it.isFocused)})
}

@Composable
fun LauncherSettings(
    modifier: Modifier = Modifier,
    close: () -> Unit = { NativeSessionHost.launcherExpanded.value = false },
    placeShelf: (() -> Unit)? = null,
    finishShelf: (() -> Unit)? = null,
    toggleFix: (() -> Unit)? = null,
    fixed: Boolean = false,
    anchorStatus: String = "",
    placeApp: ((LauncherUi, LauncherEntry, Boolean) -> Unit)? = null,
    launcherControls: (@Composable () -> Unit)? = null,
    openConnectionSettings: (() -> Unit)? = null,
) {
    val focus = LocalFocusManager.current
    var query by remember { mutableStateOf("") }
    var favoritesOnly by remember { mutableStateOf(false) }
    var settings by remember { mutableStateOf(false) }
    var detail by remember { mutableStateOf<LauncherSource?>(null) }
    DisposableEffect(Unit) { onDispose { ShelfReasonFocus.clear() } }
    LaunchedEffect(ShelfReasonFocus.request.intValue) {
        ShelfReasonFocus.hovered?.let { detail = it }
    }
    LaunchedEffect(NativeSessionHost.launcherFocusReset.value) { focus.clearFocus(force = true) }
    val hosts = LauncherSource.entries.associateWith { NativeSessionHost.shelfState(it) }
    val rows = hosts.flatMap { (source, state) ->
        val entries = if (state.entries.isNotEmpty() || state.catalogComplete) state.entries
            else NativeSessionHost.shelfCatalogs[launcherSourceKey(source)]?.entries.orEmpty()
        entries.filter { entry ->
            (!favoritesOnly || launcherFavoriteKey(source, entry) in NativeSessionHost.launcherFavorites) &&
                listOfNotNull(entry.label, entry.desktopId, source.title, hostName(source))
                    .any { it.contains(query.trim(), ignoreCase = true) }
        }.map { entry -> source to entry }
    }.sortedBy { it.second.label.lowercase() }
    MaterialTheme(colorScheme = launcherColors) {
        BoxWithConstraints(modifier.fillMaxSize().background(Color(0xFF281D30)).padding(12.dp)) {
            val wide = maxWidth >= 620.dp
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    TextButton(modifier = Modifier.weight(1f), enabled = placeShelf != null || finishShelf != null,
                        onClick = { (placeShelf ?: finishShelf)?.invoke() }) {
                        Text(if (finishShelf != null) "アプリランチャー · トリガー長押しで移動" else "アプリランチャー", color = candy)
                    }
                    if (toggleFix != null) TextButton(onClick = toggleFix) {
                        Text(if (fixed) "◆ 固定を解除" else "◇ ここに固定")
                    }
                    TextButton(onClick = { settings = !settings }) { Text("設定") }
                    TextButton(onClick = close) { Text("閉じる") }
                }
                if (anchorStatus.isNotEmpty()) Text(anchorStatus, color = candy)
                NativeSessionHost.launcherPlacementMessage.value.takeIf { it.isNotEmpty() }?.let { Text(it, color = candy) }
                if (settings) {
                    Column(Modifier.weight(1f).verticalScroll(rememberScrollState())) {
                        launcherControls?.invoke()
                        openConnectionSettings?.let { TextButton(onClick = it) { Text("接続設定") } }
                        for ((source, state) in hosts) {
                            if (state.canStop) TextButton(onClick = { NativeSessionHost.stopShelfHost(source) }) {
                                Text(hostName(source) + " の接続を閉じる")
                            }
                            if (source.linux && state.canRecover) TextButton(onClick = NativeSessionHost::recoverLinuxDesktop) { Text("前回の回収") }
                            if (source.linux && state.companionCleanup) TextButton(onClick = NativeSessionHost::recoverCompanion) { Text("追加アプリを回収") }
                        }
                        if (!NativeSessionHost.launcherUi.value.linux) TermuxLauncherFiles(NativeSessionHost.launcherUi.value)
                    }
                } else {
                    Row(Modifier.fillMaxSize(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        if (wide) Column { ShelfTabs(favoritesOnly) { favoritesOnly = it } }
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            if (!wide) Row { ShelfTabs(favoritesOnly) { favoritesOnly = it } }
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                LauncherField("アプリ名・ホスト名で検索", query, { query = it }, Modifier.weight(1f))
                                Text("起動枠 ${NativeSessionHost.launcherCapacity.value.used}/${LauncherCapacity.MAX_APPS}", color = candy)
                                TextButton(onClick = NativeSessionHost::refreshLauncherMenu) { Text("更新") }
                            }
                            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                                for ((source, state) in hosts) {
                                    val interactions = remember(source) { MutableInteractionSource() }
                                    val hovered by interactions.collectIsHoveredAsState()
                                    val failed = state.firstFailure != null || state.menuRefreshFailed
                                    LaunchedEffect(hovered) {
                                        if (hovered) { ShelfReasonFocus.hovered = source; if (failed) detail = source }
                                        else if (ShelfReasonFocus.hovered == source) ShelfReasonFocus.hovered = null
                                    }
                                    OutlinedButton(onClick = { detail = if (detail == source) null else source },
                                        interactionSource = interactions,
                                        modifier = Modifier.hoverable(interactions).onPreviewKeyEvent {
                                            if (it.nativeKeyEvent.keyCode == android.view.KeyEvent.KEYCODE_DPAD_UP) {
                                                detail = source
                                                true
                                            } else false
                                        }) {
                                        Text((when {
                                            failed -> "× "
                                            state.connected -> "● "
                                            state.busy || state.menuRefreshing -> "… "
                                            else -> "○ "
                                        }) + hostName(source))
                                    }
                                }
                            }
                            detail?.let { source ->
                                val state = hosts.getValue(source)
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text(state.message, Modifier.weight(1f), color = Color.White)
                                    TextButton(onClick = { NativeSessionHost.refreshShelfHost(source) }) { Text("再試行") }
                                    openConnectionSettings?.let { TextButton(onClick = it) { Text("接続設定") } }
                                }
                            }
                            if (rows.isEmpty()) Text(if (query.isNotBlank() || favoritesOnly) "一致するアプリはありません"
                                else if (hosts.values.any { it.busy || it.menuRefreshing }) "一覧を取得中"
                                else if (hosts.values.all { it.catalogComplete }) "取得したアプリは0件です"
                                else "ホストの状態を選ぶと、一覧の取得状況を確認できます", color = Color.White)
                            else Text("${rows.size} 件", color = candy)
                            LazyVerticalGrid(GridCells.Adaptive(220.dp), Modifier.weight(1f),
                                verticalArrangement = Arrangement.spacedBy(8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                items(rows, key = { (host, entry) -> launcherSourceKey(host) + ":" + entry.id }) { (host, entry) ->
                                    val state = hosts.getValue(host)
                                    ShelfAppCard(host, entry, state, placeApp)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ShelfTabs(favoritesOnly: Boolean, select: (Boolean) -> Unit) {
    FilterChip(!favoritesOnly, { select(false) }, { Text("すべてのアプリ") })
    FilterChip(favoritesOnly, { select(true) }, { Text("お気に入り") })
}

internal object ShelfReasonFocus {
    @Volatile var hovered: LauncherSource? = null
    val request = mutableIntStateOf(0)
    fun clear() { hovered = null }
}

private fun hostName(source: LauncherSource) = if (source.linux)
    NativeSessionHost.linuxConnectionUi.value.displayName.ifBlank { source.title } else source.title

@Composable
private fun ShelfAppCard(source: LauncherSource, entry: LauncherEntry, state: LauncherUi,
    place: ((LauncherUi, LauncherEntry, Boolean) -> Unit)?) {
    var menu by remember { mutableStateOf(false) }
    var messageAtClick by remember { mutableStateOf("") }
    var notice by remember(state.runtime, state.session, state.revision, entry.id) { mutableStateOf<String?>(null) }
    val favorite = launcherFavoriteKey(source, entry)
    val refusal = NativeSessionHost.launcherCapacity.value.refusal(source)
    val live = refusal == null && state.connected && !state.menuRefreshFailed && !state.busy && entry.available &&
        state.entries.any { it.id == entry.id && it.available }
    OutlinedCard(onClick = {
        if (live) { messageAtClick = state.message; notice = "起動を要求しました"; NativeSessionHost.launchShelfApp(source, state, entry) }
        else if (refusal != null) notice = refusal
        else notice = launchUnavailableReason(entry.error) ?: if (entry.error != null) LauncherMessages.error(entry.error) else state.message
    }, colors = CardDefaults.outlinedCardColors(containerColor = Color(0xFF402E48)), border = BorderStroke(1.dp, Color(0xFFA987A5))) {
        Row(Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(icon(entry.icon), color = candy)
            Column(Modifier.weight(1f).padding(horizontal = 8.dp)) {
                Text(entry.label, color = Color.White, style = MaterialTheme.typography.titleMedium)
                Text(hostName(source), color = Color(0xFFBBF2DC))
                val message = if (notice != null && (state.message != messageAtClick || state.firstFailure != null || state.menuRefreshFailed)) state.message else notice
                if (message != null || !live) Text(message ?: refusal ?: launchUnavailableReason(entry.error) ?: "起動準備待ち", color = Color(0xFFFFD59E))
            }
            TextButton(onClick = { menu = !menu }, modifier = Modifier.semantics { contentDescription = entry.label + " の操作" }) { Text("⋯") }
        }
        if (menu) Column {
            TextButton(onClick = { NativeSessionHost.toggleLauncherFavorite(favorite); menu = false }) {
                Text(if (favorite in NativeSessionHost.launcherFavorites) "お気に入りを解除" else "お気に入りに追加")
            }
            if (place != null) {
                if (state.v3 && state.runtime == NativeSessionHost.launcherUi.value.runtime)
                    TextButton(enabled = live, onClick = { place(state, entry, false); menu = false }) { Text("引き出して起動") }
                TextButton(onClick = { place(state, entry, true); menu = false }) { Text("起動ボタンを置く") }
            }
        }
    }
}

private fun launcherSourceKey(source: LauncherSource): String = if (source.linux)
    "ubuntu:${NativeSessionHost.linuxConnectionUi.value.savedBinding}" else source.profile

private fun launcherFavoriteKey(source: LauncherSource, entry: LauncherEntry): String =
    "${launcherSourceKey(source)}:${entry.desktopId ?: entry.id}"

@Composable
private fun TermuxLauncherFiles(state: LauncherUi) {
    val focus = LocalFocusManager.current
    var path by remember(state.path) { mutableStateOf(state.path) }
    var form by remember { mutableStateOf(LauncherForm()) }
    var fileMode by remember { mutableStateOf(false) }
    LauncherField("Termux内の設定ファイル",path,{path=it})
    Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
        Button(enabled=state.connected && !state.busy && path.isNotBlank(),onClick={NativeSessionHost.reloadLauncher(path)}) {Text("読込・再読込")}
        Button(onClick={fileMode=!fileMode}) {Text(if(fileMode) "一覧へ戻る" else "ファイルから起動・登録")}
    }
    if(fileMode) {
        Text("ターミナル内で実行 · 相対パスはTermuxのホーム基準",color=Color.White)
        LauncherField("実行ファイル・スクリプト",form.executable,{form=form.copy(executable=it)})
        LauncherField("インタープリター（任意）",form.interpreter,{form=form.copy(interpreter=it)})
        LauncherField("作業ディレクトリ",form.cwd,{form=form.copy(cwd=it)})
        form.args.forEachIndexed {index,arg ->
            Row(verticalAlignment=Alignment.CenterVertically) {
                LauncherField("引数 ${index+1}（空欄も1引数）",arg,{value -> form=form.copy(args=form.args.toMutableList().also {it[index]=value})},Modifier.weight(1f))
                TextButton(onClick={form=form.copy(args=form.args.filterIndexed {i,_-> i!=index})}) {Text("削除")}
            }
        }
        TextButton(enabled=form.args.size<64,onClick={form=form.copy(args=form.args+"")}) {Text("＋ 引数を追加")}
        LauncherField("登録ID",form.id,{form=form.copy(id=it)})
        LauncherField("表示名",form.label,{form=form.copy(label=it)})
        Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
            Button(enabled=state.connected && !state.busy && !NativeSessionHost.terminalBusy.value && form.valid(),
                onClick={focus.clearFocus(force=true);NativeSessionHost.launchFile(form)}) {Text("今回だけ起動")}
            Button(enabled=state.connected && !state.busy && form.valid() && path.isNotBlank(),
                onClick={NativeSessionHost.saveLauncher(form,path)}) {Text("設定へ保存")}
        }
        Text("同じ登録IDは置き換えます。保存だけでは起動しません。",color=Color.White,style=MaterialTheme.typography.bodySmall)
    }
}
