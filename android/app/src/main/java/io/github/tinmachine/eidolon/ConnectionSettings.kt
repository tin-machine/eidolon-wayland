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

/** 公開鍵だけをclipboardへ送る。秘密鍵はこのUIへ渡さない。 */
@Composable fun ConnectionSettings() {
    val state=NativeSessionHost.connectionUi.value
    val context=LocalContext.current
    var expanded by remember {mutableStateOf(false)}
    Button(onClick={expanded=!expanded;NativeSessionHost.resetInput()}) {Text(if(expanded) "接続設定を閉じる" else "接続設定・初回ペアリング")}
    if(!expanded) return
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
