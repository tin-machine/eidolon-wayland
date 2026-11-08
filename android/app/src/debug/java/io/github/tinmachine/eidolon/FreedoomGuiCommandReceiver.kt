package io.github.tinmachine.eidolon

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** shell DUMP限定。定義/argv/env/target/キー本文を受けず、固定case/nonceだけを扱う。 */
class FreedoomGuiCommandReceiver:BroadcastReceiver() {
    override fun onReceive(context:Context,intent:Intent) {
        if(intent.action!="io.github.tinmachine.eidolon.FREEDOOM_GUI_COMMAND" || intent.extras?.keySet()!=setOf("case","nonce")) return
        val name=intent.getStringExtra("case") ?: return
        val nonce=intent.getStringExtra("nonce") ?: return
        if(name !in setOf("freedoom-keys","freedoom-close","freedoom-stop") || !nonce.matches(Regex("[0-9a-f]{32}"))) return
        FreedoomGuiCase.command(name,nonce)
    }
}
