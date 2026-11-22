package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class LauncherFailuresTest {
    private val nonce = "0123456789abcdef"
    private fun state(request: String? = null, reason: String = "natural", code: Int = -11): JSONObject {
        val launch = JSONObject().put("mode", "gui").put("terminal_id", "1").put("accepted", true).put("request_id", request)
        val receipt = JSONObject().put("nonce", nonce).put("session", "7").put("terminal_id", "1").put("request_id", request)
            .put("reason", reason).put("child_returncode", code)
        val remote = JSONObject().put("terminal_launches", JSONArray().put(launch)).put("gui_receipts", JSONArray().put(receipt))
        val tx = JSONObject().put("session", "7").put("app_forward", JSONObject().put("nonce", nonce).put("remote", remote))
        return JSONObject().put("transport", tx)
    }
    @Test fun receiptBelongsToOneLaunchEvenWhenTheHostAlsoHasCleanupPending() {
        val snapshot = state()
        snapshot.getJSONObject("transport").put("state", "CleanupPending")
        assertNull(TermuxAppObservation("7", nonce, "4").read(snapshot))
        assertNull(TermuxAppObservation("8", nonce, null).read(snapshot))
        assertNull(TermuxAppObservation("7", "fedcba9876543210", null).read(snapshot))
        val watch = TermuxAppObservation("7", nonce, null)
        val crash = watch.read(snapshot) as AppObservation.Failed
        assertTrue(crash.reason.contains("SIGSEGV"))
        assertTrue(crash.log.contains("child_returncode=-11"))
        assertEquals(watch.event(snapshot), crash.event)
        assertEquals(AppObservation.Cancelled, watch.read(state(reason = "cancel", code = -15)))
        assertEquals(AppObservation.Completed, watch.read(state(code = 0)))
    }

    @Test fun aHelperWindowDoesNotClearTheWarning() {
        val snapshot = state("47")
        val remote = snapshot.getJSONObject("transport").getJSONObject("app_forward").getJSONObject("remote")
        remote.remove("gui_receipts")
        val window = JSONObject().put("life", "Mapped").put("app_id", "io.github.tinmachine.eidolon.foot.t-$nonce-1")
        snapshot.put("app", JSONObject().put("session", "7").put("nonce", nonce).put("windows", JSONArray().put(window)))
        val watch = TermuxAppObservation("7", nonce, "47")
        assertNull(watch.read(snapshot))
        window.put("app_id", "io.github.tinmachine.eidolon.gui.g-$nonce-1")
        assertEquals(AppObservation.Displayed, watch.read(snapshot))
    }

    @Test fun ubuntuEarlyExitIsScopedAndDoesNotInventAnExitCode() {
        val history = LauncherFailures()
        val attempt = history.begin("one-app")
        val observation = LinuxAppObservation("7", "43", attempt)
        val report = JSONObject().put("session", "7").put("launch_request", "42")
            .put("terminal_observed", true).put("terminal_reason", "process_exited")
        val snapshot = JSONObject().put("transport", JSONObject().put("session", "7").put("desktop_v3_transport", report))
        assertFalse(observation.read(1, snapshot, history))
        assertNull(history.reason(attempt.key))
        report.put("launch_request", "43").put("terminal_reason", "stop")
        assertTrue(observation.read(1, snapshot, history))
        assertNull(history.reason(attempt.key))
        report.put("terminal_reason", "process_exited")
        assertTrue(observation.read(1, snapshot, history))
        assertTrue(history.reason(attempt.key)!!.log.contains("process_exited"))
    }

    @Test fun olderCopiesCannotOverwriteNewerLaunchesOrOtherCards() {
        val history = LauncherFailures()
        val reason = AppFailure("終了", "child_returncode=2")
        val first = history.begin("termux:first")
        val other = history.begin("termux:other")
        history.record(first, reason)
        val retry = history.begin(first.key)
        assertEquals(reason, history.reason(first.key))
        history.record(retry, null)
        history.finish(retry)
        history.record(first, reason)
        history.record(other, reason)
        assertNull(history.reason(first.key))
        assertEquals(reason, history.reason(other.key))
        assertEquals(reason, AppFailure.decode(reason.encode()))
    }
}
