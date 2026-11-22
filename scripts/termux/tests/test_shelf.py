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

    def test_tombstone_requires_matching_user_launch_directory_and_unique_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            launch = home / ".local/state/eidolon/freedoom-gui/0123456789abcdef/1"
            launch.mkdir(parents=True)
            wad = home / ".local/share/eidolon/freedoom/0.13.0/assets-id/assets/freedoom1.wad"
            prefix = "1234567890.123 21 21 F DEBUG : "
            body = ["*** *** ***", f"uid: {os.getuid()}", "signal 11 (SIGSEGV), code 1",
                    f"Cmdline: {home.parent}/usr/bin/chocolate-doom -iwad {wad} -config {launch}/default.cfg",
                    "#00 pc 000000000003bcfc  /private/path/to/libEGL_mesa.so"]
            report, = shelf["crash_records"]([prefix + line for line in body])
            app = dict(id="freedoom", execution_profile=dict(kind="freedoom_phase1_software", assets_run_id="assets-id"))
            match = shelf["match_crash"]
            result = match(home, [app], report)
            self.assertEqual(result["frames"], [["00", "libEGL_mesa.so", "000000000003bcfc"]])
            self.assertNotIn(str(home), json.dumps(result))
            self.assertFalse(result["render_present"])
            self.assertIsNone(match(home, [app, dict(app, id="another")], report))
            self.assertIsNone(match(home, [app], dict(report, own=False)))
            self.assertIsNone(match(home, [app], dict(report, argv=report["argv"][:-1] + ["/elsewhere/default.cfg"])))
