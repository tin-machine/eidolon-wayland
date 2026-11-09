# Embedded by the Rust SSH owner. Values above this source are typed/captured
# locally; the remote cannot choose the protocol, directory, helper, or argv.
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import pwd
import stat
import sys


def require(ok):
    if not ok:
        raise SystemExit("DESKTOP_V3_BOOTSTRAP_INVALID")


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result)
        result[key] = value
    return result


def forbidden(_):
    raise SystemExit("DESKTOP_V3_BOOTSTRAP_INVALID")


def decode(raw):
    return json.loads(raw, object_pairs_hook=pairs, parse_float=forbidden, parse_constant=forbidden)


def opened_directory(path):
    require(path.is_absolute() and all(part not in (".", "..") for part in path.parts))
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


def directory(path):
    fd = opened_directory(path)
    try:
        info = os.fstat(fd)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == expected_uid)
        require(not info.st_mode & 0o022)
    finally:
        os.close(fd)


def identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid,
            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def read(path, limit=4194304, mode=0o400):
    parent = opened_directory(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_uid == expected_uid)
            require(before.st_nlink == 1)
            require(stat.S_IMODE(before.st_mode) == mode if mode is not None
                    else bool(before.st_mode & 0o100) and not before.st_mode & 0o022)
            require(0 < before.st_size <= limit)
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            require(len(raw) == before.st_size and identity(before) == identity(after) == identity(current))
            return raw
    finally:
        os.close(parent)


require(os.getuid() == os.geteuid() == expected_uid and expected_uid != 0)
home = Path(pwd.getpwuid(expected_uid).pw_dir)
require(home.is_absolute() and home.resolve() == home)
for path in (home, home / ".local", home / ".local/lib"):
    directory(path)
base = home / ".local/lib/eidolon-linux"
directory(base)
require(stat.S_IMODE(base.stat().st_mode) == 0o700)
base_bytes = read(base / "bundle.json", 16384, mode=0o600)
require(hashlib.sha256(base_bytes).hexdigest() == pins["base_bundle_sha256"])
base_manifest = decode(base_bytes)
require(set(base_manifest) == {"schema", "backend", "revision", "files"})
require(type(base_manifest["schema"]) is int and base_manifest["schema"] == 1)
require(base_manifest["backend"] == "linux_posix" and base_manifest["revision"] == "eidolon-linux-shm-v1")
base_expected = {"eidolon-linux-session.py": source_hashes["eidolon-linux-session.py"],
                 "waypipe": pins["waypipe_sha256"], "eidolon-shm-fixture": pins["shm_fixture_sha256"]}
require(base_manifest["files"] == base_expected)
for name, expected in base_expected.items():
    raw = read(base / name, 67108864, mode=0o600 if name.endswith(".py") else None)
    require(hashlib.sha256(raw).hexdigest() == expected)
root = home / ".local/lib/eidolon-linux-desktop-v3"
directory(root)
require(stat.S_IMODE(root.stat().st_mode) == 0o700)
bundle = root / build_id
directory(bundle)
require(stat.S_IMODE(bundle.stat().st_mode) == 0o500)
require(set(inventory) == {"schema", "protocol_revision", "integration", "sources", "generated"})
require(type(inventory["schema"]) is int and inventory["schema"] == 1)
require(inventory["protocol_revision"] == "eidolon-linux-desktop-v3")
require(set(inventory["integration"]) == {"c1_commit", "c2_commit"})
require(all(type(sha) is str and len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)
            for sha in inventory["integration"].values()))
manifest_bytes = read(bundle / "desktop-bundle.json", 65536)
require(hashlib.sha256(manifest_bytes).hexdigest() == pins["desktop_bundle_sha256"])
manifest = decode(manifest_bytes)
require(set(manifest) == {"schema", "backend", "protocol_revision", "base_bundle_sha256", "app_manifest_sha256", "files"})
require(type(manifest["schema"]) is int and manifest["schema"] == 1)
require(manifest["backend"] == "linux_desktop" and manifest["protocol_revision"] == "eidolon-linux-desktop-v3")
require(manifest["base_bundle_sha256"] == pins["base_bundle_sha256"])
require(manifest["app_manifest_sha256"] == pins["app_manifest_sha256"])
require(set(manifest["files"]) == set(inventory["sources"]) | set(inventory["generated"]))
require(set(source_hashes) == set(inventory["sources"]))
expected_files = set(manifest["files"]) | {"desktop-bundle.json"}
expected_dirs = set()
for name in expected_files:
    require(type(name) is str and name and not name.startswith("/"))
    require(all(part not in ("", ".", "..") for part in name.split("/")))
    parent = Path(name).parent
    while parent != Path("."):
        expected_dirs.add(parent.as_posix())
        parent = parent.parent
