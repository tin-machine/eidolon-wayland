#!/usr/bin/env python3
"""Read-only, on-demand Python structural analysis; see docs/source-structure-deep-analysis.md."""
import sys

# Analysis must not create bytecode beside the source it inspects.
sys.dont_write_bytecode = True

import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
from pathlib import Path


__deep_source_sha256__ = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class _SourceOnlyLoader(importlib.machinery.SourceFileLoader):
    """Do not execute timestamp-valid pyc beside a changed generator source.

    -B/dont_write_bytecode prevents writes, not reads. Hash the exact bytes we
    compile, and expose that identity before execution so repository.py can
    check itself even during import. This loader never handles target sources.
    """

    def get_code(self, fullname):
        source = self.get_data(self.path)
        self.source_sha256 = hashlib.sha256(source).hexdigest()
        return compile(source, self.path, "exec", dont_inherit=True, optimize=0)

    def exec_module(self, module):
        code = self.get_code(module.__name__)
        module.__deep_source_sha256__ = self.source_sha256
        exec(code, module.__dict__)


class _GeneratorFinder(importlib.abc.MetaPathFinder):
    def __init__(self):
        directory = Path(__file__).resolve().with_suffix("")
        self.sources = {
            "deep_structure": directory / "__init__.py",
            "deep_structure.python_analysis": directory / "python_analysis.py",
            "deep_structure.repository": directory / "repository.py",
        }

    def find_spec(self, fullname, path=None, target=None):
        source = self.sources.get(fullname)
        if source is None:
            return None
        package_paths = [str(source.parent)] if fullname == "deep_structure" else None
        return importlib.util.spec_from_file_location(
            fullname, source, loader=_SourceOnlyLoader(fullname, str(source)),
            submodule_search_locations=package_paths)


# The supported entry point is this CLI in a new process. Restrict the importer
# to these three own modules, and restore ordinary import handling afterwards.
_finder = _GeneratorFinder()
sys.meta_path.insert(0, _finder)
try:
    from deep_structure.repository import main
finally:
    sys.meta_path.remove(_finder)

if __name__ == "__main__":
    raise SystemExit(main())
