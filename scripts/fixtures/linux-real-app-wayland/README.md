# L7a A0 host Wayland fixture

This is a genuine Eidolon `ProtocolState` compositor and seat, not a GTK test double or Xvfb. It does not launch children: `scripts/check-linux-real-app` owns the real Waypipe client/server, private bus and unmodified `/usr/bin/mousepad --disable-server`, their environment, and their complete descendant cleanup through the existing live pidfd owner. It deliberately has no arbitrary command or text-injection operation.

## Build and launch

Build the existing native dependencies first, exactly as for `scripts/check-waypipe-ime`. Then:

```sh
cargo +1.95.0 build --locked --manifest-path scripts/fixtures/linux-real-app-wayland/Cargo.toml --target-dir target
target/debug/linux-real-app-wayland-probe --runtime-dir /absolute/private/runtime --artifact-dir /absolute/private/compositor
```

Both directories must already exist, belong to the caller, have no group/other permissions, and not be symlinks. The artifact directory must have no `result.json`. The runtime path must be short enough for a Unix socket. The owner supplies these directories from an empty synthetic profile, never an existing user profile. The fixture creates `wayland-l7a` in the runtime directory. Set `XDG_RUNTIME_DIR` to that directory and `WAYLAND_DISPLAY=wayland-l7a` for the Waypipe client. Start the actual application through the matching Waypipe server, not directly on this socket.

No app environment is needed by this binary. The owner must construct the application environment and configure `GSETTINGS_BACKEND=eidolon-keyfile-v1` / `GIO_EXTRA_MODULES` for the real app as specified by the A0 plan.

## Control protocol

Unbuffered line-delimited JSON on stdin/stdout. The first line is:

```json
{"schema":1,"event":"ready","socket_name":"wayland-l7a","protocol":"eidolon-a0-control-v1","server":"Eidolon ProtocolState"}
```

Each request has an increasing positive uint32 `id`. Window IDs are positive decimal **strings**, matching native identifiers. Unknown fields and operations are rejected. Each command gets exactly one `{id,ok,error?,snapshot}` reply. Do not pipeline commands: wait for the matching reply. Native frame callbacks and disconnect cleanup continue while stdin is idle.

| Operation | Extra fields | Behavior |
| --- | --- | --- |
| `snapshot` | none | Current metadata and lifetime observations |
| `focus` | `window` | Focus a currently mapped real toplevel |
| `key` | `window`, `evdev`, `pressed` | Physical seat event using the current focus token; no text injection |
| `click` | `window`, `uv: [u,v]` | Pointer move, primary press/release using native hit testing; coordinates within `[0,1)` |
| `close` | `window` | Actual `xdg_toplevel.close` |
| `shutdown` | none | Finish only after all clients, objects, surfaces, windows and SHM storage are zero |

For example, focus the observed main ID, then send evdev 29 down, 24 down/up, and 29 up to press Ctrl+O. The returned snapshot immediately follows delivery, not application processing. Poll until the real Open dialog appears and has the main in its transient chain. Use the actual mapped dialog ID for its seat focus. If Enter closes a dialog before key-up, the fixture accepts the late release only when that target is no longer mapped and native pressed-key count is already zero after input reset. This no-op does not increment input delivery; releases to live mapped targets or while any native key remains held retain normal validation.

`snapshot.windows` has `window,parent,app_id,app_id_valid,life,frame,logical_size,layers`. `life` uses the native lifecycle enum (`Created`, `AwaitingInitialCommit`, `Configured`, `Mapped`, `Unmapped`, `Destroyed`). `snapshot.observed_mapped` retains each WindowId's first mapped metadata and a `dispatch_batch`. Equal batches do not establish request order inside the dispatch. This allows the owner to check that the initial stable state contains exactly one parentless mapped main with a SHM frame; subsequent dialogs must remain bound to that latched ID. Never infer a new main from a dialog losing its parent after destruction.

The snapshot also includes `counts` (`clients,objects,surfaces,windows,pixel_bytes,pool_bytes`), `input` (`focus_window,focus_token,delivered,pressed`), relevant protocol `counters`, and `accepted_clients`. There are no window titles, text contents, filesystem paths or pixels. `app_id` is bounded ASCII metadata; an unexpected string is null with `app_id_valid=false` and must be rejected. An absent app_id is null with `app_id_valid=true`; it is permitted only for a dialog whose real parent chain reaches the latched main.

The owner stops/reaps the application, bus and both Waypipe ends, polls until counts are zero, then sends `shutdown`. The fixture emits a final `finished` event and stores `result.json` mode 0600. Stdin EOF, malformed protocol, signal, output backpressure, or a fixed 120-second whole-run deadline cause failure. Command traffic never renews this deadline. On failure the fixture force-disconnects Wayland clients and records that this was forced; this is not evidence of the owner's normal process cleanup.

## Evidence boundary

The receipt's `state=complete` means only compositor/seat operation and clean teardown completed. `scope=compositor-and-seat-only` and `mousepad_feasibility_verified=false` are intentional. The owner must separately prove all of the following before reporting A0 pass:

- The executable and package are the real, unmodified supported Mousepad, traversing both real Waypipe ends.
- Exactly one initial main, expected app_id, and native SHM/frame observation.
- A real Open dialog with the correct transient parent; seat-driven Open/edit/Save, verified against independently read output bytes. A screenshot or a key counter cannot substitute for the file oracle.
- Preferences dialog/transient behavior, immutable setting values and actual UI read-only state, plus persistence after restart. Backend writability alone is not a claim about observed widgets.
- Every owned external descendant has exited and been reaped, including children that changed process group or session. This compositor owns none of those processes.