seen_files, seen_dirs = set(), set()
for current, dirs, names in os.walk(bundle, followlinks=False):
    current = Path(current)
    directory(current)
    require(stat.S_IMODE(current.stat().st_mode) == 0o500)
    for name in dirs:
        path = current / name
        directory(path)
        seen_dirs.add(path.relative_to(bundle).as_posix())
    for name in names:
        seen_files.add((current / name).relative_to(bundle).as_posix())
require(seen_files == expected_files and seen_dirs == expected_dirs)
snapshot = {}
for name, expected in manifest["files"].items():
    require(type(expected) is str and len(expected) == 64 and all(c in "0123456789abcdef" for c in expected))
    limit = (65536 if name == "settings-backend.json" else 262144 if name in source_hashes
             else 16384 if name == "app-manifest.json"
             else 131072 if name in {"reviewed-package.json", "package-inspection.json"} else 4194304)
    raw = read(bundle / name, limit,
               mode=0o500 if name in {"eidolon-gtk3-desktop", "gio/libeidolon-settings.so", "linux-settings-probe"} else 0o400)
    require(hashlib.sha256(raw).hexdigest() == expected)
    if name in source_hashes:
        require(expected == source_hashes[name])
    snapshot[name] = raw
require(snapshot["linux-desktop-v3-sources.json"] == inventory_raw.encode("utf-8"))
require(hashlib.sha256(snapshot["app-manifest.json"]).hexdigest() == pins["app_manifest_sha256"])

# All bundle source is imported from the verified snapshot, never sys.path or
# a second filesystem read. Fixed legacy inspector aliases also use these bytes.
modules = {Path(name).stem: name for name in source_hashes if name.endswith(".py") and "/" not in name}
require(len(modules) == sum(name.endswith(".py") and "/" not in name for name in source_hashes))
require("eidolon_linux_session" not in modules)
modules["eidolon_linux_session"] = "eidolon-linux-session.py"


class PinnedLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in modules:
            return importlib.util.spec_from_loader(fullname, self, origin=str(bundle / modules[fullname]))
        if fullname.split(".")[0] not in sys.stdlib_module_names:
            raise ImportError("DESKTOP_V3_UNPINNED_IMPORT")
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        name = modules.get(module.__name__)
        if name is None:
            name = Path(module.__spec__.origin).relative_to(bundle).as_posix()
        require(name in snapshot and name in source_hashes and name.endswith(".py"))
        module.__file__ = str(bundle / name)
        exec(compile(snapshot[name], module.__file__, "exec"), module.__dict__)


loader = PinnedLoader()
sys.meta_path.insert(0, loader)


def fixed_spec(name, location, *args, **kwargs):
    # The inherited A0 inspector loads its fixed manifest sibling under an alias.
    # Any other explicit source path must still be a verified bundle source.
    require(not args and not kwargs)
    try:
        relative = Path(location).relative_to(bundle).as_posix()
    except ValueError:
        raise ImportError("DESKTOP_V3_UNPINNED_IMPORT") from None
    require(relative in source_hashes and relative.endswith(".py"))
    return importlib.util.spec_from_loader(name, loader, origin=str(bundle / relative))


importlib.util.spec_from_file_location = fixed_spec
import linux_desktop_v3_contract as contract
import linux_desktop_v3_bundle as validator
captured = contract.Pins.parse(contract.canonical_json(pins))
validator.validate_bundle(home, captured, source_hashes, inventory_raw.encode("utf-8"))
import eidolon_linux_desktop_session_v3 as helper
raise SystemExit(helper.main(argv))
