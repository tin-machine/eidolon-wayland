"""固定Cargo依存からテーマ/変換のlicenseと再取得可能なsource一覧を作る。"""
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib
import tarfile

from native_build import ROOT, TARGET


def inventory():
    metadata = json.loads(subprocess.check_output(
        ["cargo", "metadata", "--locked", "--format-version", "1", "--filter-platform", TARGET],
        cwd=ROOT, text=True))
    packages = {p["id"]: p for p in metadata["packages"]}
    nodes = {n["id"]: n for n in metadata["resolve"]["nodes"]}
    roots = [p["id"] for p in packages.values() if p["name"] in {"eidolon-theme", "cgmath"}]
    if len(roots) != 2:
        raise ValueError("theme dependency roots are ambiguous")
    todo, selected = roots[:], set()
    while todo:
        package_id = todo.pop()
        if package_id in selected:
            continue
        selected.add(package_id)
        todo.extend(d["pkg"] for d in nodes[package_id]["deps"]
                    if any(k["kind"] != "dev" for k in d["dep_kinds"]))
    locked = {(p["name"], p["version"]): p for p in tomllib.loads((ROOT / "Cargo.lock").read_text())["package"]
              if p.get("source") == "registry+https://github.com/rust-lang/crates.io-index"}
    assets, records = {}, []
    for package in sorted((packages[id_] for id_ in selected), key=lambda p: (p["name"], p["version"])):
        if package["source"] is None:
            continue  # repository-owned model/theme have no additional upstream license file.
        name, version = package["name"], package["version"]
        lock = locked.get((name, version))
        if lock is None or lock["source"] != package["source"] or not package["license"]:
            raise ValueError(f"unrecorded theme dependency: {name} {version}")
        source = Path(package["manifest_path"]).parent
        # registry展開先には.cargo-checksum.jsonが無いCargoもある。
        # 配布archiveそのものをCargo.lockと照合し、その中の原本を同梱する。
        archive = source.parents[2] / "cache" / source.parent.name / f"{name}-{version}.crate"
        if hashlib.sha256(archive.read_bytes()).hexdigest() != lock["checksum"]:
            raise ValueError(f"theme package checksum mismatch: {name}")
        licenses = {}
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                parts = Path(member.name).parts
                if len(parts) != 2 or parts[0] != f"{name}-{version}" or not member.isfile():
                    continue
                filename = parts[1]
                if not filename.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")):
                    continue
                if member.size > 2 * 1024 * 1024:
                    raise ValueError(f"theme license file size limit: {name}")
                data = tar.extractfile(member).read()
                path = f"licenses/theme/{name}-{version}/{filename}"
                if path in assets:
                    raise ValueError(f"duplicate theme license: {path}")
                assets[path] = data
                licenses[path] = hashlib.sha256(data).hexdigest()
        if not licenses:
            raise ValueError(f"theme license file missing: {name}")
        records.append(dict(name=name, version=version, license=package["license"],
                            archive_sha256=lock["checksum"],
                            source=f"https://static.crates.io/crates/{name}/{name}-{version}.crate",
                            modified=False, licenses=licenses))
    return dict(schema=1, scope="theme-and-transform", packages=records), assets


def write(assets_root):
    metadata, files = inventory()
    generated = assets_root / "licenses/theme"
    if generated.is_dir():
        for path in generated.rglob("*"):
            if path.is_file() and path.relative_to(assets_root).as_posix() not in files:
                path.unlink()
    for name, data in files.items():
        path = assets_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return metadata


def validate(archive, value):
    expected, files = inventory()
    if value != expected:
        raise ValueError("theme license/source inventory mismatch")
    names = archive.namelist()
    expected_names = {"assets/" + name for name in files}
    if {name for name in names if name.startswith("assets/licenses/theme/")} != expected_names:
        raise ValueError("theme license assets missing or unexpected")
    for name, data in files.items():
        path = "assets/" + name
        if names.count(path) != 1 or archive.read(path) != data:
            raise ValueError("theme license asset hash mismatch")
