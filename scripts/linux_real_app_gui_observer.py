"""Read-only fixed GTK observation for explicitly marked synthetic A0 runs.

All widget mutations still travel through the genuine compositor/Waypipe seat.
Coordinates and widget properties returned here are private control data. The
harness projects them to a fixed receipt schema; never publish this snapshot.
"""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import re
import select
import socket
import stat
import struct
import time

import linux_real_app_inspect as inspect

MODULE_NAME = "libeidolon-a0-gui-observer.so"
MANIFEST_NAME = "gui-observer.json"
MARKER_NAME = "gui-observer-v1"
SOCKET_NAME = "gui-observer.sock"
MAX_REPLY = 16384
TARGETS = frozenset({"line_numbers", "opening_mode", "session_restore", "always_show_tabs",
                     "make_backup", "tab_view", "tab_window", "tab_file"})
OBSERVATION_FAILURES = frozenset({
    "APP_GUI_OBSERVER_WAYLAND_DISPLAY", "APP_GUI_OBSERVER_DUP_MAIN", "APP_GUI_OBSERVER_DUP_PREFS",
    "APP_GUI_OBSERVER_DUP_VIEW", "APP_GUI_OBSERVER_DUP_NOTEBOOK", "APP_GUI_OBSERVER_DUP_MENUBAR",
    "APP_GUI_OBSERVER_DUP_TARGET", "APP_GUI_OBSERVER_SCAN_BOUND",
})


class ObserverError(ValueError):
    """Fixed, payload-free failure codes suitable for the enclosing receipt."""


def require(value, code="APP_GUI_OBSERVER_INVALID"):
    if not value:
        raise ObserverError(code)


def _identifier(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def _private(path):
    fd = inspect._open_absolute_directory(path)
    try:
        info = os.fstat(fd)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o700, "APP_GUI_OBSERVER_DIRECTORY")
        return info
    finally:
        os.close(fd)


