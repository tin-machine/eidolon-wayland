package io.github.tinmachine.eidolon

import androidx.compose.foundation.background
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import android.os.Build
import android.util.Log

@Composable
fun ControlPanel(chooseTheme: () -> Unit, allowTermux: () -> Unit, openTermux: () -> Unit) {
    var clicks by remember { mutableIntStateOf(0) }
    var details by remember { mutableStateOf(false) }
    val input=NativeSessionHost.inputStatus.value
    val device=NativeSessionHost.deviceMetrics.value
    Column(Modifier.fillMaxSize().background(Color(0xFF102331)).padding(16.dp),
        verticalArrangement=Arrangement.spacedBy(6.dp), horizontalAlignment=Alignment.CenterHorizontally) {
        Text("Eidolon · Build ${BuildConfig.VERSION_CODE}",color=Color(0xFF67E8C3),style=MaterialTheme.typography.titleMedium)
        Text(input.message,color=if(input.reason=="READY") Color(0xFF67E8C3) else Color(0xFFFFD59E),style=MaterialTheme.typography.titleMedium)
        Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
            Button(onClick={NativeSessionHost.openTerminal()}) {Text("ターミナルを開く")}
            Button(onClick={NativeSessionHost.showLauncher()}) {Text("アプリ")}
            if(NativeSessionHost.terminalBusy.value) Button(onClick={NativeSessionHost.disconnect()}) {Text("取消")}
            when(input.action) {
                InputStatus.Action.FINISH_EDITING -> Button(onClick={NativeSessionHost.finishLayoutEditing()}) {Text(input.actionLabel)}
                else -> Unit
            }
        }
        if(NativeSessionHost.termuxHelp.value) Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
            Button(onClick=allowTermux) {Text("Termux連携を許可")}
            Button(onClick=openTermux) {Text("Termuxを開く")}
        }
        val visible=NativeSessionHost.windowChoices.value.filter {it.slot!=null}
        if(visible.isNotEmpty()) Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
            for(window in visible) Button(enabled=!NativeSessionHost.layoutEditing.value,onClick={NativeSessionHost.focusTerminal(window)}) {
                Text("窓 ${window.window} へ入力")
            }
        }
        if(!details) {
            Text(device.summary(),color=Color.White,style=MaterialTheme.typography.bodySmall)
            Text(device.networkSummary(),color=Color.White,style=MaterialTheme.typography.bodySmall,maxLines=2,
                overflow=androidx.compose.ui.text.style.TextOverflow.Ellipsis)
            Text("接続先: このQuestのTermux · ${NativeSessionHost.connectionSummary.value}",color=Color.White,style=MaterialTheme.typography.bodySmall,maxLines=2)
        }
        if(NativeSessionHost.launcherExpanded.value) LauncherSettings(Modifier.weight(1f))
        else Button(onClick={details=!details}) {Text(if(details) "設定と詳細を閉じる" else "設定と詳細")}
        if(details && !NativeSessionHost.launcherExpanded.value) Column(Modifier.weight(1f).verticalScroll(rememberScrollState()),
            verticalArrangement=Arrangement.spacedBy(12.dp),horizontalAlignment=Alignment.CenterHorizontally) {
            Text(device.summary(),color=Color.White,style=MaterialTheme.typography.bodySmall)
            Text(device.networkSummary(),color=Color.White,style=MaterialTheme.typography.bodySmall)
            Text("接続先: このQuestのTermux · ${NativeSessionHost.connectionSummary.value}",color=Color.White,style=MaterialTheme.typography.bodySmall)
            Text("Runtime: ${NativeSessionHost.summary.value}",color=Color.White)
            Text(NativeSessionHost.credentialSummary.value,color=Color.White)
            ConnectionSettings()
            Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                Button(onClick=allowTermux) {Text("Termux連携を許可")}
                Button(onClick=openTermux) {Text("Termuxを開く")}
            }
            for (window in NativeSessionHost.windowChoices.value) {
                Text("ウィンドウ ${window.window} · ${window.slot?.let { "表示枠 ${it+1}" } ?: "一覧に保持"}",color=Color.White)
                if(window.ambiguous) Text("同じアプリを複数開いているため、配置は今回の接続中に保持します",color=Color.White)
                Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                    Button(onClick={NativeSessionHost.selectWindow(window)}) { Text("配置の対象にする") }
                    Button(onClick={NativeSessionHost.showWindow(window,0)}) { Text("枠1へ表示") }
                    Button(onClick={NativeSessionHost.showWindow(window,1)}) { Text("枠2へ表示") }
                    Button(onClick={NativeSessionHost.closeWindow(window)}) { Text("閉じる") }
                }
            }
            Text(NativeSessionHost.workspaceSummary.value,color=Color.White)
            val observing = NativeSessionHost.workloadObservation.value
            Button(onClick={NativeSessionHost.setWorkloadObservation(!observing)}) {
                Text(if(observing) "負荷の計測を停止" else "負荷の計測を再開")
            }
            val theme = NativeSessionHost.themeUi.value
            Text(theme.message, color=Color.White)
            Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                Button(onClick={NativeSessionHost.setTheme("off")}) { Text("装飾なし") }
                Button(onClick={NativeSessionHost.setTheme("builtin")}) { Text("Cyan Orbit") }
                Button(enabled=theme.imported,onClick={NativeSessionHost.setTheme("imported")}) { Text("読み込んだテーマ") }
            }
            Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                Button(onClick={chooseTheme()}) { Text("テーマZIPを読み込む") }
                Button(enabled=!theme.loading,onClick={NativeSessionHost.setTheme(theme.selection,!theme.reduced)}) {
                    Text(if(theme.reduced) "アニメーションを有効に" else "動きを抑える")
                }
            }
            Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                Button(onClick={NativeSessionHost.setBackground("passthrough")}) { Text("現実を背景に") }
                Button(onClick={NativeSessionHost.setBackground("virtual")}) { Text("単色の背景に") }
            }
            Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                Button(onClick={NativeSessionHost.setKeyboardLayout("us")}) { Text("US配列") }
                Button(onClick={NativeSessionHost.setKeyboardLayout("jis")}) { Text("JIS配列") }
            }
            Button(enabled=NativeSessionHost.layoutAvailable.value,onClick={if(NativeSessionHost.layoutEditing.value) NativeSessionHost.finishLayoutEditing() else NativeSessionHost.setLayoutEditing(true)}) {
                Text(if(NativeSessionHost.layoutEditing.value) "配置を確定して入力へ戻る" else "配置を編集")
            }
            if(NativeSessionHost.layoutEditing.value) {
                Text("グリップで移動できます。ボタンは選択したパネルに適用します。",color=Color.White)
                Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                    Button(onClick={NativeSessionHost.nudgePose(x=-.1)}) { Text("左") }
                    Button(onClick={NativeSessionHost.nudgePose(x=.1)}) { Text("右") }
                    Button(onClick={NativeSessionHost.nudgePose(y=.1)}) { Text("上") }
                    Button(onClick={NativeSessionHost.nudgePose(y=-.1)}) { Text("下") }
                }
                Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                    Button(onClick={NativeSessionHost.nudgePose(z=-.1)}) { Text("手前") }
                    Button(onClick={NativeSessionHost.nudgePose(z=.1)}) { Text("奥") }
                    Button(onClick={NativeSessionHost.nudgePose(yaw=-15.0)}) { Text("角度 −15°") }
                    Button(onClick={NativeSessionHost.nudgePose(yaw=15.0)}) { Text("角度 +15°") }
                }
            }
            Text(NativeSessionHost.layoutSummary.value,color=Color.White)
            Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                Button(enabled=NativeSessionHost.layoutAvailable.value,onClick={NativeSessionHost.resizeTerminal(800,600)}) { Text("800×600") }
                Button(enabled=NativeSessionHost.layoutAvailable.value,onClick={NativeSessionHost.resizeTerminal(1024,768)}) { Text("1024×768") }
                Button(enabled=NativeSessionHost.layoutAvailable.value,onClick={NativeSessionHost.resizeTerminal(1280,720)}) { Text("1280×720") }
            }
            Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                Button(enabled=NativeSessionHost.layoutAvailable.value,onClick={NativeSessionHost.setPhysicalSize(.96,.72)}) { Text("小さく表示") }
                Button(enabled=NativeSessionHost.layoutAvailable.value,onClick={NativeSessionHost.setPhysicalSize(1.2,.9)}) { Text("標準表示") }
                Button(enabled=NativeSessionHost.layoutAvailable.value,onClick={NativeSessionHost.setPhysicalSize(1.44,1.08)}) { Text("大きく表示") }
            }
            Button(onClick = { NativeSessionHost.resetInput() }) { Text("操作パネルへ戻る") }
            Button(onClick = { NativeSessionHost.disconnect() }) { Text("接続を閉じる") }
            Text("Spatial SDK 0.15.0 · Android API ${Build.VERSION.SDK_INT}", color = Color.White)
            Button(onClick = {
                clicks++
                Log.i("EidolonPhase0", "panel_click count=$clicks version=${BuildConfig.VERSION_CODE}")
            }) { Text("確認 / Verify ($clicks)") }
            Button(onClick = { NativeSessionHost.requestSnapshot() }) { Text("診断を保存 / Snapshot") }
        }
    }
}
