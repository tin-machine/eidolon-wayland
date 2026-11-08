"""Read-only L7a package inspection and pre-spawn recovery-data protection.

No application, package manager, shell, help/version command or installer is run.
A successful static inspection is a candidate for the explicit A0 runtime check,
never an available catalog entry. A reviewed descriptor must bind an installed
package to a root-owned build evidence record. Unknown distro builds stay unknown.

The optional system_root argument is an API seam for synthetic filesystem tests;
it is deliberately unavailable on the operator CLI. The future helper must hold
its profile flock across guard_recovery_state() and spawn. This module does not
establish process ownership or authorize Recover, which must remain independent
of the current application package.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import struct
import sys
import xml.etree.ElementTree as ET

APP_ID = "mousepad-editor-v1"
UPSTREAM_VERSION = "0.6.3"
UPSTREAM_COMMIT = "65fbfe0459259f820fcc8b9c4dd454228304842a"
EXECUTABLE = "/usr/bin/mousepad"
SCHEMA_PATH = "/usr/share/glib-2.0/schemas/org.xfce.mousepad.gschema.xml"
COMPILED_SCHEMA_PATH = "/usr/share/glib-2.0/schemas/gschemas.compiled"
BUILD_EVIDENCE_PATH = "/usr/share/doc/mousepad/eidolon-build-evidence.json"
BUILD_MAKEFILE_PATH = "/usr/share/doc/mousepad/eidolon-mousepad-Makefile"
SYSTEM_SETTINGS_DIRECTORY = "/etc/glib-2.0/settings"
REQUIRED_WRITABLE_KEYS = frozenset({
    b"/org/xfce/mousepad/preferences/view/show-line-numbers",
    b"/org/xfce/mousepad/preferences/view/font-name",
})
SETTINGS_RELATIVE_PATH = "config/glib-2.0/settings/keyfile"
SESSION_GROUP = "org/xfce/mousepad/state/application"
SESSION_KEY = "session"
RECOVERY_DIRECTORY = "data/Mousepad"
SETTINGS_KIND = "eidolon-keyfile-v1"
SETTINGS_POLICY = "mousepad-single-main-no-restore-v1"
MAX_JSON_BYTES = 128 * 1024
MAX_KEYFILE_BYTES = 128 * 1024
MAX_SYSTEM_BYTES = 128 * 1024 * 1024
MAX_STATUS_BYTES = 32 * 1024 * 1024
MAX_RECOVERY_ENTRIES = 64
MAX_STATE_ENTRIES = 80
MAX_ACTIVE_SESSIONS = 8
MAX_RECEIPT_BYTES = 64 * 1024
MAX_DEPENDENCIES = 128
SYSTEM_OWNER_UID = 0
HASH = re.compile(r"[0-9a-f]{64}\Z")
PACKAGE_NAME = re.compile(r"[a-z0-9][a-z0-9+.-]{0,127}\Z")
# These bytes were reviewed at the immutable mousepad-0.6.3 upstream tag.
# https://github.com/xfce-mirror/mousepad/tree/mousepad-0.6.3
SOURCE_HASHES = {
    "configure.ac": "76b1ad228bf153aa88cba0a11d8c8225432ae9bec39baa6a010df21fee33146e",
    "mousepad/mousepad-settings-store.c": "7c7f2597ebf139af1c03b9af2c29299bbf437ce8a7d564c51cc02c84be961fbe",
    "mousepad/mousepad-history.c": "c7f7dfa1ea20ca5f08fd8282c4db31d33788b1e1e1594abd8a1058caee907a3a",
    "mousepad/org.xfce.mousepad.gschema.xml": "0dd0a59b84d965ad581cefe2a8205ea94a39d078f5015a086fee4d3eeb3effd5",
}
REQUIRED_ROLES = frozenset({"gtk", "glib", "gtksourceview", "gio", "dbus_daemon", "dbus_run_session", "schema_xml", "schema_compiled"})
DEPENDENCY_ROLES = REQUIRED_ROLES | {"gio_module", "dependency"}
DESCRIPTOR_FIELDS = frozenset({"schema", "app_id", "package", "package_version", "architecture", "upstream_version", "executable_sha256", "source_evidence_sha256", "dependencies"})
DEPENDENCY_FIELDS = frozenset({"role", "path", "sha256", "package", "version", "architecture"})
EVIDENCE_FIELDS = frozenset({"schema", "package", "version", "architecture", "upstream_version", "settings_backend", "executable_sha256", "makefile_sha256", "source_sha256", "source_commit"})
MANIFEST_FIELDS = frozenset({"schema", "app_id", "package", "package_version", "architecture", "executable", "executable_sha256", "dependency_fingerprint", "settings_kind", "settings_policy", "settings_module_sha256", "settings_relative_path", "window_app_ids", "launch_policy", "state_policy"})
REASONS = frozenset({"NONE", "APP_NOT_INSTALLED", "APP_PACKAGE_UNSUPPORTED", "APP_DEPENDENCY_CHANGED", "APP_BACKEND_UNSUPPORTED", "APP_SETTINGS_UNVERIFIED", "APP_STATE_INVALID", "APP_STATE_RECOVERY_REQUIRED", "STATE_LIMIT"})


class InspectionError(ValueError):
    """A fixed public reason, with no private path or data in its text."""


def require(condition, reason):
    if not condition:
        raise InspectionError(reason)


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "APP_PACKAGE_UNSUPPORTED")
        result[key] = value
    return result


def decode_record(data, reason="APP_PACKAGE_UNSUPPORTED"):
    try:
        require(0 < len(data) <= MAX_JSON_BYTES, reason)
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicates,
                           parse_constant=lambda _: (_ for _ in ()).throw(InspectionError(reason)))
        require(type(value) is dict, reason)
        return value
    except (ValueError, UnicodeError, RecursionError) as error:
        raise InspectionError(reason) from error


def _ascii(value):
    return type(value) is str and 1 <= len(value) <= 128 and all(33 <= ord(c) <= 126 for c in value)


def _hash(value):
    return type(value) is str and HASH.fullmatch(value) is not None


def _path_parts(path):
    require(type(path) is str and path.startswith("/") and "\x00" not in path and len(path) <= 4096,
            "APP_PACKAGE_UNSUPPORTED")
    parts = path.split("/")[1:]
    require(parts and all(p not in {"", ".", ".."} for p in parts), "APP_PACKAGE_UNSUPPORTED")
    return parts


def _identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _open_absolute_directory(path):
    """Open every component without following a symlink, including ancestors."""
    path = os.fspath(path)
    parts = _path_parts(path) if path != "/" else []
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for part in parts:
            new_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = new_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _directory_info(fd, uid, private, reason):
    info = os.fstat(fd)
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == uid and
            (stat.S_IMODE(info.st_mode) == 0o700 if private else not info.st_mode & 0o022), reason)
    return info


def _read_file_at(directory_fd, name, *, uid, limit, private, executable=False, allow_empty=False, with_identity=False, reason):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory_fd)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == uid and before.st_nlink == 1 and
                (stat.S_IMODE(before.st_mode) == 0o600 if private else not before.st_mode & 0o022) and
                (not executable or before.st_mode & 0o111) and
                (0 if allow_empty else 1) <= before.st_size <= limit, reason)
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(fd, min(64 * 1024, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(fd)
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        require(len(data) == before.st_size and _identity(before) == _identity(after) == _identity(current), reason)
        return (bytes(data), _identity(before)) if with_identity else bytes(data)
    finally:
        os.close(fd)


class SystemFiles:
    """Root-owned, non-writable system files; no subprocess or loader calls."""
    def __init__(self, root=Path("/")):
        self.root = Path(root)

    def read(self, path, limit=MAX_SYSTEM_BYTES, *, executable=False, allow_empty=False):
        parts = _path_parts(path)
        fd = _open_absolute_directory(self.root)
        try:
            _directory_info(fd, SYSTEM_OWNER_UID, False, "APP_DEPENDENCY_CHANGED")
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                os.close(fd)
                fd = child
                _directory_info(fd, SYSTEM_OWNER_UID, False, "APP_DEPENDENCY_CHANGED")
            return _read_file_at(fd, parts[-1], uid=SYSTEM_OWNER_UID, limit=limit, private=False,
                                 executable=executable, allow_empty=allow_empty, reason="APP_DEPENDENCY_CHANGED")
        finally:
            os.close(fd)


def _descriptor(value):
    require(type(value) is dict and set(value) == DESCRIPTOR_FIELDS and type(value["schema"]) is int and
            value["schema"] == 1 and value["app_id"] == APP_ID and value["package"] == "mousepad" and
            value["upstream_version"] == UPSTREAM_VERSION and _ascii(value["package_version"]) and
            _ascii(value["architecture"]) and _hash(value["executable_sha256"]) and
            _hash(value["source_evidence_sha256"]), "APP_PACKAGE_UNSUPPORTED")
    deps = value["dependencies"]
    require(type(deps) is list and len(REQUIRED_ROLES) <= len(deps) <= MAX_DEPENDENCIES, "APP_PACKAGE_UNSUPPORTED")
    roles, paths = [], set()
    for item in deps:
        require(type(item) is dict and set(item) == DEPENDENCY_FIELDS and type(item["role"]) is str and item["role"] in DEPENDENCY_ROLES and
                type(item["package"]) is str and PACKAGE_NAME.fullmatch(item["package"]) and
                _ascii(item["version"]) and _ascii(item["architecture"]) and _hash(item["sha256"]), "APP_PACKAGE_UNSUPPORTED")
        _path_parts(item["path"])
        require(item["path"].startswith(("/usr/", "/etc/", "/var/lib/")) and item["path"] not in paths,
                "APP_PACKAGE_UNSUPPORTED")
        roles.append(item["role"])
        paths.add(item["path"])
    require(REQUIRED_ROLES <= set(roles) and all(roles.count(role) == 1 for role in REQUIRED_ROLES), "APP_PACKAGE_UNSUPPORTED")
    fixed = {"schema_xml": SCHEMA_PATH, "schema_compiled": COMPILED_SCHEMA_PATH,
             "dbus_daemon": "/usr/bin/dbus-daemon", "dbus_run_session": "/usr/bin/dbus-run-session"}
    require(all(item["role"] not in fixed or item["path"] == fixed[item["role"]] for item in deps), "APP_PACKAGE_UNSUPPORTED")
    return value


def _deb_status(data):
    """Parse bounded dpkg status paragraphs without running dpkg-query."""
    try:
        source = data.decode("utf-8")
    except UnicodeError as error:
        raise InspectionError("APP_PACKAGE_UNSUPPORTED") from error
    records = {}
    for stanza in re.split(r"\n[ \t]*\n", source):
        fields = {}
        for line in stanza.splitlines():
            if not line or line[0].isspace():
                continue
            key, sep, value = line.partition(":")
            require(bool(sep) and key not in fields, "APP_PACKAGE_UNSUPPORTED")
            fields[key] = value.strip()
        if not fields:
            continue
        require(all(k in fields for k in ("Package", "Status", "Version", "Architecture")), "APP_PACKAGE_UNSUPPORTED")
        key = fields["Package"], fields["Architecture"]
        require(key not in records, "APP_PACKAGE_UNSUPPORTED")
        records[key] = fields
    return records


def _check_package(records, package, version, architecture):
    record = records.get((package, architecture))
    require(record is not None and record["Status"] == "install ok installed" and record["Version"] == version,
            "APP_DEPENDENCY_CHANGED")


def _package_owns(system, package, architecture, path):
    require(PACKAGE_NAME.fullmatch(package) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", architecture), "APP_PACKAGE_UNSUPPORTED")
    try:
        data = system.read(f"/var/lib/dpkg/info/{package}:{architecture}.list", MAX_STATUS_BYTES)
    except FileNotFoundError:
        data = system.read(f"/var/lib/dpkg/info/{package}.list", MAX_STATUS_BYTES)
    try:
        listed = data.decode("utf-8").split("\n")
    except UnicodeError as error:
        raise InspectionError("APP_DEPENDENCY_CHANGED") from error
    if path in listed:
        return
    # On merged-/usr Debian systems dpkg can retain the pre-merge pathname.
    # Permit only the known top-level /lib -> usr/lib alias, with the exact
    # remaining path unchanged. Never resolve a package-list SONAME symlink
    # or an arbitrary filesystem alias to establish package ownership.
    require(path.startswith("/usr/lib/") and path[len("/usr"):] in listed,
            "APP_DEPENDENCY_CHANGED")
    root_fd = _open_absolute_directory(system.root)
    try:
        _directory_info(root_fd, SYSTEM_OWNER_UID, False, "APP_DEPENDENCY_CHANGED")
        before = os.stat("lib", dir_fd=root_fd, follow_symlinks=False)
        require(stat.S_ISLNK(before.st_mode) and before.st_uid == SYSTEM_OWNER_UID and
                os.readlink("lib", dir_fd=root_fd) in {"usr/lib", "/usr/lib"} and
                _identity(before) == _identity(os.stat("lib", dir_fd=root_fd, follow_symlinks=False)),
                "APP_DEPENDENCY_CHANGED")
    finally:
        os.close(root_fd)


def _check_elf(data, architecture):
    machines = {"amd64": (2, 62), "arm64": (2, 183), "i386": (1, 3), "armhf": (1, 40)}
    require(architecture in machines and len(data) >= 64 and data[:4] == b"\x7fELF" and
            data[5] == 1 and (data[4], struct.unpack_from("<H", data, 18)[0]) == machines[architecture] and
            struct.unpack_from("<H", data, 16)[0] in {2, 3}, "APP_PACKAGE_UNSUPPORTED")


def _check_schema(data):
    try:
        require(b"<!DOCTYPE" not in data and b"<!ENTITY" not in data, "APP_SETTINGS_UNVERIFIED")
        tree = ET.fromstring(data)
        require(tree.tag == "schemalist", "APP_SETTINGS_UNVERIFIED")
        schemas = {}
        enums = {}
        for schema in tree.findall("schema"):
            require(schema.get("id") not in schemas, "APP_SETTINGS_UNVERIFIED")
            schemas[schema.get("id")] = schema
        for enum in tree.findall("enum"):
            require(enum.get("id") not in enums, "APP_SETTINGS_UNVERIFIED")
            enums[enum.get("id")] = {value.get("nick") for value in enum.findall("value")}
        expected = (("org.xfce.mousepad.state.application", "session", "as", "[]"),
                    ("org.xfce.mousepad.preferences.file", "autosave-timer", "u", "30"),
                    ("org.xfce.mousepad.preferences.file", "session-restore", "org.xfce.mousepad.SessionRestore", "'after-a-crash'"),
                    ("org.xfce.mousepad.preferences.window", "opening-mode", "org.xfce.mousepad.OpeningMode", "'tab'"))
        for schema_id, key_name, kind, default in expected:
            schema = schemas.get(schema_id)
            require(schema is not None and schema.get("path") == "/" + schema_id.replace(".", "/") + "/", "APP_SETTINGS_UNVERIFIED")
            matches = [key for key in schema.findall("key") if key.get("name") == key_name]
            require(len(matches) == 1, "APP_SETTINGS_UNVERIFIED")
            key = matches[0]
            require((key.get("enum") if "." in kind else key.get("type")) == kind and
                    key.find("default") is not None and key.find("default").text.strip() == default, "APP_SETTINGS_UNVERIFIED")
        require({"never", "after-a-crash"} <= enums.get("org.xfce.mousepad.SessionRestore", set()) and
                {"tab", "window", "mixed"} <= enums.get("org.xfce.mousepad.OpeningMode", set()), "APP_SETTINGS_UNVERIFIED")
    except (ET.ParseError, AttributeError, TypeError) as error:
        raise InspectionError("APP_SETTINGS_UNVERIFIED") from error


def _system_settings_record(system):
    """Pin the fixed global keyfile inputs; the A0 probe still proves semantics.

    GLib gkeyfilesettingsbackend.c load_system_settings() reads these two
    files even with a dedicated profile filename. Its lock parser splits on
    LF without stripping spaces; use the same exact key comparison here.
    https://github.com/GNOME/glib/blob/2.80.5/gio/gkeyfilesettingsbackend.c
    """
    result = []
    for name in ("defaults", "locks"):
        path = SYSTEM_SETTINGS_DIRECTORY + "/" + name
        try:
            data = system.read(path, MAX_KEYFILE_BYTES, allow_empty=True)
        except FileNotFoundError:
            result.append({"path": path, "sha256": None})
            continue
        require(b"\0" not in data, "APP_SETTINGS_UNVERIFIED")
        if name == "locks":
            require(not REQUIRED_WRITABLE_KEYS.intersection(data.split(b"\n")), "APP_SETTINGS_UNVERIFIED")
        result.append({"path": path, "sha256": hashlib.sha256(data).hexdigest()})
    return result


def validate_reviewed_package(descriptor, *, system_root=Path("/")):
    """Return a canonical dependency record after static checks; never launch.

    Descriptor schema is DESCRIPTOR_FIELDS; each dependencies item has exactly
    DEPENDENCY_FIELDS. Build evidence is installed at BUILD_EVIDENCE_PATH and
    generated mousepad/Makefile at BUILD_MAKEFILE_PATH, both owned by the Mousepad package.
    Neither an installed version string nor a descriptor assertion alone proves
    that GSETTINGS_BACKEND can intercept this particular executable.
    """
    descriptor = _descriptor(descriptor)
    system = SystemFiles(system_root)
    executable = system.read(EXECUTABLE, executable=True)
    require(hashlib.sha256(executable).hexdigest() == descriptor["executable_sha256"], "APP_DEPENDENCY_CHANGED")
    _check_elf(executable, descriptor["architecture"])
    records = _deb_status(system.read("/var/lib/dpkg/status", MAX_STATUS_BYTES))
    _check_package(records, "mousepad", descriptor["package_version"], descriptor["architecture"])
    for path in (EXECUTABLE, BUILD_EVIDENCE_PATH, BUILD_MAKEFILE_PATH, SCHEMA_PATH):
        _package_owns(system, "mousepad", descriptor["architecture"], path)
    evidence_data = system.read(BUILD_EVIDENCE_PATH, MAX_JSON_BYTES)
    require(hashlib.sha256(evidence_data).hexdigest() == descriptor["source_evidence_sha256"], "APP_DEPENDENCY_CHANGED")
    evidence = decode_record(evidence_data)
    require(set(evidence) == EVIDENCE_FIELDS and type(evidence["schema"]) is int and evidence["schema"] == 1 and
            evidence["package"] == "mousepad" and evidence["version"] == descriptor["package_version"] and
            evidence["architecture"] == descriptor["architecture"] and evidence["upstream_version"] == UPSTREAM_VERSION and
            evidence["source_sha256"] == SOURCE_HASHES and evidence["executable_sha256"] == descriptor["executable_sha256"] and
            evidence["source_commit"] == UPSTREAM_COMMIT and _hash(evidence["makefile_sha256"]), "APP_PACKAGE_UNSUPPORTED")
    require(evidence["settings_backend"] == "default", "APP_BACKEND_UNSUPPORTED")
    makefile = system.read(BUILD_MAKEFILE_PATH, 1024 * 1024)
    require(hashlib.sha256(makefile).hexdigest() == evidence["makefile_sha256"], "APP_DEPENDENCY_CHANGED")
    # configure uses AM_CONDITIONAL, not config.h. A false conditional leaves
    # its am__append assignment commented out in this generated Makefile.
    active_lines = b"\n".join(line for line in makefile.splitlines() if not line.lstrip().startswith(b"#"))
    require(re.search(rb"^libmousepad_la_CFLAGS[ \t]*=", active_lines, re.M) is not None and
            b"MOUSEPAD_SETTINGS_KEYFILE_BACKEND" not in active_lines, "APP_BACKEND_UNSUPPORTED")
    dependencies = []
    for item in descriptor["dependencies"]:
        _check_package(records, item["package"], item["version"], item["architecture"])
        # gschemas.compiled is generated by the GLib trigger, not a dpkg
        # payload file. Its root ownership/hash and the exact source schema
        # are checked independently; the declared GLib package must exist.
        if item["role"] != "schema_compiled":
            _package_owns(system, item["package"], item["architecture"], item["path"])
        data = system.read(item["path"], executable=item["role"] in {"dbus_daemon", "dbus_run_session"})
        require(hashlib.sha256(data).hexdigest() == item["sha256"], "APP_DEPENDENCY_CHANGED")
        if item["role"] == "schema_xml":
            require(item["sha256"] == SOURCE_HASHES["mousepad/org.xfce.mousepad.gschema.xml"], "APP_PACKAGE_UNSUPPORTED")
            _check_schema(data)
        if item["role"] == "schema_compiled":
            require(data.startswith(b"GVariant"), "APP_SETTINGS_UNVERIFIED")
        dependencies.append(dict(item))
    return {"schema": 1, "app_id": APP_ID, "package": "mousepad", "package_version": descriptor["package_version"],
            "architecture": descriptor["architecture"], "executable_sha256": descriptor["executable_sha256"],
            "source_evidence_sha256": descriptor["source_evidence_sha256"],
            "dependencies": sorted(dependencies, key=lambda item: (item["role"], item["package"], item["path"])),
            "system_keyfile_policy": _system_settings_record(system)}


def validate_manifest(value):
    """Strict schema for a manifest produced only after the separate A0 gate."""
    require(type(value) is dict and set(value) == MANIFEST_FIELDS and type(value["schema"]) is int and value["schema"] == 1 and
            value["app_id"] == APP_ID and value["package"] == "mousepad" and _ascii(value["package_version"]) and
            _ascii(value["architecture"]) and value["executable"] == EXECUTABLE and
            all(_hash(value[k]) for k in ("executable_sha256", "dependency_fingerprint", "settings_module_sha256")) and
            value["settings_kind"] == SETTINGS_KIND and value["settings_policy"] == SETTINGS_POLICY and
            value["settings_relative_path"] == SETTINGS_RELATIVE_PATH and value["window_app_ids"] == ["org.xfce.mousepad"] and
            value["launch_policy"] == "mousepad-private-bus-v1" and value["state_policy"] == "persistent-keyfile-locked-v1",
            "APP_PACKAGE_UNSUPPORTED")
    return value


def _private_optional_dir(parent_fd, name, uid):
    try:
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    try:
        _directory_info(child, uid, True, "APP_STATE_RECOVERY_REQUIRED")
        return child
    except BaseException:
        os.close(child)
        raise


def _optional_private_path(root_fd, parts, uid, snapshots=None):
    fd = os.dup(root_fd)
    walked = []
    try:
        for part in parts:
            walked.append(part)
            child = _private_optional_dir(fd, part, uid)
            if snapshots is not None:
                snapshots.append((tuple(walked), None if child is None else _identity(os.fstat(child))))
            if child is None:
                os.close(fd)
                return None
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _recheck_private_snapshots(root_fd, snapshots, uid):
    for parts, expected in snapshots:
        parent = _optional_private_path(root_fd, parts[:-1], uid)
        if parent is None:
            require(expected is None, "APP_STATE_RECOVERY_REQUIRED")
            continue
        try:
            try:
                current = _identity(os.stat(parts[-1], dir_fd=parent, follow_symlinks=False))
            except FileNotFoundError:
                current = None
            require(expected == current, "APP_STATE_RECOVERY_REQUIRED")
        finally:
            os.close(parent)


def _empty_session(data):
    """Conservative subset of GKeyFile, with the exact full GSettings path.

    The reviewed backend uses root '/' and no root group: its section is the
    key path without leading/trailing '/'. Duplicate or unclassifiable syntax
    is blocked rather than interpreted differently from GLib. Only a missing
    session key, [] or explicitly typed @as [] proves there is no saved session.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeError as error:
        raise InspectionError("APP_STATE_RECOVERY_REQUIRED") from error
    require("\x00" not in text and all(ord(c) >= 32 or c in "\n\r\t" for c in text), "APP_STATE_RECOVERY_REQUIRED")
    groups, values, group = set(), {}, None
    for line in text.split("\n"):
        line = line.strip(" \t\r")
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            require(re.fullmatch(r"\[[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\]", line) is not None and line not in groups,
                    "APP_STATE_RECOVERY_REQUIRED")
            groups.add(line)
            group = line[1:-1]
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        require(group is not None and sep and re.fullmatch(r"[A-Za-z0-9_-]+", key) is not None and (group, key) not in values,
                "APP_STATE_RECOVERY_REQUIRED")
        values[group, key] = value.strip()
    value = values.get((SESSION_GROUP, SESSION_KEY))
    return value is None or re.fullmatch(r"(?:@as\s+)?\[\s*\]", value) is not None


