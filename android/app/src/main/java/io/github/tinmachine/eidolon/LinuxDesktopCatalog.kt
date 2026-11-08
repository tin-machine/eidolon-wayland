package io.github.tinmachine.eidolon

import org.json.JSONObject

/** 実inspectのUI専用receipt。診断へ出さず、sessionとrevisionをclick時にも照合する。 */
class LinuxDesktopCatalog private constructor(val session: String, val revision: String, private val scope: String) {
    fun accepts(session: String, revision: String, id: String) =
        session == this.session && revision == this.revision && id == APP_ID
    fun selection(): JSONObject = JSONObject().put("scope", scope).put("revision", revision).put("app_id", APP_ID)
    override fun toString() = "LinuxDesktopCatalog(<private>)"
    companion object {
        const val APP_ID = "gtk3-desktop"
        fun decode(session: String, value: JSONObject): LinuxDesktopCatalog {
            require(session.matches(Regex("[1-9][0-9]{0,18}")) && session.toLongOrNull() != null)
            val limit = value.get("instance_limit")
            require(value.keys().asSequence().toSet() == setOf("namespace", "selection", "label", "mode", "instance_limit") &&
                value.get("namespace") == "linux-desktop" && value.get("mode") == "gui" &&
                (limit is Int && limit == 1 || limit is Long && limit == 1L) && value.get("label") == "Linux アプリ") { "DESKTOP_CATALOG_INVALID" }
            val selection = value.getJSONObject("selection")
            require(selection.keys().asSequence().toSet() == setOf("scope", "revision", "app_id") &&
                selection.get("app_id") == APP_ID) { "DESKTOP_CATALOG_INVALID" }
            val scope = selection.get("scope") as? String ?: throw IllegalArgumentException("DESKTOP_CATALOG_INVALID")
            val revision = selection.get("revision") as? String ?: throw IllegalArgumentException("DESKTOP_CATALOG_INVALID")
            require(scope.matches(Regex("[0-9a-f]{64}")) && revision.matches(Regex("[0-9a-f]{64}"))) { "DESKTOP_CATALOG_INVALID" }
            return LinuxDesktopCatalog(session, revision, scope)
        }
    }
}

object LinuxDesktopMessages {
    fun error(code:String?):String? = when(code) {
        null,"","null" -> null
        "HOST_KEY_UNKNOWN" -> "接続先のホスト鍵を設定してください"
        "HOST_KEY_MISMATCH" -> "接続先のホスト鍵が変わっています。設定で指紋を照合してください"
        "AUTH_FAILED" -> "このLinux用の公開鍵を、接続先ユーザーに登録してください"
        "DNS_NOT_IMPLEMENTED" -> "この版ではIPv4アドレスを指定してください"
        "SSH_CONNECTION_REFUSED","AUTH_TIMEOUT","SSH_CONNECT_FAILED","CONNECT_TIMEOUT" -> "接続先・sshd・ネットワークを確認して接続し直してください"
        "REMOTE_EXIT","SSH_IO_FAILED" -> "Linuxとの通信が終了しました。回収状態を確認してください"
        else -> "Linuxとの処理を完了できませんでした。接続先と転送用ファイル、前回の回収状態を確認してください"
    }
}
