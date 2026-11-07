"""固定probe source bundleの作成・同UIDの新規directoryへの検査付き配備。stdlibのみ。"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

SOURCE_PATHS = {
    "sdl-wayland-probe.c": "scripts/fixtures/sdl-wayland-probe.c",
    "eidolon-sdl-probe.py": "scripts/termux/eidolon-sdl-probe.py",
}
MAX_SOURCE = 65536
MAX_PAYLOAD = 196608
RUN_ID = re.compile(r"[0-9a-f]{32}")


class InstallError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise InstallError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def prepare_bundle(repository, run_id, source_commit, source_dirty, *, source_paths=SOURCE_PATHS, kind="eidolon-sdl-source-v1"):
    require(RUN_ID.fullmatch(run_id) is not None, "RUN_ID_INVALID")
    require(re.fullmatch(r"[0-9a-f]{40}", source_commit) is not None and type(source_dirty) is bool, "PROVENANCE_INVALID")
    files = {}
    for name, relative in source_paths.items():
        source = repository / relative
        require(stat.S_ISREG(source.lstat().st_mode) and not source.is_symlink(), "SOURCE_NOT_REGULAR")
        require(0 < source.stat().st_size <= MAX_SOURCE, "SOURCE_SIZE_INVALID")
        data = source.read_bytes()
        require(0 < len(data) <= MAX_SOURCE, "SOURCE_SIZE_INVALID")
        files[name] = data
    manifest = dict(schema=1, kind=kind, run_id=run_id,
                    source_commit=source_commit, source_dirty=source_dirty,
                    files={name: dict(bytes=len(data), sha256=sha256(data)) for name, data in files.items()})
    payload = canonical(dict(manifest=manifest, files={name: base64.b64encode(data).decode("ascii") for name, data in files.items()}))
    require(len(payload) <= MAX_PAYLOAD, "PAYLOAD_TOO_LARGE")
    return payload, manifest


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_FIELD")
        result[key] = value
    return result


def decode_bundle(payload, expected_hash, *, source_paths=SOURCE_PATHS, kind="eidolon-sdl-source-v1"):
    require(0 < len(payload) <= MAX_PAYLOAD, "PAYLOAD_SIZE_INVALID")
    require(sha256(payload) == expected_hash, "PAYLOAD_HASH_MISMATCH")
    try:
        bundle = json.loads(payload, object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError) as failure:
        raise InstallError("PAYLOAD_JSON_INVALID") from failure
    require(type(bundle) is dict and set(bundle) == {"manifest", "files"}, "BUNDLE_INVALID")
    manifest, encoded = bundle["manifest"], bundle["files"]
    require(type(manifest) is dict and set(manifest) == {
        "schema", "kind", "run_id", "source_commit", "source_dirty", "files"}, "MANIFEST_INVALID")
    require(type(manifest["schema"]) is int and manifest["schema"] == 1 and
            manifest["kind"] == kind, "MANIFEST_INVALID")
    require(type(manifest["run_id"]) is str and RUN_ID.fullmatch(manifest["run_id"]) is not None, "RUN_ID_INVALID")
    require(type(manifest["source_commit"]) is str and re.fullmatch(r"[0-9a-f]{40}", manifest["source_commit"]) is not None and
            type(manifest["source_dirty"]) is bool, "PROVENANCE_INVALID")
    require(type(encoded) is dict and set(encoded) == set(source_paths) and
            type(manifest["files"]) is dict and set(manifest["files"]) == set(source_paths), "SOURCE_NAMES_INVALID")
    files = {}
    for name in source_paths:
        info = manifest["files"][name]
        require(type(info) is dict and set(info) == {"bytes", "sha256"} and type(info["bytes"]) is int and
                0 < info["bytes"] <= MAX_SOURCE and type(info["sha256"]) is str and
                re.fullmatch(r"[0-9a-f]{64}", info["sha256"]) is not None, "SOURCE_METADATA_INVALID")
        require(type(encoded[name]) is str and len(encoded[name]) <= ((MAX_SOURCE + 2) // 3) * 4, "SOURCE_SIZE_INVALID")
        try:
            data = base64.b64decode(encoded[name], validate=True)
        except ValueError as failure:
            raise InstallError("SOURCE_ENCODING_INVALID") from failure
        require(0 < len(data) <= MAX_SOURCE, "SOURCE_SIZE_INVALID")
        require(len(data) == info["bytes"] and sha256(data) == info["sha256"], "SOURCE_HASH_MISMATCH")
        files[name] = data
    files["manifest.json"] = canonical(manifest) + b"\n"
    return manifest, files


def check_directory(fd, expected_uid):
    info = os.fstat(fd)
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == expected_uid and not info.st_mode & 0o022, "DIRECTORY_UNSAFE")


def install_bundle(payload, expected_hash, home, expected_uid, *, source_paths=SOURCE_PATHS,
                   kind="eidolon-sdl-source-v1", scope="nx11"):
    """内容検査を先に終え、openat/O_NOFOLLOWで既存fileを置換せず配備する。"""
    require(expected_uid == os.getuid(), "UID_MISMATCH")
    require((kind, scope) in {("eidolon-sdl-source-v1", "nx11"),
                             ("eidolon-gtk3-fixture-source-v1", "nx15-fixture")}, "SOURCE_SCOPE_INVALID")
    require(all(Path(name).name == name and name not in {".", "..", "manifest.json"} for name in source_paths), "SOURCE_NAMES_INVALID")
    manifest, files = decode_bundle(payload, expected_hash, source_paths=source_paths, kind=kind)
    require(home.is_absolute(), "HOME_INVALID")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(home, flags)
    try:
        check_directory(fd, expected_uid)
        for part in (".local", "state", "eidolon", scope):
            try:
                os.mkdir(part, mode=0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, flags, dir_fd=fd)
            try:
                check_directory(child, expected_uid)
            except Exception:
                os.close(child)
                raise
            os.close(fd)
            fd = child
        # 既存runはsymlinkや空directoryでも再利用しない。
        try:
            os.mkdir(manifest["run_id"], mode=0o700, dir_fd=fd)
        except FileExistsError as failure:
            raise InstallError("RUN_ALREADY_EXISTS") from failure
        run_fd = os.open(manifest["run_id"], flags, dir_fd=fd)
        try:
            os.fchmod(run_fd, 0o700)
            check_directory(run_fd, expected_uid)
            for name, data in files.items():
                target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=run_fd)
                with os.fdopen(target, "wb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                read_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=run_fd)
                with os.fdopen(read_fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    require(stat.S_ISREG(info.st_mode) and info.st_uid == expected_uid and
                            stat.S_IMODE(info.st_mode) == 0o600 and info.st_size == len(data), "INSTALLED_FILE_UNSAFE")
                    require(sha256(stream.read(len(data) + 1)) == sha256(data), "INSTALLED_HASH_MISMATCH")
            os.fsync(run_fd)
        finally:
            os.close(run_fd)
    finally:
        os.close(fd)
    return dict(status="pass", uid=expected_uid, run_id=manifest["run_id"],
                directory=str(home / ".local/state/eidolon" / scope / manifest["run_id"]),
                manifest_sha256=sha256(files["manifest.json"]), files=manifest["files"])


def termux_home(executable):
    """run-asのHOMEはapp dataを指すため、実Python prefixからTermux HOMEを導出する。"""
    binary = Path(executable).resolve(strict=True)
    prefix = binary.parent.parent
    require(binary.parent.name == "bin" and binary.name.startswith("python") and prefix.name == "usr" and
            prefix.parent.name == "files" and prefix.parent.parent.name == "com.termux", "PYTHON_PREFIX_INVALID")
    require(stat.S_ISREG(binary.stat().st_mode) and binary.stat().st_uid == os.getuid(), "PYTHON_PREFIX_UNSAFE")
    for directory in (binary.parent, prefix):
        info = directory.stat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o022,
                "PYTHON_PREFIX_UNSAFE")
    # Android/Termux標準のfiles親は0771。同じapp UID/GIDのgroup writeだけ許容する。
    # usr/bin・home以下を共有書込み可能にする例外ではない。
    files = prefix.parent.stat()
    require(stat.S_ISDIR(files.st_mode) and files.st_uid == os.getuid() and
            files.st_gid == os.getgid() == os.getuid() and not files.st_mode & 0o002,
            "PYTHON_PREFIX_UNSAFE")
    home = prefix.parent / "home"
    require(stat.S_ISDIR(home.lstat().st_mode) and not home.is_symlink(), "HOME_INVALID")
    return home


def remote_main():
    try:
        require(len(sys.argv) == 3, "REMOTE_ARGUMENT_INVALID")
        payload = sys.stdin.buffer.read(MAX_PAYLOAD + 1)
        report = install_bundle(payload, sys.argv[1], termux_home(sys.executable), int(sys.argv[2]))
        report["home_source"] = "python_prefix"
        print(json.dumps(report))
        return 0
    except (OSError, ValueError, TypeError) as failure:
        print(json.dumps(dict(status="fail", error=str(failure) if isinstance(failure, InstallError) else "INSTALL_IO_FAILED")))
        return 1


if __name__ == "__main__":
    raise SystemExit(remote_main())
