# I6 N03 — Launch Admission negative acceptance (Host R/T)

Status: **implementation checkpoint / independently verifiable partial I6**. Baseline:
`main` at `995bf219f0e9d03f53c539bfc956f7db720a668b` (PR #39).
The open C2 Waypipe refactor PR #38 is **not** an implementation dependency.
Primary requirements: [I6 detailed design](../../linux-real-app-v3-i6-detailed-design.md),
[fixed cases-v1.json](../../../scripts/fixtures/linux-desktop-v3-acceptance/cases-v1.json),
[N01 Inspect-only](i6-n01-inspect-only.md), [N02 typed acceptance](i6-n02-typed-acceptance.md).

## Frozen contract

The original manifest remains the authority for the five **distinct** N03 variants,
each with seven assertions; the total is **5 variants / 35 assertions**, not an
acceptance of the other 82 required variants (28 families, 87 variants,
533 assertions remain unchanged). Every case requires
`start_count_at_most_one`, `rejected_launch_no_spawn`,
`product_census_complete`, `journal_finalized`,
`registry_destroy_accepted`. Four no-allocation cases also require
`no_local_allocation_sealed_before_stop`. The valid first Launch in
`double_launch` instead requires
`origin_retirement_before_runtime_stop`.

| Variant | Rejected request | Expected Registry immediate return | Expected command completion | Scenario |
| --- | --- | --- | --- | --- |
| `unknown_app_id` | 32 | `InvalidArgument` (3) | **none** | Real catalog then an unrecognized `AppId`; parsing cannot queue work. |
| `scope_changed` | 32 | `Accepted` (0) | `NOT_READY` | Mutate **only** one valid 64-hex scope. |
| `revision_changed` | 32 | `Accepted` (0) | `NOT_READY` | Mutate **only** one valid 64-hex revision. |
| `old_catalog` | 42 | `Accepted` (0) | `NOT_READY` | Real old Inspect → old capture finalizer → new connection/Inspect → old Selection against *new* capture. |
| `double_launch` | 33 | `Accepted` (0) | `BUSY` | Legitimate Launch 32 still owns a live app; second request 33 is rejected; first remains operational and retires normally. |

These values are **not** calibrated from observed data. A changed code path
must fix the product, or N03 stays failed.

## Execution and proof boundary

`host_n03.rs` reuses the N02 normal Registry, Runtime, SSH, C2 and
Waypipe fixture (and for `double_launch`, N02 GTK3 + synthetic Host SDK
and readback). No test-side direct application spawn or synthetic LaunchPermit.
The native driver writes the actual Registry return, request-specific
completion, original inspected Selection, launch request and live
post-refusal snapshot. The Host transport trace independently records
C2 sends, protocol events, each capture's context/pins, local-allocation
seal, remote cleanup, I/O retirement, journal finalizer and stop.

The independent `linux_real_app_v3_n03_oracle.py` recomputes:
- Reconstructed capture digest from live `Context`, `Pins`,
  immutable source hashes, Inspect request and I/O generation; catalog
  Selection from that reconstructed context, **not** from the submitted Launch.
- Exact request 32/33/42 versus the immediate Registry status and its
  own Runtime completion; `Accepted` as a queue response cannot prove
  Launch admission, nor can another request's completion.
- Zero C2 `Start` and no `Started` protocol event for the four
  no-allocation variants. For `double_launch` exactly one legitimate
  `Start`, independent N02 positive SDK/readback/Ready/input verification,
  no second remote spawn, and first-owner retention at the Busy boundary.
- N01 strict no-allocation protocol proof for **each** no-launch capture,
  including actual seal before remote Stop, cleanup, I/O fence and finalizer.
  The `old_catalog` test must contain two distinct *contiguous* capture
  epochs and the old finalizer must precede the new capture.
- Same live Runtime's end-state census, complete source/owner/runner
  correlation, finalizer before origin retirement observation,
  Runtime shutdown and actual Registry destruction; plus independent
  disposable-container process/lock census.

The native file's `error`, `cleanup_error`, any producer `status=pass`,
or a claimed `start_count` alone cannot establish an assertion. The
full transport trace and external observations are replayed and cross-checked.
A timeout, missing ACK, lost finalizer, new empty Runtime, or fixture rescue
cannot replace product cleanup.

## Evidence / CI boundaries

The source-bound disposable fixture uses fresh non-root driver/app homes,
fresh pinned SSH server and key, `--network none`, fixed build inputs and
no host desktop or Docker mount. The N03 supervisor executes each variant
with a **new run ID / owner / capture**, independently reopens the persisted
native receipt, and exports a bounded environment census. Its five runs
must not be combined into one invented run or shared owner.

A per-variant `n03_receipt` contains raw native and environment evidence,
owner and (only for `double_launch`) verified target identity. The
unchanged formal I6 report format is validated by
`scripts/linux_real_app_v3_evidence.py`, which independently runs the
N03 oracle and returns **`status=incomplete` / `exit_code=2`**,
`verified_case_oracles=["N03/<variant>"]`. Untested cases remain
`not_run`. Every N03 receipt keeps
`cleanup.environment_disposal=not_yet`; container deletion requires
an external disposal witness and is not inferred from a product receipt.

Dedicated workflow job `v3_n03` invokes
`scripts/ci/check-linux-desktop-v3-n03`. The outer exporter
`scripts/linux_real_app_v3_n03_ci.py` reads *only* the fixed named
bounded JSON files, recomputes all five outcomes, revalidates formal typed
reports, and rejects adversarial mutations including wrong status,
wrong request, extra Start, hidden local app, missing finalizer, stale/forged
capture, owner/target/source digest changes, premature retirement and
false producer pass. The exporter records the checkout commit separately
from the PR head SHA.

### Checks

```bash
python3 -m unittest discover -s scripts/tests -p 'test_linux_real_app_v3_n03.py' -v
cargo +1.95.0 fmt --manifest-path scripts/fixtures/linux-real-app-v3-host/Cargo.toml -- --check
bash scripts/ci/check-linux-desktop-v3-n03 --artifact-root /absolute/empty/private/artifact-directory
```

The last command requires a real disposable Docker-capable CI runner.
A passing job proves only N03 Host R/T partial acceptance: **no Meta SDK
runtime on Quest 3, physical input, Android storage acceptance, external
disposal certificate, or full 87-variant I6 approval**.
`host_v3_acceptance_passed=false` is non-negotiable until the remaining
acceptance and physical evidence are independently satisfied.
