"""B2 build-only compiler and immutable publication path, never a GUI launcher.

This file is intentionally outside the remote runtime source closure. Public
build/print-pins fail closed until C1/C2 commit and complete sources are present.
"""
from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import stat
import sys
import tempfile

from linux_desktop_v3_contract import Manifest, Pins, REVISION, canonical_json, hexadecimal, parse_json
import linux_desktop_v3_bundle as bundle
import linux_real_app_inspect as inspector

ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "scripts" / bundle.INVENTORY_NAME


def account_home():
    bundle.require(sys.platform == "linux" and os.getuid() == os.geteuid() != 0, "BUILD_PLATFORM_UNSUPPORTED")
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    fd = bundle.open_directory(home)
    try:
        info = os.fstat(fd)
        bundle.require(info.st_uid == os.getuid() and not info.st_mode & 0o022, "BUILD_LOCATION_INVALID")
    finally:
        os.close(fd)
    return home


def _parent(home):
    path = home
    for component in (".local", "lib", "eidolon-linux-desktop-v3"):
        parent = bundle.open_directory(path)
        try:
            try:
                os.mkdir(component, 0o700, dir_fd=parent)
                os.fsync(parent)
            except FileExistsError:
                pass
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            try:
                info = os.fstat(child)
                bundle.require(info.st_uid == os.getuid() and not info.st_mode & 0o022 and
                               (component != "eidolon-linux-desktop-v3" or stat.S_IMODE(info.st_mode) == 0o700),
                               "BUILD_LOCATION_INVALID")
            finally:
                os.close(child)
        finally:
            os.close(parent)
        path /= component
    return path


