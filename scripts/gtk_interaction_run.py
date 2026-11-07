"""固定GTK fixtureのpin/leaseを検査し、本文なしcounterとSHMを自然終了まで観測する。"""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys

from sdl_lease_capture import CaptureError, canonical, capture_remote, decode, hexadecimal, need
from sdl_probe_install import termux_home
from sdl_probe_run import checked_file, live_binding
from gtk_probe_run import file_identity, fixed_environment, observe_owned, private_run, validate_target as validate_gtk_runtime
from sdl_observation import WaylandTrace

UID = 10158
OPERATOR = "eidolon-gtk-interaction-v1"
SOURCE_PINS = None  # hostがrepository内の固定C/build sourceの期待値だけを埋め込む。
CASES = {"input", "dialog", "resize"}
COUNTERS = {"a_down", "a_up", "delete_down", "delete_up", "other_keys", "entry_changes", "button_clicks",
            "dialog_opened", "dialog_closed", "dialog_responses", "main_configures", "dialog_configures", "main_draws", "dialog_draws"}
BOOLS = {"complete", "host_selftest", "physical_input_verified", "token_seen", "delete_seen", "dialog_parent_set",
         "dialog_visible", "resize_large_seen", "resize_return_seen"}
FIELDS = COUNTERS | BOOLS | {"schema", "probe", "run_id", "case", "app_id", "dialog_app_id", "pid", "uid", "sequence",
                             "phase", "reason", "logical_size", "entry_rect", "button_rect"}


def app_ids(nonce):
    main = "io.github.tinmachine.eidolon.gtkfixture.r" + nonce
    return main, main + ".dialog"


def completed(row, case):
    if not row or not row["complete"]: return False
    if case == "input":
        return (all(row[k] == 1 for k in ("a_down", "a_up", "delete_down", "delete_up", "button_clicks")) and
                row["entry_changes"] == 2 and row["other_keys"] == 0 and row["token_seen"] and row["delete_seen"])
    if case == "dialog":
        return all(row[k] == 1 for k in ("dialog_opened", "dialog_closed", "dialog_responses")) and row["dialog_parent_set"] and not row["dialog_visible"]
    return row["resize_large_seen"] and row["resize_return_seen"] and row["logical_size"] == [640, 360]


class FixtureReports:
    def __init__(self, nonce, case, emit, *, uid=UID):
        self.nonce, self.case, self.emit, self.uid = nonce, case, emit, uid
        self.pid, self.pending, self.rows, self.bytes = None, bytearray(), [], 0

    def feed(self, data):
        self.bytes += len(data);need(self.bytes <= 524288, "GTK_REPORT_LIMIT")
        self.pending.extend(data)
        while b"\n" in self.pending:
            line, _, rest = self.pending.partition(b"\n");self.pending = bytearray(rest)
            value = decode(line, 16384)
            need(type(value) is dict and set(value) == FIELDS and type(value["schema"]) is int and value["schema"] == 1 and
                 value["probe"] == "eidolon-gtk3-interaction-v1" and value["case"] == self.case and value["run_id"] == self.nonce and
                 (value["app_id"], value["dialog_app_id"]) == app_ids(self.nonce) and value["uid"] == self.uid and value["pid"] == self.pid,
                 "GTK_REPORT_IDENTITY")
            need(all(type(value[k]) is bool for k in BOOLS) and not value["host_selftest"] and not value["physical_input_verified"] and
                 all(type(value[k]) is int and 0 <= value[k] <= 2147483647 for k in COUNTERS), "GTK_REPORT_TYPE")
            need(type(value["sequence"]) is int and value["sequence"] == len(self.rows) + 1 and len(self.rows) < 256 and
                 value["phase"] in {"ready", "state", "final"} and value["reason"] in
                 {"running", "completed", "window_closed", "timeout", "display_unavailable", "wayland_required"}, "GTK_REPORT_PHASE")
            for key, count in (("logical_size", 2), ("entry_rect", 4), ("button_rect", 4)):
                need(type(value[key]) is list and len(value[key]) == count and all(type(n) is int and 0 <= n <= 4096 for n in value[key]), "GTK_REPORT_GEOMETRY")
            need(not self.rows or all(value[k] >= self.rows[-1][k] for k in COUNTERS), "GTK_REPORT_COUNTER_REGRESSED")
            need(not self.rows or self.rows[-1]["phase"] != "final", "GTK_REPORT_AFTER_FINAL")
            self.rows.append(value);self.emit(dict(phase="fixture", report=value))
        need(len(self.pending) <= 16384, "GTK_REPORT_LIMIT")

    def summary(self):
        last = self.rows[-1] if self.rows else None
        return dict(reports=self.rows, bytes=self.bytes, complete=not self.pending and bool(last) and
                    last["phase"] == "final" and last["reason"] == "completed" and completed(last, self.case))


