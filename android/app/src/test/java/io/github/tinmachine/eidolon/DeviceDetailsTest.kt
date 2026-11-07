package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

class DeviceDetailsTest {
    @Test fun reportedNoneIsAValueAndNeverSubstitutesForMissingOrUnsupported() {
        val none=ThermalReading.measured(0,100)
        assertEquals(DeviceDetailStatus.AVAILABLE,none.status);assertEquals(0,none.code)
        for(status in listOf(DeviceDetailStatus.UNSUPPORTED,DeviceDetailStatus.UNAVAILABLE,DeviceDetailStatus.STALE)) {
            assertNull(ThermalReading(status).code)
        }
        for(code in listOf(-1,7,Int.MAX_VALUE)) {
            val reading=ThermalReading.measured(code,100)
            assertEquals(DeviceDetailStatus.UNAVAILABLE,reading.status);assertNull(reading.code)
        }
        assertNull(ThermalReading.measured(0,-1).code)
    }
    @Test fun storageZeroIsFullButInvalidRangesDoNotBecomeZero() {
        assertEquals(0L,DataFilesystemReading.measured(0,100,1).availableBytes)
        assertEquals(Long.MAX_VALUE,DataFilesystemReading.measured(Long.MAX_VALUE,Long.MAX_VALUE,1).availableBytes)
        for((available,total) in listOf(-1L to 100L,101L to 100L,0L to 0L,0L to -1L)) {
            val reading=DataFilesystemReading.measured(available,total,1)
            assertEquals(DeviceDetailStatus.UNAVAILABLE,reading.status);assertNull(reading.availableBytes);assertNull(reading.totalBytes)
        }
    }
    @Test fun lifecyclePollsAtThirtySecondsAndPreservesSampleTimesOnEarlyNotifications() {
        val state=DeviceDetailLifecycle();val token=state.start()
        assertTrue(state.shouldPoll(100));assertFalse(state.shouldPoll(101));assertFalse(state.shouldPoll(30_099));assertTrue(state.shouldPoll(30_100))
        state.accept(token,DeviceDetailSnapshot(ThermalReading.measured(0,100),DataFilesystemReading.measured(20,40,100)),200)
        assertEquals(100L,state.refresh(5000).filesystem.sampledMs)
    }
    @Test fun stoppedAndPreviousLifetimeResultsCannotReturnAfterRestart() {
        val state=DeviceDetailLifecycle();val old=state.start();state.stop()
        val reading=DeviceDetailSnapshot(ThermalReading.measured(1,100),DataFilesystemReading.measured(20,40,100))
        assertFalse(state.accept(old,reading,101));assertEquals(DeviceDetailStatus.STOPPED,state.snapshot.thermal.status)
        val current=state.start();assertFalse(state.accept(old,reading,101))
        assertEquals(DeviceDetailStatus.PENDING,state.snapshot.filesystem.status)
        assertTrue(state.accept(current,reading,101));assertEquals(1,state.snapshot.thermal.code)
    }
    @Test fun expiredOrNonMonotonicSamplesDropValuesAndNewPollCanRecover() {
        val state=DeviceDetailLifecycle();val token=state.start()
        val reading=DeviceDetailSnapshot(ThermalReading.measured(1,100),DataFilesystemReading.measured(20,40,100))
        state.accept(token,reading,90_100);assertEquals(DeviceDetailStatus.AVAILABLE,state.snapshot.thermal.status)
        assertEquals(DeviceDetailStatus.STALE,state.refresh(90_101).thermal.status);assertNull(state.snapshot.filesystem.availableBytes)
        state.accept(token,reading,99);assertEquals(DeviceDetailStatus.STALE,state.snapshot.thermal.status)
        assertTrue(state.shouldPoll(100));assertTrue(state.shouldPoll(99))
        state.accept(token,reading,100);assertEquals(DeviceDetailStatus.AVAILABLE,state.snapshot.thermal.status)
    }