Unobserved or unsupported UI steps remain false in `checks` and listed in `pending`; they prevent the A0 gate from passing. Missing cargo, native build inputs, Mousepad, Waypipe, DBus or the backend module must be an explicit failure, not a passing skip. This fixture proves neither SDK/HMD rendering nor physical controller/keyboard behavior and does not open the production v3 gate by itself.

## Python owner adapter

`driver.py` exports `Control(process)` for an already spawned binary with binary stdin/stdout pipes, `wait_main(expected_app_id)`, actual `focus/key/click/close`, bounded snapshot polling, and `exercise_open_edit_save(control, input_path, output_path, initial_bytes)`. The owner still owns all process lifetimes. Example synthetic input is `b"eidolon-a0-opened\n"`; both paths must be absolute ASCII names in the same private directory, the input must exist with those exact bytes, and output must not exist. The driver opens with Ctrl+O, selects the path with chooser Ctrl+L, edits via evdev seat keys, saves with Ctrl+S and Save As with Ctrl+Shift+S, then independently checks both files. It rejects a new root, lost/replaced main identity, an orphan/cyclic dialog, or a foreign app_id during the sequence.

The driver returns `state=partial`, `feasibility_verified=false` even if every Open/Save check succeeds. Its fixed `remaining_checks` are `independent_existing_mousepad_instance`, `normal_close_restart`, `preferences_widgets_read_only`, `preferences_transient_parent`, `settings_persist_after_restart`, and `restart_after_forced_stop_has_one_main`. A settings API probe cannot clear the widget item. An aggregate owner additionally needs Waypipe/package provenance and reaped external process groups; the adapter's successful return does not certify those. Unsupported chooser shortcuts or timing fail a bounded step instead of silently downgrading it to pass.

## Aggregate owner API

`scripts/linux_real_app_wayland.py` exports:

```python
run_feasibility(repo: Path, artifact: Path, settings_bundle: Path,
                *, waypipe: Path, compositor: Path) -> dict
```

The paths must be the current checkout, an empty owner-private artifact directory, the verified settings bundle, the exact Waypipe host metadata executable, and `target/debug/linux-real-app-wayland-probe`. There is no operator option for arbitrary commands. The call requires a dedicated single-threaded, non-root process with no existing children, as required by `ProcessOwner`.

The owner starts both Waypipe ends, a private session bus with no service activation directories, and real Mousepad in a newly created synthetic profile. It holds `profile/profile.lock` from before the read-only recovery guard and app-bearing spawn until every descendant is reaped. The guard is repeated immediately before the app-bearing spawn; a pending cleanup retains the same lock and live owner. Private runtime sockets use a short temporary path. The internal FIFO adapter replaces the compositor child's stdin before `exec`; the PID stays within the same process owner.

The resulting `wayland.json` contains `status` (`failed` or `incomplete` at this foundation stage), exactly the following boolean `checks`, and `pending` listing every false check in this order:

| Check | Evidence required by this adapter |
| --- | --- |
| `private_bus` | Actual app-bearing launch through the newly created private bus |
| `independent_instance` | Pending: an existing ordinary Mousepad instance must also be exercised |
| `first_main` | Exactly one initial native main with an owned SHM frame and latched lifetime ID |
| `expected_app_id` | The native main reports `org.xfce.mousepad` |
| `transient_dialogs` | Actual Open and Save As dialogs have a parent chain to that same main |
| `open_edit_save_as` | Seat-driven Open/edit/Save/Save As, independently verified exact file bytes |
| `clean_close_restart` | Pending: normal Close and subsequent restart in the same profile |
| `gui_setting_persistence` | Pending: independently observed GUI setting after restart |
| `protected_settings_gui_readonly` | Pending: actual Preferences widget read-only state and transient behavior |

A successful implemented sequence has five true checks and four pending checks. Its status remains `incomplete`, with `feasibility_verified=false`. The driver's more detailed observations are nested under `evidence`; a successfully launched fresh instance alone does not prove behavior in the presence of an existing ordinary instance. Failure to build or run dependencies yields `failed`, with no passing skip. The A0 parent must require all nine booleans and a successful status before opening the next gate.

Cleanup uses bounded TERM/KILL/reap attempts on the same live `ProcessOwner`, drains output pipes, closes pidfds, then removes only this run's temporary runtime directory. It makes one bounded retry if the first owner cleanup cannot be verified. If cleanup still cannot be verified, `CleanupPending` carries the live `run` and failed `receipt`; the caller must retain it and call `retry_cleanup` in the same process. It must not discard the owner or recover from stored numeric PIDs. Application stdout/stderr is bounded and drained without retaining its content.

Failure diagnostics in the owner receipt use a fixed whitelist: driver phase,
last operation, known native error codes, up to eight windows' IDs/parent IDs,
bounded app_id/validity, lifecycle/frame/layer count, and native focus/key counts.
Unknown properties and free-form strings are excluded. An `INITIAL_APP_ID`
failure also records metadata after 250 ms of read-only dispatch to distinguish
an initial metadata value from a later GTK update; this does not admit the
window, send application input, or change that failed result.