def guard_recovery_state(profile_root, *, uid=None):
    """Read-only, bounded pre-spawn barrier. Caller holds profile flock.

    Missing optional state is empty; a missing/untrusted root, malformed file,
    symlink, hardlink, any recovery-directory entry, or scan race is not empty.
    Documents are deliberately neither read nor changed. No filenames/content
    are returned. Re-run immediately before spawn while retaining the same lock.
    """
    uid = os.getuid() if uid is None else uid
    result = {"clear": False, "reason": "APP_STATE_RECOVERY_REQUIRED", "session_empty": False, "recovery_data_empty": False}
    root_fd = None
    try:
        root_fd = _open_absolute_directory(profile_root)
        root_info = _directory_info(root_fd, uid, True, "APP_STATE_RECOVERY_REQUIRED")
        snapshots = []
        config_fd = _optional_private_path(root_fd, ["config", "glib-2.0", "settings"], uid, snapshots)
        if config_fd is None:
            result["session_empty"] = True
        else:
            try:
                before = os.fstat(config_fd)
                try:
                    data, identity = _read_file_at(config_fd, "keyfile", uid=uid, limit=MAX_KEYFILE_BYTES,
                                                   private=True, allow_empty=True, with_identity=True,
                                                   reason="APP_STATE_RECOVERY_REQUIRED")
                    snapshots.append((("config", "glib-2.0", "settings", "keyfile"), identity))
                    result["session_empty"] = _empty_session(data)
                except FileNotFoundError:
                    snapshots.append((("config", "glib-2.0", "settings", "keyfile"), None))
                    result["session_empty"] = True
                require(_identity(before) == _identity(os.fstat(config_fd)), "APP_STATE_RECOVERY_REQUIRED")
            finally:
                os.close(config_fd)
        recovery_fd = _optional_private_path(root_fd, ["data", "Mousepad"], uid, snapshots)
        if recovery_fd is None:
            result["recovery_data_empty"] = True
        else:
            try:
                before = os.fstat(recovery_fd)
                count = 0
                with os.scandir(recovery_fd) as entries:
                    for entry in entries:
                        count += 1
                        require(count <= MAX_RECOVERY_ENTRIES, "APP_STATE_RECOVERY_REQUIRED")
                        info = entry.stat(follow_symlinks=False)
                        require(stat.S_ISREG(info.st_mode) and info.st_uid == uid and info.st_nlink == 1 and
                                stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= MAX_SYSTEM_BYTES,
                                "APP_STATE_RECOVERY_REQUIRED")
                require(_identity(before) == _identity(os.fstat(recovery_fd)), "APP_STATE_RECOVERY_REQUIRED")
                result["recovery_data_empty"] = count == 0
            finally:
                os.close(recovery_fd)
        _recheck_private_snapshots(root_fd, snapshots, uid)
        current_fd = _open_absolute_directory(profile_root)
        try:
            require(_identity(root_info) == _identity(os.fstat(root_fd)) == _identity(os.fstat(current_fd)), "APP_STATE_RECOVERY_REQUIRED")
        finally:
            os.close(current_fd)
        result["clear"] = result["session_empty"] and result["recovery_data_empty"]
        if result["clear"]:
            result["reason"] = "NONE"
    except (OSError, InspectionError, UnicodeError):
        pass
    finally:
        if root_fd is not None:
            os.close(root_fd)
    return result


