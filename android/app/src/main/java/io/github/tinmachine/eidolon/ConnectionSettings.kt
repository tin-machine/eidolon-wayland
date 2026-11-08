package io.github.tinmachine.eidolon

import android.content.ClipData
import android.content.ClipboardManager
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect

/** 公開鍵だけをclipboardへ送る。秘密鍵はこのUIへ渡さない。 */
@Composable fun ConnectionSettings() {
    val state=NativeSessionHost.connectionUi.value
    val context=LocalContext.current
    val focus=LocalFocusManager.current
    LaunchedEffect(NativeSessionHost.launcherFocusReset.value) {focus.clearFocus(force=true)}
    val linux=NativeSessionHost.selectedConnection.value==LinuxConnectionProfile.ID
    Text("接続先: ${NativeSessionHost.connectionTarget.value}",color=Color.White)
    Row {
        Button(enabled=state.canSave && linux,onClick={NativeSessionHost.selectConnection("termux-local")}) {Text("このQuestのTermuxを選ぶ")}
        Button(enabled=!linux && (state.canSave || NativeSessionHost.canSelectLinuxRecovery.value),onClick={NativeSessionHost.selectConnection(LinuxConnectionProfile.ID)}) {
            Text(if(NativeSessionHost.canSelectLinuxRecovery.value) "Linuxの回収先を選ぶ" else "別のLinuxを選ぶ")
        }
    }
    if(!state.canSave) Text("接続先の変更は、前の接続・ウィンドウ・回収の終了確認後にできます。",color=Color.White)
    var expanded by remember {mutableStateOf(false)}
    Button(onClick={expanded=!expanded;NativeSessionHost.resetInput()}) {Text(if(expanded) "接続設定を閉じる" else "接続設定・初回ペアリング")}
    if(!expanded) return
    if(linux) { LinuxConnectionSettings();return }
    var username by remember(state.savedToken) {mutableStateOf(state.username)}
    var hostKey by remember(state.savedToken) {mutableStateOf(state.hostKey)}
    var confirmed by remember(username,hostKey,state.savedToken) {mutableStateOf(false)}
    val proposed=runCatching {ConnectionProfile.parse(username,hostKey)}.getOrNull()
    Column {
        Text("1. APKの公開鍵をTermuxに登録します",color=Color.White)
        SelectionContainer {Text(state.publicKey.ifEmpty {"公開鍵を準備中"},color=Color.White)}
        Button(enabled=state.publicKey.isNotEmpty(),onClick={
            context.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("Eidolon public key",state.publicKey))
        }) {Text("APK公開鍵をコピー")}
        Text("Termuxで eidolon-pair に公開鍵を渡し、表示されたusernameとhost_keyを以下へ貼り付けます。",color=Color.White)
        Text("2. Termuxの公開情報を入力します（端末内の127.0.0.1:8022へ接続）",color=Color.White)
        OutlinedTextField(value=username,onValueChange={username=it.take(64)},label={Text("Termux username")},singleLine=true,enabled=state.canSave)
        OutlinedTextField(value=hostKey,onValueChange={hostKey=it.take(4096)},label={Text("host_key（ssh-ed25519 …）")},enabled=state.canSave)
        Text(if(proposed==null) "usernameとEd25519公開ホスト鍵を入力してください" else "照合する指紋: ${proposed.fingerprint}",color=Color.White)
        if(state.hostKey.isNotEmpty()) Text("保存済みの指紋: ${ConnectionProfile.fingerprint(state.hostKey)}",color=Color.White)
        Row {
            Checkbox(checked=confirmed,onCheckedChange={confirmed=it},enabled=state.canSave && proposed!=null)
            Text("Termuxのhost_fingerprintと一致することを確認しました",color=Color.White)
        }
        Button(enabled=state.canSave && proposed!=null && confirmed,onClick={
            NativeSessionHost.saveConnection(username,hostKey,state.savedToken,confirmed)
        }) {Text(if(state.savedToken.isEmpty()) "接続設定を保存" else "確認した鍵・接続設定へ変更")}
        if(!state.canSave) Text("接続を切断し、前回の終了確認が済むと保存できます。",color=Color.White)
        Text(state.message,color=Color.White)
    }
}

