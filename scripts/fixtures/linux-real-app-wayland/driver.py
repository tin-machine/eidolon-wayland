"""Bounded controller for the genuine A0 Wayland compositor.

The owner starts/reaps all processes. This module sends physical seat key events
and checks independent file bytes; it never opens another display, injects text
into GTK, or reports Preferences widgets as verified from a settings API probe.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import time
from typing import Any, Callable

MAX_REPLY = 128 * 1024
PHASES = frozenset({"startup", "initial_main", "main_ready", "open_dialog", "open_file",
                    "edit_file", "save_file", "save_as_dialog", "save_as_file",
                    "file_oracle_complete", "normal_close", "waypipe_cleanup", "compositor_shutdown",
                    "response_probe", "forced_stop", "app_crash"})
OPERATIONS = frozenset({"snapshot", "focus", "key", "click", "close", "shutdown"})
NATIVE_ERRORS = frozenset({"STALE", "NOT_READY", "INPUT_NOT_FOCUSED", "INPUT_NOT_MAPPED",
                          "INPUT_SOURCE", "CLIENTS_REMAIN"})
PENDING_CHECKS = (
    "independent_existing_mousepad_instance",
    "normal_close_restart",
    "preferences_widgets_read_only",
    "preferences_transient_parent",
    "settings_persist_after_restart",
    "restart_after_forced_stop_has_one_main",
)
CTRL, SHIFT, ALT, ENTER, END, ESC = 29, 42, 56, 28, 107, 1


class ProbeError(RuntimeError):
    """A fixed code safe to put in a receipt; never includes payloads/paths."""


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProbeError("CONTROL_DUPLICATE_FIELD")
        result[key] = value
    return result


def _window_id(value: Any) -> bool:
    return (isinstance(value, str) and value.isascii() and value.isdecimal()
            and not value.startswith("0") and len(value) <= 19
            and int(value) <= 9223372036854775807)


def mapped(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    windows = snapshot.get("windows")
    if not isinstance(windows, list) or len(windows) > 32:
        raise ProbeError("SNAPSHOT_WINDOWS")
    result = []
    ids = set()
    for window in windows:
        if not isinstance(window, dict) or not _window_id(window.get("window")):
            raise ProbeError("SNAPSHOT_WINDOW_ID")
        if window["window"] in ids:
            raise ProbeError("SNAPSHOT_DUPLICATE_WINDOW")
        ids.add(window["window"])
        if window.get("parent") is not None and not _window_id(window["parent"]):
            raise ProbeError("SNAPSHOT_PARENT_ID")
        if window.get("life") == "Mapped":
            result.append(window)
    return result


def has_frame(window: dict[str, Any]) -> bool:
    size = window.get("logical_size")
    return (_window_id(window.get("frame")) and isinstance(size, dict)
            and all(type(size.get(k)) is int and size[k] > 0 for k in ("width", "height"))
            and type(window.get("layers")) is int and window["layers"] > 0)


def transient_to(window: str, main: str, windows: list[dict[str, Any]]) -> bool:
    records = {w["window"]: w for w in windows}
    seen = set()
    cursor = window
    while cursor != main:
        if cursor in seen or cursor not in records:
            return False
        seen.add(cursor)
        cursor = records[cursor].get("parent")
        if cursor is None:
            return False
    return window != main


def diagnostic_metadata(control: Any) -> dict[str, Any]:
    """Bounded fixed metadata only; never copy arbitrary snapshot properties."""
    def identifier(value: Any) -> str | None:
        return value if _window_id(value) else None
    def count(value: Any) -> int | None:
        return value if type(value) is int and 0 <= value <= 18446744073709551615 else None
    phase, operation = getattr(control, "phase", None), getattr(control, "last_operation", None)
    result = {"phase": phase if isinstance(phase, str) and phase in PHASES else "unknown",
              "operation": operation if isinstance(operation, str) and operation in OPERATIONS else None,
              "native_error": None, "windows": [], "window_count": None, "windows_truncated": False}
    error = getattr(control, "last_error", None)
    if error is not None:
        result["native_error"] = error if isinstance(error, str) and error in NATIVE_ERRORS else "REJECTED"
    snapshot = getattr(control, "last_snapshot", None)
    if not isinstance(snapshot, dict):
        return result
    windows = snapshot.get("windows")
    if isinstance(windows, list) and len(windows) <= 32:
        result["window_count"] = len(windows)
        result["windows_truncated"] = len(windows) > 8
        for window in windows[:8]:
            if not isinstance(window, dict):
                continue
            app_id = window.get("app_id")
            safe_id = (app_id is None or isinstance(app_id, str) and len(app_id) <= 128
                       and app_id.isascii() and all(c.isalnum() or c in "._-" for c in app_id))
            life = window.get("life")
            result["windows"].append({
                "window": identifier(window.get("window")), "parent": identifier(window.get("parent")),
                "app_id": app_id if safe_id else None,
                "app_id_valid": safe_id and window.get("app_id_valid") is True,
                "life": life if life in ("Created", "AwaitingInitialCommit", "Configured", "Mapped", "Unmapped", "Destroyed") else None,
                "frame": identifier(window.get("frame")), "layers": count(window.get("layers")),
            })
    input_state = snapshot.get("input")
    if isinstance(input_state, dict):
        result["input"] = {"focus_window": identifier(input_state.get("focus_window")),
                           "focus_token": identifier(input_state.get("focus_token")),
                           "pressed": count(input_state.get("pressed")), "delivered": count(input_state.get("delivered"))}
    return result


class Control:
    def __init__(self, process: Any, timeout: float = 4.0, pump: Callable[[], None] | None = None,
                 *, lifetime: str, run_limit_seconds: int):
        if (type(lifetime) is not str or lifetime not in ("run", "precursor")
                or type(run_limit_seconds) is not int or run_limit_seconds <= 0):
            raise ProbeError("CONTROL_EXPECTATION")
        if process.stdin is None or process.stdout is None or not 0 < timeout <= 10:
            raise ProbeError("CONTROL_PIPES")
        self.process = process
        self.pump = pump or (lambda: None)
        self.timeout = timeout
        self.next_id = 0
        self.buffer = bytearray()
        self.main: str | None = None
        self.expected_app_id: str | None = None
        self.last_snapshot: dict[str, Any] | None = None
        self.phase = "startup"
        self.last_operation: str | None = None
        self.last_error: str | None = None
        os.set_blocking(process.stdin.fileno(), False)
        os.set_blocking(process.stdout.fileno(), False)
        ready = self._read(time.monotonic() + timeout)
        if (type(ready.get("schema")) is not int or type(ready.get("run_limit_seconds")) is not int
                or ready != {"schema": 1, "event": "ready", "socket_name": "wayland-l7a",
                             "protocol": "eidolon-a0-control-v1", "server": "Eidolon ProtocolState",
                             "lifetime": lifetime, "run_limit_seconds": run_limit_seconds}):
            raise ProbeError("CONTROL_READY")

    def _read(self, deadline: float) -> dict[str, Any]:
        fd = self.process.stdout.fileno()
        while True:
            self.pump()
            end = self.buffer.find(b"\n")
            if end >= 0:
                if end > MAX_REPLY:
                    raise ProbeError("CONTROL_REPLY_BOUND")
                line = bytes(self.buffer[:end])
                del self.buffer[:end + 1]
                try:
                    value = json.loads(line, object_pairs_hook=_object,
                                       parse_constant=lambda _: (_ for _ in ()).throw(ProbeError("CONTROL_JSON")))
                except (ValueError, UnicodeError) as exc:
                    raise ProbeError("CONTROL_JSON") from exc
                if not isinstance(value, dict):
                    raise ProbeError("CONTROL_JSON_OBJECT")
                return value
            if len(self.buffer) > MAX_REPLY:
                raise ProbeError("CONTROL_REPLY_BOUND")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeError("CONTROL_TIMEOUT")
            if not select.select([fd], [], [], min(0.02, remaining))[0]:
                continue
            try:
                chunk = os.read(fd, 16384)
            except BlockingIOError:
                continue
            if not chunk:
                raise ProbeError("CONTROL_EOF")
            self.buffer.extend(chunk)

    def request(self, operation: str, **fields: Any) -> dict[str, Any]:
        self.last_operation = operation if operation in OPERATIONS else None
        self.last_error = None
        self.next_id += 1
        if self.next_id > 4294967295:
            raise ProbeError("CONTROL_SEQUENCE")
        encoded = json.dumps({"op": operation, "id": self.next_id, **fields},
                             separators=(",", ":"), allow_nan=False).encode("ascii") + b"\n"
        if len(encoded) > 4096:
            raise ProbeError("CONTROL_REQUEST_BOUND")
        deadline = time.monotonic() + self.timeout
        fd = self.process.stdin.fileno()
        while encoded:
            self.pump()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeError("CONTROL_TIMEOUT")
            if not select.select([], [fd], [], min(0.02, remaining))[1]:
                continue
            try:
                count = os.write(fd, encoded)
            except BlockingIOError:
                continue
            except BrokenPipeError as exc:
                raise ProbeError("CONTROL_EOF") from exc
            encoded = encoded[count:]
        reply = self._read(deadline)
        if type(reply.get("id")) is not int or reply["id"] != self.next_id:
            raise ProbeError("CONTROL_REPLY_SEQUENCE")
        snapshot = reply.get("snapshot")
        if not isinstance(snapshot, dict):
            raise ProbeError("CONTROL_SNAPSHOT")
        mapped(snapshot)
        self.last_snapshot = snapshot
        if reply.get("ok") is not True:
            # Preserve only known fixed codes; never propagate arbitrary text.
            error = reply.get("error")
            self.last_error = error if isinstance(error, str) and error in NATIVE_ERRORS else "REJECTED"
            raise ProbeError("CONTROL_OPERATION_FAILED")
        return snapshot

    def snapshot(self) -> dict[str, Any]:
        return self.request("snapshot")

    def focus(self, window: str) -> dict[str, Any]:
        return self.request("focus", window=window)

    def key(self, window: str, evdev: int, pressed: bool) -> dict[str, Any]:
        return self.request("key", window=window, evdev=evdev, pressed=pressed)

    def click(self, window: str, u: float, v: float) -> dict[str, Any]:
        return self.request("click", window=window, uv=[u, v])

    def close(self, window: str) -> dict[str, Any]:
        return self.request("close", window=window)

    def shutdown(self) -> dict[str, Any]:
        return self.request("shutdown")

    def wait(self, predicate: Callable[[dict[str, Any]], Any], timeout: float,
             failure: str) -> Any:
        if not 0 < timeout <= 30:
            raise ProbeError("WAIT_BOUND")
        deadline = time.monotonic() + timeout
        while True:
            snapshot = self.snapshot()
            result = predicate(snapshot)
            if result:
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeError(failure)
            time.sleep(min(0.02, remaining))

    def settle(self, seconds: float = 0.15) -> None:
        # UI focus changes are asynchronous. Dispatch continues; this bounded
        # settling interval is not counted as proof that a requested UI exists.
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.assert_topology(self.snapshot())
            time.sleep(0.01)

    def wait_main(self, expected_app_id: str, timeout: float = 20.0) -> str:
        if self.main is not None:
            raise ProbeError("MAIN_ALREADY_LATCHED")
        self.phase = "initial_main"
        def initial(snapshot: dict[str, Any]) -> str | None:
            windows = mapped(snapshot)
            if not windows:
                return None
            if len(windows) != 1 or windows[0].get("parent") is not None:
                raise ProbeError("INITIAL_NOT_SINGLE_MAIN")
            first = windows[0]
            if first.get("app_id") != expected_app_id or first.get("app_id_valid") is not True:
                raise ProbeError("INITIAL_APP_ID")
            observed = snapshot.get("observed_mapped", [])
            if (not isinstance(observed, list) or not observed
                    or any(w.get("window") != first["window"] for w in observed)):
                raise ProbeError("INITIAL_MAIN_FIRST")
            return first["window"] if has_frame(first) else None
        self.main = self.wait(initial, timeout, "INITIAL_MAIN_TIMEOUT")
        self.expected_app_id = expected_app_id
        self.focus(self.main)
        self.settle()
        self.phase = "main_ready"
        return self.main

    def assert_topology(self, snapshot: dict[str, Any]) -> None:
        if self.main is None:
            return
        windows = mapped(snapshot)
        main = next((w for w in windows if w["window"] == self.main), None)
        if main is None or main.get("parent") is not None:
            raise ProbeError("MAIN_LIFETIME_LOST")
        for window in windows:
            if window.get("app_id_valid") is not True:
                raise ProbeError("APP_ID_INVALID")
            if window["window"] == self.main:
                if window.get("app_id") != self.expected_app_id:
                    raise ProbeError("MAIN_APP_ID_CHANGED")
            elif (not transient_to(window["window"], self.main, windows)
                  or window.get("app_id") not in (None, "", self.expected_app_id)):
                raise ProbeError("DIALOG_NOT_TRANSIENT")

    def wait_dialog(self, previous: set[str], timeout: float = 5.0) -> str:
        def found(snapshot: dict[str, Any]) -> str | None:
            self.assert_topology(snapshot)
            candidates = [w for w in mapped(snapshot) if w["window"] not in previous]
            if len(candidates) > 1:
                raise ProbeError("DIALOG_AMBIGUOUS")
            return candidates[0]["window"] if candidates and has_frame(candidates[0]) else None
        return self.wait(found, timeout, "DIALOG_TIMEOUT")

    def wait_closed(self, dialog: str, timeout: float = 5.0) -> None:
        def gone(snapshot: dict[str, Any]) -> bool:
            self.assert_topology(snapshot)
            return not any(w["window"] == dialog for w in mapped(snapshot))
        self.wait(gone, timeout, "DIALOG_CLOSE_TIMEOUT")


def chord(control: Any, window: str, *codes: int) -> None:
    pressed = []
    try:
        for code in codes:
            control.key(window, code, True)
            pressed.append(code)
    finally:
        # Release modifiers even if an intermediate key fails. The enclosing
        # owner still terminates the run on the original error.
        release_error = None
        for code in reversed(pressed):
            try:
                control.key(window, code, False)
            except ProbeError as error:
                release_error = error
        if release_error is not None:
            raise release_error


def observe_ready(control: Control) -> dict[str, Any]:
    """Current one-main/frame observation without resetting any latch/history."""
    state = control.snapshot()
    control.assert_topology(state)
    windows = mapped(state)
    if len(windows) != 1 or windows[0]["window"] != control.main or not has_frame(windows[0]):
        raise ProbeError("RESPONSE_NOT_SINGLE_MAIN")
    counts, inputs = state.get("counts", {}), state.get("input", {})
    if (type(counts.get("clients")) is not int or counts["clients"] != 1
            or type(inputs.get("delivered")) is not int or inputs["delivered"] < 0
            or type(inputs.get("pressed")) is not int or inputs["pressed"] != 0):
        raise ProbeError("RESPONSE_CLIENT_INPUT")
    observed = state.get("observed_mapped")
    if not isinstance(observed, list) or not observed:
        raise ProbeError("RESPONSE_OBSERVATION_HISTORY")
    return {"window": control.main, "frame": windows[0]["frame"],
            "app_id": control.expected_app_id, "client_count": counts["clients"],
            "observed_mapped_count": len(observed),
            "input_delivered": inputs["delivered"], "pressed": inputs["pressed"]}


def exercise_response(control: Control) -> dict[str, Any]:
    """Application acknowledgement via a real Ctrl+O/Escape dialog round trip.

    A compositor input counter alone cannot prove Mousepad still responds.
    Require a new transient dialog with an SHM frame, then its removal and the
    exact original main. No document or preference is edited by this probe.
    """
    before = observe_ready(control)
    control.phase = "response_probe"
    control.focus(control.main)
    chord(control, control.main, CTRL, 24)
    dialog = control.wait_dialog({control.main})
    control.focus(dialog)
    control.settle()
    chord(control, dialog, ESC)
    control.wait_closed(dialog)
    control.focus(control.main)
    control.settle()
    after = observe_ready(control)
    if after["input_delivered"] <= before["input_delivered"]:
        raise ProbeError("RESPONSE_INPUT_NOT_DELIVERED")
    return {"responsive": True, "main": after["window"], "frame": after["frame"],
            "input_delivered_before": before["input_delivered"],
            "input_delivered_after": after["input_delivered"],
            "window_count": 1, "client_count": after["client_count"], "pressed": after["pressed"],
            "topology_preserved": after["window"] == before["window"],
            "dialog_window": dialog}


_KEYS = {letter: (code, False) for letter, code in zip(
    "qwertyuiopasdfghjklzxcvbnm", [16,17,18,19,20,21,22,23,24,25,30,31,32,33,34,35,36,37,38,44,45,46,47,48,49,50])}
_KEYS.update({str(n): (n + 1, False) for n in range(1, 10)})
_KEYS.update({"0": (11, False), " ": (57, False), "\n": (ENTER, False),
              "-": (12, False), "_": (12, True), ".": (52, False),
              "/": (53, False), ":": (39, True)})
_KEYS.update({letter.upper(): (code, True) for letter, (code, _) in list(_KEYS.items()) if letter.isalpha()})


def type_ascii(control: Any, window: str, text: str) -> None:
    if not isinstance(text, str) or not 1 <= len(text) <= 1024 or any(c not in _KEYS for c in text):
        raise ProbeError("SYNTHETIC_TEXT_UNSUPPORTED")
    for char in text:
        code, shift = _KEYS[char]
        chord(control, window, *([SHIFT, code] if shift else [code]))


def wait_file_bytes(path: Path, expected: bytes, timeout: float = 5.0,
                    pump: Callable[[], None] | None = None) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if pump is not None:
            pump()
        try:
            if path.is_symlink():
                raise ProbeError("FILE_ORACLE_SYMLINK")
            with path.open("rb") as file:
                actual = file.read(len(expected) + 1)
            if actual == expected:
                return
        except FileNotFoundError:
            pass
        if time.monotonic() >= deadline:
            raise ProbeError("FILE_ORACLE_TIMEOUT")
        time.sleep(0.02)


def exercise_open_edit_save(control: Control, input_path: Path, output_path: Path,
                            initial: bytes, appended: str = "eidolon-a0-saved\n") -> dict[str, Any]:
    """Drive the real UI. Only exact independently read bytes certify Save.

    input_path must be a caller-created UTF-8 synthetic text file, output_path
    absent in the same synthetic directory. No user data is read or logged.
    Preferences read-only UI evidence deliberately stays pending.
    """
    if control.main is None or not initial.endswith(b"\n") or len(initial) > 4096:
        raise ProbeError("FILE_ORACLE_INPUT")
    for path in (input_path, output_path):
        if (not path.is_absolute() or len(str(path)) > 256
                or any(c not in _KEYS or c == "\n" for c in str(path))
                or path.is_symlink()):
            raise ProbeError("FILE_ORACLE_PATH")
    if input_path.parent != output_path.parent or output_path.exists():
        raise ProbeError("FILE_ORACLE_DESTINATION")
    try:
        initial.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ProbeError("FILE_ORACLE_ENCODING") from exc
    wait_file_bytes(input_path, initial, 0.2)
    main = control.main
    before = control.snapshot()
    control.assert_topology(before)
    delivered = before["input"]["delivered"]
    previous = {w["window"] for w in mapped(before)}
    control.phase = "open_dialog"
    control.focus(main)
    chord(control, main, CTRL, 24)  # Ctrl+O
    dialog = control.wait_dialog(previous)
    control.phase = "open_file"
    control.focus(dialog)
    chord(control, dialog, CTRL, 38)  # chooser Location, Ctrl+L
    control.settle()
    chord(control, dialog, CTRL, 30)
    type_ascii(control, dialog, str(input_path))
    # GTK 3.24.41 debounces location selection-changed by 150 ms; that
    # callback enables the accept button. Dispatch after the last character
    # before sending its single activation (gtkfilechooserwidget.c:2570-2613).
    control.settle(0.25)
    chord(control, dialog, ENTER)
    control.wait_closed(dialog)
    control.phase = "edit_file"
    control.focus(main)
    control.settle()
    chord(control, main, CTRL, END)
    type_ascii(control, main, appended)
    control.phase = "save_file"
    chord(control, main, CTRL, 31)  # Ctrl+S
    expected = initial + appended.encode("ascii")
    wait_file_bytes(input_path, expected, pump=control.pump)
    previous = {w["window"] for w in mapped(control.snapshot())}
    control.phase = "save_as_dialog"
    chord(control, main, CTRL, SHIFT, 31)  # Ctrl+Shift+S
    save_dialog = control.wait_dialog(previous)
    control.phase = "save_as_file"
    control.focus(save_dialog)
    chord(control, save_dialog, CTRL, 38)
    control.settle()
    chord(control, save_dialog, CTRL, 30)
    type_ascii(control, save_dialog, str(output_path))
    control.settle(0.25)
    chord(control, save_dialog, ENTER)
    control.wait_closed(save_dialog)
    wait_file_bytes(output_path, expected, pump=control.pump)
    after = control.snapshot()
    control.assert_topology(after)
    if after["input"]["delivered"] <= delivered or after["input"]["pressed"] != 0:
        raise ProbeError("INPUT_DELIVERY_OR_RELEASE")
    control.phase = "file_oracle_complete"
    return {"schema": 1, "state": "partial", "scope": "waypipe-mousepad-seat-file-io",
            "checks": {"initial_single_main": True, "main_app_id": True,
                       "initial_shm_frame": True, "open_dialog_parent": True,
                       "save_as_dialog_parent": True, "seat_input_delivered": True,
                       "opened_edited_saved_bytes": True, "save_as_bytes": True},
            "remaining_checks": list(PENDING_CHECKS), "feasibility_verified": False,
            "physical_input_verified": False, "main_window": main,
            "open_dialog_window": dialog, "save_as_dialog_window": save_dialog,
            "payload_logged": False, "titles_logged": False, "pixels_logged": False}
