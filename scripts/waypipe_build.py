"""Waypipeの固定source/patchの再構成。展開済みsourceも毎回照合する。"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(path):
    result = {}
    for file in path.rglob("*"):
        name = file.relative_to(path).as_posix()
        if file.is_symlink():
            result[name] = {"symlink": os.readlink(file)}
        elif file.is_file():
            result[name] = {"sha256": digest(file)}
        elif not file.is_dir():
            raise RuntimeError("unexpected source type: " + name)
    return result


def prepare(name, spec, cache):
    archive = cache / (name + "-" + spec["version"] + ".tar")
    if not archive.exists():
        temporary = archive.with_suffix(".download")
        with urllib.request.urlopen(spec["source"], timeout=60) as response, temporary.open("wb") as out:
            shutil.copyfileobj(response, out)
        temporary.replace(archive)
    if digest(archive) != spec["sha256"]:
        raise RuntimeError(name + " archive hash mismatch")
    with tempfile.TemporaryDirectory(prefix=name + "-", dir=cache) as temporary:
        stage = Path(temporary)
        with tarfile.open(archive) as tar:
            tar.extractall(stage, filter="data")
        roots = list(stage.iterdir())
        if len(roots) != 1 or not roots[0].is_dir() or roots[0].is_symlink():
            raise RuntimeError(name + " source layout")
        source = roots[0]
        for patch in spec["patches"]:
            path = ROOT / patch["file"]
            if digest(path) != patch["sha256"]:
                raise RuntimeError(name + " patch hash mismatch")
            subprocess.run(["patch", "--batch", "--fuzz=0", "-p1", "-i", str(path)], cwd=source, check=True)
        if (ROOT / "native" / spec["license_file"]).read_bytes() != (source / spec["license_source"]).read_bytes():
            raise RuntimeError(name + " license mismatch")
        expected = tree(source)
        fingerprint = hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()
        target = cache / (name + "-source-" + fingerprint)
        if target.is_symlink():
            raise RuntimeError(name + " source directory symlink")
        if target.exists():
            if tree(target) != expected:
                raise RuntimeError(name + " source differs from archive + patches")
        else:
            source.rename(target)
        return target, fingerprint
