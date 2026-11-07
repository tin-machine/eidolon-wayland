package io.github.tinmachine.eidolon

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** DUMP権限を持つshellからの固定case/nonceだけを受ける。Activityは起動しない。 */
class GtkFixtureCommandReceiver: BroadcastReceiver() {
    override fun onReceive(context:Context,intent:Intent) {
        if(intent.action!="io.github.tinmachine.eidolon.GTK_FIXTURE_COMMAND" || intent.extras?.keySet()!=setOf("case","nonce")) return
        val name=intent.getStringExtra("case") ?: return
        val nonce=intent.getStringExtra("nonce") ?: return
        if(name !in setOf("gtk-input","gtk-dialog","gtk-resize","gtk-dialog-dismiss","gtk-resize-return","gtk-close","gtk-cancel") || !nonce.matches(Regex("[0-9a-f]{32}"))) return
        GtkFixtureCase.command(name,nonce)
    }
}
