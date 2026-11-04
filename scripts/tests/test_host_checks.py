import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).parents[1]))
from host_checks import check_members, rust_test_count


class GateTests(unittest.TestCase):
    def test_compile_or_zero_tests_cannot_be_reported_as_test_success(self):
        for output in ["Finished dev profile", "test result: ok. 0 passed; 0 failed;", "running 0 tests"]:
            with self.assertRaises(ValueError):
                rust_test_count(output)
        self.assertEqual(rust_test_count("test result: ok. 2 passed; 0 failed;\ntest result: ok. 0 passed; 0 failed;"), 2)

    def test_new_crate_requires_explicit_suite_registration(self):
        check_members(["model", "core"], ["model", "core"])
        with self.assertRaises(ValueError):
            check_members(["model", "core", "transport"], ["model", "core"])
