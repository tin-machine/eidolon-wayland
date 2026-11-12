"""Build and verify APK redistribution notices. No network access or broad license inference.

Only reviewed license-family policies are accepted for Android runtime groups.
A policy match is a build gate, NOT a legal certification of every published AAR.
"""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
ASSET_PREFIX = "assets/licenses/eidolon/"
ANDROID_MANIFEST = "assets/licenses/android/runtime-modules.json"
META_MODULES = {
    "meta-spatial-sdk", "meta-spatial-sdk-toolkit",
    "meta-spatial-sdk-vr", "meta-spatial-sdk-compose",
}
# Keep entries narrow. Unknown runtime groups must fail the APK upload gate.
# Full per-artifact notices/source review remains a release/publication requirement.
ANDROID_POLICY = {
    "androidx": ("Apache-2.0", "https://android.googlesource.com/platform/frameworks/support/"),
    "org.jetbrains.kotlin": ("Apache-2.0", "https://github.com/JetBrains/kotlin"),
    "org.jetbrains.kotlinx": ("Apache-2.0", "https://github.com/Kotlin/kotlinx.coroutines"),
    "com.meta.spatial": ("LicenseRef-Meta-SDK", "https://developers.meta.com/vr/licenses/oculussdk/"),
    "com.squareup.okhttp3": ("Apache-2.0", "https://github.com/square/okhttp"),
    "com.squareup.okio": ("Apache-2.0", "https://github.com/square/okio"),
    "com.squareup.moshi": ("Apache-2.0", "https://github.com/square/moshi"),
    "com.google.code.gson": ("Apache-2.0", "https://github.com/google/gson"),
}
# Explicitly approved exact Maven artifacts, not their entire publisher groups.
# Both have published Apache-2.0 source/license declarations.
ANDROID_EXACT_POLICY = {
    "com.google.guava:listenablefuture:1.0": (
        "Apache-2.0", "https://github.com/google/guava/tree/master/listenablefuture"),
    "org.jetbrains:annotations:23.0.0": (
        "Apache-2.0", "https://github.com/JetBrains/java-annotations"),
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _source_records(theme):
    if not isinstance(theme, dict) or theme.get("schema") != 1 or not isinstance(theme.get("packages"), list):
        raise ValueError("theme inventory missing")
    records = []
    for item in theme["packages"]:
        if "MPL-2.0" not in item["license"]:
            continue
        name, version = item["name"], item["version"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or not re.fullmatch(r"[A-Za-z0-9.+_-]+", version):
            raise ValueError("invalid MPL package identity")
        expected = f"https://static.crates.io/crates/{name}/{name}-{version}.crate"
        if item.get("source") != expected or item.get("modified") is not False:
            raise ValueError(f"MPL source unavailable or modified: {name} {version}")
        if not re.fullmatch(r"[a-f0-9]{64}", item.get("archive_sha256", "")):
            raise ValueError(f"MPL archive digest missing: {name} {version}")
        if not item.get("licenses"):
            raise ValueError(f"MPL license texts missing: {name} {version}")
        records.append({
            "name": name, "version": version, "license": item["license"],
            "archive_sha256": item["archive_sha256"], "source": expected,
            "modified": False,
        })
    return sorted(records, key=lambda row: (row["name"], row["version"]))


def _body(theme):
    mpl = _source_records(theme)
    rows = [
        "# Source availability for MPL-2.0 dependencies",
        "",
        "These upstream crates are included in the build dependency graph.",
        "A source archive, exact version and SHA-256 are provided for each package.",
        "Some packages (including procedural macros) are build-time only; the list",
        "is deliberately conservative and does not assert each package is linked",
        "into the distributed executable.",
        "",
        "The unmodified archives are available under MPL-2.0 at:",
    ]
    for p in mpl:
        rows.extend([
            "", f"- {p['name']} {p['version']}",
            f"  - Source: {p['source']}",
            f"  - Archive SHA-256: {p['archive_sha256']}",
            "  - Changes: none (original upstream crate)",
        ])
    rows.extend(["", "MPL-2.0: https://www.mozilla.org/en-US/MPL/2.0/", ""])
    return "\n".join(rows).encode("utf-8"), mpl


def assets(theme):
    src, mpl = _body(theme)
    files = {
        "LICENSE-MIT.txt": (ROOT / "LICENSE").read_bytes(),
        "THIRD_PARTY_NOTICES.md": (ROOT / "THIRD_PARTY_NOTICES.md").read_bytes(),
        "Apache-2.0.txt": (ROOT / "ci/licenses/Apache-2.0.txt").read_bytes(),
        "MPL-SOURCES.md": src,
        "META-SDK-NOTICE.txt": (
            "Meta Spatial SDK 0.15.0\n"
            "Copyright \u00a9 Meta Platform Technologies, LLC and its affiliates. All rights reserved.\n"
            "The SDK is NOT licensed under Eidolon Wayland's MIT license.\n"
            "Meta Platform Technologies SDK License Agreement:\n"
            "https://developers.meta.com/vr/licenses/oculussdk/\n"
            "Meta SDK libraries are supplied only as part of this application.\n"
            "Additional third-party materials supplied by Meta may have separate terms.\n"
        ).encode("utf-8"),
        "ANDROID-RUNTIME-LICENSE-POLICY.md": _android_policy_text(),
    }
    if not files["LICENSE-MIT.txt"].startswith(b"MIT License\n"):
        raise ValueError("first-party MIT license missing")
    if b"Apache License" not in files["Apache-2.0.txt"]:
        raise ValueError("Apache-2.0 license text missing")
    index = {
        "schema": 1,
        "scope": "apk-redistribution-notices",
        "assets": {key: digest(value) for key, value in sorted(files.items())},
        "mpl_sources": mpl,
    }
    files["distribution.json"] = canonical(index)
    return files, index


def _android_policy_text():
    lines = [
        "# Android runtime dependency policy",
        "",
        "Generated from the Gradle runtime classpath at build time, not from buildscript",
        "or test dependencies. All resolved modules are recorded in",
        "assets/licenses/android/runtime-modules.json.",
        "",
        "The following group policies are reviewed expectations, NOT a substitute for",
        "checking actual AAR/POM contents, inherited notices or transitive licenses.",
        "Unknown groups cause the APK validation to fail before CI artifact upload.",
        "",
        "| Maven group | Expected license | Upstream reference |",
        "| --- | --- | --- |",
    ]
    for group, (license_id, link) in sorted(ANDROID_POLICY.items()):
        lines.append(f"| {group} | {license_id} | {link} |")
    lines.extend(["", "Specific reviewed Maven coordinates (not a publisher-wide allowlist):", "",
                  "| Maven coordinate | Expected license | Upstream reference |",
                  "| --- | --- | --- |"])
    for coordinate, (license_id, link) in sorted(ANDROID_EXACT_POLICY.items()):
        lines.append(f"| {coordinate} | {license_id} | {link} |")
    lines.extend([
        "", "This APK includes the Apache-2.0 text and a Meta SDK notice separately.",
        "Source distribution and APK release remain subject to the complete",
        "third-party license/notice inventory and SDK distribution terms.",
        "",
    ])
    return "\n".join(lines).encode("utf-8")


def write(directory, theme):
    files, index = assets(theme)
    base = Path(directory) / "licenses/eidolon"
    base.mkdir(parents=True, exist_ok=True)
    for filename, data in files.items():
        (base / filename).write_bytes(data)
    return {"schema": 1, "assets": {
        ASSET_PREFIX + name: digest(data) for name, data in sorted(files.items())
    }}


def validate(archive, stored, theme):
    files, _ = assets(theme)
    expected = {ASSET_PREFIX + name: data for name, data in files.items()}
    if stored != {"schema": 1, "assets": {name: digest(data) for name, data in sorted(expected.items())}}:
        raise ValueError("license compliance inventory mismatch")
    names = archive.namelist()
    if {name for name in names if name.startswith(ASSET_PREFIX)} != set(expected):
        raise ValueError("APK license assets missing, duplicated, or unexpected")
    for name, data in expected.items():
        if names.count(name) != 1 or archive.read(name) != data:
            raise ValueError(f"APK license asset missing or altered: {name}")
    return len(_source_records(theme))


def _policy(group, name, version):
    exact = ANDROID_EXACT_POLICY.get(f"{group}:{name}:{version}")
    if exact is not None:
        return exact
    matches = [(key, value) for key, value in ANDROID_POLICY.items()
               if group == key or group.startswith(key + ".")]
    if not matches:
        return None
    return sorted(matches, key=lambda item: len(item[0]), reverse=True)[0][1]


def validate_android(archive, profile):
    names = archive.namelist()
    if names.count(ANDROID_MANIFEST) != 1:
        raise ValueError("Android runtime dependency manifest missing/duplicated")
    data = archive.read(ANDROID_MANIFEST)
    if len(data) > 512 * 1024:
        raise ValueError("Android runtime dependency manifest too large")
    report = json.loads(data)
    if set(report) != {"schema", "variant", "modules"} or report["schema"] != 1 or report["variant"] != profile:
        raise ValueError("Android runtime dependency manifest schema/variant mismatch")
    modules = report["modules"]
    if not isinstance(modules, list) or not modules or len(modules) > 1000:
        raise ValueError("Android runtime modules absent or excessive")
    coordinates = []
    unknown = []
    meta = set()
    for module in modules:
        if not isinstance(module, dict) or set(module) != {"group", "name", "version"}:
            raise ValueError("Android runtime dependency record malformed")
        group, name, version = (module[key] for key in ("group", "name", "version"))
        if not all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.+-]+", value)
                   for value in (group, name, version)):
            raise ValueError("Android runtime coordinate malformed")
        coordinates.append(f"{group}:{name}:{version}")
        if _policy(group, name, version) is None:
            unknown.append(coordinates[-1])
        if group == "com.meta.spatial":
            meta.add(name)
    if coordinates != sorted(set(coordinates)):
        raise ValueError("Android runtime modules must be unique and sorted")
    if unknown:
        raise ValueError("Android runtime dependency license requires review: " + ", ".join(unknown))
    if META_MODULES - meta:
        raise ValueError("Meta Spatial SDK runtime dependencies are incomplete")
    return len(modules)
