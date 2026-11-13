# Spatial Anchor — D6 feature index

The [Spatial Anchor × Linux / SA0 design](../../spatial-anchor-linux-design-20261111.md) is registered as **`architecture / proposed`** at the D6 review baseline `443d5f740053efc0c55914ed4c556118e54d4220`.

```sh
python3 scripts/docs.py context --feature spatial-anchor --task SA0
python3 scripts/docs.py affected --path android/app/src/main/java/io/github/tinmachine/eidolon/SpatialPanelAdapter.kt
```

**SA0 is an observation-only experiment**, not a request to change the normal workspace. Verify create → persist → Activity teardown → rediscovery → Quest reboot → rediscovery → erase with actual SDK and HMD evidence. Existing workspace/SDK source paths are review associations, not claims that production anchor persistence or Quest acceptance has been implemented. Leave unknown lifecycle/OS behavior as unknown.
