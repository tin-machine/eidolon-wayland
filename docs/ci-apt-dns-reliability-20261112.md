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

## PR #52 follow-up: slow package transfer (2026-11-19)

This is a different observed failure from the original DNS incident. At PR
HEAD `d6f9ff852ae4185460e884cb61b0a5858bb7b38b`, both failed jobs resolved the
configured Ubuntu mirror and completed `apt-get update` before timing out
while downloading packages:

| Exact job | Observed package-transfer result |
| --- | --- |
| [Android 134827836559](https://github.com/tin-machine/eidolon-wayland/actions/runs/44311868356/job/134827836559) | `us-east-2.ec2.archive.ubuntu.com`; 38.2 MB required; the 11.2 MB `cmake` request at 13:17:12 UTC was followed by the next package request at 13:20:19; install deadline at 13:21:05, exit 124, before unpacking |
| [Foundation 134827850627](https://github.com/tin-machine/eidolon-wayland/actions/runs/44311868353/job/134827850627) | Same mirror; 41.9 MB required; still downloading at the 240-second deadline, exit 124, before application checks |
| [Host 134827836593](https://github.com/tin-machine/eidolon-wayland/actions/runs/44311868356/job/134827836593) | `azure.archive.ubuntu.com`; 38.2 MB fetched in 8 seconds; dependency setup succeeded |

These logs establish a slow package-acquisition phase, not its deeper network
cause. A DNS probe and a successful index update do not prove package-transfer
throughput. The APT HTTP timeout is a connection/data timeout, not a minimum
transfer-rate guarantee. The exact historical mirror-list contents were not
dumped and must not be reconstructed as an observation.

### Bounded recovery without extending the install deadline

The helper recognizes only a referenced, root-owned, regular, single-link,
non-group/world-writable `/etc/apt/apt-mirrors.txt`, at most 16 KiB, containing
up to four official Ubuntu archive URLs with optional numeric priority
metadata. Credentials, extra ports, query/fragment strings, unknown metadata
or mirror domains disable this optional path. Unknown/custom configurations
continue through the previous bounded update/install path unchanged.

For a supported list that **already contains** `archive.ubuntu.com`:

1. Keep the existing 90-second fail-closed update. Start one 240-second
   monotonic install budget, covering both downloads, mirror controls and
   the final installation.
2. Run `apt-get install --download-only` with the exact package list, for
   at most 120 seconds. No `dpkg` unpack/configuration occurs in this phase.
3. Only GNU timeout exit **124** permits one retry. Other failures, including
   signature/package errors and ambiguous SIGKILL exit 137, remain failures.
4. Temporarily assign priority 0 to the **existing** official archive entry.
   Keep every configured URI, preserve the relative priorities of the other
   mirrors, and do not change suites, architectures, signing keys, package
   requests or third-party repositories. Compare the original bytes before
   atomic replacement. No new mirror is introduced.
5. Retry download-only once using the remaining original budget, reserving
   45 seconds for install plus the bounded restoration/termination allowance.
   Restore the exact original mirror-list bytes in `finally`, including on
   retry or mutation failure. A foreign edit or failed restoration fails the
   setup; it is never overwritten or reported as successful.
6. Only after successful download and restoration, run
   `apt-get install --no-download` with the remaining original budget.
   Missing cache entries or an install failure remain nonzero. There is no
   retry of a partially executed `dpkg` transaction.

The helper logs only validated hostnames, numeric priorities, byte count and
SHA-256 for the observed mirror list, never raw configuration or credentials.
The privileged internal mutation command is restricted to the one runner
path; it cannot be used to name an arbitrary file. Read/replacement identity
checks include nanosecond mtime/ctime and exclude atime, which reading can
legitimately advance.

This changes only how the configured download fallback is reached. The
package set, authentication checks, fail-closed update, 90/240-second limits,
test selection and test/acceptance oracles are unchanged. No workflow edit
is required; the existing four helper callers receive the same behavior.

### Verification and remaining observation

The APT helper suite now has **27 passing tests**, executed locally as UID
1000 with no sudo, package installation or network. The tests include exact
package preservation, a shared deadline, no `dpkg` before download success,
one timeout-only fallback, restore-before-install, restoration after failure,
foreign edit/new mirror refusal, unsafe file identity, unknown format,
atime versus nanosecond content changes, and original failure retention if
restoration also fails.

A separate non-root experiment used the installed **APT 3.0.3** `apt-helper`
with two temporary `file:` mirrors and no package repositories or network.
Priority 0 selected the expected fixture bytes; a second invocation after
changing the priorities read the changed mirror list. This supports the
transport mechanism but is not an Ubuntu CI network success claim. The local
helper also reported an unreadable system apt.conf fragment; both fixture
downloads nevertheless exited 0. Actual updated-HEAD CI results must be
reported separately.

Primary references:

- [Ubuntu noble apt-transport-mirror(1)](https://manpages.ubuntu.com/manpages/noble/man1/apt-transport-mirror.1.html): priority metadata, lowest-priority selection and configured fallback.
- [Ubuntu noble apt-get(8)](https://manpages.ubuntu.com/manpages/noble/man8/apt-get.8.html): download-only and no-download semantics.
- [APT mirror transport source, tag 2.7.14](https://github.com/Debian/apt/blob/2.7.14/methods/mirror.cc): unsigned priority parsing, ascending sort and per-method mirror-list acquisition.
- [GitHub runner image mirror configuration](https://github.com/actions/runner-images/blob/main/images/ubuntu/scripts/build/configure-apt-sources.sh): current source emits the AWS/Azure primary plus existing official archive/security fallbacks. The 152-byte AWS form is a source-backed test fixture, not a recovered historical receipt.
