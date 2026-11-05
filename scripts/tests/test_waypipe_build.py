"""既存の展開cacheを未記録のsource変更として使わないことを検証する。"""
import hashlib
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import waypipe_build


class SourceTests(unittest.TestCase):
    def test_source_changes_extra_files_and_archive_corruption_are_preserved_and_rejected(self):
        with tempfile.TemporaryDirectory(prefix="eidolon-waypipe-source-") as folder:
            root = Path(folder)
            (root / "native/licenses").mkdir(parents=True)
            (root / "native/licenses/fixture").write_bytes(b"fixture license")
            cache = root / "cache"
            cache.mkdir()
            archive = root / "fixture.tar"
            with tarfile.open(archive, "w") as tar:
                for name, data in [("LICENSE", b"fixture license"), ("code.c", b"original source")]:
                    member = tarfile.TarInfo("fixture/" + name)
                    member.size = len(data)
                    tar.addfile(member, io.BytesIO(data))
            spec = dict(version="1", source=archive.as_uri(), sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                        license_source="LICENSE", license_file="licenses/fixture", patches=[])
            with patch.object(waypipe_build, "ROOT", root):
                source, fingerprint = waypipe_build.prepare("fixture", spec, cache)
                self.assertEqual(waypipe_build.prepare("fixture", spec, cache), (source, fingerprint))
                (source / "code.c").write_bytes(b"unrecorded change")
                with self.assertRaisesRegex(RuntimeError, "differs"):
                    waypipe_build.prepare("fixture", spec, cache)
                self.assertEqual((source / "code.c").read_bytes(), b"unrecorded change")
                (source / "code.c").write_bytes(b"original source")
                (source / "extra.c").write_bytes(b"extra source")
                with self.assertRaisesRegex(RuntimeError, "differs"):
                    waypipe_build.prepare("fixture", spec, cache)
                (source / "extra.c").unlink()
                cached_archive = cache / "fixture-1.tar"
                cached_archive.write_bytes(b"corrupt archive")
                with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
                    waypipe_build.prepare("fixture", spec, cache)
                self.assertEqual(cached_archive.read_bytes(), b"corrupt archive")
