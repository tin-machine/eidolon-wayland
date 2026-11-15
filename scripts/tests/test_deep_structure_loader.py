"""Regression for stale generator pyc; -B alone does not prohibit pyc reads."""

import hashlib
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]


class SourceOnlyLoaderTests(unittest.TestCase):
    def test_cli_bypasses_same_size_same_mtime_pyc_and_pins_loaded_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "deep_structure.py"
            script.write_bytes((REPO / "scripts/deep_structure.py").read_bytes())
            package = root / "deep_structure"
            package.mkdir()
            contents = {
                "__init__.py": 'VALUE = "old_package"\n',
                "python_analysis.py": 'VALUE = "old_core"\n',
                "repository.py": '''import json
import sys
from . import python_analysis
VALUE = "old_repository"
def main():
    names = ["deep_structure", "deep_structure.python_analysis", "deep_structure.repository"]
    print(json.dumps({"values": [sys.modules[n].VALUE for n in names],
                      "loaded": {n: sys.modules[n].__deep_source_sha256__ for n in names}}))
    return 0
''',
            }
            hashes = {}
            for filename, old in contents.items():
                path = package / filename
                path.write_text(old)
                before = path.stat()
                py_compile.compile(str(path), doraise=True,
                                   invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
                new = old.replace("old_", "new_")
                self.assertEqual(len(new.encode()), len(old.encode()))
                path.write_text(new)
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
                name = "deep_structure" if filename == "__init__.py" else "deep_structure." + path.stem
                hashes[name] = hashlib.sha256(new.encode()).hexdigest()
            # Confirm this is an effective stale-pyc fixture: ordinary -B still
            # executes the old source despite the new source bytes on disk.
            ordinary = subprocess.check_output(
                [sys.executable, "-B", "-c", "import deep_structure.python_analysis as p; print(p.VALUE)"],
                cwd=root, text=True)
            self.assertEqual(ordinary.strip(), "old_core")
            before = {str(path.relative_to(root)): path.read_bytes()
                      for path in root.rglob("*") if path.is_file()}
            output = subprocess.check_output([sys.executable, str(script)], cwd=root, text=True)
            result = json.loads(output)
            self.assertEqual(result["values"], ["new_package", "new_core", "new_repository"])
            self.assertEqual(result["loaded"], hashes)
            after = {str(path.relative_to(root)): path.read_bytes()
                     for path in root.rglob("*") if path.is_file()}
            self.assertEqual(before, after, "the CLI must not change source or cached bytecode")


if __name__ == "__main__":
    unittest.main()