def inspect_receipt_capacity(home, *, uid=None):
    """Count the existing v2 namespace without reading/deleting user records.

    Every entry consumes the frozen helper's cap, not just success receipts.
    It accepts exactly 80 existing entries, then creates a session: 81 fully
    closed cycles are possible from empty, with the 82nd refused. entries is
    a bounded lower bound when scan_complete=false, never an unbounded scan.
    A malformed or uninspectable namespace is not reported as empty/capacious.
    No ACK, garbage collection, or Recover behavior is introduced here.
    """
    uid = os.getuid() if uid is None else uid
    result = {"checked": False, "reason": "APP_STATE_INVALID", "entries": 0, "receipts": 0, "sessions": 0, "capacity_remaining": 0, "scan_complete": False}
    home_fd = state_fd = None
    try:
        home_fd = _open_absolute_directory(home)
        _directory_info(home_fd, uid, False, "APP_STATE_INVALID")
        try:
            state_fd = os.open(".eidolon-desktop", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=home_fd)
        except FileNotFoundError:
            result.update(checked=True, reason="NONE", capacity_remaining=MAX_STATE_ENTRIES + 1, scan_complete=True)
            return result
        before = _directory_info(state_fd, uid, True, "APP_STATE_INVALID")
        with os.scandir(state_fd) as entries:
            for entry in entries:
                result["entries"] += 1
                if result["entries"] > MAX_STATE_ENTRIES:
                    result.update(checked=True, reason="STATE_LIMIT")
                    return result
                info = entry.stat(follow_symlinks=False)
                require(info.st_uid == uid and not stat.S_ISLNK(info.st_mode), "APP_STATE_INVALID")
                if re.fullmatch(r"r-[0-9a-f]{16}\.json", entry.name):
                    require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1 and
                            0 < info.st_size <= MAX_RECEIPT_BYTES, "APP_STATE_INVALID")
                    result["receipts"] += 1
                elif re.fullmatch(r"s-[0-9a-f]{16}", entry.name):
                    require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700, "APP_STATE_INVALID")
                    result["sessions"] += 1
                else:
                    raise InspectionError("APP_STATE_INVALID")
        require(_identity(before) == _identity(os.fstat(state_fd)), "APP_STATE_INVALID")
        result.update(checked=True, scan_complete=True, capacity_remaining=max(0, MAX_STATE_ENTRIES + 1 - result["entries"]))
        result["reason"] = "NONE" if result["entries"] <= MAX_STATE_ENTRIES and result["sessions"] < MAX_ACTIVE_SESSIONS else "STATE_LIMIT"
    except (OSError, InspectionError):
        pass
    finally:
        if state_fd is not None:
            os.close(state_fd)
        if home_fd is not None:
            os.close(home_fd)
    return result


