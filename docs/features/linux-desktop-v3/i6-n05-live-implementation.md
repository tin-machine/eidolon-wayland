# I6 N05 — Live Open / Edit / Save As integration

> Status: **Draft PR #43; not accepted until current-source live Docker CI and independent saved receipt revalidation pass.** The frozen I6 Host profile remains `incomplete`; no Meta Quest / physical-input proof is claimed.

## Scope and frozen acceptance

Exactly `N05/open_edit_save_as`, evidence class **R + S**, outcome `complete`, 10 frozen assertions; the manifest remains 28 families / 87 variants / 533 assertions / `e2d0d80c35fc5494415a627a7a11d162b127fecc38646535afacf9f08ff40f33`. Neither N01–N04 semantics nor the underlying v2/v3 and SSH/C2 source pin are changed.

Fixed original is UTF-8 without BOM, eight LF-terminated lines, 78 bytes, SHA-256 `884b4478282ae75f7f297783a415cb6d529b29b63fcf3b6e56c1110ed2af1262`. Save As must write a **new** 95-byte file with `eidolon-i6-saved\n` appended and SHA-256 `79a400c35081c5b9bc257e6d440512320c134a680b28c6c904e39b5d40e18337`. These expected bytes are fixed before every run; never derive expectations from the observed file.

## Runtime and trust boundaries

The native N05 Host driver reuses **N02's ordinary** Registry, Runtime, actual SSH Controller, source-pinned C2, Waypipe and Mousepad Launch. The same native compositor admits a full main TargetKey and GTK transient dialogs; Host SDK synthetic targets require current renderer readback. A real input command is submitted through the existing Registry focus and keyboard input gate. It is not an alternate keyboard channel, direct Mousepad spawn, clipboard, D-Bus automation, synthetic file result or direct file write.

The disposable supervisor creates a fresh app-UID directory `/home/eidolon-app/n05-<run-id>/`, an exclusively created `input.txt`, and **no** `saved-as.txt`. The supervisor retains the directory FD through the GUI phase, checks current name resolves to that same FD, then independently opens fixed components using `dir_fd` / `O_NOFOLLOW` / `fstat`. It checks input and output type, owner, single-link, distinct device/inode, exact bytes, fixed length/digest and unchanged input metadata. Only metadata, not file content, enters the bounded receipt. The driver cannot use the supervisor to write the output file.

Note: a file-only positive sub-oracle is deliberately **not** proof of a GUI save. File evidence is promoted only after same-run/capture GUI delivery, distinct Open and Save As dialog generations, current SDK frame evidence, and complete product cleanup have passed the independent N05 oracle.

## GUI event choreography

- Launch from an actual Inspect/Selection. Require Started ACK, main target, B1 Ready and current SDK/renderer content frame (N02).
- Focus the main target via Registry `focus_window`, derive current epoch/token, issue physical Ctrl+O. Wait for newly mapped, parented Open xdg_toplevel in GUI admission, then wait for actual Host SDK readback.
- Focus that dialog, issue Ctrl+L and type the input pathname with physical-key `pressed`/`released` packets. Every request must receive actual `command_complete ACCEPTED`; the compositor delivered counter must rise by the full expected physical-key count. Confirm using bounded Enter (or GTK mnemonic after unsuccessful Enter) and observe child destruction. Stale target key-up after immediate destroy is disallowed (same N04 rule).
- Focus the still-live main, issue Ctrl+End and type the ASCII fixed append. The pre-existing Japanese UTF-8 text is never synthesized through a keyboard API or overwritten.
- Issue physical Ctrl+Shift+S, require a **different** admitted transient Save As target with same parent and owner. Mousepad seeds the Save As chooser with the currently open input file, including its parent directory. Through the normal input gate, issue Ctrl+L and Ctrl+A to replace the existing path/name, type only the **fixed new basename** `saved-as.txt`, then confirm. The independent supervisor alone verifies the resulting absolute output path in the run-owned directory and that the original file is unchanged.
- Reopen the output from the independent supervisor FD and demand exact bytes. Dialog closure alone, Registry ACK alone, or local file hash alone cannot pass acceptance.
- Normal Stop ACK, C2 cleanup, I/O fence, all native/SDK/renderer target generations, journal finalizer, Runtime shutdown and Registry destroy must be ordered. Fixture rescue and watchdog results cannot grant product success.

