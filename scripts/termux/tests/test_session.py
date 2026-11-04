import importlib.util
import concurrent.futures
from pathlib import Path
import socket
import tempfile
import threading
import unittest

spec = importlib.util.spec_from_file_location("launcher", Path(__file__).resolve().parents[1] / "eidolon-session.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class SessionTests(unittest.TestCase):
    def test_dangling_receipt_blocks_nonce_reuse_without_creating_directory(self):
        with tempfile.TemporaryDirectory(prefix="eidolon-launcher-") as value:
            home, nonce = Path(value), "c" * 16
            root = home / ".eidolon"
            root.mkdir(mode=0o700)
            receipt = root / ("r-" + nonce + ".json")
            receipt.symlink_to("missing-receipt")
            with self.assertRaises(FileExistsError):
                launcher.prepare(home, nonce)
            self.assertTrue(receipt.is_symlink())
            self.assertFalse((root / ("s-" + nonce)).exists())

    def test_exclusive_prepare_permissions_socket_and_idempotent_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="eidolon-launcher-") as value:
            home = Path(value)
            nonce = "a" * 16
            result = launcher.prepare(home, nonce)
            with self.assertRaises(FileExistsError):
                launcher.prepare(home, nonce)
            directory = Path(result["socket"]).parent
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual((directory / "owner.json").stat().st_mode & 0o777, 0o600)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.bind(result["socket"])
                self.assertTrue(launcher.cleanup(home, nonce, creator=True)["cleaned"])
            self.assertTrue(launcher.cleanup(home, nonce, creator=True)["already_absent"])

    def test_rejects_paths_symlinks_marker_changes_and_unowned_contents(self):
        with tempfile.TemporaryDirectory(prefix="eidolon-launcher-") as value:
            home = Path(value)
            for nonce in ["../escape", "A" * 16, "a" * 17, "a" * 15, "a;id" * 4]:
                with self.assertRaises(ValueError):
                    launcher.prepare(home, nonce)
            (home / "target").mkdir(mode=0o700)
            (home / ".eidolon").symlink_to(home / "target")
            with self.assertRaises(ValueError):
                launcher.prepare(home, "a" * 16)
            (home / ".eidolon").unlink()
            result = launcher.prepare(home, "a" * 16)
            directory = Path(result["socket"]).parent
            (directory / "p").symlink_to(home / "target")
            with self.assertRaises(ValueError):
                launcher.cleanup(home, "a" * 16, creator=True)
            (directory / "p").unlink()
            (directory / "unrelated").write_text("preserve")
            with self.assertRaises(ValueError):
                launcher.cleanup(home, "a" * 16, creator=True)
            self.assertTrue((directory / "owner.json").exists())
            (directory / "unrelated").unlink()
            (directory / "owner.json").write_text('{"nonce":"other"}')
            with self.assertRaises(ValueError):
                launcher.cleanup(home, "a" * 16, creator=True)

    def test_cli_rejects_arbitrary_modes_and_extra_arguments(self):
        for args in [["shell", "a" * 16], ["echo", "a" * 16, "id"], []]:
            with self.assertRaises(ValueError):
                launcher.main(args)

    def test_echo_sizes_hashes_four_simultaneous_channels_and_half_close(self):
        with tempfile.TemporaryDirectory(prefix="eidolon-launcher-") as value:
            home, nonce = Path(value), "b" * 16
            path = launcher.prepare(home, nonce)["socket"]
            barrier = threading.Barrier(4)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(path)
                listener.listen(8)
                listener.settimeout(10)

                def serve():
                    def copy(peer, index):
                        with peer:
                            peer.settimeout(10)
                            if index >= 2:
                                barrier.wait(timeout=10)
                            while block := peer.recv(65536):
                                peer.sendall(block)
                            peer.sendall(launcher.TRAILER)
                    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                        futures = [pool.submit(copy, listener.accept()[0], i) for i in range(6)]
                        for future in futures:
                            future.result(timeout=15)

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    server = pool.submit(serve)
                    result = launcher.echo(home, nonce)
                    server.result(timeout=15)
                self.assertEqual(result["channels"], 6)
                self.assertEqual(result["bytes"], sum(launcher.SIZES))
                self.assertEqual([row["size"] for row in result["copies"]], list(launcher.SIZES))
                self.assertTrue(all(row["tail_matched"] and len(row["sha256"]) == 64 for row in result["copies"]))
            self.assertTrue(launcher.cleanup(home, nonce, creator=True)["cleaned"])


if __name__ == "__main__":
    unittest.main()