def inspect_host(descriptor=None, *, system_root=Path("/"), home=None, uid=None):
    """Return a privacy-limited static-inspection receipt, never availability."""
    receipt = {"schema": 1, "app_id": APP_ID, "stage": "inspection", "inspection_passed": False,
               "available": False, "runtime_verified": False, "reason": "APP_PACKAGE_UNSUPPORTED",
               "reviewed_descriptor_sha256": None, "executable_sha256": None, "dependency_fingerprint": None,
               "checks": {"executable": False, "package": False, "default_backend": False, "schema": False},
               "receipt_capacity": None, "pending": ["feasibility", "acceptance", "quest"]}
    try:
        executable = SystemFiles(system_root).read(EXECUTABLE, executable=True)
        receipt["checks"]["executable"] = True
        receipt["executable_sha256"] = hashlib.sha256(executable).hexdigest()
        if descriptor is not None:
            record = validate_reviewed_package(descriptor, system_root=system_root)
            receipt["reviewed_descriptor_sha256"] = digest(descriptor)
            receipt["dependency_fingerprint"] = digest(record)
            receipt["checks"].update(package=True, default_backend=True, schema=True)
            receipt.update(inspection_passed=True, reason="NONE")
    except FileNotFoundError:
        receipt["reason"] = "APP_NOT_INSTALLED" if not receipt["checks"]["executable"] else "APP_PACKAGE_UNSUPPORTED"
    except InspectionError as error:
        receipt["reason"] = str(error)
    except (OSError, ValueError, TypeError, KeyError):
        receipt["reason"] = "APP_DEPENDENCY_CHANGED"
    if home is not None:
        receipt["receipt_capacity"] = inspect_receipt_capacity(home, uid=uid)
        if receipt["receipt_capacity"]["reason"] != "NONE":
            receipt.update(inspection_passed=False, reason=receipt["receipt_capacity"]["reason"])
    return receipt