class InteractionTrace(WaylandTrace):
    def __init__(self, nonce):
        super().__init__();self.expected = app_ids(nonce)
        self.top_surfaces, self.roles, self.parents, self.commits = {}, {}, set(), dict(main=0, dialog=0)
        self.popups = self.unknown_app_ids = 0

    def line(self, line):
        super().line(line)
        if len(self.top_surfaces) > 128 or len(self.parents) > 128: self.complete = False;return
        match = re.search(r"xdg_surface[@#](\d+)\.get_toplevel\(new id xdg_toplevel[@#](\d+)\)", line)
        if match and int(match[1]) in self.xdg_surfaces: self.top_surfaces[int(match[2])] = self.xdg_surfaces[int(match[1])]
        match = re.search(r'xdg_toplevel[@#](\d+)\.set_app_id\("([^"\\]*)"\)', line)
        if match:
            if match[2] in self.expected: self.roles[int(match[1])] = "main" if match[2] == self.expected[0] else "dialog"
            else: self.unknown_app_ids += 1
        match = re.search(r"xdg_toplevel[@#](\d+)\.set_parent\(xdg_toplevel[@#](\d+)\)", line)
        if match: self.parents.add((int(match[1]), int(match[2])))
        if re.search(r"xdg_surface[@#]\d+\.get_popup\(", line): self.popups += 1
        match = re.search(r"wl_surface[@#](\d+)\.commit\(", line)
        if match and self.attached.get(int(match[1])) == "shm":
            for top, surface in self.top_surfaces.items():
                if surface == int(match[1]) and top in self.roles: self.commits[self.roles[top]] += 1

    def summary(self):
        result = super().summary()
        result.update(fixture_shm_commits=self.commits, popup_count=self.popups, unknown_app_ids=self.unknown_app_ids,
                      dialog_parent_observed=any(self.roles.get(child) == "dialog" and self.roles.get(parent) == "main" for child, parent in self.parents))
        return result


