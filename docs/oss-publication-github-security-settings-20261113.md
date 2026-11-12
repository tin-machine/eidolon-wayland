# Eidolon Wayland — GitHub security hardening and `protect-main` ruleset

**Prepared:** 2026-11-13 (JST)  
**Evidence baseline:** `main` `4f52820f52c0265356897bd4f12e7f0435478bd7` (Private at preparation)  
**Status:** Configuration plan and security policy only. This document does **not** claim that the repository has been made Public or that any ruleset or GitHub security setting has been enabled.

## Scope and separation of responsibility

This document prepares GitHub's *repository-level* protections. The independent APK license/NOTICE/SBOM review remains in `docs/apk-license-compliance-20261112.md` and `docs/oss-license-publication-review-20261112.md`; it is not duplicated or declared complete here.

The owner has chosen **Collaborators only** for pull requests and continues to accept public Issues. This reduces untrusted fork-PR paths but does not make Issue text, GitHub comments, action inputs, dependencies, build scripts, or package registries trusted. Do not automatically execute Issue/Discussion/Comment content in CI, an AI agent, or a privileged action.

This repository has no self-hosted runner registered; the current CI uses `ubuntu-24.04` GitHub-hosted runners. The two existing workflows use `permissions: contents: read`; the `report` job alone uses `statuses: write` after a push, and checkout uses `persist-credentials: false`. These controls should be preserved.

## Evidence: actual PR check names

The following names were read from completed workflow **jobs** on the exact baseline commit (not guessed from YAML job IDs):

