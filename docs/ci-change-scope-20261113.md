# Eidolon Wayland — change-scoped CI (2026-11-13)

## Why not `on.pull_request.paths-ignore`?

Both `.github/workflows/check.yml` and `.github/workflows/linux-real-app.yml` define checks planned as required in the Public `protect-main` Ruleset. GitHub leaves required checks **Pending** when the workflow itself is skipped by a path filter. The desired mechanism is an **always-triggered workflow with a lightweight scope job and conditional expensive jobs**.

- `CI scope (host/android)` controls `host` and `android`.
- `CI scope (L7a)` controls `GIO policy and recovery guard` and `Pinned Mousepad and real Waypipe foundation`.
- These two lightweight scope jobs **must themselves be added as required checks**, alongside the four existing check names. **D4 adds the always-running `Documentation integrity` check**, which should also be required after it has passed on main. If the scope computation fails, its required check fails and prevents a silent skip of build jobs.
- Job-level conditional skips are reported by GitHub as successful for required checks. The scope result should nevertheless be inspected on nontrivial PRs.
- A docs-only push no longer emits new debug APK artifacts or the legacy `eidolon/host` / `eidolon/android` commit statuses.

## Actual routing policy

Changed files are determined by `git diff --name-status -z --find-renames` with full fetch history. PRs use **three-dot** base/head comparison; pushes use **two-dot** before/after comparison. Old and new names from renames/copies are both checked so moving a build input into documentation cannot suppress required tests.

| Changed paths | host | Android / APK | GIO settings | Mousepad / Waypipe |
| --- | :---: | :---: | :---: | :---: |
| Only `README.md`, `.github/SECURITY.md`, ordinary `docs/**/*.md` | — | — | — | — |\n| Explicit `docs/features/<feature>/manifest.yaml` | — | — | — | — |
| `docs/design/**` | ✓ | — | — | — |
| `android/**` | — | ✓ | — | — |
| `android/gradle/**`, Gradle wrapper scripts | ✓ | ✓ | — | — |
| `.github/workflows/check.yml` | ✓ | ✓ | — | — |
| `.github/workflows/linux-real-app.yml` | ✓ (actionlint) | — | ✓ | ✓ |
| `scripts/**`, `native/**`, `Cargo*`, `ci/**`, root `LICENSE`, `THIRD_PARTY_NOTICES.md` | ✓ | ✓ | ✓ | ✓ |
| `docs/**` non-Markdown fixtures such as JSON; any unknown file | ✓ | ✓ | ✓ | ✓ |
| Invalid/ambiguous/missing Git diff, empty diff, or manual dispatch | ✓ | ✓ | ✓ | ✓ |

The **D4 documentation integrity job runs on every PR and push to main**, including code-only diffs. It checks the managed registry and dependencies via `scripts/docs.py validate`, the legacy Phase 0–6 task contracts via `scripts/check-design`, the resolver and CI scope regression tests, and the documentation-system context smoke test. Unregistered legacy docs are **not** automatically accepted or covered by the managed-graph validator.\n\nOnly the exact per-feature `docs/features/<feature>/manifest.yaml` shape is treated as documentation-only. `docs/design/**` retains its host check, all JSON/other YAML/fixture inputs remain full build triggers, as do `AGENTS.md`, `scripts/**`, schema JSONs, and CI changes. There is **no broad `docs/**/*.yaml` exclusion**.\n\nThis classifier is **conservative**: skipping fewer jobs is acceptable, but missing a dependency is not. In particular, all `scripts/**` changes are treated as shared because test/fixture/build entrypoints can affect all workflows. `LICENSE` and `THIRD_PARTY_NOTICES.md` are APK inputs, **not** inert documentation. The `docs/design` exception exists because `scripts/check-design` reads its contracts and link targets.

The classifier itself is `scripts/ci/ci_scope.py`, with pure Python regression tests in `scripts/tests/test_ci_scope.py`. On a PR, the scope job runs the **base branch** version of the classifier if it exists, rather than trusting a newly edited classifier from the PR being tested. On the initial PR introducing the classifier, the base version does not exist: the script path is an unknown/shared input so all builds execute. Unknown event types, invalid SHAs, absent merge-base, git failures, and empty diffs trigger all jobs.

This still does **not** provide a fully tamper-proof CI policy against a contributor with permission to edit the workflow YAML or change repository rulesets. Keep PRs limited to collaborators, grant the smallest necessary repository permissions, and apply branch protections as separately documented. Review changes to CI scope rules as security-sensitive changes.

## Main push report semantics

The existing `report` job writes push-only `eidolon/host` and `eidolon/android` Commit Status contexts. It now:

- Does **not** run when both host and Android are scoped out.
- Records only jobs scheduled by the change-scope decision. It does not mark intentionally skipped jobs as failures.
- Still records a failure when a **scheduled** build is unexpectedly skipped or fails.
- Is not a required PR check; PR Rulesets use the scope job and the four actual GitHub Actions job checks instead.

This prevents a docs-only main merge from reporting erroneous red build statuses. The `report` job is still separate from `pull_request` and still uses the existing restricted `statuses: write` permission.

## Verification and post-merge smoke test

Pure tests (no Android SDK/network required):

```sh
python3 -m unittest discover -s scripts/tests -p test_ci_scope.py -v
```

The initial implementation PR changes `scripts/**` and both workflows, so all four builds **must** run once for the PR. After it is merged:

1. Open an ordinary docs-only PR changing a new `docs/*.md` file (do not change `docs/design/**`). Both CI scope jobs should succeed and the four expensive jobs should be marked **Skipped**. No APK artifact should be uploaded.
2. Change an Android-only path and confirm only `android` is scheduled.
3. Change `docs/design/tasks.json` and confirm `host` is scheduled.
4. Verify `workflow_dispatch` still runs everything even without a relevant diff.
5. When activating `protect-main`, require the **six existing required checks plus `Documentation integrity`** (seven total), with expected source GitHub Actions. Confirm actual job names/sources and that the docs job is always emitted before changing the Ruleset.

**Do not** add workflow-level path filters to any required workflow; do not suppress checks using commit-message `[skip ci]` markers.

## Sources

- GitHub — [Troubleshooting required status checks](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks)
- GitHub — [Using conditions to control job execution](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-jobs-with-conditions)
- GitHub — [Workflow syntax: `needs`](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idneeds)

## D4 migration acceptance (2026-11-13)

- `Documentation integrity` is intentionally unconditional. It does not compile Rust, Android or APKs and does not use APT; Python 3 and Git are available on `ubuntu-24.04`.
- In this **D4 implementation PR**, the scope classifier, its tests, and `check.yml` change. The trusted base-branch classifier will still route them to the expensive jobs. Do **not** suppress those jobs to make this PR green.
- **After merge**, a PR containing only `docs/features/<feature>/manifest.yaml` and ordinary Markdown must run `Documentation integrity`, and the four expensive jobs should skip. A missing ID or malformed manifest must fail `Documentation integrity` and block merge **if that check is configured as required**.
- Changes to existing `docs/design/**`, non-manifest YAML, fixture JSON, schema JSON, root `AGENTS.md`, `scripts/**`, or unknown paths must retain their original conservative build coverage.
- GitHub Rulesets required checks are **repository settings**, not changes to this PR. Updating required checks must be performed separately by an authorized admin; do not claim that this PR changed them.

Official references: [GitHub required status checks](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks), [workflow job conditions](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-jobs-with-conditions).
