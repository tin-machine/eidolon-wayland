"""Read the authenticated user's Linux application menu without activating apps."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import sys

MAX_ENTRIES = 1024
MAX_CATALOG_BYTES = 512 * 1024


def gio_bindings():
    # GIO reads XDG search roots in precedence order and resolves the same
    # Desktop Entry IDs for both GTK and Qt applications.
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GioUnix

    return Gio, GioUnix


def desktop_entries():
    Gio, GioUnix = gio_bindings()
    return [entry for entry in Gio.AppInfo.get_all()
            if isinstance(entry, GioUnix.DesktopAppInfo)]


def workdir_identity(path):
    if not os.path.isabs(path):
        return None
    try:
        info = os.stat(path)
    except (OSError, ValueError):
        return None
    if not stat.S_ISDIR(info.st_mode) or not os.access(path, os.X_OK):
        return None
    return [info.st_dev, info.st_ino]


def describe(entry):
    from gi.repository import GLib

    # SSH has no desktop shell. Include launchers for either desktop while
    # honoring XDG hiding, NoDisplay and the user's shadowing by desktop ID.
    if entry.get_is_hidden() or entry.get_nodisplay() or entry.get_string("Type") != "Application":
        return None
    desktop_id = entry.get_id()
    name = entry.get_display_name()
    if (not desktop_id or not name or not desktop_id.endswith(".desktop")
            or len(desktop_id.encode()) > 255 or len(name.encode()) > 255
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in desktop_id + name)):
        return None
    path = os.environ.get("PATH", os.defpath)
    try_exec = entry.get_string("TryExec")
    if try_exec and shutil.which(try_exec, path=path) is None:
        return None
    command = entry.get_commandline()
    workdir = entry.get_string("Path")
    directory_identity = workdir_identity(workdir) if workdir else None
    executable = shutil.which(entry.get_executable() or "", path=path)
    try:
        if executable:
            info = os.stat(executable)
            binary_identity = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]
        else:
            binary_identity = None
    except OSError:
        executable, binary_identity = None, None
    reason = None
    if entry.get_boolean("Terminal"):
        reason = "terminal_required"
    elif not command or executable is None:
        reason = "dbus_activation_required" if entry.get_boolean("DBusActivatable") else "executable_unavailable"
    elif workdir and directory_identity is None:
        reason = "workdir_unavailable"
    else:
        try:
            launch_argv(entry, command)
        except (ValueError, GLib.Error):
            reason = "exec_format_unsupported"
    identity = [
        desktop_id, command, executable, binary_identity, workdir, name,
        entry.get_string("Icon"), try_exec,
        entry.get_boolean("Terminal"), entry.get_boolean("DBusActivatable"),
    ]
    if workdir:
        identity.append(directory_identity)
    revision = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    return {"id": desktop_id, "name": name, "revision": revision, "disabled_reason": reason}


def catalog(entries):
    by_id = {}
    for entry in entries:
        item = describe(entry)
        if item is not None:
            by_id.setdefault(item["id"], item)
    return {"schema": 1, "entries": sorted(by_id.values(), key=lambda item: item["id"])}


def launch_argv(entry, commandline):
    """Expand one checked Desktop Entry for opening an empty document or window."""
    from gi.repository import GLib

    _, parsed = GLib.shell_parse_argv(commandline)
    if not parsed or "%" in parsed[0]:
        raise ValueError("desktop_entry_executable_invalid")
    executable = shutil.which(parsed[0], path=os.environ.get("PATH", os.defpath))
    if not executable:
        raise ValueError("desktop_entry_executable_unavailable")
    argv = [os.path.abspath(executable)]

    def field_code(match):
        code = match[0]
        if code == "%%":
            return "%"
        if code == "%c":
            return entry.get_display_name()
        if code == "%k":
            return entry.get_filename()
        raise ValueError("desktop_entry_field_code_unsupported")

    for arg in parsed[1:]:
        if arg in ("%f", "%F", "%u", "%U", "%d", "%D", "%n", "%N", "%v", "%m"):
            continue
        if arg == "%i":
            icon = entry.get_string("Icon")
            if icon:
                argv.extend(("--icon", icon))
        else:
            argv.append(re.sub(r"%(?:.|$)", field_code, arg))
    return argv


def resolve_selection(desktop_id, revision):
    """Resolve again under the same authenticated account, before spawning."""
    _, GioUnix = gio_bindings()

    entry = GioUnix.DesktopAppInfo.new(desktop_id)
    item = describe(entry) if entry is not None else None
    if item is None or item["id"] != desktop_id or item["revision"] != revision:
        raise ValueError("desktop_entry_changed")
    if item["disabled_reason"]:
        raise ValueError(item["disabled_reason"])
    argv = launch_argv(entry, entry.get_commandline())
    cwd = entry.get_string("Path")
    return argv, cwd or None


def resolve_revision(revision):
    if re.fullmatch(r"[a-f0-9]{64}", revision) is None:
        raise ValueError("desktop_entry_changed")
    entries = catalog(desktop_entries())["entries"]
    match = next((item for item in entries if item["revision"] == revision), None)
    if match is None:
        raise ValueError("desktop_entry_changed")
    argv, cwd = resolve_selection(match["id"], revision)
    return {"argv": argv, "cwd": cwd}


def main():
    try:
        if len(sys.argv) == 1:
            result = catalog(desktop_entries())
            if len(result["entries"]) > MAX_ENTRIES:
                raise ValueError("desktop_entry_limit")
        elif len(sys.argv) == 3 and sys.argv[1] == "resolve":
            result = resolve_revision(sys.argv[2])
        else:
            return 2
        raw = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(raw) >= MAX_CATALOG_BYTES:
            raise ValueError("desktop_entry_limit")
        sys.stdout.buffer.write(raw + b"\n")
    except (ImportError, ValueError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