    private class Backend(private val hold: CountDownLatch?=null): DeviceDetailBackend {
        val entered=CountDownLatch(1)
        val thermalCode=AtomicInteger();val reads=AtomicInteger();val listeners=AtomicInteger();val closed=AtomicInteger();val executedCallbacks=AtomicInteger()
        val failClose=AtomicBoolean(false)
        @Volatile var executor: Executor?=null
        @Volatile var changed: ((Int)->Unit)?=null
        override fun thermal(now: Long)=ThermalReading.measured(thermalCode.get(),now)
        override fun filesystem(now: Long): DataFilesystemReading {
            reads.incrementAndGet();entered.countDown()
            if(hold!=null && !hold.await(3,TimeUnit.SECONDS)) throw IllegalStateException("fixture timeout")
            return DataFilesystemReading.measured(20,40,now)
        }
        override fun listen(executor: Executor,changed: (Int)->Unit): AutoCloseable {
            listeners.incrementAndGet();this.executor=executor;this.changed=changed
            return AutoCloseable {if(failClose.get()) throw IllegalStateException("fixture close failure");closed.incrementAndGet()}
        }
        fun fire(code: Int) {thermalCode.set(code);executor!!.execute {executedCallbacks.incrementAndGet();changed!!(code)}}
    }
    private fun await(condition: ()->Boolean) {
        val until=System.nanoTime()+TimeUnit.SECONDS.toNanos(3)
        while(!condition()) {if(System.nanoTime()>=until) fail("condition timeout");Thread.sleep(2)}
    }
    @Test fun repeatedPollsAndThermalEventsCoalesceBehindOneBlockedReader() {
        val hold=CountDownLatch(1);val backend=Backend(hold);val worker=DeviceDetailWorker(clock={100})
        val ui=ConcurrentLinkedQueue<()->Unit>();val observed=mutableListOf<DeviceDetailSnapshot>()
        val owner=worker.start(backend,{ui.add(it)},{observed.add(it)})
        try {
            assertTrue(backend.entered.await(3,TimeUnit.SECONDS))
            repeat(1000) {worker.poll(owner);backend.fire(it%7)}
            assertEquals(1,backend.reads.get());assertEquals(0,backend.executedCallbacks.get())
            hold.countDown();await {backend.reads.get()==2 && backend.executedCallbacks.get()==1}
            assertEquals(1,ui.size)
            ui.poll().invoke()
            assertEquals(1,observed.size);assertEquals(999%7,observed.single().thermal.code)
        } finally {hold.countDown();worker.stop(owner);await {backend.closed.get()==1}}
    }
    @Test fun stopDuringReadRejectsQueuedUiAndLatePlatformCallbacksAndRemovesListener() {
        val hold=CountDownLatch(1);val backend=Backend(hold);val worker=DeviceDetailWorker(clock={100})
        val ui=ConcurrentLinkedQueue<()->Unit>();val published=AtomicInteger()
        val owner=worker.start(backend,{ui.add(it)},{published.incrementAndGet()})
        assertTrue(backend.entered.await(3,TimeUnit.SECONDS))
        worker.stop(owner);backend.fire(6);hold.countDown();await {backend.closed.get()==1}
        while(ui.isNotEmpty()) ui.poll().invoke()
        assertEquals(0,published.get());assertEquals(0,backend.executedCallbacks.get())
    }
    @Test fun pendingUiResultIsIgnoredAfterStopEvenIfReadAlreadyFinished() {
        val backend=Backend();val worker=DeviceDetailWorker(clock={100});val ui=ConcurrentLinkedQueue<()->Unit>()
        val published=AtomicInteger();val owner=worker.start(backend,{ui.add(it)},{published.incrementAndGet()})
        await {ui.isNotEmpty()};worker.stop(owner)
        ui.poll().invoke();assertEquals(0,published.get());await {backend.closed.get()==1}
    }
    @Test fun replacementWaitsForOldReaderAndOldStopDoesNotCancelNewOwner() {
        val hold=CountDownLatch(1);val oldBackend=Backend(hold);val nextBackend=Backend();val worker=DeviceDetailWorker(clock={100})
        val ui=ConcurrentLinkedQueue<()->Unit>();val oldPublished=AtomicInteger();val newPublished=AtomicInteger()
        val old=worker.start(oldBackend,{ui.add(it)},{oldPublished.incrementAndGet()})
        assertTrue(oldBackend.entered.await(3,TimeUnit.SECONDS))
        val next=worker.start(nextBackend,{ui.add(it)},{newPublished.incrementAndGet()})
        worker.stop(old);assertEquals(0,nextBackend.reads.get());hold.countDown()
        try {
            await {ui.poll()?.invoke();newPublished.get()==1}
            assertEquals(0,oldPublished.get());assertEquals(1,newPublished.get());assertEquals(1,oldBackend.closed.get())
        } finally {worker.stop(next);await {nextBackend.closed.get()==1}}
    }
    @Test fun thermalApiExceptionDoesNotHideFilesystemOrInventNone() {
        val worker=DeviceDetailWorker(clock={100});val ui=ConcurrentLinkedQueue<()->Unit>()
        val observed=mutableListOf<DeviceDetailSnapshot>()
        val backend=object: DeviceDetailBackend {
            override fun thermal(now: Long): ThermalReading = throw UnsupportedOperationException()
            override fun filesystem(now: Long)=DataFilesystemReading.measured(0,40,now)
            override fun listen(executor: Executor, changed: (Int)->Unit): AutoCloseable = throw UnsupportedOperationException()
        }
        val owner=worker.start(backend,{ui.add(it)},{observed.add(it)})
        try {
            await {ui.isNotEmpty()};ui.poll().invoke()
            assertEquals(DeviceDetailStatus.UNSUPPORTED,observed.single().thermal.status)
            assertNull(observed.single().thermal.code)
            assertEquals(0L,observed.single().filesystem.availableBytes)
            assertEquals("unsupported",observed.single().listener)
        } finally {worker.stop(owner)}
    }
    @Test fun aQueuedOldThermalNotificationCannotOverwriteAnExplicitNewRead() {
        val hold=CountDownLatch(1);val backend=Backend(hold);val worker=DeviceDetailWorker(clock={100})
        val ui=ConcurrentLinkedQueue<()->Unit>();val observed=mutableListOf<DeviceDetailSnapshot>()
        val owner=worker.start(backend,{ui.add(it)},{observed.add(it)})
        try {
            assertTrue(backend.entered.await(3,TimeUnit.SECONDS))
            backend.fire(5);backend.thermalCode.set(1);worker.poll(owner);hold.countDown()
            await {ui.poll()?.invoke();backend.reads.get()==2 && observed.lastOrNull()?.thermal?.code==1}
            assertEquals(1,observed.last().thermal.code)
        } finally {hold.countDown();worker.stop(owner);await {backend.closed.get()==1}}
    }
    @Test fun failedListenerRemovalDoesNotAccumulateRegistrationsAcrossRestart() {
        val first=Backend();val second=Backend();val worker=DeviceDetailWorker(clock={100});val ui=ConcurrentLinkedQueue<()->Unit>()
        val observed=mutableListOf<DeviceDetailSnapshot>()
        val old=worker.start(first,{ui.add(it)},{})
        await {first.reads.get()==1};first.failClose.set(true)
        val next=worker.start(second,{ui.add(it)},{observed.add(it)})
        try {
            await {ui.poll()?.invoke();observed.lastOrNull()?.listener=="release_failed"}
            assertEquals(0,second.listeners.get());assertEquals("release_failed",observed.last().listener)
            first.failClose.set(false);worker.poll(next)
            await {first.closed.get()==1 && second.listeners.get()==1}
            worker.stop(old) // A retired Activity cannot release the newer listener.
            assertEquals(0,second.closed.get())
        } finally {first.failClose.set(false);worker.stop(next);await {second.closed.get()==1}}
    }
}
