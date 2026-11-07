package io.github.tinmachine.eidolon

import androidx.compose.foundation.background
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
private fun icon(name: String): String = when(name) {
    "builtin:terminal" -> ">_"
    "builtin:editor" -> "✎"
    "builtin:game" -> "★"
    else -> "▶"
}

@Composable
fun LauncherStrip() {
    val state=NativeSessionHost.launcherUi.value
    val focus=LocalFocusManager.current
    Row(Modifier.fillMaxSize().padding(8.dp),horizontalArrangement=Arrangement.spacedBy(8.dp,Alignment.CenterHorizontally),
        verticalAlignment=Alignment.CenterVertically) {
        Button(modifier=Modifier.weight(1f),onClick={focus.clearFocus();NativeSessionHost.openTerminal()},colors=ButtonDefaults.buttonColors(containerColor=candy,contentColor=Color(0xFF28203D))) {
            Text(">_ ターミナル")
        }
        for(entry in state.entries.filter {it.id!="terminal"}.take(2)) {
            Button(modifier=Modifier.weight(1f),enabled=state.connected && entry.available && !state.busy && !NativeSessionHost.terminalBusy.value,
                onClick={NativeSessionHost.launchRegistered(state.session,state.revision,entry.id)},
                colors=ButtonDefaults.buttonColors(containerColor=Color(0xFFD6C6FF),contentColor=Color(0xFF28203D))) {
                Text("${icon(entry.icon)} ${entry.label}",maxLines=1,overflow=androidx.compose.ui.text.style.TextOverflow.Ellipsis)
            }
        }
        Button(modifier=Modifier.weight(1f),onClick={NativeSessionHost.showLauncher()},colors=ButtonDefaults.buttonColors(containerColor=Color(0xFFFFDF9E),contentColor=Color(0xFF28203D))) {
            Text("＋ アプリ")
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
fun LauncherSettings(modifier: Modifier=Modifier) {
    val state=NativeSessionHost.launcherUi.value
    val focus=LocalFocusManager.current
    LaunchedEffect(NativeSessionHost.launcherFocusReset.value) {focus.clearFocus(force=true)}
    var path by remember {mutableStateOf(state.path)}
    var form by remember {mutableStateOf(LauncherForm())}
    var fileMode by remember {mutableStateOf(false)}
    LaunchedEffect(state.path) {path=state.path}
    Column(modifier.fillMaxWidth().background(Color(0xFF182B38)).padding(12.dp).verticalScroll(rememberScrollState()),
        verticalArrangement=Arrangement.spacedBy(8.dp)) {
        Row(horizontalArrangement=Arrangement.spacedBy(8.dp),verticalAlignment=Alignment.CenterVertically) {
            Text("アプリランチャー",color=candy,style=MaterialTheme.typography.titleMedium)
            Button(onClick={NativeSessionHost.launcherExpanded.value=false}) {Text("閉じる")}
        }
        Text(state.message,color=Color.White)
        if(!state.connected) Button(onClick={NativeSessionHost.openTerminal()}) {Text("ターミナルを開いて接続")}
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
        } else {
            for(entry in state.entries) {
                Column {
                    Button(enabled=state.connected && entry.available && !state.busy && !NativeSessionHost.terminalBusy.value,
                        onClick={focus.clearFocus(force=true);NativeSessionHost.launchRegistered(state.session,state.revision,entry.id)}) {Text("${icon(entry.icon)} ${entry.label}")}
                    if(!entry.available) Text(LauncherMessages.error(entry.error),color=Color(0xFFFFD59E))
                }
            }
            Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                Button(enabled=!state.busy && state.offset>=8,onClick={NativeSessionHost.launcherPage(state.offset-8)}) {Text("前へ")}
                Text("${if(state.total==0) 0 else state.offset+1}–${minOf(state.offset+8,state.total)} / ${state.total}",color=Color.White)
                Button(enabled=!state.busy && state.offset+8<state.total,onClick={NativeSessionHost.launcherPage(state.offset+8)}) {Text("次へ")}
            }
        }
    }
}
