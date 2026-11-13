# CodeQL Kotlin — lightweight manual-build probe (2026-11-13)

## Purpose and evidence

Eidolon Wayland's initial GitHub CodeQL **Default setup** analyzed Rust, Python, C/C++ and GitHub Actions successfully, but the `java-kotlin` autobuild failed on 2026-11-13 in [run 43423758338](https://github.com/tin-machine/eidolon-wayland/actions/runs/43423758338), job `Analyze (java-kotlin)` (`131879893813`):

```text
error: no such command: `ndk`
Execution failed for task ':app:buildDebugEidolonNative'.
```

The CodeQL autobuilder invoked Gradle `assemble`, which triggers `android/app/build.gradle.kts`'s `buildDebugEidolonNative` and `cargo ndk`. The normal Android CI installs `cargo-ndk 4.1.2` explicitly, while the automatically inferred CodeQL build did not. A failed native build **does not** establish any Kotlin security finding.

The subsequent GitHub default-setup adjustment continued analysis of the four successful languages but excluded Kotlin. **Do not remove or replace that working default setup merely to test a new build recipe.**

## Experiment (one non-uploading workflow)

The workflow [`.github/workflows/codeql-kotlin-probe.yml`](../.github/workflows/codeql-kotlin-probe.yml):

1. Uses the existing Android CI JDK 25 with the repository's pinned Gradle wrapper, Android platform 36 and build tools 35.0.0.
2. Runs `./gradlew :app:compileDebugKotlin --dry-run` and **fails before compilation** if it sees `buildDebugEidolonNative` or `buildReleaseEidolonNative` in the task graph. It deliberately does not install Rust, the NDK, `cargo-ndk`, or build an APK.
3. Calls CodeQL `init` with `languages: java-kotlin`, `build-mode: manual`, then compiles `:app:compileDebugKotlin` under CodeQL instrumentation using `--rerun-tasks`.
4. Requires at least one compiled Kotlin/JVM `.class` file, calls CodeQL `analyze` with **`upload: never`** and `upload-database: false`, and verifies that local SARIF with a `runs` entry was produced.

The workflow is limited to relevant PR changes and `workflow_dispatch`, and has only `contents: read`. Its GitHub Actions job is **not** added to the six `protect-main` required checks; it is a diagnostic probe. It does not publish or persist SARIF, database contents, APKs, credentials, or Gradle artifacts. A passing job proves a traced build and local SARIF generation; it does **not** prove that CodeQL's Kotlin extraction included every Kotlin source file, or that alerts have been uploaded to the Security tab.

On this experiment PR, the existing `CI scope` fallback may select all of the current heavy tests because the new workflow file is an unknown build input. This is a safe one-time conservative choice, not a bypass of existing required checks.

## Acceptance and recovery

| Evidence | Acceptance |
| --- | --- |
| Native build task graph | Neither `buildDebugEidolonNative` nor `buildReleaseEidolonNative` present |
| Manual build | `:app:compileDebugKotlin` succeeds on hosted runner without Rust/NDK |
| JVM output | Nonzero `*.class` count |
| CodeQL | `init`, `analyze` succeed; local SARIF exists |
| Existing security coverage | Default setup's Rust/Python/C++/Actions results remain active; **no** new Code scanning uploads |
| Existing required CI | Six `protect-main` contexts unaffected |

If the Gradle dry run unexpectedly includes native build tasks, **do not bypass the guard** or install the entire NDK/Waypipe toolchain just to make the experiment green. Investigate and isolate the Kotlin compilation graph first. If the build succeeds but the CodeQL action cannot coexist with Default setup even with uploading disabled, keep the successful Kotlin build as evidence and propose a separate Advanced setup migration; do not disable working default coverage as a side effect.

## Future promotion (separate decision)

CodeQL **Default** and **Advanced** setups are not interchangeable per language: turning on Default setup disables older custom CodeQL workflows or blocks their result uploads. The experiment intentionally uses `upload: never`, so it **does not** fix the Kotlin coverage gap in the Security tab yet.

After this probe passes, plan a distinct migration PR with a complete CodeQL Advanced setup matrix for **`java-kotlin` (manual), `rust`, `python`, `c-cpp` and `actions`**. Verify successful analysis for *all five* languages and then switch CodeQL Default to Advanced in GitHub Settings using an explicit release sequence. Do not prematurely require Kotlin CodeQL as a Ruleset status check (a missing check can block PRs). Keep all CodeQL Actions pinned to reviewed full commit SHAs, or define a reviewed periodic pin refresh.

## Sources

- [CodeQL — Java/Kotlin compiled language build options](https://docs.github.com/en/code-security/reference/code-scanning/codeql/build-options-for-compiled-languages)
- [CodeQL — Default/Advanced setup collision](https://docs.github.com/en/code-security/reference/code-scanning/troubleshoot-analysis-errors/results-different-than-expected)
- [CodeQL Action analyze inputs — `upload: never`](https://github.com/github/codeql-action/blob/main/analyze/action.yml)
- [CodeQL — Advanced setup configuration](https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/configure-code-scanning/configuring-advanced-setup-for-code-scanning)


## Coverage expansion (PR-A, 2026-11-13; pending CI evidence)

The initial local CodeQL scan reported **153/219** Kotlin files scanned, coinciding with `android/app/src/main` (78) and `android/app/src/debug` (75). This is not necessarily proof that exactly those individual files were extracted; use CodeQL's file coverage when drawing that conclusion.

Source-set inventory at the PR-A base: app main 78, debug 75, release 1, app JVM test 39, app debug-specific JVM test 20, probe main 2; four Gradle Kotlin DSL scripts. The theoretical total for runtime and test Kotlin is **215/219**, if all requested variants compile and CodeQL extracts them. The remaining four Gradle Kotlin DSL scripts are security-sensitive build configuration and are *not* necessarily extracted as application Kotlin by the CodeQL Java/Kotlin tracer.

The probe now compiles `:app:compileDebugKotlin`, `:app:compileReleaseKotlin`, `:probe:compileDebugKotlin`, and `:app:compileDebugUnitTestKotlin` under a single manual CodeQL run. It checks the **full dry-run task graph first** and refuses to proceed if any `build(Debug|Release)EidolonNative` or `nativeProbe` task is present. All four Kotlin output directories must contain JVM classes.

This is **compile-only**: neither instrumentation nor JVM tests are run, no Quest device is used, and no APK is built. SARIF stays local via `upload: never`. A green build alone does not prove that every file is extracted: compare the CodeQL log's `CodeQL scanned X out of Y Kotlin files` statement with the baseline and investigate non-extracted source sets. If any task unexpectedly requires native packaging, preserve the guard and change the plan rather than installing the full native build chain just to make the probe green.

Only after this evidence passes should a separate PR introduce full five-language Advanced setup. Do not change the current Default setup while validating PR-A.

### Verified implementation evidence — PR #22

On 2026-11-13, [CodeQL Kotlin coverage run #43431320588](https://github.com/tin-machine/eidolon-wayland/actions/runs/43431320588) **succeeded**. The Gradle dry-run task graph had no native/APK build, CodeQL manual initialization succeeded, four Kotlin compiler outputs contained 485 debug, 334 release, 6 probe, and 85 unit-test JVM class files, and local `java.sarif` had one CodeQL run. Crucially, CodeQL itself printed **`CodeQL scanned 215 out of 219 Kotlin files in this invocation`**. This is the observed static analysis coverage of Kotlin sources in this PR, not a Quest/HMD test and not uploaded Code scanning results. The remaining four files are Gradle Kotlin DSL scripts that need separate supply-chain/build-integrity controls.
