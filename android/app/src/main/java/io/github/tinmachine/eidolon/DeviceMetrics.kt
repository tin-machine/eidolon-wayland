package io.github.tinmachine.eidolon

import android.app.ActivityManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager
import android.os.StatFs
import java.util.concurrent.Executor
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import java.util.Locale
import org.json.JSONObject

data class DeviceMetricsSnapshot(
    val sampledMs: Long = 0,
    val batteryPercent: Int? = null,
    val charging: Boolean? = null,
    val availableRam: Long? = null,
    val totalRam: Long? = null,
    val addresses: List<String> = emptyList(),
    val network: String = "unknown",
    val processCpu: ProcessCpuReading = ProcessCpuReading(),
    val details: DeviceDetailSnapshot = DeviceDetailSnapshot(),
) {
    fun summary(): String {
        val battery = batteryPercent?.let { "$it%" } ?: "不明"
        val charge = when(charging) { true -> "充電中"; false -> "放電中"; null -> "状態不明" }
        fun gib(v: Long?) = v?.let { String.format(Locale.ROOT, "%.1f", it / 1073741824.0) } ?: "?"
        return "電池 $battery · $charge / RAM空き ${gib(availableRam)} / ${gib(totalRam)} GiB"
    }
    fun networkSummary(): String = when(network) {
        "connected" -> "Quest IP: ${addresses.joinToString(", ").ifEmpty { "取得待ち" }}"
        "disconnected" -> "Questネットワーク: 未接続"
        else -> "Quest IP: 取得できません"
    }
    fun processCpuSummary(): String = when(processCpu.status) {
        ProcessCpuStatus.AVAILABLE -> String.format(Locale.ROOT, "Eidolon CPU %.1f%%（1コア=100%%、直近%.1f秒平均）",
            processCpu.percent, checkNotNull(processCpu.intervalMs) / 1000.0)
        ProcessCpuStatus.WARMING_UP -> "Eidolon CPU 計測中（初回待ち）"
        ProcessCpuStatus.UNAVAILABLE -> "Eidolon CPU 取得できません"
        ProcessCpuStatus.STALE -> "Eidolon CPU 欠測・再計測中"
        ProcessCpuStatus.STOPPED -> "Eidolon CPU 計測停止"
    }
    fun thermalSummary(): String = when(details.thermal.status) {
        DeviceDetailStatus.AVAILABLE -> when(details.thermal.code) {
            0 -> "OS熱状態 NONE（未対応端末との識別はできません）"
            1 -> "OS熱制限 軽度（LIGHT）"
            2 -> "OS熱制限 中程度（MODERATE）"
            3 -> "OS熱制限 強い（SEVERE）"
            4 -> "OS熱制限 重大（CRITICAL）"
            5 -> "OS熱制限 緊急（EMERGENCY）"
            else -> "OS熱制限 シャットダウン段階（SHUTDOWN）"
        }
        DeviceDetailStatus.PENDING -> "OS熱状態 取得待ち"
        DeviceDetailStatus.UNSUPPORTED -> "OS熱状態 API非対応"
        DeviceDetailStatus.UNAVAILABLE -> "OS熱状態 取得できません"
        DeviceDetailStatus.STALE -> "OS熱状態 欠測・再取得中"
        DeviceDetailStatus.STOPPED -> "OS熱状態 計測停止"
    }
    fun dataFilesystemSummary(): String = when(details.filesystem.status) {
        DeviceDetailStatus.AVAILABLE -> String.format(Locale.ROOT,
            "Eidolon保存先 空き %.1f / %.1f GiB（ファイルシステム）",
            checkNotNull(details.filesystem.availableBytes) / 1073741824.0,
            checkNotNull(details.filesystem.totalBytes) / 1073741824.0)
        DeviceDetailStatus.PENDING -> "Eidolon保存先の空き 取得待ち"
        DeviceDetailStatus.UNSUPPORTED -> "Eidolon保存先の空き API非対応"
        DeviceDetailStatus.UNAVAILABLE -> "Eidolon保存先の空き 取得できません"
        DeviceDetailStatus.STALE -> "Eidolon保存先の空き 欠測・再取得中"
        DeviceDetailStatus.STOPPED -> "Eidolon保存先の空き 計測停止"
    }
    fun processPssSummary(): String = when(details.pss.status) {
        DeviceDetailStatus.AVAILABLE -> String.format(Locale.ROOT,"Eidolon PSS %.1f MiB（共有メモリを按分）",
            checkNotNull(details.pss.bytes)/1048576.0)
        DeviceDetailStatus.PENDING -> "Eidolon PSS 取得待ち"
        DeviceDetailStatus.UNSUPPORTED -> "Eidolon PSS API非対応"
        DeviceDetailStatus.UNAVAILABLE -> "Eidolon PSS 取得できません"
        DeviceDetailStatus.STALE -> "Eidolon PSS 欠測・再取得中"
        DeviceDetailStatus.STOPPED -> "Eidolon PSS 計測停止"
    }
    fun json(): JSONObject = JSONObject().put("sampled_monotonic_ms",sampledMs)
        .put("battery_percent",batteryPercent ?: JSONObject.NULL).put("charging",charging ?: JSONObject.NULL)
        .put("available_ram_bytes",availableRam ?: JSONObject.NULL).put("total_ram_bytes",totalRam ?: JSONObject.NULL)
        .put("network",network).put("addresses",org.json.JSONArray(addresses))
        .put("process_cpu_percent",processCpu.percent ?: JSONObject.NULL)
        .put("process_cpu_status",processCpu.status.wireValue)
        .put("process_cpu_sampled_monotonic_ms",processCpu.sampledMonotonicMs ?: JSONObject.NULL)
        .put("process_cpu_interval_ms",processCpu.intervalMs ?: JSONObject.NULL)
        .put("process_cpu_basis","one_core_100_percent")
        .put("thermal_status",details.thermal.status.wire).put("thermal_code",details.thermal.code ?: JSONObject.NULL)
        .put("thermal_sampled_monotonic_ms",details.thermal.sampledMs ?: JSONObject.NULL)
        .put("thermal_reason",details.thermal.reason ?: JSONObject.NULL).put("thermal_listener_status",details.listener)
        .put("thermal_hardware_support","not_verified")
        .put("data_fs_status",details.filesystem.status.wire).put("data_fs_scope","eidolon_data_dir_filesystem")
        .put("data_fs_available_bytes",details.filesystem.availableBytes ?: JSONObject.NULL)
        .put("data_fs_total_bytes",details.filesystem.totalBytes ?: JSONObject.NULL)
        .put("data_fs_sampled_monotonic_ms",details.filesystem.sampledMs ?: JSONObject.NULL)
        .put("data_fs_reason",details.filesystem.reason ?: JSONObject.NULL)
        .put("process_pss_status",details.pss.status.wire).put("process_pss_scope","eidolon_process_only")
        .put("process_pss_bytes",details.pss.bytes ?: JSONObject.NULL)
        .put("process_pss_sampled_monotonic_ms",details.pss.sampledMs ?: JSONObject.NULL)
        .put("process_pss_reason",details.pss.reason ?: JSONObject.NULL)
}

