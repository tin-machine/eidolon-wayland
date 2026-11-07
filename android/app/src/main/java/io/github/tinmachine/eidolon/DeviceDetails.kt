package io.github.tinmachine.eidolon

import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.Executor
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/** Framework observations only. NONE is a reported value, not proof of hardware support. */
enum class DeviceDetailStatus(val wire: String) {
    STOPPED("stopped"), PENDING("pending"), AVAILABLE("available"), UNSUPPORTED("unsupported"),
    UNAVAILABLE("unavailable"), STALE("stale"),
}

data class ThermalReading(val status: DeviceDetailStatus = DeviceDetailStatus.STOPPED,
    val code: Int? = null, val sampledMs: Long? = null, val reason: String? = null) {
    init {
        require((status == DeviceDetailStatus.AVAILABLE) == (code != null))
        require(code == null || code in 0..6)
        require(sampledMs == null || sampledMs >= 0)
        require(status != DeviceDetailStatus.AVAILABLE || sampledMs != null)
    }
    companion object {
        fun measured(code: Int, now: Long): ThermalReading =
            if(code in 0..6 && now>=0) ThermalReading(DeviceDetailStatus.AVAILABLE,code,now)
            else ThermalReading(DeviceDetailStatus.UNAVAILABLE,reason="invalid_value")
    }
}

data class DataFilesystemReading(val status: DeviceDetailStatus = DeviceDetailStatus.STOPPED,
    val availableBytes: Long? = null, val totalBytes: Long? = null, val sampledMs: Long? = null, val reason: String? = null) {
    init {
        require((status == DeviceDetailStatus.AVAILABLE) == (availableBytes != null && totalBytes != null))
        require((availableBytes == null) == (totalBytes == null))
        require(availableBytes == null || (availableBytes >= 0 && totalBytes!! > 0 && availableBytes <= totalBytes))
        require(sampledMs == null || sampledMs >= 0)
        require(status != DeviceDetailStatus.AVAILABLE || sampledMs != null)
    }
    companion object {
        fun measured(available: Long, total: Long, now: Long): DataFilesystemReading =
            if(available>=0 && total>0 && available<=total && now>=0)
                DataFilesystemReading(DeviceDetailStatus.AVAILABLE,available,total,now)
            else DataFilesystemReading(DeviceDetailStatus.UNAVAILABLE,reason="invalid_value")
    }
}

data class DeviceDetailSnapshot(val thermal: ThermalReading = ThermalReading(),
    val filesystem: DataFilesystemReading = DataFilesystemReading(), val listener: String = "stopped") {
    fun freshAt(now: Long): DeviceDetailSnapshot {
        fun stale(time: Long?) = time!=null && (now<time || now-time>DeviceDetailLifecycle.STALE_MS)
        return copy(thermal=if(stale(thermal.sampledMs)) ThermalReading(DeviceDetailStatus.STALE,reason="sample_expired") else thermal,
            filesystem=if(stale(filesystem.sampledMs)) DataFilesystemReading(DeviceDetailStatus.STALE,reason="sample_expired") else filesystem)
    }
    companion object {
        fun pending() = DeviceDetailSnapshot(ThermalReading(DeviceDetailStatus.PENDING),
            DataFilesystemReading(DeviceDetailStatus.PENDING),"pending")
    }
}

/** UI owner: opaque lifetime tokens never wrap; early battery events do not trigger disk queries. */
class DeviceDetailLifecycle {
    private var epoch: Any? = null
    private var attemptedMs: Long? = null
    var snapshot = DeviceDetailSnapshot(); private set
    fun start(): Any {
        epoch?.let {return it}
        return Any().also {epoch=it;attemptedMs=null;snapshot=DeviceDetailSnapshot.pending()}
    }
    fun stop(): DeviceDetailSnapshot {epoch=null;attemptedMs=null;snapshot=DeviceDetailSnapshot();return snapshot}
    fun shouldPoll(now: Long): Boolean {
        if(epoch==null || now<0) return false
        val previous=attemptedMs
        if(previous!=null && now>=previous && now-previous<INTERVAL_MS) return false
        attemptedMs=now
        return true
    }
    fun accept(token: Any, value: DeviceDetailSnapshot, now: Long): Boolean {
        if(epoch!==token) return false
        snapshot=value.freshAt(now)
        return true
    }
    fun refresh(now: Long): DeviceDetailSnapshot {snapshot=snapshot.freshAt(now);return snapshot}
    companion object {const val INTERVAL_MS=30_000L;const val STALE_MS=90_000L}
}

interface DeviceDetailBackend {
    fun thermal(now: Long): ThermalReading
    fun filesystem(now: Long): DataFilesystemReading
    fun listen(executor: Executor, changed: (Int) -> Unit): AutoCloseable
}

/**
 * One process-wide worker in production. Latest subscription/read/callback are coalesced, not queued.
 * A stopped/older subscription cannot publish or remove a replacement subscription.
 */
