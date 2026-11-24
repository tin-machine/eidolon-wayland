"""Separate-process C2 session ownership fixture; not a product source."""
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts/linux"))

import linux_desktop_v3_contract as C
import eidolon_linux_desktop_v3 as core

if len(sys.argv) not in (3, 4):
    raise SystemExit(2)
home, raw = sys.argv[1:3]
context = C.Context.parse(raw.encode())
core.pwd.getpwuid = lambda uid: SimpleNamespace(pw_uid=uid, pw_dir=home)
if len(sys.argv) == 4:
    import eidolon_linux_desktop_session_v3 as session
    bootstrap = SimpleNamespace(context=context, pins=C.Pins.parse(sys.argv[3].encode()))
    sources = {"linux_desktop_v3_contract.py": "1" * 64}
    raise SystemExit(session.serve(bootstrap, None, sources, admission=True))
try:
    core.prepare_session(context)
except core.HelperError as error:
    print(error.public_reason)
    raise SystemExit(3)
raise SystemExit(4)
