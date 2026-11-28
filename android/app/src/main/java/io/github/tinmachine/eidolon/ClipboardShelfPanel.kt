package io.github.tinmachine.eidolon

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import org.json.JSONObject

private data class ShelfCard(val id: String, val origin: String, val ageSeconds: Long, val bytes: Int)

@Composable
internal fun ClipboardShelfPanel(modifier: Modifier = Modifier) {
    var cards by remember { mutableStateOf(emptyList<ShelfCard>()) }
    var window by remember { mutableStateOf<NativeSessionHost.ClipboardWindow?>(null) }
    var pending by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf("") }
    var previousOutcome by remember { mutableStateOf<String?>(null) }
    var preview by remember { mutableStateOf<Pair<String, String>?>(null) }

    fun refresh() {
        val result = NativeSessionHost.clipboardShelfRequest("list")
        if (result.optString("status") != "ok") {
            cards = emptyList()
            preview = null
            window = null
            pending = false
            previousOutcome = null
            message = label(result.optString("reason"))
            return
        }
        window = NativeSessionHost.clipboardWindow()
        pending = result.optBoolean("pending")
        val rows = result.optJSONArray("cards")
        cards = if (rows == null) emptyList() else (0 until rows.length()).mapNotNull { index ->
            val entry = rows.optJSONObject(index) ?: return@mapNotNull null
            val runtime = entry.optString("runtime").toLongOrNull() ?: return@mapNotNull null
            val id = entry.optString("id")
            val sourceTarget = entry.optJSONObject("target") ?: return@mapNotNull null
            ShelfCard(id, NativeSessionHost.clipboardCardOrigin(runtime, sourceTarget),
                entry.optLong("age_ms") / 1000, entry.optInt("bytes"))
        }
        if (preview?.first !in cards.map { it.id }) preview = null
        val outcome = result.optString("outcome").takeUnless { it.isBlank() || it == "null" }
        if (outcome != previousOutcome) outcome?.let { message = label(it) }
        previousOutcome = outcome
    }

    fun request(type: String, card: String? = null, endpoint: NativeSessionHost.ClipboardWindow? = null): JSONObject {
        val result = NativeSessionHost.clipboardShelfRequest(type, card, endpoint)
        when (result.optString("status")) {
            "pending" -> {
                pending = true
                previousOutcome = null
                message = "処理中"
            }
            "error" -> message = label(result.optString("reason"))
        }
        return result
    }

    LaunchedEffect(Unit) {
        while (true) {
            refresh()
            delay(if (pending) 120 else 500)
        }
    }

    Column(modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        val selected = window
        Text(selected?.let { "選択中 · ${it.label}" } ?: "コピー元または貼り付け先の窓を選択してください")
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(enabled = selected != null && !pending, onClick = {
                if (selected != null) request("capture", endpoint = selected)
            }) { Text("選択中の窓から取得") }
            TextButton(enabled = cards.isNotEmpty() || pending, onClick = {
                request("clear")
                preview = null
                refresh()
            }) { Text("すべて削除") }
        }
        if (message.isNotBlank()) Text(message)
        for (card in cards) {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("${card.origin} · ${card.ageSeconds} 秒前 · ${card.bytes} バイト")
                    preview?.takeIf { it.first == card.id }?.let { Text(it.second) }
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        TextButton(onClick = {
                            if (preview?.first == card.id) {
                                preview = null
                            } else {
                                val result = request("preview", card.id)
                                if (result.optString("status") == "ok") {
                                    val text = result.getString("text")
                                    preview = card.id to if (result.optBoolean("truncated")) "$text…" else text
                                }
                            }
                        }) { Text(if (preview?.first == card.id) "隠す" else "表示") }
                        Button(enabled = selected != null && !pending, onClick = {
                            if (selected != null) request("paste", card.id, selected)
                        }) { Text("選択中の窓に渡す") }
                        TextButton(onClick = {
                            request("remove", card.id)
                            refresh()
                        }) { Text("削除") }
                    }
                }
            }
        }
    }
}

private fun label(reason: String): String = when (reason) {
    "CAPTURED" -> "クリップボードから取得しました"
    "OFFERED" -> "選択中の窓に渡しました。窓で通常の貼り付けを行ってください"
    "EMPTY", "NO_SELECTION", "SHELF_SELECTION" -> "コピー元の窓でテキストをコピーしてください"
    "STALE", "NOT_READY" -> "操作対象の窓をもう一度選択してください"
    "BUSY" -> "前の操作の完了をお待ちください"
    "TOO_LARGE" -> "取得できるテキストは1 MiBまでです"
    "SHELF_FULL" -> "棚の空きがありません。不要な項目を削除してください"
    "MISSING" -> "この項目は削除されたか期限が切れました"
    "NOT_TEXT", "INVALID_UTF8" -> "UTF-8 のテキストを取得できませんでした"
    "TIMEOUT" -> "クリップボードの応答を取得できませんでした"
    else -> "クリップボードの操作を完了できませんでした"
}