def prepare(runtime, profile_root, profile_id, runtime_id):
    """Mark only the supplied live synthetic run; never alter its settings."""
    runtime, profile_root = Path(runtime), Path(profile_root)
    require(_identifier(profile_id) and _identifier(runtime_id), "APP_GUI_OBSERVER_ID")
    _private(runtime)
    identity = _private(profile_root)
    for child in ("home", "config", "data", "state"):
        _private(profile_root / child)
    require(len(os.fsencode(runtime / SOCKET_NAME)) < 108, "APP_GUI_OBSERVER_SOCKET_PATH")
    marker = ("eidolon-a0-gui-observer-v1\n" + runtime_id + "\n" + profile_id + "\n"
              + f"{identity.st_dev}:{identity.st_ino}\n").encode("ascii")
    fd = os.open(runtime / MARKER_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(marker)
        output.flush()
        os.fsync(output.fileno())
    return {"EIDOLON_A0_GUI_RUN_ID": runtime_id, "EIDOLON_A0_GUI_PROFILE_ID": profile_id}


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def _integer(value, minimum=0, maximum=4096):
    return type(value) is int and minimum <= value <= maximum


def _pair(value, minimum=0, maximum=32768):
    return type(value) is list and len(value) == 2 and all(_integer(x, minimum, maximum) for x in value)


def _target(target, kind):
    require(type(target) is dict and set(target) == {"present", "mapped", "sensitive", "center",
                                                    "size", "active", "expected_value", "value_digest"})
    require(all(type(target[k]) is bool for k in ("present", "mapped", "sensitive")))
    require((target["center"] is None and target["size"] is None)
            or (_pair(target["center"], -32768) and _pair(target["size"], 1)))
    require(target["active"] is None or type(target["active"]) is bool)
    require(target["expected_value"] is None or type(target["expected_value"]) is bool)
    require(target["value_digest"] is None or type(target["value_digest"]) is str
            and re.fullmatch(r"[0-9a-f]{64}", target["value_digest"]) is not None)
    if not target["present"]:
        require(not target["mapped"] and not target["sensitive"]
                and all(target[k] is None for k in ("center", "size", "active", "expected_value", "value_digest")))
    elif kind == "combo":
        require(target["active"] is None and type(target["expected_value"]) is bool)
    elif kind == "plain":
        require(all(target[k] is None for k in ("active", "expected_value", "value_digest")))
    else:
        require(type(target["active"]) is bool and target["expected_value"] is None
                and target["value_digest"] is None)


def validate_snapshot(value, request, profile_id, runtime_id):
    """Reject stale, ambiguous, oversized and free-form observer replies."""
    require(type(value) is dict)
    require(type(value.get("schema")) is int and value["schema"] == 1
            and type(value.get("id")) is int and value["id"] == request
            and value.get("run_id") == runtime_id and value.get("profile_id") == profile_id,
            "APP_GUI_OBSERVER_CORRELATION")
    require(type(value.get("ok")) is bool)
    if value["ok"] is False:
        require(set(value) == {"schema", "id", "run_id", "profile_id", "ok", "error"}
                and type(value["error"]) is str and value["error"] in OBSERVATION_FAILURES)
        raise ObserverError(value["error"])
    require(set(value) == {"schema", "id", "run_id", "profile_id", "ok", "main_count",
                           "preferences_count", "main", "preferences"})
    for field, reason in (("main_count", "APP_GUI_OBSERVER_DUP_MAIN"),
                          ("preferences_count", "APP_GUI_OBSERVER_DUP_PREFS")):
        require(_integer(value[field], 0, 1), reason)
    main = value["main"]
    if value["main_count"] == 0:
        require(main is None)
    else:
        require(type(main) is dict and set(main) == {"mapped", "view_count", "mapped_view_count",
                                                   "line_numbers", "document_tabs", "toplevel_size",
                                                   "edit_menu", "edit_menu_open", "preferences_selected"})
        require(type(main["mapped"]) is bool and _integer(main["view_count"], 0, 32)
                and _integer(main["mapped_view_count"], 0, 1)
                and main["mapped_view_count"] <= main["view_count"]
                and (type(main["line_numbers"]) is bool or main["line_numbers"] is None)
                and _integer(main["document_tabs"], 0, 32) and _pair(main["toplevel_size"], 1)
                and type(main["edit_menu_open"]) is bool and type(main["preferences_selected"]) is bool)
        _target(main["edit_menu"], "plain")
        require(not main["edit_menu_open"] or main["edit_menu"]["present"])
        require(not main["preferences_selected"] or main["edit_menu_open"])
    prefs = value["preferences"]
    if value["preferences_count"] == 0:
        require(prefs is None)
    else:
        require(type(prefs) is dict and set(prefs) == {"mapped", "transient_for_main", "page", "pages",
                                                     "toplevel_size", "targets"})
        require(type(prefs["mapped"]) is bool and type(prefs["transient_for_main"]) is bool
                and _integer(prefs["pages"], 0, 5) and _integer(prefs["page"], -1, 4)
                and prefs["page"] < prefs["pages"] and _pair(prefs["toplevel_size"], 1)
                and type(prefs["targets"]) is dict and set(prefs["targets"]) == TARGETS)
        for name, target in prefs["targets"].items():
            kind = ("combo" if name in ("opening_mode", "session_restore")
                    else "plain" if name.startswith("tab_") else "toggle")
            _target(target, kind)
    return value


class Observer:
    def __init__(self, runtime, profile_id, runtime_id, pump=None, timeout=4.0, *, peer_check=None):
        self.runtime = Path(runtime)
        require(_identifier(profile_id) and _identifier(runtime_id), "APP_GUI_OBSERVER_ID")
        require(type(timeout) in (int, float) and 0 < timeout <= 10, "APP_GUI_OBSERVER_TIMEOUT_BOUND")
        require(peer_check is None or callable(peer_check), "APP_GUI_OBSERVER_PEER_CHECK")
        _private(self.runtime)
        self.path = self.runtime / SOCKET_NAME
        require(len(os.fsencode(self.path)) < 108, "APP_GUI_OBSERVER_SOCKET_PATH")
        self.profile_id, self.runtime_id = profile_id, runtime_id
        self.pump = pump or (lambda: None)
        self.timeout = timeout
        self.sequence = 0
        self.peer_pid = None
        self.peer_check = peer_check

    def snapshot(self):
        self.sequence += 1
        require(self.sequence <= 4294967295, "APP_GUI_OBSERVER_SEQUENCE")
        deadline = time.monotonic() + self.timeout
        connection = None
        try:
            while connection is None:
                self.pump()
                require(time.monotonic() < deadline, "APP_GUI_OBSERVER_TIMEOUT")
                try:
                    info = self.path.lstat()
                    require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid()
                            and stat.S_IMODE(info.st_mode) == 0o600, "APP_GUI_OBSERVER_SOCKET")
                    current = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    try:
                        current.setblocking(False)
                        code = current.connect_ex(str(self.path))
                    except BaseException:
                        current.close()
                        raise
                    if code not in (0, errno.EINPROGRESS, errno.EAGAIN):
                        current.close()
                        require(code in (errno.ENOENT, errno.ECONNREFUSED), "APP_GUI_OBSERVER_CONNECT")
                    else:
                        connection = current
                        break
                except FileNotFoundError:
                    pass
                time.sleep(.01)
            while True:
                self.pump()
                require(time.monotonic() < deadline, "APP_GUI_OBSERVER_TIMEOUT")
                if select.select([], [connection], [], .02)[1]:
                    require(connection.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0, "APP_GUI_OBSERVER_CONNECT")
                    break
            pid, uid, _gid = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            require(pid > 0 and uid == os.getuid() and (self.peer_pid is None or self.peer_pid == pid),
                    "APP_GUI_OBSERVER_PEER")
            # The live owner may require this socket peer to be its current
            # unmodified Mousepad descendant before any snapshot is requested.
            # Its failure propagates; another UID/PID is never tried instead.
            if self.peer_check is not None:
                self.peer_check(pid)
            self.peer_pid = pid
            data = f"OBSERVE {self.sequence}\n".encode("ascii")
            output = bytearray()
            while data or not output.endswith(b"\n"):
                self.pump()
                require(time.monotonic() < deadline, "APP_GUI_OBSERVER_TIMEOUT")
                readable, writable, _ = select.select([connection], [connection] if data else [], [], .02)
                if writable:
                    try:
                        sent = connection.send(data)
                        require(sent > 0, "APP_GUI_OBSERVER_EOF")
                        data = data[sent:]
                    except BlockingIOError:
                        pass
                if readable:
                    try:
                        chunk = connection.recv(4096)
                    except BlockingIOError:
                        continue
                    require(bool(chunk), "APP_GUI_OBSERVER_EOF")
                    output.extend(chunk)
                    require(len(output) <= MAX_REPLY, "APP_GUI_OBSERVER_BOUND")
            require(not data and output.count(b"\n") == 1, "APP_GUI_OBSERVER_RECORD")
            try:
                value = json.loads(output, object_pairs_hook=_object,
                                   parse_constant=lambda _: (_ for _ in ()).throw(ObserverError("APP_GUI_OBSERVER_JSON")))
            except (UnicodeError, json.JSONDecodeError) as error:
                raise ObserverError("APP_GUI_OBSERVER_JSON") from error
            return validate_snapshot(value, self.sequence, self.profile_id, self.runtime_id)
        except OSError as error:
            raise ObserverError("APP_GUI_OBSERVER_IO") from error
        finally:
            if connection is not None:
                connection.close()
