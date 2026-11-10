"""B2 immutable v3 bytes and source closure; never spawn or recover a process.

The builder CLI owns compilers. C2 owns live profile/lease/bus/process recovery.
This module deliberately separates immutable validation from current-package
inspection, so package removal does not invalidate an old owner's Recover.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from types import MappingProxyType

from linux_desktop_v3_contract import MAX_RECORD_BYTES, Manifest, Pins, REVISION, canonical_json, hexadecimal, parse_json
import linux_real_app_inspect as inspector

INVENTORY_NAME = "linux-desktop-v3-sources.json"
MANIFEST_NAME = "desktop-bundle.json"
MANIFEST_LIMIT = 64 * 1024
SOURCE_LIMIT = 256 * 1024
FILE_LIMIT = 4 * 1024 * 1024
EXECUTABLES = frozenset({"eidolon-gtk3-desktop", "gio/libeidolon-settings.so", "linux-settings-probe"})
GENERATED = frozenset({"app-manifest.json", "eidolon-gtk3-desktop", "gio/giomodule.cache",
                       "gio/libeidolon-settings.so", "linux-settings-probe", "package-inspection.json",
                       "reviewed-package.json", "settings-backend.json"})
C_SOURCES = frozenset({"eidolon_linux_desktop_session_v3.py", "eidolon_linux_desktop_v3.py",
                       "eidolon_linux_persistent_profile.py", "linux-desktop-v3-bus.conf.in"})
_IMPORT_STATE = frozenset({"path", "modules", "meta_path", "path_hooks", "path_importer_cache", "__dict__"})
REQUIRED_SOURCES = C_SOURCES | frozenset({INVENTORY_NAME, "eidolon-linux-session.py",
    "eidolon_linux_process_owner.py", "eidolon_settings_backend.c", "gtk3-desktop-demo.c",
    "linux-settings-probe.c", "linux_desktop_v3_bundle.py", "linux_desktop_v3_contract.py",
    "linux_desktop_v3_wire.py", "linux_real_app_inspect.py", "linux_real_app_manifest.py"})


class BundleError(ValueError):
    """Public fixed reason only, never paths or package contents."""


def require(value, reason):
    if not value:
        raise BundleError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _relative(name):
    return (type(name) is str and 1 <= len(name) <= 160 and name.isascii() and
            all(c.isalnum() or c in "/-_." for c in name) and
            not name.startswith("/") and all(p not in {"", ".", ".."} for p in name.split("/")))


def inventory(raw):
    """Validate the one checked-in inventory; its paths are data, never argv."""
    value = parse_json(raw, MANIFEST_LIMIT)
    require(type(value) is dict and set(value) == {"schema", "protocol_revision", "integration", "sources", "generated"}
            and type(value["schema"]) is int and value["schema"] == 1 and value["protocol_revision"] == REVISION,
            "BUNDLE_INVENTORY_INVALID")
    integration = value["integration"]
    require(type(integration) is dict and set(integration) == {"c1_commit", "c2_commit"} and
            all(v is None or hexadecimal(v, 40) for v in integration.values()), "BUNDLE_INVENTORY_INVALID")
    sources = value["sources"]
    require(type(sources) is dict and set(sources) == REQUIRED_SOURCES and
            all(_relative(k) and "/" not in k and _relative(v) and v.startswith("scripts/") for k, v in sources.items()) and
            len(set(sources.values())) == len(sources), "BUNDLE_INVENTORY_INVALID")
    require(type(value["generated"]) is list and value["generated"] == sorted(GENERATED), "BUNDLE_INVENTORY_INVALID")
    require(sources[INVENTORY_NAME] == "scripts/" + INVENTORY_NAME, "BUNDLE_INVENTORY_INVALID")
    return value


def require_integrated(value):
    require(all(value["integration"][k] is not None for k in ("c1_commit", "c2_commit")),
            "BUNDLE_INTEGRATION_INCOMPLETE")


def _identity(value):
    return value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def open_directory(path):
    """Resolve no symlinks, including intermediate path components."""
    path = Path(path)
    require(path.is_absolute() and str(path) == os.fspath(path) and
            all(p not in {".", ".."} for p in path.parts), "BUNDLE_LOCATION_INVALID")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_regular(path, *, mode=None, uid=None, limit=FILE_LIMIT):
    """Bounded, no-follow read with opened and path identity recheck."""
    path = Path(path)
    parent = open_directory(path.parent)
    fd = None
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                (uid is None or before.st_uid == uid) and
                (mode is None or stat.S_IMODE(before.st_mode) == mode) and
                not before.st_mode & 0o022 and 0 < before.st_size <= limit, "BUNDLE_FILE_INVALID")
        data = bytearray()
        while len(data) <= limit:
            part = os.read(fd, min(65536, limit + 1 - len(data)))
            if not part:
                break
            data.extend(part)
        current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        require(len(data) == before.st_size and _identity(before) == _identity(os.fstat(fd)) == _identity(current),
                "BUNDLE_FILE_CHANGED")
        return bytes(data)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def source_status(root, raw):
    """Read-only dependency report, never product pins or an availability claim."""
    spec = inventory(raw)
    missing = sorted(name for name, path in spec["sources"].items() if not (Path(root) / path).is_file())
    complete = False
    if not missing and all(v is not None for v in spec["integration"].values()):
        source_snapshot(root, raw)
        complete = True
    return {"schema": 1, "protocol_revision": REVISION, "integration_complete": complete,
            "missing_sources": missing, "integration": dict(spec["integration"]),
            "available": False, "production_acceptance_passed": False, "quest_verified": False}


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return prefix + "." + node.attr if prefix else node.attr
    return ""


_FIXED_INSPECTOR_CALLS = frozenset(ast.dump(ast.parse(text, mode="eval").body, include_attributes=False) for text in (
    'importlib.util.spec_from_file_location("eidolon_fixed_manifest", Path(__file__).with_name("linux_real_app_manifest.py"))',
    'importlib.util.module_from_spec(_manifest_spec)',
    '_manifest_spec.loader.exec_module(_manifest)',
))


def check_python_closure(contents):
    """Inspect all pinned Python files, including imports not reached in a test.

    This is an allowlist audit of trusted source, not a Python sandbox. The
    immutable file hash comparison is independently required before loading.
    The existing inspector has one exact, fixed sibling loader edge.
    """
    modules = {PurePosixPath(name).stem: name for name in contents if name.endswith(".py")}
    if "eidolon-linux-session.py" in contents:
        modules["eidolon_linux_session"] = "eidolon-linux-session.py"
    edges = {}
    for name in sorted(set(modules.values())):
        try:
            tree = ast.parse(contents[name], filename=name)
        except (SyntaxError, UnicodeError, ValueError) as error:
            raise BundleError("BUNDLE_SOURCE_INVALID") from error
        imports = set()
        aliases = {}
        fixed_calls = set()
        allowed_dynamic_nodes = set()
        if name == "linux_real_app_inspect.py":
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and ast.dump(node, include_attributes=False) in _FIXED_INSPECTOR_CALLS:
                    allowed_dynamic_nodes.update(id(child) for child in ast.walk(node))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                require(not isinstance(node, ast.ImportFrom) or node.level == 0, "BUNDLE_IMPORT_UNPINNED")
                if isinstance(node, ast.ImportFrom):
                    require(not any(a.name == "*" for a in node.names), "BUNDLE_IMPORT_UNPINNED")
                    require(not (node.module == "sys" and any(a.name in _IMPORT_STATE for a in node.names)),
                            "BUNDLE_IMPORT_UNPINNED")
                    require(not (node.module and node.module.startswith(("importlib", "runpy"))),
                            "BUNDLE_IMPORT_UNPINNED")
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
                for module in names:
                    require(type(module) is str, "BUNDLE_IMPORT_UNPINNED")
                    top = module.split(".", 1)[0]
                    if top in modules:
                        require(module == top, "BUNDLE_IMPORT_UNPINNED")
                        imports.add(modules[top])
                    else:
                        require(top in sys.stdlib_module_names, "BUNDLE_IMPORT_UNPINNED")
                for alias in node.names:
                    if isinstance(node, ast.Import):
                        aliases[alias.asname or alias.name.split(".")[0]] = alias.name
                    else:
                        aliases[alias.asname or alias.name] = node.module + "." + alias.name
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute)):
                dotted = _call_name(node)
                head, _, tail = dotted.partition(".")
                resolved = aliases.get(head, head) + ("." + tail if tail else "")
                require(resolved not in {"__builtins__", "__import__", "eval", "exec", "compile",
                        "builtins.__import__", "builtins.eval", "builtins.exec", "builtins.compile"},
                        "BUNDLE_IMPORT_UNPINNED")
                if resolved == "importlib" or resolved.startswith("importlib.") or resolved == "runpy" or resolved.startswith("runpy."):
                    require(id(node) in allowed_dynamic_nodes, "BUNDLE_IMPORT_UNPINNED")
            if isinstance(node, ast.Attribute):
                dotted = _call_name(node)
                head, _, tail = dotted.partition(".")
                resolved = aliases.get(head, head) + ("." + tail if tail else "")
                require(resolved not in {"sys." + field for field in _IMPORT_STATE} and
                        resolved not in {"builtins.__import__", "builtins.exec", "builtins.eval", "builtins.compile"},
                        "BUNDLE_IMPORT_UNPINNED")
            if not isinstance(node, ast.Call):
                continue
            called = _call_name(node.func)
            head, _, tail = called.partition(".")
            resolved = aliases.get(head, head) + ("." + tail if tail else "")
            final = resolved.rsplit(".", 1)[-1]
            if final in {"getattr", "setattr", "delattr"} and node.args and isinstance(node.args[0], ast.Name):
                require(aliases.get(node.args[0].id, node.args[0].id) not in {"sys", "importlib", "importlib.util", "builtins"},
                        "BUNDLE_IMPORT_UNPINNED")
            dynamic = (resolved in {"__import__", "eval", "exec", "compile", "builtins.__import__", "builtins.eval",
                                    "builtins.exec", "builtins.compile"} or
                       final in {"import_module", "spec_from_file_location",
                                 "module_from_spec", "exec_module", "SourceFileLoader", "SourcelessFileLoader", "load_module",
                                 "run_path", "run_module"} or resolved.startswith("importlib."))
            if dynamic:
                shape = ast.dump(node, include_attributes=False)
                require(name == "linux_real_app_inspect.py" and shape in _FIXED_INSPECTOR_CALLS,
                        "BUNDLE_IMPORT_UNPINNED")
                fixed_calls.add(shape)
        if name == "linux_real_app_inspect.py":
            require(fixed_calls == _FIXED_INSPECTOR_CALLS and "linux_real_app_manifest.py" in contents,
                    "BUNDLE_IMPORT_UNPINNED")
            imports.add("linux_real_app_manifest.py")
        edges[name] = sorted(imports)
    return edges


def check_source_hashes(contents, expected):
    require(type(contents) is dict and type(expected) is dict and set(contents) == set(expected) and
            all(type(data) is bytes and hexadecimal(expected[name], 64) and sha(data) == expected[name]
                for name, data in contents.items()), "BUNDLE_SOURCE_CHANGED")


def source_snapshot(root, raw, *, require_complete=True):
    spec = inventory(raw)
    contents = {}
    for name, relative in spec["sources"].items():
        try:
            contents[name] = read_regular(Path(root) / relative, limit=SOURCE_LIMIT)
        except FileNotFoundError as error:
            raise BundleError("BUNDLE_SOURCE_MISSING") from error
    require(contents[INVENTORY_NAME] == raw, "BUNDLE_SOURCE_CHANGED")
    check_python_closure(contents)
    if require_complete:
        require_integrated(spec)
    return contents


def bundle_manifest(contents, spec, *, base_bundle_sha256, app_manifest_sha256):
    """Hash already captured bytes; never embed this manifest's hash/build-id."""
    require(type(contents) is dict and set(contents) == set(spec["sources"]) | GENERATED and
            all(type(data) is bytes and 0 < len(data) <= FILE_LIMIT for data in contents.values()) and
            hexadecimal(base_bundle_sha256, 64) and hexadecimal(app_manifest_sha256, 64), "BUNDLE_FILES_INVALID")
    require(sha(contents["app-manifest.json"]) == app_manifest_sha256, "BUNDLE_MANIFEST_CHANGED")
    app = Manifest.parse(contents["app-manifest.json"])
    require(app.canonical() == contents["app-manifest.json"], "BUNDLE_MANIFEST_INVALID")
    return {"schema": 1, "backend": "linux_desktop", "protocol_revision": REVISION,
            "base_bundle_sha256": base_bundle_sha256, "app_manifest_sha256": app_manifest_sha256,
            "files": {name: sha(data) for name, data in sorted(contents.items())}}


