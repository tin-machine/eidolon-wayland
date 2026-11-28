import base64
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
loader = SourceFileLoader("eidolonctl_cli", str(SCRIPTS / "eidolonctl"))
cli = module_from_spec(spec_from_loader(loader.name, loader))
loader.exec_module(cli)


class EidolonCtlTest(unittest.TestCase):
    nonce = "a" * 32
    windows = [{"id": "1:2:3:4", "generation": "5", "visible": True, "width": 800, "height": 600}]

    def broadcast(self, *, nonce=None, schema=1, code=0, status="ok", **result):
        data = json.dumps({
            "schema": schema,
            "nonce": self.nonce if nonce is None else nonce,
            "status": status,
            "windows": self.windows,
            **result,
        }).encode()
        encoded = base64.urlsafe_b64encode(data).rstrip(b"=").decode()
        return f'Broadcasting: Intent\nBroadcast completed: result={code}, data="{encoded}"\n'

    def test_verified_ordered_broadcast_and_explicit_adb_target(self):
        reply = subprocess.CompletedProcess([], 0, self.broadcast(), "")
        with patch.object(cli.secrets, "token_hex", return_value=self.nonce), patch.object(
            cli.subprocess, "run", return_value=reply,
        ) as adb:
            self.assertEqual(cli.list_windows("/opt/android-sdk/platform-tools/adb", "known-target"), self.windows)
        argv = adb.call_args.args[0]
        self.assertEqual(argv[:4], ["/opt/android-sdk/platform-tools/adb", "-s", "known-target", "shell"])
        self.assertIn("am broadcast -n io.github.tinmachine.eidolon/.EidolonCtlReceiver", argv[4])
        self.assertTrue(argv[4].endswith(f"--es nonce {self.nonce}"))

    def test_stale_nonce_and_missing_debug_receiver_are_rejected(self):
        for output in (
            self.broadcast(nonce="b" * 32),
            self.broadcast(schema=2),
            self.broadcast(code=1, status="runtime_unavailable"),
            "Broadcast completed: result=0\n",
            self.broadcast() + self.broadcast(),
        ):
            with self.subTest(output=output), self.assertRaises(ValueError):
                cli.decode_list(output, self.nonce)

    def test_show_waits_for_the_requested_generation_to_be_visible(self):
        args = SimpleNamespace(adb="adb", serial="known-target", window_id="1:2:3:4", generation="5")
        broadcast = (self.broadcast(show="requested"), self.nonce)
        hidden = [dict(self.windows[0], visible=False)]
        with patch.object(cli, "broadcast", return_value=broadcast) as sender, patch.object(
            cli, "list_windows", side_effect=[hidden, self.windows],
        ), patch.object(cli.time, "sleep"):
            self.assertEqual(cli.show_window(args), {"show": "shown", "window": self.windows[0]})
        sender.assert_called_once_with("adb", "known-target", "SHOW", {"window": "1:2:3:4", "generation": "5"})
        replaced = [dict(self.windows[0], generation="6")]
        with patch.object(cli, "broadcast", return_value=broadcast), patch.object(
            cli, "list_windows", return_value=replaced,
        ), self.assertRaisesRegex(ValueError, "stale"):
            cli.show_window(args)
