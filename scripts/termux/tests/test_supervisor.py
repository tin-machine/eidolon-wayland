import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

spec = importlib.util.spec_from_file_location("lease_launcher", Path(__file__).resolve().parents[1] / "eidolon-session.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class SupervisorTests(unittest.TestCase):
    def run_case(self, source, action, *, lease=0.3, grace=0.15):
        with tempfile.TemporaryDirectory(prefix="eidolon-lease-") as value:
            home, nonce = Path(value), "c" * 16
            read, write = os.pipe()
            events = []
            timers = []
            closed = False

            def send(kind, **extras):
                os.write(write, (json.dumps({"type": kind, "nonce": nonce, **extras}) + "\n").encode())

            def close():
                nonlocal closed
                if not closed:
                    closed = True
                    os.close(write)

            def emit(event):
                events.append(event)
                if event["type"] == "prepared":
                    if action == "prepare_eof":
                        close()
                    else:
                        send("start", mode="echo")
                elif event["type"] == "child_started":
                    self.assertTrue(launcher.cleanup(home, nonce)["active"])
                    if action == "eof":
                        close()
                    elif action in {"stop", "heartbeat"}:
                        def later():
                            try:
                                if action == "heartbeat":
                                    for _ in range(8):
                                        send("heartbeat")
                                        time.sleep(0.07)
                                else:
                                    deadline = time.monotonic() + 2
                                    while not (home / "ready").exists():
                                        if time.monotonic() > deadline:
                                            raise TimeoutError("fixture ready")
                                        time.sleep(0.005)
                                send("stop")
                            except Exception:
                                close()
                        timer = threading.Thread(target=later)
                        timers.append(timer)
                        timer.start()
                    elif action == "wrong_nonce":
                        os.write(write, (json.dumps({"type": "heartbeat", "nonce": "d" * 16}) + "\n").encode())
                    elif action == "oversized":
                        os.write(write, b"x" * 4097)

            code = source.replace("READY_FILE", repr(str(home / "ready")))
            try:
                result = launcher.supervise(home, nonce, read, emit, lease_seconds=lease, grace_seconds=grace,
                                            factory=lambda mode, token: [sys.executable, "-c", code])
                self.assertTrue(result["cleaned"], result)
                self.assertFalse(result["group_alive"], result)
                self.assertTrue(result["pipes_closed"], result)
                self.assertFalse((home / ".eidolon" / ("s-" + nonce)).exists())
                audit = launcher.cleanup(home, nonce)
                self.assertTrue(audit["already_absent"])
                self.assertTrue(audit["receipt"]["cleaned"])
                self.assertFalse(audit["child_alive"])
                self.assertEqual(audit["receipt"]["reason"], result["reason"])
                self.assertFalse(launcher.same_process(result["child"]))
                for timer in timers:
                    timer.join(timeout=2)
                    self.assertFalse(timer.is_alive())
                return result, events
            finally:
                close()
                os.close(read)

    def test_eof_after_prepare_and_during_owned_child_reaps_without_other_processes(self):
        unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
        try:
            result, _ = self.run_case("import time;time.sleep(30)", "prepare_eof")
            self.assertEqual(result["reason"], "EOF")
            self.assertIsNone(result["child"])
            # 150msの試験用graceでは並行build時のscheduler遅延をkill経路と誤認する。
            # 非協調childの短grace検査は別case。ここは製品と同じ3秒で通常EOF回収を検査する。
            result, _ = self.run_case("import time;time.sleep(30)", "eof", grace=3.0)
            self.assertEqual(result["reason"], "EOF")
            self.assertEqual(result["exit_signal"], signal.SIGTERM)
            self.assertTrue(result["term_sent"])
            self.assertFalse(result["kill_sent"])
            self.assertIsNone(unrelated.poll())
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=3)

    def test_lease_expiry_and_heartbeat_extension_are_distinct(self):
        result, _ = self.run_case("import time;time.sleep(30)", "lease")
        self.assertEqual(result["reason"], "LeaseExpired")
        self.assertTrue(result["term_sent"])
        started = time.monotonic()
        result, _ = self.run_case("import time;time.sleep(30)", "heartbeat")
        self.assertEqual(result["reason"], "Stop")
        self.assertGreater(time.monotonic() - started, 0.5)

    def test_term_ignoring_child_is_killed_after_grace_and_waited(self):
        source = "import signal,time,pathlib;signal.signal(signal.SIGTERM,signal.SIG_IGN);pathlib.Path(READY_FILE).touch();time.sleep(30)"
        started = time.monotonic()
        result, _ = self.run_case(source, "stop")
        self.assertEqual(result["reason"], "Stop")
        self.assertTrue(result["term_sent"] and result["kill_sent"])
        self.assertEqual(result["exit_signal"], signal.SIGKILL)
        self.assertGreater(time.monotonic() - started, 0.15)

    def test_waypipe_display_socket_left_by_signal_is_owned_and_removed(self):
        source = ("import pathlib,socket,time;home=pathlib.Path(READY_FILE).parent;"
                  "peer=socket.socket(socket.AF_UNIX);peer.bind(str(home/'.eidolon'/('s-'+'c'*16)/'w'));"
                  "pathlib.Path(READY_FILE).touch();time.sleep(30)")
        result, _ = self.run_case(source, 'stop')
        self.assertEqual(result['reason'], 'Stop')
        self.assertEqual(result['exit_signal'], signal.SIGTERM)

    def test_unexpected_file_keeps_lease_and_emits_failed_cleanup_receipt(self):
        with tempfile.TemporaryDirectory(prefix='eidolon-unknown-') as value:
            home, nonce = Path(value), 'a' * 16
            read, write = os.pipe()
            def emit(event):
                if event['type'] == 'prepared':
                    directory = Path(event['socket']).parent
                    (directory / 'unexpected').write_text('preserve')
                    os.write(write, (json.dumps({'type':'stop','nonce':nonce})+'\n').encode())
            try:
                result = launcher.supervise(home, nonce, read, emit)
                self.assertFalse(result['cleaned'])
                self.assertEqual(result['error'], 'CLEANUP_FAILED')
                directory = home / '.eidolon' / ('s-' + nonce)
                self.assertTrue((directory / 'lease.json').is_file())
                self.assertEqual((directory / 'unexpected').read_text(), 'preserve')
                self.assertFalse((directory.parent / ('r-' + nonce + '.json')).exists())
            finally:
                os.close(write)
                os.close(read)

    def test_stdout_stderr_flood_drains_with_separate_bounded_tails(self):
        source = "import os;os.write(1,b'fixture-sensitive-text\\n');[os.write(1,b'x'*65536) for _ in range(40)];[os.write(2,b'y'*65536) for _ in range(40)]"
        result, _ = self.run_case(source, "natural", lease=5)
        self.assertEqual(result["reason"], "Completed")
        self.assertEqual(result["exit_status"], 0)
        self.assertEqual(result["stdout"]["bytes"], 40 * 65536 + len(b"fixture-sensitive-text\n"))
        self.assertEqual(result["stderr"]["bytes"], 40 * 65536)
        for name in ["stdout", "stderr"]:
            self.assertTrue(result[name]["truncated"])
            self.assertEqual(result[name]["retained"], 65536)
        self.assertNotIn("fixture-sensitive-text", json.dumps(result))

    def test_wrong_nonce_and_oversized_partial_control_stop_only_owned_child(self):
        for action in ["wrong_nonce", "oversized"]:
            result, _ = self.run_case("import time;time.sleep(30)", action)
            self.assertEqual(result["reason"], "ControlInvalid")
            self.assertTrue(result["term_sent"])

    def test_pid_identity_mismatch_and_orphan_audit_never_signal_saved_pids(self):
        with tempfile.TemporaryDirectory(prefix="eidolon-lease-") as value:
            home, nonce = Path(value), "e" * 16
            directory = Path(launcher.prepare(home, nonce)["socket"]).parent
            identity = launcher.process_identity(os.getpid())
            stale = {**identity, "start": "0"}
            self.assertFalse(launcher.same_process(stale))
            launcher.write_record(directory / "lease.json", {**launcher.marker(nonce), "owner": stale, "child": identity})
            report = launcher.cleanup(home, nonce)
            self.assertFalse(report["cleaned"])
            self.assertTrue(report["orphaned"])
            self.assertTrue(directory.exists())
            launcher.write_record(directory / "lease.json", {**launcher.marker(nonce), "owner": stale, "child": stale})
            self.assertFalse(launcher.cleanup(home, nonce)["cleaned"])
            self.assertTrue(directory.exists())
            self.assertTrue(launcher.cleanup(home, nonce, creator=True)["cleaned"])

    def test_lost_prepare_or_started_output_still_reaps_and_persists_receipt(self):
        for lost_at in ["prepared", "child_started"]:
            with tempfile.TemporaryDirectory(prefix="eidolon-lease-") as value:
                home, nonce = Path(value), "f" * 16
                read, write = os.pipe()
                if lost_at == "child_started":
                    os.write(write, (json.dumps({"type": "start", "nonce": nonce, "mode": "echo"}) + "\n").encode())
                def emit(event):
                    if event["type"] == lost_at:
                        raise BrokenPipeError("synthetic lost SSH output")
                try:
                    result = launcher.supervise(home, nonce, read, emit, lease_seconds=1, grace_seconds=0.15,
                                                factory=lambda mode, token: [sys.executable, "-c", "import time;time.sleep(30)"])
                    self.assertEqual(result["reason"], "IOFailed")
                    self.assertTrue(result["cleaned"] and result["pipes_closed"])
                    self.assertFalse(launcher.cleanup(home, nonce)["child_alive"])
                finally:
                    os.close(write)
                    os.close(read)


if __name__ == "__main__":
    unittest.main()