def parse_bundle_manifest(raw, spec):
    value = parse_json(raw, MANIFEST_LIMIT)
    require(type(value) is dict and set(value) == {"schema", "backend", "protocol_revision", "base_bundle_sha256", "app_manifest_sha256", "files"}
            and type(value["schema"]) is int and value["schema"] == 1 and value["backend"] == "linux_desktop" and
            value["protocol_revision"] == REVISION and hexadecimal(value["base_bundle_sha256"], 64) and
            hexadecimal(value["app_manifest_sha256"], 64) and type(value["files"]) is dict and
            set(value["files"]) == set(spec["sources"]) | GENERATED and
            all(hexadecimal(h, 64) for h in value["files"].values()) and canonical_json(value) == raw,
            "BUNDLE_MANIFEST_INVALID")
    require(value["files"]["app-manifest.json"] == value["app_manifest_sha256"], "BUNDLE_MANIFEST_CHANGED")
    return value


def immutable_directory(home, pins):
    require(type(pins) is Pins and Path(home).is_absolute(), "BUNDLE_LOCATION_INVALID")
    return Path(home) / ".local/lib/eidolon-linux-desktop-v3" / pins.build_id()


def file_limit(name, spec):
    """Use the same pre-read caps in builders, bootstrap and validation."""
    if name == MANIFEST_NAME or name == "settings-backend.json":
        return MANIFEST_LIMIT
    if name in spec["sources"]:
        return SOURCE_LIMIT
    if name == "app-manifest.json":
        return MAX_RECORD_BYTES
    if name in {"reviewed-package.json", "package-inspection.json"}:
        return inspector.MAX_JSON_BYTES
    return FILE_LIMIT