/** 可視期間だけ購読する。CPUは既存5秒tick、熱/容量/PSSは専用workerで30秒周期。 */
class DeviceMetrics(context: Context, private val publish: (DeviceMetricsSnapshot) -> Unit,
    private val elapsedMillis: () -> Long = SystemClock::elapsedRealtime,
    private val processCpuMillis: () -> Long? = { android.os.Process.getElapsedCpuTime() },
) {
    private val app = context.applicationContext
    private val handler = Handler(Looper.getMainLooper())
    private var active = false
    private var battery: Intent? = null
    private val cpu = ProcessCpuTracker()
    private val detailState = DeviceDetailLifecycle()
    private var detailSubscription: DeviceDetailWorker.Subscription? = null
    private var latest = DeviceMetricsSnapshot()
    private val receiver = object: BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) { battery=intent; if(active) sample() }
    }
    private val tick = object: Runnable {
        override fun run() { if(active) {sample();handler.postDelayed(this,ProcessCpuTracker.INTERVAL_MS)} }
    }
    fun start() {
        if(active) return
        active=true;cpu.start()
        val epoch=detailState.start()
        detailState.shouldPoll(elapsedMillis())
        detailSubscription=detailWorker.start(AndroidDeviceDetailBackend(app), {task -> handler.post(task)}, {value ->
            if(active && detailState.accept(epoch,value,elapsedMillis())) {
                latest=latest.copy(details=detailState.snapshot)
                publish(latest)
            }
        })
        battery=app.registerReceiver(receiver,IntentFilter(Intent.ACTION_BATTERY_CHANGED),Context.RECEIVER_NOT_EXPORTED)
        handler.post(tick)
    }
    fun stop() {
        if(!active) return
        active=false;handler.removeCallbacks(tick)
        detailSubscription?.let(detailWorker::stop);detailSubscription=null
        latest=latest.copy(processCpu=cpu.stop(),details=detailState.stop())
        // CPU is stopped; do not refresh the timestamps of unmeasured battery/RAM/IP.
        try { publish(latest) } finally { app.unregisterReceiver(receiver) }
    }
    private fun sample() {
        val level=battery?.getIntExtra(BatteryManager.EXTRA_LEVEL,-1) ?: -1
        val scale=battery?.getIntExtra(BatteryManager.EXTRA_SCALE,-1) ?: -1
        val percent=if(level>=0 && scale>0 && level<=scale) (level.toLong()*100/scale).toInt() else null
        val status=battery?.getIntExtra(BatteryManager.EXTRA_STATUS,-1)
        val charging=when(status) {
            BatteryManager.BATTERY_STATUS_CHARGING, BatteryManager.BATTERY_STATUS_FULL -> true
            BatteryManager.BATTERY_STATUS_DISCHARGING, BatteryManager.BATTERY_STATUS_NOT_CHARGING -> false
            else -> null
        }
        val memory=runCatching { ActivityManager.MemoryInfo().also {app.getSystemService(ActivityManager::class.java).getMemoryInfo(it)} }.getOrNull()
        val network=runCatching {
            val manager=app.getSystemService(ConnectivityManager::class.java)
            val current=manager.activeNetwork
            if(current==null) "disconnected" to emptyList<String>() else {
                val links=manager.getLinkProperties(current)
                (if(links==null) "unknown" else "connected") to (links?.linkAddresses?.mapNotNull { link ->
                    link.address.takeUnless {it.isLoopbackAddress || it.isLinkLocalAddress}?.hostAddress?.substringBefore('%')
                }?.distinct()?.take(8) ?: emptyList())
            }
        }.getOrElse {"unknown" to emptyList()}
        val now=elapsedMillis()
        latest=DeviceMetricsSnapshot(now,percent,charging,
            memory?.availMem?.takeIf {it>=0},memory?.totalMem?.takeIf {it>0},network.second,network.first,
            cpu.sample(now,processCpuMillis),detailState.refresh(now))
        publish(latest)
        if(detailState.shouldPoll(now)) detailSubscription?.let(detailWorker::poll)
    }
    companion object {
        // Survives Activity restart without starting a second reader while an old StatFs call completes.
        private val detailWorker=DeviceDetailWorker(SystemClock::elapsedRealtime) {android.util.Log.w("EidolonDeviceMetrics",it)}
    }
}

