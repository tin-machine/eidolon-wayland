# APK License Compliance — implementation and publication gates (2026-11-12)

This change makes the **artifact upload path fail closed for known license
inventory violations**, and supplies readable notices in the actual debug and
unsigned release APKs. It does **not** claim a legal review of every binary
component. Public visibility and signed release remain separate decisions.

## Inputs and generated APK assets

| Input | Emitted path in APK | Validation |
| --- | --- | --- |
| Repository LICENSE (MIT) | assets/licenses/eidolon/LICENSE-MIT.txt | Byte-for-byte comparison with repository |
| THIRD_PARTY_NOTICES.md | assets/licenses/eidolon/THIRD_PARTY_NOTICES.md | Byte-for-byte comparison |
| Apache-2.0 license text | assets/licenses/eidolon/Apache-2.0.txt | Byte-for-byte comparison with ci/licenses/Apache-2.0.txt |
| Meta SDK attribution + applicable terms URL | assets/licenses/eidolon/META-SDK-NOTICE.txt | Fixed text and hash; not a relicensed SDK |
| MPL-2.0 Cargo source offer | assets/licenses/eidolon/MPL-SOURCES.md | Exact crate/version, upstream archive URL and SHA-256 |
| Explicit Android license group policy | assets/licenses/eidolon/ANDROID-RUNTIME-LICENSE-POLICY.md | Exact text and source repository checks |
| Manifest of notice hashes and MPL source records | assets/licenses/eidolon/distribution.json | Hashes verified against native-build.json metadata |
| Resolved Gradle debug/release runtime components | assets/licenses/android/runtime-modules.json | Schema, variant, sort, uniqueness, allowlist of known groups |

The existing six fixed native license texts, the theme/transform dependency
license inventory and all existing native hash validations remain in force.

### Android dependency closure

Gradle's runtime resolution is captured **for the variant being assembled**,
not inferred from direct dependencies or debug test classpaths. Any unresolved
or new/unreviewed Maven group aborts the validation (and CI artifact upload).
Review the precise Maven coordinate in a PR, assess the upstream license and
its notices, and only then update the documented policy in
scripts/apk_license_compliance.py.

Two additional **exact coordinates** are reviewed as Apache-2.0 based on their
upstream license declarations: `com.google.guava:listenablefuture:1.0`
(https://github.com/google/guava/tree/master/listenablefuture) and
`org.jetbrains:annotations:23.0.0`
(https://github.com/JetBrains/java-annotations).
Their publisher groups are NOT implicitly allowlisted for other artifacts
or versions.

The current group-based policy is an **expected license family**. A match
does NOT prove every upstream AAR/POM's license text, embedded assets or
NOTICE requirements have been completely reviewed. The Apache-2.0 copy and
Meta SDK link are provided, but neither overrides any upstream rights.

The Meta SDK 0.15.0 runtime modules are expected as a set; the validator
rejects a missing one. Meta SDK distribution must be in conformity with
https://developers.meta.com/vr/licenses/oculussdk/ and is not MIT-licensed.

### MPL-2.0

The source offer for cssparser, cssparser-macros and dtoa-short (or any future
MPL-2.0 entries in the theme inventory) is derived from pinned Cargo metadata.
Each record includes the exact upstream source archive and its SHA-256. The
generator rejects modified entries and missing source or checksum data. A
proc-macro can be build-time only; inclusion in the conservative inventory
does not assert binary linkage.

Upstream obligations: MPL-2.0 §3.1–3.2 and Mozilla's FAQ Q8:
https://www.mozilla.org/en-US/MPL/2.0/FAQ/

## Gates and commands

- scripts/build-native copies notices into the generated native assets and
  stores SHA-256 metadata in assets/native-build.json.
- Gradle generates assets/licenses/android/runtime-modules.json from the
  resolved runtime artifacts immediately after build-native.
- scripts/check-native-package verifies native outputs **and** notices.
- scripts/check-apk-license independently verifies the completed APK,
  requires no Android SDK or network, and checks the Android dependency list.
- scripts/build-android and scripts/build-release run both checks.
- The GitHub Actions Android job runs the explicit check **before** uploading
  debug APK; a failing gate prevents the new artifact from being published.
- **Public repositories never upload new debug APK artifacts** through this
  workflow, even on a passing license check. Private repository builds retain
  debug APK delivery. Publishing binaries to Public requires a separate
  affirmative decision after full notice review.
- scripts/tests/test_apk_license_compliance.py exercises accepted records and
  negative cases (missing/mutated notice, unknown Maven group, missing Meta
  module, incorrect variant, duplicate runtime module, modified MPL source).

## What this does not certify yet

1. **Full per-artifact license notices** for all resolved Android/Gradle
   transitive modules are not yet independently verified against their
   published POM/AARs. The group-level expected-license policy is an
   engineering regression gate, not a legal-compliance certificate.
2. **Complete Cargo linkage-based SBOM** for every non-theme dependency,
   and Android manifests/other Meta SDK binary assets still require final
   review. Source-availability records cover the existing theme subgraph.
3. The existing packaging exclusion of META-INF/LICENSE is not removed.
   The included Apache-2.0 and other notices do not automatically restore
   every excluded upstream NOTICE; an AAR/POM and packaged asset audit is
   required before unconditional APK redistribution approval.
4. **Preexisting artifacts** from previous CI runs are unchanged. They may
   still be downloadable after changing the repository to Public. Check
   retention and permissions before visibility change; no artifacts were
   deleted by this PR.
5. The debug build includes the Meta Spatial SDK. The existing private-key
   test-certificate findings in the SDK are tracked independently; this
   change does not prove their runtime purpose or modify the SDK binary.

## Source references

- Repository: LICENSE, THIRD_PARTY_NOTICES.md, native/deps.lock.json,
  Cargo.lock, scripts/theme_licenses.py
- Meta SDK agreement: https://developers.meta.com/vr/licenses/oculussdk/
- Android Gradle runtime graph: https://docs.gradle.org/current/userguide/dependency_resolution.html
- Mozilla MPL FAQ: https://www.mozilla.org/en-US/MPL/2.0/FAQ/
- GitHub artifacts: https://docs.github.com/en/rest/actions/artifacts

**Status:** A mechanical acceptance gate for known obligations; **not** a
GO to change repository visibility or distribute APKs without the outstanding
per-artifact and third-party notice review.