def validate_inspection_receipt(value):
    require(type(value) is dict and set(value) == {"schema", "app_id", "stage", "inspection_passed", "available", "runtime_verified", "reason", "reviewed_descriptor_sha256", "executable_sha256", "dependency_fingerprint", "checks", "receipt_capacity", "pending"} and
            type(value["schema"]) is int and value["schema"] == 1 and value["app_id"] == APP_ID and value["stage"] == "inspection" and
            type(value["inspection_passed"]) is bool and value["available"] is False and value["runtime_verified"] is False and
            type(value["reason"]) is str and value["reason"] in REASONS and all(value[key] is None or _hash(value[key]) for key in
                                               ("reviewed_descriptor_sha256", "executable_sha256", "dependency_fingerprint")) and
            type(value["checks"]) is dict and set(value["checks"]) == {"executable", "package", "default_backend", "schema"} and
            all(type(v) is bool for v in value["checks"].values()) and value["pending"] == ["feasibility", "acceptance", "quest"], "APP_PACKAGE_UNSUPPORTED")
    require(value["inspection_passed"] == (value["reason"] == "NONE"), "APP_PACKAGE_UNSUPPORTED")
    require(not value["inspection_passed"] or (all(value["checks"].values()) and
            all(value[key] is not None for key in ("reviewed_descriptor_sha256", "executable_sha256", "dependency_fingerprint"))), "APP_PACKAGE_UNSUPPORTED")
    capacity = value["receipt_capacity"]
    if capacity is not None:
        require(type(capacity) is dict and set(capacity) == {"checked", "reason", "entries", "receipts", "sessions", "capacity_remaining", "scan_complete"} and
                type(capacity["checked"]) is bool and type(capacity["scan_complete"]) is bool and type(capacity["reason"]) is str and
                capacity["reason"] in {"NONE", "APP_STATE_INVALID", "STATE_LIMIT"} and
                all(type(capacity[k]) is int and 0 <= capacity[k] <= MAX_STATE_ENTRIES + 1 for k in
                    ("entries", "receipts", "sessions", "capacity_remaining")), "APP_PACKAGE_UNSUPPORTED")
        require(capacity["receipts"] + capacity["sessions"] <= capacity["entries"] and
                (not value["inspection_passed"] or capacity["reason"] == "NONE"), "APP_PACKAGE_UNSUPPORTED")
        if capacity["scan_complete"]:
            require(capacity["checked"] and capacity["entries"] == capacity["receipts"] + capacity["sessions"] and
                    capacity["capacity_remaining"] == max(0, MAX_STATE_ENTRIES + 1 - capacity["entries"]),
                    "APP_PACKAGE_UNSUPPORTED")
        else:
            require(capacity["capacity_remaining"] == 0, "APP_PACKAGE_UNSUPPORTED")
        if capacity["reason"] == "NONE":
            require(capacity["checked"] and capacity["scan_complete"] and capacity["entries"] <= MAX_STATE_ENTRIES and
                    capacity["sessions"] < MAX_ACTIVE_SESSIONS, "APP_PACKAGE_UNSUPPORTED")
        if capacity["reason"] != "NONE":
            require(not value["inspection_passed"] and value["reason"] == capacity["reason"], "APP_PACKAGE_UNSUPPORTED")
    return value