The N05 Host driver aggregates per-step delivered counts and bounded current-focus observations, rather than hundreds of redundant per-key diagnostic events. The underlying product still processes every key and its completion; any failure aborts the run.

## Oracles, CI and evidence

`scripts/linux_real_app_v3_n05_oracle.py` separately checks the supervisor metadata, GUI step chronology, exact target/parent/SDK chain for main+Open+Save As, actual delivered input and full original N02 product/cleanup oracle. Reuses the unchanged N04 full-capture C2 protocol sequence checker, not N04's 125-second timing expectations.

`linux_real_app_v3_n05_typed.py` assembles the sole allowed `n05-open_edit_save_as-receipt.json` with run/capture/source/owner binding. `linux_real_app_v3_evidence.py` only accepts that case-specific typed receipt when independently revalidating; formally still `host_v3_acceptance_passed=false`, `status=incomplete`, with remaining I6 variants not run. The exporter verifies fixed files only and replays adversarial changes (BOM, newline, ownership, inode/links, capture/run swaps, input delivery, dialog parent/focus, missing finalizer and leaked SDK/renderer). Dedicated `v3_n05` CI launches a disposable, clean-checkout, source-bound Docker image and pins actual checkout commit separately from PR head.

## Acceptance / review status

A green **unit test** is insufficient. N05 PASS requires an actual disposable GUI run and successful independent file and formal typed revalidation on that same source; otherwise all 10 assertions stay unverified. Initial CI runs established Open dialog creation and physical-path delivery. A later run also confirmed Open dialog closure, Ctrl+End, the exact 17-byte edit key sequence, and Ctrl+Shift+S delivery, but timed out while waiting for the Save As dialog. The Mousepad 0.6.3 pinned source defines `win.file.save-as = <Control><Shift>S` and constructs a parented `GtkFileChooserDialog`. The N05 driver was taking its 'already-existing windows' snapshot **after** the shortcut, so a fast-created Save As window was incorrectly considered old and could never satisfy freshness. The fix takes the mapped-window baseline **before** the shortcut, records it separately for Open and Save As, and lets the existing admission + parent + native SDK readback independently recognize a new child even if GTK created it before the first post-command poll. The oracle rejects a missing/post-shortcut baseline or reused dialog generation; Rust and Python negative tests cover this race. A green live CI result is still required to declare N05 complete.

The pre-shortcut baseline fix has now been validated in live CI: the real Open and Save As dialogs both appear, with distinct full TargetKeys, current SDK readbacks, real input delivery and normal cleanup. The next failed step was **independent file proof**: the input stayed exactly 78 bytes with its pinned digest, but `saved-as.txt` did not exist at the required path even though the Save As dialog closed. This confirms that dialog closure / real key delivery are insufficient evidence of saving. The Save As navigation has therefore been changed to replace the current filename in the existing input directory rather than typing an absolute path into a potentially prefilled location. A bounded failure-only supervisor diagnostic records type, size, owner and digest comparisons; it never manufactures the missing output. Only a new full live run and independent oracle can promote N05.

See also [GTK 3 FileChooser.set_file](https://docs.gtk.org/gtk3/method.FileChooser.set_file.html): in Save mode, selecting an existing input file establishes its parent folder and places its basename in the name entry. This is precisely what the pinned Mousepad 0.6.3 Save As handler does.

Source: [Mousepad 0.6.3 default key accelerator](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/mousepad-application.c#L629-L633) and [Save As GtkFileChooserDialog](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/mousepad-dialogs.c#L1139-L1197).

No Quest/Android/physical-keyboard guarantee is established by a synthetic Host SDK.
