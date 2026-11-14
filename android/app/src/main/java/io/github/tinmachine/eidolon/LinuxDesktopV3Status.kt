package io.github.tinmachine.eidolon

import org.json.JSONObject

data class LinuxDesktopV3View(
    val phase: String,
    val message: String,
    val firstFailure: String? = null,
    val cleanupFailure: String? = null,
) {
    val ready: Boolean get() = phase == "ready"
}

/** Read-only presentation of native transport/B1/I4 observations. Command ACKs are not an input. */
object LinuxDesktopV3Status {
    fun observe(runtime: Long, session: String, snapshot: JSONObject, previousFirstFailure: String? = null): LinuxDesktopV3View {
        val transport = snapshot.optJSONObject("transport")
        val report = transport?.optJSONObject("desktop_v3_transport")?.takeIf {
            runtime > 0 && LinuxDesktopV3Catalog.positiveDecimal(session) &&
                transport.opt("session") == session && it.opt("session") == session &&
                it.opt("protocol_revision") == LinuxDesktopV3Catalog.REVISION
        }
        val presentation = snapshot.optJSONObject("desktop_v3_presentation").takeIf { report != null }
        val first = previousFirstFailure ?: reason(presentation, "first_failure") ?: reason(report, "error") ?:
            reason(report?.optJSONObject("faults"), "first")
        val cleanup = reason(snapshot, "desktop_v3_cleanup_error") ?: reason(report?.optJSONObject("faults"), "cleanup")
        val transportPhase = report?.optJSONObject("transport")?.optString("phase")
        val presentationPhase = presentation?.optString("state")
        val phase = when {
            report?.opt("cleanup_pending") == true || presentationPhase == "pending" ||
                transportPhase == "cleanup_pending" || transport?.opt("state") == "CleanupPending" ||
                snapshot.opt("desktop_v3_finalizer_pending") == true -> "pending"
            presentationPhase == "stopping" || transportPhase == "stopping" ||
                report?.opt("terminal_observed") == true && transportPhase != "complete" -> "stopping"
            presentationPhase == "ready" && first == null && cleanup == null &&
                isReady(runtime, session, snapshot, report, presentation) -> "ready"
            presentationPhase in setOf("awaiting_sdk", "ready") -> "awaiting_sdk"
            presentationPhase == "awaiting_main" || transportPhase in setOf("forwarded", "started") -> "awaiting_main"
            else -> "catalog_waiting"
        }
        return LinuxDesktopV3View(phase, when (phase) {
            "ready" -> "アプリを表示中 · 現在のウィンドウへ入力できます"
            "awaiting_main" -> "対象アプリのメインウィンドウを待っています"
            "awaiting_sdk" -> "ウィンドウの表示準備中です。SDKと描画の確認を待っています"
            "stopping" -> "アプリと転送処理を停止しています"
            "pending" -> "前回の回収が未完了です。利用可能な回収操作を確認してください"
            else -> if (transportPhase == "catalog") "固定アプリを確認しました。選択すると起動します"
                else "接続先の固定アプリ一覧を確認してください"
        }, first, cleanup)
    }

    fun settings(pending: Boolean): LinuxDesktopV3View = if (pending)
        LinuxDesktopV3View("save_pending", "設定保存は未完了です。元の保存処理を再開してください")
    else LinuxDesktopV3View("configuration_mismatch", "保存した設定の組が一致しません。接続設定を確認してください")

    private fun reason(value: JSONObject?, key: String): String? =
        (value?.opt(key) as? String)?.takeIf { it.isNotEmpty() && it !in setOf("null", "NONE") }

    private fun isReady(runtime: Long, session: String, snapshot: JSONObject, report: JSONObject?,
        presentation: JSONObject?): Boolean {
        if (report == null || presentation == null) return false
        val transport = report.optJSONObject("transport") ?: return false
        val target = snapshot.optJSONObject("desktop_v3_sdk_target") ?: return false
        val key = target.optJSONObject("target") ?: return false
        return transport.opt("phase") == "started" && transport.opt("launch_accepted") == true &&
            transport.opt("journal_pending") == true && report.opt("started_observed") == true &&
            report.opt("terminal_observed") == false && report.opt("cleanup_pending") == false &&
            reason(report, "error") == null && reason(report.optJSONObject("faults"), "first") == null &&
            presentation.opt("startup_completed") == true && presentation.opt("cleanup_pending") == false &&
            reason(presentation, "first_failure") == null &&
            target.opt("runtime") == runtime.toString() && key.opt("session") == session &&
            listOf("window", "scene", "generation").all { (key.opt(it) as? String)?.let(LinuxDesktopV3Catalog::positiveDecimal) == true } &&
            presentation.opt("main") == key.opt("window") && target.opt("action") == "create" &&
            (target.opt("create_command") as? String)?.let(LinuxDesktopV3Catalog::positiveDecimal) == true &&
            (report.opt("launch_request") as? String)?.let(LinuxDesktopV3Catalog::positiveDecimal) == true &&
            target.opt("acknowledged") == true && target.opt("binding_present") == true && target.opt("renderer_frame_seen") == true
    }
}

