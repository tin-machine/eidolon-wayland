package io.github.tinmachine.eidolon

import java.util.concurrent.Executor

/** 検査中の操作と、runtime切替え前にqueueした古い操作を通常runtimeへ再生しない。 */
internal class DebugSdkProductExecutor(private val lease: DebugSdkRuntimeLease, private val worker: Executor,
    private val admission: RuntimeAdmission? = null) : Executor {
    override fun execute(command: Runnable) {
        val ticket = lease.metadata().generation
        val epoch = admission?.ticket()
        if (admission != null && epoch == null) return
        if (!lease.permitsProduct(ticket)) return
        worker.execute { if (lease.permitsProduct(ticket) && (admission == null || admission.permits(checkNotNull(epoch)))) command.run() }
    }
}