def validate_artifacts(request, home):
    need(os.getuid() == UID and hexadecimal(request["run_id"], 32) and hexadecimal(request["binary_sha256"], 64) and
         hexadecimal(request["manifest_sha256"], 64), "GTK_ARTIFACT_REQUEST_INVALID")
    need(type(SOURCE_PINS) is dict and set(SOURCE_PINS) == {"gtk3-interaction-fixture.c", "build-gtk3-fixture.py"}, "GTK_SOURCE_PINS_MISSING")
    validate_gtk_runtime(home)  # 既知gtk3.24.52 packageと同梱binaryのpinを再利用する。
    directory = home / ".local/state/eidolon/nx15-fixture" / request["run_id"]
    fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in (".local", "state", "eidolon", "nx15-fixture", request["run_id"]):
            info = os.fstat(fd);need(info.st_uid == UID and not info.st_mode & 0o022, "GTK_DIRECTORY_UNSAFE")
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd);os.close(fd);fd = child
        need(stat.S_IMODE(os.fstat(fd).st_mode) == 0o700, "GTK_DIRECTORY_UNSAFE")
        data = checked_file(fd, "manifest.json", UID, 0o600, 65536)
        need(hashlib.sha256(data).hexdigest() == request["manifest_sha256"], "GTK_MANIFEST_CHANGED")
        manifest = decode(data, 65536)
        need(manifest.get("kind") == "eidolon-gtk3-fixture-source-v1" and manifest.get("run_id") == request["run_id"] and
             manifest.get("schema") == 1 and manifest.get("files") == SOURCE_PINS, "GTK_MANIFEST_INVALID")
        for name, pin in SOURCE_PINS.items():
            data = checked_file(fd, name, UID, 0o600, 65536)
            need(len(data) == pin["bytes"] and hashlib.sha256(data).hexdigest() == pin["sha256"], "GTK_SOURCE_CHANGED")
        build = os.open("build", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            info = os.fstat(build);need(info.st_uid == UID and stat.S_IMODE(info.st_mode) == 0o700, "GTK_DIRECTORY_UNSAFE")
            before = os.stat("gtk3-fixture", dir_fd=build, follow_symlinks=False)
            data = checked_file(build, "gtk3-fixture", UID, 0o700, 4*1024*1024)
            after = os.stat("gtk3-fixture", dir_fd=build, follow_symlinks=False)
            need(file_identity(before) == file_identity(after) and hashlib.sha256(data).hexdigest() == request["binary_sha256"] and
                 data[:6] == b"\x7fELF\x02\x01" and int.from_bytes(data[18:20], "little") == 183, "GTK_BINARY_CHANGED")
            receipt = decode(checked_file(build, "build.json", UID, 0o600, 16384), 16384)
            need(receipt.get("status") == "built" and receipt.get("executed") is False and receipt.get("gtk_version") == "3.24.52" and
                 receipt.get("manifest_sha256") == request["manifest_sha256"] and receipt.get("binary_sha256") == request["binary_sha256"] and
                 receipt.get("binary_bytes") == len(data) and receipt.get("elf_machine") == 183, "GTK_BUILD_RECEIPT_CHANGED")
        finally: os.close(build)
    finally: os.close(fd)
    return directory / "build/gtk3-fixture", file_identity(after)


def run_operator(request, home, control_fd, emit, *, signal_state=None):
    need(type(request) is dict and set(request) == {"binding", "run_id", "binary_sha256", "manifest_sha256", "case", "nonce"} and
         request["case"] in CASES and hexadecimal(request["nonce"], 32) and request["binding"]["uid"] == UID, "GTK_REQUEST_INVALID")
    captured = capture_remote(request["binding"], home)
    binary, expected = validate_artifacts(request, home)
    def checker():
        need(not (signal_state and signal_state["signal"]), "WRAPPER_SIGNAL")
        live_binding(request["binding"], home, captured)
    checker()
    directory = private_run(home, request["nonce"])
    parser, trace = FixtureReports(request["nonce"], request["case"], emit, uid=UID), InteractionTrace(request["nonce"])
    def event(value):
        if value["phase"] == "started": parser.pid = value["child_pid"]
        emit(value)
    emit(dict(phase="validated", nonce=request["nonce"], case=request["case"], uid=UID))
    observed = observe_owned([str(binary), "--case", request["case"], "--run-id", request["nonce"]],
                             fixed_environment(home, directory, captured), control_fd, checker, event,
                             executable_identity=expected, observation_seconds=35, natural=True,
                             stdout_handler=parser.feed, trace=trace, maximum_stdout=524288,
                             spawn_handler=lambda pid:setattr(parser,"pid",pid))
    try: checker();validate_artifacts(request, home)
    except (OSError, ValueError, TypeError, KeyError): observed.update(error="GTK_BINDING_OR_ARTIFACT_CHANGED")
    reports = parser.summary();summary = observed["trace"]
    shm = (summary["complete"] and summary["content_shm_commits"] >= 1 and summary["protocol_errors"] == 0 and
           summary["content_non_shm_commits"] == summary["content_unknown_commits"] == summary["unknown_app_ids"] == 0 and
           set(summary["content_shm_formats"]) <= {0, 1} and summary["fixture_shm_commits"]["main"] >= 1 and
           (request["case"] != "dialog" or summary["fixture_shm_commits"]["dialog"] >= 1 and summary["dialog_parent_observed"] and summary["popup_count"] == 0))
    passed = (reports["complete"] and shm and observed["cleanup_confirmed"] and observed["child_returncode"] == 0 and
              observed["stop_reason"] == "NATURAL_EXIT" and observed["error"] is None and observed["cancel_reason"] is None and not observed["term_sent"])
    observed.update(schema=1, operator=OPERATOR, status="observed" if passed else "failed", case=request["case"], nonce=request["nonce"],
                    run_id=request["run_id"], binary_sha256=request["binary_sha256"], manifest_sha256=request["manifest_sha256"],
                    fixture=reports, shm_gate="pass" if shm else "fail", capture=captured, physical_input="not_run", ime="not_run", hmd="not_run")
    fd = os.open(directory / "report.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream: stream.write(canonical(observed)+b"\n");stream.flush();os.fsync(stream.fileno())
    emit(dict(phase="finished", result=observed))
    return observed


def remote_main():
    previous, state = {}, {"signal": False}
    os.umask(0o077)
    try:
        request = bytearray()
        while len(request) <= 16384:
            byte = os.read(0, 1);need(byte, "STARTUP_EOF")
            if byte == b"\n": break
            request.extend(byte)
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP): previous[number] = signal.signal(number, lambda *_: state.__setitem__("signal", True))
        report = run_operator(decode(bytes(request), 16384), termux_home(sys.executable), 0,
                              lambda value: print(json.dumps(value), flush=True), signal_state=state)
        return 0 if report["status"] == "observed" else 1
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps(dict(phase="blocked", reason=error.reason if isinstance(error, CaptureError) else "GTK_OPERATOR_UNAVAILABLE")), flush=True)
        return 2
    finally:
        for number, handler in previous.items(): signal.signal(number, handler)
