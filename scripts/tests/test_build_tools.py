from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class WrapperTests(unittest.TestCase):
    def fixture(self, folder):
        root = Path(folder)
        (root / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts/check-build-tools", root / "scripts/check-build-tools")
        shutil.copytree(ROOT / "ci", root / "ci")
        shutil.copytree(ROOT / "android/gradle/wrapper", root / "android/gradle/wrapper")
        return root

    def test_modified_wrapper_jar_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            jar = root / "android/gradle/wrapper/gradle-wrapper.jar"
            jar.write_bytes(jar.read_bytes() + b"corrupt fixture")
            result = subprocess.run(["python3", root / "scripts/check-build-tools"], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("JAR", result.stderr)

    def test_distribution_checksum_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            properties = root / "android/gradle/wrapper/gradle-wrapper.properties"
            properties.write_text(properties.read_text().replace("distributionSha256Sum=2", "distributionSha256Sum=0"))
            result = subprocess.run(["python3", root / "scripts/check-build-tools"], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("distribution", result.stderr)