| Workflow | Actual GitHub check/job name | Run | Result |
| --- | --- | --- | --- |
| `検証` (`.github/workflows/check.yml`) | `host` | [43249416352](https://github.com/tin-machine/eidolon-wayland/actions/runs/43249416352) | success |
| `検証` | `android` | [43249416352](https://github.com/tin-machine/eidolon-wayland/actions/runs/43249416352) | success |
| `L7a foundation` (`.github/workflows/linux-real-app.yml`) | `GIO policy and recovery guard` | [43249416373](https://github.com/tin-machine/eidolon-wayland/actions/runs/43249416373) | success |
| `L7a foundation` | `Pinned Mousepad and real Waypipe foundation` | [43249416373](https://github.com/tin-machine/eidolon-wayland/actions/runs/43249416373) | success |

The second workflow uses `jobs.settings` / `jobs.mousepad` **internally**, but overrides display names via `jobs.<id>.name`. Select the display names shown above when adding required checks, then reconfirm the precise check context shown by GitHub on an actual PR. Do not accidentally require `settings` or `mousepad` as check names.

**Do not require** `report`, `eidolon/host`, or `eidolon/android`:

- `report` is intentionally skipped on `pull_request`.
- `eidolon/host` and `eidolon/android` are Commit Status contexts written by the `report` job on a **push to main**; they are not produced for PR heads.
- A required check absent on PRs can leave a PR permanently blocked.
- `workflow_dispatch`-only job checks do not satisfy PR branch rulesets; the existing workflows also use `pull_request`, which is appropriate.

Both workflows currently have unfiltered `pull_request` triggers; if future changes add `paths`, `branches`, or other skip conditions, re-evaluate every required check first. A check must have succeeded in the repository in the preceding seven days for selection.

## `protect-main` recommended branch ruleset

**Not yet possible to activate on this private repository with the current plan.** The GitHub API returned HTTP 403: `Upgrade to GitHub Pro or make this repository public to enable this feature.` GitHub documents branch rulesets for GitHub Free **public** repositories. Prepare now; activate only after explicitly authorized Public visibility change.

Go to **Settings → Rules → Rulesets → New ruleset → New branch ruleset** (menu wording may vary):

| Setting | Recommended value | Reason |
| --- | --- | --- |
| Ruleset name | `protect-main` | Recognizable audit identity |
| Target | **Default branch** (`main`) | Survives a default-branch rename better than an exact text rule |
| Enforcement | **Active** | Enabled only after required checks are selectable and validated |
| Bypass list | **Empty**, no admin or bot bypass | Avoid silently weakening protections |
| Restrict deletions | **ON** | Disallow branch deletion |
| Block force pushes / non-fast-forward | **ON** | Preserve branch history |
| Require a pull request before merging | **ON** | Direct push to `main` blocked by the rule |
| Required approving reviews | **0** | Solo maintainer can merge their own PR |
| Require status checks to pass | **ON** | Enforce the four checks in the table above |
| Expected source of each check | **GitHub Actions**, if selectable and verified | Avoid a similarly named arbitrary external status |
| Require branches to be up to date | **OFF** initially (loose) | Avoid unnecessary full rebuilds for each concurrent merge |
| Require merge queue | **OFF** | Workflows do not declare `merge_group` |
| Require signed commits | **OFF** initially | Older/third-party commits may not be signed |
| Require linear history | **OFF** | Preserve existing merge-commit workflow |
| Require code owner review | **OFF** | No mandatory second reviewer for a single maintainer |
| Require conversation resolution | Optional; **OFF** initially | Review threads are not yet a blocking merge contract |

With **zero approvals**, PRs and checks are still enforced. An empty bypass list is deliberately stronger than adding `Repository admins` as an exception, although repository administrators retain the ability to **edit or disable** rulesets. Protect the account with phishing-resistant MFA/passkeys and review who has admin/write permissions.

**Do not enable the ruleset prematurely**: with a missing/renamed required check, the only legitimate recovery may be to correct or temporarily disable the ruleset as an administrator. Document such an exception before changing protections.

## Activation sequence and verification

1. **Before Public:** complete other publication gates, including verifying that no publicly downloadable debug-APK artifact remains; rerun the artifact inventory immediately before changing visibility because new Private CI can upload more APKs. Keep licensing audit decisions in their separate workstream. Obtain the owner's explicit approval before switching visibility.
2. **After Public:** promptly enable Secret scanning and Push protection (as available to this repository), then Dependency graph / Dependabot alerts. Do not automatically enable Dependabot-generated PRs while the update/CI policy is being reviewed.
3. In **Settings → Rules → Rulesets**, create the branch ruleset with the values above. Verify each of the four required check names in the UI and set expected app source when possible. Keep `main` changes frozen between Public change and ruleset activation.
4. Enable **Private vulnerability reporting** in **Settings → Security and quality → Advanced Security**. Verify the public **Report a vulnerability** control. The new `.github/SECURITY.md` is guidance, **not** the mechanism that turns on private reporting.
5. Check that `Collaborators only` is still effective, external fork workflow approval is appropriately restrictive, Issue submission is possible, and there are no workflows triggered by untrusted Issue text or `pull_request_target` with privileged checkout.
6. Verify `main` ruleset shows **Active**. Open a harmless collaborator PR and confirm that the four actual checks are created, that merging is blocked until they succeed, and that a zero-reviewer solo merge is possible. Do not simulate unsafe force pushes or bypass the ruleset.
7. Consider CodeQL scanning and dependency review as follow-up hardening. They are **not** automatic proof of no vulnerabilities. Capture their activation status separately.

### Failure and rollback

- If a required check is shown as `Expected — Waiting for status`, check the exact job display name, event (`pull_request` versus `workflow_dispatch`), workflow filters, and expected app source. Do not add a permanent admin bypass to hide the symptom.
- If `main` becomes unmergeable due to a misconfigured ruleset, an administrator can move the ruleset to **Disabled** temporarily, correct the settings, and reactivate it. Record the reason and duration of such a change. A Private visibility change is **not** the rollback mechanism.
- After changing workflow job names or events, update the required-check list in the same change plan **before** enabling the renamed checks.

## Publication gate accounting

| Gate | State as of baseline | Publication decision |
| --- | --- | --- |
| First-party `SECURITY.md` | Added by this PR when merged | Recommended before Public |
| Private vulnerability reporting | Not enabled by this PR; Public required | Enable immediately after Public |
| Branch ruleset `protect-main` | Planned only; private-plan restriction | Enable immediately after Public |
| 4 required PR check jobs | Green on baseline main, **not protected** | Names verified; enforce after Public |
| GitHub secret scanning / push protection | Settings not changed by this PR | Verify and enable after Public |
| Existing APK artifacts | Not deleted or re-audited by this PR | Must be resolved in publication gate before Public |
| Third-party APK license audit | Separate workstream; not certified here | Follow existing release/publication decisions |
| Repository visibility | **Private**, unchanged by this PR | Explicit approval required |

## Primary references

- GitHub — [Adding a security policy](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/add-security-policy)
- GitHub — [Private vulnerability reporting](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository)
- GitHub — [Creating rulesets for a repository](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository)
- GitHub — [Available rules for rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)
- GitHub — [Troubleshooting required status checks](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks)
- Repository — `.github/workflows/check.yml`, `.github/workflows/linux-real-app.yml`, `docs/apk-license-compliance-20261112.md`