@Composable private fun LinuxConnectionSettings() {
    val state=NativeSessionHost.linuxConnectionUi.value
    val context=LocalContext.current
    var name by remember(state.savedBinding) {mutableStateOf(state.displayName)}
    var host by remember(state.savedBinding) {mutableStateOf(state.host)}
    var port by remember(state.savedBinding) {mutableStateOf(state.port)}
    var username by remember(state.savedBinding) {mutableStateOf(state.username)}
    var hostKey by remember(state.savedBinding) {mutableStateOf(state.hostKey)}
    var confirmed by remember(name,host,port,username,hostKey,state.savedBinding) {mutableStateOf(false)}
    val draft=LinuxConnectionDraft(name,host,port,username,hostKey)
    // 入力検証だけ。保存時はHostが実installationと現在bindingを別途捕捉する。
    val proposed=runCatching {LinuxConnectionProfile.propose(draft,"0".repeat(32),null)}.getOrNull()
    val ipv4=proposed?.let {LinuxConnectionProfile.endpointSupport(it)==LinuxConnectionProfile.EndpointSupport.IPV4}==true
    var pins by remember(state.pinsToken,state.savedBinding) {mutableStateOf(state.pinsJson)}
    var pinsConfirmed by remember(pins,state.pinsToken,state.savedBinding) {mutableStateOf(false)}
    val validPins=runCatching {LinuxDesktopPins.parse(pins)}.isSuccess
    Column {
        Text("1. このLinux接続用の公開鍵を登録します",color=Color.White)
        Text("Termux用とは別の鍵です。接続先ユーザーのSSH公開鍵認証へ登録してください。",color=Color.White)
        SelectionContainer {Text(state.publicKey.ifEmpty {"公開鍵を準備中"},color=Color.White)}
        Button(enabled=state.publicKey.isNotEmpty(),onClick={
            context.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("Eidolon Linux public key",state.publicKey))
        }) {Text("Linux用公開鍵をコピー")}
        Text("2. 接続先と、別経路で照合したホスト鍵を保存します",color=Color.White)
        ConnectionField(name,{name=it.take(160)},"表示名",state.canSave)
        ConnectionField(host,{host=it.take(253)},"LinuxのIPv4アドレス",state.canSave)
        ConnectionField(port,{port=it.take(5)},"SSHポート",state.canSave)
        ConnectionField(username,{username=it.take(64)},"Linux username",state.canSave)
        ConnectionField(hostKey,{hostKey=it.take(4096)},"host_key（ssh-ed25519 …）",state.canSave,false)
        Text("この版の接続対象はIPv4です。DNS名・IPv6では接続できません。",color=Color.White)
        val fingerprint=proposed?.hostKey?.let {ConnectionProfile.fingerprint(it)}
        Text(fingerprint?.let {"照合する指紋: $it"} ?: "接続先とEd25519公開ホスト鍵を入力してください",color=Color.White)
        Row {
            Checkbox(checked=confirmed,onCheckedChange={confirmed=it},enabled=state.canSave && proposed!=null && ipv4)
            Text("接続先のホスト鍵の指紋と一致することを確認しました",color=Color.White)
        }
        Button(enabled=state.canSave && proposed!=null && ipv4 && confirmed,onClick={
            NativeSessionHost.saveLinuxConnection(draft,state.savedBinding,confirmed)
        }) {Text("Linux接続設定を保存")}
        Text("3. 固定GTKデモの転送用ファイルを確認します",color=Color.White)
        Text("固定builderの --print-pins で出力したJSONを貼り付けてください。接続先を変更した場合も再確認が必要です。",color=Color.White)
        ConnectionField(pins,{pins=it.take(4096)},"転送用ファイルの確認情報（JSON）",state.canSave,false)
        Text(if(state.pinsReady) "保存した接続先に対して確認済みです" else "この接続先に対する確認・保存が必要です",color=Color.White)
        Row {
            Checkbox(checked=pinsConfirmed,onCheckedChange={pinsConfirmed=it},enabled=state.canSave && validPins && state.savedBinding.isNotEmpty())
            Text("接続先に用意した固定bundleの確認情報と一致します",color=Color.White)
        }
        Button(enabled=state.canSave && validPins && state.savedBinding.isNotEmpty() && pinsConfirmed,onClick={
            NativeSessionHost.saveLinuxPins(pins,state.savedBinding,state.pinsToken,pinsConfirmed)
        }) {Text("転送用ファイルの確認情報を保存")}
        Text(state.message,color=Color.White)
    }
}

@Composable private fun ConnectionField(value:String,change:(String)->Unit,label:String,enabled:Boolean,singleLine:Boolean=true) {
    val token=remember {"connection-${java.util.UUID.randomUUID()}"}
    DisposableEffect(token) {onDispose {NativeSessionHost.panelTextInput(token,false)}}
    OutlinedTextField(value=value,onValueChange=change,label={Text(label)},enabled=enabled,singleLine=singleLine,
        modifier=Modifier.onFocusChanged {NativeSessionHost.panelTextInput(token,it.isFocused)})
}
