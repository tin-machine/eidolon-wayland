"""Execute the actual snapshot loader with existing reviewed B1/B2 sources.

This extracts the loader AST from the product bootstrap; it does not copy/fork
its implementation, invent a C2 helper, run main(), publish pins, or lift the
complete-source gate. Whole bootstrap/real helper acceptance remains pending.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "native/transport/src/linux_desktop/v3/bootstrap.py"
INVENTORY = ROOT / "scripts/linux-desktop-v3-sources.json"
GOLDEN = ROOT / "scripts/tests/fixtures/linux-desktop-v3-contract.json"

# Test harness only. The PinnedLoader, fixed_spec and loader-installation code
# are selected unchanged from the product AST passed through stdin.
HARNESS = r'''
import ast, hashlib, importlib, importlib.util, json, os, pathlib, subprocess, sys
payload = json.load(sys.stdin)
tree = ast.parse(payload["bootstrap"], filename="product-bootstrap.py")
starts = [i for i, node in enumerate(tree.body)
          if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "modules" for t in node.targets)]
ends = [i for i, node in enumerate(tree.body)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Attribute) and
            ast.unparse(t) == "importlib.util.spec_from_file_location" for t in node.targets)]
assert len(starts) == len(ends) == 1 and starts[0] < ends[0]
section = tree.body[starts[0]:ends[0] + 1]
assert {node.name for node in section if isinstance(node, (ast.ClassDef, ast.FunctionDef))} == {"PinnedLoader", "fixed_spec"}
assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in section)
prefix = []
for node in tree.body:
    if not isinstance(node, (ast.Import, ast.ImportFrom)):
        break
    prefix.append(node)
require = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "require"]
assert len(require) == 1
selected = ast.Module(body=prefix + require + section, type_ignores=[])
snapshot = {name: bytes.fromhex(data) for name, data in payload["snapshot"].items()}
assert all(hashlib.sha256(data).hexdigest() == payload["source_hashes"][name] for name, data in snapshot.items())
assert "eidolon_linux_desktop_session_v3.py" not in snapshot
assert "eidolon_linux_desktop_v3.py" not in snapshot
assert "eidolon_linux_real_app.py" not in snapshot
bundle = pathlib.Path(payload["virtual_bundle"])
assert not bundle.exists()
scope = {"bundle": bundle, "snapshot": snapshot, "source_hashes": payload["source_hashes"]}
exec(compile(selected, "product-bootstrap-loader.py", "exec"), scope)
loader = scope["loader"]
def no_process(*args, **kwargs):
    raise AssertionError("loader unit test must not start an application/process")
subprocess.Popen = no_process
os.system = no_process
os.execve = no_process
if hasattr(os, "posix_spawn"):
    os.posix_spawn = no_process
if hasattr(os, "fork"):
    os.fork = no_process

case = payload["case"]
if case == "transitive_missing":
    try:
        importlib.import_module("linux_desktop_v3_contract")
    except ImportError as error:
        assert str(error) == "DESKTOP_V3_UNPINNED_IMPORT", str(error)
    else:
        raise AssertionError("missing B1 manifest imported through external sys.path")
else:
    C = importlib.import_module("linux_desktop_v3_contract")
    M = importlib.import_module("linux_real_app_manifest")
    B = importlib.import_module("linux_desktop_v3_bundle")
    W = importlib.import_module("linux_desktop_v3_wire")
    I = importlib.import_module("linux_real_app_inspect")
    base = importlib.import_module("eidolon_linux_session")
    owner = importlib.import_module("eidolon_linux_process_owner")
    for module, name in ((C, "linux_desktop_v3_contract.py"), (M, "linux_real_app_manifest.py"),
                         (B, "linux_desktop_v3_bundle.py"), (W, "linux_desktop_v3_wire.py"),
                         (I, "linux_real_app_inspect.py"), (base, "eidolon-linux-session.py"),
                         (owner, "eidolon_linux_process_owner.py")):
        assert module.__spec__.loader is loader
        assert module.__file__ == str(bundle / name)
    assert I._manifest.__spec__.loader is loader
    assert I._manifest.__file__ == str(bundle / "linux_real_app_manifest.py")
    assert I._manifest.MANIFEST_FIELDS == M.MANIFEST_FIELDS
    if case == "golden":
        records = {item["id"]: item for item in payload["golden"]["records"]}
        values = {}
        for name, cls in (("pins", C.Pins), ("manifest", C.Manifest), ("context", C.Context), ("selection", C.Selection)):
            item = records[name]
            value = cls.parse(item["raw"].encode())
            assert value.canonical() == item["canonical_utf8"].encode()
            assert value.sha256() == item["sha256"]
            values[name] = value
        values["manifest"].matches_pins(values["pins"])
        values["selection"].matches(values["context"], values["pins"])
        assert I.validate_manifest(values["manifest"].record()) == values["manifest"].record()
        W.Binding(values["context"], values["pins"])
        assert base.REVISION == "eidolon-linux-shm-v1"
        inventory = B.inventory(payload["inventory"].encode())
        try:
            B.require_integrated(inventory)
        except B.BundleError as error:
            assert str(error) == "BUNDLE_INTEGRATION_INCOMPLETE"
        else:
            raise AssertionError("unit test unexpectedly enabled incomplete product sources")
    elif case == "reject":
        for name in ("untrusted_site_policy", "eidolon_linux_desktop_session_v3", "eidolon_linux_real_app",
                     "linux_desktop_v3_contract.unpinned"):
            try:
                importlib.import_module(name)
            except (ImportError, ModuleNotFoundError):
                pass
            else:
                raise AssertionError("unpinned module accepted: " + name)
        for location in (str(bundle.parent / "outside.py"), str(bundle / "unknown.py"),
                         str(bundle / "linux_desktop_v3_contract.pyc"), str(bundle / "../outside.py")):
            try:
                importlib.util.spec_from_file_location("untrusted_alias", location)
            except (ImportError, SystemExit):
                pass
            else:
                raise AssertionError("outside/unpinned/pyc source accepted")
        try:
            importlib.util.spec_from_file_location("alias", str(bundle / "linux_real_app_manifest.py"), loader=loader)
        except SystemExit:
            pass
        else:
            raise AssertionError("explicit arbitrary loader argument accepted")
        # Ordinary standard-library imports still use the system interpreter.
        decimal = importlib.import_module("decimal")
        assert str(decimal.Decimal("1.5") + decimal.Decimal("2.5")) == "4.0"
    else:
        raise AssertionError("unknown loader test case")
assert not bundle.exists()
print(json.dumps({"case": case, "loader_verified": True, "product_integration": False,
                  "application_executed": False}, sort_keys=True))
'''


class BootstrapSnapshotLoaderTests(unittest.TestCase):
    def execute(self, case, *, missing=()):
        inventory_raw = INVENTORY.read_text()
        spec = json.loads(inventory_raw)
        snapshot = {name: (ROOT / path).read_bytes() for name, path in spec["sources"].items()
                    if name.endswith(".py") and name not in missing and (ROOT / path).is_file()}
        self.assertNotIn("eidolon_linux_desktop_session_v3.py", snapshot)
        with tempfile.TemporaryDirectory() as temporary:
            payload = {"bootstrap": BOOTSTRAP.read_text(), "snapshot": {name: data.hex() for name, data in snapshot.items()},
                       "source_hashes": {name: hashlib.sha256(data).hexdigest() for name, data in snapshot.items()},
                       "inventory": inventory_raw, "golden": json.loads(GOLDEN.read_bytes()),
                       "virtual_bundle": str(Path(temporary) / "not-created-immutable-bundle"), "case": case}
            result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", HARNESS],
                                    input=json.dumps(payload).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=20, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(json.loads(result.stdout), {"case": case, "loader_verified": True,
                         "product_integration": False, "application_executed": False})

    def test_actual_pinned_loader_imports_real_b1_b2_inspector_and_base_alias(self):
        self.execute("golden")

    def test_actual_loader_rejects_unpinned_external_pyc_and_helper_imports(self):
        self.execute("reject")

    def test_missing_real_b1_dependency_never_falls_back_to_checkout_or_site(self):
        self.execute("transitive_missing", missing=("linux_real_app_manifest.py",))


if __name__ == "__main__":
    unittest.main()