def _directory_names(directory, expected, uid):
    required_dirs = {str(PurePosixPath(name).parent) for name in expected if "/" in name}
    required_dirs.add(".")
    for relative in sorted(required_dirs):
        current = directory if relative == "." else directory / relative
        fd = open_directory(current)
        try:
            info = os.fstat(fd)
            require(info.st_uid == uid and stat.S_IMODE(info.st_mode) == 0o500, "BUNDLE_DIRECTORY_INVALID")
            want = {PurePosixPath(name).parts[0] for name in expected} if relative == "." else {
                name[len(relative) + 1:] for name in expected if name.startswith(relative + "/")}
            require(set(os.listdir(fd)) == want, "BUNDLE_FILES_INVALID")
        finally:
            os.close(fd)


def _settings_record(contents):
    data = parse_json(contents["settings-backend.json"], MANIFEST_LIMIT)
    names = {"eidolon_settings_backend.c", "linux-settings-probe.c", "gio/libeidolon-settings.so", "linux-settings-probe", "gio/giomodule.cache"}
    require(type(data) is dict and set(data) == {"schema", "kind", "settings_kind", "settings_policy", "backend_type",
            "settings_relative_path", "gio_version", "files", "production_ready"} and type(data["schema"]) is int and
            data["schema"] == 1 and data["kind"] == "linux-real-app-settings-a0" and
            data["settings_kind"] == "eidolon-keyfile-v1" and data["settings_policy"] == "mousepad-single-main-no-restore-v1" and
            data["backend_type"] == "EidolonKeyfileV1Backend" and data["settings_relative_path"] == "config/glib-2.0/settings/keyfile" and
            type(data["gio_version"]) is str and re.fullmatch(r"2\.[0-9]{1,3}\.[0-9]{1,3}", data["gio_version"]) and
            data["production_ready"] is False and
            type(data["files"]) is dict and data["files"] == {name: sha(contents[name]) for name in names},
            "BUNDLE_SETTINGS_INVALID")
    require(contents["gio/giomodule.cache"] == b"libeidolon-settings.so: gsettings-backend\n", "BUNDLE_SETTINGS_INVALID")
    return data


