"""Contract tests for path-sensitive CI. Unknowns must execute all jobs."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ci"))
import ci_scope


class ClassifierTests(unittest.TestCase):
    def assert_jobs(self, paths, want):
        self.assertEqual(ci_scope.classify(paths), set(want))

    def test_docs_only(self):
        self.assert_jobs(["README.md", ".github/SECURITY.md",
                          "docs/architecture.md", "docs/oss-publication-github-security-settings-20261113.md"], [])

    def test_feature_manifests_are_docs_only(self):
        self.assert_jobs(["docs/features/linux-desktop-v3/manifest.yaml"], [])
        self.assert_jobs(["docs/features/documentation-system/manifest.yaml",
                          "docs/features/linux-desktop-v3/README.md"], [])

    def test_manifest_allowlist_is_exact_and_no_fixtures_are_skipped(self):
        for path in (
            "docs/features/manifest.yaml",
            "docs/features/v3/nested/manifest.yaml",
            "docs/features/v3/tasks.yaml",
            "docs/features/v3/fixture.json",
            "docs/knowledge/schemas/document-metadata.schema.json",
            "docs/knowledge/schemas/feature-manifest.schema.json",
            "docs/knowledge/fixture.yaml",
            "docs/knowledge/inventory-20261113.csv",
            "AGENTS.md",
        ):
            with self.subTest(path=path):
                self.assert_jobs([path], ci_scope.ALL)
        # Preserve the existing legacy Phase-0–6 contract: docs/design/** -> host.
        self.assert_jobs(["docs/design/protocol/manifest.yaml"], ["host"])

    def test_manifest_and_product_change_does_not_hide_build(self):
        self.assert_jobs(["docs/features/v3/manifest.yaml", "native/core/src/main.rs"],
                         ci_scope.ALL)
        self.assert_jobs(["docs/features/v3/manifest.yaml", "android/app/build.gradle.kts"],
                         ["android"])

    def test_renamed_build_input_into_manifest_keeps_build_gate(self):
        paths = ci_scope.decode_name_status(
            bytes([0]).join([
                b"R100", b"native/old.rs",
                b"docs/features/v3/manifest.yaml", b"",
            ])
        )
        self.assert_jobs(paths, ci_scope.ALL)

    def test_design_docs_have_host_check(self):
        self.assert_jobs(["docs/design/contracts.md", "docs/design/tasks.json"], ["host"])

    def test_fixtures_under_docs_are_build_inputs(self):
        self.assert_jobs(["docs/curved-workspace/fixtures/geometry-v1.json"], ci_scope.ALL)

    def test_android_only(self):
        self.assert_jobs(["android/app/src/main/java/App.kt"], ["android"])

    def test_gradle_wrapper_requires_host_check(self):
        self.assert_jobs(["android/gradle/wrapper/gradle-wrapper.jar"], ["android", "host"])

    def test_check_workflow(self):
        self.assert_jobs([".github/workflows/check.yml"], ["host", "android"])

    def test_l7_workflow_includes_actionlint(self):
        self.assert_jobs([".github/workflows/linux-real-app.yml"],
                         ["host", "settings", "mousepad"])

    def test_native_and_scripts_are_shared(self):
        for path in ("native/core/src/main.rs", "scripts/ci/ci_scope.py",
                     "scripts/tests/test_ci_scope.py", "Cargo.lock", "ci/toolchains.lock.json"):
            with self.subTest(path=path):
                self.assert_jobs([path], ci_scope.ALL)

    def test_notices_are_build_inputs(self):
        for path in ("LICENSE", "THIRD_PARTY_NOTICES.md", "ci/licenses/Apache-2.0.txt"):
            with self.subTest(path=path):
                self.assert_jobs([path], ci_scope.ALL)

    def test_mixed_docs_and_android(self):
        self.assert_jobs(["docs/usage.md", "android/app/build.gradle.kts"], ["android"])

    def test_unknown_and_unsafe_paths_are_full(self):
        for path in (".gitattributes", "docs/future.json", "/absolute", "../escape"):
            with self.subTest(path=path):
                self.assert_jobs([path], ci_scope.ALL)

    def test_empty_diff_is_full(self):
        self.assert_jobs([], ci_scope.ALL)


class GitDiffTests(unittest.TestCase):
    def test_rename_and_delete_include_all_names(self):
        raw = b"D\0android/old.kt\0R100\0android/new.kt\0docs/new.md\0A\0README.md\0"
        self.assertEqual(ci_scope.decode_name_status(raw),
                         ["android/old.kt", "android/new.kt", "docs/new.md", "README.md"])

    def test_rename_from_binary_to_doc_still_builds(self):
        paths = ci_scope.decode_name_status(b"R100\0android/App.kt\0docs/App.md\0")
        self.assertEqual(ci_scope.classify(paths), {"android"})

    def test_malformed_rename_raises(self):
        with self.assertRaises(ValueError):
            ci_scope.decode_name_status(b"R100\0old\0")

    def test_dispatch_and_unknown_events_are_full(self):
        self.assertIsNone(ci_scope.diff_paths("workflow_dispatch", "", ""))
        self.assertIsNone(ci_scope.diff_paths("issue_comment", "1" * 40, "2" * 40))

    def test_empty_and_invalid_shas_are_full(self):
        self.assertIsNone(ci_scope.diff_paths("push", "0" * 40, "1" * 40))
        self.assertIsNone(ci_scope.diff_paths("pull_request", "bad", "1" * 40))

    @patch("ci_scope.subprocess.check_output")
    def test_pr_uses_three_dot(self, mocked):
        mocked.return_value = b"M\0docs/notes.md\0"
        self.assertEqual(ci_scope.diff_paths("pull_request", "1" * 40, "2" * 40),
                         ["docs/notes.md"])
        self.assertEqual(mocked.call_args.args[0][-1], "1" * 40 + "..." + "2" * 40)

    @patch("ci_scope.subprocess.check_output")
    def test_push_uses_two_dot(self, mocked):
        mocked.return_value = b"M\0android/build.gradle.kts\0"
        self.assertEqual(ci_scope.diff_paths("push", "1" * 40, "2" * 40),
                         ["android/build.gradle.kts"])
        self.assertEqual(mocked.call_args.args[0][-1], "1" * 40 + ".." + "2" * 40)

    @patch("ci_scope.subprocess.check_output", side_effect=ci_scope.subprocess.CalledProcessError(1, "git"))
    def test_git_failure_is_full(self, _):
        self.assertIsNone(ci_scope.diff_paths("push", "1" * 40, "2" * 40))

    @patch("ci_scope.subprocess.check_output")
    def test_empty_diff_is_full(self, mocked):
        mocked.return_value = b""
        self.assertIsNone(ci_scope.diff_paths("push", "1" * 40, "2" * 40))


if __name__ == "__main__":
    unittest.main()
