package io.github.tinmachine.eidolon

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** DUMP権限の固定caseだけ。Activity/既存PTYへの入力を起動しない。 */
class IoWorkloadCommandReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != "io.github.tinmachine.eidolon.IO_WORKLOAD_COMMAND" ||
            intent.extras?.keySet() != setOf("case", "nonce")) return
        val name = intent.getStringExtra("case") ?: return
        val nonce = intent.getStringExtra("nonce") ?: return
        if (name !in setOf("io-workload", "io-workload-stop") || !nonce.matches(Regex("[0-9a-f]{32}"))) return
        IoWorkloadCase.command(name, nonce)
    }
}
