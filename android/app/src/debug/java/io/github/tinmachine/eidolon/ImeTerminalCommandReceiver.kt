package io.github.tinmachine.eidolon

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Shell-only fixed debug controls. Does not pause or launch an Activity. */
class ImeTerminalCommandReceiver: BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if(intent.action!="io.github.tinmachine.eidolon.IME_TERMINAL_COMMAND" ||
            intent.extras?.keySet()!=setOf("case","nonce")) return
        val name=intent.getStringExtra("case") ?: return
        val nonce=intent.getStringExtra("nonce") ?: return
        if(name !in setOf("ime-terminal-focus","ime-terminal-toggle","ime-terminal-stop") || !nonce.matches(Regex("[0-9a-f]{32}"))) return
        ImeTerminalCase.command(name,nonce)
    }
}
