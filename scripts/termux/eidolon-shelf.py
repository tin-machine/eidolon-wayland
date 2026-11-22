"""Read the paired account's launcher entries without starting a Wayland client."""
import configparser
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
import sys


def desktop_rows(roots):
    seen = set()
    for root in roots:
        applications = root / "applications"
        for path in sorted(applications.rglob("*.desktop")):
            desktop_id = "-".join(path.relative_to(applications).parts)
            if desktop_id in seen:
                continue
            seen.add(desktop_id)
            config = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                if not path.is_file() or path.stat().st_size > 65536:
                    continue
                config.read(path, encoding="utf-8")
                entry = config["Desktop Entry"]
                if entry.get("Type") != "Application" or any(
                    entry.get(key, "").lower() == "true" for key in ("Hidden", "NoDisplay")
                ):
                    continue
                name = entry.get("Name[ja]") or entry["Name"]
                if not name.strip() or len(name.encode()) > 255 or not name.isprintable():
                    continue
            except (KeyError, UnicodeError, OSError, configparser.Error):
                continue
            yield dict(id="xdg-" + hashlib.sha256(desktop_id.encode()).hexdigest()[:40],
                       label=name, icon="builtin:script", available=False,
                       error="GUI_NOT_VERIFIED", desktop_id=desktop_id)


# This is the only current GUI execution profile with an identifiable launch directory.
# Never attribute a process to an XDG entry by its executable or display name alone.
def crash_records(lines):
    reports = {}
    for line in lines:
        match = re.fullmatch(r"\s*(\d+)\.(\d{3})\s+(\d+)\s+\d+ F DEBUG\s*:\s*(.*)", line)
        if not match:
            continue
        seconds, millis, writer, message = match.groups()
        if message.startswith("*** ***"):
            reports[writer] = {"time_ms": int(seconds) * 1000 + int(millis), "frames": []}
        record = reports.get(writer)
        if record is None:
            continue
        if message.startswith("Cmdline: "):
            record["argv"] = message[9:].split()
        elif message == f"uid: {os.getuid()}":
            record["own"] = True
        elif message.startswith("signal 11 (SIGSEGV)"):
            record["signal"] = 11
        else:
            frame = re.match(r"#(\d{2}) pc ([0-9a-f]{1,16})\s+(\S+)", message)
            if frame and len(record["frames"]) < 16:
                index, pc, path = frame.groups()
                module = Path(path).name
                if module in {"chocolate-doom", "libSDL2-2.0.so", "libEGL_mesa.so", "libc.so"}:
                    record["frames"].append([index, module, pc])
    return reports.values()


def match_crash(home, apps, record):
    argv = record.get("argv", [])
    engine = home.parent / "usr/bin/chocolate-doom"
    if not (record.get("own") and record.get("signal") == 11 and record["frames"] and
            len(argv) >= 5 and argv[:2] == [str(engine), "-iwad"] and argv[3] == "-config"):
        return None
    config = Path(argv[4])
    if len(config.parents) < 2 or config.name != "default.cfg" or not config.parent.is_dir():
        return None
    launch = config.parent.name
    nonce = config.parents[1].name
    if (config.parents[1].parent != home / ".local/state/eidolon/freedoom-gui" or
            not re.fullmatch("[0-9a-f]{16}", nonce) or not re.fullmatch("[1-9][0-9]{0,18}", launch)):
        return None
    matches = []
    for app in apps:
        profile = app.get("execution_profile", {})
        if profile.get("kind") == "freedoom_phase1_software":
            root = home / ".local/share/eidolon/freedoom/0.13.0" / profile["assets_run_id"]
            if argv[2] == str(root / "assets/freedoom1.wad"):
                matches.append(app["id"])
    if len(matches) != 1:
        return None
    # A verified disassembly of this exact Termux build identifies its PLT call.
    call = False
    if any(module == "chocolate-doom" and int(pc, 16) == 0x6fb04 for _, module, pc in record["frames"]):
        if engine.is_file() and engine.stat().st_size <= 4 * 1024 * 1024:
            call = hashlib.sha256(engine.read_bytes()).hexdigest() == "abcb1d304f6c9f859d46c156990c9db214b0a3e67707170a7655844dc0c5c253"
    return dict(id=matches[0], event=nonce + "-" + launch, time_ms=record["time_ms"], frames=record["frames"], render_present=call)


def recent_crashes(home, catalog):
    if not any(app.get("execution_profile", {}).get("kind") == "freedoom_phase1_software" for app in catalog.apps):
        return []
    try:
        read = subprocess.run(["/system/bin/logcat", "-d", "-b", "crash", "-v", "epoch", "-t", "240", "DEBUG:F", "*:S"],
                              capture_output=True, timeout=1.25, check=True)
        modified = (home / catalog.path).stat().st_mtime * 1000
        results = []
        for record in crash_records(read.stdout.decode("utf-8", "replace").splitlines()):
            if record["time_ms"] >= modified:
                value = match_crash(home, catalog.apps, record)
                if value is not None:
                    results.append(value)
        return sorted(results, key=lambda row: row["time_ms"])
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def main(path, expected_hash, diagnostics=False):
    home = Path.home()
    helper = home / ".local/lib/eidolon/eidolon-session.py"
    source = helper.read_bytes()
    if hashlib.sha256(source).hexdigest() != expected_hash:
        raise ValueError("helper_version")
    scope = {"__name__": "eidolon_launcher", "__file__": str(helper)}
    exec(compile(source, str(helper), "exec"), scope)
    catalog = scope["LauncherCatalog"](home)
    try:
        catalog.reload(path)
    except (FileNotFoundError, scope["CatalogError"]) as exc:
        if not isinstance(exc, FileNotFoundError) and str(exc) != "PATH_MISSING":
            raise
    entries = [catalog.summary(app) for app in catalog.apps]
    prefix = Path(os.environ.get("PREFIX", "/data/data/com.termux/files/usr"))
    roots = [Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))]
    roots.extend(Path(item) for item in os.environ.get("XDG_DATA_DIRS", str(prefix / "share")).split(":") if item)
    entries.extend(desktop_rows(roots))
    if len(entries) > 2048:
        raise ValueError("catalog_limit")
    print(json.dumps(dict(revision=catalog.revision, builtin=catalog.path is None, entries=entries,
                          crashes=recent_crashes(home, catalog) if diagnostics else [])))


if __name__ == "__main__":
    main(bytes.fromhex(sys.argv[1]).decode(), sys.argv[2], sys.argv[3:] == ["1"])
