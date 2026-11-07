package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

class InputIntentSerialTest {
    @Test fun externalPointerBetweenReadAndDispatchPermanentlyCancelsTrial() {
        val serial=InputIntentSerial();val trial=serial.capture();var dispatches=0
        assertTrue(serial.current(trial)) // 古いfocus snapshotがまだ同一でも外部intentを優先する。
        serial.serial {serial.incrementAndGet();dispatches++}
        assertNull(serial.dispatch(trial,{true}) {dispatches++})
        assertFalse(serial.current(trial));assertEquals(1,dispatches)
        assertFalse(serial.seal(trial));assertFalse(serial.expectRetirement(trial,InputRetirement(1,"gtk")))
    }
    @Test fun normalInputWaitsForConditionalCheckAndEnqueueAsOneOperation() {
        val serial=InputIntentSerial();val trial=serial.capture();val entered=CountDownLatch(1);val attempted=CountDownLatch(1)
        val release=CountDownLatch(1);val normalFinished=AtomicBoolean();val order=mutableListOf<String>()
        val debug=Thread {serial.dispatch(trial,{entered.countDown();release.await(2,TimeUnit.SECONDS)}) {order.add("gtk-enqueued")}}
        debug.start();assertTrue(entered.await(2,TimeUnit.SECONDS))
        val normal=Thread {attempted.countDown();serial.serial {serial.incrementAndGet();order.add("normal-enqueued");normalFinished.set(true)}}
        normal.start();assertTrue(attempted.await(2,TimeUnit.SECONDS));assertFalse(normalFinished.get())
        release.countDown();debug.join(2000);normal.join(2000)
        assertFalse(debug.isAlive || normal.isAlive);assertEquals(listOf("gtk-enqueued","normal-enqueued"),order)
        assertFalse(serial.current(trial))
    }
    @Test fun normalFirstNeverAllowsStaleConditionalDispatchToEnqueue() {
        val serial=InputIntentSerial();val trial=serial.capture();val entered=CountDownLatch(1);val release=CountDownLatch(1)
        var ran=false
        val normal=Thread {serial.serial {serial.incrementAndGet();entered.countDown();release.await(2,TimeUnit.SECONDS)}}
        normal.start();assertTrue(entered.await(2,TimeUnit.SECONDS))
        val debug=Thread {serial.dispatch(trial,{true}) {ran=true}}
        debug.start();release.countDown();normal.join(2000);debug.join(2000);assertFalse(ran)
    }
    @Test fun ownPointerAdvancesTicketButSealedTrialCanNeverSendMoreInput() {
        val serial=InputIntentSerial();val trial=serial.capture();val captured=serial.get()
        assertEquals(true,serial.dispatch(trial,{true}) {serial.ownedIncrement(trial);true})
        assertTrue(serial.current(trial));assertEquals(captured+1,serial.get())
        assertTrue(serial.seal(trial));assertNull(serial.dispatch(trial,{true}) {error("late focus")})
        assertTrue(serial.expectRetirement(trial,InputRetirement(1,"gtk/main")));serial.retiring(InputRetirement(1,"gtk/main"))
        assertTrue(serial.current(trial));assertNull(serial.dispatch(trial,{true}) {error("late key")})
        serial.retiring(InputRetirement(1,"gtk/main"));assertFalse(serial.current(trial)) // receipt消費は一回だけ。
    }
    @Test fun foreignRetirementAndPhysicalKeyLoseEvenSealedTrialWithoutEpochRebase() {
        for(physical in listOf(false,true)) {
            val serial=InputIntentSerial();val trial=serial.capture();serial.seal(trial);serial.expectRetirement(trial,InputRetirement(1,"gtk"))
            if(physical) serial.invalidate() else serial.retiring(InputRetirement(1,"foot"))
            assertFalse(serial.current(trial));serial.retiring(InputRetirement(1,"gtk"));assertFalse(serial.current(trial))
        }
    }
    @Test fun sameTargetFromDifferentRuntimeCannotConsumeRetirementPermission() {
        val serial=InputIntentSerial();val trial=serial.capture();serial.seal(trial)
        assertTrue(serial.expectRetirement(trial,InputRetirement(1,"gtk")))
        serial.retiring(InputRetirement(2,"gtk"));assertFalse(serial.current(trial))
        serial.retiring(InputRetirement(1,"gtk"));assertFalse(serial.current(trial))
    }
    @Test fun contextFailureAndNewCaptureCannotResurrectOldIntent() {
        val serial=InputIntentSerial();val old=serial.capture();assertNull(serial.dispatch(old,{false}) {true})
        assertNull(serial.dispatch(old,{true}) {true});val fresh=serial.capture()
        assertTrue(serial.current(fresh));assertFalse(serial.current(old));serial.release(old);assertTrue(serial.current(fresh))
        serial.release(fresh);assertFalse(serial.current(fresh))
    }
    @Test fun ordinaryCounterAndDispatchOrderStillWorkWithoutDebugTrial() {
        val serial=InputIntentSerial();assertEquals(0L,serial.get())
        var enqueued=0L
        serial.serial {val ticket=serial.incrementAndGet();enqueued=ticket}
        serial.invalidate();assertEquals(1L,serial.get());assertEquals(1L,enqueued)
        assertEquals(2L,serial.incrementAndGet())
    }
    @Test fun captureInvalidatesEarlierDeferredFocusTicketWithoutResettingNativeEpoch() {
        val serial=InputIntentSerial();val pending=serial.incrementAndGet();val trial=serial.capture()
        assertNotEquals(pending,serial.get());assertTrue(serial.current(trial))
    }
}
