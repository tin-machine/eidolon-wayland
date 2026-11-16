# I6 N06 — live Dialog Cancel / Save As Confirm / Unsaved Close Cancel / Close Confirm

> Status: implementation and **source-bound real Docker CI under review**. This document is not an acceptance claim. All I6 case/variant/assertion definitions stay frozen. Host synthetic SDK (S) is not Meta SDK/Quest evidence.

## Pinned baseline and upstream

Base main: `4ee239e161790b1264e85cdad08721902f229190` (N05 PR #43 merged). Open PR #38 is not a dependency. Mousepad 0.6.3 source commit `65fbfe0459259f820fcc8b9c4dd454228304842a`:
- `win.file.open`: Ctrl+O; `win.file.save-as`: Ctrl+Shift+S.
- `win.file.close-tab`: Ctrl+W; **`win.file.close-window`: Ctrl+Shift+W**. N06 uses close-window, not an invented accelerator.
- `mousepad_dialogs_save_changes()`: GTK modal Cancel / Don't Save / Save. Cancel can be triggered by Esc on the real admitted dialog. Don't Save uses the actual `_Don't Save` GTK mnemonic (Alt+D); the result must be observed, never inferred from an Accepted command.

Sources: [application accelerators](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/mousepad-application.c#L623-L637), [close-window action](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/mousepad-window.c#L4995-L5055), [save changes dialog](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/mousepad-dialogs.c#L371-L439).

## Four immutable acceptance variants

| Variant | Required assertions | Strongest differentiating proof |
| --- | ---: | --- |
| open_cancel | 8 | Before-shortcut baseline, fresh transient dialog readback, real Esc-down delivered, destroyed dialog and surviving main, no file modifications |
| save_as_confirm | 8 | N05 exact 78/95-byte file contract, same normal GUI and SDK evidence, independently surviving main after Save As |
| unsaved_close_cancel | 9 | Real Open/Edit, Ctrl+Shift+W, fresh Save Changes dialog, real Esc, surviving modified buffer, **second Save As via normal gate to prove retained 95 bytes** |
| close_confirm | 9 | Real Open/Edit, close-window intent *before* request, dialog GTK Don't Save, main destroy and actual app/transport process exit, ordered observations |

Every run has a distinct random run ID, independent Runtime/session/capture and application owner. No fixture-driven Mousepad spawn, direct file write, clipboard or input bypass is permitted. The original 78-byte input is prepared exclusively by the supervisor before launch and must not change. The optional 95-byte output must be created by Mousepad GUI only and checked by directory-FD-based, nofollow, app-UID-bound supervisor.

## Event and ownership protocol

1. N02's normal Registry→Runtime→SSH Controller→C2→Waypipe→Mousepad→native compositor→Host SDK→renderer runs unchanged.
2. Record mapped-window baseline **before** the shortcut. Normal focus/token/epoch and per-key command completions are required.
3. Detect a genuinely new mapped xdg_toplevel with main parent and GUI admission; acquire full native TargetKey and synthetic SDK current real-renderer readback.
4. Record dialog response input and actual delivered count. For Escape the GTK target can synchronously be destroyed by key-down: do **not** manufacture a successful stale key-up.
5. Observe dialog disappearance, main survival or main destruction (variant-specific), then full generation retirement.
6. Normal controller Stop/terminal, remote cleanup, I/O/native/SDK/renderer retirement, I3/B1 history finalization, origin retirement, Runtime Stop and Registry destroy must occur. Fixture rescue cannot be substituted.
7. Close intent is an observation *before* keyboard dispatch. On natural close, preserve the actual product terminal reason, including `APP_WINDOW_UNSUPPORTED`, and require chronological proof of main destroy, real app exit and finalizer. No rewriting the reason.

## Independent verification policy

Rust driver receipts do not carry verdict authority. N06 independent oracle checks N02 upstream provenance/normal cleanup, full R+S target history, key-delivery counter and focus/token freshness, before-shortcut dialog baselines, event ordering, per-generation SDK retirement, and separate supervisor file hashes. The saved N06 typed receipt is independently revalidated by `linux_real_app_v3_evidence.py`; four variants remain partial formal I6 evidence. Mutation tests reject stale focus, registry-only Accepted, crossed captures/parent, reused target generation, missing Cancel or edit retention, falsified Save As, Close intent without app exit and all incomplete cleanup paths. CI exports only named bounded JSON receipts and explicitly fails on missing source-bound evidence.

## Fixed invariants

28 families, 87 variants, 533 assertions, manifest SHA-256 `e2d0d80c35fc5494415a627a7a11d162b127fecc38646535afacf9f08ff40f33`, `host_v3_acceptance_passed=false`, Quest/physical input unverified, N01–N05 unchanged; v2/v3 and C1/C2 pins/owners unchanged. No claim of full I6 completion.