@dataclass(frozen=True, repr=False)
class VerifiedBundle:
    directory: Path
    pins: Pins
    manifest: Manifest
    files: MappingProxyType


def validate_bundle(home, pins, source_hashes, inventory_raw):
    """Immutable-only Recover gate. Never inspect current package or run code.

    source_hashes and inventory_raw must come from the preselected trusted APK
    source set, never a remote announcement. The SSH bootstrap checks the base
    helper/Waypipe/SHM pins separately before importing this module.
    """
    spec = inventory(inventory_raw)
    require_integrated(spec)
    require(type(source_hashes) is dict and set(source_hashes) == set(spec["sources"]) and
            all(hexadecimal(v, 64) for v in source_hashes.values()), "BUNDLE_SOURCE_INVALID")
    directory = immutable_directory(home, pins)
    expected = set(spec["sources"]) | GENERATED | {MANIFEST_NAME}
    _directory_names(directory, expected, os.getuid())
    contents = {name: read_regular(directory / name, mode=0o500 if name in EXECUTABLES else 0o400,
                                  uid=os.getuid(), limit=file_limit(name, spec))
                for name in sorted(expected)}
    raw = contents.pop(MANIFEST_NAME)
    manifest = parse_bundle_manifest(raw, spec)
    p = pins.record()
    require(sha(raw) == p["desktop_bundle_sha256"] and manifest["base_bundle_sha256"] == p["base_bundle_sha256"] and
            manifest["app_manifest_sha256"] == p["app_manifest_sha256"], "BUNDLE_PIN_MISMATCH")
    require(all(sha(contents[name]) == h for name, h in manifest["files"].items()), "BUNDLE_FILE_CHANGED")
    check_source_hashes({name: contents[name] for name in source_hashes}, source_hashes)
    require(contents[INVENTORY_NAME] == inventory_raw, "BUNDLE_SOURCE_CHANGED")
    check_python_closure(contents)
    app = Manifest.parse(contents["app-manifest.json"])
    app.matches_pins(pins)
    require(app.canonical() == contents["app-manifest.json"] and
            app.record()["settings_module_sha256"] == sha(contents["gio/libeidolon-settings.so"]) and
            p["desktop_binary_sha256"] == sha(contents["eidolon-gtk3-desktop"]), "BUNDLE_PIN_MISMATCH")
    for name in EXECUTABLES:
        inspector._check_elf(contents[name], app.record()["architecture"])
    _settings_record(contents)
    descriptor = parse_json(contents["reviewed-package.json"], inspector.MAX_JSON_BYTES)
    inspector._descriptor(descriptor)
    receipt = inspector.validate_inspection_receipt(parse_json(contents["package-inspection.json"], inspector.MAX_JSON_BYTES))
    require(receipt["inspection_passed"] is True and receipt["reviewed_descriptor_sha256"] == inspector.digest(descriptor) and
            receipt["executable_sha256"] == app.record()["executable_sha256"] and
            receipt["dependency_fingerprint"] == app.record()["dependency_fingerprint"] and
            all(descriptor[k] == app.record()[k] for k in ("package", "package_version", "architecture", "executable_sha256")),
            "BUNDLE_PACKAGE_RECORD_INVALID")
    return VerifiedBundle(directory, pins, app, MappingProxyType(contents))


def validate_launch_package(bundle):
    """C2 calls this immediately before Launch, separately from Recover."""
    require(type(bundle) is VerifiedBundle, "BUNDLE_PIN_MISMATCH")
    descriptor = parse_json(bundle.files["reviewed-package.json"], inspector.MAX_JSON_BYTES)
    record = inspector.validate_reviewed_package(descriptor)
    require(inspector.digest(record) == bundle.manifest.record()["dependency_fingerprint"], "APP_DEPENDENCY_CHANGED")
    return bundle.manifest
