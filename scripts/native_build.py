"""製品nativeの入力同定。Gradle・Cargo・APK検査で同じ契約を使う。"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]
NDK = "27.3.13750724"
API = 34
ABI = "arm64-v8a"
TARGET = "aarch64-linux-android"


def source_files(root=ROOT):
    fixed = ["Cargo.toml", "Cargo.lock", "rust-toolchain.toml", "scripts/build-native",
             "scripts/native-build-inputs", "scripts/native_build.py", "scripts/theme_licenses.py", "scripts/check-native-package", "scripts/termux/eidolon-session.py",
             "scripts/termux/eidolon-gui.py", "scripts/termux/eidolon_gui_owner.py",
             "scripts/linux/eidolon-linux-session.py",
             "scripts/fixtures/gtk3-interaction-fixture.c",
             "scripts/build-native-deps", "scripts/check-native-deps", "scripts/native-keymap-generator.c", "scripts/prepare-smithay",
             "scripts/build-waypipe", "scripts/waypipe_build.py", "scripts/build-wayland-fixture"]
    paths = {root / p for p in fixed if (root / p).is_file()}
    for folder in ["native", "android/app/src", "android/gradle"]:
        paths.update(p for p in (root / folder).rglob("*") if p.is_file()
                     and not {"target", "build", ".cache", "generated", "__pycache__"}.intersection(p.relative_to(root).parts))
    paths.update(p for p in (root / "android").glob("*.kts"))
    paths.add(root / "android/app/build.gradle.kts")
    return sorted(paths)


def identity(version, profile, target=TARGET, root=ROOT):
    if not 1 <= version <= 2_100_000_000 or profile not in {"debug", "release"}:
        raise ValueError("versionCode/profileが不正です")
    commit = os.environ.get("EIDOLON_SOURCE_COMMIT")
    dirty = os.environ.get("EIDOLON_SOURCE_DIRTY")
    if commit is None or dirty is None:
        # Gitのない配布sourceでは3項目すべてを明示できる。
        if commit is None:
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        if dirty is None:
            dirty = "true" if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=root) else "false"
    digest = os.environ.get("EIDOLON_SOURCE_DIGEST")
    if digest is None:
        hasher = hashlib.sha256()
        for path in source_files(root):
            hasher.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
        digest = hasher.hexdigest()
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"[0-9a-f]{64}", digest) or dirty not in {"true", "false"}:
        raise ValueError("source commit/digest/dirtyが不正です")
    manifest = tomllib.loads((root / "Cargo.toml").read_text())
    return dict(schema=1, source_commit=commit, source_digest=digest, source_dirty=dirty == "true",
                apk_version_code=version, crate_version=manifest["workspace"]["package"]["version"], target=target, profile=profile)


def canonical(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True)
