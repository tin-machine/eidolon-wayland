# I6 N05 — Open / Edit / Save As evidence boundary (foundation PR)

## Scope and status

This PR implements the independent **file supervisor and negative-test foundation** for the frozen `N05/open_edit_save_as` acceptance variant. It does **not** claim an actual Mousepad GUI save, N05 acceptance, or a complete I6. No N01–N04 oracle, v2/v3 contract, manifest or expected digest is modified.

The full N05 requires the regular N02 launch (Registry → Runtime → SSH → C2 → Waypipe → Mousepad → compositor → Host SDK → renderer), current focus/input token, Ctrl+O, a verified parented Open dialog, typed path through that GUI, Ctrl+End, the fixed append via input gate, Save As dialog, independent file observation, and full product Stop/census/journal/retirement. The foundation verifier must fail closed when any of these proof classes are missing; it does not synthesize GUI evidence.

## Immutable file contract

The initial input is eight UTF-8 LF-terminated lines (78 bytes, SHA-256 `884b4478282ae75f7f297783a415cb6d529b29b63fcf3b6e56c1110ed2af1262`). The only requested document edit is `eidolon-i6-saved\n` after Ctrl+End. Expected output is 95 bytes, SHA-256 `79a400c35081c5b9bc257e6d440512320c134a680b28c6c904e39b5d40e18337`. These values come from §10.1, never from a GUI observation.

An independent disposable supervisor prepares **only** the input file in a freshly created run-owned directory. It records input dev/inode/UID/nlink before GUI and reopens both names after GUI with `dir_fd` + `O_NOFOLLOW|O_CLOEXEC`, `fstat`, and read from that same FD. Output must be absent at baseline, independently created later, and distinct from input. Inode, owner, exact content, digest, type, nlink, and run-binding must agree. No file is read using path-based `open` after an `is_symlink` check. A reliable GUI-origin proof additionally requires a separately recorded actual delivered input, current focus token, distinct parented dialog generations, SDK/renderer evidence, and product cleanup. A file byte match alone does not prove GUI causality.

## Integration path

1. Run the supervisor under a disposable owner separate from the native GUI driver. Prepare a random per-run directory with restrictive permissions, stable trusted directory FD and **only input**. Do not create the output or give the GUI actor a way to modify the supervisor receipt.
2. Pass the already-owned input/output paths into the ordinary N02-derived Host driver, without directly spawning Mousepad, using any secondary synthetic input API, or modifying C2 owner semantics.
3. Preserve receipt owner join on run_id/runtime/session/capture/native origin, include per-key event chronology for Open dialog, current SDK/renderer, actual delivered key counts, GUI path entry, edit, Save As dialog and save confirmation. Saved file alone is insufficient.
4. Complete existing N04-grade normal Stop, retirement of every dialog generation, finalizer and Registry destroy. Fixture rescue must remain separate.
5. Bind source before and after execution, preserve the 28/87/533 manifest (digest `e2d0d80c35fc5494415a627a7a11d162b127fecc38646535afacf9f08ff40f33`), formal `status=incomplete` and `host_v3_acceptance_passed=false`.
6. A dedicated source-bound Docker N05 CI job and formal receipt exporter must not pass until actual live evidence supports all ten immutable assertions. Revalidation must never substitute N04 artifacts.

## Non-claims and follow-up

This foundation PR intentionally leaves the live GUI driver, independent GUI oracle, typed final N05 receipt, formal exporter and dedicated Docker live CI job **unimplemented**. All ten N05 assertions therefore remain **unverified**. Negative unit tests of the file sub-oracle are not equivalent to acceptance.

Review carefully before extending to the live GUI implementation. The contract here is read-only after initial input fixture preparation and cannot manufacture a saved result.
