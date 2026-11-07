package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.Executor

class ProcessPssTest {
    @Test fun kibConversionUsesLongAndDoesNotReplaceInvalidValuesWithZero() {
        assertEquals(0L,ProcessPssReading.measured(0,1).bytes)
        assertEquals(Int.MAX_VALUE.toLong()*1024,ProcessPssReading.measured(Int.MAX_VALUE.toLong(),1).bytes)
        for(kib in listOf(-1L,Int.MAX_VALUE.toLong()+1,Long.MAX_VALUE)) {
            assertEquals(DeviceDetailStatus.UNAVAILABLE,ProcessPssReading.measured(kib,1).status)
            assertNull(ProcessPssReading.measured(kib,1).bytes)
        }
        assertNull(ProcessPssReading.measured(1,-1).bytes)
    }
    @Test fun stopRestartAndSampleExpiryClearPssWithoutRefreshingOldSampleTime() {
        val state=DeviceDetailLifecycle();val first=state.start()
        assertEquals(DeviceDetailStatus.PENDING,state.snapshot.pss.status)
        val sample=DeviceDetailSnapshot(pss=ProcessPssReading.measured(10,100))
        assertTrue(state.accept(first,sample,100))
        assertEquals(100L,state.refresh(90_100).pss.sampledMs)
        assertEquals(DeviceDetailStatus.STALE,state.refresh(90_101).pss.status)
        assertNull(state.snapshot.pss.bytes)
        state.stop();assertEquals(DeviceDetailStatus.STOPPED,state.snapshot.pss.status)
        val second=state.start();assertFalse(state.accept(first,sample,101))
        assertEquals(DeviceDetailStatus.PENDING,state.snapshot.pss.status)
        assertTrue(state.accept(second,sample,99));assertEquals(DeviceDetailStatus.STALE,state.snapshot.pss.status)
    }
    @Test fun pssFailureDoesNotHideThermalOrFilesystemAndReadRunsOffCallerThread() {
        val caller=Thread.currentThread();val ui=ConcurrentLinkedQueue<()->Unit>();val samples=mutableListOf<DeviceDetailSnapshot>()
        val worker=DeviceDetailWorker({100})
        val owner=worker.start(object: DeviceDetailBackend {
            override fun thermal(now:Long)=ThermalReading.measured(0,now)
            override fun filesystem(now:Long)=DataFilesystemReading.measured(20,40,now)
            override fun pss(now:Long):ProcessPssReading {assertNotSame(caller,Thread.currentThread());throw SecurityException()}
            override fun listen(executor:Executor,changed:(Int)->Unit)=AutoCloseable {}
        },{ui.add(it)},{samples.add(it)})
        try {
            val deadline=System.nanoTime()+2_000_000_000
            while(samples.isEmpty() && System.nanoTime()<deadline) {ui.poll()?.invoke();Thread.sleep(2)}
            assertEquals(1,samples.size)
            assertEquals(DeviceDetailStatus.AVAILABLE,samples.single().thermal.status)
            assertEquals(20L,samples.single().filesystem.availableBytes)
            assertEquals(DeviceDetailStatus.UNAVAILABLE,samples.single().pss.status)
            assertNull(samples.single().pss.bytes)
        } finally {worker.stop(owner)}
    }
}
