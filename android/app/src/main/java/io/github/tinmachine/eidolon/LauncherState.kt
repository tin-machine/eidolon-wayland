package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject

data class LauncherEntry(val id: String, val label: String, val icon: String, val available: Boolean, val error: String?)
data class LauncherUi(
    val session: String = "", val connected: Boolean = false, val busy: Boolean = false,
    val revision: String = "", val builtin: Boolean = true, val total: Int = 0, val offset: Int = 0,
    val entries: List<LauncherEntry> = emptyList(), val path: String = ".config/eidolon/launcher.toml",
    val message: String = "ターミナルを開くとアプリ一覧を読み込めます",
)

object LauncherMessages {
    fun error(code: String?): String = when(code) {
        null -> ""
        "PATH_MISSING" -> "指定したファイルが見つかりません"
        "CATALOG_CHANGED" -> "設定が変更されています。再読込してください"
        "CATALOG_DUPLICATE_ID" -> "同じ登録IDが複数あります"
        "CATALOG_INVALID", "CATALOG_SCHEMA" -> "設定ファイルの形式を確認してください"
        "CATALOG_LIMIT" -> "設定・項目・引数の上限を超えています"
        "CATALOG_FILE_UNSAFE" -> "設定ファイルの所有者と書込権限を確認してください"
        "CATALOG_UNAVAILABLE" -> "設定の読込にはTermuxのPython 3.11以降が必要です"
        "EXECUTABLE_DENIED", "INTERPRETER_UNAVAILABLE" -> "実行ファイルの権限・インタープリターを確認してください"
        "EXECUTABLE_NOT_FILE" -> "通常の実行ファイルを指定してください"
        "CWD_UNAVAILABLE" -> "作業ディレクトリを利用できません"
        "GUI_NOT_VERIFIED" -> "GUIの起動は準備中です"
        "APP_NOT_FOUND" -> "登録が見つかりません。再読込してください"
        else -> "操作できませんでした。接続状態とファイルを確認してください"
    }
}

/** 引数欄は一つずつ保持し、空白で分割せずnativeへ渡す。本文は診断へ保存しない。 */
data class LauncherForm(val id: String = "my-app", val label: String = "マイアプリ", val executable: String = "",
    val interpreter: String = "", val cwd: String = ".", val args: List<String> = emptyList()) {
    fun valid(): Boolean = id.matches(Regex("[a-zA-Z0-9][a-zA-Z0-9_.-]{0,47}")) &&
        label.isNotBlank() && label.toByteArray().size <= 160 && executable.isNotBlank() &&
        listOf(executable,interpreter,cwd).all { it.toByteArray().size <= 1024 && it.none { c -> c.code < 32 || c.code in 127..159 } } &&
        label.none { it.code < 32 || it.code in 127..159 } && args.size <= 64 &&
        args.all { it.toByteArray().size <= 2048 && '\u0000' !in it } && args.sumOf { it.toByteArray().size } <= 6000
    fun definition(): JSONObject {
        require(valid())
        return JSONObject().put("id",id).put("label",label).put("mode","terminal").put("executable",executable)
            .put("args",JSONArray(args)).put("cwd",cwd.ifEmpty { "." }).put("icon","builtin:script").also {
                if(interpreter.isNotEmpty()) it.put("interpreter",interpreter)
            }
    }
}
