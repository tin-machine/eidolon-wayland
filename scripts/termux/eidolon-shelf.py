"""Read the paired account's launcher entries without starting a Wayland client."""
import configparser
import hashlib
import json
import os
from pathlib import Path
import sys


def desktop_rows(roots):
    seen = set()
    for root in roots:
        applications = root / "applications"
        for path in sorted(applications.rglob("*.desktop")):
            desktop_id = "-".join(path.relative_to(applications).parts)
            if desktop_id in seen:
                continue
            seen.add(desktop_id)
            config = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                if not path.is_file() or path.stat().st_size > 65536:
                    continue
                config.read(path, encoding="utf-8")
                entry = config["Desktop Entry"]
                if entry.get("Type") != "Application" or any(
                    entry.get(key, "").lower() == "true" for key in ("Hidden", "NoDisplay")
                ):
                    continue
                name = entry.get("Name[ja]") or entry["Name"]
                if not name.strip() or len(name.encode()) > 255 or not name.isprintable():
                    continue
            except (KeyError, UnicodeError, OSError, configparser.Error):
                continue
            yield dict(id="xdg-" + hashlib.sha256(desktop_id.encode()).hexdigest()[:40],
                       label=name, icon="builtin:script", available=False,
                       error="GUI_NOT_VERIFIED", desktop_id=desktop_id)


def main(path, expected_hash):
    home = Path.home()
    helper = home / ".local/lib/eidolon/eidolon-session.py"
    source = helper.read_bytes()
    if hashlib.sha256(source).hexdigest() != expected_hash:
        raise ValueError("helper_version")
    scope = {"__name__": "eidolon_launcher", "__file__": str(helper)}
    exec(compile(source, str(helper), "exec"), scope)
    catalog = scope["LauncherCatalog"](home)
    try:
        catalog.reload(path)
    except (FileNotFoundError, scope["CatalogError"]) as exc:
        if not isinstance(exc, FileNotFoundError) and str(exc) != "PATH_MISSING":
            raise
    entries = [catalog.summary(app) for app in catalog.apps]
    prefix = Path(os.environ.get("PREFIX", "/data/data/com.termux/files/usr"))
    roots = [Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))]
    roots.extend(Path(item) for item in os.environ.get("XDG_DATA_DIRS", str(prefix / "share")).split(":") if item)
    entries.extend(desktop_rows(roots))
    if len(entries) > 2048:
        raise ValueError("catalog_limit")
    print(json.dumps(dict(revision=catalog.revision, builtin=catalog.path is None, entries=entries)))


if __name__ == "__main__":
    main(bytes.fromhex(sys.argv[1]).decode(), sys.argv[2])
