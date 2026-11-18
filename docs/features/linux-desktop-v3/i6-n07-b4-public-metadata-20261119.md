# I6 N07-B4 — public storage metadata export and CodeQL

**2026-11-19 JST, source-bound correction to the CI report.** This is a
diagnostic/export change inside Draft [PR #52](https://github.com/tin-machine/eidolon-wayland/pull/52),
not Golden approval or formal N07 acceptance. The
[settings lifecycle v2 contract](i6-n07-settings-lifecycle-v2.md), C1/C2
ownership, normal product paths and existing candidate revisions remain
unchanged.

## 1. The failing check is separate from the Actions workflows

At PR HEAD `2d66c10aa8d87efe5bec10d94429350d4d7f90c9`, the Actions runs
[Verification 44416734517](https://github.com/tin-machine/eidolon-wayland/actions/runs/44416734517),
[CodeQL Advanced 44416734500](https://github.com/tin-machine/eidolon-wayland/actions/runs/44416734500)
and [L7a 44416734531](https://github.com/tin-machine/eidolon-wayland/actions/runs/44416734531)
all completed successfully on attempt 1. However, the separate
[GitHub Advanced Security / CodeQL check 135199150389](https://github.com/tin-machine/eidolon-wayland/runs/135199150389)
failed with **one new High alert**. Workflow execution success did not mean
the PR's security result passed. The earlier workflow-only report missed
this check; its all-CI-success interpretation is corrected here.

The actual CI merge checkout was
`96221f38d2ef50c4eac9b95b95124b24f6e326ed`. It and that PR HEAD have Git tree
`955b77000c40eacaacd63135160d4e4a70daacb1`. Main remains
`ee9192d12957a17aaa3d61bd6e8ba1ca86a99d6f`; the CI checkout is not a merge
into main. The successful B4 GUI acquisition at artifact `14480625397`
remains evidence about its original source, not about a later fix.

## 2. Exact reproduction and data flow

The original source was independently extracted from that Git tree and
analyzed with the same official CodeQL CLI **2.27.4**, Python extractor
**7.2.1** and `codeql/python-queries@1.9.0` as CI. The single query
`py/clear-text-storage-sensitive-data` reproduced exactly one result with
the same sink and diagnostic as the GitHub annotation.

The reported flow starts at
[`linux_real_app_v3_n07_b4_review.py`](../../../scripts/linux_real_app_v3_n07_b4_review.py)'s
`supervisor["trusted_storage"]` lookup, then passes through `deepcopy`, the
candidate's `storage` member, canonical JSON serialization and the shared
[`linux_real_app_v3_n07_review.py`](../../../scripts/linux_real_app_v3_n07_review.py)
file writer. The original source/sink lines were 178 and 126 respectively.
The retained SARIF contains all 18 flow locations; its SHA-256 is
`d7e9f2437845809ebfe646a5107e26e1950f312c2a35edb44b715b01dc0568b0`.

CodeQL's version-matched
[sensitive-name heuristic](https://github.com/github/codeql/blob/codeql-cli/v2.27.4/shared/concepts/codeql/concepts/internal/SensitiveDataHeuristics.qll)
includes `trusted` in generic secret names. Its
[Python source model](https://github.com/github/codeql/blob/codeql-cli/v2.27.4/python/ql/lib/semmle/python/dataflow/new/SensitiveDataSources.qll)
treats such dictionary lookups as sources. In this fixture, the field holds
public observation metadata: generated fixture identities, bindings and
digests, numeric filesystem stamps and the fixed managed C1 layout.
The actual trace does **not** begin at random ID generation, an SSH private
key, or the live storage-owner constructor.

## 3. Explicit public projection before export

The exporter builds the public storage object from its declared fields
instead of copying the opaque aggregate. Existing `StorageRecord` and
`StorageOwner` parsers retain their exact schemas. Record/owner digests and
the managed root layout are derived from those validated values; filesystem
observations contain only the explicitly allowed numeric fields. Unknown
members at any nested level are rejected. The reconstructed object must be
canonically identical to the input, so this is not a lossy filter or a
normalization that turns invalid metadata into valid evidence.

The full independent collection validator still checks both real sessions,
context bindings, owner/root/control-file identity and cleanup. Public
projection is an additional export boundary, not an alternative oracle.
Candidate construction and validation now finish before raw inputs are
written to the review bundle. A rejected candidate can produce a bounded
reason-only diagnostic, but cannot first publish an unexpected metadata
member into `evidence/supervisor.json`.

The separate acquisition/partial-receipt preservation and rejected-renderer
diagnostic paths retain their existing meaning. This change does not claim
to validate or redact arbitrary external logs or all producer output.

## 4. Compatibility and verification requirements

- The supervisor wire key `trusted_storage` and its v1/v2 schemas remain.
- B3 candidate-v1, strict B4 candidate-v2 and lifecycle B4 candidate-v3
  retain their existing revision, field values and canonical bytes for
  valid saved inputs. No old artifact is silently upgraded.
- Projection does not mutate the original inputs or share mutable nested
  objects with them. It rejects unknown fields instead of dropping them.
- Regression tests cover both B4 candidate revisions, nested metadata
  canaries, invalid types, output-before-validation and input isolation.
- Reopen the saved real candidate and reproduce JSON, HTML and all image
  assets byte-for-byte. Re-run the same versioned CodeQL query on the exact
  modified source, followed by fresh real GUI CI and all independent PR
  check runs. Local query success alone is not a completed CI result.

There is no query exclusion, alert dismissal, broad writer sanitizer,
security-setting change or weaker random source. SSH credential FD handling,
C2 nonce generation, deadlines, reason classification and N01-N06 oracles
remain unchanged.

### Observed local comparison

The unchanged query/CLI/pack reported **1 result before and 0 after** the
projection. The modified exporter SHA-256 is
`bce559880d0e97a09ae424476e3c2ea9f154aed7a42661ce39ea022ff989fcf5`;
the after-SARIF SHA-256 is
`0a6fba0a54b5bf7734b6d2e83ff47cda91e36fca4aa5e5c20c3948f7ab0ca79f`.
This comparison used an isolated, uncommitted source snapshot. It is not
the later PR security-check result.

A separate process revalidated the original real candidate-v3 from artifact
`14480625397`, rejected all **38** semantic mutations, independently decoded
all six PNGs against raw RGBA, and reproduced all **104** bundle files
byte-for-byte. Candidate SHA-256 remains
`9c3337a7772e801f01b6cb42eb15f7dec5aeb384906d1abf1c38c4f89121c76b`.
The old source's synthetic unit candidate-v2 also reproduces all 104 files;
this synthetic check is not real v2 GUI acquisition. Original ZIP, extracted
files and input objects remain untouched. The compatibility audit SHA-256 is
`fd79b2cc2e515b730d10acb7b07512d4210535db7abb3736513e11255038f89f`.

## 5. Acceptance boundary

The fixed manifest remains **28 families / 87 variants / 533 assertion
references**, SHA-256
`e2d0d80c35fc5494415a627a7a11d162b127fecc38646535afacf9f08ff40f33`.
`APPROVED_SHA256=None`, `reviewed_golden=false`,
`menu_identity_verified=false`, `verified_n07_assertions=[]`, N07 **0/13**
and `host_v3_acceptance_passed=false` remain in force. Effective theme,
independent human Golden review/pinning, B5's typed receipt and acceptance
connection, the other [I6 work](i6-remaining-work-20261119.md) and Quest
hardware verification remain separate work.

Fresh run/check/artifact identities belong in the PR result record after
they actually complete; this design/change record does not predeclare
their success.
