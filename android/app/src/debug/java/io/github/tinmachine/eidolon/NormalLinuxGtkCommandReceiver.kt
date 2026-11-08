package io.github.tinmachine.eidolon

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** shell DUMP限定。通常UIで保存・選択した接続先を使い、設定や入力本文をIntentで受けない。 */
class NormalLinuxGtkCommandReceiver:BroadcastReceiver() {
    override fun onReceive(context:Context,intent:Intent) {
        if(!BuildConfig.DEBUG || intent.component?.packageName!=context.packageName || intent.component?.className!=javaClass.name ||
            intent.action!="io.github.tinmachine.eidolon.NORMAL_LINUX_GTK_COMMAND" || intent.extras?.keySet()!=setOf("case","nonce")) return
        val name=intent.getStringExtra("case") ?: return
        val nonce=intent.getStringExtra("nonce") ?: return
        if(name !in setOf("normal-linux-gtk","normal-linux-gtk-stop") || !nonce.matches(Regex("[0-9a-f]{32}"))) return
        NormalLinuxGtkCase.command(name,nonce)
    }
}