/** Fixed reason mapping only. Neither remote stderr nor unknown strings become user-visible message bodies. */
object LinuxDesktopV3Messages {
    fun reason(code: String?): String? = when (code) {
        null, "", "null", "NONE" -> null
        "APP_NOT_INSTALLED" -> "対象アプリが接続先に導入されていません"
        "APP_PACKAGE_UNSUPPORTED" -> "対象アプリのパッケージ版が対応条件と一致しません"
        "APP_DEPENDENCY_CHANGED" -> "接続先の依存ファイルが確認時から変わっています"
        "APP_BACKEND_UNSUPPORTED" -> "対象アプリの表示方式を利用できません"
        "APP_SETTINGS_UNVERIFIED" -> "アプリ設定の保存方式を確認できませんでした"
        "APP_ORIGIN_UNKNOWN" -> "起動したアプリとウィンドウの対応を確認できませんでした"
        "APP_WINDOW_UNSUPPORTED" -> "対応していないウィンドウ構成が検出されました"
        "APP_STATE_BUSY" -> "同じアプリ設定を使用している処理の終了を待ってください"
        "APP_STATE_INVALID" -> "アプリ設定の所有情報が一致しません"
        "APP_STATE_RECOVERY_REQUIRED", "RECOVERY_REQUIRED", "local_admission_unavailable", "previous_io_pending" ->
            "前回の処理が未回収です。同じ接続先と元の処理で回収してください"
        "APP_LAUNCH_TIMEOUT" -> "対象アプリの起動を期限内に確認できませんでした"
        "APP_PRESENTATION_TIMEOUT" -> "ウィンドウ表示を期限内に確認できませんでした"
        "APP_PRESENTATION_FAILED" -> "現在のウィンドウを表示できませんでした"
        "APP_STARTUP_CANCELLED" -> "アプリの起動が取り消されました"
        "APP_OUTPUT_RATE_LIMIT" -> "アプリの出力量が上限を超えたため停止しました"
        "APP_V3_NOT_IMPLEMENTED" -> "この構成ではLinux Desktop v3の起動に対応していません"
        "STATE_LIMIT" -> "状態・ウィンドウ履歴の上限に達しました。回収後にRuntimeを作り直してください"
        "CAPABILITY_STALE", "DESKTOP_CATALOG_STALE", "scope_mismatch" -> "アプリ一覧の確認内容が古くなっています。再確認してください"
        "DESKTOP_CATALOG_INVALID" -> "接続先のアプリ一覧が固定の対応項目と一致しません"
        "ssh_io" -> "回収中のSSH通信を完了できませんでした"
        "command_timeout" -> "接続先の処理完了を期限内に確認できませんでした"
        "channel_incomplete", "forward_retirement", "router_drain" -> "前回の通信経路の終了確認が残っています"
        "forward_request" -> "アプリの転送経路を用意できませんでした"
        "journal_reserve", "journal_update", "journal_clear" -> "回収記録の保存処理が未完了です"
        "local_retirement_timeout" -> "端末内のウィンドウ・描画資源の回収が未完了です"
        else -> if(code.startsWith("SAVE_")) settingsReason(code)
            else "処理の完了を確認できませんでした。接続先と回収状態を確認してください"
    }

    fun settingsReason(code: String?): String? = when (code) {
        null, "", "null", "NONE" -> null
        "SETTINGS_SAVE_PENDING" -> "設定の有効化は未完了です。元の保存処理を明示的に再開してください"
        "SETTINGS_INCONSISTENT", "DESKTOP_CONFIGURATION_MISMATCH" -> "設定と登録情報の組が一致しないため、接続を開始できません"
        "SETTINGS_SAVE_FAILED" -> "設定を保存できませんでした。表示された再開方法を確認してください"
        "SETTINGS_STALE", "PROFILE_CHANGED", "STORAGE_STALE" -> "保存中に設定が変わりました。最新の状態を読み直してください"
        "DESKTOP_V3_CONFIG_INVALID" -> "v3設定JSONの形式・容量・固定bundleの照合値を確認してください"
        "DESKTOP_PINS_REQUIRED" -> "この接続先に対応する転送用ファイルの確認情報が必要です"
        "RUNTIME_CONFIG_TOO_LARGE" -> "接続設定全体の容量が実行時の上限を超えています"
        "RUNTIME_CONFIG_INVALID" -> "接続設定を実行時の形式へ変換できませんでした"
        "RUNTIME_RECLAMATION_PENDING", "SDK_RETIREMENT_PENDING", "LOCAL_OWNER_UNKNOWN" ->
            "前のRuntimeの回収を証明できないため、設定は保存していません"
        "SAVE_ADMISSION_CLOSED" -> "別の保存・回収処理を実行しています。完了または明示再開を確認してください"
        "SAVE_OWNER_UNKNOWN", "SAVE_CAPTURE_STALE" -> "元のRuntimeと保存要求の対応を確認できないため、設定を保存できません"
        "SAVE_REMOTE_PENDING" -> "接続先のアプリの終了が未確認です。元の接続先での回収が必要です"
        "SAVE_ORIGIN_PENDING" -> "前のアプリのウィンドウ・描画資源の回収が未完了です"
        "SAVE_RECOVERY_PENDING" -> "回収操作の完了を確認できませんでした。元の設定と回収記録を保持しています"
        "SAVE_OBSERVATION_FAILED" -> "元のRuntimeの状態を取得できず、回収を確認できませんでした"
        "SAVE_STOP_REJECTED" -> "元のRuntimeへの停止要求が受理されませんでした"
        "SAVE_SDK_DISPOSAL_FAILED" -> "SDKの表示資源を破棄できず、設定を保存できませんでした"
        "SAVE_DESTROY_REJECTED" -> "元のRuntimeの破棄が受理されず、設定を保存できませんでした"
        "SAVE_RUNTIME_PENDING", "SAVE_RECLAMATION_INCOMPLETE" -> "接続・ウィンドウを含むRuntime全体の回収が未完了です"
        "SAVE_PUBLICATION_FAILED" -> "設定の保存・読み戻しが未完了です。元の保存要求を明示的に再開してください"
        else -> "設定保存は未完了です。元の設定と回収記録を保持しています"
    }
}
