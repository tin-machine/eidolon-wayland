"""Explicit non-root fixture context; never install a package in these tests."""
import importlib.machinery
import importlib.util
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
loader = importlib.machinery.SourceFileLoader("real_app_provision_context", str(ROOT / "scripts/ci/prepare-linux-real-app-fixture"))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)


class ProvisionContextTests(unittest.TestCase):
    def test_local_host_never_implicitly_provisions_and_container_is_explicit(self):
        with mock.patch.dict(module.os.environ, {}, clear=True), mock.patch.object(module.sys, "platform", "linux"), \
             mock.patch.object(module.os, "getuid", return_value=1000), mock.patch.object(module.os, "geteuid", return_value=1000):
            with mock.patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0)):
                self.assertFalse(module.provisioning_allowed(False))
                self.assertTrue(module.provisioning_allowed(True))
            for mode, uid in [(stat.S_IFLNK | 0o777, 0), (stat.S_IFREG | 0o666, 0), (stat.S_IFREG | 0o644, 1000)]:
                with mock.patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=mode, st_uid=uid)):
                    self.assertFalse(module.provisioning_allowed(True))
            with mock.patch.object(Path, "lstat", side_effect=FileNotFoundError):
                self.assertFalse(module.provisioning_allowed(True))

    def test_root_or_uid_mismatch_is_not_allowed_even_in_ci(self):
        with mock.patch.dict(module.os.environ, {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Linux"}, clear=True), \
             mock.patch.object(module.sys, "platform", "linux"):
            for real, effective, expected in [(1000,1000,True),(0,0,False),(1000,0,False),(1000,1001,False)]:
                with mock.patch.object(module.os, "getuid", return_value=real), mock.patch.object(module.os, "geteuid", return_value=effective):
                    self.assertEqual(module.provisioning_allowed(False), expected)


if __name__ == "__main__":
    unittest.main()
