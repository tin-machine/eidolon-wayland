"""Build only the fixed L7a A0 settings module/probe, never Mousepad or a bundle.

The caller supplies a new private artifact directory. Compiler inputs are fixed
repository files and pkg-config's system GIO flags. No installation, application
launch, inherited compiler flags, or v2 source mutation is performed here.
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import time

ROOT = Path(__file__).resolve().parents[1]
_loader = importlib.machinery.SourceFileLoader("real_app_fixed_build_tools", str(ROOT / "scripts/build-linux-gtk-bundle"))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
_tools = importlib.util.module_from_spec(_spec)
_loader.exec_module(_tools)

SOURCES = {
    "eidolon_settings_backend.c": ROOT / "scripts/linux/eidolon_settings_backend.c",
    "linux-settings-probe.c": ROOT / "scripts/fixtures/linux-settings-probe.c",
}
MODULE_NAME = "libeidolon-settings.so"
PROBE_NAME = "linux-settings-probe"
BACKEND_NAME = "eidolon-keyfile-v1"


class BuildError(ValueError):
    pass


def require(value, reason):
    if not value:
        raise BuildError(reason)


def fixed_environment(home):
    return {"PATH": "/usr/bin:/bin", "HOME": str(home), "TMPDIR": str(home),
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


def run_bounded(argv, environment, *, seconds=30, stdout_limit=16384, total_limit=1024 * 1024, build_log=None):
    """Drain both pipes fairly, retain bounded stdout, and reap the actual group.

    Stderr is counted only for runtime probes. An optional log is reserved for
    compiler output from the two fixed sources, never application output.
    Nonzero exits are returned so negative probes can be checked explicitly.
    """
    group = _tools.helper.Group([str(value) for value in argv], environment)
    selector = selectors.DefaultSelector()
    output = bytearray()
    counts = {"stdout": 0, "stderr": 0}
    status = None
    try:
        for name in counts:
            pipe = getattr(group.child, name)
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        deadline = time.monotonic() + seconds
        while selector.get_map() or not group.exited():
            require(time.monotonic() < deadline, "APP_CHECK_TIMEOUT")
            for key, _ in selector.select(.02):
                data = os.read(key.fd, 16384)
                if not data:
                    selector.unregister(key.fd)
                    continue
                counts[key.data] += len(data)
                require(sum(counts.values()) <= total_limit, "APP_CHECK_OUTPUT_LIMIT")
                if build_log is not None:
                    build_log.write(data)
                if key.data == "stdout":
                    require(len(output) + len(data) <= stdout_limit, "APP_CHECK_OUTPUT_LIMIT")
                    output.extend(data)
        info = os.waitid(os.P_PID, group.child.pid, os.WEXITED | os.WNOWAIT)
        status = info.si_status if info.si_code == os.CLD_EXITED else -info.si_status
    finally:
        reclaimed = group.finish()
        selector.close()
        group.child.stdout.close()
        group.child.stderr.close()
        require(reclaimed, "APP_CHECK_CLEANUP_PENDING")
    return {"stdout": bytes(output), "returncode": status, "output_bytes": counts, "group_reaped": True}


def _command(argv, env, *, log=None, seconds=30):
    result = run_bounded(argv, env, seconds=seconds, build_log=log)
    require(result["returncode"] == 0, "APP_SETTINGS_BUILD_FAILED")
    return result


def _tool(path):
    try:
        return _tools.trusted_tool(path)
    except (ValueError, OSError) as error:
        raise BuildError("APP_BUILD_DEPENDENCY_UNAVAILABLE") from error


def build_settings(directory):
    directory = Path(directory)
    require(directory.is_absolute() and directory.resolve() == directory, "APP_BUILD_LOCATION_INVALID")
    directory.mkdir(mode=0o700)
    (directory / "gio").mkdir(mode=0o700)
    env = fixed_environment(directory)
    cc, pkg = _tool("/usr/bin/cc"), _tool("/usr/bin/pkg-config")
    query = shutil.which("gio-querymodules", path=env["PATH"])
    require(query is not None, "APP_BUILD_DEPENDENCY_UNAVAILABLE")
    query = _tool(query)
    inputs = {name: path.read_bytes() for name, path in SOURCES.items()}
    require(all(0 < len(data) <= 65536 for data in inputs.values()), "APP_BUILD_SOURCE_INVALID")
    _command([pkg, "--atleast-version=2.80", "gio-2.0"], env)
    versions = _command([pkg, "--modversion", "gio-2.0", "gmodule-2.0"], env)["stdout"].decode("ascii").splitlines()
    require(len(versions) == 2 and len(set(versions)) == 1 and re.fullmatch(r"2\.[0-9]{1,3}\.[0-9]{1,3}", versions[0]),
            "APP_SETTINGS_GLIB_UNSUPPORTED")
    flags = shlex.split(_command([pkg, "--cflags", "--libs", "gio-2.0", "gmodule-2.0"], env)["stdout"].decode("ascii"))
    require(bool(flags) and all(flag.startswith(("-I", "-L", "-l", "-D", "-pthread", "-Wl,")) for flag in flags),
            "APP_SETTINGS_FLAGS_INVALID")
    for name, data in inputs.items():
        _tools.exclusive_file(directory / name, data, 0o600)
    module = directory / "gio" / MODULE_NAME
    probe = directory / PROBE_NAME
    with (directory / "build.log").open("xb") as log:
        os.chmod(directory / "build.log", 0o600)
        common = [cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2", "-Wl,-z,relro,-z,now"]
        module_result = _command([*common, "-fPIC", "-shared", "-Wl,-z,defs", str(directory / "eidolon_settings_backend.c"),
                                  "-o", str(module), *flags], env, log=log)
        probe_result = _command([*common, str(directory / "linux-settings-probe.c"), "-o", str(probe), *flags], env, log=log)
        _command([query, str(directory / "gio")], env, log=log)
    module.chmod(0o500)
    probe.chmod(0o500)
    cache = directory / "gio/giomodule.cache"
    cache.chmod(0o400)
    require(cache.read_text() == MODULE_NAME + ": gsettings-backend\n", "APP_SETTINGS_MODULE_REGISTRATION_INVALID")
    for binary in (module, probe):
        data = binary.read_bytes()
        require(64 <= len(data) <= 4 * 1024 * 1024 and data[:4] == b"\x7fELF", "APP_SETTINGS_BINARY_INVALID")
    require(all(path.read_bytes() == inputs[name] for name, path in SOURCES.items()), "APP_BUILD_INPUT_CHANGED")
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in inputs.items()}
    hashes.update({"gio/" + MODULE_NAME: hashlib.sha256(module.read_bytes()).hexdigest(),
                   PROBE_NAME: hashlib.sha256(probe.read_bytes()).hexdigest(),
                   "gio/giomodule.cache": hashlib.sha256(cache.read_bytes()).hexdigest()})
    for name in inputs:
        (directory / name).chmod(0o400)
    manifest = {"schema": 1, "kind": "linux-real-app-settings-a0", "settings_kind": BACKEND_NAME,
                "settings_policy": "mousepad-single-main-no-restore-v1", "backend_type": "EidolonKeyfileV1Backend",
                "settings_relative_path": "config/glib-2.0/settings/keyfile", "gio_version": versions[0],
                "files": hashes, "production_ready": False}
    _tools.exclusive_file(directory / "settings-backend.json", json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n", 0o400)
    result = {"schema": 1, "stage": "settings-build", "status": "built", "application_executed": False,
              "kind": manifest["kind"], "gio_version": versions[0], "files": hashes, "compiler_reaped": True,
              "production_ready": False,
              "compiler_output_bytes": {stream: module_result["output_bytes"][stream] + probe_result["output_bytes"][stream]
                                        for stream in ("stdout", "stderr")}}
    _tools.exclusive_file(directory / "build.json", json.dumps(result, sort_keys=True, separators=(",", ":")).encode() + b"\n", 0o600)
    return result
