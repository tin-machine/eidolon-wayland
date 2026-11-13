# CodeQL Advanced setup migration — five languages (2026-11-13)

**Status: staged implementation, NOT activated.** `EIDOLON_CODEQL_ADVANCED_ENABLED` must remain **unset/false** until GitHub CodeQL Default setup is disabled and all five languages pass in the staged workflow. The prior working Default setup must continue to scan Rust, Python, C/C++ and GitHub Actions until that deliberate cutover.

## Evidence and scope

- Initial `java-kotlin` Default setup autobuild failed on `cargo ndk` (run 43423758338).
- PR #21 proved `:app:compileDebugKotlin` (485 JVM `.class` files) under CodeQL manual instrumentation, generating local SARIF and reporting 153 of 219 Kotlin files scanned. All four other language analyses passed.
- PR-A (#22) extends the non-uploading probe to debug, release, probe and JVM unit-test Kotlin sources; **do not claim full coverage until the PR-A log verifies it**.
- This migration stages `.github/workflows/codeql-advanced.yml` with a five-language matrix: `java-kotlin` manual, `rust` none, `python` none, `c-cpp` none and `actions` none. CodeQL `none` is supported for Rust and C/C++ (though the latter can miss code generated only during compilation). The remaining four languages retain the successful default-style no-build path.
- All actions are pinned to commit SHAs. The Kotlin job uses JDK 25, Gradle wrapper 9.4.1, Android platform 36 and build tools 35.0.0; native compilation/APK assembly are prohibited by a task graph guard. Non-Kotlin jobs do not install an Android toolchain.

## Safe default behavior

The repository variable `EIDOLON_CODEQL_ADVANCED_ENABLED` is an explicit activation gate.

| Event | Variable unset/false | Variable equals exactly `true` |
| --- | --- | --- |
| PR | Five-language analysis, local SARIF, **no upload** | Five-language analysis and upload |
| `workflow_dispatch` | Five-language analysis, local SARIF, **no upload** | Five-language analysis and upload |
| Push to main | Jobs skipped | Five-language analysis and upload |
| Weekly schedule (Monday 03:17 UTC) | Jobs skipped | Five-language analysis and upload |

This is a staging mechanism, not a permanent production toggle: it must remain false during PR-B review and its initial merge; the maintainers should enable it shortly after disabling GitHub's Default setup. The five-language results MUST be confirmed green in PR-B **before** attempting the cutover. Once the variable is true, the legacy Kotlin probe skips ordinary PR events, avoiding two full Kotlin scans per PR. It remains manually runnable through `workflow_dispatch` for diagnostics.

Uploading is controlled by the CodeQL Action `upload` input; `never` also suppresses unsuccessful-execution reporting (unlike legacy `false`). There are no SARIF, database or APK artifacts published by the dry run. A temporarily ignored CodeQL job is not considered accepted; the local SARIF file count and actual CodeQL scan log must be inspected.

## Cutover runbook — maintainers' manual settings

**Do not run this sequence until PR-B and its base PR-A pass all required checks and PR-B has been merged to main.** Avoid changing GitHub settings while either PR is incomplete.

1. On the PR-B Actions run, confirm `CodeQL Advanced (java-kotlin)`, `(rust)`, `(python)`, `(c-cpp)` and `(actions)` are all **Success**, and each emitted valid SARIF locally. The PR's ordinary six protected CI checks must also pass. Inspect Kotlin's extracted file count and any residual exclusions.
2. Verify the repository variable is absent or exactly `false`. Do **not** set it to `true` while Default setup is enabled.
3. In **Settings → Advanced Security → Code Security → CodeQL analysis** choose **Switch to advanced**, then confirm **Disable CodeQL** (meaning disable *Default setup*, not security scanning universally). The Code scanning Security tab may have a brief reporting gap; avoid pushing/merging unrelated changes during that window.
4. In **Settings → Secrets and variables → Actions → Variables**, create/update **repository variable** `EIDOLON_CODEQL_ADVANCED_ENABLED=true`. Variable name and value are case-sensitive. **Do not put it in Secrets**: `vars.` is required, and the setting itself is not secret. The new CodeQL workflow already exists on main.
5. In **Actions → CodeQL Advanced (staged, five languages)**, select **Run workflow → Branch main → Run workflow**. Confirm five success jobs **and** that results actually appear in **Security → Code scanning**, including **java-kotlin**. If manual dispatch is not visible, use `gh workflow run codeql-advanced.yml --ref main`.
6. Inspect Code scanning configuration status, alerts, language coverage, and duplicated/stale configurations. Keep Ruleset's six existing required checks unchanged during the initial rollout. Add a new required CodeQL gate **only after** successful PR runs prove that the check exists reliably (otherwise all merges may be blocked).
7. If CodeQL upload fails, first verify `EIDOLON_CODEQL_ADVANCED_ENABLED`, `security-events: write`, the Default setup state, and SARIF processing. If repeated uploads cannot be repaired, set the variable back to `false` before re-enabling Default setup; document and observe the ensuing reduced Kotlin coverage.

### CLI checks after activation

```bash
repo=tin-machine/eidolon-wayland
gh api "repos/$repo/code-scanning/default-setup" \
  --jq '{state, languages, query_suite}'
gh api "repos/$repo/actions/variables/EIDOLON_CODEQL_ADVANCED_ENABLED" \
  --jq '{name, value}'
gh workflow run codeql-advanced.yml -R "$repo" --ref main
gh run list -R "$repo" --workflow codeql-advanced.yml --limit 5
```

Repo variables can be changed by administrators; neither the variable nor the manual cutover is an immutable security boundary. Review administrator access, workflow changes, and CODEQL configuration changes accordingly.

### Operational trade-offs and future improvement

Unlike the legacy docs-only CI classifier, the production CodeQL Advanced workflow runs all five languages on each PR after activation, including documentation-only PRs. This is deliberate: CodeQL scans build scripts, query configurations, dependency metadata, actions, and cross-language sinks, not just a single source directory. Enabling `paths` filters on a future *required* code-scanning check can leave PR checks Pending. Any future optimizations must preserve coverage and required-check availability.

The no-build `c-cpp` analyzer may not cover build-generated and externally downloaded native components as precisely as manual compilation; consider separate CodeQL/Cargo audits for trust boundaries and third-party Waypipe/Smithay dependencies. CodeQL itself is not a proof of security. Do not equate an APK build, CodeQL green check, or a static source inventory with Quest/HMD acceptance.

## Sources

- [GitHub: CodeQL Default setup overrides Advanced setup](https://docs.github.com/en/code-security/reference/code-scanning/troubleshoot-analysis-errors/results-different-than-expected)
- [GitHub: Configuring Advanced setup](https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/configure-code-scanning/configuring-advanced-setup-for-code-scanning)
- [GitHub: CodeQL for compiled languages](https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/manage-your-configuration/codeql-for-compiled-languages)
- [CodeQL Action analyze input `upload`](https://github.com/github/codeql-action/blob/v4.39.3/analyze/action.yml)
