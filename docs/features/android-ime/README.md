# Android IME / Wayland text-input — D6 feature index

The [Android IME → Wayland plan](../../android-ime-wayland-plan-20261107.md) is registered as **proposed architecture**. The [implementation log](../../android-ime-implementation-20261107.md) and [NX09 entry](../../nx09-terminal-ime-entry-20261108.md) are **historical** records. The [A1/A2 comparison probe](../../android-ime-probe-20261107.md), [terminal debug probe](../../android-ime-terminal-probe-20261108.md), and [NX09 normal owner probe](../../nx09-normal-ime-probe-20261108.md) are runbooks for bounded/debug tests, not evidence of a successful Japanese IME or keyboard acceptance.

```sh
python3 scripts/docs.py context --feature android-ime --task A1
python3 scripts/docs.py context --feature android-ime --task NX09
python3 scripts/docs.py context --feature android-ime --task NORMAL-PROBE
python3 scripts/docs.py affected --path native/core/src/compositor/ime.rs
```

**Safety boundary:** do not promote synthetic input injection, a debug-only case, or an observed focus transition to proof of real kana-kanji conversion, normal release UI behavior, or HMD acceptance. Each historical report belongs to its pinned build/device/OS circumstances, and the present source/Quest state must be re-checked. The task keys index reading sets; they do not replace the original command preconditions.
