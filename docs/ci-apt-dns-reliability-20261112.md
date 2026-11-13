# CI APT/DNS resilience — 2026-11-12

> **Operational runbook (unregistered legacy document).** Indexed under
> [Build and CI behavior](README.md); not yet registered in a
> `docs/features/<feature>/manifest.yaml` graph. Retains its original path
> to preserve links and incident evidence. The live job-selection policy is
> [CI change scope](ci-change-scope-20261113.md).

## Incident evidence (PR-01)

The latest PR-01 `検証` run [43040295629](https://github.com/tin-machine/eidolon-wayland/actions/runs/43040295629)
and `L7a foundation` run [43040295610](https://github.com/tin-machine/eidolon-wayland/actions/runs/43040295610)
ended with cancelled jobs before application tests. The job logs show repeated
`Temporary failure resolving 'azure.archive.ubuntu.com'` and repeated APT
mirror download attempts. GIO logged `Unable to fetch some archives`. A
cancelled job is **not evidence of Rust, Android, Wayland, or Mousepad test
success or failure**.

The repository's own job `timeout-minutes` values were 15/30/10/25; their
configured limits are distinct from the GitHub-hosted runner's platform limit.
The repository's visibility or monthly included minutes will not solve runner
DNS failures.

## Mitigation in this PR

`scripts/ci/install_apt_deps.py` replaces the four individual APT
`update`/`install` command pairs in `.github/workflows/check.yml`
and `.github/workflows/linux-real-app.yml`. All package names are unchanged.

1. Read configured APT hostnames from Ubuntu `/etc/apt/apt-mirrors.txt` and
   `sources.list{,.d/*}`. Test at most four hostnames via `getent ahosts`
   (two passes, five seconds per lookup, short pause). **If none resolves,
   fail before APT**; do not report a test failure. If no hostname is
   discoverable, still attempt APT with a hard deadline.
2. Bound `apt-get update` to 90 seconds and `apt-get install` to 240
   seconds with GNU `timeout`; terminate a stuck command rather than spend
   an entire CI job on repository download retries. APT is configured with
   `Acquire::Retries=1` and 10-second HTTP/HTTPS acquire timeouts.
3. Use `APT::Update::Error-Mode=any` on `apt-get update`. A partial
   indexes-fetch failure must not be interpreted as a successful update.
   Install executes only after a completely successful update.
4. Preserve `sudo -n`, noninteractive APT and the original package set.
   Keep normal Ubuntu package signing checks and configured repositories.
   **No** `--fix-missing`, `--allow-unauthenticated`, broad
   `continue-on-error`, alternate unverified mirrors, or skipped tests.
5. Increase job budgets to Host 30, Android 45, GIO 20, Mousepad/Waypipe
   45 minutes, leaving the existing report job at 5 minutes. The DNS/APT
   portion **still fails within its own small budget** on an outage.

The mirror preflight checks **Ubuntu mirror hosts only**, not unrelated
third-party APT sources such as `packages.microsoft.com`. During PR #14's
first CI run ([Host/Android 43051152279](https://github.com/tin-machine/eidolon-wayland/actions/runs/43051152279),
[L7a 43051152724](https://github.com/tin-machine/eidolon-wayland/actions/runs/43051152724)),
the old preflight resolved that unrelated hostname while all four jobs
timed out in `apt-get update` after 90 seconds. This update corrects that
false-positive signal; it does **not** treat a successful DNS query as proof
that Ubuntu packages can be downloaded.

The DNS preflight is diagnostic, not proof that downloads will succeed.
Resolution can succeed while HTTP downloads fail. Bounded `apt-get` and
its nonzero exit status remain the source of truth.

## Verifications

Pure host tests (`scripts/tests/test_ci_apt_deps.py`) run with mocked
commands and no network/root. They cover mirror detection, resolver
fallback, full DNS outage, update failure preventing install, install
deadline, success path preserving package identities and invalid inputs.
They are picked up by the existing `scripts/check-host --suite all` Python
test discovery. A new regression also ensures that successful resolution of
unrelated third-party sources cannot mask an Ubuntu mirror DNS failure.
Under the current [scoped CI policy](ci-change-scope-20261113.md), both
workflow files and `scripts/**` force all relevant build jobs to run;
the always-on `Documentation integrity` check must continue to pass.
Do not copy the legacy workflow YAML from this PR's old base over current
main, since that would remove change-scope and documentation checks.

Operator checks:

```bash
python3 -m unittest discover -s scripts/tests -p test_ci_apt_deps.py -v
python3 scripts/check-design
scripts/check-host --suite all
```

`scripts/check-host --suite all` and real GUI tests are required unchanged.
The new PR's CI must pass at its **own exact head commit** before merging.

## Review sources

- Ubuntu 24.04 apt-get: `--error-on=any` and APT option semantics:
  https://manpages.ubuntu.com/manpages/noble/man8/apt-get.8.html
- GitHub Actions `jobs.<job_id>.timeout-minutes`:
  https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax

Future optimization (separate design): a versioned, digest-pinned CI image
with the required Ubuntu packages may reduce repeated APT dependency setup,
but adds image provenance, update, and PR trust-boundary responsibilities.