/** Every platform call, including listener registration/removal, runs on the detail worker. */
private class AndroidDeviceDetailBackend(private val app: Context): DeviceDetailBackend {
    private fun power(): PowerManager {
        if(Build.VERSION.SDK_INT<29) throw UnsupportedOperationException()
        return app.getSystemService(PowerManager::class.java) ?: throw UnsupportedOperationException()
    }
    override fun thermal(now: Long): ThermalReading = try {
        ThermalReading.measured(power().currentThermalStatus,now)
    } catch(_:UnsupportedOperationException) {ThermalReading(DeviceDetailStatus.UNSUPPORTED,reason="api_unavailable")}
    catch(_:SecurityException) {ThermalReading(DeviceDetailStatus.UNAVAILABLE,reason="access_denied")}
    catch(_:Exception) {ThermalReading(DeviceDetailStatus.UNAVAILABLE,reason="read_failed")}

    override fun filesystem(now: Long): DataFilesystemReading = try {
        val filesystem=StatFs(app.dataDir.absolutePath)
        DataFilesystemReading.measured(filesystem.availableBytes,filesystem.totalBytes,now)
    } catch(_:SecurityException) {DataFilesystemReading(DeviceDetailStatus.UNAVAILABLE,reason="access_denied")}
    catch(_:Exception) {DataFilesystemReading(DeviceDetailStatus.UNAVAILABLE,reason="read_failed")}

    override fun pss(now: Long): ProcessPssReading = try {
        val info=android.os.Debug.MemoryInfo()
        android.os.Debug.getMemoryInfo(info)
        ProcessPssReading.measured(info.totalPss.toLong(),now)
    } catch(_:SecurityException) {ProcessPssReading(DeviceDetailStatus.UNAVAILABLE,reason="access_denied")}
    catch(_:Exception) {ProcessPssReading(DeviceDetailStatus.UNAVAILABLE,reason="read_failed")}

    override fun listen(executor: Executor, changed: (Int) -> Unit): AutoCloseable {
        val manager=power()
        val listener=PowerManager.OnThermalStatusChangedListener {changed(it)}
        manager.addThermalStatusListener(executor,listener)
        return AutoCloseable {manager.removeThermalStatusListener(listener)}
    }
}
