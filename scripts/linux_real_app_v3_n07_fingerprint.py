"""Bounded N07-B3 environment observations, never golden approval.

The optional disposable-fixture policy is installed *before* a C2 session.
The observer then only reads an exact Mousepad process's allowlisted environment,
mapped fonts and mapped GTK/Pango/Cairo libraries. No GTK module is injected,
no widget/action API is called and no C1 setting is changed. Missing observations
    remain missing: intended theme inputs alone are not a direct observation of
    the effective GtkSettings property. Waypipe's upstream commit is tied to the
    existing archive by a separate, documented path/mode/payload comparison.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import select
import stat
import subprocess
import sys
import time

import linux_real_app_v3_n07_pixel as pixel

ROOT = Path(__file__).resolve().parents[1]
REVISION = "eidolon-i6-n07-fingerprint-v1"
FONT_DIRECTORY = Path("/usr/share/fonts/truetype/dejavu")
SETTINGS = Path("/etc/gtk-3.0/settings.ini")
FONTCONFIG = Path("/etc/fonts/fonts.conf")
GTK_POLICY = (b"[Settings]\ngtk-theme-name=Adwaita\n"
              b"gtk-font-name=DejaVu Sans 10\n"
              b"gtk-application-prefer-dark-theme=false\n")
FONT_POLICY = (b'<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n'
               b'<fontconfig><dir>/usr/share/fonts/truetype/dejavu</dir>'
               b'<cachedir>/var/cache/fontconfig</cachedir>'
               b'<alias><family>sans</family><prefer><family>DejaVu Sans</family></prefer></alias>'
               b'<alias><family>sans-serif</family><prefer><family>DejaVu Sans</family></prefer></alias>'
               b'<alias><family>serif</family><prefer><family>DejaVu Serif</family></prefer></alias>'
               b'<alias><family>monospace</family><prefer><family>DejaVu Sans Mono</family></prefer></alias>'
               b'</fontconfig>\n')
ENVIRONMENT = ("LANG", "LC_ALL", "GDK_SCALE", "GDK_DPI_SCALE", "GTK_THEME",
               "GTK_DATA_PREFIX", "GTK3_MODULES", "GTK_MODULES",
               "FONTCONFIG_FILE", "FONTCONFIG_PATH", "XDG_DATA_DIRS")
LIBRARIES = {"gtk": "libgtk-3.so.", "pango": "libpango-1.0.so.",
             "cairo": "libcairo.so."}
VERSION_FIELDS = {"gtk": "gtk_version", "pango": "pango_version",
                  "cairo": "cairo_version"}
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
MAX_MAPS = 1024 * 1024
MAX_FILE = 64 * 1024 * 1024
MAX_FONTS = 32
# Independently checked upstream git archive and the existing pinned release
# tar have the same 141 paths, git modes and payload hashes (2026-11-18).
# This maps the *unpatched* source. The Android patch is recorded separately.
WAYPIPE_COMMIT = "3ea049b2d33cbf1000714c28b9668f8c244f70bd"
WAYPIPE_ARCHIVE = "2d4093903d444fda39c9b7db0a0b826f450a64d4302a03c9c9e3dbe480e8bb03"
WAYPIPE_UPSTREAM_INVENTORY = "76dc5c8146c2234856e576751ed87741cb9308cb0f0e296121bdb436fa19a37a"
RENDERER_ALGORITHM = "sha256-canonical-path-gitmode-content-sha256-native-and-cargo-v1"


class FingerprintError(ValueError):
    """Only stable public reason codes; never include process contents."""


def need(ok, reason="N07_FINGERPRINT_INVALID"):
    if not ok:
        raise FingerprintError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _hex(value, expression=HEX64):
    return type(value) is str and expression.fullmatch(value) is not None


def _text(value):
    return type(value) is str and 0 < len(value) <= 160 and all(
        32 <= ord(c) < 127 for c in value)


def _same(left, right):
    """JSON scalar types matter: Python True == 1 is not receipt equality."""
    return canonical(left) == canonical(right)


def _read(path, limit=MAX_FILE):
    """Read an allowlisted system input, rejecting links and concurrent writes."""
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == 0
             and not before.st_mode & 0o022 and before.st_nlink == 1
             and 0 <= before.st_size <= limit, "N07_FINGERPRINT_FILE")
        raw = bytearray()
        while len(raw) <= limit:
            block = os.read(fd, min(65536, limit + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
        after, named = os.fstat(fd), os.stat(path, follow_symlinks=False)
        identity = lambda v: (v.st_dev, v.st_ino, v.st_size, v.st_mtime_ns, v.st_ctime_ns)
        need(len(raw) == before.st_size and identity(before) == identity(after)
             == identity(named), "N07_FINGERPRINT_FILE_CHANGED")
        return bytes(raw)
    finally:
        os.close(fd)


def _file(path):
    path = Path(path)
    raw = _read(path)
    return {"path": str(path), "size": len(raw), "sha256": sha(raw)}


def _renderer_source(root):
    paths = subprocess.check_output([
        "git", "-C", str(root), "ls-files", "--stage", "-z", "--", "native",
        "Cargo.lock", "Cargo.toml", "rust-toolchain.toml"], timeout=10)
    need(0 < len(paths) <= MAX_MAPS, "N07_FINGERPRINT_SOURCE_BOUNDS")
    inventory = []
    for entry in paths.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, _oid, stage = metadata.decode("ascii").split()
        name = name.decode("utf-8")
        need(stage == "0" and mode in ("100644", "100755")
             and not Path(name).is_absolute() and ".." not in Path(name).parts,
             "N07_FINGERPRINT_SOURCE_PATH")
        raw = _read(root / name)
        executable = bool(os.stat(root / name, follow_symlinks=False).st_mode & 0o111)
        need(executable == (mode == "100755"), "N07_FINGERPRINT_SOURCE_MODE")
        inventory.append([name, mode, sha(raw)])
    return {"algorithm": RENDERER_ALGORITHM, "file_count": len(inventory),
            "sha256": sha(canonical(sorted(inventory)))}


def _policy():
    need(_read(SETTINGS, 4096) == GTK_POLICY
         and _read(FONTCONFIG, 4096) == FONT_POLICY,
         "N07_FINGERPRINT_POLICY_CHANGED")
    return {"kind": "disposable-system-typography-v1",
            "gtk_settings_sha256": sha(GTK_POLICY),
            "fontconfig_sha256": sha(FONT_POLICY),
            "configured_theme": "Adwaita", "configured_font": "DejaVu Sans 10",
            "theme_observation": "configured-input-only-not-effective-property"}


def prepare_fixture(*, disposable=False):
    """Opt-in B3 setup of system typography; never touches app/C1 settings.

    Call only in the new disposable container before any product session. The
    production C2 environment and B1/B2 runners are deliberately unchanged.
    """
    need(disposable is True and os.getuid() == 0 and Path("/.dockerenv").is_file()
         and not Path("/var/run/docker.sock").exists(), "N07_FINGERPRINT_DISPOSABLE_ONLY")
    need(FONT_DIRECTORY.is_dir() and not FONT_DIRECTORY.is_symlink()
         and (FONT_DIRECTORY / "DejaVuSans.ttf").is_file(), "N07_FINGERPRINT_FONT_MISSING")
    for path, raw in ((SETTINGS, GTK_POLICY), (FONTCONFIG, FONT_POLICY)):
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        need(not path.is_symlink() and not path.parent.is_symlink(),
             "N07_FINGERPRINT_POLICY_PATH")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
        try:
            os.write(fd, raw)
            os.fchmod(fd, 0o644)
            os.fsync(fd)
        finally:
            os.close(fd)
    return _policy()


def _runtime_version(role, path):
    """Query the same immutable DSO bytes observed in the real app's maps.

    These version getters run in the supervisor, without GTK initialization,
    display access, widget calls or any call into the application process.
    """
    library = ctypes.CDLL(str(path))
    if role == "gtk":
        return ".".join(str(getattr(library, "gtk_get_" + part + "_version")())
                        for part in ("major", "minor", "micro"))
    function = getattr(library, role + "_version_string")
    function.restype = ctypes.c_char_p
    return function().decode("ascii")


def _environment(raw):
    need(len(raw) <= 65536, "N07_FINGERPRINT_ENV_BOUNDS")
    selected = {key: None for key in ENVIRONMENT}
    for entry in raw.split(b"\0"):
        key, sep, value = entry.partition(b"=")
        if sep and key.decode("ascii", "ignore") in selected:
            name = key.decode("ascii")
            need(selected[name] is None, "N07_FINGERPRINT_ENV_DUPLICATE")
            text = value.decode("ascii")
            need(_text(text), "N07_FINGERPRINT_ENV_INVALID")
            selected[name] = text
    return selected


def _mapped_paths(raw):
    need(type(raw) is bytes and len(raw) <= MAX_MAPS, "N07_FINGERPRINT_MAPS_BOUNDS")
    fonts, libraries = set(), {}
    for line in raw.decode("utf-8").splitlines():
        columns = line.split(None, 5)
        if len(columns) != 6:
            continue
        name = columns[5]
        if name.startswith(str(FONT_DIRECTORY) + "/"):
            need(not name.endswith(" (deleted)"), "N07_FINGERPRINT_MAPPED_FILE_DELETED")
            path = Path(name)
            need(path.parent == FONT_DIRECTORY and path.suffix == ".ttf"
                 and "\\" not in name, "N07_FINGERPRINT_FONT_PATH")
            fonts.add(name)
        elif re.search(r"\.(?:ttf|otf|ttc)(?: \(deleted\))?$", name):
            # A font outside the explicitly bounded system policy is drift,
            # not permission to copy an arbitrary mapped file.
            raise FingerprintError("N07_FINGERPRINT_FOREIGN_FONT")
        for role, prefix in LIBRARIES.items():
            if Path(name).name.startswith(prefix):
                path = Path(name)
                need(name.startswith("/usr/lib/") and ".." not in path.parts
                     and not name.endswith(" (deleted)")
                     and (role not in libraries or libraries[role] == name),
                     "N07_FINGERPRINT_LIBRARY_PATH")
                libraries[role] = name
    need(len(fonts) <= MAX_FONTS, "N07_FINGERPRINT_FONT_BOUNDS")
    return sorted(fonts), libraries


def _binding(native, environment, source):
    probe = native["n07_probe"]
    receipt = probe["before_menu"]["readback"]["receipt"]
    need(native["run_id"] == environment["run_id"] == probe["run_id"]
         and native["source_sha256"] == environment["source_sha256"] == source["tree_sha256"]
         and native["build_source"]["commit"] == source["commit"]
         and source["dirty"] is False, "N07_FINGERPRINT_SOURCE_MISMATCH")
    need(_hex(source["commit"], HEX40) and _hex(source["tree_sha256"])
         and _hex(probe["capture_id"])
         and type(probe["main_target"]) is dict
         and set(probe["main_target"]) == {"scene", "session", "window", "generation"}
         and all(type(value) is str and re.fullmatch(r"[1-9][0-9]*", value)
                 for value in probe["main_target"].values())
         and all(type(receipt[key]) is int and receipt[key] > 0
                 for key in ("renderer_owner", "context_epoch")),
         "N07_FINGERPRINT_BINDING_INVALID")
    return {"run_id": native["run_id"], "source_commit": source["commit"],
            "source_tree_sha256": source["tree_sha256"],
            "capture_id": probe["capture_id"], "target": probe["main_target"],
            "runtime": native["runtime"], "owner_id": native["owner_id"],
            "create": receipt["create"], "renderer_owner": receipt["renderer_owner"],
            "context_epoch": receipt["context_epoch"]}


def _renderer(native):
    probe = native["n07_probe"]
    receipts = [probe["before_menu"]["readback"]["receipt"],
                *(probe["b2"][name]["readback"]["receipt"] for name in (
                    "first_off_checkmark", "first_on_gutter", "first_on_checkmark"))]
    backends = [{key: receipt["backend"][key] for key in ("vendor", "renderer", "version")}
                for receipt in receipts]
    need(all(value == backends[0] for value in backends), "N07_FINGERPRINT_RENDERER_DRIFT")
    need(all(_text(value) for value in backends[0].values()),
         "N07_FINGERPRINT_RENDERER_INVALID")
    raw_scales = [layer["scale"] for receipt in receipts for layer in receipt["layers"]]
    need(raw_scales and all(type(scale) is int and scale in (1, 2) for scale in raw_scales),
         "N07_FINGERPRINT_SCALE_INVALID")
    scales = sorted(set(raw_scales))
    return {**backends[0], "layer_scales": scales}


def _check_file(value, *, font=False, role=None):
    keys = {"path", "size", "sha256"} | ({"version"} if role else set())
    need(type(value) is dict and set(value) == keys
         and type(value["path"]) is str and len(value["path"]) <= 256
         and type(value["size"]) is int and 0 < value["size"] <= MAX_FILE
         and _hex(value["sha256"]), "N07_FINGERPRINT_FILE_INVALID")
    path = Path(value["path"])
    if font:
        need(path.parent == FONT_DIRECTORY and path.suffix == ".ttf"
             and ".." not in path.parts, "N07_FINGERPRINT_FONT_PATH")
    if role:
        need(str(path).startswith("/usr/lib/") and ".." not in path.parts
             and path.name.startswith(LIBRARIES[role])
             and type(value["version"]) is str
             and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value["version"]) is not None,
             "N07_FINGERPRINT_LIBRARY_INVALID")


def _derive(record, native, environment, source):
    e = record["evidence"]
    need(type(e) is dict and set(e) == {
        "policy", "source_pins", "process", "fonts", "libraries", "renderer", "observation_errors"},
        "N07_FINGERPRINT_EVIDENCE_SHAPE")
    policy = e["policy"]
    need(policy == {"kind": "disposable-system-typography-v1",
        "gtk_settings_sha256": sha(GTK_POLICY), "fontconfig_sha256": sha(FONT_POLICY),
        "configured_theme": "Adwaita", "configured_font": "DejaVu Sans 10",
        "theme_observation": "configured-input-only-not-effective-property"},
        "N07_FINGERPRINT_POLICY_CHANGED")
    pins = e["source_pins"]
    need(type(pins) is dict and set(pins) == {
        "mousepad_commit", "mousepad_executable_sha256", "mousepad_evidence_sha256",
        "waypipe_commit", "waypipe_archive_sha256", "waypipe_upstream_inventory_sha256",
        "waypipe_tree_sha256", "waypipe_binary_sha256", "waypipe_patch_sha256",
        "renderer_source"} and pins["mousepad_commit"] == pixel.MOUSEPAD_COMMIT
         and pins["waypipe_commit"] == WAYPIPE_COMMIT
         and pins["waypipe_archive_sha256"] == WAYPIPE_ARCHIVE
         and pins["waypipe_upstream_inventory_sha256"] == WAYPIPE_UPSTREAM_INVENTORY
         and all(_hex(v) for k, v in pins.items() if k not in (
             "mousepad_commit", "waypipe_commit", "renderer_source")),
         "N07_FINGERPRINT_SOURCE_PINS")
    revision = pins["renderer_source"]
    need(type(revision) is dict and set(revision) == {"algorithm", "file_count", "sha256"}
         and revision["algorithm"] == RENDERER_ALGORITHM
         and type(revision["file_count"]) is int and 1 <= revision["file_count"] <= 10000
         and _hex(revision["sha256"]), "N07_FINGERPRINT_RENDERER_SOURCE")
    need(pins["waypipe_binary_sha256"] == environment["before"]["pins"]["waypipe_sha256"]
         == environment["after"]["pins"]["waypipe_sha256"], "N07_FINGERPRINT_WAYPIPE_MISMATCH")
    process = e["process"]
    need(process is None or (type(process) is dict and set(process) == {
        "pid", "start_ticks", "uid", "executable_sha256", "environment", "samples"}
         and all(type(process[k]) is int and process[k] > 0 for k in (
             "pid", "start_ticks", "uid", "samples"))
         and process["uid"] == environment["app"]["uid"]
         and process["executable_sha256"] == pins["mousepad_executable_sha256"]),
         "N07_FINGERPRINT_PROCESS_INVALID")
    if process is not None:
        env = process["environment"]
        need(type(env) is dict and set(env) == set(ENVIRONMENT)
             and all(v is None or _text(v) for v in env.values()),
             "N07_FINGERPRINT_ENV_INVALID")
        need(env["GTK3_MODULES"] is None and env["GTK_MODULES"] is None
             and env["FONTCONFIG_FILE"] is None and env["FONTCONFIG_PATH"] is None,
             "N07_FINGERPRINT_ENV_OVERRIDE")
    fonts, libraries = e["fonts"], e["libraries"]
    need(type(fonts) is list and len(fonts) <= MAX_FONTS
         and type(libraries) is dict and set(libraries) <= set(LIBRARIES),
         "N07_FINGERPRINT_INPUTS_INVALID")
    for font in fonts:
        _check_file(font, font=True)
    need([font["path"] for font in fonts] == sorted(set(font["path"] for font in fonts)),
         "N07_FINGERPRINT_FONT_DUPLICATE")
    for role, library in libraries.items():
        _check_file(library, role=role)
    need(process is not None or (not fonts and not libraries), "N07_FINGERPRINT_UNOBSERVED_INPUTS")
    need(_same(e["renderer"], _renderer(native)), "N07_FINGERPRINT_RENDERER_MISMATCH")
    errors = e["observation_errors"]
    need(type(errors) is list and len(errors) <= 16 and errors == sorted(set(errors)) and all(
        type(error) is str and re.fullmatch(r"N07_FINGERPRINT_[A-Z_]+", error) for error in errors),
        "N07_FINGERPRINT_ERRORS_INVALID")
    values = {"mousepad_commit": pins["mousepad_commit"], "waypipe_commit": pins["waypipe_commit"],
              "mesa_vendor": e["renderer"]["vendor"],
              "mesa_renderer": e["renderer"]["renderer"],
              "renderer_revision": "sha256:" + revision["sha256"],
              "fontconfig_sha256": policy["fontconfig_sha256"]}
    if fonts:
        # A canonical inventory digest pins every actually mapped font, not a
        # guessed fc-match candidate or a single arbitrary font file.
        values["font_sha256"] = sha(canonical(fonts))
    for role, library in libraries.items():
        values[VERSION_FIELDS[role]] = library["version"]
    if process is not None:
        env = process["environment"]
        if env["LANG"] == env["LC_ALL"] and env["LC_ALL"] is not None:
            values["locale"] = env["LC_ALL"]
    if len(e["renderer"]["layer_scales"]) == 1:
        values["scale"] = str(e["renderer"]["layer_scales"][0])
    # Theme deliberately remains missing: the configured system policy is
    # not a direct observation of the application's effective GtkSettings.
    return values


def validate_fingerprint(record, native, environment, source):
    """Recompute all available contract fields and reject envelope tampering."""
    try:
        need(type(record) is dict and set(record) == {
            "schema", "revision", "status", "binding", "values", "missing", "evidence"}
             and type(record["schema"]) is int and record["schema"] == 1
             and record["revision"] == REVISION, "N07_FINGERPRINT_RECORD_INVALID")
        need(_same(record["binding"], _binding(native, environment, source)),
             "N07_FINGERPRINT_BINDING_MISMATCH")
        values = _derive(record, native, environment, source)
        need(record["values"] == values and type(record["values"]) is dict
             and all(_text(value) for value in values.values()), "N07_FINGERPRINT_VALUES_MISMATCH")
        missing = sorted(pixel.FINGERPRINT - set(values))
        need(record["missing"] == missing
             and record["status"] == ("partial" if missing else "complete"),
             "N07_FINGERPRINT_COMPLETENESS_INVALID")
        if not missing:
            pixel._fingerprint(values)
        return record
    except FingerprintError:
        raise
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        raise FingerprintError("N07_FINGERPRINT_RECORD_INVALID") from error


class LiveFingerprintObserver:
    """Observe only the dedicated app UID's exact source-pinned Mousepad ELF.

    /proc reads are bounded, transient and never exported. Font/library bytes
    are hashed only from fixed system roots. A second Mousepad process,
    replacement PID/starttime, mutable file or override is a diagnostic error.
    Sampling runs as the same dedicated app UID, because Docker root normally
    lacks CAP_SYS_PTRACE and cannot read another UID's /proc/maps/environ.
    The helper has no display and no credentials; it exits on pipe EOF or its
    fixed deadline. No helper, C2 lock or runtime resource survives __exit__.
    """
    def __init__(self, *, app_uid, run_id, source, policy, root=ROOT):
        need(type(app_uid) is int and app_uid > 0 and _hex(run_id, re.compile(r"[0-9a-f]{32}\Z")),
             "N07_FINGERPRINT_ARGUMENT")
        self.app_uid, self.run_id, self.source = app_uid, run_id, source
        self.policy, self.root = policy, Path(root)
        self.worker = None
        self.process = None
        self.fonts, self.libraries, self.errors = {}, {}, set()
        self._hash_cache = {}
        build_raw = _read("/usr/share/doc/mousepad/eidolon-build-evidence.json", 65536)
        build = json.loads(build_raw)
        need(build["source_commit"] == pixel.MOUSEPAD_COMMIT
             and sha(_read("/usr/bin/mousepad")) == build["executable_sha256"],
             "N07_FINGERPRINT_MOUSEPAD_SOURCE")
        lock = json.loads(_read(self.root / "native/deps.lock.json", 65536))["waypipe"]
        waypipe = json.loads(_read(self.root / "native/generated/waypipe-host.json", 65536))
        need(len(lock["patches"]) == 1 and lock["sha256"] == WAYPIPE_ARCHIVE
             and waypipe["inputs"]["native/deps.lock.json"] ==
             sha(_read(self.root / "native/deps.lock.json", 65536)), "N07_FINGERPRINT_WAYPIPE_SOURCE")
        self.pins = {"mousepad_commit": build["source_commit"],
            "mousepad_executable_sha256": build["executable_sha256"],
            "mousepad_evidence_sha256": sha(build_raw),
            "waypipe_commit": WAYPIPE_COMMIT,
            "waypipe_archive_sha256": lock["sha256"],
            "waypipe_upstream_inventory_sha256": WAYPIPE_UPSTREAM_INVENTORY,
            "waypipe_tree_sha256": waypipe["trees"]["waypipe"],
            "waypipe_binary_sha256": waypipe["sha256"],
            "waypipe_patch_sha256": lock["patches"][0]["sha256"],
            "renderer_source": _renderer_source(self.root)}

    def __enter__(self):
        need(self.worker is None and self.policy == _policy(), "N07_FINGERPRINT_OBSERVER_REUSED")
        self.worker = subprocess.Popen([
            "/usr/bin/python3", "-B", str(Path(__file__).resolve()),
            "--observe", str(self.app_uid), self.pins["mousepad_executable_sha256"],
        ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            user=self.app_uid, group=self.app_uid, extra_groups=[],
            start_new_session=True, cwd="/", env={"PATH": "/usr/bin:/bin", "HOME": "/",
                "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"})
        return self

    def __exit__(self, *_args):
        self.worker.stdin.close()
        self.worker.stdin = None
        try:
            raw, _ = self.worker.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            self.worker.kill()
            self.worker.communicate(timeout=5)
            self.errors.add("N07_FINGERPRINT_OBSERVER_STUCK")
            return
        # Preserve normal product cleanup evidence even if optional observation
        # fails. The partial record exposes the fixed failure and missing data.
        try:
            need(self.worker.returncode == 0 and 0 < len(raw) <= 256 * 1024,
                 "N07_FINGERPRINT_WORKER_FAILED")
            data = json.loads(raw)
            need(type(data) is dict and set(data) == {
                "process", "fonts", "libraries", "errors"}, "N07_FINGERPRINT_WORKER_FAILED")
            need(type(data["fonts"]) is dict and len(data["fonts"]) <= MAX_FONTS
                 and type(data["libraries"]) is dict and set(data["libraries"]) <= set(LIBRARIES)
                 and type(data["errors"]) is list and len(data["errors"]) <= 16
                 and all(type(error) is str and re.fullmatch(r"N07_FINGERPRINT_[A-Z_]+", error)
                         for error in data["errors"]), "N07_FINGERPRINT_WORKER_FAILED")
            self.process, self.fonts = data["process"], data["fonts"]
            self.libraries = data["libraries"]
            self.errors.update(data["errors"])
        except (FingerprintError, ValueError, TypeError):
            self.errors.add("N07_FINGERPRINT_WORKER_FAILED")

    def _cached_file(self, path):
        key = str(path)
        value = self._hash_cache.get(key)
        observed = os.stat(path, follow_symlinks=False)
        identity = (observed.st_dev, observed.st_ino, observed.st_size,
                    observed.st_mtime_ns, observed.st_ctime_ns)
        if value is None:
            value = (_file(path), identity)
            self._hash_cache[key] = value
        need(value[1] == identity, "N07_FINGERPRINT_FILE_CHANGED")
        return dict(value[0])

    def _sample(self):
        matched = []
        for directory in Path("/proc").iterdir():
            if not directory.name.isdecimal():
                continue
            try:
                if directory.stat().st_uid != self.app_uid:
                    continue
                if os.readlink(directory / "exe") != "/usr/bin/mousepad":
                    continue
                pid = int(directory.name)
                with (directory / "stat").open("rb") as f:
                    raw_stat = f.read(8193)
                need(len(raw_stat) <= 8192, "N07_FINGERPRINT_PROCESS_BOUNDS")
                start = int(raw_stat.rsplit(b")", 1)[1].split()[19])
                with (directory / "environ").open("rb") as f:
                    env = _environment(f.read(65537))
                with (directory / "maps").open("rb") as f:
                    fonts, libraries = _mapped_paths(f.read(MAX_MAPS + 1))
                matched.append((pid, start, env, fonts, libraries))
            except (FileNotFoundError, ProcessLookupError):
                continue
        need(len(matched) <= 1, "N07_FINGERPRINT_AMBIGUOUS_PROCESS")
        for pid, start, env, fonts, libraries in matched:
            identity = {"pid": pid, "start_ticks": start, "uid": self.app_uid,
                        "executable_sha256": self.pins["mousepad_executable_sha256"],
                        "environment": env}
            if self.process is None:
                need(self._cached_file("/usr/bin/mousepad")["sha256"] == identity["executable_sha256"],
                     "N07_FINGERPRINT_MOUSEPAD_SOURCE")
                self.process = {**identity, "samples": 0}
            need({k: v for k, v in self.process.items() if k != "samples"} == identity,
                 "N07_FINGERPRINT_PROCESS_CHANGED")
            self.process["samples"] += 1
            for name in fonts:
                self.fonts[name] = self._cached_file(name)
            for role, name in libraries.items():
                value = self._cached_file(name)
                if role not in self.libraries:
                    self.libraries[role] = {**value, "version": _runtime_version(role, name)}
                need({k: v for k, v in self.libraries[role].items() if k != "version"} == value,
                     "N07_FINGERPRINT_LIBRARY_CHANGED")

    def _loop(self):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if select.select([sys.stdin.buffer], [], [], 0.025)[0]:
                need(sys.stdin.buffer.read(1) == b"", "N07_FINGERPRINT_WORKER_INPUT")
                return
            try:
                self._sample()
            except FingerprintError as error:
                self.errors.add(str(error))
            except (OSError, ValueError, UnicodeError, IndexError):
                self.errors.add("N07_FINGERPRINT_OBSERVATION_FAILED")
        self.errors.add("N07_FINGERPRINT_OBSERVER_TIMEOUT")

    def finish(self, native, environment):
        need(self.worker is not None and self.worker.poll() is not None
             and self.run_id == native["run_id"], "N07_FINGERPRINT_OBSERVER_ACTIVE")
        need(self.policy == _policy(), "N07_FINGERPRINT_POLICY_CHANGED")
        # Rehash every helper-observed system input after the process exits.
        # A worker cannot introduce an unbounded/arbitrary path for root reads.
        for font in self.fonts.values():
            _check_file(font, font=True)
            need(font == _file(font["path"]), "N07_FINGERPRINT_FILE_CHANGED")
        for role, library in self.libraries.items():
            need(role in LIBRARIES, "N07_FINGERPRINT_LIBRARY_INVALID")
            _check_file(library, role=role)
            need({key: value for key, value in library.items() if key != "version"} ==
                 _file(library["path"]), "N07_FINGERPRINT_FILE_CHANGED")
        record = {"schema": 1, "revision": REVISION,
            "binding": _binding(native, environment, self.source),
            "evidence": {"policy": self.policy, "source_pins": self.pins,
                "process": self.process, "fonts": [self.fonts[k] for k in sorted(self.fonts)],
                "libraries": self.libraries, "renderer": _renderer(native),
                "observation_errors": sorted(self.errors)}}
        values = _derive(record, native, environment, self.source)
        missing = sorted(pixel.FINGERPRINT - set(values))
        record.update(values=values, missing=missing, status="partial" if missing else "complete")
        return validate_fingerprint(record, native, environment, self.source)


def _worker_main(argv):
    need(len(argv) == 3 and argv[0] == "--observe" and argv[1].isdecimal()
         and _hex(argv[2]), "N07_FINGERPRINT_ARGUMENT")
    uid = int(argv[1])
    need(uid > 0 and os.getuid() == os.geteuid() == uid,
         "N07_FINGERPRINT_WORKER_UID")
    observer = LiveFingerprintObserver.__new__(LiveFingerprintObserver)
    observer.app_uid = uid
    observer.pins = {"mousepad_executable_sha256": argv[2]}
    observer.process, observer.fonts, observer.libraries = None, {}, {}
    observer.errors, observer._hash_cache = set(), {}
    observer._loop()
    raw = canonical({"process": observer.process, "fonts": observer.fonts,
                     "libraries": observer.libraries, "errors": sorted(observer.errors)})
    need(len(raw) <= 256 * 1024, "N07_FINGERPRINT_WORKER_BOUNDS")
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_worker_main(sys.argv[1:]))
    except (FingerprintError, OSError, ValueError):
        raise SystemExit(2)
