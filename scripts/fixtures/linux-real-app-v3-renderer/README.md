# I6 real Host renderer fixture (T evidence)

This standalone crate verifies the third I6 implementation unit. Its fixed
`eidolon-shm-fixture` client creates real `wl_shm` buffers and an xdg toplevel over
a private Unix socket pair. The ordinary `ProtocolState` assigns the window,
surface, content and frame IDs and produces immutable `WindowSnapshot` objects.
The ordinary renderer `Controller` consumes those snapshots, its render thread
owns the surfaceless EGL / GLES3 RGBA8 pbuffer, and `paint_decorated` composes the
app and a fixed theme before bounded readback and successful swap.

There is no product success setter. Scene and surface generation are explicit
renderer test inputs; this crate does not invent a Runtime handle, native
CreateSeq, SDK binding, B1 Ready state or input permit. The resulting evidence
class is **T**. `N02/gtk3_desktop`, `N02/mousepad_editor`, normal Registry Launch,
the synthetic SDK integration and its independent product oracle remain
unperformed. Nothing here makes formal I6, I7, Android storage or Quest/H pass.

## Fixed observations

The target is 1280 × 1280. A 32 × 32 SHM window is centered at `(624, 624)`.
Two fixed readback ROIs sample all 1024 app pixels and four decoration pixels.
The app uses the existing deterministic premultiplied ARGB8888 pattern. Expected
output bytes are computed independently in the Rust fixture and the Python
validator, without calling the compositor's reference renderer. The app's black
content background is opaque; its final alpha is 255. Decoration changes from
`#102040` to `#506070` on a normal semantic focus update.

The fixture holds the real Wayland frame callback while it observes initial,
idle and decoration draws. These draws must have the same content FrameSeq and
increasing present sequence. Releasing the callback permits the client's next
real SHM write/commit; that update must change both FrameSeq and expected pixels.
The old immutable snapshot retains its old bytes. Raw pixel vectors are compared
privately and are not serialized into public evidence.

Negative cases exercise foreign owner tickets, replay, wrong session/window,
uncreated and retired generations, minimum-content-frame Pending, ROI count,
byte and geometry limits, bounded queue Busy, foreign retirement fences,
generation admission seals, late old-target retirement, window retirement and
post-stop readback. The capacity case admits exactly eight future-frame tickets,
observes all eight as Pending, and requires the actual ninth request to return
Busy while the first eight remain Pending. Both the fixture and the independent
validator use the fixed eight-request contract; neither derives the accepted
capacity from product configuration or a producer-supplied count.
A separate process with no GLVND vendor must report an actual
EGL initialization failure; it cannot return a successful stub frame or skip.
The fixture waits for actual Wayland destruction, renderer target/window fences,
image/cache release and the same renderer's `owner_reclaimed` before checking
its final zero-resource census.

The independent validator checks the complete census schema and exact integer,
boolean and null types at every stage. Each observation has an empty queue and
no target, image, app texture, texture byte or GL/error residue. Before any target,
the actual theme adoption must leave the EGL context and GPU theme allocation at
zero. After window retirement, the renderer's single context and global theme
may remain alive (theme bytes remain bounded by 32 MiB). After owner reclamation,
worker/context counts and all GPU texture bytes, including the global theme,
must be zero. Historical attach/detach counters and the stable owner thread ID
remain observable. The fixture waits for both the real retirement fence and
the corresponding published census; it never replaces stale observations with
invented zero counts. Direct-validator and outer-export mutation tests reject
nonzero residue, missing fields, boolean/float aliases and under/over-capacity
claims while allowing the retained global theme at the window-retirement stage.

Context-loss receipt invalidation is additionally covered by the renderer's
explicit test backend. That test seam is T evidence, not a claim that this
fixture forcibly removed a real Mesa context.

## Run the mandatory disposable test

From a clean, committed checkout with Docker available:

```sh
bash scripts/ci/check-linux-desktop-v3-renderer \
  --artifact-root /absolute/private/empty/renderer-artifacts
```

The wrapper clones only committed files into a fresh image, uses the existing
source-pinned native dependency builder, builds this standalone crate with
`--locked` and `host-acceptance`, and runs the fixture as its own nonroot account.
The runtime container has network `none`, no mounted HOME/desktop/ADB/Docker
socket, no added capabilities and no host PID namespace. Default and
`host-acceptance` renderer tests also run in the image. Real llvmpipe execution
is mandatory: absent EGL, failed draw/readback/fence/swap, missing receipts,
timeout or external rescue fails the job. The workflow uses the existing
conservative L7a scope result and does not weaken any existing required job.

The binary embeds its actual source commit, canonical tree digest and compiled
feature identity; it also records the unchanged C1/C2 inventory hashes. The
supervisor compares the checkout before and after execution and the outer
wrapper repeats validation against its own checkout. The CI artifact separately
records the PR head and the synthetic merge source that GitHub checked out.
Dirty source or evidence from a different source is rejected.

For local diagnostic development, build the native dependencies with the usual
repository tooling and run:

```sh
cargo +1.95.0 build --locked \
  --manifest-path scripts/fixtures/linux-real-app-v3-renderer/Cargo.toml \
  --target-dir target
LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe EGL_PLATFORM=surfaceless \
  target/debug/eidolon-i6-renderer-host
```

A diagnostic run on a dirty tree can exercise actual SHM/EGL but cannot satisfy
the clean-source evidence validator. Named Unix sockets are not needed; this
fixture uses a socket pair and never connects to the user's desktop.

## Evidence files and bounds

`renderer-native.json` contains the actual receipts and comparisons.
`renderer-unavailable.json` records the EGL-unavailable negative process.
`renderer-environment.json` records actual Mesa packages and source identity.
`renderer-validation.json` is independently recomputed, including all 28 I6
families, 87 required variants and 533 assertion references without reducing
them. `renderer-container.json` records the actual image, exit, PR head and
checkout. Each JSON is bounded to 1 MiB, private, regular, and read without
following symlinks. Arbitrary logs or raw framebuffer images are not exported.

Success means `renderer_t_passed=true` and always
`host_v3_acceptance_passed=false`. Environment deletion is only attempted by the
outer trap; without a successful deletion receipt `environment_disposal` stays
`not_yet`. The existing N01 disposal receipt is a separate remaining issue.
