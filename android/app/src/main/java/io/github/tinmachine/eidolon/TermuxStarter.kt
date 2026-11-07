package io.github.tinmachine.eidolon

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import java.util.concurrent.ConcurrentHashMap

/** 固定helperのみ。返却stdout/stderrやerrmsgを保存・表示しない。 */
object TermuxStarter {
    const val PERMISSION = "com.termux.permission.RUN_COMMAND"
    private data class Request(val pending: PendingIntent, val complete: (String?) -> Unit)
    private val requests = ConcurrentHashMap<String,Request>()
    fun start(context: Context, token: String, complete: (String?) -> Unit): String? {
        val app=context.applicationContext
        if(app.packageManager.getLaunchIntentForPackage("com.termux")==null) return "Termuxが見つかりません"
        if(app.checkSelfPermission(PERMISSION)!=PackageManager.PERMISSION_GRANTED) return "Termuxの実行権限を許可してください"
        val callback=Intent(app,TermuxStartReceiver::class.java).setAction("io.github.tinmachine.eidolon.TERMUX_RESULT")
            .setData(Uri.parse("eidolon-start:$token"))
        val pending=PendingIntent.getBroadcast(app,0,callback,PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_ONE_SHOT)
        requests[token]=Request(pending,complete)
        return try {
            app.startService(Intent("com.termux.RUN_COMMAND").setClassName("com.termux","com.termux.app.RunCommandService")
                .putExtra("com.termux.RUN_COMMAND_PATH","/data/data/com.termux/files/usr/bin/eidolon-start")
                .putExtra("com.termux.RUN_COMMAND_WORKDIR","/data/data/com.termux/files/home")
                .putExtra("com.termux.RUN_COMMAND_BACKGROUND",true)
                .putExtra("com.termux.RUN_COMMAND_PENDING_INTENT",pending))
                ?: throw IllegalStateException("SERVICE_MISSING")
            null
        } catch(_:Exception) {
            cancel(token);"Termuxを開いてから再試行してください（起動制限または連携設定不足）"
        }
    }
    fun cancel(token: String) {requests.remove(token)?.pending?.cancel()}
    fun receive(intent: Intent) {
        val token=intent.data?.takeIf {it.scheme=="eidolon-start"}?.schemeSpecificPart ?: return
        val request=requests.remove(token) ?: return
        request.pending.cancel()
        val result=runCatching {intent.getBundleExtra("result")}.getOrNull()
        val error=when {
            result==null || result.getInt("err",0)!=-1 -> "Termux連携を確認してください（allow-external-apps と固定helper）"
            result.getInt("exitCode",-1)!=0 -> "Termuxの起動helperが失敗しました。初回設定を確認してください"
            else -> null
        }
        request.complete(error)
    }
}

/** 非export。Termuxに渡す一回限りの明示PendingIntentだけで受信する。 */
class TermuxStartReceiver: BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {TermuxStarter.receive(intent)}
}