def _rename_noreplace(parent_fd, source, target):
    """Linux renameat2(RENAME_NOREPLACE): even an empty existing dir is sacred."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    bundle.require(rename is not None, "BUILD_RENAME_UNAVAILABLE")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent_fd, os.fsencode(source), parent_fd, os.fsencode(target), 1) != 0:
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            raise bundle.BundleError("BUILD_ALREADY_EXISTS")
        raise bundle.BundleError("BUILD_RENAME_FAILED")


def publish_immutable(parent, build_id, contents):
    """Byte publication primitive; does not certify a product or export pins.

    The caller already validated source/package/binary inputs. Test-only byte
    publication is explicitly separate from the public complete-source gate.
    """
    parent = Path(parent)
    bundle.require(hexadecimal(build_id, 64) and type(contents) is dict and bool(contents) and
                   all(bundle._relative(name) and len(Path(name).parts) <= 2 for name in contents) and
                   all(type(data) is bytes and 0 < len(data) <= bundle.FILE_LIMIT for data in contents.values()),
                   "BUILD_INPUT_INVALID")
    parent_fd = bundle.open_directory(parent)
    stage = None
    published = False
    try:
        info = os.fstat(parent_fd)
        bundle.require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700, "BUILD_LOCATION_INVALID")
        try:
            os.stat(build_id, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise bundle.BundleError("BUILD_ALREADY_EXISTS")
        stage = Path(tempfile.mkdtemp(prefix=".v3-build-", dir=parent))
        for directory in sorted({str(Path(name).parent) for name in contents if "/" in name}):
            (stage / directory).mkdir(mode=0o700)
        for name, data in sorted(contents.items()):
            fd = os.open(stage / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fchmod(stream.fileno(), 0o500 if name in bundle.EXECUTABLES else 0o400)
                os.fsync(stream.fileno())
        directories = sorted({stage, *(stage / Path(name).parent for name in contents)}, key=lambda p: len(p.parts), reverse=True)
        for directory in directories:
            fd = bundle.open_directory(directory)
            try:
                os.fchmod(fd, 0o500)
                os.fsync(fd)
            finally:
                os.close(fd)
        # The parent itself must still be the opened, owned directory.
        current = parent.stat(follow_symlinks=False)
        bundle.require((current.st_dev, current.st_ino) == (info.st_dev, info.st_ino), "BUILD_LOCATION_CHANGED")
        _rename_noreplace(parent_fd, stage.name, build_id)
        published = True
        os.fsync(parent_fd)
        return parent / build_id
    finally:
        os.close(parent_fd)
        if stage is not None and not published and stage.exists():
            for path in [stage, *(p for p in stage.rglob("*") if p.is_dir())]:
                path.chmod(0o700)
            shutil.rmtree(stage)


def _build_tools():
    # Build-only import. Its existing Group/command drains and reaps compilers;
    # none of these modules is imported by the remote bundle validator.
    import linux_real_app_build
    return linux_real_app_build


def _compile_demo(tools, directory, source):
    directory.mkdir(mode=0o700)
    env = tools.fixed_environment(directory)
    cc, pkg = tools._tool("/usr/bin/cc"), tools._tool("/usr/bin/pkg-config")
    version = tools._command([pkg, "--modversion", "gtk+-3.0"], env)["stdout"].decode("ascii").strip()
    bundle.require(re.fullmatch(r"3\.24\.[0-9]{1,3}", version), "BUILD_GTK_UNSUPPORTED")
    flags = shlex.split(tools._command([pkg, "--cflags", "--libs", "gtk+-3.0"], env)["stdout"].decode("ascii"))
    bundle.require(flags and all(flag.startswith(("-I", "-L", "-l", "-D", "-pthread", "-Wl,")) for flag in flags),
                   "BUILD_FLAGS_INVALID")
    path = directory / "gtk3-desktop-demo.c"
    tools._tools.exclusive_file(path, source, 0o600)
    binary = directory / "eidolon-gtk3-desktop"
    tools._command([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2", str(path), "-o", str(binary), *flags], env)
    binary.chmod(0o500)
    data = bundle.read_regular(binary, mode=0o500, uid=os.getuid())
    bundle.require(64 <= len(data) and data[:6] == b"\x7fELF\x02\x01", "BUILD_BINARY_INVALID")
    return data


def _manifest(descriptor, inspection, settings_module):
    return Manifest.parse(canonical_json({
        "schema": 1, "app_id": "mousepad-editor-v1", "package": "mousepad",
        "package_version": descriptor["package_version"], "architecture": descriptor["architecture"],
        "executable": "/usr/bin/mousepad", "executable_sha256": descriptor["executable_sha256"],
        "dependency_fingerprint": inspection["dependency_fingerprint"], "settings_kind": "eidolon-keyfile-v1",
        "settings_policy": "mousepad-single-main-no-restore-v1", "settings_module_sha256": bundle.sha(settings_module),
        "settings_relative_path": "config/glib-2.0/settings/keyfile", "window_app_ids": ["mousepad"],
        "launch_policy": "mousepad-private-bus-v1", "state_policy": "persistent-keyfile-locked-v1"}))


def _pins(manifest, base_hashes):
    return Pins.parse(canonical_json({"schema": 2, "backend": "linux_desktop", "protocol_revision": REVISION,
        "base_bundle_sha256": manifest["base_bundle_sha256"], "desktop_bundle_sha256": bundle.sha(canonical_json(manifest)),
        "waypipe_sha256": base_hashes["waypipe"], "shm_fixture_sha256": base_hashes["eidolon-shm-fixture"],
        "desktop_binary_sha256": manifest["files"]["eidolon-gtk3-desktop"],
        "app_manifest_sha256": manifest["app_manifest_sha256"]}))


def build(reviewed_package):
    raw = bundle.read_regular(INVENTORY_PATH, limit=bundle.MANIFEST_LIMIT)
    spec = bundle.inventory(raw)
    sources = bundle.source_snapshot(ROOT, raw)  # C2 absent => no compiler, write or pins.
    home = account_home()
    descriptor = inspector.decode_record(bundle.read_regular(reviewed_package, limit=inspector.MAX_JSON_BYTES))
    inspection = inspector.validate_inspection_receipt(inspector.inspect_host(descriptor))
    bundle.require(inspection["inspection_passed"], inspection["reason"])
    tools = _build_tools()
    _, base_hashes, base_hash = tools._tools.helper.bundle(home)
    bundle.require(base_hashes["eidolon-linux-session.py"] == bundle.sha(sources["eidolon-linux-session.py"]),
                   "BUNDLE_SOURCE_CHANGED")
    parent = _parent(home)
    with tempfile.TemporaryDirectory(prefix=".v3-compile-", dir=parent) as temporary:
        scratch = Path(temporary)
        settings = scratch / "settings"
        tools.build_settings(settings)
        contents = dict(sources)
        for name in ("gio/libeidolon-settings.so", "gio/giomodule.cache", "linux-settings-probe", "settings-backend.json"):
            contents[name] = bundle.read_regular(settings / name)
        contents["eidolon-gtk3-desktop"] = _compile_demo(tools, scratch / "demo", sources["gtk3-desktop-demo.c"])
        app = _manifest(descriptor, inspection, contents["gio/libeidolon-settings.so"])
        contents.update({"app-manifest.json": app.canonical(), "reviewed-package.json": canonical_json(descriptor),
                         "package-inspection.json": canonical_json(inspection)})
        # Recheck the same bytes and dependencies after compilers have completed.
        bundle.require(bundle.source_snapshot(ROOT, raw) == sources and tools._tools.helper.bundle(home)[2] == base_hash and
                       inspector.inspect_host(descriptor) == inspection, "BUILD_INPUT_CHANGED")
        bundle._settings_record(contents)
        manifest = bundle.bundle_manifest(contents, spec, base_bundle_sha256=base_hash, app_manifest_sha256=app.sha256())
        selected = _pins(manifest, base_hashes)
        publish_immutable(parent, selected.build_id(), {**contents, bundle.MANIFEST_NAME: canonical_json(manifest)})
    verified = bundle.validate_bundle(home, selected, {name: bundle.sha(data) for name, data in sources.items()}, raw)
    bundle.validate_launch_package(verified)
    return {"schema": 1, "status": "built", "application_executed": False, "build_id": selected.build_id(),
            "pins": selected.record(), "available": False, "production_acceptance_passed": False, "quest_verified": False}


def print_pins(build_id):
    """Read-only static export. It never runs Mousepad, --version or a probe."""
    bundle.require(hexadecimal(build_id, 64), "BUILD_ID_INVALID")
    raw = bundle.read_regular(INVENTORY_PATH, limit=bundle.MANIFEST_LIMIT)
    spec = bundle.inventory(raw)
    sources = bundle.source_snapshot(ROOT, raw)
    home = account_home()
    tools = _build_tools()
    _, base_hashes, base_hash = tools._tools.helper.bundle(home)
    directory = home / ".local/lib/eidolon-linux-desktop-v3" / build_id
    manifest = bundle.parse_bundle_manifest(bundle.read_regular(directory / bundle.MANIFEST_NAME,
        mode=0o400, uid=os.getuid(), limit=bundle.MANIFEST_LIMIT), spec)
    bundle.require(manifest["base_bundle_sha256"] == base_hash, "BUNDLE_PIN_MISMATCH")
    selected = _pins(manifest, base_hashes)
    bundle.require(selected.build_id() == build_id, "BUILD_ID_INVALID")
    verified = bundle.validate_bundle(home, selected, {name: bundle.sha(data) for name, data in sources.items()}, raw)
    bundle.validate_launch_package(verified)
    bundle.require(bundle.source_snapshot(ROOT, raw) == sources, "BUILD_INPUT_CHANGED")
    return selected.record()