def _read_operator_descriptor(path):
    path = Path(path).absolute()
    parent_fd = _open_absolute_directory(path.parent)
    try:
        return decode_record(_read_file_at(parent_fd, path.name, uid=os.getuid(), limit=MAX_JSON_BYTES,
                                          private=True, reason="APP_PACKAGE_UNSUPPORTED"))
    finally:
        os.close(parent_fd)


def write_inspection_receipt(artifact_root, value):
    """Only the explicitly requested artifact is written; never replace one."""
    validate_inspection_receipt(value)
    artifact_root = Path(artifact_root).absolute()
    parent_fd = _open_absolute_directory(artifact_root.parent)
    try:
        try:
            os.mkdir(artifact_root.name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        fd = os.open(artifact_root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    try:
        _directory_info(fd, os.getuid(), True, "APP_STATE_INVALID")
        out = os.open("inspection.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
        try:
            data = canonical_json(value) + b"\n"
            while data:
                written = os.write(out, data)
                data = data[written:]
            os.fsync(out)
        finally:
            os.close(out)
        os.fsync(fd)
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--reviewed-package", type=Path)
    args = parser.parse_args(argv)
    try:
        descriptor = _read_operator_descriptor(args.reviewed_package) if args.reviewed_package else None
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        receipt = inspect_host(descriptor, home=home)
        write_inspection_receipt(args.artifact_root, receipt)
    except (OSError, InspectionError, KeyError):
        print(json.dumps({"stage": "inspection", "available": False, "reason": "APP_STATE_INVALID"}, sort_keys=True))
        return 2
    print(canonical_json(receipt).decode("utf-8"))
    return 0 if receipt["inspection_passed"] else 2