class DeviceDetailWorker(private val clock: () -> Long, private val failed: (String) -> Unit = {}) {
    class Subscription internal constructor(internal val backend: DeviceDetailBackend,
        internal val post: (() -> Unit) -> Unit, internal val publish: (DeviceDetailSnapshot) -> Unit) {
        internal val mailbox=AtomicReference<DeviceDetailSnapshot?>(null)
        internal val posted=AtomicBoolean(false)
    }
    private val lock=Any()
    private val executor=ThreadPoolExecutor(1,1,30,TimeUnit.SECONDS,ArrayBlockingQueue(1),
        {task -> Thread(task,"eidolon-device-details").apply {isDaemon=true}}).apply {allowCoreThreadTimeOut(true)}
    private var desired: Subscription? = null
    private var running=false
    private var dirty=false
    private var readPending=false
    private var callback: Pair<Subscription,Runnable>? = null
    // The following fields belong exclusively to the single worker.
    private var registeredOwner: Subscription? = null
    private var registration: AutoCloseable? = null
    private var valueOwner: Subscription? = null
    private var value=DeviceDetailSnapshot.pending()

    fun start(backend: DeviceDetailBackend, post: (() -> Unit) -> Unit, publish: (DeviceDetailSnapshot) -> Unit): Subscription =
        Subscription(backend,post,publish).also {owner -> synchronized(lock) {
            desired=owner;readPending=true;callback=null;kick()
        } }
    fun poll(owner: Subscription) = synchronized(lock) {if(desired===owner) {readPending=true;kick()}}
    fun stop(owner: Subscription) = synchronized(lock) {if(desired===owner) {desired=null;readPending=false;callback=null;kick()}}
    private fun current(owner: Subscription) = synchronized(lock) {desired===owner}
    private fun kick() {
        dirty=true
        if(!running) {running=true;executor.execute(::drain)}
    }
    private fun enqueueCallback(owner: Subscription, task: Runnable) = synchronized(lock) {
        if(desired===owner) {callback=owner to task;kick()}
    }
    private fun emit(owner: Subscription) {
        if(!current(owner)) return
        owner.mailbox.set(value)
        if(owner.posted.compareAndSet(false,true)) owner.post {
            owner.posted.set(false)
            val latest=owner.mailbox.getAndSet(null)
            if(latest!=null && current(owner)) owner.publish(latest)
        }
    }
    private fun drain() {
        while(true) {
            val work=synchronized(lock) {
                if(!dirty) {running=false;return}
                dirty=false
                Triple(desired,readPending,callback).also {readPending=false;callback=null}
            }
            val owner=work.first
            var releaseFailed=false
            if(registeredOwner!==owner && registration!=null) {
                try {registration!!.close();registration=null;registeredOwner=null}
                catch(_:Exception) {
                    releaseFailed=true // Retain only this one handle; no accumulating listeners.
                    runCatching {failed("thermal_listener_remove_failed")}
                }
            }
            if(owner==null) {valueOwner=null;value=DeviceDetailSnapshot();continue}
            if(!current(owner)) continue
            if(valueOwner!==owner) {valueOwner=owner;value=DeviceDetailSnapshot.pending()}
            if(releaseFailed) value=value.copy(listener="release_failed")
            else if(registeredOwner!==owner) {
                try {
                    registration=owner.backend.listen(Executor {enqueueCallback(owner,it)}) {code ->
                        if(current(owner)) {value=value.copy(thermal=ThermalReading.measured(code,clock()));emit(owner)}
                    }
                    registeredOwner=owner;value=value.copy(listener="registered")
                } catch(_:UnsupportedOperationException) {value=value.copy(listener="unsupported")}
                catch(_:Exception) {value=value.copy(listener="unavailable")}
            }
            // An already queued callback predates this explicit read; the fresh read wins.
            try {work.third?.takeIf {it.first===owner && current(owner)}?.second?.run()}
            catch(_:Exception) {
                value=value.copy(thermal=ThermalReading(DeviceDetailStatus.UNAVAILABLE,reason="callback_failed"))
                emit(owner)
            }
            if(work.second && current(owner)) {
                val now=clock()
                val thermal=try {owner.backend.thermal(now)} catch(_:UnsupportedOperationException) {ThermalReading(DeviceDetailStatus.UNSUPPORTED,reason="api_unavailable")} catch(_:Exception) {ThermalReading(DeviceDetailStatus.UNAVAILABLE,reason="read_failed")}
                val filesystem=try {owner.backend.filesystem(now)} catch(_:UnsupportedOperationException) {DataFilesystemReading(DeviceDetailStatus.UNSUPPORTED,reason="api_unavailable")} catch(_:Exception) {DataFilesystemReading(DeviceDetailStatus.UNAVAILABLE,reason="read_failed")}
                value=value.copy(thermal=thermal,filesystem=filesystem)
                emit(owner)
            }
        }
    }
}
