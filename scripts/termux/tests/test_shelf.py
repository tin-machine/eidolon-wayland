import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
shelf = runpy.run_path(str(ROOT / "eidolon-shelf.py"))


class TermuxShelfTest(unittest.TestCase):
    def test_installed_and_registered_without_starting_a_program(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            helper = home / ".local/lib/eidolon/eidolon-session.py"
            helper.parent.mkdir(parents=True)
            helper.write_bytes((ROOT / helper.name).read_bytes())
            system = home / "prefix/share"
            for directory in [home / ".local/share", system]:
                (directory / "applications").mkdir(parents=True)
            menu = system / "applications"
            (menu / "editor.desktop").write_text("[Desktop Entry]\nType=Application\nName=GUI Editor\nExec=false\n")
            (menu / "hidden.desktop").write_text("[Desktop Entry]\nType=Application\nName=hidden\n")
            (home / ".local/share/applications/hidden.desktop").write_text("[Desktop Entry]\nHidden=true\n")
            buffer = io.StringIO()
            with patch.dict(os.environ, {"HOME": str(home), "XDG_DATA_HOME": str(home / ".local/share"),
                                         "XDG_DATA_DIRS": str(system)}), contextlib.redirect_stdout(buffer):
                shelf["main"](".config/eidolon/launcher.toml", hashlib.sha256(helper.read_bytes()).hexdigest())
            result = json.loads(buffer.getvalue())
            self.assertTrue(result["builtin"])
            self.assertEqual([r["label"] for r in result["entries"]], ["ターミナル", "エディター", "GUI Editor"])
            self.assertEqual(result["entries"][-1]["error"], "GUI_NOT_VERIFIED")
